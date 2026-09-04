# -*- coding: utf-8 -*-
"""rev59 测试用例生成质量门禁单元测试（纯函数，无需 DB/服务）。"""
import pytest

from app.agents.tools.testcase.quality_gates import (
    find_fuzzy,
    normalize_steps,
    strip_quoted,
    check_expected_results_fuzzy,
    validate_steps_payload,
)


class TestStripQuoted:
    def test_removes_quote_contents(self):
        assert "点击后页面" == strip_quoted("点击「绑定成功」后页面")
        assert "点击按钮" == strip_quoted('点击“保存”按钮')

    def test_keeps_unquoted(self):
        assert "点击保存" == strip_quoted("点击保存")


class TestFindFuzzy:
    def test_detects_plain_fuzzy(self):
        assert find_fuzzy("保存成功") == "保存成功"
        assert find_fuzzy("结果正确") == "正确"
        assert find_fuzzy("功能正常") == "功能正常"

    def test_strip_quotes_option(self):
        # 去引号后不误伤需求原文文案；不去引号时按词表顺序命中具体词（绑定成功）
        assert find_fuzzy("页面提示「绑定成功」", strip_quotes=True) is None
        assert find_fuzzy("页面提示「绑定成功」", strip_quotes=False) == "绑定成功"

    def test_clean_text(self):
        assert find_fuzzy("点击保存按钮", strip_quotes=True) is None


class TestNormalizeSteps:
    def test_db_style_ok(self):
        steps = [
            {"step": "进入消息模板管理页面", "result": "模板列表按渠道列展示 WhatsApp 选项"},
            {"step": "粘贴非法 JSON 后保存", "result": "页面出现『JSON格式错误,请检查模板内容』提示且模板未创建"},
        ]
        normalized, problems = normalize_steps(steps)
        assert problems == []
        assert normalized == [
            {"step": "进入消息模板管理页面", "result": "模板列表按渠道列展示 WhatsApp 选项"},
            {"step": "粘贴非法 JSON 后保存", "result": "页面出现『JSON格式错误,请检查模板内容』提示且模板未创建"},
        ]

    def test_missing_result_rejected(self):
        steps = [{"step": "点击保存"}]
        normalized, problems = normalize_steps(steps)
        assert normalized is None
        assert any("缺少预期结果" in p for p in problems)

    def test_vague_result_rejected(self):
        steps = [{"step": "点击保存", "result": "保存成功"}]
        normalized, problems = normalize_steps(steps)
        assert normalized is None
        assert any("模糊断言词「保存成功」" in p for p in problems)

    def test_vague_step_text_rejected(self):
        steps = [{"step": "点击保存成功", "result": "页面状态从草稿变为已发布"}]
        normalized, problems = normalize_steps(steps)
        assert normalized is None
        assert any("步骤文本含模糊断言词「保存成功」" in p for p in problems)

    def test_quoted_step_text_allowed(self):
        steps = [{"step": "点击「绑定成功」弹窗的关闭按钮", "result": "弹窗关闭且页面停留个人资料页"}]
        normalized, problems = normalize_steps(steps)
        assert problems == []
        assert normalized[0]["step"].startswith("点击「绑定成功」")

    def test_alias_keys_and_auto_split(self):
        steps = [
            {"操作": "输入非法 JSON", "预期结果": "展示 JSON格式错误 提示"},
            "输入合法号码 —— 预期结果：号码规范化后为 85211112222",
        ]
        normalized, problems = normalize_steps(steps)
        assert problems == []
        assert normalized[0] == {"step": "输入非法 JSON", "result": "展示 JSON格式错误 提示"}
        assert normalized[1]["step"] == "输入合法号码"
        assert normalized[1]["result"] == "号码规范化后为 85211112222"

    def test_empty_steps(self):
        normalized, problems = normalize_steps([])
        assert normalized is None
        assert problems


class TestExpectedFuzzy:
    def test_case_level_expected(self):
        assert check_expected_results_fuzzy("订单状态变更为已发货") is None
        assert check_expected_results_fuzzy("保存成功") == "保存成功"


class TestValidatePayload:
    def test_db_payload(self):
        ok, problems = validate_steps_payload(
            {"test_case_steps": [{"step": "执行 A", "result": "A 生效，状态=1"}]}
        )
        assert ok is not None and problems == []

    def test_excel_payload(self):
        ok, problems = validate_steps_payload({"steps": [{"step": "执行 B"}]})
        assert ok is None and problems


class TestFlattenExpectedResultsGuard:
    """对齐生产(_has_leading_number)：已带序号的条目不再重复加号。"""

    def test_no_double_numbering(self):
        from app.agents.tools.testcase.testcase_tools import _flatten_expected_results

        out = _flatten_expected_results(["1. 页面跳转微商城首页", "首页内容渲染正常"])
        assert out == "1. 页面跳转微商城首页\n2. 首页内容渲染正常"

    def test_already_numbered_items_kept(self):
        from app.agents.tools.testcase.testcase_tools import _flatten_expected_results

        out = _flatten_expected_results(["(1) 保存", "2) 列表出现记录"])
        assert out == "(1) 保存\n2) 列表出现记录"
