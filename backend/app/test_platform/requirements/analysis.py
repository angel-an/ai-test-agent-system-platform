# -*- coding: utf-8 -*-
"""阶段一：需求质量门控 — 需求分析结果模型 + Complete/Incomplete/Blocked 判定。

设计约束（V1.2 口径）：
- 只做「能否生成」的可靠判断，不重构全部需求字段；
- 判定必须确定性、可解释：每条结论都能回指到缺失项/歧义项/阻断原因；
- 解析失败（结构不可用）默认判定 Incomplete，而不是直接报错。

三种状态：
- Complete    满足测试设计条件 → 可以生成测试用例
- Incomplete  存在缺失但仍可理解 → 输出报告，不生成正式用例
- Blocked     目标、流程、边界无法确认 → 暂停，等待补全需求
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Optional


# ---------------------------------------------------------------------------
# 枚举
# ---------------------------------------------------------------------------

class PRDQualityStatus(str, Enum):
    """需求质量状态（V1.2）"""

    COMPLETE = "Complete"
    INCOMPLETE = "Incomplete"
    BLOCKED = "Blocked"

    @property
    def label(self) -> str:
        """中文展示名"""
        return {
            "Complete": "完整",
            "Incomplete": "不完整",
            "Blocked": "阻塞",
        }[self.value]


class TestCaseGenerationStatus(str, Enum):
    """测试用例生成状态（接口契约字段）

    说明：契约只定义 not_started / blocked / generated 三个非驳回态；
    被需求质量门禁驳回的请求走 ``rejected``（阶段一新增，用于 UI 区分"没生成"与"被拦"）。
    """

    __test__ = False  # 避免被 pytest 当作测试类收集

    NOT_STARTED = "not_started"
    BLOCKED = "blocked"
    GENERATED = "generated"
    REJECTED = "rejected"


class GapCheckKind(str, Enum):
    """缺口检查类型"""

    REQUIRED_MISSING = "required_missing"          # 最小必填缺失
    AMBIGUOUS = "ambiguous"                        # 歧义/待确认
    CONDITIONAL_MISSING = "conditional_missing"    # 条件检查未满足
    FLAGGED = "flagged"                            # 仅标记，不阻断


# ---------------------------------------------------------------------------
# 检查项定义
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class RequirementSectionSpec:
    """需求章节检查定义。

    Args:
        item: 检查项名称
        spec: 检查项说明
        aliases: 命中该章节的别名（含常见真实文档写法）
        blocking_missing: 该章节缺失时是否升级为 Blocked
        detect_markers: 命中任一标记时才做「条件检查」（例如正文出现"公式"）
        requires_markers: 条件检查要求正文同时命中的标记（例如"公式"且"精度"）
    """

    item: str
    spec: str
    aliases: tuple[str, ...]
    blocking_missing: bool = False
    detect_markers: tuple[str, ...] = ()
    requires_markers: tuple[str, ...] = ()


# 最小必填检查（8 项）
#: 别名同时覆盖简体与繁體：实测 PR-9 的 `CS Module方案説明2.0.pdf` 用繁體
#: （`字段説明`/`欄位`/`驗收標準`…），而原别名全为简体 → 章节几乎全判"缺失"。
REQUIRED_SECTIONS: tuple[RequirementSectionSpec, ...] = (
    RequirementSectionSpec(
        item="功能点编号",
        spec="功能点编号（如 FP-001 / 功能点 1）",
        aliases=("功能点编号", "功能编号", "需求编号", "功能点清单", "功能清单", "功能点",
                 "功能點編號", "功能編號", "需求編號", "功能點清單", "功能清單", "功能點"),
    ),
    RequirementSectionSpec(
        item="功能点名称",
        spec="功能点名称",
        aliases=("功能点名称", "功能名称", "功能点", "功能點名稱", "功能名稱", "功能點"),
    ),
    RequirementSectionSpec(
        item="业务目标",
        spec="业务目标/背景，说明该功能解决什么业务问题",
        aliases=("业务目标", "需求目标", "背景", "需求背景", "业务背景",
                 "業務目標", "需求目標", "需求背景", "業務背景"),
        blocking_missing=True,
    ),
    RequirementSectionSpec(
        item="角色/权限",
        spec="角色与权限，说明谁可以操作、无权限如何处理",
        aliases=("角色", "权限", "角色权限", "角色与权限", "操作角色",
                 "權限", "角色權限", "角色與權限"),
        detect_markers=("权限",),
        requires_markers=("无权限", "无权", "拒绝访问", "越权", "权限不足"),
    ),
    RequirementSectionSpec(
        item="前置条件",
        spec="前置条件",
        aliases=("前置条件", "前提条件", "前置", "前置條件", "前提條件"),
    ),
    RequirementSectionSpec(
        item="操作流程",
        spec="操作流程/主流程，可据此推导测试步骤",
        aliases=("操作流程", "业务流程", "主流程", "流程说明", "功能流程", "操作步骤",
                 "業務流程", "流程説明", "操作步驟"),
        blocking_missing=True,
    ),
    RequirementSectionSpec(
        item="输入与数据规则",
        spec="输入与数据规则（字段、类型、必填、取值范围）",
        aliases=("输入", "输入规则", "数据规则", "字段说明", "输入与数据规则", "字段规则",
                 "輸入", "輸入規則", "數據規則", "字段説明", "輸入與數據規則", "字段規則",
                 "欄位", "欄位説明", "欄位規則"),
    ),
    RequirementSectionSpec(
        item="验收标准",
        spec="验收标准/验收条件，缺失时无法判断用例通过口径",
        aliases=("验收标准", "验收条件", "验收口径", "验收准则", "定义完成",
                 "驗收標準", "驗收條件", "驗收口徑", "驗收準則", "定義完成"),
        # 按 V1.2 口径：缺失验收标准 → Incomplete（存在缺失但仍可理解）；
        # 只有目标/流程无法确认才升级为 Blocked。
    ),
)

# 基础结果检查（2 项）
BASE_RESULT_SECTIONS: tuple[RequirementSectionSpec, ...] = (
    RequirementSectionSpec(
        item="正常结果",
        spec="正常结果/正常场景预期",
        aliases=("正常结果", "正常场景", "预期结果", "正常流程结果",
                 "正常結果", "正常場景", "預期結果", "正常流程結果"),
    ),
    RequirementSectionSpec(
        item="基本业务规则",
        spec="基本业务规则",
        aliases=("业务规则", "基本业务规则", "规则说明", "校验规则",
                 "業務規則", "基本業務規則", "規則説明", "校驗規則"),
    ),
)

# 条件检查（5 项）：涉及某类逻辑时，必须补齐对应规则
CONDITIONAL_SECTIONS: tuple[RequirementSectionSpec, ...] = (
    RequirementSectionSpec(
        item="计算公式与精度",
        spec="涉及计算时，必须给出公式与精度",
        aliases=("公式", "计算规则", "计算方式", "精度", "舍入规则",
                 "計算規則", "計算方式", "捨入規則"),
        detect_markers=("公式", "计算", "金额", "额度", "积分", "折扣", "利息", "比例",
                        "計算", "金額", "額度", "積分", "折扣", "利息"),
        requires_markers=("公式", "精度", "小数位", "舍入", "进位", "四舍五入",
                          "小數位", "捨入", "進位", "四捨五入"),
    ),
    RequirementSectionSpec(
        item="状态流转",
        spec="涉及状态时，必须给出状态流转（from → action → to）",
        aliases=("状态流转", "状态机", "状态变更", "状态说明",
                 "狀態流轉", "狀態機", "狀態變更", "狀態説明"),
        detect_markers=("状态", "冻结", "解冻", "注销", "审核", "生效", "失效",
                        "狀態", "凍結", "解凍", "註銷", "審核"),
        requires_markers=("状态流转", "状态变更", "状态机", "流转", "→", "->",
                          "狀態流轉", "狀態變更", "狀態機", "流轉"),
    ),
    RequirementSectionSpec(
        item="跨系统失败与重试",
        spec="涉及跨系统时，必须给出失败、超时、重试处理",
        aliases=("失败处理", "超时", "重试", "异常处理", "容错",
                 "失敗處理", "超時", "重試", "異常處理", "容錯"),
        detect_markers=("跨系统", "第三方", "外部系统", "接口调用", "回调", "同步", "对账",
                        "跨系統", "外部系統", "接口調用", "回調", "對賬"),
        requires_markers=("超时", "重试", "失败处理", "降级", "补偿",
                          "超時", "重試", "失敗處理", "降級", "補償"),
    ),
    RequirementSectionSpec(
        item="无权限处理",
        spec="涉及权限时，必须给出无权限处理",
        aliases=("无权限", "无权限处理", "权限不足", "越权处理",
                 "無權限", "無權限處理", "權限不足", "越權處理"),
        detect_markers=("权限", "角色", "鉴权", "授权", "權限", "鑒權", "授權"),
        requires_markers=("无权限", "无权", "拒绝访问", "越权", "权限不足",
                          "無權限", "無權", "拒絕訪問", "越權", "權限不足"),
    ),
    RequirementSectionSpec(
        item="并发与幂等",
        spec="涉及并发时，必须给出幂等和重复提交处理",
        aliases=("并发", "幂等", "重复提交", "并发控制", "併發", "冪等", "重複提交", "併發控制"),
        detect_markers=("并发", "批量", "重复提交", "同时", "锁", "多线程",
                        "併發", "重複提交", "同時", "鎖", "多線程"),
        requires_markers=("幂等", "重复提交", "并发控制", "加锁", "唯一约束", "去重",
                          "冪等", "重複提交", "併發控制", "加鎖", "唯一約束"),
    ),
)

# 明确不参与判定 / 不阻断的项：菜单路径缺失不影响分析继续
NON_BLOCKING_ITEMS: tuple[str, ...] = ("菜单路径",)

# ---------------------------------------------------------------------------
# 文档类型适配（B1，2026-09-24）
# ---------------------------------------------------------------------------
#: 触发「字段规格类文档」适配所需的最小结构化字段规则数。
#: 依据：`CS Module方案説明2.0.pdf` 这类**字段规格/方案说明**文档没有 PRD 章节体
#: （业务目标/操作流程天然不存在），但能产出 128 条字段规则、19 条约束。
#: 少于该阈值说明结构化证据不足，仍按 PRD 口径严格判定。
FIELD_SPEC_MIN_FIELD_RULES = 10
#: 文档类型标识
FIELD_SPEC_PROFILE = "field_spec"
#: 字段规格类文档中「PRD 专属、不适用」的章节：不再计入缺失，也不阻断
FIELD_SPEC_NOT_APPLICABLE_ITEMS: tuple[str, ...] = (
    "功能点编号", "功能点名称", "业务目标", "前置条件",
    "操作流程", "验收标准", "正常结果", "基本业务规则",
)


# ---------------------------------------------------------------------------
# 数据结构
# ---------------------------------------------------------------------------

@dataclass
class RequirementGap:
    """单个缺口/风险项"""

    item: str
    kind: GapCheckKind
    detail: str
    blocking: bool = False
    evidence: str = ""

    def to_dict(self) -> dict[str, Any]:
        data: dict[str, Any] = {
            "item": self.item,
            "kind": self.kind.value,
            "detail": self.detail,
        }
        if self.blocking:
            data["blocking"] = True
        if self.evidence:
            data["evidence"] = self.evidence
        return data

    @staticmethod
    def from_dict(d: Any) -> "RequirementGap":
        """把任意结构转换为缺口项（**不抛异常**）。

        非法结构（字符串/数字/None/嵌套列表等）统一降级为
        ``RequirementGap(kind=AMBIGUOUS, detail=str(value))``，
        由调用方（通常是 parse_analysis_payload）决定是否降级为 Incomplete。
        """
        if isinstance(d, RequirementGap):
            return d

        if not isinstance(d, dict):
            # 字符串/数字/None/列表等非法值：保留原文，不丢弃信息也不抛异常
            text = "" if d is None else str(d)
            return RequirementGap(
                item="未结构化缺口项",
                kind=GapCheckKind.AMBIGUOUS,
                detail=text or "(空缺口项)",
            )

        kind_raw = str(d.get("kind") or GapCheckKind.REQUIRED_MISSING.value)
        try:
            kind = GapCheckKind(kind_raw)
        except ValueError:
            kind = GapCheckKind.REQUIRED_MISSING

        raw_item = d.get("item")
        raw_detail = d.get("detail")
        raw_evidence = d.get("evidence")
        return RequirementGap(
            item=str(raw_item) if raw_item is not None else "",
            kind=kind,
            detail=str(raw_detail) if raw_detail is not None else "",
            blocking=bool(d.get("blocking", False)),
            evidence=str(raw_evidence) if raw_evidence is not None else "",
        )


@dataclass
class PRDAnalysisResult:
    """需求分析结果模型（阶段一核心）。

    Fields:
        status: Complete / Incomplete / Blocked
        can_generate_testcases: 是否允许进入测试用例生成
        missing_items: 缺失项（必填缺失 + 条件检查缺失）
        ambiguous_items: 歧义项/待确认项
        blocked_reasons: 阻断原因（status=Blocked 时非空）
        affected_features: 受影响功能点（可追溯到功能点的缺失/歧义）
        flagged_items: 仅标记不阻断的项（如优先级缺失标记"待确认"）
        checks: 全量检查明细（便于 UI/报告展示）
        parse_error: 结构化解析失败原因（非空表示走了降级判定）
    """

    status: PRDQualityStatus = PRDQualityStatus.INCOMPLETE
    can_generate_testcases: bool = False
    missing_items: list[RequirementGap] = field(default_factory=list)
    ambiguous_items: list[RequirementGap] = field(default_factory=list)
    blocked_reasons: list[str] = field(default_factory=list)
    affected_features: list[str] = field(default_factory=list)
    flagged_items: list[RequirementGap] = field(default_factory=list)
    checks: list[RequirementGap] = field(default_factory=list)
    parse_error: Optional[str] = None
    #: 文档类型档案（当前仅 "field_spec"）：为空表示按 PRD 口径判定
    applicable_profile: str = ""
    #: 档案内「不适用」的章节（不计入缺失、不阻断），保留以便审计
    not_applicable_items: list[RequirementGap] = field(default_factory=list)
    profile_reason: str = ""
    testcase_generation_status: TestCaseGenerationStatus = (
        TestCaseGenerationStatus.NOT_STARTED
    )

    # -- 便捷属性 ---------------------------------------------------------

    @property
    def is_complete(self) -> bool:
        return self.status == PRDQualityStatus.COMPLETE

    @property
    def is_field_spec(self) -> bool:
        return self.applicable_profile == FIELD_SPEC_PROFILE

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status.value,
            "status_label": self.status.label,
            "can_generate_testcases": self.can_generate_testcases,
            "missing_items": [g.to_dict() for g in self.missing_items],
            "ambiguous_items": [g.to_dict() for g in self.ambiguous_items],
            "blocked_reasons": list(self.blocked_reasons),
            "affected_features": list(self.affected_features),
            "flagged_items": [g.to_dict() for g in self.flagged_items],
            "checks": [g.to_dict() for g in self.checks],
            "parse_error": self.parse_error,
            "applicable_profile": self.applicable_profile,
            "not_applicable_items": [g.to_dict() for g in self.not_applicable_items],
            "profile_reason": self.profile_reason,
            "testcase_generation_status": self.testcase_generation_status.value,
        }

    @staticmethod
    def from_dict(d: dict[str, Any]) -> "PRDAnalysisResult":
        if not isinstance(d, dict):
            return _incomplete_from_parse_error(
                f"结构化需求分析结果类型非法: {type(d).__name__}"
            )
        status = _coerce_status(d.get("status"))
        result = PRDAnalysisResult(
            status=status,
            can_generate_testcases=bool(d.get("can_generate_testcases", False)),
            missing_items=_coerce_gap_list(d.get("missing_items")),
            ambiguous_items=_coerce_gap_list(d.get("ambiguous_items")),
            blocked_reasons=_coerce_str_list(d.get("blocked_reasons")),
            affected_features=_coerce_str_list(d.get("affected_features")),
            flagged_items=_coerce_gap_list(d.get("flagged_items")),
            checks=_coerce_gap_list(d.get("checks")),
            parse_error=d.get("parse_error"),
            applicable_profile=str(d.get("applicable_profile") or ""),
            not_applicable_items=_coerce_gap_list(d.get("not_applicable_items")),
            profile_reason=str(d.get("profile_reason") or ""),
        )
        # 门控不变量：只有 Complete 才允许生成正式用例。
        # 注意：字段规格档案（applicable_profile=field_spec）**只由确定性分析器**判定，
        # payload（LLM 结果）不得自行声明该档案来绕过门控——因此这里不放宽。
        if result.status != PRDQualityStatus.COMPLETE:
            result.can_generate_testcases = False
        return result


def _coerce_gap_list(value: Any) -> list[RequirementGap]:
    """把任意值转为缺口项列表（绝不抛异常）。"""
    if value is None:
        return []
    if isinstance(value, (list, tuple, set)):
        return [RequirementGap.from_dict(item) for item in value]
    return [RequirementGap.from_dict(value)]


def _gap_list_is_malformed(value: Any) -> bool:
    """判断缺口列表是否为非法结构（含非字典元素）。

    这类结构无法信任其语义，必须降级为 Incomplete，而不是沿用 payload 里的 status。
    """
    if value is None:
        return False
    if isinstance(value, (list, tuple, set)):
        return any(not isinstance(item, dict) for item in value)
    return not isinstance(value, dict)


def _has_any_gap(value: Any) -> bool:
    """判断缺口字段是否包含实际条目（非空）。"""
    if value is None:
        return False
    if isinstance(value, (list, tuple, set)):
        return len(value) > 0
    if isinstance(value, dict):
        return len(value) > 0
    return bool(str(value).strip())


def _coerce_str_list(value: Any) -> list[str]:
    """把任意值转为字符串列表（绝不抛异常）。"""
    if value is None:
        return []
    if isinstance(value, (list, tuple, set)):
        return [str(item) for item in value if item is not None]
    if isinstance(value, dict):
        return [f"{k}: {v}" for k, v in value.items()]
    return [str(value)]


# ---------------------------------------------------------------------------
# 结构化结果解析（LLM 输出 → PRDAnalysisResult）
# ---------------------------------------------------------------------------

def parse_analysis_payload(payload: Any) -> PRDAnalysisResult:
    """解析结构化需求分析结果。

    - payload 可以是 dict，也可以是 JSON 字符串（含 ```json 代码块包裹）。
    - **任何**解析失败（非法 JSON、缺 status、缺口项结构非法等）都默认判定为
      Incomplete（不抛异常），并在 ``parse_error`` 中记录原因，
      保证「需求分析不会因缺失内容直接失败」。
    """
    try:
        return _parse_analysis_payload_inner(payload)
    except Exception as exc:  # noqa: BLE001 — 门控要求 fail-safe：任何异常都降级为 Incomplete
        return _incomplete_from_parse_error(
            f"结构化需求分析结果解析异常（{type(exc).__name__}）: {exc}"
        )


def _parse_analysis_payload_inner(payload: Any) -> PRDAnalysisResult:
    raw: Any = payload
    if isinstance(payload, str):
        text = payload.strip()
        fenced = re.search(r"```(?:json)?\s*(.+?)\s*```", text, re.DOTALL)
        if fenced:
            text = fenced.group(1).strip()
        if not text:
            return _incomplete_from_parse_error("结构化需求分析结果为空")
        try:
            raw = json.loads(text)
        except json.JSONDecodeError as exc:
            return _incomplete_from_parse_error(f"结构化需求分析结果解析失败: {exc}")

    if not isinstance(raw, dict):
        return _incomplete_from_parse_error(
            f"结构化需求分析结果类型非法: {type(raw).__name__}"
        )

    # 兼容：analysis_report 子对象里携带缺口清单
    normalized = dict(raw)
    if "analysis_report" in normalized and "missing_items" not in normalized:
        report = normalized.get("analysis_report")
        if isinstance(report, dict):
            for key in ("missing_items", "ambiguous_items", "blocked_reasons",
                        "affected_features", "flagged_items"):
                if key in report and key not in normalized:
                    normalized[key] = report[key]

    # 兼容：prd_quality 子对象里携带状态
    if "status" not in normalized and "prd_quality" in normalized:
        quality = normalized.get("prd_quality")
        if isinstance(quality, dict) and "status" in quality:
            normalized["status"] = quality["status"]

    if "status" not in normalized:
        return _incomplete_from_parse_error("结构化需求分析结果缺少 status 字段")

    raw_status = normalized.get("status")
    if not isinstance(raw_status, (str, PRDQualityStatus)):
        return _incomplete_from_parse_error(
            f"结构化需求分析结果的 status 字段类型非法: {type(raw_status).__name__}"
        )

    # 结构性降级：声明 Complete 但同时给出缺口/阻断内容属于自相矛盾，不可信。
    # 解析失败或结构不可用时一律按 Incomplete 处理（不信任 payload 的乐观声明）。
    declared_status = _coerce_status(normalized.get("status"))
    if declared_status == PRDQualityStatus.COMPLETE:
        blocking_signals = [
            key for key in ("missing_items", "blocked_reasons")
            if _has_any_gap(normalized.get(key))
        ]
        if blocking_signals:
            return _incomplete_from_parse_error(
                "结构化需求分析结果自相矛盾：status=Complete 但同时给出 "
                + "、".join(blocking_signals)
                + "，已降级为 Incomplete"
            )

    can_generate_raw = normalized.get("can_generate_testcases")
    if can_generate_raw is not None and not isinstance(can_generate_raw, bool):
        if isinstance(can_generate_raw, str):
            can_generate_raw = can_generate_raw.strip().lower() in {"1", "true", "yes", "y"}
        else:
            can_generate_raw = bool(can_generate_raw)
        normalized["can_generate_testcases"] = can_generate_raw

    # 缺口类字段若为非法结构（字符串/数字/混合类型/字典），语义不可信 → 整体降级为 Incomplete。
    # 必须在归一化之前判定，否则非法值会被包装成合法列表而漏判。
    malformed_keys = [
        key for key in ("missing_items", "ambiguous_items", "flagged_items", "checks")
        if _gap_list_is_malformed(normalized.get(key))
    ]
    if malformed_keys:
        return _incomplete_from_parse_error(
            "结构化需求分析结果的缺口字段结构非法（"
            + "、".join(malformed_keys)
            + "），已降级为 Incomplete，请按 {item, kind, detail} 结构重新提交"
        )

    for list_key in ("missing_items", "ambiguous_items", "flagged_items", "checks",
                     "not_applicable_items"):
        value = normalized.get(list_key)
        if value is None:
            normalized[list_key] = []
        elif not isinstance(value, list):
            # 容错：单条对象包装为列表（内部元素仍由 from_dict 逐条转换）
            normalized[list_key] = [value]

    for list_key in ("blocked_reasons", "affected_features"):
        value = normalized.get(list_key)
        if value is None:
            normalized[list_key] = []
        elif isinstance(value, dict):
            normalized[list_key] = [f"{k}: {v}" for k, v in value.items()]
        elif not isinstance(value, list):
            normalized[list_key] = [str(value)]

    return PRDAnalysisResult.from_dict(normalized)


def _incomplete_from_parse_error(reason: str) -> PRDAnalysisResult:
    """解析失败降级：默认 Incomplete，禁止生成正式用例。"""
    return PRDAnalysisResult(
        status=PRDQualityStatus.INCOMPLETE,
        can_generate_testcases=False,
        missing_items=[],
        ambiguous_items=[
            RequirementGap(
                item="结构化需求分析",
                kind=GapCheckKind.AMBIGUOUS,
                detail="结构化需求分析结果不可用，已降级为 Incomplete，请补充或重新提交需求分析。",
            )
        ],
        blocked_reasons=[],
        parse_error=reason,
    )


def _coerce_status(value: Any) -> PRDQualityStatus:
    if isinstance(value, PRDQualityStatus):
        return value
    text = str(value or "").strip()
    for status in PRDQualityStatus:
        if text.lower() == status.value.lower():
            return status
    # 中文容错
    mapping = {
        "完整": PRDQualityStatus.COMPLETE,
        "不完整": PRDQualityStatus.INCOMPLETE,
        "阻塞": PRDQualityStatus.BLOCKED,
        "阻断": PRDQualityStatus.BLOCKED,
    }
    return mapping.get(text, PRDQualityStatus.INCOMPLETE)


# ---------------------------------------------------------------------------
# 文本分析
# ---------------------------------------------------------------------------

_WS = re.compile(r"[ \t\u3000]+")


def _normalize(text: str) -> str:
    return _WS.sub(" ", (text or "").replace("\r\n", "\n").replace("\r", "\n"))


def _find_alias(text: str, aliases: tuple[str, ...]) -> tuple[str, int]:
    """返回命中的别名与位置；未命中返回 ("", -1)。"""
    best = ("", -1)
    lowered = text.lower()
    for alias in aliases:
        idx = text.find(alias)
        if idx < 0:
            idx = lowered.find(alias.lower())
        if idx >= 0 and (best[1] < 0 or idx < best[1]):
            best = (alias, idx)
    return best


def _section_excerpt(text: str, alias: str, limit: int = 120) -> str:
    """截取别名附近片段作为证据。"""
    if not alias:
        return ""
    idx = text.find(alias)
    if idx < 0:
        return ""
    return text[idx: idx + limit].strip()


# 纯占位值：整段内容等于这些值时才视为"没有有效内容"
PLACEHOLDER_VALUES: tuple[str, ...] = (
    "待填写", "待补充", "待提供", "待完善", "待明确", "待录入",
    "暂无", "无", "略", "空", "略过", "不适用",
    "n/a", "na", "tbd", "todo", "none", "null", "-", "--", "—", "－", "/", "／",
)

# 占位前缀：仅当后续无实质内容时才判定为占位
# 例：「暂无」→占位；「暂无异常场景，失败直接提示金额超限」→真实业务描述
_PLACEHOLDER_PREFIXES: tuple[str, ...] = (
    "待填写", "待补充", "待提供", "待完善", "待明确", "待录入",
    "暂无", "无", "略", "不适用", "tbd", "todo", "n/a", "na",
)

# 与占位符之后的分隔/语气词（可安全跳过）
_PLACEHOLDER_FILLER_RE = re.compile(r"^[，,。.；;、：:\s]*")

# 低信息量但有业务语义的词：不阻断，只提示确认
AMBIGUOUS_MARKERS: tuple[str, ...] = (
    "待确认", "待定", "暂未确定", "后续确认", "以评审为准",
)


def _has_substantive_remainder(text: str, prefix: str) -> bool:
    """判读占位前缀后是否仍有实质内容。"""
    remainder = text[len(prefix):]
    remainder = _PLACEHOLDER_FILLER_RE.sub("", remainder)
    if len(remainder) < 2:
        return False
    # 仅由标点/分隔符组成的不算实质内容
    return bool(re.search(r"[\w\u4e00-\u9fff]{2,}", remainder))


def _is_placeholder(value: str) -> bool:
    """判断内容是否为占位符/无效值。

    关键约束：**不能**把「暂无异常场景」「无权限用户提示权限不足」这类
    真实业务描述误判为占位内容（复核点 2）。
    """
    raw = str(value or "").strip()
    if not raw:
        return True

    # 「业务目标：」「：」这类只有标签/标点、冒号后无内容
    if raw.strip("：: 　\t") == "":
        return True
    if raw.endswith(("：", ":")) and len(raw.strip("：: 　\t")) <= 20:
        return True

    text = _normalize_line(raw)
    if not text:
        return True

    # 去掉首尾标点后比对纯占位词（"无。" / "暂无；" / "-" 等）
    stripped = text.strip("：: 　\t")
    core = _PLACEHOLDER_FILLER_RE.sub("", stripped).strip("。.，,；;、！!？? 　\t")
    if not core:
        return True

    lowered = core.lower()
    if lowered in PLACEHOLDER_VALUES:
        return True

    # 仅由标点/分隔符组成
    if not re.search(r"[\w\u4e00-\u9fff]", core):
        return True

    # 占位前缀 + 无实质后续内容
    for prefix in _PLACEHOLDER_PREFIXES:
        if not lowered.startswith(prefix):
            continue
        if not _has_substantive_remainder(lowered, prefix):
            return True

    return False

# 结构化小节标题：这些整行文本不能当作上一节的内容
_SECTION_LABEL_TOKENS: tuple[str, ...] = (
    "功能点编号", "功能编号", "需求编号", "功能点名称", "功能名称", "功能点",
    "业务目标", "需求目标", "业务背景", "需求背景", "背景",
    "角色", "权限", "角色权限", "角色与权限", "无权限",
    "前置条件", "前提条件", "操作流程", "业务流程", "主流程", "流程说明",
    "输入", "输入规则", "数据规则", "字段说明", "字段规则", "输入与数据规则",
    "业务规则", "基本业务规则", "规则说明", "校验规则",
    "正常结果", "正常场景", "预期结果", "异常场景", "异常情况",
    "验收标准", "验收条件", "验收口径", "验收准则", "优先级",
    "计算公式", "公式", "精度", "状态流转", "状态变更", "菜单路径",
    "并发", "幂等", "超时", "重试", "失败处理",
)

# 条件检查所需的规则标记：必须作为「标签」显式出现才视为已补齐
# （避免 "输入与数据规则：…" 这类正文子串被当成"输入规则已说明"）
_LABEL_ANCHORED_MARKERS: tuple[str, ...] = (
    "无权限", "无权", "拒绝访问", "越权", "权限不足",
    "超时", "重试", "失败处理", "降级", "补偿",
    "幂等", "重复提交", "并发控制", "加锁", "唯一约束", "去重",
    "公式", "精度", "小数位", "舍入", "进位", "四舍五入",
    "状态流转", "状态变更", "状态机", "流转",
)

# 未被锚定的标记（如"输入规则"这类通用词）允许作为正文子串出现
_LOOSE_MARKERS: tuple[str, ...] = ("输入规则", "输入与数据规则", "字段规则", "数据规则")


def _conditional_marker_present(text: str, markers: tuple[str, ...]) -> str:
    """判断条件检查所需的规则是否已显式说明，返回命中的标记（未命中返回空串）。"""
    anchored = tuple(m for m in markers if m in _LABEL_ANCHORED_MARKERS)
    if anchored:
        explicit, _ = _find_alias(text, anchored)
        if explicit:
            return explicit

    loose = tuple(m for m in markers if m not in _LABEL_ANCHORED_MARKERS)
    if loose:
        explicit, _ = _find_alias(text, loose)
        if explicit:
            return explicit
    return ""


# 行首装饰与序号（含中文数字：实测真实文档大量使用「一、业务目标」「二、操作流程」）
_LINE_PREFIX_RE = re.compile(
    r"^\s*(?:[-*•#>]+\s*|\d+[.、)）]\s*|[一二三四五六七八九十]+[、.．)）]\s*)*"
)


def _normalize_line(line: str) -> str:
    """去掉行首装饰与序号，得到有效文本。"""
    return _LINE_PREFIX_RE.sub("", str(line or "")).strip()


def _is_section_label(value: str) -> bool:
    """判断整行是否只是下一个章节标签（不能当上一节内容）。

    支持「业务目标」「业务目标：」「3. 业务规则：」等写法。
    """
    text = _normalize_line(value).strip().strip("：:").strip()
    if not text:
        return True
    if text in _SECTION_LABEL_TOKENS:
        return True
    # "正常结果 说明" 这类以标签开头的行也视为新章节
    return any(text.startswith(token) for token in _SECTION_LABEL_TOKENS if len(token) >= 4)


def _meaningful_values(value: str) -> list[str]:
    """从候选内容中筛出有效值（剔除占位符与章节标签）。"""
    if not value:
        return []
    return [
        item for item in re.split(r"[\n；;]", value)
        if not _is_placeholder(item) and not _is_section_label(item)
    ]


def _match_label_line(lines: list[str], aliases: tuple[str, ...]) -> tuple[int, str, bool]:
    """在行首查找「别名 + 冒号」标签行。

    Returns:
        (行号, 冒号后内容, 是否命中标签)：未命中返回 (-1, "", False)。
        命中但内容为空时返回 (idx, "", True)，用于区分「标签存在无内容」与「找不到章节」。
    """
    ordered = sorted(set(aliases), key=len, reverse=True)
    for alias in ordered:
        for idx, raw in enumerate(lines):
            body = _normalize_line(raw.strip())
            if not body.startswith(alias):
                continue
            rest = body[len(alias):]
            if rest[:1] not in ("：", ":"):
                continue
            return idx, rest[1:].strip(), True
    return -1, "", False


def _extract_section_content(text: str, aliases: tuple[str, ...]) -> str:
    """提取别名对应的章节内容（同行值或下方列表），**不做有效性过滤**。

    返回空串表示该章节没有内容（例如纯模板 "业务目标："）。
    匹配顺序：
    1. 行首「别名：内容」标签行（长别名优先）；
    2. 行首「别名 内容」/「整行只有别名」；
    3. 兜底：正文中出现别名后紧跟冒号的位置。
    """
    lines = text.split("\n")

    # 1. 标签行（长别名优先）
    idx, value, matched = _match_label_line(lines, aliases)
    if matched:
        if value:
            return value
        return _collect_following_lines(lines, idx)

    # 2. 宽松的行首匹配
    ordered = sorted(set(aliases), key=len, reverse=True)
    normalized_lines = [_normalize_line(line.strip()) for line in lines]
    for alias in ordered:
        for idx, body in enumerate(normalized_lines):
            if not body.startswith(alias):
                continue
            rest = body[len(alias):]
            if rest == "":
                collected = _collect_following_lines(lines, idx)
                if collected:
                    return collected
                continue
            return rest.strip()

    # 3. 兜底：正文中 "别名：内容"
    for alias in ordered:
        pattern = re.compile(
            re.escape(alias) + r"\s*[：:]\s*([^\n]*)"
        )
        match = pattern.search(text)
        if match:
            return match.group(1).strip()

    return ""


def _collect_following_lines(lines: list[str], start: int) -> str:
    """收集章节标签下方的内容（到下一个章节标签为止）。"""
    collected: list[str] = []
    for line in lines[start + 1:]:
        stripped = line.strip()
        if not stripped:
            if collected:
                break
            continue
        if _is_section_label(stripped):
            break
        collected.append(_normalize_line(stripped))
    return "\n".join(collected)
def _has_feature_point_marker(text: str) -> bool:
    """文本中是否存在显式功能点标号（FP-001 / 功能点1 / 功能点清单）。"""
    if re.search(r"(?:FP|FEAT)[-_ ]?\d+", text, re.IGNORECASE):
        return True
    if re.search(r"功能点\s*[-_ ]?[0-9０-９一二三四五六七八九十]+", text):
        return True
    alias, _ = _find_alias(text, ("功能点清单", "功能清单", "功能点列表", "功能矩阵"))
    return bool(alias)


# 这些检查项由「显式标号 / 章节标题」满足即可，不要求特定措辞：
# 真实文档常用「FP-001 企业储值充值」而不是「功能点编号：…」。
_EXPLICIT_MARKER_ITEMS: dict[str, str] = {
    "功能点编号": "命中：显式功能点标号（FP-xxx / 功能点 N）",
    "功能点名称": "命中：功能点标题（功能点标号 + 名称）",
}


def _has_section_content(
    text: str,
    aliases: tuple[str, ...],
    item: str = "",
) -> tuple[bool, str]:
    """判断章节是否存在**有效内容**。

    Returns:
        (has_content, evidence)：has_content=False 表示章节缺失、只有标题或仅占位符。
    """
    if item in _EXPLICIT_MARKER_ITEMS and _has_feature_point_marker(text):
        matched, pos = _find_alias(text, aliases)
        evidence = _extract_section_content(text, aliases) or _EXPLICIT_MARKER_ITEMS[item]
        return True, evidence[:120]

    content = _extract_section_content(text, aliases)
    values = _meaningful_values(content)
    if not values:
        return False, ""
    return True, "；".join(values)[:120]


def analyze_requirement_text(
    requirement_text: str,
    source: str = "",
    requirement_id: str = "",
    *,
    field_rule_count: int = 0,
) -> PRDAnalysisResult:
    """对需求原文做最小必填 + 基础结果 + 条件检查，并给出状态判定。

    这是确定性判定器，不依赖 LLM；结构化 LLM 结果的落库走
    :func:`parse_analysis_payload`，两者共用 :class:`PRDAnalysisResult`。

    Args:
        requirement_text: 需求原文（Markdown/纯文本均可）
        source: 来源位置（文件名/章节），写入缺口 evidence
        requirement_id: 需求编号（用于报告回指）
        field_rule_count: 解析器从文档「字段说明表」抽出的**结构化字段规则数**。
            >0 时视为已提供「输入与数据规则」证据（不再因正文没有该章节判缺失）；
            ≥ :data:`FIELD_SPEC_MIN_FIELD_RULES` 时按**字段规格类文档**判定，
            PRD 专属章节转为「不适用」并允许生成（见 B1 说明）。
    """
    text = _normalize(requirement_text)
    checks: list[RequirementGap] = []
    missing: list[RequirementGap] = []
    ambiguous: list[RequirementGap] = []
    blocked_reasons: list[str] = []
    not_applicable: list[RequirementGap] = []
    is_field_spec = int(field_rule_count or 0) >= FIELD_SPEC_MIN_FIELD_RULES

    def _evaluate(spec: RequirementSectionSpec) -> None:
        """按「标题存在 + 内容有效」双重判定一个章节。"""
        alias, _ = _find_alias(text, spec.aliases)
        has_content, evidence = _has_section_content(text, spec.aliases)

        if has_content:
            checks.append(
                RequirementGap(
                    item=spec.item,
                    kind=GapCheckKind.FLAGGED,
                    detail=f"命中：{alias or spec.item}",
                    evidence=evidence,
                )
            )
            return

        # 结构化证据：字段说明表本身就是「输入与数据规则」的载体
        if spec.item == "输入与数据规则" and int(field_rule_count or 0) > 0:
            detail = (
                f"命中：结构化字段规则 {int(field_rule_count)} 条"
                "（来自文档字段说明表，非正文章节）"
            )
            checks.append(RequirementGap(item=spec.item, kind=GapCheckKind.FLAGGED,
                                         detail=detail, evidence=detail))
            return

        # 字段规格类文档：PRD 专属章节「不适用」（计入审计，不计入缺失/阻断）
        if is_field_spec and spec.item in FIELD_SPEC_NOT_APPLICABLE_ITEMS:
            detail = (
                f"字段规格类文档不适用：{spec.item}"
                f"（已由 {int(field_rule_count)} 条结构化字段规则及约束覆盖）"
            )
            gap = RequirementGap(item=spec.item, kind=GapCheckKind.FLAGGED,
                                 detail=detail, evidence=detail)
            checks.append(gap)
            not_applicable.append(gap)
            return

        blocking = spec.blocking_missing
        if alias:
            # 标题/字段名出现但内容为空或仅占位符 → 仍视为缺失
            reason = f"{spec.item}内容为空或仅为占位符（如「待填写」「暂无」），需补充有效内容"
        else:
            reason = f"缺少{spec.item}：{spec.spec}"

        gap = RequirementGap(
            item=spec.item,
            kind=GapCheckKind.REQUIRED_MISSING,
            detail=reason,
            blocking=blocking,
            evidence=evidence,
        )
        checks.append(
            RequirementGap(item=spec.item, kind=GapCheckKind.REQUIRED_MISSING,
                           detail=reason, blocking=blocking, evidence=evidence)
        )
        missing.append(gap)
        if blocking:
            blocked_reasons.append(reason)

    # 1. 最小必填检查
    for spec in REQUIRED_SECTIONS:
        _evaluate(spec)

    # 2. 基础结果检查
    for spec in BASE_RESULT_SECTIONS:
        _evaluate(spec)

    # 3. 条件检查（先判断是否"涉及"，再判断是否已补齐有效内容）
    for spec in CONDITIONAL_SECTIONS:
        detect_alias, _ = _find_alias(text, spec.detect_markers)
        if not detect_alias:
            checks.append(
                RequirementGap(item=spec.item, kind=GapCheckKind.FLAGGED,
                               detail="未涉及该类逻辑，跳过条件检查")
            )
            continue
        has_content, evidence = _has_section_content(
            text, spec.requires_markers
        )
        if not has_content:
            # 条件检查的「要求标记」本身就是规则名称（如「无权限」「幂等」），
            # 显式出现即视为该规则已说明，避免因措辞差异误判缺失。
            explicit_alias = _conditional_marker_present(text, spec.requires_markers)
            if explicit_alias:
                has_content, evidence = True, _extract_section_content(
                    text, (explicit_alias,)
                ) or f"命中：{explicit_alias}"

        if has_content:
            checks.append(
                RequirementGap(item=spec.item, kind=GapCheckKind.FLAGGED,
                               detail=f"涉及「{detect_alias}」，已补齐",
                               evidence=evidence)
            )
            continue
        gap = RequirementGap(
            item=spec.item,
            kind=GapCheckKind.CONDITIONAL_MISSING,
            detail=f"涉及「{detect_alias}」，但缺少{spec.item}：{spec.spec}",
            evidence=source,
        )
        checks.append(
            RequirementGap(item=spec.item, kind=GapCheckKind.CONDITIONAL_MISSING,
                           detail=gap.detail, evidence=source)
        )
        missing.append(gap)

    # 4. 歧义项
    alias, _ = _find_alias(text, ("待确认", "待定", "TBD", "TODO", "暂未确定"))
    if alias:
        ambiguous.append(
            RequirementGap(
                item="待确认内容",
                kind=GapCheckKind.AMBIGUOUS,
                detail=f"需求中存在「{alias}」表述，需确认后才能定稿用例预期",
                evidence=_section_excerpt(text, alias),
            )
        )

    if not _has_structured_feature_list(text):
        ambiguous.append(
            RequirementGap(
                item="功能点清单",
                kind=GapCheckKind.AMBIGUOUS,
                detail="未识别到结构化功能点清单，功能点编号将按文档顺序自动生成，建议补全后复核",
            )
        )

    # 5. 优先级缺失 → 标记"待确认"，不自动补 P1
    priority_ok, priority_evidence = _has_section_content(
        text, ("优先级", "优先等级", "P0", "P1")
    )
    if not priority_ok:
        flagged = RequirementGap(
            item="优先级",
            kind=GapCheckKind.FLAGGED,
            detail="未提供优先级，标记为「待确认」，不自动推断 P1",
        )
    else:
        flagged = RequirementGap(
            item="优先级",
            kind=GapCheckKind.FLAGGED,
            detail="命中：优先级",
            evidence=priority_evidence,
        )

    return _build_result(
        checks=checks,
        missing=missing,
        ambiguous=ambiguous,
        flagged=[flagged],
        blocked_reasons=blocked_reasons,
        affected_features=_extract_affected_features(text),
        not_applicable=not_applicable,
        field_rule_count=int(field_rule_count or 0),
    )


def _has_structured_feature_list(text: str) -> bool:
    """是否识别到结构化功能点清单（功能点编号/清单/表格）。"""
    if re.search(r"FP[-_ ]?\d+", text, re.IGNORECASE):
        return True
    if re.search(r"功能点\s*[0-9０-９一二三四五六七八九十]+", text):
        return True
    alias, _ = _find_alias(text, ("功能点清单", "功能清单", "功能点列表", "功能矩阵"))
    return bool(alias)


def _extract_affected_features(text: str) -> list[str]:
    """从文本中抽取功能点编号（受影响功能点的原始线索）。"""
    ids: list[str] = []
    for match in re.finditer(r"FP[-_ ]?\d+", text, re.IGNORECASE):
        token = match.group(0).replace(" ", "").replace("_", "-").upper()
        token = token.replace("FP--", "FP-")
        if token not in ids:
            ids.append(token)
    return ids


# ---------------------------------------------------------------------------
# 状态判定
# ---------------------------------------------------------------------------

def _build_result(
    checks: list[RequirementGap],
    missing: list[RequirementGap],
    ambiguous: list[RequirementGap],
    flagged: list[RequirementGap],
    blocked_reasons: list[str],
    affected_features: list[str],
    not_applicable: list[RequirementGap] | None = None,
    field_rule_count: int = 0,
) -> PRDAnalysisResult:
    """按 V1.2 规则判定状态，并叠加「字段规格类文档」适配（B1）。

    判定顺序（高优先级先命中）：
    1. 目标/流程无法确认（必填阻断缺失）→ Blocked
    2. 存在缺失但仍可理解（含缺验收标准）→ Incomplete
    3. 满足测试设计条件 → Complete

    字段规格适配（`field_rule_count >= FIELD_SPEC_MIN_FIELD_RULES`）：
    - PRD 专属章节已被移入 `not_applicable`，因此不再阻断、也不计入缺失；
    - 状态**仍如实保留 Incomplete**（PRD 章节确实不存在），
      但 `can_generate_testcases=True` —— 允许生成，并以
      `applicable_profile` / `profile_reason` 说明依据，避免把"字段规格完整"谎报成"PRD 完整"。
    """
    profile = FIELD_SPEC_PROFILE if field_rule_count >= FIELD_SPEC_MIN_FIELD_RULES else ""
    if blocked_reasons:
        status = PRDQualityStatus.BLOCKED
    elif profile:
        # 字段规格档案：PRD 章节已转为「不适用」，但**不得**因此谎报 Complete——
        # 文档确实不含 PRD 章节，状态如实保留 Incomplete。
        status = PRDQualityStatus.INCOMPLETE
    elif missing:
        status = PRDQualityStatus.INCOMPLETE
    else:
        status = PRDQualityStatus.COMPLETE

    can_generate = status == PRDQualityStatus.COMPLETE
    profile_reason = ""
    if profile:
        can_generate = True
        profile_reason = (
            f"按字段规格类文档判定：文档提供 {field_rule_count} 条结构化字段规则"
            f"（≥{FIELD_SPEC_MIN_FIELD_RULES}），"
            "PRD 专属章节（业务目标/操作流程等）不适用，故允许生成用例；"
            "质量状态仍如实标记为「缺失 PRD 章节」。"
        )

    # 受影响功能点：阻断/缺失优先，其次歧义线索
    affected: list[str] = []
    for gap in missing:
        if gap.item in ("操作流程", "业务目标", "输入与数据规则"):
            for feature_id in affected_features:
                if feature_id not in affected:
                    affected.append(feature_id)

    return PRDAnalysisResult(
        status=status,
        can_generate_testcases=can_generate,
        missing_items=missing,
        ambiguous_items=ambiguous,
        blocked_reasons=blocked_reasons,
        affected_features=affected,
        flagged_items=flagged,
        checks=checks,
        applicable_profile=profile,
        not_applicable_items=list(not_applicable or []),
        profile_reason=profile_reason,
    )


# ---------------------------------------------------------------------------
# 报告渲染
# ---------------------------------------------------------------------------

def render_analysis_report(result: PRDAnalysisResult) -> dict[str, Any]:
    """构建分析报告（UI/导出共用）。"""
    return {
        "status": result.status.value,
        "status_label": result.status.label,
        "can_generate_testcases": result.can_generate_testcases,
        "blocked_reasons": list(result.blocked_reasons),
        "missing_items": [g.to_dict() for g in result.missing_items],
        "ambiguous_items": [g.to_dict() for g in result.ambiguous_items],
        "flagged_items": [g.to_dict() for g in result.flagged_items],
        "affected_features": list(result.affected_features),
        "parse_error": result.parse_error,
        # 文档类型适配：为空表示按 PRD 口径判定
        "applicable_profile": result.applicable_profile,
        "profile_reason": result.profile_reason,
        "not_applicable_items": [g.to_dict() for g in result.not_applicable_items],
    }


def render_analysis_markdown(
    result: PRDAnalysisResult,
    requirement_id: str = "",
    source: str = "",
) -> str:
    """渲染需求分析报告为 Markdown（Incomplete/Blocked 时作为交付物）。"""
    lines: list[str] = []
    lines.append("# 需求质量分析报告")
    if requirement_id:
        lines.append(f"**需求编号**: {requirement_id}")
    if source:
        lines.append(f"**来源**: {source}")
    lines.append(f"**质量状态**: {result.status.label}（{result.status.value}）")
    lines.append(
        f"**测试用例生成**: {'允许' if result.can_generate_testcases else '禁止'}"
    )
    lines.append("")

    if result.applicable_profile:
        lines.append("## 文档类型适配")
        lines.append("")
        lines.append(f"- 适用档案：**{result.applicable_profile}**（字段规格类文档）")
        if result.profile_reason:
            lines.append(f"- 判定依据：{result.profile_reason}")
        if result.not_applicable_items:
            lines.append("- PRD 专属章节按「不适用」处理（不计入缺失、不阻断）：")
            for gap in result.not_applicable_items:
                lines.append(f"  - {gap.item}")
        lines.append("")

    if result.parse_error:
        lines.append("## 解析降级说明")
        lines.append("")
        lines.append(f"- {result.parse_error}")
        lines.append("")

    lines.append("## 阻断原因")
    lines.append("")
    if result.blocked_reasons:
        for reason in result.blocked_reasons:
            lines.append(f"- ❌ {reason}")
    else:
        lines.append("- 无")
    lines.append("")

    lines.append("## 缺失项")
    lines.append("")
    if result.missing_items:
        for gap in result.missing_items:
            flag = "🚫" if gap.blocking else "⚠️"
            lines.append(f"- {flag} **{gap.item}**：{gap.detail}")
    else:
        lines.append("- 无")
    lines.append("")

    lines.append("## 歧义项 / 待确认")
    lines.append("")
    if result.ambiguous_items:
        for gap in result.ambiguous_items:
            lines.append(f"- ❓ **{gap.item}**：{gap.detail}")
    else:
        lines.append("- 无")
    lines.append("")

    lines.append("## 标记项（不阻断）")
    lines.append("")
    if result.flagged_items:
        for gap in result.flagged_items:
            lines.append(f"- 🏷️ **{gap.item}**：{gap.detail}")
    else:
        lines.append("- 无")
    lines.append("")

    lines.append("## 受影响功能点")
    lines.append("")
    if result.affected_features:
        for feature_id in result.affected_features:
            lines.append(f"- {feature_id}")
    else:
        lines.append("- 无（或未识别到功能点编号）")
    lines.append("")

    lines.append("## 检查明细")
    lines.append("")
    lines.append("| 检查项 | 结果 | 说明 |")
    lines.append("| --- | --- | --- |")
    for gap in result.checks:
        if gap.kind == GapCheckKind.FLAGGED and gap.detail.startswith("命中"):
            mark = "✅"
        elif gap.blocking:
            mark = "🚫"
        elif gap.kind in (GapCheckKind.REQUIRED_MISSING, GapCheckKind.CONDITIONAL_MISSING):
            mark = "⚠️"
        else:
            mark = "➖"
        lines.append(f"| {gap.item} | {mark} | {gap.detail} |")
    lines.append("")

    if not result.can_generate_testcases:
        lines.append("---")
        lines.append("")
        lines.append(
            "> 判定结论：当前需求不满足测试设计条件，**不生成正式测试用例**。"
            "请补齐上述缺失项后重新提交需求分析。"
        )
        lines.append("")

    return "\n".join(lines)
