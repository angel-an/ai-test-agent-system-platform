# -*- coding: utf-8 -*-
"""需求文档结构化（阶段一：降噪 + 表格结构化）。

只做**确定性**解析，不调用任何模型。产出四样东西：

- ``full_text``：段落 + 表格拍平文本（保持既有质量门禁的输入语义不变）；
- ``narrative_text``：仅叙述性文本，且已剔除纯标题行与表格文本；
- ``field_rules``：来自"字段说明"类表格的**字段规则**（含跨页续行合并）；
- ``template_tables``：导入模板表头等非字段表（单独保留，不混进字段规则）。

设计约束（刻意的克制）：
- 不新增数据库表、不改门禁与执行链路；
- 解析失败时**降级**为纯文本（绝不因为结构化失败而让上传失败）；
- 所有规则保留 ``source``（表号/行号），供追溯。
"""

from __future__ import annotations

import io
import re
from dataclasses import dataclass, field as dc_field
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# 标题 / 噪音判定
# ---------------------------------------------------------------------------

#: 形如 "1.1 背景说明" / "2.1.2 字段说明" / "3. 待确认事项" / "一、概述"
_NUMBERED_HEADING_RE = re.compile(
    r"^\s*(?:第?\s*[0-9０-９]+\s*[章节]|[0-9０-９]+(?:\s*[.．]\s*[0-9０-９]+)*\s*[.．、]?|[一二三四五六七八九十]+[、.])\s*\S"
)
#: 纯标题行（无标点、无动作词、字数很短）——用于剔除被切出来的章节名
_HEADING_ONLY_RE = re.compile(
    r"^\s*(?:[0-9０-９]+(?:\s*[.．]\s*[0-9０-９]+)*\s*[.．、]?\s*)?"
    r"[\u4e00-\u9fa5A-Za-z/·（）()\s]{2,24}\s*$"
)
#: 章节名关键词：整行只有标题词时视为标题（"字段说明"、"原型图"…）
_HEADING_WORDS = (
    "背景说明", "需求说明", "需求描述", "需求范围", "产品方案", "原型图", "字段说明",
    "导入结果", "会员详情", "储值流水", "待确认事项", "适用范围", "术语表", "概述",
    "功能说明", "业务流程", "修订记录", "版本记录", "目录", "附录",
)
#: 出现这些动词/连接词，说明是"可验证的规则句"，不是标题。
#: 注意：这里刻意**不含**"导入/导出/记录/保存"等易出现在章节名里的词
#: （例如"2.3 导入结果"是标题，不是规则）。
_ACTION_HINTS = (
    "支持", "需要", "应当", "必须", "不能", "不得", "禁止", "允许", "校验", "提示",
    "显示", "隐藏", "提交", "删除", "新增", "修改", "查询",
    "过滤", "筛选", "默认为", "默认选中", "选项范围", "最多", "至少", "仅支持",
    "不足", "超出", "拒绝", "拦截", "流转", "审批", "扣减", "退还", "必填",
)
#: 强动作词：出现在"编号 + 极短行"时仍按规则句保留
_STRONG_ACTION_HINTS = _ACTION_HINTS

#: 元数据行（页眉页脚、版本表等）
_METADATA_RE = re.compile(
    r"^(文档版本|文檔版本|版本号|版本號|修改日期|修改人|修改內容|修改内容|修订历史|修訂歷史|"
    r"软件需求规格说明书|軟件需求規格說明書|全称|定義/說明|备注|備註|评论|評論|"
    r"V\d+(?:\.\d+)*|\d{4}[-/]\d{1,2}[-/]\d{1,2})"
)


def is_heading_line(text: str) -> bool:
    """判断一行是否为章节标题（非用例素材）。"""
    raw = _strip_zero_width(text or "").strip()
    if not raw:
        return True

    numbered = bool(_NUMBERED_HEADING_RE.match(raw))
    body = _NUMBERED_HEADING_RE.sub("", raw, count=1).strip() if numbered else raw
    body = body or raw
    has_punct = bool(re.search(r"[，。；、：:,.]", body))

    # 1) 纯标题词（"背景说明" / "2.1.2 字段说明" / "2.3 导入结果"）
    if body in _HEADING_WORDS:
        return True
    if len(body) <= 24 and any(body.endswith(word) for word in _HEADING_WORDS):
        return True

    # 2) 编号 + 极短无标点行（"1 企业充值/退款"、"2.2 储值流水"）
    if numbered and len(body) <= 14 and not has_punct:
        if not any(hint in body for hint in _STRONG_ACTION_HINTS):
            return True

    # 3) 无编号的极短无标点行
    if len(body) <= 12 and not has_punct and not any(hint in body for hint in _ACTION_HINTS):
        return True
    return False


def _strip_zero_width(text: str) -> str:
    return (text or "").replace("\u200b", "").replace("\ufeff", "")


# ---------------------------------------------------------------------------
# 字段规则模型
# ---------------------------------------------------------------------------

