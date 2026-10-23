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
from functools import lru_cache
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
    #: 「是否可編輯」列原文（是/否/系統自動生成/""）；列不存在時為空串，不猜
    editable: str = ""
    constraints: list[FieldConstraint] = dc_field(default_factory=list)
    source: dict[str, Any] = dc_field(default_factory=dict)

    def constraint(self, kind: str) -> FieldConstraint | None:
        for item in self.constraints:
            if item.kind == kind:
                return item
        return None

    @property
    def case_eligible(self) -> bool:
        """本条规则能否产出**可判定断言**的用例。

        rev132：生成门槛（`必填 或 有枚举取值`）此前散在 `ai.py::_field_rule_cases` 里，
        解析侧无法回答"27 条规则只出了 10 条用例，另外 17 条为什么没了"——
        规则被静默丢弃且零告警。把判据收口到这里，生成与审计共用同一个口径。
        """
        return bool(self.required or self.constraint("options"))

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
                # rev111：单位取原文（"最多7組"/"最多30天"），不再一律写成"条"
                unit = (item.detail or {}).get("unit") or "条"
                parts.append(f"最多{_fmt(item.numbers[0])}{unit}")
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
            "editable": self.editable,
            "constraints": [c.to_dict() for c in self.constraints],
            "source": self.source,
        }


@dataclass
class StructuredRequirement:
    full_text: str
    narrative_text: str
    field_rules: list[FieldRule] = dc_field(default_factory=list)
    template_tables: list[dict[str, Any]] = dc_field(default_factory=list)
    #: 「列表顯示字段」表的欄位清單（每表一项：table + columns[name/desc/source]）——
    #: 与 template_tables 互补：template_tables 只存表头/行数，这里存**每一列**
    display_columns: list[dict[str, Any]] = dc_field(default_factory=list)
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
    #: 每页行数（PDF 才有）——把「表号」翻译成「文档位置」的必要输入，缺省为空
    page_line_counts: list[int] = dc_field(default_factory=list)
    #: 表号 → {line, heading, section}：表在文档里的定位（缺省为空，用表头行文本定位）
    table_locations: dict[int, dict[str, Any]] = dc_field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "source_type": self.source_type,
            "field_rule_count": len(self.field_rules),
            # 能过生成门槛（必填/枚举）的规则数：与 field_rule_count 的差额 = 静默丢弃的规则
            "field_rule_case_eligible": sum(1 for r in self.field_rules if r.case_eligible),
            "field_rules": [r.to_dict() for r in self.field_rules],
            "template_table_count": len(self.template_tables),
            "display_column_tables": len(self.display_columns),
            "display_columns": self.display_columns,
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
#: 小数位数的其余三种真实写法（rev183，真实文档实测）：
#: 「最多2位小数」「最多保留2位小数」「保留两位小数」「小数位数：2」。
#: 原词表只认「小数点后最多保留N位」，实测整类精度约束被静默判「无约束」。
_DECIMAL_ALT_RE = re.compile(
    r"(?:小数点后|小數點後|小数|小數)?\s*"
    r"(?:最多|最多保留|最多保留至|保留|限制|限制为|限制為|上限|不超过|不超過|精确到|精確到)?\s*"
    r"([0-9]+|[一二两三四五六两])\s*(?:位|個位|个位)\s*小数"
)
#: 长度上限：兼容简体原文（"最多输入50个字"）与 CS 规格书的繁體列表式（"限制輸入50個字符"）
_MAX_LENGTH_RE = re.compile(
    r"(?:最多输入|最多輸入|限制输入|限制輸入|限制|最多|上限|不超过|不超過|小於等於|小于等于)\s*"
    r"(?:輸入|输入|填寫|填写)?\s*([0-9]+)\s*(?:個|个)?\s*(?:字|字符)"
)
_MAX_COUNT_RE = re.compile(
    # rev111：补「最多(支持)(選擇|設置)N 組/道/時間段/天」——实测这些写法此前**完全不被识别**，
    # 导致 YL 营业时间「最多支持選擇7組」、YL「最多選擇30天」（EDW 筛选）、
    # PR-2「限制最多设置10道」（问卷题目上限）都产不出边界用例。
    r"(?:最多|上限|限制(?:最多)?)\s*(?:支持|支援)?\s*"
    r"(?:選擇|选择|設置|设置|輸入|输入|填寫|填写|新增|添加)?\s*"
    r"([0-9]+)\s*(?:個|个)?\s*"
    r"(手机号|手機號|时间段|時間段|附件數|附件数|附件|组|組|道|条|條|项|項|天|日)"
)
#: 文件大小：兼容"文件大小 10M"与"上傳文件大小不超過10m"
_FILE_SIZE_RE = re.compile(
    r"(?:文件|附件)?\s*大小\s*(?:不超過|不超过|限制|最大|上限|≤|<=)?\s*"
    r"([0-9]+(?:\.[0-9]+)?)\s*(M|MB|兆|G|GB)",
    re.IGNORECASE,
)
_FORMATS_RE = re.compile(r"文件格式(?:仅|僅)?支持[：:]\s*([^\n。；;]+)")
#: 文件格式的**其余声明形态**（rev183，真实文档实测）：
#: 「文件格式仅支持jpg，png格式」「仅支持jpg、png格式」「仅支持 jpg/png 格式」
#: 「支持jpg、png格式上传」「上传格式支持：jpg，png」「支持的文件类型：jpg/png」。
#: 原词表要求字面「文件格式支持：」，实测这几种写法全判「无约束」
#: （小杨生煎问卷「3. 文件格式仅支持：jpg、png」能抽到，是因为它恰好带了冒号）。
_EXT_LIST = (
    r"(?P<exts>[A-Za-z0-9]{1,5}(?:\s*[、,，/／|｜]\s*\.?[A-Za-z0-9]{1,5})*)"
)
_FORMATS_ALT_RE = re.compile(
    r"(?:(?:文件格式|檔案格式|附件格式|上[传傳]格式|文件[类類]型|檔案[類类]型|格式)"
    r"\s*(?:[仅僅只]|可)?\s*(?:支持|支援|允许|允許|接受|限定|為|为|是)?"
    r"|(?:[仅僅只]|可)?\s*(?:支持|支援|允许|允許|接受)\s*(?=[A-Za-z0-9]))"
    r"\s*[：:]?\s*"
    + _EXT_LIST +
    r"\s*(?:格式|類型|类型|的文件|的檔案)?"
)
#: 扩展名白名单：只认「看起来就是扩展名」的短串，避免把中文短语当扩展名。
_KNOWN_EXTS = {
    "jpg", "jpeg", "png", "gif", "bmp", "webp", "svg", "heic", "tif", "tiff",
    "pdf", "doc", "docx", "xls", "xlsx", "csv", "txt", "rtf", "ppt", "pptx",
    "zip", "rar", "7z", "mp4", "mov", "avi", "mp3", "wav", "json", "xml",
}
#: 中文数字 → 阿拉伯数字（「保留两位小数」）
_CN_NUM = {"一": 1, "二": 2, "两": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9}
#: 格式清单被否定的语境：「不支持」「暂不支持」「禁止上传」「不可上传」——
#: 命中的不是"可用格式"，不能反过来生成"该格式应被接受"的断言。
_FORMAT_NEGATION_RE = re.compile(r"(?:不|未|禁|无法|無法)\s*(?:支持|支援|允许|允許|接受|上[传傳])")


