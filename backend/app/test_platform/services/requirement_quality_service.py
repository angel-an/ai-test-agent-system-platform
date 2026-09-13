# -*- coding: utf-8 -*-
"""需求质量与用例生成服务（阶段一 + 阶段二）。

职责：
- 需求分析 → 质量判定 → 生成门控 → 契约校验 → 覆盖追踪；
- 提供 API 契约字段（prd_quality / analysis_report / testcase_generation）；
- 提供「按功能点查询用例」「识别未覆盖功能点」等追踪查询能力。

设计约束：不接触数据库与 LangGraph 运行时，保持纯逻辑、可单测；
落库/导出由调用方（现有 ai-test-agent-system 能力）负责。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional

from app.test_platform.requirements.analysis import (
    PRDAnalysisResult,
    TestCaseGenerationStatus,
    parse_analysis_payload,
    render_analysis_markdown,
    render_analysis_report,
)
from app.test_platform.requirements.coverage import (
    CoverageMatrix,
    build_coverage_matrix,
    case_feature_id,
    identify_coverage_gaps,
    render_coverage_report,
    validate_cases_traceability,
)
from app.test_platform.requirements.features import (
    FeaturePoint,
    extract_feature_points,
)
from app.test_platform.requirements.gate import (
    GateOverride,
    RequirementNotReadyError,
    build_gated_response,
    build_generated_response,
    check_generation_gate,
    mark_generation_status,
    require_complete,
)
from app.test_platform.requirements.legacy_adapter import (
    attach_traceability,
    convert_legacy_case,
)
from app.test_platform.requirements.pipeline import (
    PipelineResult,
    generate_testcases_with_gate,
    run_requirement_pipeline,
)


@dataclass
class GenerationOutcome:
    """一次生成请求的完整结果。"""

    analysis: PRDAnalysisResult
    feature_points: list[FeaturePoint] = field(default_factory=list)
    cases: list[dict[str, Any]] = field(default_factory=list)
    coverage: Optional[CoverageMatrix] = None
    requirements: dict[str, Any] = field(default_factory=dict)
    contract_problems: list[dict[str, Any]] = field(default_factory=list)
    traceability_problems: list[dict[str, Any]] = field(default_factory=list)
    analysis_report_markdown: str = ""

    @property
    def blocked(self) -> bool:
        return not self.analysis.can_generate_testcases

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "prd_quality": {
                "status": self.analysis.status.value,
                "status_label": self.analysis.status.label,
                "can_generate_testcases": self.analysis.can_generate_testcases,
                "missing_count": len(self.analysis.missing_items),
                "ambiguous_count": len(self.analysis.ambiguous_items),
                "blocked_count": len(self.analysis.blocked_reasons),
            },
            "analysis_report": render_analysis_report(self.analysis),
            "testcase_generation": {
                "status": (
                    TestCaseGenerationStatus.BLOCKED.value
                    if self.blocked
                    else (
                        TestCaseGenerationStatus.GENERATED.value
                        if self.cases
                        else TestCaseGenerationStatus.NOT_STARTED.value
                    )
                ),
                "reasons": list(self.analysis.blocked_reasons),
                "created_count": len(self.cases),
            },
            "requirements": self.requirements,
            "feature_points": [fp.to_dict() for fp in self.feature_points],
            "cases": self.cases,
            "contract_validation": {
                "passed": not self.contract_problems and not self.traceability_problems,
                "contract_problems": self.contract_problems,
                "traceability_problems": self.traceability_problems,
            },
            "analysis_report_markdown": self.analysis_report_markdown,
        }
        if self.coverage is not None:
            payload["coverage"] = render_coverage_report(self.coverage)
        return payload


class RequirementQualityService:
    """需求质量判定服务（阶段一）。"""

    def analyze(
        self,
        requirement_text: str,
        *,
        requirement_id: str = "",
        source: str = "",
        analysis_payload: Any = None,
    ) -> PRDAnalysisResult:
        """执行需求分析；给出结构化分析结果时以它为准（解析失败降级 Incomplete）。"""
        if analysis_payload is not None:
            return parse_analysis_payload(analysis_payload)
        return run_requirement_pipeline(
            requirement_text,
            requirement_id=requirement_id,
            source=source,
        ).analysis

    def pipeline(
        self,
        requirement_text: str,
        *,
        requirement_id: str = "",
        source: str = "",
        analysis_payload: Any = None,
        override: Optional[GateOverride] = None,
    ) -> PipelineResult:
        """推进「需求上传 → 分析 → 质量判定」。

        ``override`` 为服务端受控的人工放行凭证，未通过校验时直接抛
        :class:`GateOverrideNotAuthorizedError`（fail-closed）。
        """
        return run_requirement_pipeline(
            requirement_text,
            requirement_id=requirement_id,
            source=source,
            analysis_payload=analysis_payload,
            override=override,
        )

    def gate_report(
        self,
        requirement_text: str,
        *,
        requirement_id: str = "",
        source: str = "",
        analysis_payload: Any = None,
        override: Optional[GateOverride] = None,
    ) -> dict[str, Any]:
        """只做门控判断，返回质量报告（不生成用例）。"""
        return generate_testcases_with_gate(
            requirement_text,
            requirement_id=requirement_id,
            source=source,
            analysis_payload=analysis_payload,
            override=override,
        )


class TestCaseGenerationService:
    """测试用例生成服务（带硬门控）。"""

    def generate(
        self,
        requirement_text: str,
        *,
        requirement_id: str = "",
        source: str = "",
        analysis_payload: Any = None,
        generator: Optional[Any] = None,
        validator: Optional[Any] = None,
        override: Optional[GateOverride] = None,
    ) -> GenerationOutcome:
        """执行「门控 → 生成 → 契约校验 → 覆盖追踪」。

        硬门控：``if not analysis.can_generate_testcases: return quality_report``
        人工放行必须传入服务端受控的 ``override`` 凭证。
        """
        response = generate_testcases_with_gate(
            requirement_text,
            requirement_id=requirement_id,
            source=source,
            analysis_payload=analysis_payload,
            generator=generator,
            validator=validator,
            override=override,
        )

        analysis = RequirementQualityService().analyze(
            requirement_text,
            requirement_id=requirement_id,
            source=source,
            analysis_payload=analysis_payload,
        )
        feature_points = [
            FeaturePoint.from_dict(item) for item in response.get("feature_points", [])
        ]
        cases = response.get("cases") or []
        coverage = None
        if feature_points:
            coverage = build_coverage_matrix(
                feature_points, cases, requirement_id=requirement_id
            )

        contract_validation = response.get("contract_validation") or {}

        return GenerationOutcome(
            analysis=analysis,
            feature_points=feature_points,
            cases=cases,
            coverage=coverage,
            requirements=build_requirements_section(
                analysis, feature_points, cases, requirement_id
            ),
            contract_problems=contract_validation.get("contract_problems", []),
            traceability_problems=contract_validation.get("traceability_problems", []),
            analysis_report_markdown=response.get("analysis_report_markdown", ""),
        )

    def assert_ready(
        self,
        analysis: Optional[PRDAnalysisResult],
        *,
        override: Optional[GateOverride] = None,
    ) -> None:
        """硬门控断言（供调用方在生成前调用）。

        仅接受服务端受控的人工放行凭证，不接受客户端布尔开关。
        """
        require_complete(analysis, override=override)


def resolve_sole_feature_id(feature_points: list[FeaturePoint]) -> Optional[str]:
    """仅当需求只有一个功能点时，返回可用于兜底的 feature_id。

    多功能点需求返回 None —— 禁止把用例默认绑定到第一个功能点，
    缺失 feature_id 的用例应被标记为未追踪，而不是产生错误关联。
    """
    if len(feature_points) == 1:
        return feature_points[0].feature_id
    return None


def build_requirements_section(
    analysis: PRDAnalysisResult,
    feature_points: list[FeaturePoint],
    cases: list[dict[str, Any]],
    requirement_id: str = "",
) -> dict[str, Any]:
    """需求 → 功能点 → 测试点 → 测试用例 的链路摘要。"""
    test_points: list[str] = []
    coverage_by_feature: dict[str, int] = {}
    cases_by_feature: dict[str, list[str]] = {}

    for case in cases:
        feature_id = case_feature_id(case)
        case_id = str(case.get("id") or case.get("用例编号") or "(无编号)")
        if feature_id:
            cases_by_feature.setdefault(feature_id, []).append(case_id)
            coverage_by_feature[feature_id] = coverage_by_feature.get(feature_id, 0) + 1
        tp_id = case.get("tp_code") or case.get("test_point_id") or case.get("测试点编号")
        if tp_id and str(tp_id) not in test_points:
            test_points.append(str(tp_id))

    return {
        "requirement_id": requirement_id,
        "status": analysis.status.value,
        "can_generate_testcases": analysis.can_generate_testcases,
        "feature_count": len(feature_points),
        "test_point_count": len(test_points),
        "case_count": len(cases),
        "affected_features": list(analysis.affected_features),
        "features": [
            {
                "feature_id": fp.feature_id,
                "feature_name": fp.feature_name,
                "priority": fp.priority.value,
                "case_count": coverage_by_feature.get(fp.feature_id, 0),
                "case_ids": cases_by_feature.get(fp.feature_id, []),
            }
            for fp in feature_points
        ],
    }


# ---------------------------------------------------------------------------
# 追踪查询（阶段二）
# ---------------------------------------------------------------------------

def query_cases_by_feature(
    feature_id: str,
    cases: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """查询某功能点对应的全部测试用例（含历史用例的兼容读取）。"""
    target = str(feature_id or "").strip().upper()
    if not target:
        return []
    matched: list[dict[str, Any]] = []
    for case in cases:
        case_feature = case_feature_id(case).upper()
        if case_feature == target:
            matched.append(case)
            continue
        # 兼容旧用例：source_location / remarks 里携带功能点编号
        haystack = f"{case.get('source_location') or ''} {case.get('remarks') or ''}".upper()
        if target in haystack:
            matched.append(case)
    return matched


def build_coverage_report(
    feature_points: list[FeaturePoint],
    cases: list[dict[str, Any]],
    requirement_id: str = "",
) -> dict[str, Any]:
    """构建覆盖率与缺口报告（含未覆盖功能点、未覆盖验收标准）。"""
    matrix = build_coverage_matrix(feature_points, cases, requirement_id=requirement_id)
    return render_coverage_report(matrix)


def build_compatibility_report(cases: list[dict[str, Any]]) -> dict[str, Any]:
    """历史数据兼容报告：旧用例可读、新用例可追踪、未追踪用例需补齐。"""
    from app.test_platform.requirements.legacy_adapter import classify_cases

    buckets = classify_cases(cases)
    untraced = buckets.get("untraced", [])

    return {
        "total": len(cases),
        "legacy_case_ids": buckets.get("legacy", []),
        "traced_case_ids": buckets.get("traced", []),
        "untraced_case_ids": untraced,
        "traceability_problems": validate_cases_traceability(cases),
        "gaps": (
            [f"存在 {len(untraced)} 条用例缺少功能点追踪（需补齐 feature_id）：" + "、".join(untraced[:10])]
            if untraced
            else []
        ),
    }


def to_platform_case(
    legacy_case: dict[str, Any],
    *,
    feature_id: str = "",
    requirement_id: str = "",
    acceptance_id: str = "",
    source_location: str = "",
) -> dict[str, Any]:
    """旧格式用例 → 平台格式（并补齐追踪字段）。"""
    converted = convert_legacy_case(legacy_case)
    return attach_traceability(
        converted,
        feature_id=feature_id,
        requirement_id=requirement_id,
        acceptance_id=acceptance_id,
        source_location=source_location,
    )


__all__ = [
    "GenerationOutcome",
    "RequirementQualityService",
    "TestCaseGenerationService",
    "generate_testcases_with_gate",
    "run_requirement_pipeline",
    "resolve_sole_feature_id",
    "build_requirements_section",
    "query_cases_by_feature",
    "build_coverage_report",
    "build_compatibility_report",
    "to_platform_case",
    "RequirementNotReadyError",
    "build_gated_response",
    "build_generated_response",
    "check_generation_gate",
    "mark_generation_status",
    "render_analysis_markdown",
]
