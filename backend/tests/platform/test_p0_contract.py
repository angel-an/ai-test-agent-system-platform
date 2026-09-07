# -*- coding: utf-8 -*-
"""P0-1 测试：统一生成契约校验。"""

import pytest

from app.test_platform.testcase_contract import (
    TestCaseContract,
    TestCaseStep,
    GenerationResult,
    TestCaseKeyword,
    TestCasePriority,
    TestCaseType,
    RiskLevel,
    validate_contract,
    validate_generation_result,
)


class TestTestCaseStep:
    def test_basic(self):
        s = TestCaseStep(step="点击保存按钮", result="页面提示保存成功")
        assert s.step == "点击保存按钮"
        assert s.result == "页面提示保存成功"
        assert s.data is None

    def test_with_data(self):
        s = TestCaseStep(step="输入账号", result="账号校验通过", data="demo_user_001")
        assert s.data == "demo_user_001"

    def test_to_dict(self):
        s = TestCaseStep(step="A", result="B")
        d = s.to_dict()
        assert d == {"step": "A", "result": "B"}

    def test_from_dict(self):
        s = TestCaseStep.from_dict({"step": "X", "result": "Y", "data": "Z"})
        assert s.step == "X"
        assert s.result == "Y"
        assert s.data == "Z"


class TestTestCaseContract:
    def test_basic(self):
        c = TestCaseContract(
            id="TC-001",
            title="测试登录功能",
            keyword=TestCaseKeyword.POSITIVE,
            priority=TestCasePriority.HIGH,
            steps=[TestCaseStep(step="输入账号密码", result="登录成功")],
        )
        assert c.id == "TC-001"
        assert c.keyword == TestCaseKeyword.POSITIVE
        d = c.to_dict()
        assert d["id"] == "TC-001"
        assert d["keyword"] == "正向"

    def test_from_dict(self):
        c = TestCaseContract.from_dict({
            "id": "TC-002",
            "title": "测试注册",
            "keyword": "正向",
            "priority": "high",
            "steps": [{"step": "填写表单", "result": "注册成功"}],
        })
        assert c.id == "TC-002"
        assert c.priority == TestCasePriority.HIGH
        assert len(c.steps) == 1

    def test_from_dict_string_steps(self):
        """兼容字符串步骤（自动拆分）"""
        c = TestCaseContract.from_dict({
            "id": "TC-003",
            "title": "测试",
            "steps": ["点击保存 —— 预期结果：保存成功"],
        })
        assert len(c.steps) == 1
        assert c.steps[0].step == "点击保存"
        assert c.steps[0].result == "保存成功"


class TestGenerationResult:
    def test_basic(self):
        r = GenerationResult(
            version="v1.0",
            cases=[TestCaseContract(id="TC-001", title="T1")],
        )
        assert r.version == "v1.0"
        assert len(r.cases) == 1


class TestNormalizeCaseFields:
    def test_identifier_to_id(self):
        from app.test_platform.testcase_contract import normalize_case_fields
        result = normalize_case_fields({"identifier": "TC-001", "name": "测试"})
        assert result["id"] == "TC-001"
        assert result["title"] == "测试"
        assert "identifier" not in result
        assert "name" not in result

    def test_preserves_canonical_fields(self):
        from app.test_platform.testcase_contract import normalize_case_fields
        result = normalize_case_fields({"id": "TC-001", "title": "已有标题", "name": "测试"})
        # title 已存在，name 不映射（避免覆盖）
        assert result["id"] == "TC-001"
        assert result["title"] == "已有标题"
        assert result["name"] == "测试"  # name 保留

    def test_real_project_alias(self):
        from app.test_platform.testcase_contract import normalize_case_fields
        # 模拟 PR-9 真实项目输出
        raw = {
            "identifier": "TC-437641",
            "name": "积分兑换正向",
            "module": "App-积分兑换",
            "keyword": "正向",
            "risk_level": "high",
        }
        result = normalize_case_fields(raw)
        assert result["id"] == "TC-437641"
        assert result["title"] == "积分兑换正向"


class TestValidateContract:
    def test_valid_case(self):
        valid, problems = validate_contract({
            "id": "TC-001",
            "title": "测试",
            "steps": [{"step": "A", "result": "B"}],
        })
        assert valid is True
        assert problems == []

    def test_missing_id(self):
        valid, problems = validate_contract({
            "title": "测试",
            "steps": [{"step": "A", "result": "B"}],
        })
        assert valid is False
        assert any("id" in p for p in problems)

    def test_missing_title(self):
        valid, problems = validate_contract({
            "id": "TC-001",
            "steps": [{"step": "A", "result": "B"}],
        })
        assert valid is False
        assert any("title" in p for p in problems)

    def test_missing_steps_and_expected(self):
        valid, problems = validate_contract({
            "id": "TC-001",
            "title": "测试",
        })
        assert valid is False
        assert any("步骤" in p or "预期" in p for p in problems)

    def test_invalid_keyword(self):
        valid, problems = validate_contract({
            "id": "TC-001",
            "title": "测试",
            "keyword": "无效关键词",
            "steps": [{"step": "A", "result": "B"}],
        })
        assert valid is False
        assert any("关键词" in p for p in problems)

    def test_step_missing_result(self):
        valid, problems = validate_contract({
            "id": "TC-001",
            "title": "测试",
            "steps": [{"step": "仅步骤"}],
        })
        assert valid is False
        assert any("缺少预期结果" in p for p in problems)


class TestValidateGenerationResult:
    def test_valid(self):
        valid, global_p, case_p = validate_generation_result({
            "version": "v1",
            "cases": [
                {"id": "TC-001", "title": "T1", "steps": [{"step": "A", "result": "B"}]},
            ],
        })
        assert valid is True
        assert global_p == []
        assert case_p == []

    def test_empty_cases(self):
        valid, global_p, case_p = validate_generation_result({
            "version": "v1",
            "cases": [],
        })
        assert valid is False
        assert any("空" in p for p in global_p)

    def test_missing_version(self):
        valid, global_p, _ = validate_generation_result({
            "cases": [{"id": "TC-001", "title": "T1", "steps": [{"step": "A", "result": "B"}]}],
        })
        assert valid is False
        assert any("版本" in p for p in global_p)

    def test_case_with_problems(self):
        valid, _, case_p = validate_generation_result({
            "version": "v1",
            "cases": [
                {"id": "", "title": "", "steps": []},
            ],
        })
        assert valid is False
        assert len(case_p) == 1
        assert case_p[0]["index"] == 1