def _format_list_is_placeholder(text: str, formats: list[str]) -> bool:
    """格式清单是否只是「被否定的例子」（不支持/禁止上传…）。"""
    if not formats or not _FORMAT_NEGATION_RE.search(text or ""):
        return False
    # 只有否定语出现在格式词附近（同一句）时才让步，避免整段里别处的「不支持」误伤
    sentence = re.split(r"[。\n]", text or "")[0]
    return bool(_FORMAT_NEGATION_RE.search(sentence))
#: 选项/枚举：兼容"选项范围：…"与"展示枚舉内容Dine-in，In-Store Takeaway…"、"支持選擇ETZ"
#: 刻意**不含**裸「選項/选项」——正文里的"選項卡"之类会误命中
#: FIX-2026-09-28：补冒号式枚举「枚舉：Active，Closed，Overdue」（CS 规格书 Status 字段的写法，
#: 原词表只有「枚舉内容/枚舉值」两种形态 → 该枚举此前完全没被抽取，Status 字段无法生成断言）。
#: 裸「枚舉/枚举」仍**必须带冒号**才算声明，避免正文里的普通提及被当成枚举值。
_OPTIONS_RE = re.compile(
    # rev111：容错「選項發範圍」（YL 導入任務筛选字段「狀態」的错别字，实测因此识别不出枚举
    # → 该字段零用例）；同时容忍字间空格。
    # rev132：补「可选项/可選項」（小杨生煎问卷 PDF 写「2. 可选项：外卖、自提、堂食」，
    # 原词表不认 ⇒ 业务类型 3 个合法取值从未进用例）；捕获段不再在「；」处截断，
    # 交给下面按分隔符切分 + 条款词/编号尾缀清理，否则「是；否」只剩「是」。
    r"(?:選\s*項\s*[發发]?\s*範圍|选\s*项\s*[发發]?\s*范围|"
    r"可\s*选\s*项|可\s*選\s*項|"
    r"枚舉内容|枚舉內容|枚举内容|枚举內容|"
    r"枚舉值|枚举值|枚舉值為|支持選擇|支持选择|"
    r"枚舉\s*[：:]|枚举\s*[：:])\s*[：:，,]?\s*([^\n。]+)"
)
#: 默认值：兼容"默认选中：X"与"默認為In Progress"、"默認YL選項"
_DEFAULT_RE = re.compile(
    r"(?:默认选中|默認選中|默认值|默認值|默认为|默認為|默認|默认)\s*[：:]?\s*"
    r"(?:為|为|是|值[：:]?)?\s*([^\n。；;，,]{1,40})"
)
#: 必填的**正面**表述（说明文本里写的，而不是表格列）
#: rev132：补简体「必选」——原词表只有繁体「必選」，实测小杨生煎问卷 PDF 原文写
#: 「1. 多选，必选」⇒ required_from_text 返回 False，业务类型/是否必答双双被判非必填、
#: 整条规则卡在生成门槛外（该 PDF 30 条规则只有 4 条过门槛）。
_REQUIRED_TEXT_RE = re.compile(r"必填|必選|必选|不可[為为]空|不能[為为]空|不能为空")

#: 只读/编辑态：文档把「不可編輯」寫在說明列裡（YL 主數據經度/緯度），無獨立「是否可編輯」列。
#: 刻意不碰裸「禁用」——「禁用該功能後…」是行為規則不是字段狀態；「禁用狀態」才算字段態。
_READ_ONLY_RE = re.compile(
    r"(不可[編编]輯|僅可查看|仅供查看|只讀|只读|僅供查看|禁用狀態|禁用状态)")
#: 必填的**反面**表述（"非必選内容"里含"必選"，必须先剔除再判正面）
#: rev132：补简体「非必选」「不需填」——否则新版正面词「必选」会被否定句误判成必填
_OPTIONAL_TEXT_RE = re.compile(
    r"非必[選选填]|不必填|不需填|選填|选填|可選填|可选填|非必填")
