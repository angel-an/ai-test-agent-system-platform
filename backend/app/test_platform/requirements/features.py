# -*- coding: utf-8 -*-
"""阶段二：最小功能点模型 — 编号生成、功能点抽取、需求/测试点/用例覆盖链路。

范围控制：只做「需求 → 功能点 → 测试点 → 测试用例 → 执行结果」的稳定关联，
不做通用规则引擎，也不强制迁移历史数据。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from app.test_platform.requirements.analysis import PRDAnalysisResult


FEATURE_ID_PREFIX = "FP"


class FeaturePriority(str, Enum):
    """功能点优先级"""

    P0 = "P0"
    P1 = "P1"
    P2 = "P2"
    P3 = "P3"
    PENDING = "待确认"  # 缺失优先级时的标记，禁止自动补 P1


def normalize_feature_id(raw: str) -> str:
    """把各种功能点编号写法归一化为 ``FP-001``。"""
    text = str(raw or "").strip()
    if not text:
        return ""
    match = re.search(r"(?:FP|FEAT|功能点|功能)[-_\s]*([0-9]+)", text, re.IGNORECASE)
    if match:
        return f"{FEATURE_ID_PREFIX}-{int(match.group(1)):03d}"
    if text.isdigit():
        return f"{FEATURE_ID_PREFIX}-{int(text):03d}"
    return text.upper()


def generate_feature_id(sequence: int) -> str:
    """按序号生成唯一功能点编号（FP-001 起）。"""
    try:
        seq = int(sequence)
    except (TypeError, ValueError):
        raise ValueError(f"功能点序号必须是整数，收到: {sequence!r}")
    if seq < 1:
        raise ValueError(f"功能点序号必须 >= 1，收到: {seq}")
    return f"{FEATURE_ID_PREFIX}-{seq:03d}"


@dataclass
class FeaturePoint:
    """最小功能点模型（阶段二）。

    每个功能点必须有唯一编号 ``feature_id``，测试用例通过 ``feature_id`` 回指。
    """

    feature_id: str
    feature_name: str
    business_goal: str = ""
    roles_permissions: list[str] = field(default_factory=list)
    preconditions: list[str] = field(default_factory=list)
    operation_flow: list[str] = field(default_factory=list)
    input_rules: list[str] = field(default_factory=list)
    business_rules: list[str] = field(default_factory=list)
    normal_results: list[str] = field(default_factory=list)
    exception_cases: list[str] = field(default_factory=list)
    acceptance_criteria: list[str] = field(default_factory=list)
    priority: FeaturePriority = FeaturePriority.PENDING
    requirement_id: str = ""
    source_location: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "feature_id": self.feature_id,
            "feature_name": self.feature_name,
            "business_goal": self.business_goal,
            "roles_permissions": list(self.roles_permissions),
            "preconditions": list(self.preconditions),
            "operation_flow": list(self.operation_flow),
            "input_rules": list(self.input_rules),
            "business_rules": list(self.business_rules),
            "normal_results": list(self.normal_results),
            "exception_cases": list(self.exception_cases),
            "acceptance_criteria": list(self.acceptance_criteria),
            "priority": self.priority.value,
            "requirement_id": self.requirement_id,
            "source_location": self.source_location,
        }

    @staticmethod
    def from_dict(d: dict[str, Any]) -> "FeaturePoint":
        return FeaturePoint(
            feature_id=normalize_feature_id(d.get("feature_id") or d.get("id") or ""),
            feature_name=str(d.get("feature_name") or d.get("name") or ""),
            business_goal=str(d.get("business_goal") or ""),
            roles_permissions=_as_str_list(d.get("roles_permissions")),
            preconditions=_as_str_list(d.get("preconditions")),
            operation_flow=_as_str_list(d.get("operation_flow")),
            input_rules=_as_str_list(d.get("input_rules")),
            business_rules=_as_str_list(d.get("business_rules")),
            normal_results=_as_str_list(d.get("normal_results")),
            exception_cases=_as_str_list(d.get("exception_cases")),
            acceptance_criteria=_as_str_list(d.get("acceptance_criteria")),
            priority=_coerce_priority(d.get("priority")),
            requirement_id=str(d.get("requirement_id") or ""),
            source_location=str(d.get("source_location") or ""),
        )


def _as_str_list(value: Any) -> list[str]:
    """把字符串/列表统一为字符串列表。"""
    if value is None:
        return []
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return []
        return [text]
    if isinstance(value, (list, tuple, set)):
        return [str(v).strip() for v in value if str(v).strip()]
    return [str(value)]


def _coerce_priority(value: Any) -> FeaturePriority:
    if isinstance(value, FeaturePriority):
        return value
    text = str(value or "").strip().upper()
    for priority in FeaturePriority:
        if text == priority.value.upper():
            return priority
    # 也接受 critical/high/medium/low
    alias = {
        "CRITICAL": FeaturePriority.P0,
        "HIGH": FeaturePriority.P1,
        "MEDIUM": FeaturePriority.P2,
        "LOW": FeaturePriority.P3,
    }
    return alias.get(text, FeaturePriority.PENDING)


# ---------------------------------------------------------------------------
# 功能点抽取
# ---------------------------------------------------------------------------

_HEADING_RE = re.compile(r"^[ \t]*(#{1,6})[ \t]*(.+?)[ \t]*$")
# 只把「功能点」「功能点N」「模块N」「Feature」这类显式标题当作功能点标题，
# 避免「功能点清单」「功能说明」等结构性小节被误判为一个功能点。
_FEATURE_HEADING_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"^(?:功能点|模块|特性)\s*[0-9０-９一二三四五六七八九十]+\b.*$"),
    re.compile(r"^(?:功能点|模块|特性)\s*[：:]\s*\S.*$"),
    re.compile(r"^功能\s*[0-9０-９一二三四五六七八九十]+\b.*$"),
    re.compile(r"^Feature\s*[0-9]*\s*[：:、.\-–—]?\s*\S.*$", re.IGNORECASE),
)
_NUMBERED_HEADING_RE = re.compile(r"^(\d+(?:\.\d+)*)(?:[.、)）]\s*|\s+)(.*)$")
# 这些词出现在标题里说明是结构性小节而非功能点
_EXCLUDED_HEADING_WORDS: tuple[str, ...] = (
    "清单", "列表", "编号", "说明", "矩阵", "汇总", "概述", "背景", "范围",
    "目录", "流程", "规则", "标准", "条件", "目标", "权限", "角色", "统计",
    "步骤", "字段", "数据", "公式", "场景", "结果", "验收",
)

# 章节别名 → 功能点字段
_SECTION_FIELD_ALIASES: tuple[tuple[tuple[str, ...], str], ...] = (
    (("业务目标", "需求目标", "业务背景", "需求背景", "背景"), "business_goal"),
    (("角色权限", "角色与权限", "角色", "权限"), "roles_permissions"),
    (("前置条件", "前提条件", "前置"), "preconditions"),
    (("操作流程", "业务流程", "主流程", "流程说明", "功能流程", "操作步骤"), "operation_flow"),
    (("输入与数据规则", "输入规则", "数据规则", "字段说明", "字段规则", "输入"), "input_rules"),
    (("业务规则", "基本业务规则", "规则说明", "校验规则"), "business_rules"),
    (("正常结果", "正常场景", "预期结果", "正常流程结果"), "normal_results"),
    (("异常场景", "异常用例", "异常情况", "失败场景"), "exception_cases"),
    (("验收标准", "验收条件", "验收口径", "验收准则"), "acceptance_criteria"),
)

_SCALAR_FIELDS = {"business_goal"}
_LIST_FIELDS = {
    "roles_permissions", "preconditions", "operation_flow", "input_rules",
    "business_rules", "normal_results", "exception_cases", "acceptance_criteria",
}

_NOTES_TITLE_RE = re.compile(
    r"^(?:[0-9]+(?:\.[0-9]+)*[.、)）]?\s*)?(注|说明|备注|附录|参考资料|修订记录|术语)"
    r"[：:0-9]*\s*$"
)


def _looks_like_notes_section(section: "_Section") -> bool:
    """「注/说明/备注/附录」等章节只可能引用功能点编号，不应被当作功能点。"""
    if not _NOTES_TITLE_RE.match(section.title.strip()):
        return False
    body = section.body
    return bool(body) and all(
        not line.strip() or line.strip().startswith("FP-")
        for line in body.split("\n")
    )


def _is_metadata_section(title: str) -> bool:
    """功能点清单/列表这类容器章节不是功能点本身。"""
    return any(word in title for word in ("清单", "列表", "汇总", "目录"))


def _drop_container_sections(sections: list["_Section"]) -> list["_Section"]:
    """剔除仅作为容器/清单的章节。

    规则：
    - 标题形如「功能点清单/列表/汇总/目录」→ 容器，剔除；
    - 章节本身**不是**显式功能点标题、也不是编号小节（如 1.1），
      但文档内存在更深的标题层级（或同级更具体的功能点章节）→ 视为父级容器，剔除；
    - 其余保留。整份文档都被判为容器时退回原列表。
    """
    if not sections:
        return sections

    has_deeper_heading = any(s.level > 1 for s in sections)
    has_explicit_feature = any(_match_feature_id(s.title) for s in sections)

    kept: list[_Section] = []
    for section in sections:
        title = section.title.strip()
        if not title or _is_metadata_section(title) or _looks_like_notes_section(section):
            continue

        if has_deeper_heading and not _match_feature_id(title) and not _is_numbered_subsection(title):
            # 文档大标题 / 父级模块名：其正文实际上是后续章节的内容
            continue

        kept.append(section)

    if kept:
        return kept
    return sections


def _is_numbered_subsection(title: str) -> bool:
    match = _NUMBERED_HEADING_RE.match(title.strip())
    return bool(match) and "." in match.group(1)


@dataclass
class _Section:
    """文档章节（标题 + 正文）"""

    title: str
    body: str
    source: str = ""
    level: int = 1


def extract_feature_points(
    requirement_text: str,
    requirement_id: str = "",
    source_location: str = "",
) -> list[FeaturePoint]:
    """从需求原文抽取功能点，并分配唯一编号。

    抽取策略（确定性、保守）：
    1. 显式功能点编号（FP-001 / 功能点 1）优先，直接采用该编号；
    2. 否则按 Markdown 标题 / "功能点：" / 数字编号"1.2 标题"识别功能点名称；
    3. 识别不到任何标题时，退化为「文档级单一功能点」，名称取文档首行非空文本。

    抽出的编号保证在本批内唯一。
    """
    text = (requirement_text or "").replace("\r\n", "\n").replace("\r", "\n")
    sections = _split_sections(text)

    # 纯标题、无正文的章节（例如文档大标题）不算功能点；
    # 但如果整份文档只有这种章节，则保留为文档级功能点。
    named_sections = [s for s in sections if s.body.strip()]
    if named_sections:
        sections = named_sections

    # 若文档里存在更具体的功能点章节，则剔除「功能点清单」这类容器章节与文档大标题
    sections = _drop_container_sections(sections)

    features: list[FeaturePoint] = []
    used_ids: set[str] = set()

    for order, section in enumerate(sections, start=1):
        raw_id = _match_feature_id(section.title)
        feature_id = normalize_feature_id(raw_id) if raw_id else generate_feature_id(order)

        # 编号冲突兜底：确保唯一
        if feature_id in used_ids:
            candidate_seq = len(used_ids) + 1
            candidate = generate_feature_id(candidate_seq)
            while candidate in used_ids:
                candidate_seq += 1
                candidate = generate_feature_id(candidate_seq)
            feature_id = candidate
        used_ids.add(feature_id)

        feature = FeaturePoint(
            feature_id=feature_id,
            feature_name=_clean_feature_name(section.title, order),
            requirement_id=requirement_id,
            source_location=section.source or source_location,
        )
        _fill_feature_fields(feature, section.body)
        _fill_priority(feature, text)
        features.append(feature)

    if not features:
        feature = FeaturePoint(
            feature_id=generate_feature_id(1),
            feature_name=_first_meaningful_line(text) or "整体需求",
            requirement_id=requirement_id,
            source_location=source_location,
        )
        _fill_feature_fields(feature, text)
        _fill_priority(feature, text)
        features.append(feature)

    return features


def _split_sections(text: str) -> list[_Section]:
    """把文档拆成功能点级别的章节。"""
    lines = text.split("\n")
    sections: list[_Section] = []
    current_title = ""
    current_body: list[str] = []
    current_line = 0
    current_level = 1

    def flush() -> None:
        if current_title or current_body:
            body = "\n".join(current_body).strip()
            if current_title or body:
                sections.append(
                    _Section(
                        title=current_title,
                        body=body,
                        source=f"L{current_line}" if current_line else "",
                        level=current_level,
                    )
                )

    for idx, line in enumerate(lines, start=1):
        stripped = line.strip()
        if not stripped:
            if current_body and current_body[-1] != "":
                current_body.append("")
            continue

        title, level = _heading_candidate(stripped)
        if title:
            flush()
            current_title = title
            current_level = level or 1
            current_body = []
            current_line = idx
            continue

        current_body.append(stripped)
        if not current_title:
            current_line = idx

    flush()
    return [s for s in sections if s.title or s.body]


def _heading_candidate(line: str) -> tuple[str, int]:
    """判断该行是否是功能点标题，返回 (标题文本, 层级)；非标题返回 ("", 0)。"""
    markdown = _HEADING_RE.match(line)
    if markdown:
        title = markdown.group(2).strip()
        if title and not _has_excluded_word(title):
            return title, len(markdown.group(1))

    if len(line) > 60 or _looks_like_sentence(line) or _is_top_level_list_item(line):
        return "", 0

    if any(pattern.match(line) for pattern in _FEATURE_HEADING_PATTERNS):
        return line.strip(), 1

    numbered = _NUMBERED_HEADING_RE.match(line)
    if numbered:
        # 多级编号（1.1 / 2.3.1）通常是标题；
        # 单级编号（1. / 2.）在需求文档中绝大多数是流程/规则列表项，不作为新功能点标题，
        # 否则会把"操作流程"下的每一步都拆成一个功能点。
        if "." in numbered.group(1):
            return line.strip(), 2
        return "", 0

    return "", 0


def _is_top_level_list_item(line: str) -> bool:
    """「1. xxx」这类单级编号更可能是流程/规则列表项，而不是功能点标题。"""
    match = _NUMBERED_HEADING_RE.match(line)
    return bool(match) and "." not in match.group(1)


def _has_excluded_word(text: str) -> bool:
    return any(word in text for word in _EXCLUDED_HEADING_WORDS)


def _looks_like_sentence(line: str) -> bool:
    """排除「1. 用户点击按钮后页面刷新。」这类正文列表项。"""
    return line.rstrip().endswith(("。", "；", ";", "！", "!", "，", ","))


def _match_feature_id(title: str) -> str:
    match = re.search(r"(?:FP|FEAT)[-_ ]?(\d+)", title, re.IGNORECASE)
    if match:
        return f"FP-{match.group(1)}"
    match = re.search(r"功能点\s*[-_ ]?([0-9０-９]+)", title)
    if match:
        return f"FP-{match.group(1)}"
    return ""


def _clean_feature_name(title: str, order: int) -> str:
    text = re.sub(r"^#{1,6}\s*", "", title or "").strip()
    numbered = _NUMBERED_HEADING_RE.match(text)
    if numbered:
        text = numbered.group(2).strip() or numbered.group(1)
    text = re.sub(
        r"^(?:功能点|功能|模块|特性|Feature)\s*[0-9０-９一二三四五六七八九十]*\s*[：:、.\-–—]?\s*",
        "",
        text,
        flags=re.IGNORECASE,
    )
    text = re.sub(r"^(?:FP|FEAT)[-_ ]?\d+\s*[：:、.\-–—]?\s*", "", text, flags=re.IGNORECASE)
    text = text.strip(" ：:、.-–—")
    if text and not _has_excluded_word(text):
        return text
    if "清单" in title or "列表" in title:
        return f"功能点清单（{title.strip()}）"
    return text or f"功能点{order}"


def _first_meaningful_line(text: str) -> str:
    for line in text.split("\n"):
        stripped = line.strip().lstrip("#").strip()
        if stripped:
            return stripped[:60]
    return ""


def _fill_feature_fields(feature: FeaturePoint, body: str) -> None:
    """从章节正文中提取字段（按「别名：内容」或别名小节）。"""
    if not body:
        return

    for aliases, field_name in _SECTION_FIELD_ALIASES:
        values = _collect_values(body, aliases)
        if not values:
            continue
        if field_name in _SCALAR_FIELDS:
            if not getattr(feature, field_name):
                setattr(feature, field_name, values[0])
        elif field_name in _LIST_FIELDS:
            existing = getattr(feature, field_name)
            for value in values:
                if value not in existing:
                    existing.append(value)


def _collect_values(body: str, aliases: tuple[str, ...]) -> list[str]:
    """收集章节有效内容（复用 analysis 的统一取值逻辑，自动剔除占位符）。

    兼容两种写法：
    -「别名：内容」同行书写；
    -「别名：」下方挂列表项。
    """
    from app.test_platform.requirements.analysis import (
        _extract_section_content,
        _meaningful_values,
    )

    content = _extract_section_content(body, aliases)
    values = _meaningful_values(content)
    return [value for value in values if value]


def _fill_priority(feature: FeaturePoint, doc_text: str) -> None:
    """填充优先级；缺失时保持「待确认」，不自动补 P1。"""
    match = re.search(
        r"优先级\s*[：:]\s*(P[0-3]|高|中|低|critical|high|medium|low)",
        doc_text,
        re.IGNORECASE,
    )
    if match:
        raw = match.group(1).strip()
        alias = {"高": "P0", "中": "P2", "低": "P3"}
        feature.priority = _coerce_priority(alias.get(raw, raw))
    else:
        feature.priority = FeaturePriority.PENDING


# ---------------------------------------------------------------------------
# 与需求分析结果的联动
# ---------------------------------------------------------------------------

def apply_affected_features(
    feature_points: list[FeaturePoint],
    analysis: PRDAnalysisResult,
) -> list[FeaturePoint]:
    """把需求分析里的受影响功能点编号，在功能点清单中就地标记。

    只在编号能对应上时标记（不新建功能点），保证「受影响功能点」可追溯。
    """
    affected = {str(f).upper() for f in (analysis.affected_features or [])}
    if not affected:
        return feature_points

    for feature in feature_points:
        if feature.feature_id.upper() in affected:
            marker = f"[受影响] {analysis.status.label}"
            if marker not in feature.exception_cases:
                feature.exception_cases.append(marker)
    return feature_points


def build_feature_point_index(
    feature_points: list[FeaturePoint],
) -> dict[str, FeaturePoint]:
    """构建 feature_id → FeaturePoint 索引。

    编号重复时后者覆盖；落库/生成前请先用 :func:`assert_unique_feature_ids` 校验。
    """
    return {fp.feature_id: fp for fp in feature_points if fp.feature_id}


def assert_unique_feature_ids(feature_points: list[FeaturePoint]) -> list[str]:
    """校验功能点编号唯一性，返回重复编号清单（空列表 = 全部唯一）。"""
    seen: set[str] = set()
    duplicates: list[str] = []
    for fp in feature_points:
        if not fp.feature_id:
            duplicates.append("(空编号)")
            continue
        if fp.feature_id in seen:
            duplicates.append(fp.feature_id)
        seen.add(fp.feature_id)
    return sorted(set(duplicates))
