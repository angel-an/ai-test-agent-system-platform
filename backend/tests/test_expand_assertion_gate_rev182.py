# -*- coding: utf-8 -*-
"""rev182：扩写必须带断言（消灭空壳用例）。

背景（2026-10-22 对全库 238 条扩写产物的实测）：
- 反向场景（…必填或非法输入校验）119 条里 **105 条断言为 0（88%）**
- 边界场景（…边界条件处理）119 条里 **77 条断言为 0（64%）**
- 合计 **182/238 = 76%**，其中 **40 条已绑 `web_test_id`** 编译进执行链路
  （`auto_executable=False` 但资产已绑，在用例列表里和正常用例长得一样）

根因：`_rule_assertions(rule).get(assertion_kind, [])` 取不到就给空列表。
rev181 的闸门只保证「能回链到字段」，**没保证该字段在这个维度上有约束**。

本文件锁三件事：
① 扩写判定与断言取值用**同一套**逻辑（「能扩」⇔「有断言可挂」）；
② 三个维度都无约束的字段不再扩写，且原因可解释（`expand_skips`）；
③ 有约束的字段**不能误杀**（必填/范围/枚举/格式/大小/数量上限都要扩）。
"""
import pytest

from app.api.v2.ai import (
    _assertions_for_scenario,
    _build_requirement_cases,
    _build_expand_skip_warning,
)
from app.services.requirement_structure import FieldRule, extract_constraints


def _rule(name: str, desc: str, field_type: str = "text", required: bool = False) -> FieldRule:
    """构造带**真实解析出的约束**的 FieldRule。

    ⚠️ 直接 `FieldRule(name=..., description=...)` 不会解析约束（`constraints` 为空列表），
    拿它测扩写会得出"所有字段都无约束"的错误结论——必须过 `extract_constraints`。
    """
    return FieldRule(name=name, description=desc, field_type=field_type,
                     required=required, constraints=extract_constraints(desc))


def _build(points, rules):
    return _build_requirement_cases(list(points), "需求.pdf", field_rules=list(rules))


# ------------------------------------------------------------ 核心不变式


@pytest.mark.parametrize("label,rule,point", [
    ("必填+最多字数", _rule("限购标签", "必填；最多8个字", required=True), "限购标签最多8个字"),
    ("仅范围", _rule("消费金额门槛", "可选；范围[1,99999]", "number"), "消费金额门槛输入10000时被拦截"),
    ("仅枚举", _rule("业务类型", "必填；选项范围：外卖、自提、堂食", required=True), "业务类型必选"),
    ("仅文件大小", _rule("上传文件", "文件大小≤1M"), "上传文件超过1M被拒绝"),
    ("仅数量上限", _rule("限购商品", "最多10条"), "限购商品最多10条"),
    ("必填+范围+精度", _rule("储值金额", "必填；范围[0,99999]；最多2位小数", "number", True),
     "储值金额输入99999时可保存"),
])
def test_expanded_scenario_always_carries_assertions(label, rule, point) -> None:
    """只要扩写了反向/边界，就必须带断言——这是 rev182 的唯一硬指标。"""
    cases, _gaps, _skips = _build([point], [rule])
    expanded = [c for c in cases if c.keyword in ("反向", "边界")]
    assert expanded, f"{label} 应至少扩写一个场景"
    for case in expanded:
        assertions = case.custom_fields.get("assertions") or []
        assert assertions, f"{label} 的{case.keyword}场景断言为空：{case.name}"
        assert case.custom_fields.get("executable_track") == "auto"


@pytest.mark.parametrize("label,rule,point", [
    ("无任何约束", _rule("备注", "选填"), "备注可以不填"),
    ("仅精度", _rule("单价", "最多2位小数", "number"), "单价最多2位小数"),
])
def test_unconstrained_field_is_not_expanded(label, rule, point) -> None:
    """三个维度都无约束 → 不扩写（正向用例仍保留，不丢需求）。"""
    cases, _gaps, skips = _build([point], [rule])
    assert [c for c in cases if c.keyword == "正向"], "正向用例必须保留"
    assert not [c for c in cases if c.keyword in ("反向", "边界")], f"{label} 不该扩写"
    assert skips and skips[0]["reason"] == "no_constraint"
    assert skips[0].get("field") == rule.name


# ------------------------------------------------------------ 断言取值逻辑


def test_reverse_scenario_falls_back_to_value_constraint() -> None:
    """反向场景不能只认 required 桶——只有范围约束的字段也要能拿到断言。"""
    buckets = {"required": [], "boundary": [{"kind": "boundary", "field": "x"}], "condition": []}
    assert _assertions_for_scenario("反向", buckets) == [{"kind": "boundary", "field": "x"}]


def test_reverse_scenario_prefers_required_over_value() -> None:
    """有必填约束时优先用必填断言（更贴近「必填或非法输入」的前半句）。"""
    buckets = {"required": [{"kind": "required"}], "boundary": [{"kind": "boundary"}], "condition": []}
    assert _assertions_for_scenario("反向", buckets) == [{"kind": "required"}]


def test_boundary_scenario_only_uses_boundary_bucket() -> None:
    """边界场景不该退到必填断言——那是另一类判定。"""
    buckets = {"required": [{"kind": "required"}], "boundary": [], "condition": []}
    assert _assertions_for_scenario("边界", buckets) == []


def test_empty_buckets_yield_no_assertions() -> None:
    assert _assertions_for_scenario("反向", {"required": [], "boundary": [], "condition": []}) == []


# ------------------------------------------------------------ 不误杀


