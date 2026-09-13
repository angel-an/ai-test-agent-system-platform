# -*- coding: utf-8 -*-
"""需求质量门控 API（阶段一 + 阶段二）。

提供：
- POST /requirements/analyze              需求分析 → 质量判定（Complete/Incomplete/Blocked）
- POST /requirements/generate-testcases   带硬门控的用例生成入口
- POST /requirements/coverage             覆盖率与缺口报告
- GET  /requirements/gate                 当前生成会话状态
- POST /requirements/compatibility        历史数据兼容转换（旧格式 → 平台格式）

响应契约（统一）：
    {
      "prd_quality": {...},
      "analysis_report": {...},
      "testcase_generation": {"status": "not_started|blocked|generated|rejected", ...}
    }
"""

from __future__ import annotations

from typing import Any, Optional

from fastapi import APIRouter, Body, HTTPException
from pydantic import BaseModel, Field

from app.schemas.common import SuccessResponse
from app.test_platform.requirements.analysis import (
    render_analysis_markdown,
    render_analysis_report,
)
from app.test_platform.requirements.features import extract_feature_points
from app.test_platform.requirements.gate import (
    GateOverride,
    GateOverrideAuditError,
    GateOverrideNotAuthorizedError,
    check_generation_gate,
    clear_session,
    get_session,
    record_generated,
    start_session,
    update_session,
)
from app.test_platform.requirements.legacy_adapter import (
    classify_cases,
    convert_legacy_cases,
)
from app.test_platform.services.requirement_quality_service import (
    RequirementQualityService,
    TestCaseGenerationService,
    build_compatibility_report,
    build_coverage_report,
    query_cases_by_feature,
)

router = APIRouter(
    prefix="/projects/{project_identifier}/requirements",
    tags=["需求质量门控"],
)


# ---------------------------------------------------------------------------
# 请求模型
# ---------------------------------------------------------------------------

class RequirementAnalyzeRequest(BaseModel):
    """需求分析请求"""

    requirement_text: str = Field(..., min_length=1, description="需求原文（Markdown/纯文本）")
    requirement_id: str = Field(default="", description="需求编号")
    source: str = Field(default="", description="来源位置（文件名/章节）")
    analysis_payload: Optional[Any] = Field(
        default=None,
        description="可选：结构化需求分析结果（LLM 输出，dict 或 JSON 字符串）。解析失败自动降级 Incomplete",
    )
    session_key: Optional[str] = Field(default=None, description="生成会话标识（用于状态跟踪）")
    override_token: Optional[str] = Field(
        default=None,
        description="人工放行凭证（服务端受控）：必须与 REQUIREMENT_GATE_OVERRIDE_TOKEN 一致，默认未启用",
    )
    override_reason: Optional[str] = Field(
        default=None, description="人工放行原因（必填，不少于 10 个字符，用于审计）"
    )

    def build_override(self, operator: str = "") -> Optional[GateOverride]:
        """按需构造人工放行请求；未提供凭证/原因时返回 None。"""
        if not self.override_token and not self.override_reason:
            return None
        return GateOverride(
            token=self.override_token or "",
            reason=self.override_reason or "",
            operator=operator,
            requirement_id=self.requirement_id or "",
            source=self.source or "",
        )


class RequirementGenerateRequest(RequirementAnalyzeRequest):
    """带门控的用例生成请求"""

    test_cases: list[dict[str, Any]] = Field(
        default_factory=list,
        description="待校验/入库的测试用例（由 Agent 生成后提交；为空时只做门控与准备）",
    )


class RequirementCoverageRequest(BaseModel):
    """覆盖报告请求"""

    requirement_text: str = Field(default="", description="需求原文（用于抽取功能点）")
    requirement_id: str = Field(default="", description="需求编号")
    feature_points: list[dict[str, Any]] = Field(
        default_factory=list, description="功能点清单（优先使用；为空则从需求原文抽取）"
    )
    test_cases: list[dict[str, Any]] = Field(default_factory=list, description="测试用例列表")
    results_by_case: dict[str, Any] = Field(
        default_factory=dict, description="用例 → 执行结果映射（可选）"
    )
    feature_id: str = Field(default="", description="可选：只查询某个功能点的用例")


class RequirementCompatibilityRequest(BaseModel):
    """历史数据兼容转换请求"""

    test_cases: list[dict[str, Any]] = Field(
        default_factory=list, description="旧格式用例（action/target/expected_results）"
    )
    feature_id: str = Field(default="", description="统一补齐的功能点编号（可选）")
    requirement_id: str = Field(default="", description="统一补齐的需求编号（可选）")


# ---------------------------------------------------------------------------
# 路由
# ---------------------------------------------------------------------------

