# -*- coding: utf-8 -*-
"""阶段一：生成流程阶段机。

流程：
    需求上传 → 结构化需求分析 → 质量判定
      → Complete：生成测试点和测试用例
      → Incomplete/Blocked：输出报告并暂停用例生成
      → 测试用例契约校验 → 执行、报告、追踪

本模块只负责"阶段推进与拒绝原因"，具体执行（LLM/落库）由调用方注入，
便于单元测试与离线验证。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Optional

from app.test_platform.requirements.analysis import (
    PRDAnalysisResult,
    PRDQualityStatus,
    TestCaseGenerationStatus,
    render_analysis_markdown,
    render_analysis_report,
)
from app.test_platform.requirements.coverage import (
    build_coverage_matrix,
    render_coverage_report,
)
from app.test_platform.requirements.features import extract_feature_points
from app.test_platform.requirements.gate import (
    GateOverride,
    RequirementNotReadyError,
    build_gated_response,
    build_generated_response,
    check_generation_gate,
    mark_generation_status,
)


class PipelineStage(str, Enum):
    """流程阶段"""

    REQUIREMENT_UPLOAD = "requirement_upload"
    ANALYSIS = "analysis"
    QUALITY_GATE = "quality_gate"
    ANALYSIS_REPORT = "analysis_report"          # Incomplete/Blocked 的出口
    TEST_POINT_GENERATION = "test_point_generation"
    TESTCASE_GENERATION = "testcase_generation"
    CONTRACT_VALIDATION = "contract_validation"
    EXECUTION = "execution"
    REPORT = "report"
    TRACKING = "tracking"


# Complete 之后的阶段顺序
_COMPLETE_PATH: tuple[PipelineStage, ...] = (
    PipelineStage.TEST_POINT_GENERATION,
    PipelineStage.TESTCASE_GENERATION,
    PipelineStage.CONTRACT_VALIDATION,
    PipelineStage.EXECUTION,
    PipelineStage.REPORT,
    PipelineStage.TRACKING,
)

# 未通过门控时的出口阶段
_PAUSED_PATH: tuple[PipelineStage, ...] = (PipelineStage.ANALYSIS_REPORT,)


@dataclass
class PipelineResult:
    """一次流程推进的结果。"""

    stage: PipelineStage
    status: PRDQualityStatus
    can_generate_testcases: bool
    analysis: PRDAnalysisResult
    next_stages: list[PipelineStage] = field(default_factory=list)
    feature_points: list[Any] = field(default_factory=list)
    reasons: list[str] = field(default_factory=list)
    analysis_report: dict[str, Any] = field(default_factory=dict)
    analysis_report_markdown: str = ""
    response: dict[str, Any] = field(default_factory=dict)

    @property
    def paused(self) -> bool:
        return not self.can_generate_testcases

    def to_dict(self) -> dict[str, Any]:
        return {
            "stage": self.stage.value,
            "status": self.status.value,
            "status_label": self.status.label,
            "can_generate_testcases": self.can_generate_testcases,
            "next_stages": [s.value for s in self.next_stages],
            "feature_points": [
                fp.to_dict() if hasattr(fp, "to_dict") else fp for fp in self.feature_points
            ],
            "reasons": list(self.reasons),
            "analysis_report": self.analysis_report,
            "response": self.response,
        }


def run_requirement_pipeline(
    requirement_text: str,
    *,
    requirement_id: str = "",
    source: str = "",
    analysis_payload: Any = None,
    override: Optional["GateOverride"] = None,
) -> PipelineResult:
    """推进「需求上传 → 分析 → 质量判定」并给出后续阶段。

    Args:
        requirement_text: 需求原文
        requirement_id: 需求编号
        source: 来源位置（文件名/章节）
        analysis_payload: 可选的结构化分析结果（LLM 输出，dict 或 JSON 字符串）。
            提供时以它为准（解析失败自动降级 Incomplete）；否则用确定性文本分析。
        override: 服务端受控的人工放行凭证（默认 None → 严格按 V1.2 门控）

    Returns:
        :class:`PipelineResult`；``paused=True`` 表示停在分析报告出口，不生成用例。
    """
    from app.test_platform.requirements.analysis import (
        analyze_requirement_text,
        parse_analysis_payload,
    )

    if analysis_payload is not None:
        analysis = parse_analysis_payload(analysis_payload)
        # 结构化结果缺少受影响功能点时，用文本线索补齐（可追溯性）
        if not analysis.affected_features:
            text_only = analyze_requirement_text(
                requirement_text, source=source, requirement_id=requirement_id
            )
            analysis.affected_features = text_only.affected_features
    else:
        analysis = analyze_requirement_text(
            requirement_text, source=source, requirement_id=requirement_id
        )

    allowed, reasons = check_generation_gate(analysis, override=override)

    if allowed:
        feature_points = extract_feature_points(
            requirement_text, requirement_id=requirement_id, source_location=source
        )
        return PipelineResult(
            stage=PipelineStage.QUALITY_GATE,
            status=analysis.status,
            can_generate_testcases=True,
            analysis=analysis,
            next_stages=list(_COMPLETE_PATH),
            feature_points=feature_points,
            reasons=[],
            analysis_report=render_analysis_report(analysis),
            analysis_report_markdown=render_analysis_markdown(
                analysis, requirement_id=requirement_id, source=source
            ),
            response=build_generated_response(analysis, created_count=0),
        )

    mark_generation_status(analysis, TestCaseGenerationStatus.BLOCKED)
    return PipelineResult(
        stage=PipelineStage.ANALYSIS_REPORT,
        status=analysis.status,
        can_generate_testcases=False,
        analysis=analysis,
        next_stages=list(_PAUSED_PATH),
        feature_points=[],
        reasons=reasons,
        analysis_report=render_analysis_report(analysis),
        analysis_report_markdown=render_analysis_markdown(
            analysis, requirement_id=requirement_id, source=source
        ),
        response=build_gated_response(analysis),
    )


def generate_testcases_with_gate(
    requirement_text: str,
    *,
    requirement_id: str = "",
    source: str = "",
    analysis_payload: Any = None,
    generator: Optional[Callable[..., list[dict[str, Any]]]] = None,
    validator: Optional[Callable[[dict[str, Any]], tuple[bool, list[str]]]] = None,
    override: Optional[GateOverride] = None,
) -> dict[str, Any]:
    """带硬门控的生成入口（平台级）。

    严格对应：

    .. code-block:: python

        if not analysis.can_generate_testcases:
            return quality_report

    Args:
        requirement_text: 需求原文
        requirement_id: 需求编号
        source: 来源位置
        analysis_payload: 可选结构化分析结果
        generator: 通过门控后的用例生成函数 ``(feature_points, analysis) -> list[case]``。
            None 时只做门控与契约校验准备，返回空用例列表（供 Agent/LLM 自行填充）。
        validator: 用例契约校验函数，默认使用
            :func:`app.test_platform.testcase_contract.validate_contract`。

    Returns:
        契约响应体：

        - ``prd_quality``: 需求质量摘要
        - ``analysis_report``: 分析报告（含缺失项/歧义项/阻断原因）
        - ``testcase_generation``: ``status`` 为 not_started/blocked/generated/rejected
        - 另外附带 ``feature_points`` / ``coverage`` / ``contract_validation``
    """
    pipeline = run_requirement_pipeline(
        requirement_text,
        requirement_id=requirement_id,
        source=source,
        analysis_payload=analysis_payload,
        override=override,
    )

    # 硬门控：不满足测试设计条件 → 直接返回质量报告，不进入用例生成
    if not pipeline.can_generate_testcases:
        response = dict(pipeline.response)
        response["stage"] = pipeline.stage.value
        response["testcase_generation"]["status"] = TestCaseGenerationStatus.BLOCKED.value
        response["analysis_report_markdown"] = pipeline.analysis_report_markdown
        return response

    cases: list[dict[str, Any]] = []
    if generator is not None:
        cases = list(generator(pipeline.feature_points, pipeline.analysis) or [])

    # 覆盖率与契约校验
    matrix = build_coverage_matrix(
        pipeline.feature_points, cases, requirement_id=requirement_id
    )
    coverage_report = render_coverage_report(matrix)

    if validator is None:
        from app.test_platform.testcase_contract import validate_contract

        validator = validate_contract  # type: ignore[assignment]

    contract_problems: list[dict[str, Any]] = []
    trace_problems: list[dict[str, Any]] = []

    from app.test_platform.requirements.coverage import (
        validate_case_traceability,
    )

    for idx, case in enumerate(cases, start=1):
        valid, problems = validator(case)  # type: ignore[misc]
        if not valid:
            contract_problems.append({
                "index": idx,
                "case_id": case.get("id", "unknown"),
                "problems": problems,
            })
        trace_issues = validate_case_traceability(case, require_traceability=True)
        if trace_issues:
            trace_problems.append({
                "index": idx,
                "case_id": case.get("id", "unknown"),
                "problems": trace_issues,
            })

    status = (
        TestCaseGenerationStatus.GENERATED
        if cases
        else TestCaseGenerationStatus.NOT_STARTED
    )
    mark_generation_status(pipeline.analysis, status)

    from app.test_platform.services.requirement_quality_service import (
        build_requirements_section,
    )

    return {
        "prd_quality": build_generated_response(
            pipeline.analysis, created_count=len(cases)
        )["prd_quality"],
        "analysis_report": pipeline.analysis_report,
        "testcase_generation": {
            "status": status.value,
            "reasons": [],
            "created_count": len(cases),
        },
        "stage": PipelineStage.CONTRACT_VALIDATION.value,
        "feature_points": [fp.to_dict() for fp in pipeline.feature_points],
        "cases": cases,
        "requirements": build_requirements_section(
            pipeline.analysis, pipeline.feature_points, cases, requirement_id
        ),
        "coverage": coverage_report,
        "contract_validation": {
            "passed": not contract_problems and not trace_problems,
            "contract_problems": contract_problems,
            "traceability_problems": trace_problems,
        },
        "analysis_report_markdown": pipeline.analysis_report_markdown,
    }


__all__ = [
    "PipelineStage",
    "PipelineResult",
    "RequirementNotReadyError",
    "run_requirement_pipeline",
    "generate_testcases_with_gate",
]
