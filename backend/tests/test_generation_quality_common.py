"""真实需求暴露的共性误生成回归。"""
from app.api.v2.ai import (
    _assign_case_metadata, _extract_requirement_points, _extract_import_clauses,
    _is_filter_field_rule, _build_requirement_cases,
)
from app.services.requirement_structure import FieldRule, extract_constraints
from app.schemas.test_case import TestCaseCreate as CaseCreate


def test_document_title_and_character_fragments_are_not_requirements():
    text = "YL-主数据管理（已鎖定，勿改。改動聯繫蘇元）\n字符類型：大小寫字母、數字、漢字\n订单取消后必须隐藏评价入口"
    assert _extract_requirement_points(text, filename="YL-主数据管理.pdf") == ["订单取消后必须隐藏评价入口"]


def test_survey_rules_are_not_import_or_filter_cases():
    assert _extract_import_clauses("1. 必填，最多输入20个字\n2. 点击删除问卷") == []
    assert not _is_filter_field_rule(FieldRule(name="适用门店", description="选项范围：全部门店、指定门店"))
    assert _is_filter_field_rule(FieldRule(name="问卷标题", description="支持模糊查询"))


def test_rules_do_not_become_modules_and_unlinked_rules_are_not_tripled():
    """规则文本不得变成模块名；无归属的用例进"未归属（非业务模块）"分组。

    2026-10-14 收口变更：此前无归属用例的模块名取**文档名**（"问卷"），在模块分布里
    看起来像多了一个业务模块；现改为可自解释的非业务分组名（P1-3）。
    """
    from app.api.v2.ai import UNMAPPED_MODULE

    cases, _gaps, _skips = _build_requirement_cases(["订单取消后不显示评价入口"], "问卷.pdf")
    assert len(cases) == 1
    _assign_case_metadata(cases, "3. 用于内部区分问卷\n订单取消后不显示评价入口", "问卷.pdf", None)
    assert cases[0].module == UNMAPPED_MODULE
    assert cases[0].module != "问卷"
    assert "订单取消后不显示评价入口" in cases[0].test_case_steps[-1].result


def test_traditional_length_constraint_is_captured():
    constraint, = extract_constraints("小於等於30個字符；")
    assert constraint.kind == "max_length"
    assert constraint.numbers == [30]


def test_lettered_import_items_do_not_become_narrative_cases():
    text = "导入规则\n9. 多组数据必须完整\na. 电话1、区号1\nb. 搜索关键词1、搜索关键词2\n"
    assert not any("电话1" in p or "关键词1" in p for p in _extract_requirement_points(text))


def test_empty_and_placeholder_clauses_are_removed():
    assert _extract_requirement_points("1、xxx字段：不能超過50個字符\nb. 导出明细字段：") == []
    assert _extract_requirement_points("c. 同一模板问卷一天只触发一次订阅消息推送") == ["同一模板问卷一天只触发一次订阅消息推送"]


def test_count_is_not_an_enum():
    assert not any(c.kind == 'options' for c in extract_constraints('支持選擇7組，'))