@router.post(
    "/analyze",
    response_model=SuccessResponse[dict[str, Any]],
    summary="需求分析并判定质量状态",
    description=(
        "结构化需求分析 → Complete/Incomplete/Blocked 判定。"
        "不完整需求也能生成分析报告；Incomplete/Blocked 不产生正式测试用例。"
    ),
)
async def analyze_requirement(
    project_identifier: str,
    body: RequirementAnalyzeRequest = Body(...),
) -> SuccessResponse[dict[str, Any]]:
    service = RequirementQualityService()
    try:
        result = service.analyze(
            body.requirement_text,
            requirement_id=body.requirement_id,
            source=body.source,
            analysis_payload=body.analysis_payload,
        )
        override = body.build_override(operator=project_identifier)
    except GateOverrideNotAuthorizedError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc

    if body.session_key:
        update_session(body.session_key, result)

    try:
        allowed, reasons = check_generation_gate(result, override=override)
    except GateOverrideNotAuthorizedError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except GateOverrideAuditError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc

    data = {
        "prd_quality": {
            "status": result.status.value,
            "status_label": result.status.label,
            "can_generate_testcases": result.can_generate_testcases,
            "missing_count": len(result.missing_items),
            "ambiguous_count": len(result.ambiguous_items),
            "blocked_count": len(result.blocked_reasons),
        },
        "analysis_report": render_analysis_report(result),
        "testcase_generation": {
            "status": "not_started" if allowed else "blocked",
            "reasons": reasons,
            "created_count": 0,
        },
        "analysis_report_markdown": render_analysis_markdown(
            result, requirement_id=body.requirement_id, source=body.source
        ),
    }
    return SuccessResponse(success=True, data=data)


@router.post(
    "/generate-testcases",
    response_model=SuccessResponse[dict[str, Any]],
    summary="带硬门控的测试用例生成入口",
    description=(
        "if not analysis.can_generate_testcases: return quality_report。"
        "只有 Complete 才会进入契约校验与覆盖追踪。"
    ),
)
async def generate_testcases(
    project_identifier: str,
    body: RequirementGenerateRequest = Body(...),
) -> SuccessResponse[dict[str, Any]]:
    service = TestCaseGenerationService()

    if body.session_key:
        start_session(
            body.session_key,
            requirement_id=body.requirement_id,
            source=body.source,
        )

    provided_cases = list(body.test_cases)

    def _generator(feature_points, analysis):  # noqa: ANN001
        return provided_cases

    try:
        outcome = service.generate(
            body.requirement_text,
            requirement_id=body.requirement_id,
            source=body.source,
            analysis_payload=body.analysis_payload,
            generator=_generator if provided_cases else None,
            override=body.build_override(operator=project_identifier),
        )
    except GateOverrideNotAuthorizedError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except GateOverrideAuditError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc

    if body.session_key:
        updated = update_session(body.session_key, outcome.analysis)
        if not outcome.blocked:
            record_generated(body.session_key, [c.get("id", "") for c in outcome.cases])
        else:
            updated.reasons = list(outcome.analysis.blocked_reasons)

    return SuccessResponse(success=True, data=outcome.to_dict())


@router.post(
    "/coverage",
    response_model=SuccessResponse[dict[str, Any]],
    summary="覆盖率与缺口报告",
    description="功能点 → 测试点 → 测试用例 → 执行结果 覆盖关系；识别未覆盖功能点与验收标准。",
)
async def coverage_report(
    project_identifier: str,
    body: RequirementCoverageRequest = Body(...),
) -> SuccessResponse[dict[str, Any]]:
    if body.feature_points:
        from app.test_platform.requirements.features import FeaturePoint

        feature_points = [FeaturePoint.from_dict(fp) for fp in body.feature_points]
    else:
        feature_points = extract_feature_points(
            body.requirement_text, requirement_id=body.requirement_id
        )

    report = build_coverage_report(
        feature_points, body.test_cases, requirement_id=body.requirement_id
    )

    data: dict[str, Any] = {"coverage": report}

    if body.feature_id:
        data["cases_by_feature"] = query_cases_by_feature(body.feature_id, body.test_cases)

    data["compatibility"] = build_compatibility_report(body.test_cases)
    return SuccessResponse(success=True, data=data)


@router.get(
    "/gate",
    response_model=SuccessResponse[dict[str, Any]],
    summary="查询生成会话的门控状态",
    description="返回该会话最近一次需求分析的判定结果与生成状态（not_started/generated/blocked）。",
)
async def get_gate_status(
    project_identifier: str,
    session_key: Optional[str] = None,
) -> SuccessResponse[dict[str, Any]]:
    session = get_session(session_key)
    if session is None:
        return SuccessResponse(
            success=True,
            data={
                "session_key": session_key,
                "status": "not_started",
                "exists": False,
                "reasons": [],
            },
        )
    return SuccessResponse(success=True, data={"exists": True, **session.to_dict()})


@router.delete(
    "/gate",
    response_model=SuccessResponse[dict[str, Any]],
    summary="清理生成会话",
)
async def clear_gate_status(
    project_identifier: str,
    session_key: Optional[str] = None,
) -> SuccessResponse[dict[str, Any]]:
    clear_session(session_key)
    return SuccessResponse(success=True, data={"cleared": True, "session_key": session_key})


@router.post(
    "/compatibility",
    response_model=SuccessResponse[dict[str, Any]],
    summary="历史数据兼容转换",
    description="旧格式（action/target/expected_results）→ 平台格式（step/result）；旧用例仍可读取与执行。",
)
async def compatibility_convert(
    project_identifier: str,
    body: RequirementCompatibilityRequest = Body(...),
) -> SuccessResponse[dict[str, Any]]:
    converted = convert_legacy_cases(body.test_cases)

    if body.feature_id or body.requirement_id:
        from app.test_platform.requirements.legacy_adapter import attach_traceability

        converted = [
            attach_traceability(
                case,
                feature_id=body.feature_id,
                requirement_id=body.requirement_id,
            )
            for case in converted
        ]

    return SuccessResponse(
        success=True,
        data={
            "converted_cases": converted,
            "classification": classify_cases(converted),
        },
    )
