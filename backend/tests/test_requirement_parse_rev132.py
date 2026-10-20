"""rev132：需求解析三处断点的回归测试（依据小杨生煎问卷 PDF 实测）。

三个断点都是**静默失效**——不报错、不告警，只是规则没约束、用例不生成：
1. 说明列与名称列撞车：`['题目类型','字段说明','说明']` 里「说明」命中「字段说明」，
   规则正文退化成字段名 ⇒ 零约束 ⇒ 零用例；
2. 必填词表只有繁体「必選」：PDF 原文「多选，必选」被判非必填；
3. 枚举词表不认「可选项：」，且捕获在全角「；」处截断（「是；否」只剩「是」）。

另有两个同源问题（同一次实测暴露）：取值被下一条编号污染（「排除门店3.」）、
状态×可操作按钮矩阵被当成字段表续页产出噪音规则。
"""

from __future__ import annotations

from app.services.requirement_structure import (
    _column_index,
    _DESC_HINTS,
    _split_tables,
    extract_constraints,
    required_from_text,
)

QUESTION_HEADER = ["题目类型", "字段说明", "说明"]
FORM_HEADER = ["字段", "字段类型", "是否必填", "字段说明"]
FILTER_HEADER = ["字段", "字段类型", "字段校验", "筛选规则"]

QUESTION_ROW = [
    "全部(单选题/多选题/问答题/打分题)",
    "是否必答",
    "1. 必选，单选2. 选项范围：是；否3. 默认选中：是",
]


def _rules(tables):
    rules, _templates, _rejected, _rebalanced, _display = _split_tables(tables)
    return rules


class TestDescColumn:
    """断点 1：说明列不得与名称列同指一列。"""

    def test_desc_column_skips_name_column(self):
        header = QUESTION_HEADER
        # 名称列 = 第 2 列（「字段说明」），说明列必须落到第 3 列（「说明」）
        assert _column_index(header, ("字段",)) == 1
        assert _column_index(header, _DESC_HINTS, exclude={1}) == 2

    def test_rule_description_is_real_rule_text(self):
        rules = _rules([[QUESTION_HEADER, QUESTION_ROW]])
        assert [r.name for r in rules] == ["是否必答"]
        rule = rules[0]
        # 修复前 description == "是否必答"（= 字段名本身），一整个约束正文丢失
        assert rule.description == QUESTION_ROW[2]
        assert rule.description != rule.name

    def test_earlier_desc_column_still_wins(self):
        """列序即优先级：`字段校验` 在 `筛选规则` 之前，说明列应取前者。

        反例：改成"精确命中优先"会命中后一列「筛选规则」，
        筛选规则的校验正文（≤30字符/默认为空）整段丢失。
        """
        row = ["店鋪編號", "輸入框", "小於等於30個字符；默認為空", "精準搜索"]
        rules = _rules([[FILTER_HEADER, row]])
        assert rules[0].description == "小於等於30個字符；默認為空"
        assert rules[0].constraint("max_length") is not None


class TestRequiredVocabulary:
    """断点 2：简体「必选」必须与繁体「必選」等价。"""

    def test_simplified_bixuan_is_required(self):
        assert required_from_text("1. 多选，必选") is True
        assert required_from_text("1. 多选，必選") is True
        assert required_from_text("1. 必填") is True

    def test_negative_forms_still_optional(self):
        for text in ("非必选，可选填", "非必選内容", "选填即可", "不需填写"):
            assert required_from_text(text) is False, text

    def test_required_flows_into_field_rule(self):
        rules = _rules([[QUESTION_HEADER, QUESTION_ROW]])
        assert rules[0].required is True


class TestOptionsVocabulary:
    """断点 3：枚举词表与分隔符。"""

    def test_ke_xuanxiang_wording(self):
        found = {c.kind: c for c in extract_constraints(
            "2. 可选项：外卖、自提、堂食3. 未选校验：请选择业务类型")}
        assert found["options"].detail["options"] == ["外卖", "自提", "堂食"]

    def test_fullwidth_semicolon_is_separator(self):
        found = {c.kind: c for c in extract_constraints("2. 选项范围：是；否3. 默认选中：是")}
        assert found["options"].detail["options"] == ["是", "否"]
        assert found["default"].detail["value"] == "是"

    def test_option_values_not_polluted_by_next_clause_numbering(self):
        found = {c.kind: c for c in extract_constraints(
            "2. 选项范围：全部门店、指定门店、排除门店3. 默认选中全部门店4. 切换为指定门店后需选择门店，可多选")}
        assert found["options"].detail["options"] == ["全部门店", "指定门店", "排除门店"]
        assert found["default"].detail["value"] == "全部门店"

    def test_latin_word_with_dunhao_is_not_a_list_marker(self):
        """`Active、Closed` 里的「e、」不是列表项标记 a、"""
        found = {c.kind: c for c in extract_constraints("枚举: Active、Closed")}
        assert found["options"].detail["options"] == ["Active", "Closed"]

    def test_clause_with_colon_is_not_an_option(self):
        found = {c.kind: c for c in extract_constraints("选项范围：A、B；校验：必填")}
        assert found["options"].detail["options"] == ["A", "B"]


class TestContinuationGuard:
    """同宽但不同主题的表不得被当成字段表续页。"""

    def test_status_matrix_not_absorbed(self):
        tables = [
            [FORM_HEADER, ["店铺编号", "输入框", "是", "小于等于30个字符"]],
            [["状态", "说明", "可操作按钮"]],
            [["待启用", "初始创建完成的问卷状态即为待启用，待启用状态下可编辑",
              "查看、编辑、启用、删除、导出明细"]],
            [["启用", "已启用的问卷即为投入使用，不可继续编辑",
              "查看、复制、禁用、删除、导出明细"]],
        ]
        names = [r.name for r in _rules(tables)]
        assert names == ["店铺编号"], names
        assert "说明" not in names

    def test_real_continuation_still_absorbed(self):
        """真正的跨页续页（名称列都是短标签）必须照常并入。"""
        tables = [
            [FORM_HEADER, ["店铺编号", "输入框", "是", "小于等于30个字符"]],
            [["店铺名称", "输入框", "是", "不超过50个字符"]],
        ]
        names = [r.name for r in _rules(tables)]
        assert names == ["店铺编号", "店铺名称"], names


class TestCaseEligibility:
    """生成门槛（必填 或 有枚举）收口到 FieldRule.case_eligible，与审计口径同源。"""

    def test_gate_matches_required_or_options(self):
        rules = _rules([
            [QUESTION_HEADER, QUESTION_ROW],
            [QUESTION_HEADER, ["", "操作-删除题目", "支持删除题目"]],
        ])
        by_name = {r.name: r for r in rules}
        assert by_name["是否必答"].case_eligible is True
        assert by_name["操作-删除题目"].case_eligible is False

    def test_summary_exposes_eligible_count(self):
        from app.services.requirement_structure import StructuredRequirement

        rules = _rules([
            [QUESTION_HEADER, QUESTION_ROW],
            [QUESTION_HEADER, ["", "操作-删除题目", "支持删除题目"]],
        ])
        structure = StructuredRequirement(
            full_text="", narrative_text="", field_rules=rules,
        )
        summary = structure.to_dict()
        assert summary["field_rule_count"] == 2
        assert summary["field_rule_case_eligible"] == 1