@dataclass
class FieldConstraint:
    """从"字段说明"里抽出的单条约束。"""

    kind: str                      # range|decimal_places|max_length|max_count|file_size|formats|options|default
    raw: str                       # 原文片段（保证可追溯）
    numbers: list[float] = dc_field(default_factory=list)
    detail: dict[str, Any] = dc_field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {"kind": self.kind, "raw": self.raw, "numbers": self.numbers, "detail": self.detail}


@dataclass
class FieldRule:
    """字段规则（字段说明表格的一行，已合并跨页续行）。"""

    name: str
    field_type: str = ""
    required: bool = False
    description: str = ""
    condition: str = ""
    constraints: list[FieldConstraint] = dc_field(default_factory=list)
    source: dict[str, Any] = dc_field(default_factory=dict)

    def constraint(self, kind: str) -> FieldConstraint | None:
        for item in self.constraints:
            if item.kind == kind:
                return item
        return None

    def summary(self) -> str:
        """给用例标题用的约束摘要（引用文档原值）。"""
        parts: list[str] = []
        for item in self.constraints:
            if item.kind == "range" and len(item.numbers) >= 2:
                left = "[" if item.detail.get("min_inclusive") else "("
                right = "]" if item.detail.get("max_inclusive") else ")"
                parts.append(
                    f"{left}{_fmt(item.numbers[0])},{_fmt(item.numbers[1])}{right}"
                )
            elif item.kind == "decimal_places" and item.numbers:
                parts.append(f"最多{_fmt(item.numbers[0])}位小数")
            elif item.kind == "max_length" and item.numbers:
                parts.append(f"最长{_fmt(item.numbers[0])}字")
            elif item.kind == "max_count" and item.numbers:
                parts.append(f"最多{_fmt(item.numbers[0])}条")
            elif item.kind == "file_size" and item.numbers:
                parts.append(f"文件≤{_fmt(item.numbers[0])}M")
            elif item.kind == "formats":
                parts.append("格式：" + "/".join(item.detail.get("formats", [])[:4]))
        return "；".join(parts)

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "field_type": self.field_type,
            "required": self.required,
            "description": self.description,
            "condition": self.condition,
            "constraints": [c.to_dict() for c in self.constraints],
            "source": self.source,
        }


@dataclass
class StructuredRequirement:
    full_text: str
    narrative_text: str
    field_rules: list[FieldRule] = dc_field(default_factory=list)
    template_tables: list[dict[str, Any]] = dc_field(default_factory=list)
    source_type: str = "text"
    #: 未能解析成字段标签的表格行（如 PDF 说明列里的整段文字）——显式暴露而非静默丢弃
    rejected_fields: list[dict[str, Any]] = dc_field(default_factory=list)
    #: 需要"重列化"才恢复成表的行数（PDF 抽取会把多列粘成一格）——审计用
    rebalanced_rows: int = 0
    #: 表结构完全没被识别、改用文本行重建出来的表数（无边框表格实测 find_tables()=0）
    rebuilt_table_count: int = 0
    #: 结构化解析整体失败、已降级为纯文本（此时 field_rules 必然为空，必须显式暴露）
    degraded: bool = False
    degraded_reason: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "source_type": self.source_type,
            "field_rule_count": len(self.field_rules),
            "field_rules": [r.to_dict() for r in self.field_rules],
            "template_table_count": len(self.template_tables),
            "rejected_field_count": len(self.rejected_fields),
            "rejected_fields": self.rejected_fields[:10],
            "rebalanced_rows": self.rebalanced_rows,
            "rebuilt_table_count": self.rebuilt_table_count,
            "degraded": self.degraded,
            "degraded_reason": self.degraded_reason,
        }


def _fmt(value: float) -> str:
    """数值格式化：保留小数精度（"%g" 会把 99999.01 截成 99999）。"""
    text = f"{float(value):.10f}".rstrip("0").rstrip(".")
    return text if text else "0"


# ---------------------------------------------------------------------------
# 约束抽取（正则，针对真实文档措辞；抽不到就不产出该类用例）
# ---------------------------------------------------------------------------

