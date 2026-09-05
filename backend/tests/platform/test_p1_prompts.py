# -*- coding: utf-8 -*-
"""P1-1 测试：固化生成提示词。"""

import pytest

from app.platform.generation_prompts import (
    check_prompt_compliance,
    build_generation_prompt,
    format_step,
    format_kb_citation,
    STEP_FORMAT_TEMPLATE,
)


class TestCheckPromptCompliance:
    def test_valid_case(self):
        problems = check_prompt_compliance({
            "id": "TC-001",
            "title": "测试登录",
            "steps": [
                {"step": "输入账号密码", "result": "页面跳转首页"},
                {"step": "点击登录按钮", "result": "页面显示欢迎消息"},
            ],
        })
        assert problems == []

    def test_missing_steps(self):
        problems = check_prompt_compliance({
            "id": "TC-001",
            "title": "测试",
        })
        assert any("步骤" in p for p in problems)

    def test_step_missing_result(self):
        problems = check_prompt_compliance({
            "id": "TC-001",
            "title": "测试",
            "steps": [{"step": "仅步骤"}],
        })
        assert any("成对" in p or "缺少预期结果" in p for p in problems)

    def test_fuzzy_in_result(self):
        problems = check_prompt_compliance({
            "id": "TC-001",
            "title": "测试",
            "steps": [{"step": "点击保存", "result": "保存成功"}],
        })
        assert any("模糊" in p for p in problems)

    def test_mixed_expectation_in_step(self):
        problems = check_prompt_compliance({
            "id": "TC-001",
            "title": "测试",
            "steps": [{"step": "点击保存，预期结果：保存成功", "result": "xxx"}],
        })
        assert any("混写" in p for p in problems)

    def test_kb_citation_format(self):
        problems = check_prompt_compliance({
            "id": "TC-001",
            "title": "测试",
            "steps": [{"step": "A", "result": "B"}],
            "remarks": "知识库: xxx",
        })
        # 包含知识库标记但格式不正确
        assert any("格式" in p for p in problems)

    def test_kb_citation_correct_format(self):
        problems = check_prompt_compliance({
            "id": "TC-001",
            "title": "测试",
            "steps": [{"step": "A", "result": "B"}],
            "remarks": "来源《需求文档》第 3 节",
        })
        assert not any("格式" in p for p in problems)


class TestBuildGenerationPrompt:
    def test_contains_requirement(self):
        prompt = build_generation_prompt("测试登录功能")
        assert "测试登录功能" in prompt

    def test_contains_fuzzy_words(self):
        prompt = build_generation_prompt("测试")
        assert "模糊词禁止表" in prompt

    def test_contains_kb_docs(self):
        prompt = build_generation_prompt("测试", knowledge_base_docs=["需求文档", "设计文档"])
        assert "需求文档" in prompt
        assert "设计文档" in prompt


class TestFormatStep:
    def test_basic(self):
        s = format_step("点击保存", "页面提示保存成功")
        assert "点击保存" in s
        assert "预期结果" in s
        assert "页面提示保存成功" in s


class TestFormatKbCitation:
    def test_basic(self):
        s = format_kb_citation("需求文档", "3")
        assert "来源《需求文档》第 3 节" == s
