"""PRD 章节模块归属与生成元数据回归。"""
from app.api.v2.ai import _assign_case_metadata
from app.schemas.test_case import TestCaseCreate


def test_prd_sections_preserve_previous_module_and_distinguish_import_task():
    text = ("1. 门店主数据\n门店编号不可重复，导入失败。\n"
            "2. 导入任务\n任务编号支持精确搜索。\n"
            "3. 商品主数据\n商品编号唯一。\n"
            "4. 支付方式主数据\n支付编号唯一。\n")
    cases = [TestCaseCreate(name=field, module="旧模块", custom_fields={
        "requirement_rule": {"field": field},
    }) for field in ("门店编号", "任务编号", "商品编号", "支付编号")]
    _assign_case_metadata(cases, text, "需求.pdf", None)
    assert [c.module for c in cases] == ["门店主数据", "导入任务", "商品主数据", "支付方式主数据"]
    assert all(c.custom_fields["original_module"] == "旧模块" for c in cases)
    assert all("AI生成" in c.tags for c in cases)


def test_ambiguous_and_companion_cases_keep_existing_module():
    """歧义用例保持原模块；通用建议走专用模块（本轮收口变更，见下）。

    2026-10-14 质量问题收口：通用建议（伴生场景）此前落在"文档名"兜底模块里，
    与有 PRD 归属证据的用例混在一起，模块分布看不出哪些是通用建议。
    现改为专用模块名 + 「通用建议非PRD条款」标签双重标识——**不强行归类**到业务大模块。
    """
    from app.api.v2.ai import GENERIC_SUGGESTION_MODULE

    text = "1. 门店主数据\n创建人不可为空。\n2. 商品主数据\n创建人不可为空。\n"
    cases = [
        TestCaseCreate(name="创建人", module="原模块", custom_fields={
            "requirement_rule": {"field": "创建人"}}),
        TestCaseCreate(name="伴生", custom_fields={"companion_template": True,
            "requirement_rule": {"field": "门店"}}),
    ]
    _assign_case_metadata(cases, text, "需求.pdf", None)
    assert cases[0].module == "原模块"
    assert cases[1].module == GENERIC_SUGGESTION_MODULE
    assert cases[1].custom_fields["module_source"] == "generic_suggestion"
    assert "通用建议非PRD条款" in cases[1].tags
    assert "通用伴生" in cases[1].tags


def test_supplement_tags_do_not_create_requirement_evidence():
    case = TestCaseCreate(name="补充", custom_fields={
        "manual_supplement": True, "source_evidence": "营业时间支持多组导入。"})
    _assign_case_metadata([case], "1. 门店主数据\n营业时间支持多组导入。", "需求.pdf", None)
    assert case.module == "门店主数据"
    assert "待确认" in case.tags
    assert "requirement_rule_id" not in case.custom_fields