_RANGE_RE = re.compile(
    r"([\(\[])\s*([0-9]+(?:\.[0-9]+)?)\s*[,，]\s*([0-9]+(?:\.[0-9]+)?)\s*([\)\]])"
)
_DECIMAL_RE = re.compile(r"小数点后最多保留\s*([0-9]+)\s*位")
#: 长度上限：兼容简体原文（"最多输入50个字"）与 CS 规格书的繁體列表式（"限制輸入50個字符"）
_MAX_LENGTH_RE = re.compile(
    r"(?:最多输入|最多輸入|限制输入|限制輸入|限制|最多|上限|不超过|不超過)\s*"
    r"(?:輸入|输入|填寫|填写)?\s*([0-9]+)\s*(?:個|个)?\s*(?:字|字符)"
)
_MAX_COUNT_RE = re.compile(
    r"(?:最多输入|最多輸入|最多|上限|限制)\s*([0-9]+)\s*(?:個|个)?\s*"
    r"(手机号|手機號|条|條|附件|附件數|附件数)"
)
#: 文件大小：兼容"文件大小 10M"与"上傳文件大小不超過10m"
_FILE_SIZE_RE = re.compile(
    r"(?:文件|附件)?\s*大小\s*(?:不超過|不超过|限制|最大|上限|≤|<=)?\s*"
    r"([0-9]+(?:\.[0-9]+)?)\s*(M|MB|兆|G|GB)",
    re.IGNORECASE,
)
_FORMATS_RE = re.compile(r"文件格式(?:仅|僅)?支持[：:]\s*([^\n。；;]+)")
#: 选项/枚举：兼容"选项范围：…"与"展示枚舉内容Dine-in，In-Store Takeaway…"、"支持選擇ETZ"
#: 刻意**不含**裸「選項/选项」——正文里的"選項卡"之类会误命中
#: FIX-2026-09-28：补冒号式枚举「枚舉：Active，Closed，Overdue」（CS 规格书 Status 字段的写法，
#: 原词表只有「枚舉内容/枚舉值」两种形态 → 该枚举此前完全没被抽取，Status 字段无法生成断言）。
#: 裸「枚舉/枚举」仍**必须带冒号**才算声明，避免正文里的普通提及被当成枚举值。
_OPTIONS_RE = re.compile(
    r"(?:选项范围|選項範圍|枚舉内容|枚舉內容|枚举内容|枚举內容|"
    r"枚舉值|枚举值|枚舉值為|支持選擇|支持选择|"
    r"枚舉\s*[：:]|枚举\s*[：:])\s*[：:，,]?\s*([^\n。；;]+)"
)
#: 默认值：兼容"默认选中：X"与"默認為In Progress"、"默認YL選項"
_DEFAULT_RE = re.compile(
    r"(?:默认选中|默認選中|默认值|默認值|默认为|默認為|默認|默认)\s*[：:]?\s*"
    r"(?:為|为|是|值[：:]?)?\s*([^\n。；;，,]{1,40})"
)
#: 必填的**正面**表述（说明文本里写的，而不是表格列）
_REQUIRED_TEXT_RE = re.compile(r"必填|必選|不可[為为]空|不能[為为]空|不能为空")
#: 必填的**反面**表述（"非必選内容"里含"必選"，必须先剔除再判正面）
_OPTIONAL_TEXT_RE = re.compile(r"非必[選选填]|不必填|選填|选填|可選填|可选填|非必填")
_CONDITION_RE = re.compile(r"(当[^\n。；;]*?显示该字段)")
#: 连接词（不得进入字段名）
_CONNECTIVE_RE = re.compile(r"^(?:且|并且|并|和|以及|同时)+")
#: 条件里的"X选择：值" / "X选中：值"
_CONDITION_TERM_RE = re.compile(r"([\u4e00-\u9fa5A-Za-z]+)\s*(?:选择|选中)[：:]\s*([^且）\)\n。；;]+)")


def extract_constraints(description: str) -> list[FieldConstraint]:
    """从字段说明文本抽取约束（每条保留原文片段）。"""
    text = _strip_zero_width(description or "")
    out: list[FieldConstraint] = []

    match = _RANGE_RE.search(text)
    if match:
        out.append(FieldConstraint(
            kind="range",
            raw=match.group(0),
            numbers=[float(match.group(2)), float(match.group(3))],
            detail={
                "min_inclusive": match.group(1) == "[",
                "max_inclusive": match.group(4) == "]",
            },
        ))

    match = _DECIMAL_RE.search(text)
    if match:
        out.append(FieldConstraint(kind="decimal_places", raw=match.group(0),
                                   numbers=[float(match.group(1))]))

    match = _MAX_LENGTH_RE.search(text)
    if match:
        out.append(FieldConstraint(kind="max_length", raw=match.group(0),
                                   numbers=[float(match.group(1))]))

    match = _MAX_COUNT_RE.search(text)
    if match:
        out.append(FieldConstraint(kind="max_count", raw=match.group(0),
                                   numbers=[float(match.group(1))],
                                   detail={"unit": match.group(2)}))

    match = _FILE_SIZE_RE.search(text)
    if match:
        out.append(FieldConstraint(kind="file_size", raw=match.group(0),
                                   numbers=[float(match.group(1))],
                                   detail={"unit": match.group(2).upper()}))

    match = _FORMATS_RE.search(text)
    if match:
        raw_formats = re.split(r"[0-9]+\s*[、.]?\s*文件大小", match.group(1))[0]
        formats = [
            re.sub(r"^[0-9]+\s*[、.]?\s*", "", f).strip().lstrip(".")
            for f in re.split(r"[、,，/]", raw_formats)
            if f.strip()
        ]
        formats = [f for f in formats if f and not any(ch.isdigit() for ch in f)]
        if formats:
            out.append(FieldConstraint(kind="formats", raw="文件格式仅支持：" + "、".join(formats),
                                       detail={"formats": formats}))

    match = _OPTIONS_RE.search(text)
    if match:
        raw_options = re.split(
            r"默认选中|默认值|默认：|默認選中|默認值|默認為|默認", match.group(1)
        )[0]
        # 关键词本身可能落在捕获段开头（例："，支持選擇ETZ"）
        raw_options = re.sub(r"^(?:支持選擇|支持选择|為|为|是)\s*", "", raw_options).strip()
        options = []
        for item in re.split(r"[、,，/]", raw_options):
            text_item = item.strip().strip("。；;")
            if not text_item:
                continue
            # 仅当选项本身没有左括号时，才去掉尾部多余的右括号
            if "（" not in text_item and "(" not in text_item:
                text_item = text_item.rstrip("）)")
            options.append(text_item)
        if options:
            out.append(FieldConstraint(kind="options", raw=match.group(0),
                                       detail={"options": options}))

    match = _DEFAULT_RE.search(text)
    if match:
        value = match.group(1).strip()
        # "默認YL選項" 的尾缀"選項"不是值本身；但**不能**一并削掉尾字（
        # 实测把 "企业储值充值" 削成了 "企业储值充"）
        value = re.sub(r"(?:選項|选项)$", "", value).strip().strip("（）()")
        if value:
            out.append(FieldConstraint(kind="default", raw=match.group(0),
                                       detail={"value": value}))

    return out