#: 条件显示声明。rev111 起不再只认「当…显示该字段」：
#: 实测真实文档（YL 主數據）用的是另外三种写法——
#: 「排隊打開顯示該字段」「排隊打開且排隊集成方選擇GULU、QRS時顯示該字段」
#: 「當店鋪狀態開啟時，顯示該字段」，此前全部识别不出 → 两文档「条件显示校验」用例数为 **0**。
_CONDITION_RE = re.compile(
    r"((?:[當当])?[^\n。；;]{2,80}?)\s*(?:時|时)?\s*[，,]?\s*"
    r"(?:顯示該字段|显示该字段|顯示該欄位|显示该栏位|"
    r"才顯示該字段|才显示该字段|才顯示|才显示|"
    # rev184：裸「顯示/显示」**必须带字段宾语**（该字段/该栏位/此字段/此栏位/該項…）。
    # rev183 曾把它写成 `顯示|显示|展示` 裸分支，结果真实文档里「固定显示，包括…」
    # 「为空时显示"-"」这类**状态/交互描述**全被判成条件显示（8 个字段 cond 从 '' 变 '固定'），
    # 「不需要展示路径」被判成 cond='也不需要' —— 会直接生成假的「条件显示校验」用例。
    r"(?:顯示|显示|展示)(?:該|该|此|這|这)?(?:字段|欄位|栏位|項|项))"
)
#: rev183：条件声明的**前置语式**——「仅当…显示该字段」原来看不懂，
#: 因为 `_CONDITION_RE` 要求条件短语 ≥2 字、且「仅当」贴着条件不构成可识别前缀，
#: 实测「仅当订单类型选择外卖时显示该字段」整条被判"无条件"（条件显示用例因此为 0）。
#: 这里把引导词单独抽出来：模式 = `[仅在]?(?:当|如果|若)…(时)…才?显示`。
_CONDITION_GUARD_RE = re.compile(
    r"(?:[仅僅只][当當]|[当當]|如果|若)\s*[^\n。；;]{2,80}?"
    r"(?:[时時][，,]?\s*(?:才|則|则)?\s*(?:顯示|显示|展示|可[见見])"
    r"|(?:才|則|则)\s*(?:顯示|显示|展示))"
)
#: 连接词（不得进入字段名）
_CONNECTIVE_RE = re.compile(r"^(?:且|并且|并|和|以及|同时)+")
#: 条件里的"X选择：值" / "X选中：值" / "X=值"
_CONDITION_TERM_RE = re.compile(
    r"([\u4e00-\u9fa5A-Za-z]+)\s*(?:选择|选中|選中|選擇|为|為|是|=|：|:)\s*([^且）\)\n。；;，,]+)"
)
#: rev184：条件段的**语义门槛**。命中条件声明还不够，条件本身得是"条件"。
#: 实测反例（真实 PDF，全部应为「无条件」）：
#:   「固定显示，包括支付宝…」→「固定」不是条件；
#:   「也不需要展示路径」→「也不需要」不是条件；
#:   「为空时显示"-"」→ 是值的兜底显示，不是字段的条件显示。
#: 正例必须至少具备一项：引导词（当/仅当/如果/若）、比较/状态谓词（选择/等于/大于/开启/
#: 打开/勾选/满足/包含…）、或明确的数值比较符。
_CONDITION_SEMANTIC_RE = re.compile(
    r"(?:[當当]|如果|若|除非|一旦"
    r"|选择|選擇|选中|選中|勾选|勾選|填写|填寫|输入|輸入|录入|錄入"
    r"|等于|等於|!=|>=|<=|[><=＝]"
    r"|开启|開啟|打开|打開|关闭|關閉|启用|啟用|禁用|停用"
    r"|满足|滿足|符合|超过|超過|低于|低於|大于|大於|小于|小於"
    r"|为|為|是|非|无|無|有值|为空|為空|不空|不为空|不為空"
    r"|包含|包括|属于|屬於|不在|在)"
)
#: 明显的"展示/显示"描述词——出现即说明这句在讲展示方式而不是条件（「固定显示」「也不需要展示」）
#: 但若同时出现引导词或比较谓词，则以语义门槛为准，不被这条否掉。
_CONDITION_NEGATIVE_RE = re.compile(
    r"^(?:固定|也不需要|不需要|无需|無需|不用|默认|默認|始终|始終|均|都)"
)
#: rev184：**兜底值显示**不是条件显示。「为空时显示"-"」「无值时展示"—」
#: 讲的是"取不到值时页面显示什么"，不是"什么条件下才显示该字段"。
#: 这是本 SUT 里最常见的一类描述（问卷 8 个只读字段全是这个句式）。
_CONDITION_FALLBACK_RE = re.compile(
    r"(?:为空|為空|无值|無值|不存在|未获取|未獲取|没[有獲获]|没有|无数据|無數據)"
    r"\s*(?:时|時|的时候|的時候)?\s*[，,]?\s*(?:则|則|就|统一|統一)?\s*"
    r"[“\"「『'‘]?\s*[-—–－/／]?\s*[”\"」』'’]?\s*$"
)


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
    digits: int | None = None
    decimal_raw = ""
    if match:
        digits = int(match.group(1))
        decimal_raw = match.group(0)
    else:
        # rev183：补「最多2位小数 / 最多保留2位小数 / 保留两位小数 / 小数位数：2」。
        # 只认到「位小数」为止，绝不把「最多输入20个字」之类当成精度约束。
        alt = _DECIMAL_ALT_RE.search(text)
        if alt:
            token = alt.group(1)
            digits = int(token) if token.isdigit() else _CN_NUM.get(token)
            decimal_raw = alt.group(0).strip()
    if digits is not None:
        out.append(FieldConstraint(kind="decimal_places", raw=decimal_raw,
                                   numbers=[float(digits)]))

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
    formats: list[str] = []
    formats_raw = ""
    if match:
        # 捕获段会跨到下一条编号（"…jpg、png4. 单个文件大小不超过2M"）——
        # 先按「文件大小」和「下一条编号」两处截断。注意编号正则不能带后顾断言：
        # 「png4.」的 4 前一个字符是字母，带 (?<![A-Za-z0-9]) 就切不开，编号会粘成 token。
        raw_formats = re.split(r"[0-9]+\s*[、.]?\s*文件大小", match.group(1))[0]
        raw_formats = re.split(r"\s*\d{1,2}\s*[.．、]\s*", raw_formats)[0]
        formats = [
            re.sub(r"^[0-9]+\s*[、.]?\s*", "", f).strip().lstrip(".")
            for f in re.split(r"[、,，/]", raw_formats)
            if f.strip()
        ]
        formats = [f for f in formats if f and not any(ch.isdigit() for ch in f)]
        formats_raw = "文件格式仅支持：" + "、".join(formats) if formats else ""
    if not formats:
        # rev183：其余声明形态（「支持jpg、png格式上传」「仅支持jpg、png格式」
        # 「上传格式支持：jpg，png」「支持的文件类型：jpg/png」…）。
        # 只在「扩展名白名单」命中时采信，避免把「格式要求：参照附件说明」当格式清单。
        alt = _FORMATS_ALT_RE.search(text)
        if alt:
            candidate = re.split(
                r"\s*\d{1,2}\s*[.．、]\s*", alt.group("exts") or ""
            )[0]
            formats = [
                f.strip().strip(".").lower()
                for f in re.split(r"[、,，/／|｜]", candidate)
                if f.strip()
            ]
            # 未登记的短串（"xml2"、"参照附件说明"）一律丢弃；全丢 = 不是格式声明
            formats = [f for f in formats if f in _KNOWN_EXTS]
            if formats:
                formats_raw = alt.group(0).strip()
    if formats and _format_list_is_placeholder(text, formats):
        # 「暂不支持任何格式」「不支持上传」之类：白名单里的词只是"被否定的那个例子"，
        # 不是可用格式清单。宁可判「无约束」，也不要拿它生成"上传 jpg 应成功"的假通过断言。
        formats, formats_raw = [], ""
    if formats:
        out.append(FieldConstraint(kind="formats", raw=formats_raw,
                                   detail={"formats": formats}))

    match = _OPTIONS_RE.search(text)
    if match:
        raw_options = re.split(
            r"默认选中|默认值|默认：|默認選中|默認值|默認為|默認", match.group(1)
        )[0]
        # 关键词本身可能落在捕获段开头（例："，支持選擇ETZ"）。
        # rev132：不再无条件剥掉前导「是」——「选项范围：是；否」的第一个取值就叫「是」，
        # 剥掉后枚举只剩「否」，断言会拿错误取值去校验。只有「是」后面跟空白才算系动词。
        raw_options = re.sub(r"^(?:支持選擇|支持选择|為|为)\s*", "", raw_options)
        raw_options = re.sub(r"^是(?=\s)", "", raw_options).strip()
        # 捕获段会跨到下一条编号（"…堂食3. 未选校验：请选择业务类型"）——先在下一条编号处截断，
        # 否则最后一个合法取值会因为带冒号被当成条款丢掉（实测「堂食」就是这样消失的）。
        # 标记前必须不是字母/数字：否则 "Active、Closed" 的「e、」会被当成列表项标记 a、
        raw_options = re.split(
            r"\s*(?<![A-Za-z0-9])(?:\d{1,2}|[a-z])\s*[.．、]\s*", raw_options
        )[0].strip()
        options = []
        # 全角「；」既是选项分隔符（"是；否"），也可能是条款分隔符（"…；校驗：必填"）。
        # 前者必须切开，后者靠下面的「含冒号即丢弃」挡住。
        for item in re.split(r"[、,，/；;]", raw_options):
            text_item = item.strip().strip("。；;")
            if not text_item:
                continue
            if re.fullmatch(r"\d+\s*(?:组|組|个|個|条|條)", text_item):
                continue  # 数量上限不是枚举取值
            # 尾随的下一条编号（"否3." 来自 "选项范围：是；否3. 默认选中"）不是取值
            text_item = re.sub(r"\s*\d{1,2}\s*[.．、]\s*$", "", text_item).strip()
            if not text_item:
                continue
            # 冒号 = 又一条属性声明（"校驗：必填"），不是枚举值
            if "：" in text_item or ":" in text_item:
                continue
            if len(text_item) > 30:
                continue  # 长句是条款正文，不是取值
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
        # 捕获会跨到下一条编号（"默认选中全部门店4. 切换为指定门店后需选择门店"）——
        # 在编号处截断，否则默认值带着半句规则进断言（rev132 实测）
        value = re.split(
            r"\s*(?<![A-Za-z0-9])(?:\d{1,2}|[a-z])\s*[.．、]\s*", value
        )[0].strip()
        # "默認YL選項" 的尾缀"選項"不是值本身；但**不能**一并削掉尾字（
        # 实测把 "企业储值充值" 削成了 "企业储值充"）
        value = re.sub(r"(?:選項|选项)$", "", value).strip().strip("（）()")
        if value:
            out.append(FieldConstraint(kind="default", raw=match.group(0),
                                       detail={"value": value}))

    match = _READ_ONLY_RE.search(text)
    if match:
        out.append(FieldConstraint(kind="read_only", raw=match.group(0),
                                   detail={"state": "disabled"}))

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
    """抽取条件显示约束，例如"变动类型=企业储值充值 且 变更方式=批量导入"。

    三种形态（rev183 补齐第三种）：
    - 「X选择：值」式 → 归一成 ``X=值``（可多条件用「 且 」连接）；
    - 无条件值可抽（"排隊打開顯示該字段"）→ 直接用原文条件短语（去掉「當/時」等虚词），
      绝不因为"抽不出 X=值"就丢掉整条条件显示规则；
    - 「仅当…时（才）显示该字段」→ 前置语式。此前只认后置的「…顯示該字段」，
      「仅当」被当成普通两字前缀，条件短语长度不足或被逗号切断 ⇒ 整条判"无条件"，
      「条件显示校验」用例数为 0（真实文档里这正是最常见的一种写法）。

    rev184：补**语义门槛**。命中条件句式不等于真是条件——真实文档里
    "固定显示，包括支付宝小程序"、"为空时显示"-""、"也不需要展示路径"
    都会命中条件句式，但它们讲的是**展示方式/兜底值**，不是字段的条件显示。
    这类误判会直接生成假的「条件显示校验」用例（断言验的是不存在的开关）。
    """
    text = _strip_zero_width(description or "")
    match = _CONDITION_RE.search(text) or _CONDITION_GUARD_RE.search(text)
    if not match:
        return ""
    segment = _clean_condition_segment(match.group(0))
    # 语义门槛：去掉引导词后，条件段里必须还有「条件语义」（谓词/比较/引导词）。
    # 「固定显示」→ 削掉"显示"剩「固定」；「也不需要展示」→ 剩「也不需要」
    # → 两者都不含任何条件谓词，直接判无条件。
    probe = re.sub(r"^(?:[仅僅只][有當当]|[仅僅只][当當]|只有|[当當]|如果|若|一旦|除非)", "", segment).strip()
    # 兜底值显示（「…为空时显示"-"」）先排除：它连「条件谓词」都算不上，
    # 而且本 SUT 里大量出现（问卷只读字段全部是这个句式）——放过去会刷出 8 条假的条件显示用例。
    if _CONDITION_FALLBACK_RE.search(probe):
        return ""
    if _CONDITION_NEGATIVE_RE.match(probe) and not re.search(
        r"(?:选择|選擇|选中|選中|勾选|勾選|等于|等於|开启|開啟|打开|打開|[><=＝]|包含|满足|滿足|符合)",
        probe,
    ):
        return ""
    clauses: list[str] = []
    for term in _CONDITION_TERM_RE.finditer(segment):
        name = _CONNECTIVE_RE.sub("", term.group(1).strip()).strip()
        # 条件引导词/介词不属于字段名（"当变动类型选择：X" → "变动类型"；"订单类型" 保留）
        name = re.sub(r"^[當当仅僅只如果若]\s*", "", name).strip()
        # 「且变更方式选择」：连接词可能残留在字段名开头（上一个条件的尾巴），先剥
        name = re.sub(r"^(?:且|并且|并|以及|同时|和)", "", name).strip()
        # 动作词是分隔符不是字段名，但**只剥最后一个**：
        # 「变动类型选择」→「变动类型」；「变更方式选择」→「变更方式」；
        # 「订单来源选择」→ 保持（"来源选择"本身就是字段名，剥了会变成另一个字段）。
        verb = re.search(r"(?:选择|選擇|选中|選中)$", name)
        if verb:
            head = name[: verb.start()].strip()
            if len(head) >= 3 and not re.search(r"(?:来源|來源)$", head):
                name = head
        # 只剩引导词（"仅当选择外卖" 里没有字段名）→ 不出 X=值，走原文短语
        name = re.sub(r"^[有是]\s*", "", name).strip() if len(name) <= 3 else name
        value = _CONNECTIVE_RE.sub("", term.group(2).strip()).strip().rstrip("）)")
        value = re.sub(r"[，,]?$", "", value).strip()
        # 条件值尾部的虚词不进断言（"选择外卖时" → "外卖"）
        value = re.sub(r"(?:的)?(?:时候|时|時)$", "", value).strip()
        if len(name) >= 2 and value and len(name) <= 20 and len(value) <= 30:
            clauses.append(f"{name}={value}")
    if clauses:
        condition = " 且 ".join(clauses)
        return re.sub(r"(?:\s*且\s*){2,}", " 且 ", condition)
    return _condition_phrase(segment)


