# -*- coding: utf-8 -*-
"""P1-4 测试：生成质量报告。"""

import pytest

from app.platform.quality_report import (
    compute_quality_metrics,
    mark_pending_items,
    GenerationQualityReport,
    build_generation_quality_report,
)


class TestComputeQualityMetrics:
    def test_empty_cases(self):
        m = compute_quality_metrics([])
        assert m["total_cases"] == 0
        assert m["result_completeness_rate"] == 0.0

    def test_basic_distribution(self):
        cases = [
            {"id": "TC-001", "keyword": "正向", "steps": [{"step": "A", "result": "B"}], "executable_flag": True},
            {"id": "TC-002", "keyword": "反向", "steps": [{"step": "C", "result": "D"}], "executable_flag": True},
            {"id": "TC-003", "keyword": "边界", "steps": [{"step": "E", "result": "F"}], "executable_flag": False},
        ]
        m = compute_quality_metrics(cases)
        assert m["total_cases"] == 3
        assert m["keyword_distribution"]["正向"] == 1
        assert m["keyword_distribution"]["反向"] == 1
        assert m["keyword_distribution"]["边界"] == 1
        assert m["result_completeness_rate"] == 1.0
        assert m["executable_rate"] == pytest.approx(2 / 3, abs=0.01)

    def test_fuzzy_word_detection(self):
        cases = [
            {"id": "TC-001", "keyword": "正向", "steps": [{"step": "A", "result": "成功"}]},
        ]
        m = compute_quality_metrics(cases)
        assert m["fuzzy_word_rate"] == 1.0
        assert m["result_completeness_rate"] == 0.0  # 模糊词导致 normalize_steps 失败

    def test_coverage_dimensions(self):
        cases = [
            {"id": "TC-001", "keyword": "正向", "remarks": "DB检查 并发测试", "steps": [{"step": "A", "result": "B"}]},
            {"id": "TC-002", "keyword": "反向", "remarks": "安全测试", "steps": [{"step": "C", "result": "D"}]},
        ]
        m = compute_quality_metrics(cases)
        assert m["coverage_dimensions"]["db"] is True
        assert m["coverage_dimensions"]["concurrent"] is True
        assert m["coverage_dimensions"]["security"] is True

    def test_missing_dimensions(self):
        cases = [
            {"id": "TC-001", "keyword": "正向", "steps": [{"step": "A", "result": "B"}]},
        ]
        m = compute_quality_metrics(cases)
        assert m["coverage_dimensions"]["db"] is False
        assert m["coverage_dimensions"]["concurrent"] is False
        assert m["coverage_dimensions"]["security"] is False

    def test_module_distribution(self):
        cases = [
            {"id": "TC-001", "module": "登录", "steps": [{"step": "A", "result": "B"}]},
            {"id": "TC-002", "module": "登录", "steps": [{"step": "C", "result": "D"}]},
            {"id": "TC-003", "module": "支付", "steps": [{"step": "E", "result": "F"}]},
        ]
        m = compute_quality_metrics(cases)
        assert m["module_distribution"]["登录"] == 2
        assert m["module_distribution"]["支付"] == 1


class TestMarkPendingItems:
    def test_passed(self):
        cases = [
            {"id": "TC-001", "steps": [{"step": "A", "result": "B"}], "executable_flag": True},
        ]
        result = mark_pending_items(cases)
        assert result[0]["_status"] == "通过"

    def test_pending_review_fuzzy(self):
        cases = [
            {"id": "TC-001", "steps": [{"step": "A", "result": "成功"}]},
        ]
        result = mark_pending_items(cases)
        assert result[0]["_status"] == "待复核"
        assert "模糊" in result[0]["_status_reason"]

    def test_pending_confirm_not_executable(self):
        cases = [
            {"id": "TC-001", "steps": [{"step": "A", "result": "B"}]},
        ]
        result = mark_pending_items(cases)
        assert result[0]["_status"] == "待确认"
        assert "可执行" in result[0]["_status_reason"]

    def test_defect_missing_result(self):
        cases = [
            {"id": "TC-001", "steps": [{"step": "仅步骤"}]},
        ]
        result = mark_pending_items(cases)
        assert result[0]["_status"] == "缺陷"


class TestGenerationQualityReport:
    def test_to_dict(self):
        report = GenerationQualityReport(
            project="PROJ-1",
            version="v1",
            metrics={"total_cases": 10},
            pending_items=[{"_status": "通过"}, {"_status": "待复核"}],
            gaps=["缺口1"],
        )
        d = report.to_dict()
        assert d["project"] == "PROJ-1"
        assert d["pending_summary"]["total"] == 2
        assert d["pending_summary"]["passed"] == 1
        assert d["pending_summary"]["pending_review"] == 1

    def test_to_markdown(self):
        report = GenerationQualityReport(
            project="PROJ-1",
            version="v1",
            metrics={
                "total_cases": 5,
                "result_completeness_rate": 1.0,
                "fuzzy_word_rate": 0.0,
                "executable_rate": 0.8,
                "keyword_ratios": {"正向": 0.4, "反向": 0.4, "边界": 0.2},
                "coverage_dimensions": {"db": True, "concurrent": False, "security": True},
            },
            pending_items=[{"_status": "通过"}],
            gaps=[],
        )
        md = report.to_markdown()
        assert "PROJ-1" in md
        assert "质量指标" in md
        assert "覆盖维度" in md


class TestBuildGenerationQualityReport:
    def test_complete(self):
        cases = [
            {"id": "TC-001", "title": "登录", "keyword": "正向", "module": "认证",
             "steps": [{"step": "A", "result": "B"}], "executable_flag": True, "remarks": "DB检查"},
            {"id": "TC-002", "title": "错误密码", "keyword": "反向", "module": "认证",
             "steps": [{"step": "C", "result": "D"}], "executable_flag": True, "remarks": "并发测试"},
            {"id": "TC-003", "title": "超长密码", "keyword": "边界", "module": "认证",
             "steps": [{"step": "E", "result": "F"}], "executable_flag": True, "remarks": "安全测试"},
        ]
        report = build_generation_quality_report("PROJ-1", "v1", cases)
        assert report.project == "PROJ-1"
        assert report.version == "v1"
        assert report.metrics["total_cases"] == 3
        # 没有缺口
        assert len(report.gaps) == 0

    def test_with_gaps(self):
        cases = [
            {"id": "TC-001", "title": "登录", "keyword": "正向",
             "steps": [{"step": "A", "result": "成功"}]},  # 模糊词
        ]
        report = build_generation_quality_report("PROJ-1", "v1", cases)
        # 模糊词占比 100% > 10%
        assert any("模糊" in g for g in report.gaps)
        # result 完整率 0% < 100%
        assert any("完整率" in g for g in report.gaps)