def required_from_text(description: str) -> bool:
    """从**说明文本**判断是否必填。

    CS 规格书把"必填"写在说明里（例：`1. 必填2. 限制輸入50個字符…`），
    而不是单独的"是否必填"列——只按列判断会让整份文档的必填约束全部丢失
    （实测 128 条规则 required 全为 False → 0 条规则用例）。
    判据先把"非必選/选填"等**反面词**剔除，再看正面词，避免"非必選"被当成"必選"。
    """
    text = _strip_zero_width(description or "")
    stripped = _OPTIONAL_TEXT_RE.sub("", text)
    return bool(_REQUIRED_TEXT_RE.search(stripped))


def extract_condition(description: str) -> str:
    """抽取条件显示约束，例如"变动类型=企业储值充值 且 变更方式=批量导入"。"""
    text = _strip_zero_width(description or "")
    match = _CONDITION_RE.search(text)
    if not match:
        return ""
    clauses: list[str] = []
    for term in _CONDITION_TERM_RE.finditer(match.group(1)):
        name = _CONNECTIVE_RE.sub("", term.group(1).strip()).strip()
        value = _CONNECTIVE_RE.sub("", term.group(2).strip()).strip().rstrip("）)")
        value = re.sub(r"[，,]$", "", value).strip()
        if name and value:
            clauses.append(f"{name}={value}")
    condition = " 且 ".join(clauses)
    return re.sub(r"(?:\s*且\s*){2,}", " 且 ", condition)


# ---------------------------------------------------------------------------
# 表格解析
# ---------------------------------------------------------------------------

_FIELD_HEADER_HINTS = ("字段", "字段说明")
_REQUIRED_HINTS = ("是否必填", "必填", "是否必填项", "必填项", "是否必填欄位")
_TYPE_HINTS = ("字段类型", "类型", "字段類型", "類型", "欄位類型")
#: 说明列的别名（含繁体：CS 规格书用「字段説明」，简繁不兼容会导致整张表识别失败）
_DESC_HINTS = ("字段说明", "字段説明", "说明", "説明", "字段描述", "描述", "备注", "備註",
               # rev104：篩選查詢區/匹配規則類表格同樣承載字段校驗規則
               "校驗", "校验", "篩選規則", "筛选规则", "匹配規則", "匹配规则")


def _normalize_cell(value: Any) -> str:
    text = _strip_zero_width(str(value or ""))
    # 表格单元格里的软换行还原成空格，便于正则匹配
    return re.sub(r"\s*\n\s*", "", text).strip()


def _is_field_table(header: list[str]) -> bool:
    joined = " ".join(_normalize_cell(c) for c in header)
    # 典型字段表：同时出现「字段」与「说明/説明/描述/备注」
    if "字段" in joined or "欄位" in joined:
        if any(hint in joined for hint in _DESC_HINTS) or "必填" in joined:
            return True
    return all(hint in joined for hint in _FIELD_HEADER_HINTS)


def _column_index(header: list[str], hints: tuple[str, ...]) -> int | None:
    for idx, cell in enumerate(header):
        text = _normalize_cell(cell)
        if any(hint in text for hint in hints):
            return idx
    return None


#: 列间隙痕迹：PDF 抽取把"多列粘成一格"后，列与列之间通常仍是 2+ 空格/制表符
_CELL_SPLIT_RE = re.compile(r"[ \t\u3000]{2,}")
#: 可能承载"字段名"的表头列（含繁体/英文）
_NAME_HINTS = ("字段", "欄位", "名稱", "名称", "name", "標題", "标题", "title")