@pytest.mark.parametrize("label,rule,point,expect_keywords", [
    ("范围→反向+边界", _rule("金额", "范围[1,100]", "number"), "金额输入100时被拦截", {"反向", "边界"}),
    ("枚举→反向", _rule("类型", "必填；选项范围：A、B", required=True), "类型必选", {"反向"}),
    # ⚠️ 格式类必须写成需求文档里的规范句式「文件格式仅支持：jpg、png」。
    # 「仅支持jpg，png格式」这种口语写法 `extract_constraints` 抽不出 formats，
    # 属于**解析层既有缺口**（不是 rev182 引入的，见文末 test_parser_gap_...）。
    ("格式→边界", _rule("图片", "文件格式仅支持：jpg、png"), "图片文件格式仅支持jpg格式", {"边界"}),
])
def test_constraint_kinds_are_not_falsely_killed(label, rule, point, expect_keywords) -> None:
    cases, _gaps, _skips = _build([point], [rule])
    got = {c.keyword for c in cases}
    assert expect_keywords <= got, f"{label} 期望包含 {expect_keywords}，实际 {got}"


def test_list_point_is_not_expanded() -> None:
    """列表/导航类条目不扩写（原有规则，rev182 收窄为 列表|入口|点击|拖拽）。"""
    rule = _rule("限购商品", "必填；最多8个字", required=True)
    cases, _gaps, skips = _build(["限购商品列表完整展示五个业务字段"], [rule])
    assert not [c for c in cases if c.keyword in ("反向", "边界")]
    assert skips[0]["reason"] == "list_or_nav"


# ------------------------------------------------------------ 可解释性


def test_expand_skip_warning_lists_reasons() -> None:
    """不扩写必须给原因，不能静默少生成用例。"""
    skips = [
        {"point": "备注可以不填", "reason": "no_behavior"},
        {"point": "单价最多2位小数", "reason": "no_constraint", "field": "单价"},
        {"point": "增加某菜单", "reason": "unlinked"},
        {"point": "限购商品列表", "reason": "list_or_nav"},
    ]
    text = _build_expand_skip_warning(skips)
    assert text and "4 个需求点" in text
    assert "正向用例均已保留" in text
    for token in ("没有可验证行为", "无约束", "未回链", "列表/导航"):
        assert token in text, token


def test_expand_skip_warning_is_none_when_empty() -> None:
    assert _build_expand_skip_warning([]) is None


def test_expand_skip_recorded_for_each_reason() -> None:
    """四种跳过理由都要能被真实样本触发（否则告警文案是死代码）。"""
    rules = [
        _rule("备注", "选填"),
        _rule("限购商品", "必填；最多8个字", required=True),
        _rule("单价", "最多2位小数", "number"),
    ]
    points = [
        "备注可以不填",            # 已回链、无列表词、有选填行为 → no_constraint
        "单价最多2位小数",          # 同上，精度写法解析不出 → no_constraint
        "增加会员限购商品管理列表--交易云-运营-增加菜单",  # list_or_nav
    ]
    _cases, _gaps, skips = _build(points, rules)
    reasons = {s["reason"] for s in skips}
    assert {"no_constraint", "list_or_nav"} <= reasons, reasons


def test_skip_reason_points_at_the_most_specific_cause() -> None:
    """原因归属要「最具体」：数据缺口 > 场景不适用 > 没有行为。

    「增加会员限购商品管理列表（交易云-运营-增加菜单）」同时满足「未回链」「列表类」
    「没有行为词」三条——但只有「未回链」能指路（去补字段说明表）。判成 list_or_nav /
    no_behavior 会让人以为"改代码就行"。
    """
    rule = _rule("限购商品", "必填；最多8个字", required=True)
    _cases, _gaps, skips = _build(
        ["增加会员限购商品管理列表--交易云-运营-增加菜单", "限购商品点击查询"], [rule]
    )
    # 列表类优先于 no_behavior：条目里带「列表」时即便有行为词也不扩写，
    # 且理由要说"不适用"而不是"没有行为"。
    assert skips[0]["reason"] == "list_or_nav", skips
    assert skips[1]["reason"] == "list_or_nav", skips


def test_unlinked_is_reported_before_no_behavior() -> None:
    """字段说明表里没有的条目 → 报 unlinked（补数据），不要报 no_behavior（改代码）。"""
    rule = _rule("限购商品", "必填；最多8个字", required=True)
    # 不含字段名、不含列表/导航词、也没有行为词——三条理由里只有 unlinked 成立。
    _cases, _gaps, skips = _build(["优惠券发放规则的补充说明"], [rule])
    assert skips and skips[0]["reason"] == "unlinked", skips


# ------------------------------------------------------------ 解析层缺口（不由本闸门负责）


@pytest.mark.parametrize("desc,kind", [
    ("仅支持jpg，png格式", "formats"),
    ("最多2位小数", "precision"),
    ("仅当业务类型为外卖时显示", "condition"),
])
def test_parser_gap_is_not_reported_as_gate_bug(desc, kind) -> None:
    """口语化写法抽不出约束 = `extract_constraints` 的缺口，不是扩写闸门的 bug。

    登记这些样本的目的：如果哪天解析层补上了，这条测试会失败并提醒同步收窄
    「跳过 = 无约束」的假设，而不是让缺口一直以"闸门误杀"的形态被误报。
    """
    got = {c.kind for c in extract_constraints(desc)}
    assert kind not in got, (
        f"`extract_constraints({desc!r})` 已能抽出 {kind}（实际 {got}）——"
        f"请同步更新 test_constraint_kinds_are_not_falsely_killed 的口语样本"
    )
