# -*- coding: utf-8 -*-
"""阶段二：覆盖关系 —— 功能点 → 测试点 → 测试用例 → 执行结果。

配套历史数据兼容策略：
- 没有 ``feature_id`` 的旧用例允许读取，被识别为 ``untraced``（未追踪），不计入缺口；
- 新生成用例必须带 ``feature_id``（由 :func:`validate_case_traceability` 约束）；
- 不强制一次性迁移历史数据。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Iterable, Optional

from app.test_platform.requirements.features import FeaturePoint, normalize_feature_id


# 追踪字段（测试用例契约扩展）
TRACE_FIELDS: tuple[str, ...] = (
    "requirement_id",
    "feature_id",
    "acceptance_id",
    "source_location",
)

# 「旧用例」判定只看需求来源类字段：feature_id 是阶段二新引入的强制字段，
# 仅缺 feature_id 的用例属于「新用例未补齐追踪」，必须被校验拦下而不是当作历史数据放行。
LEGACY_MARKER_FIELDS: tuple[str, ...] = (
    "requirement_id",
    "acceptance_id",
    "source_location",
)

FEATURE_ID_RE = re.compile(r"(?:FP|FEAT)[-_ ]?(\d+)", re.IGNORECASE)
# 指标记：匹配 FP-001 / 功能点1 这类前缀（数字紧跟前缀，避免 FP-001-AC-02 误判为 AC 编号）
_FEATURE_PREFIX_RE = re.compile(
    r"(?:FP|FEAT|功能点|功能)\s*[-_ ]?\s*[0-9０-９]+", re.IGNORECASE
)
# 验收标记：AC / 验收标准 后跟编号
_AC_MARKER_RE = re.compile(
    r"(?:AC|ACC|验收标准|验收项)\s*[-_ ]?\s*([0-9０-９]+)", re.IGNORECASE
)


def _ac_seq_and_feature_prefix(text: str) -> tuple[Optional[int], str]:
    """解析编号文本，返回 (验收序号, 功能点前缀)。

    - ``FP-001-AC-02`` → (2, "FP-001")
    - ``AC-2`` / ``验收标准2`` → (2, "")
    - 无法解析 → (None, "")
    """
    raw = str(text or "").strip()
    if not raw:
        return None, ""

    feature_prefix = ""
    feature_match = _FEATURE_PREFIX_RE.search(raw)
    if feature_match:
        feature_prefix = normalize_feature_id(feature_match.group(0))

    ac_match = _AC_MARKER_RE.search(raw)
    if ac_match:
        return int(ac_match.group(1)), feature_prefix

    digits = re.findall(r"\d+", raw)
    if digits:
        return int(digits[-1]), feature_prefix
    return None, ""
TP_CODE_KEYS = ("tp_code", "test_point_id", "测试点编号", "test_point")
CASE_ID_KEYS = ("id", "用例编号", "case_id", "identifier")


# ---------------------------------------------------------------------------
# 编号/取值辅助
# ---------------------------------------------------------------------------

def _case_id(case: dict[str, Any]) -> str:
    for key in CASE_ID_KEYS:
        value = case.get(key)
        if value:
            return str(value)
    return ""


def _case_title(case: dict[str, Any]) -> str:
    for key in ("title", "name", "用例标题"):
        value = case.get(key)
        if value:
            return str(value)
    return ""


def case_feature_id(case: dict[str, Any]) -> str:
    """读取用例的 feature_id；缺失时尝试从 source_location 兜底解析。"""
    raw = case.get("feature_id")
    if raw:
        return normalize_feature_id(str(raw))
    match = FEATURE_ID_RE.search(str(case.get("source_location") or ""))
    if match:
        return normalize_feature_id(match.group(0))
    return ""


def case_test_point_id(case: dict[str, Any]) -> str:
    """读取用例的测试点编号（tp_code 等）。"""
    for key in TP_CODE_KEYS:
        value = case.get(key)
        if value:
            return str(value)
    return ""


def case_acceptance_id(case: dict[str, Any]) -> str:
    raw = case.get("acceptance_id")
    if raw:
        return str(raw)
    match = _AC_MARKER_RE.search(str(case.get("source_location") or ""))
    if match:
        return match.group(0)
    return ""


def is_legacy_case(case: dict[str, Any]) -> bool:
    """旧用例判定：缺少全部需求来源类追踪字段（历史数据允许读取，不强制迁移）。

    注意：只缺 ``feature_id``（阶段二新增的强制字段）的用例**不算**旧用例，
    会被 :func:`validate_case_traceability` 判定为"未补齐追踪"。
    """
    return not any(case.get(field) for field in LEGACY_MARKER_FIELDS)


def validate_case_traceability(
    case: dict[str, Any],
    *,
    require_traceability: bool = False,
) -> list[str]:
    """校验用例是否可追溯到功能点。

    Args:
        case: 测试用例
        require_traceability: 是否为「新生成用例」语境。True 时即使缺少全部追踪字段
            也要求补齐 ``feature_id``；False（默认）时，完全无追踪字段的旧用例直接放行，
            以兼容历史数据读取与执行。

    Returns:
        问题清单（空列表表示可追踪/可放行）。
    """
    case_id = _case_id(case)
    if not require_traceability and is_legacy_case(case):
        return []

    problems: list[str] = []
    if not case_feature_id(case):
        problems.append(
            f"用例 {case_id or '(无编号)'} 缺少 feature_id，无法追溯到功能点"
        )
    if not case.get("requirement_id") and not case.get("source_location"):
        problems.append(
            f"用例 {case_id or '(无编号)'} 缺少 requirement_id / source_location，需求来源不明确"
        )
    return problems


def validate_cases_traceability(
    cases: Iterable[dict[str, Any]],
    *,
    require_traceability: bool = False,
) -> list[dict[str, Any]]:
    """批量校验，返回带问题清单的用例数组（仅包含有问题的用例）。"""
    problems: list[dict[str, Any]] = []
    for case in cases:
        issues = validate_case_traceability(
            case, require_traceability=require_traceability
        )
        if issues:
            problems.append({
                "case_id": _case_id(case),
                "title": _case_title(case)[:40],
                "problems": issues,
            })
    return problems


# ---------------------------------------------------------------------------
# 验收标准
# ---------------------------------------------------------------------------

def build_feature_criteria(feature: FeaturePoint) -> list[dict[str, Any]]:
    """为功能点生成验收标准清单（带稳定编号）。

    约定：验收标准编号 ``{feature_id}-AC-{n}``，例如 ``FP-001-AC-01``。
    """
    criteria: list[dict[str, Any]] = []
    for seq, text in enumerate(feature.acceptance_criteria or [], start=1):
        if not str(text).strip():
            continue
        criteria.append({
            "acceptance_id": f"{feature.feature_id}-AC-{seq:02d}",
            "feature_id": feature.feature_id,
            "seq": seq,
            "text": str(text).strip(),
        })
    return criteria


def _char_trigrams(text: str) -> set[str]:
    """中文友好的字符三元组（用于模糊包含判定）。"""
    compact = re.sub(r"\s+", "", str(text or ""))
    if len(compact) < 3:
        return {compact} if compact else set()
    return {compact[i:i + 3] for i in range(len(compact) - 2)}


def _text_covers_criterion(criterion_text: str, haystack: str, min_ratio: float = 0.6) -> bool:
    """判断用例文本是否覆盖某条验收标准。

    先用原文直接包含判定；中文表述常被改写，故再用字符三元组重合度兜底。
    """
    criterion = str(criterion_text or "").strip()
    if len(criterion) < 6 or not haystack:
        return False

    if criterion[:12] in haystack:
        return True

    criterion_grams = _char_trigrams(criterion)
    if not criterion_grams:
        return False
    haystack_grams = _char_trigrams(haystack)
    overlap = criterion_grams & haystack_grams
    return len(overlap) / len(criterion_grams) >= min_ratio


def _criterion_matches(criteria: dict[str, Any], case: dict[str, Any]) -> bool:
    """判断用例是否覆盖**这一条**验收标准。

    匹配优先级：
    1. 用例 ``acceptance_id`` 与该验收标准编号精确匹配；
    2. 用例 ``acceptance_id`` 与编号数字部分匹配（如 AC-1 vs FP-001-AC-01）；
    3. 用例未声明 ``acceptance_id`` 时，按验收标准原文（或字符三元组）文本兜底匹配。

    重要：用例一旦声明了 ``acceptance_id``，就**只**认这一条验收标准，
    不再对其他验收标准做文本兜底，避免"一条用例覆盖该功能点全部验收标准"的误报。
    """
    case_ac = case_acceptance_id(case)
    acceptance_id = str(criteria.get("acceptance_id") or "")

    if case_ac:
        if case_ac.upper() == acceptance_id.upper():
            return True

        case_seq, case_prefix = _ac_seq_and_feature_prefix(case_ac)
        criterion_seq, criterion_prefix = _ac_seq_and_feature_prefix(acceptance_id)
        if case_seq is None or criterion_seq is None:
            return False
        if case_seq != criterion_seq:
            return False
        # 用例侧带功能点前缀（FP-002-AC-01）时，必须功能点也一致，
        # 否则会跨功能点误匹配；用例侧只是简单编号（AC-2）时不比较前缀。
        if case_prefix and criterion_prefix and case_prefix != criterion_prefix:
            return False
        return True

    haystack = " ".join(
        [
            str(case.get("title") or case.get("name") or ""),
            str(case.get("expected_results") or ""),
            str(case.get("remarks") or ""),
            _flatten_steps(case),
        ]
    )
    return _text_covers_criterion(str(criteria.get("text") or ""), haystack)


def _flatten_steps(case: dict[str, Any]) -> str:
    steps = case.get("steps") or case.get("test_case_steps") or []
    parts: list[str] = []
    if isinstance(steps, list):
        for step in steps:
            if isinstance(step, dict):
                parts.append(str(step.get("step", "")))
                parts.append(str(step.get("result", "")))
            else:
                parts.append(str(step))
    else:
        parts.append(str(steps))
    return " ".join(parts)


# ---------------------------------------------------------------------------
# 覆盖矩阵
# ---------------------------------------------------------------------------

@dataclass
class FeatureCoverage:
    """单个功能点的覆盖情况"""

    feature_id: str
    feature_name: str
    requirement_id: str = ""
    priorities: str = ""
    case_ids: list[str] = field(default_factory=list)
    test_point_ids: list[str] = field(default_factory=list)
    acceptance_total: int = 0
    acceptance_covered: int = 0
    uncovered_criteria: list[dict[str, Any]] = field(default_factory=list)

    @property
    def case_count(self) -> int:
        return len(self.case_ids)

    @property
    def covered(self) -> bool:
        return bool(self.case_ids)

    @property
    def acceptance_rate(self) -> float:
        if self.acceptance_total == 0:
            return 1.0 if self.case_ids else 0.0
        return self.acceptance_covered / self.acceptance_total

    def to_dict(self) -> dict[str, Any]:
        return {
            "feature_id": self.feature_id,
            "feature_name": self.feature_name,
            "requirement_id": self.requirement_id,
            "priority": self.priorities,
            "covered": self.covered,
            "case_count": self.case_count,
            "case_ids": list(self.case_ids),
            "test_point_ids": list(self.test_point_ids),
            "acceptance_total": self.acceptance_total,
            "acceptance_covered": self.acceptance_covered,
            "acceptance_rate": round(self.acceptance_rate, 4),
            "uncovered_criteria": list(self.uncovered_criteria),
        }


@dataclass
class CoverageMatrix:
    """覆盖矩阵：功能点 → 测试点 → 测试用例 → 执行结果"""

    requirement_id: str = ""
    features: list[FeatureCoverage] = field(default_factory=list)
    untraced_case_ids: list[str] = field(default_factory=list)
    legacy_case_ids: list[str] = field(default_factory=list)
    orphan_case_ids: list[str] = field(default_factory=list)  # 带 feature_id 但功能点不存在
    total_cases: int = 0
    results_by_case: dict[str, Any] = field(default_factory=dict)  # 用例 → 执行结果

    # -- 指标 -------------------------------------------------------------

    @property
    def feature_count(self) -> int:
        return len(self.features)

    @property
    def covered_feature_count(self) -> int:
        return sum(1 for f in self.features if f.covered)

    @property
    def uncovered_features(self) -> list[FeatureCoverage]:
        return [f for f in self.features if not f.covered]

    @property
    def uncovered_criteria(self) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        for feature in self.features:
            out.extend(feature.uncovered_criteria)
        return out

    @property
    def feature_coverage_rate(self) -> float:
        if not self.features:
            return 0.0
        return self.covered_feature_count / self.feature_count

    @property
    def acceptance_coverage_rate(self) -> float:
        total = sum(f.acceptance_total for f in self.features)
        if total == 0:
            return 0.0
        covered = sum(f.acceptance_covered for f in self.features)
        return covered / total

    @property
    def traced_case_count(self) -> int:
        return self.total_cases - len(self.untraced_case_ids) - len(self.legacy_case_ids)

    def to_dict(self) -> dict[str, Any]:
        return {
            "requirement_id": self.requirement_id,
            "feature_count": self.feature_count,
            "covered_feature_count": self.covered_feature_count,
            "feature_coverage_rate": round(self.feature_coverage_rate, 4),
            "acceptance_coverage_rate": round(self.acceptance_coverage_rate, 4),
            "total_cases": self.total_cases,
            "traced_case_count": self.traced_case_count,
            "untraced_info": {
                "legacy_case_count": len(self.legacy_case_ids),
                "legacy_case_ids": list(self.legacy_case_ids),
                "untraced_case_count": len(self.untraced_case_ids),
                "untraced_case_ids": list(self.untraced_case_ids),
                "orphan_case_ids": list(self.orphan_case_ids),
            },
            "features": [f.to_dict() for f in self.features],
            "results_by_case": dict(self.results_by_case),
        }


def build_coverage_matrix(
    feature_points: list[FeaturePoint],
    cases: list[dict[str, Any]],
    requirement_id: str = "",
    results_by_case: Optional[dict[str, Any]] = None,
) -> CoverageMatrix:
    """构建功能点 → 测试点 → 测试用例 → 执行结果 覆盖关系。

    Args:
        feature_points: 功能点清单（feature_id 必须唯一）
        cases: 测试用例列表（新旧混排安全）
        requirement_id: 需求编号
        results_by_case: 可选的执行结果映射 {case_id: 结果}
    """
    matrix = CoverageMatrix(requirement_id=requirement_id, total_cases=len(cases))
    coverage_by_feature: dict[str, FeatureCoverage] = {}
    criteria_by_feature: dict[str, list[dict[str, Any]]] = {}

    for feature in feature_points:
        coverage = FeatureCoverage(
            feature_id=feature.feature_id,
            feature_name=feature.feature_name,
            requirement_id=feature.requirement_id or requirement_id,
            priorities=feature.priority.value,
        )
        criteria = build_feature_criteria(feature)
        coverage.acceptance_total = len(criteria)
        coverage_by_feature[feature.feature_id] = coverage
        criteria_by_feature[feature.feature_id] = criteria
        matrix.features.append(coverage)

    covered_criteria_ids: dict[str, set[str]] = {fid: set() for fid in coverage_by_feature}

    for case in cases:
        case_id = _case_id(case)
        feature_id = case_feature_id(case)

        if is_legacy_case(case):
            # 历史数据：允许读取，不计入缺口，也不视为未追踪异常
            matrix.legacy_case_ids.append(case_id)
            continue

        if not feature_id:
            matrix.untraced_case_ids.append(case_id)
            continue

        coverage = coverage_by_feature.get(feature_id)
        if coverage is None:
            # 带编号但功能点不存在：孤儿用例，保留读取但提示
            matrix.orphan_case_ids.append(case_id)
            matrix.untraced_case_ids.append(case_id)
            continue

        if case_id and case_id not in coverage.case_ids:
            coverage.case_ids.append(case_id)
        tp_id = case_test_point_id(case)
        if tp_id and tp_id not in coverage.test_point_ids:
            coverage.test_point_ids.append(tp_id)

        for criteria in criteria_by_feature.get(feature_id, []):
            if _criterion_matches(criteria, case):
                covered_criteria_ids[feature_id].add(criteria["acceptance_id"])

    for feature_id, coverage in coverage_by_feature.items():
        covered = covered_criteria_ids.get(feature_id, set())
        coverage.acceptance_covered = len(covered)
        coverage.uncovered_criteria = [
            c for c in criteria_by_feature.get(feature_id, [])
            if c["acceptance_id"] not in covered
        ]

    # 执行结果映射在报告渲染时按需回填（链路：用例 → 执行结果）
    matrix.results_by_case = dict(results_by_case or {})

    return matrix


# ---------------------------------------------------------------------------
# 缺口识别
# ---------------------------------------------------------------------------

def identify_coverage_gaps(matrix: CoverageMatrix) -> list[str]:
    """识别覆盖缺口（未覆盖功能点、未覆盖验收标准）。"""
    gaps: list[str] = []

    for feature in matrix.uncovered_features:
        gaps.append(
            f"未覆盖功能点：{feature.feature_id} {feature.feature_name}"
            f"（优先级 {feature.priorities}）"
        )

    for criteria in matrix.uncovered_criteria:
        gaps.append(
            f"未覆盖验收标准：{criteria['acceptance_id']} {criteria['text'][:40]}"
        )

    if matrix.untraced_case_ids:
        gaps.append(
            f"存在 {len(matrix.untraced_case_ids)} 条用例缺少功能点追踪（需补齐 feature_id）："
            + "、".join(matrix.untraced_case_ids[:10])
        )

    return gaps


def render_coverage_report(matrix: CoverageMatrix) -> dict[str, Any]:
    """质量报告新增部分：未覆盖功能点、未覆盖验收标准、受影响功能点、用例数量与覆盖率。"""
    data = matrix.to_dict()
    return {
        **data,
        "uncovered_features": [
            {
                "feature_id": f.feature_id,
                "feature_name": f.feature_name,
                "priority": f.priorities,
            }
            for f in matrix.uncovered_features
        ],
        "uncovered_acceptance_criteria": matrix.uncovered_criteria,
        "gaps": identify_coverage_gaps(matrix),
    }


def summary_line(matrix: CoverageMatrix) -> str:
    """简短摘要，便于日志/报告首行展示。"""
    return (
        f"用例 {matrix.total_cases} 条（可追踪 {matrix.traced_case_count} 条，"
        f"历史 {len(matrix.legacy_case_ids)} 条，未追踪 {len(matrix.untraced_case_ids)} 条）；"
        f"功能点覆盖 {matrix.covered_feature_count}/{matrix.feature_count}"
        f"（{matrix.feature_coverage_rate * 100:.1f}%）；"
        f"验收标准覆盖 {matrix.acceptance_coverage_rate * 100:.1f}%"
    )