def _explode_cells(cells: list[str]) -> list[str]:
    """按列间隙把（可能粘在一起的）单元格炸开。"""
    parts: list[str] = []
    for cell in cells:
        for piece in _CELL_SPLIT_RE.split(_normalize_cell(cell)):
            piece = piece.strip()
            if piece:
                parts.append(piece)
    return parts


#: "字段名 → 类型/必填标记"的分界：`Member 是 必填，…` / `Case Status Select 是 …`
_GLUED_MARKER_RE = re.compile(
    r"(?<=[\w\u4e00-\u9fff])\s+"
    r"(?=(?:是|否|必填|必選|選填|選取|Y|N|Text|Select|Date|Number|Input|DatePicker|Checkbox)\b)"
)


def _split_glued_row(text: str, header_len: int) -> list[str] | None:
    """整行被抽成**一格**时，切出真正的字段名（其余全部进"说明"列）。

    实测（PR-9 CS 规格书）：PDF 抽取把整行粘成一格，说明列文字落到字段名列，
    被 `_looks_like_field_label` 拒绝后整行丢弃 → `field_rules=0`。
    这里只恢复**字段名**：类型/必填留空（不猜、不造假），
    说明列放最后并保留原文，供 `extract_constraints` 按文档措辞抽断言。
    """
    if header_len < 2:
        return None
    normalized = _normalize_cell(text)
    match = _GLUED_MARKER_RE.search(normalized)
    if match is None:
        return None
    label = normalized[: match.start()].strip()
    rest = normalized[match.start():].strip()
    if not rest or not _looks_like_field_label(label):
        return None
    return [label] + [""] * (header_len - 2) + [rest]


def _rebalance_row(cells: list[str], header_len: int) -> list[str]:
    """把"列数与表头不符"的行还原成 header_len 列。

    实测（PR-9 CS 规格书 PDF）：整行被抽成一格，说明列的整段文字落到"字段名"列，
    被 `_looks_like_field_label` 全量拒绝 → `field_rules=0` → 该批 0 条可执行断言。
    策略保守，不做无依据的猜测：

    - 列数已相符 → 原样返回；
    - 按 2+ 空格炸开后**正好**等于 header_len → 直接采用；
    - 多于 header_len → 前 header_len-1 列各取一段，其余全部并入**最后一列**（说明列通常最长）；
    - 仍不足且整行只有一格 → 用 `_split_glued_row` 切出字段名（单空格粘连形态）；
    - 都不成立 → 不猜，原样返回（仍走拒绝路径，只记录 rejected_fields）。
    """
    if header_len <= 0 or len(cells) == header_len:
        return cells
    parts = _explode_cells(cells)
    if len(parts) < header_len:
        if len(cells) == 1:
            glued = _split_glued_row(cells[0], header_len)
            if glued is not None:
                return glued
        return cells
    if len(parts) == header_len:
        return parts
    return parts[: header_len - 1] + [" ".join(parts[header_len - 1:])]


def _raw_rows_from_table(
    rows: list[list[str]],
    header: list[str],
    table_index: int,
    *,
    start_row: int,
) -> list[dict[str, Any]]:
    """按表头列位把表格行映射成原始行（保留表号/行号用于追溯）。"""
    idx_name = _column_index(header, ("字段",))
    idx_type = _column_index(header, _TYPE_HINTS)
    idx_required = _column_index(header, _REQUIRED_HINTS)
    idx_desc = _column_index(header, _DESC_HINTS)
    if idx_name is None:
        idx_name = 0
    if idx_desc is None:
        idx_desc = len(header) - 1

    out: list[dict[str, Any]] = []
    header_len = len(header)
    for offset, row in enumerate(rows):
        raw_cells = [_normalize_cell(c) for c in row]
        if not any(raw_cells):
            continue
        cells = _rebalance_row(raw_cells, header_len)
        rebalanced = cells != raw_cells

        def cell(index: int | None) -> str:
            if index is None or index >= len(cells):
                return ""
            return cells[index]

        name = cell(idx_name)

        out.append({
            "name": name,
            "type": cell(idx_type),
            "required": cell(idx_required),
            "desc": cell(idx_desc),
            "table": table_index,
            "row": start_row + offset,
            "rebalanced": rebalanced,
        })
    return out


