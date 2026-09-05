# -*- coding: utf-8 -*-
"""P1-2 测试：质量门禁扩展（重复场景、覆盖缺口）。"""

import pytest

from app.agents.tools.testcase.quality_gates import (
    check_duplicate_scenarios,
    check_coverage_gaps,
    QualityReport,
    run_quality_gates,
)


class TestCheckDuplicateScenarios:
    def test_no_duplicates(self):
        cases = [
            {"id": "TC-001", "title": "测试登录"},
            {"id": "TC-002", "title": "测试注册"},
            {"id": "TC-003", "title": "测试退出"},
        ]
        dups = check_duplicate_scenarios(cases)
        assert dups == []

    def test_detects_similar_titles(self):
        cases = [
            {"id": "TC-001", "title": "测试登录功能"},
            {"id": "TC-002", "title": "测试登录功能"},
        ]
        dups = check_duplicate_scenarios(cases)
        assert len(dups) == 1
        assert dups[0]["similarity"] == 1.0

    def test_detects_near_similar(self):
        cases = [
            {"id": "TC-001", "title": "测试登录功能正常流程"},
            {"id": "TC-002", "title": "测试登录功能正常流程验证"},
        ]
        dups = check_duplicate_scenarios(cases, threshold=0.8)
        assert len(dups) >= 1

    def test_ignores_different_titles(self):
        cases = [
            {"id": "TC-001", "title": "测试登录"},
            {"id": "TC-002", "title": "测试支付"},
        ]
        dups = check_duplicate_scenarios(cases)
        assert dups == []

    def test_empty_cases(self):
        assert check_duplicate_scenarios([]) == []


class TestCheckCoverageGaps:
    def test_complete_coverage(self):
        cases = [
            {"id": "TC-001", "keyword": "正向", "remarks": "DB检查"},
            {"id": "TC-002", "keyword": "反向", "remarks": "并发测试"},
            {"id": "TC-003", "keyword": "边界", "remarks": "安全测试"},
            {"id": "TC-004", "keyword": "正向"},
            {"id": "TC-005", "keyword": "反向"},
            {"id": "TC-006", "keyword": "边界"},
            {"id": "TC-007", "keyword": "反向"},
            {"id": "TC-008", "keyword": "边界"},
            {"id": "TC-009", "keyword": "正向"},
            {"id": "TC-010", "keyword": "反向"},
        ]
        gaps = check_coverage_gaps(cases)
        # 正向 3/10 = 30% <= 45%，反向 4/10 = 40% >= 30%，边界 3/10 = 30% >= 10%
        # DB/并发/安全都有
        assert gaps == []

    def test_too_many_positive(self):
        cases = [
            {"id": "TC-001", "keyword": "正向"},
            {"id": "TC-002", "keyword": "正向"},
            {"id": "TC-003", "keyword": "正向"},
            {"id": "TC-004", "keyword": "正向"},
            {"id": "TC-005", "keyword": "正向"},
            {"id": "TC-006", "keyword": "反向", "remarks": "DB"},
            {"id": "TC-007", "keyword": "边界", "remarks": "并发"},
            {"id": "TC-008", "keyword": "正向"},
            {"id": "TC-009", "keyword": "正向"},
            {"id": "TC-010", "keyword": "正向"},
        ]
        gaps = check_coverage_gaps(cases)
        assert any("正向" in g and "45%" in g for g in gaps)

    def test_missing_negative(self):
        cases = [
            {"id": "TC-001", "keyword": "正向", "remarks": "DB检查 并发测试 安全测试"},
            {"id": "TC-002", "keyword": "正向"},
            {"id": "TC-003", "keyword": "边界"},
            {"id": "TC-004", "keyword": "正向"},
        ]
        gaps = check_coverage_gaps(cases)
        assert any("反向" in g for g in gaps)

    def test_missing_db_concurrent_security(self):
        cases = [
            {"id": "TC-001", "keyword": "正向"},
            {"id": "TC-002", "keyword": "反向"},
            {"id": "TC-003", "keyword": "边界"},
        ]
        gaps = check_coverage_gaps(cases)
        assert any("DB" in g for g in gaps)
        assert any("并发" in g for g in gaps)
        assert any("安全" in g for g in gaps)

    def test_empty_cases(self):
        gaps = check_coverage_gaps([])
        assert any("空" in g for g in gaps)


class TestQualityReport:
    def test_passed_when_all_clear(self):
        report = QualityReport()
        assert report.passed is True

    def test_failed_when_structural_error(self):
        report = QualityReport()
        report.structural_errors.append("错误")
        assert report.passed is False

    def test_to_dict(self):
        report = QualityReport()
        report.total_cases = 10
        report.fuzzy_cases = [("TC-001", ["模糊"])]
        d = report.to_dict()
        assert d["total_cases"] == 10
        assert d["fuzzy_cases_count"] == 1


class TestRunQualityGates:
    def test_all_pass(self):
        cases = [
            {"id": "TC-001", "title": "测试登录", "keyword": "正向", "remarks": "DB检查",
             "steps": [{"step": "输入账号", "result": "页面显示输入框"}]},
            {"id": "TC-002", "title": "测试错误密码", "keyword": "反向", "remarks": "并发测试",
             "steps": [{"step": "输入错误密码", "result": "提示密码错误"}]},
            {"id": "TC-003", "title": "测试边界", "keyword": "边界", "remarks": "安全测试",
             "steps": [{"step": "输入超长密码", "result": "截断至最大长度"}]},
        ]
        report = run_quality_gates(cases)
        assert report.passed is True

    def test_detects_fuzzy(self):
        cases = [
            {"id": "TC-001", "title": "测试", "keyword": "正向",
             "steps": [{"step": "点击保存", "result": "保存成功"}]},
        ]
        report = run_quality_gates(cases)
        assert report.passed is False
        assert len(report.fuzzy_cases) == 1

    def test_detects_duplicate(self):
        cases = [
            {"id": "TC-001", "title": "测试登录功能", "keyword": "正向",
             "steps": [{"step": "A", "result": "B"}]},
            {"id": "TC-002", "title": "测试登录功能", "keyword": "正向",
             "steps": [{"step": "A", "result": "B"}]},
        ]
        report = run_quality_gates(cases)
        assert len(report.duplicates) == 1

    def test_detects_coverage_gap(self):
        cases = [
            {"id": "TC-001", "title": "测试", "keyword": "正向",
             "steps": [{"step": "A", "result": "B"}]},
        ]
        report = run_quality_gates(cases)
        assert len(report.coverage_gaps) > 0