#: 条件段尾部：谓语与时态虚词（「…时显示该字段」「…时才展示」「…则显示」）
_CONDITION_TAIL_RE = re.compile(
    r"\s*(?:"
    r"(?:的)?(?:[时時]候|[时時])[，,]?\s*(?:才|則|则)?\s*(?:顯示|显示|展示|可[见見])?"
    r"|(?:才|則|则)\s*(?:顯示|显示|展示)?"
    r"|(?:顯示該字段|显示该字段|顯示該欄位|显示该栏位|顯示|显示|展示)"
    r"|(?:才|則|则)"
    r")\s*$"
)


def _clean_condition_segment(raw: str) -> str:
    """把条件声明的整段先削成「只剩条件本身」。

    「仅当订单类型选择外卖时显示该字段」若不先削掉引导词/谓语，
    条件值会连成 "外卖时显示该字段"，直接进断言就会验错对象。
    """
    seg = _strip_zero_width(raw or "").strip()
    # 顺序要紧：「只有当…」必须先剥「只有」再剥「当」，否则剩个孤零零的「有」
    seg = re.sub(r"^(?:[仅僅只][有當当]|[仅僅只][当當]|[只][有]|[当當]|如果|若)\s*", "", seg).strip()
    seg = re.sub(r"^(?:[仅僅只][当當]|[当當])\s*", "", seg).strip()
    # 尾部谓语可能叠两层（"时才显示该字段"），循环削
    for _ in range(3):
        new = _CONDITION_TAIL_RE.sub("", seg).strip()
        if new == seg:
            break
        seg = new
    return seg.strip("，, ")


def _condition_phrase(raw: str) -> str:
    """把「排隊打開且排隊集成方選擇GULU、QRS」「當店鋪狀態開啟」归一成条件短语。"""
    phrase = _clean_condition_segment(raw)
    phrase = re.sub(r"且\s*[當当]", "且", phrase)
    # 只取最靠近「顯示該字段」的那一小句，避免把前文整段带进条件
    phrase = re.split(r"[，,；;]", phrase)[-1].strip()
    phrase = re.sub(r"[（(]+$", "", phrase).strip()
    phrase = re.sub(r"(?:的)?(?:時候|情况|情況)$", "", phrase).strip()
    return phrase[:60]


# ---------------------------------------------------------------------------
# 表格解析
# ---------------------------------------------------------------------------