def _merge_raw_rows(raw: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """合并跨页续行（无名行并入相邻具名行）。"""
    merged: list[dict[str, Any]] = []
    i = 0
    while i < len(raw):
        item = raw[i]
        if not item["name"]:
            nxt = raw[i + 1] if i + 1 < len(raw) else None
            if nxt is not None and nxt["name"] and not nxt["required"]:
                nxt["required"] = nxt["required"] or item["required"]
                nxt["desc"] = (item["desc"] + " " + nxt["desc"]).strip()
                nxt["table"] = min(item["table"], nxt["table"])
                nxt["row"] = min(item["row"], nxt["row"])
                nxt["rebalanced"] = bool(item.get("rebalanced") or nxt.get("rebalanced"))
                merged.append(nxt)
                i += 2
                continue
            if merged:
                merged[-1]["desc"] = (merged[-1]["desc"] + " " + item["desc"]).strip()
                merged[-1]["required"] = merged[-1]["required"] or item["required"]
                merged[-1]["rebalanced"] = bool(
                    merged[-1].get("rebalanced") or item.get("rebalanced")
                )
                i += 1
                continue
            i += 1
            continue
        merged.append(item)
        i += 1
    return merged


_SENTENCE_PUNCT = "。；;！？!?"


def _looks_like_field_label(name: str) -> bool:
    """判断表格"字段/名称"列里的值是否真的是**字段标签**。

    实测问题（PR-9 CS 文档）：PDF 表格的说明列是整段文字，被当成字段名后
    产出 `field="2. 選擇"Rejected"出現Reasons輸入框…"` 这样的规则——编译层是按
    字段名匹配定位器的，于是这些规则 0 条可执行（实测 0/7），只是把噪音搬进了"规则"。
    判据保守：只剔除**明显是段落/列表项**的值，正常短标签一律放行。
    """
    text = (name or "").strip()
    if not text or len(text) > 24:
        return False
    if any(p in text for p in _SENTENCE_PUNCT):
        return False
    if "：" in text or ":" in text:
        return False
    # 列表项编号开头："1. xxx" / "b. xxx" / "h、xxx"
    if re.match(r"^([a-zA-Z]|\d{1,2})\s*[.)、]\s*\S", text):
        return False
    # 长且多分句 → 段落
    if len(text) > 16 and text.count("，") >= 2:
        return False
    return True


def _split_tables(
    tables: list[list[list[str]]],
) -> tuple[list[FieldRule], list[dict[str, Any]], list[dict[str, Any]], int]:
    """把表格分成「字段说明表」与「模板表」，并处理跨页续表（表头只在首页）。

    返回 `(field_rules, template_tables, rejected_fields, rebalanced_rows)`；
    最后一个值是"需要重列化才恢复的行数"，用于审计 PDF 抽取质量。
    """
    raw: list[dict[str, Any]] = []
    template_tables: list[dict[str, Any]] = []
    current_header: list[str] | None = None

    for index, rows in enumerate(tables):
        if not rows:
            continue
        header = rows[0]
        if _is_field_table(header):
            current_header = header
            raw.extend(_raw_rows_from_table(rows[1:], header, index, start_row=2))
            continue
        if current_header is not None and len(header) == len(current_header):
            # 续表：表头只在首页，续页直接是数据行
            raw.extend(_raw_rows_from_table(rows, current_header, index, start_row=1))
            continue
        body = [r for r in rows[1:] if any(_normalize_cell(c) for c in r)]
        if header and len(header) <= 6:
            template_tables.append({
                "table": index,
                "header": [_normalize_cell(c) for c in header],
                "row_count": len(body),
            })

    field_rules: list[FieldRule] = []
    rejected_fields: list[dict[str, Any]] = []
    merged_rows = _merge_raw_rows(raw)
    for item in merged_rows:
        if not item["name"]:
            continue
        if not _looks_like_field_label(item["name"]):
            # 不静默丢弃：记录下来，供覆盖度/质量报告暴露"没能解析成字段的表格行"
            rejected_fields.append({
                "name": item["name"][:120],
                "reason": "不是字段标签（疑似段落文本/列表项），该行不生成可编译规则",
                "source": {"table": item["table"], "row": item["row"],
                           "rebalanced": bool(item.get("rebalanced"))},
            })
            continue
        field_rules.append(FieldRule(
            name=item["name"],
            field_type=item["type"],
            # 必填：表格列（是/必填）**或**说明文本里的"必填"（CS 规格书把必填写在说明里）
            required=(
                "是" in item["required"]
                or "必填" in item["required"]
                or required_from_text(item["desc"])
            ),
            description=item["desc"],
            condition=extract_condition(item["desc"]),
            constraints=extract_constraints(item["desc"]),
            source={"table": item["table"], "row": item["row"],
                    "rebalanced": bool(item.get("rebalanced"))},
        ))
    rebalanced_rows = sum(1 for item in merged_rows if item.get("rebalanced"))
    return field_rules, template_tables, rejected_fields, rebalanced_rows


#: 文本行重建时的表头判据：同时出现"字段/欄位"与"说明/説明/描述/备注/必填"
def _tables_from_text_lines(text: str) -> list[list[list[str]]]:
    """表结构完全没被识别时，用**文本行的列间隙**把字段说明表重建出来。

    实测（PyMuPDF 1.27）：无边框线的表格 `page.find_tables()` 返回 **0 张表**，
    于是字段说明表整表丢失 → `field_rule_count=0` → 整批用例 0 条可执行断言。
    这里按"表头行 + 连续的多段行"重建，避免这种静默归零。
    """
    lines = [line.rstrip() for line in (text or "").splitlines()]
    tables: list[list[list[str]]] = []
    index = 0
    while index < len(lines):
        header_parts = [p for p in _CELL_SPLIT_RE.split(lines[index].strip()) if p.strip()]
        if len(header_parts) < 2 or not _is_field_table(header_parts):
            index += 1
            continue
        rows: list[list[str]] = [header_parts]
        cursor = index + 1
        blank_run = 0
        while cursor < len(lines) and len(rows) < 200:
            line = lines[cursor].strip()
            if not line:
                blank_run += 1
                if blank_run >= 2:
                    break
                cursor += 1
                continue
            blank_run = 0
            parts = [p for p in _CELL_SPLIT_RE.split(line) if p.strip()]
            if len(parts) < 2:
                # 单段行：表格通常已结束（下一节标题/正文）
                if len(rows) > 1:
                    break
                cursor += 1
                continue
            rows.append(parts)
            cursor += 1
        if len(rows) > 1:
            tables.append(rows)
        index = cursor if cursor > index else index + 1
    return tables


# ---------------------------------------------------------------------------
# 文档解析入口
# ---------------------------------------------------------------------------

def _table_text_blob(tables: list[list[list[str]]]) -> set[str]:
    """表格文本集合（用于从 PDF 叙述文本里剔除表格内容）。"""
    blob: set[str] = set()
    for rows in tables:
        for row in rows:
            for cell in row:
                text = _normalize_cell(cell)
                if len(text) >= 2:
                    blob.add(text)
    return blob


def _drop_table_lines(text: str, blob: set[str]) -> str:
    if not blob:
        return text
    kept: list[str] = []
    for line in (text or "").splitlines():
        norm = _normalize_cell(line)
        if not norm:
            continue
        if norm in blob or any(norm and norm in cell for cell in blob):
            continue
        kept.append(line.rstrip())
    return "\n".join(kept)


_SENTENCE_END = "。！？!?；;"
#: 新块起始：编号列表项 / 项目符号 / 页码标记 / 协作回复
_NEW_BLOCK_RE = re.compile(r"^\s*(\d{1,2}\s*[.)、]|[a-hA-H]\s*[.)、]|[•·▪◦*\-–—]\s|@|={2,}\s*PAGE)")
#: 分页标记：本身是噪音，既不进结果也不参与合并（否则会粘成 "===== PAGE 1 =====标题"）
_PAGE_MARKER_RE = re.compile(r"^\s*={2,}\s*(PAGE|页|頁)\s*\d+\s*={2,}\s*$", re.IGNORECASE)
#: 结构化章节标记（与"长度启发式"无关，可安全用于断句）
_HEADING_MARKER_RE = re.compile(
    r"^\s*(第[一二三四五六七八九十百]+[章節节]|[一二三四五六七八九十]+、|"
    r"[（(][一二三四五六七八九十\d]+[)）]|\d+(\.\d+)*[.)、]\s|[A-Za-z][.)、]\s)"
)


def _is_short_label(line: str) -> bool:
    """短且无句读的行多为标签/小标题（"一、背景"、"搜索框"），不应被当作长句的续行。"""
    text = (line or "").strip()
    return bool(text) and len(text) <= 12 and "，" not in text and text[-1] not in _SENTENCE_END


def _starts_new_block(line: str) -> bool:
    """只按**结构标记**判断新块起始。

    刻意**不**使用 `is_heading_line`：它是给"过滤需求条目"用的启发式（短、无标点即视为标题），
    用在断行合并上会把短续行（实测 "戶滿意度調研"）误判成新块，导致合句失败。
    短小标题由 `_is_short_label(buf)` 在合并前断开处理，两者职责分开。
    """
    if not line:
        return True
    return bool(_NEW_BLOCK_RE.match(line) or _HEADING_MARKER_RE.match(line))


def _merge_wrapped_lines(text: str) -> str:
    """把 PDF 硬换行的碎片合并回完整句子。

    实测（CS 规格书）：一条需求被行宽切成
        「搭建 CS Module 客户服务管理系统，统一归集多渠道客户诉求，实现 Case 工单的指」
        「派、进度追踪、历史记录查询」
    抽取时被当成 2~3 条独立"需求"，既产生噪音又**吃掉 12 条叙述配额**，
    导致文档后半部分的真实规则永远抽不到（这才是"漏"的主因）。
    这里只在**明确是续行**时合并：上一行没有句末标点，且本行不是新块起始。
    """
    out: list[str] = []
    buf = ""
    for raw in (text or "").splitlines():
        line = raw.strip()
        if not line:
            if buf:
                out.append(buf)
                buf = ""
            continue
        if _PAGE_MARKER_RE.match(line):
            # 分页标记是噪音：丢弃且不参与合并
            if buf:
                out.append(buf)
                buf = ""
            continue
        if _starts_new_block(line) or (_is_short_label(buf) and buf[-1] not in _SENTENCE_END):
            if buf:
                out.append(buf)
                buf = ""
            buf = line
            continue
        if not buf:
            buf = line
        elif buf[-1] in _SENTENCE_END:
            out.append(buf)
            buf = line
        else:
            # 中文之间直接相接；两侧都是 ASCII 时补一个空格避免粘连
            if buf[-1].isascii() and line[0].isascii() and buf[-1].isalnum() and line[0].isalnum():
                buf = f"{buf} {line}"
            else:
                buf = buf + line
        if buf and buf[-1] in _SENTENCE_END:
            out.append(buf)
            buf = ""
    if buf:
        out.append(buf)
    return "\n".join(out)


def _parse_docx(content: bytes) -> tuple[str, str, list[list[list[str]]]]:
    from docx import Document

    document = Document(io.BytesIO(content))
    paragraphs = [p.text for p in document.paragraphs if _strip_zero_width(p.text).strip()]
    tables: list[list[list[str]]] = []
    for table in document.tables:
        rows: list[list[str]] = []
        for row in table.rows:
            rows.append([cell.text for cell in row.cells])
        if rows:
            tables.append(rows)
    table_text = "\n".join("\t".join(_normalize_cell(c) for c in row) for rows in tables for row in rows)
    full_text = "\n".join(paragraphs + ([table_text] if table_text else []))
    return full_text, "\n".join(paragraphs), tables


def _parse_pdf(content: bytes) -> tuple[str, str, list[list[list[str]]]]:
    import fitz

    doc = fitz.open(stream=content, filetype="pdf")
    page_texts: list[str] = []
    tables: list[list[list[str]]] = []
    for page in doc:
        page_texts.append(page.get_text("text") or "")
        finder = getattr(page, "find_tables", None)
        if finder is None:
            continue
        try:
            found = finder()
        except Exception:  # noqa: BLE001 —— 表格识别失败不影响文本解析
            continue
        for table in getattr(found, "tables", []) or []:
            try:
                rows = table.extract()
            except Exception:  # noqa: BLE001
                continue
            if rows:
                tables.append(rows)
    full_text = "\n".join(page_texts)
    return full_text, full_text, tables


def _parse_plain(content: bytes) -> tuple[str, str, list[list[list[str]]]]:
    for encoding in ("utf-8", "utf-8-sig", "gb18030", "latin-1"):
        try:
            text = content.decode(encoding)
            return text, text, []
        except UnicodeDecodeError:
            continue
    return "", "", []


def parse_requirement_document(
    filename: str | None,
    content_type: str | None,
    content: bytes,
) -> StructuredRequirement:
    """解析需求文档 → 结构化结果（失败自动降级为纯文本）。"""
    suffix = Path(filename or "").suffix.lower()
    normalized_type = (content_type or "").lower()

    source_type = "text"
    try:
        if suffix == ".docx" or "openxmlformats-officedocument.wordprocessingml" in normalized_type:
            source_type = "docx"
            full_text, narrative, tables = _parse_docx(content)
        elif suffix == ".pdf" or normalized_type == "application/pdf" or content[:4] == b"%PDF":
            source_type = "pdf"
            full_text, narrative, tables = _parse_pdf(content)
        else:
            full_text, narrative, tables = _parse_plain(content)
    except Exception:  # noqa: BLE001 —— 结构化失败降级为纯文本
        full_text, narrative, tables = _parse_plain(content)
        source_type = "text"

    field_rules, template_tables, rejected_fields, rebalanced_rows = _split_tables(tables)
    # 兜底（rev104 起**总是**执行，不再仅零规则时）：fitz 表格提取会被行内
    # 評論塊截斷（实测 YL 主數據：店鋪字段表中段 12 行被「評論（7）」打散丟失），
    # 用文本行的列间隙重建并**按字段名去重合并**——既兜零表，也补断表。
    rebuilt = _tables_from_text_lines(full_text)
    rebuilt_table_count = 0
    if rebuilt:
        extra_rules, extra_templates, extra_rejected, extra_rebalanced = _split_tables(rebuilt)
        seen = {rule.name for rule in field_rules}
        added = [rule for rule in extra_rules if rule.name not in seen]
        if added:
            rebuilt_table_count = len(rebuilt)
            field_rules = field_rules + added
            template_tables = template_tables + extra_templates
            rejected_fields = rejected_fields + extra_rejected
            rebalanced_rows += extra_rebalanced
    if source_type == "pdf":
        # PDF 的表格文本混在页文本里 → 从叙述文本中剔除，避免重复生成噪音用例
        narrative = _drop_table_lines(narrative, _table_text_blob(tables))
        # 再把硬换行碎片合并成完整句子（否则一条需求会被算成 2~3 条，挤掉真规则）
        narrative = _merge_wrapped_lines(narrative)

    return StructuredRequirement(
        full_text=full_text,
        narrative_text=narrative,
        field_rules=field_rules,
        template_tables=template_tables,
        source_type=source_type,
        rejected_fields=rejected_fields,
        rebalanced_rows=rebalanced_rows,
        rebuilt_table_count=rebuilt_table_count,
    )


__all__ = [
    "FieldConstraint",
    "FieldRule",
    "StructuredRequirement",
    "extract_condition",
    "extract_constraints",
    "is_heading_line",
    "parse_requirement_document",
]