_FIELD_HEADER_HINTS = ("字段", "字段说明")
#: 「是否可編輯」列（YL 主數據店鋪字段表：值=是/否/系統自動生成）。
#: 此前列位映射只取 name/type/required/desc，該列被靜默丟棄——編輯態用例失去文檔依據。
_EDITABLE_HINTS = ("是否可編輯", "是否可编辑", "可編輯", "可编辑")
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


def _looks_like_table_header(header: list[str]) -> bool:
    """表头健全性：真实表头单元格都是**短标签**。

    实测（YL 主數據 p3）：fitz 把列表字段表殘段 + 「列表排序規則」+ 篩選查詢區表
    + 導入規則條款整段粘進**一個單元格**，該行含「字段/校驗/必填」被 _is_field_table
    誤判成字段表表頭 → 後續真正的列表表續段（12 個列表列）被當字段表續行吞掉，
    列表列全丟、還多出 12 條無約束 field_rules。膠水表頭必須拒絕。
    """
    for cell in header:
        text = _normalize_cell(cell)
        if not text:
            continue
        if len(text) > 24 or any(p in text for p in "。；;"):
            return False
    return True


def _is_field_table(header: list[str]) -> bool:
    if not _looks_like_table_header(header):
        return False
    joined = " ".join(_normalize_cell(c) for c in header)
    # 典型字段表：同时出现「字段」与「说明/説明/描述/备注」
    if "字段" in joined or "欄位" in joined:
        if any(hint in joined for hint in _DESC_HINTS) or "必填" in joined:
            return True
    return all(hint in joined for hint in _FIELD_HEADER_HINTS)


#: 列表显示字段表的「名称列」表头（含繁简）。这类表列出**列表页展示欄位**，
#: 不是表单字段——列一旦进 field_rules，编译层按字段名绑表单定位器会 0 条可执行。
_LIST_NAME_HINTS = ("列表欄位", "列表栏位", "字段名稱", "字段名称",
                    "顯示欄位", "显示栏位", "顯示字段", "显示字段")


def _is_list_display_table(header: list[str]) -> bool:
    """判断表头是否为「列表顯示字段」类表格（列展示清單，非表单字段）。

    实测（YL 主數據）：店鋪表头「字段名稱|業務規則說明」、商品/支付「列表欄位|欄位說明」
    ——「說明」既非简体「说明」也非繁体「説明」，_DESC_HINTS 全部不命中，整张表被
    误判成模板表，且 template_tables 只存行数 → 店鋪 15 列/商品 7 列/支付 5 列全丢。
    判据刻意排除带 必填/類型/说明类词头的表：那些是表单字段说明表，仍走 field_rules。
    """
    joined = " ".join(_normalize_cell(c) for c in header)
    if not any(hint in joined for hint in _LIST_NAME_HINTS):
        return False
    if "必填" in joined:
        return False
    if not _looks_like_table_header(header):
        return False
    if any(hint in joined for hint in _TYPE_HINTS):
        return False
    # 表单说明列词（说明/説明/描述/校驗/匹配規則…）命中时按字段表优先，
    # 仍交回 _is_field_table 的既有通道，避免双重判定抢表
    if any(hint in joined for hint in _DESC_HINTS):
        return False
    return True


def _column_index(
    header: list[str],
    hints: tuple[str, ...],
    *,
    exclude: set[int] | None = None,
) -> int | None:
    """按词表定位列号，**从左到右**取第一个命中，并跳过已被占用的列。

    rev132（实测小杨生煎问卷 PDF 表7/9）：表头 `['题目类型','字段说明','说明']`，
    取名称列用「字段」→ 1（对），取说明列用「说明」→ **又命中 1「字段说明」**，
    真正承载约束正文的第 3 列从未被读取 ⇒ 题目区 14 条规则的 description
    退化成字段名本身 ⇒ 零约束 ⇒ 零用例（26/30 条规则卡在生成门槛之外）。
    因此这里允许 exclude 掉已被占用的列（名称/类型/必填），一列只解释一次。

    注意**不能**改成"精确命中优先"：YL 筛选表 `['字段','字段类型','字段校验','筛选规则']`
    里「筛选规则」是精确词、「字段校验」只是包含词，精确优先会跳过后一列、
    把说明列指到「筛选规则」，筛选规则的校验正文（≤30字符/默认为空）整段丢失。
    列序即优先级。
    """
    skip = exclude or set()
    for idx, cell in enumerate(header):
        if idx in skip:
            continue
        text = _normalize_cell(cell)
        if text and any(hint in text for hint in hints):
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


#: 续表对齐时「必填列」单元格的可信取值（含 YL 的「系統自動生成」——它出现在必填列
#: 是因為 PDF 列错位，语义上仍是"该列是必填/状态标记列"的证据）
_REQUIRED_CELL_VALUES = ("是", "否", "必填", "必選", "選填", "选填",
                         "系統自動生成", "系统自动生成", "Y", "N")


def _continuation_offset(
    rows: list[list[str]], header: list[str],
) -> int | None:
    """寬續表對齊：找到偏移量 o，使 rows 的 o..o+len(header)-1 列像是 header 的數據行。

    实测（YL 主數據 p2）：店鋪字段表被 PDF 評論列撐成 9 列（前導空列 + 5 字段列 +
    批注列），與 5 列表頭不等寬 → 整段 20 行續表被丟棄（詳細地址/精度/維度/電話等
    全部丟失）。按內容打分選偏移：字段名列像短標籤且必填列像必填標記的行數達標才算。
    """
    width = len(header)
    if not rows or len(rows[0]) < width:
        return None
    best_offset: int | None = None
    best_hits = 0
    for off in range(0, len(rows[0]) - width + 1):
        hits = 0
        for row in rows:
            cells = [_normalize_cell(c) for c in row]
            if len(cells) < off + width:
                continue
            name, _ftype, req = cells[off], cells[off + 1], cells[off + 2]
            if name and _looks_like_field_label(name) and req in _REQUIRED_CELL_VALUES:
                hits += 1
        if hits > best_hits:
            best_hits = hits
            best_offset = off
    # 至少 3 行對上（或全表一半以上）才認定為續表，避免誤劫持後續無關表格
    if best_offset is not None and best_hits >= max(3, (len(rows) + 1) // 2):
        return best_offset
    return None


def _raw_rows_from_table(
    rows: list[list[str]],
    header: list[str],
    table_index: int,
    *,
    start_row: int,
) -> list[dict[str, Any]]:
    """按表头列位把表格行映射成原始行（保留表号/行号用于追溯）。"""
    idx_name = _column_index(header, ("字段",))
    used: set[int] = set() if idx_name is None else {idx_name}
    idx_type = _column_index(header, _TYPE_HINTS, exclude=used)
    if idx_type is not None:
        used.add(idx_type)
    idx_required = _column_index(header, _REQUIRED_HINTS, exclude=used)
    if idx_required is not None:
        used.add(idx_required)
    idx_editable = _column_index(header, _EDITABLE_HINTS, exclude=used)
    if idx_editable is not None:
        used.add(idx_editable)
    # 说明列必须避开上面已占用的列：`['题目类型','字段说明','说明']` 场景下
    # 若与名称列同指第 2 列，规则正文就整段丢失（rev132 实测）
    idx_desc = _column_index(header, _DESC_HINTS, exclude=used)
    if idx_name is None:
        idx_name = 0
    if idx_desc is None:
        idx_desc = len(header) - 1
    if idx_desc == idx_name:
        # 仍撞在名称列 → 退最后一列；只剩一列时宁可留空也不复制字段名
        idx_desc = len(header) - 1 if len(header) - 1 != idx_name else None

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

        # 列错位纠正（rev111）：实测 YL p3「營業時間/選擇組件」行——名字列空、
        # 名字落在"字段類型"列、说明在说明列。若不做纠正，该行既不成规则，
        # 其说明还会被跨行合并挂到下一行（分组标题）上。
        if not cell(idx_name) and idx_type is not None and idx_type != idx_name:
            type_cell = cell(idx_type)
            if (_looks_like_field_label(type_cell) and len(type_cell) >= 2
                    and type_cell not in _REQUIRED_CELL_VALUES
                    and not any(hint in type_cell for hint in _TYPE_HINTS)):
                cells = list(cells)
                cells[idx_type] = ""
                cells[idx_name] = type_cell
                rebalanced = True

        name = cell(idx_name)

        out.append({
            "name": name,
            "type": cell(idx_type),
            "required": cell(idx_required),
            "editable": cell(idx_editable),
            "desc": cell(idx_desc),
            "table": table_index,
            "row": start_row + offset,
            "rebalanced": rebalanced,
        })
    return out


def _display_columns_from_rows(
    rows: list[list[str]],
    header: list[str],
    table_index: int,
    *,
    start_row: int,
) -> list[dict[str, Any]]:
    """「列表顯示字段」表的行 → 欄位清單（name + 可选说明，保留表号/行号追溯）。"""
    idx_name = _column_index(header, _LIST_NAME_HINTS)
    if idx_name is None:
        idx_name = 0
    idx_desc = len(header) - 1
    if idx_desc == idx_name:
        idx_desc = 1 if len(header) > 1 else idx_name
    out: list[dict[str, Any]] = []
    header_len = len(header)
    for offset, row in enumerate(rows):
        if not any(_normalize_cell(c) for c in row):
            continue
        cells = _rebalance_row([_normalize_cell(c) for c in row], header_len)
        name = cells[idx_name] if idx_name < len(cells) else ""
        # 与 field_rules 同一保守标准：长段落/列表项不当欄位名（防說明列串行）
        if not name or not _looks_like_field_label(name):
            continue
        desc = cells[idx_desc] if idx_desc < len(cells) and idx_desc != idx_name else ""
        out.append({"name": name, "desc": desc,
                    "source": {"table": table_index, "row": start_row + offset}})
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
                nxt["editable"] = nxt["editable"] or item.get("editable", "")
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
                merged[-1]["editable"] = merged[-1].get("editable", "") or item.get("editable", "")
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


def _looks_like_field_continuation(
    rows: list[list[str]],
    header: list[str],
    *,
    max_rows: int = 12,
) -> bool:
    """判断一张同宽的表是不是**上一张字段说明表**的续页（跨页表头只在首页）。

    rev132 实测（小杨生煎问卷 PDF p4-p5）：状态表 `['状态','说明','可操作按钮']` 与其后的
    `['待启用','初始创建完成的问卷状态即为待启用…','查看、编辑、启用、删除、导出明细']`
    宽度都是 3，被无脑当成字段表续行 ⇒ 产出「说明/状态」「已启用的问卷即为投入使用…/启用」
    这类**噪音规则**（永远过不了生成门槛，却污染规则总数与追溯矩阵）。

    判据：
    1. 首行本身带「按钮/按鈕」列 → 是状态×操作矩阵，不是字段表；
    2. 名称列的非空值里 ≥60% 长得像字段标签（短、无编号、非整句）才算续页；
       合并单元格留下的空名称不参与判定。
    """
    first = " ".join(_normalize_cell(c) for c in (rows[0] if rows else []))
    if "按钮" in first or "按鈕" in first:
        return False
    idx_name = _column_index(header, ("字段",))
    if idx_name is None:
        idx_name = 0
    checked = 0
    plausible = 0
    for row in rows[:max_rows]:
        cells = [_normalize_cell(c) for c in row]
        if idx_name >= len(cells):
            continue
        name = cells[idx_name]
        if not name:
            continue
        checked += 1
        # 字段名不会带逗号/顿号（那是整句说明被抽到名称列的特征）
        if _looks_like_field_label(name) and "，" not in name and "、" not in name:
            plausible += 1
    if checked == 0:
        return False
    return plausible / checked >= 0.6


def _split_tables(
    tables: list[list[list[str]]],
) -> tuple[list[FieldRule], list[dict[str, Any]], list[dict[str, Any]], int,
           list[dict[str, Any]]]:
    """把表格分成「字段说明表」/「列表顯示字段表」/「模板表」，并处理跨页续表（表头只在首页）。

    返回 `(field_rules, template_tables, rejected_fields, rebalanced_rows, display_columns)`；
    倒数第二个值是"需要重列化才恢复的行数"，用于审计 PDF 抽取质量；
    最后一个值是列表显示表的欄位清單（每表一项：table + columns[name/desc/source]）。
    """
    raw: list[dict[str, Any]] = []
    template_tables: list[dict[str, Any]] = []
    display_columns: list[dict[str, Any]] = []
    current_header: list[str] | None = None
    current_kind = ""

    for index, rows in enumerate(tables):
        if not rows:
            continue
        header = rows[0]
        if _is_field_table(header):
            current_header = header
            current_kind = "field"
            raw.extend(_raw_rows_from_table(rows[1:], header, index, start_row=2))
            continue
        if _is_list_display_table(header):
            current_header = header
            current_kind = "list"
            display_columns.append({
                "table": index,
                "columns": _display_columns_from_rows(rows[1:], header, index, start_row=2),
            })
            continue
        is_continuation = current_header is not None and len(header) == len(current_header)
        if is_continuation and current_kind == "field" and not _looks_like_field_continuation(
            rows, current_header
        ):
            # 同宽但主题不同的表（状态×可操作按钮矩阵等）不是字段表续页：
            # 强行当续行会产出永远过不了门槛的噪音规则（rev132 实测 3 条）
            is_continuation = False
        if is_continuation:
            # 续表：表头只在首页，续页直接是数据行
            if current_kind == "list":
                display_columns[-1]["columns"].extend(
                    _display_columns_from_rows(rows, current_header, index, start_row=1))
            else:
                raw.extend(_raw_rows_from_table(rows, current_header, index, start_row=1))
            continue
        if current_header is not None and current_kind == "field" and len(header) > len(current_header):
            # 宽续表：PDF 評論列把續表撐寬（实测 YL 9列 vs 表頭5列）→ 按內容對齊偏移後接入
            off = _continuation_offset(rows, current_header)
            if off is not None:
                sliced = [
                    (list(r) + [""] * (off + len(current_header)))[off:off + len(current_header)]
                    for r in rows
                ]
                raw.extend(_raw_rows_from_table(sliced, current_header, index, start_row=1))
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
        if not any(str(item.get(key) or "").strip()
                   for key in ("type", "required", "editable", "desc")):
            # rev111：只有名称、其余列全空 → 实测是**分组标题行**
            # （YL p3 的「排隊/控制該店鋪是否支持排隊，以及優先取票規則」「訂位/控制該店鋪
            # 是否支持訂位」「外賣/控制該店鋪是否支持跳轉到外賣頁」）：它们不是字段，
            # 此前被当成字段规则（零用例的噪音）。
            rejected_fields.append({
                "name": item["name"][:120],
                "reason": "疑似小节/分组标题行（只有名称、无类型无说明），不生成字段规则",
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
            # 「是否可編輯」列原文（无该列时为空串）；「否/系統自動生成」→ 生成层只讀用例的列證據
            editable=item.get("editable", ""),
            constraints=extract_constraints(item["desc"]),
            source={"table": item["table"], "row": item["row"],
                    "rebalanced": bool(item.get("rebalanced"))},
        ))
    rebalanced_rows = sum(1 for item in merged_rows if item.get("rebalanced"))
    return field_rules, template_tables, rejected_fields, rebalanced_rows, display_columns


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
        if (_starts_new_block(line) or re.match(r"^(操作[-－]|字段说明|字段說明|题目类型|題目類型)", line)
                or (_is_short_label(buf) and buf[-1] not in _SENTENCE_END)):
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


# ---------------------------------------------------------------------------
# 文档定位：证据文本 → 行号 → 最近标题 → 一级章节
# ---------------------------------------------------------------------------
#
# 用途（本轮质量问题收口）：把「这条用例/这条字段规则」钉回 PRD 的具体位置，
# 从而按**文档证据**判定它属于哪个一级章节、位于哪个子场景（字段说明 / 筛选查询区 …）。
# 纪律：定位不到就返回 ``None``，由调用方保留原模块——**绝不猜归属**。

#: 定位用的压缩口径：去掉空白与常见分隔标点（与生成侧 compact 口径一致）
_LOCATE_COMPACT_RE = re.compile(r"[\s\u200b\u200c\u200d\ufeff，。；、：:,.（）()]")
#: 带编号的标题行：捕获编号与标题正文
_LOCATE_HEADING_RE = re.compile(r"^\s*(\d+(?:[.．]\d+)*)\s*[.．、]?\s+(\S.*)$")


@dataclass(frozen=True)
class _DocumentIndex:
    """全文压缩串 + 字符→行号映射 + 标题清单（构建一次，供多次定位复用）。"""

    compact: str
    line_of: tuple[int, ...]
    #: (行号, 标题全文, 标题正文, 层级)；层级 = 编号里的点数（"1." → 0）
    headings: tuple[tuple[int, str, str, int], ...]
    #: 每页起始行号（0-based）；无分页信息时为空
    page_starts: tuple[int, ...]


@lru_cache(maxsize=8)
def _document_index(full_text: str, page_line_counts: tuple[int, ...]) -> _DocumentIndex:
    lines = (full_text or "").splitlines()
    compact_chars: list[str] = []
    line_of: list[int] = []
    headings: list[tuple[int, str, str, int]] = []
    page_starts: list[int] = []
    cursor = 0
    for page_index, count in enumerate(page_line_counts or ()):
        page_starts.append(cursor)
        cursor += count
    for line_no, raw in enumerate(lines):
        text = _strip_zero_width(raw).strip()
        if text:
            match = _LOCATE_HEADING_RE.match(text)
            if match and is_heading_line(text):
                numbering = match.group(1)
                headings.append((line_no, text, match.group(2).strip(), numbering.count(".")))
        for ch in _LOCATE_COMPACT_RE.sub("", raw):
            compact_chars.append(ch)
            line_of.append(line_no)
    return _DocumentIndex(
        compact="".join(compact_chars),
        line_of=tuple(line_of),
        headings=tuple(headings),
        page_starts=tuple(page_starts),
    )


def _page_line_range(index: _DocumentIndex, page: int | None) -> tuple[int, int] | None:
    """页码（1-based）→ 行号区间 ``[start, end)``；页码或分页信息缺失时返回 ``None``。"""
    if not page or page < 1 or not index.page_starts:
        return None
    if page > len(index.page_starts):
        return None
    start = index.page_starts[page - 1]
    end = (index.page_starts[page] if page < len(index.page_starts)
           else _total_lines(index))
    return start, end


def _total_lines(index: _DocumentIndex) -> int:
    """全文行数（用字符→行号映射的最后一个行号 + 1 推得，避免另存字段）。"""
    return (index.line_of[-1] + 1) if index.line_of else 0


def _nearest_headings(
    index: _DocumentIndex, line_no: int
) -> tuple[str, str, str]:
    """返回 ``(最近标题全文, 最近标题正文, 最近一级章节标题正文)``。"""
    heading_text = heading_title = section = ""
    for h_line, h_text, h_title, depth in index.headings:
        if h_line > line_no:
            break
        heading_text, heading_title = h_text, h_title
        if depth == 0:
            section = h_title
    return heading_text, heading_title, section


#: 定位时的候选前缀长度（由长到短；越长越不易误命中）
_LOCATE_PREFIX_LENGTHS = (120, 80, 60, 40, 24, 16, 12, 8)


def locate_evidence(
    full_text: str,
    evidence: str,
    *,
    page: int | None = None,
    page_line_counts: list[int] | None = None,
    min_chars: int = 4,
) -> dict[str, Any] | None:
    """把证据文本定位到文档行号与所属标题/一级章节（确定性、只读）。

    - 先按**最长可唯一命中**的前缀定位；无唯一前缀时退化为最长命中前缀的首次出现
      （与条款抽取的文档序一致）；
    - 给了 ``page`` 就先把搜索限制在该页（同名文本跨页重复时靠页号区分），
      页内找不到才放宽到全文并如实标注 ``scope``；
    - 定位不到返回 ``None``，由调用方保留原归属。

    返回 ``{line, heading, heading_title, section, matches, unique, scope}``。
    """
    key = _LOCATE_COMPACT_RE.sub("", evidence or "")
    if len(key) < min_chars:
        return None
    index = _document_index(full_text or "", tuple(page_line_counts or ()))
    if not index.compact:
        return None

    scope = "document"
    window: tuple[int, int] | None = _page_line_range(index, page)
    if window is not None:
        scope = "page"
    found: tuple[int, int] | None = None  # (命中次数, 起点字符下标)
    unique = False
    for scope_name, active in (("page", window), ("document", None)):
        if scope_name == "page" and active is None:
            continue
        for length in (*_LOCATE_PREFIX_LENGTHS, len(key)):
            if length < min_chars or length > len(key):
                continue
            prefix = key[:length]
            positions = []
            start = 0
            while True:
                hit = index.compact.find(prefix, start)
                if hit < 0:
                    break
                positions.append(hit)
                start = hit + 1
                if len(positions) > 50:
                    break
            if active is not None:
                lower, upper = active
                positions = [p for p in positions
                             if lower <= index.line_of[p] < upper]
            if not positions:
                continue
            if len(positions) == 1:
                found = (1, positions[0])
                unique = True
            elif found is None:
                found = (len(positions), positions[0])
            if unique:
                break
        if found is not None:
            scope = scope_name
            break
    if found is None:
        return None
    line_no = index.line_of[found[1]]
    heading_text, heading_title, section = _nearest_headings(index, line_no)
    return {
        "line": line_no,
        "heading": heading_text,
        "heading_title": heading_title,
        "section": section,
        "matches": found[0],
        "unique": unique,
        "scope": scope,
    }


def locate_table_locations(
    tables: list[list[list[str]]],
    pages: list[int | None],
    full_text: str,
    page_line_counts: list[int] | None = None,
) -> dict[int, dict[str, Any]]:
    """表号 → 文档位置：用表头行文本（退化用首行/最长单元格）定位该表所在子章节。

    表头是表自身的**原文**，比按字段名全文搜更可靠（同名表在多个章节重复时，
    用表所在页号区分）。定位不到的表不出现在结果里。
    """
    out: dict[int, dict[str, Any]] = {}
    for index, rows in enumerate(tables or []):
        if not rows:
            continue
        page = pages[index] if index < len(pages or []) else None
        candidates: list[str] = []
        header = " ".join(_normalize_cell(c) for c in rows[0] if _normalize_cell(c))
        if header:
            candidates.append(header)
        for row in rows[1:3]:
            joined = " ".join(_normalize_cell(c) for c in row if _normalize_cell(c))
            if joined:
                candidates.append(joined)
                break
        longest = max((_normalize_cell(c) for row in rows for c in row), key=len, default="")
        if longest:
            candidates.append(longest)
        for candidate in candidates:
            location = locate_evidence(
                full_text, candidate, page=page, page_line_counts=page_line_counts
            )
            if not location:
                continue
            out[index] = {"page": page, **location}
            break
    return out


def _parse_docx(content: bytes) -> tuple[str, str, list[list[list[str]]]]:
    """解析 docx：**按正文顺序**拍平段落与表格（表格不再一律堆到文末）。

    顺序很重要：表 → 子章节的归属（``table_locations``）靠"表在正文中的位置"推算，
    早前把全部表格拼到全文末尾，会让每张表都被算到最后一个标题之下。
    """
    from docx import Document
    from docx.table import Table
    from docx.text.paragraph import Paragraph

    document = Document(io.BytesIO(content))
    paragraphs = [p.text for p in document.paragraphs if _strip_zero_width(p.text).strip()]
    tables: list[list[list[str]]] = []
    ordered: list[str] = []
    for block in document.element.body.iterchildren():
        tag = str(getattr(block, "tag", "")).rsplit("}", 1)[-1]
        if tag == "p":
            text = Paragraph(block, document).text
            if _strip_zero_width(text).strip():
                ordered.append(text)
        elif tag == "tbl":
            rows = [[cell.text for cell in row.cells] for row in Table(block, document).rows]
            if not rows:
                continue
            tables.append(rows)
            ordered.append("\n".join(
                "\t".join(_normalize_cell(c) for c in row) for row in rows))
    full_text = "\n".join(ordered)
    return full_text, "\n".join(paragraphs), tables


def _parse_pdf(
    content: bytes,
) -> tuple[str, str, list[list[list[str]]], list[int | None], list[int]]:
    import fitz

    doc = fitz.open(stream=content, filetype="pdf")
    page_texts: list[str] = []
    tables: list[list[list[str]]] = []
    table_pages: list[int | None] = []
    for page_no, page in enumerate(doc, start=1):
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
                table_pages.append(page_no)
    full_text = "\n".join(page_texts)
    page_line_counts = [len(text.splitlines()) for text in page_texts]
    return full_text, full_text, tables, table_pages, page_line_counts


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
    table_pages: list[int | None] = []
    page_line_counts: list[int] = []
    try:
        if suffix == ".docx" or "openxmlformats-officedocument.wordprocessingml" in normalized_type:
            source_type = "docx"
            full_text, narrative, tables = _parse_docx(content)
        elif suffix == ".pdf" or normalized_type == "application/pdf" or content[:4] == b"%PDF":
            source_type = "pdf"
            full_text, narrative, tables, table_pages, page_line_counts = _parse_pdf(content)
        else:
            full_text, narrative, tables = _parse_plain(content)
    except Exception:  # noqa: BLE001 —— 结构化失败降级为纯文本
        full_text, narrative, tables = _parse_plain(content)
        source_type = "text"
    if not table_pages:
        table_pages = [None] * len(tables)

    field_rules, template_tables, rejected_fields, rebalanced_rows, display_columns = _split_tables(tables)
    # 兜底（rev104 起**总是**执行，不再仅零规则时）：fitz 表格提取会被行内
    # 評論塊截斷（实测 YL 主數據：店鋪字段表中段 12 行被「評論（7）」打散丟失），
    # 用文本行的列间隙重建并**按字段名去重合并**——既兜零表，也补断表。
    rebuilt = _tables_from_text_lines(full_text)
    rebuilt_table_count = 0
    if rebuilt:
        extra_rules, extra_templates, extra_rejected, extra_rebalanced, extra_display = _split_tables(rebuilt)
        seen = {rule.name for rule in field_rules}
        added = [rule for rule in extra_rules if rule.name not in seen]
        if added:
            rebuilt_table_count = len(rebuilt)
            field_rules = field_rules + added
            template_tables = template_tables + extra_templates
            rejected_fields = rejected_fields + extra_rejected
            rebalanced_rows += extra_rebalanced
        # 列表显示表按**列名序列签名**去重：find_tables 与文本重建可能重复捕获同一张表
        seen_disp = {tuple(c["name"] for c in d["columns"]) for d in display_columns}
        for d in extra_display:
            sig = tuple(c["name"] for c in d["columns"])
            if sig and sig not in seen_disp:
                seen_disp.add(sig)
                display_columns.append(d)
    if source_type == "pdf":
        # PDF 的表格文本混在页文本里 → 从叙述文本中剔除，避免重复生成噪音用例
        # 只剔除已进入规则通道的表格；功能清单、流程和状态表仍需叙述承接。
        used_tables = {r.source.get("table") for r in field_rules}
        used_tables.update(d.get("table") for d in display_columns)
        narrative = _drop_table_lines(narrative, _table_text_blob(
            [rows for index, rows in enumerate(tables) if index in used_tables]))
        # 再把硬换行碎片合并成完整句子（否则一条需求会被算成 2~3 条，挤掉真规则）
        narrative = _merge_wrapped_lines(narrative)

    # 表/规则 → 文档位置（本轮质量收口）：用表头行文本定位表所在子章节，
    # 再把定位结果写回每条字段规则的 source（page / line / heading / section）。
    # 定位不到就不写——调用方据此保留原归属，**不猜**。
    table_locations = locate_table_locations(
        tables, table_pages, full_text, page_line_counts)
    for rule in field_rules:
        location = table_locations.get(rule.source.get("table"))
        if location is None:
            location = locate_evidence(
                full_text, rule.description or rule.name,
                page=rule.source.get("page"), page_line_counts=page_line_counts)
        if not location:
            continue
        rule.source.setdefault("page", location.get("page"))
        rule.source["line"] = location["line"]
        rule.source["heading"] = location["heading"]
        rule.source["heading_title"] = location["heading_title"]
        rule.source["section"] = location["section"]

    return StructuredRequirement(
        full_text=full_text,
        narrative_text=narrative,
        field_rules=field_rules,
        template_tables=template_tables,
        display_columns=display_columns,
        source_type=source_type,
        rejected_fields=rejected_fields,
        rebalanced_rows=rebalanced_rows,
        rebuilt_table_count=rebuilt_table_count,
        page_line_counts=page_line_counts,
        table_locations=table_locations,
    )


__all__ = [
    "FieldConstraint",
    "FieldRule",
    "StructuredRequirement",
    "extract_condition",
    "extract_constraints",
    "is_heading_line",
    "locate_evidence",
    "locate_table_locations",
    "parse_requirement_document",
]
