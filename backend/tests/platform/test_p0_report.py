# -*- coding: utf-8 -*-
"""P0-3 测试：统一报告模板。"""

import pytest

from app.test_platform.report_templates import (
    TestResult,
    TestResultCode,
    EvidenceRef,
    WriteBackRecord,
    ReportHeader,
    ReportSection,
    ReportFooter,
    ExecutionReport,
    build_execution_report,
    render_report_markdown,
    RESULT_CODE_MAP,
)


class TestTestResult:
    def test_enum_values(self):
        assert TestResult.PASSED.value == "通过"
        assert TestResult.PENDING_REVIEW.value == "待复核"
        assert TestResult.PENDING_CONFIRM.value == "待确认"
        assert TestResult.DEFECT.value == "缺陷"

    def test_result_code_map(self):
        assert RESULT_CODE_MAP[TestResult.PASSED] == TestResultCode.PASSED
        assert RESULT_CODE_MAP[TestResult.DEFECT] == TestResultCode.DEFECT


class TestEvidenceRef:
    def test_screenshot(self):
        e = EvidenceRef(evidence_type="screenshot", path="/a.png")
        d = e.to_dict()
        assert d["type"] == "screenshot"
        assert d["path"] == "/a.png"

    def test_api_response(self):
        e = EvidenceRef(evidence_type="api_response", content='{"code":0}')
        d = e.to_dict()
        assert d["content"] == '{"code":0}'


class TestWriteBackRecord:
    def test_basic(self):
        r = WriteBackRecord(
            case_id="TC-001",
            case_title="测试登录",
            result=TestResult.PASSED,
            duration_ms=1200,
            runner="web",
        )
        d = r.to_dict()
        assert d["case_id"] == "TC-001"
        assert d["result"] == "通过"
        assert d["result_code"] == "PASSED"
        assert d["runner"] == "web"

    def test_with_evidence(self):
        r = WriteBackRecord(
            case_id="TC-002",
            case_title="测试保存",
            result=TestResult.DEFECT,
            evidence=[EvidenceRef("screenshot", "/err.png")],
            error_message="保存失败",
        )
        d = r.to_dict()
        assert d["error_message"] == "保存失败"
        assert len(d["evidence"]) == 1


class TestExecutionReport:
    def test_summary_all_passed(self):
        report = ExecutionReport(
            header=ReportHeader(project="PROJ-1", report_type="API测试"),
            sections=[ReportSection(
                title="API",
                records=[
                    WriteBackRecord("TC-001", "T1", TestResult.PASSED),
                    WriteBackRecord("TC-002", "T2", TestResult.PASSED),
                ],
            )],
            footer=ReportFooter(conclusion="全部通过"),
        )
        s = report.summary()
        assert s["total"] == 2
        assert s["passed"] == 2
        assert s["pass_rate"] == 1.0

    def test_summary_with_defect(self):
        report = ExecutionReport(
            header=ReportHeader(project="PROJ-1", report_type="Web测试"),
            sections=[ReportSection(
                title="Web",
                records=[
                    WriteBackRecord("TC-001", "T1", TestResult.PASSED),
                    WriteBackRecord("TC-002", "T2", TestResult.DEFECT),
                    WriteBackRecord("TC-003", "T3", TestResult.PENDING_REVIEW),
                ],
            )],
            footer=ReportFooter(conclusion="有缺陷"),
        )
        s = report.summary()
        assert s["total"] == 3
        assert s["passed"] == 1
        assert s["defect"] == 1
        assert s["pending_review"] == 1

    def test_to_dict(self):
        report = ExecutionReport(
            header=ReportHeader(project="PROJ-1", report_type="测试", version="v1"),
            sections=[],
            footer=ReportFooter(conclusion="无记录"),
        )
        d = report.to_dict()
        assert d["header"]["project"] == "PROJ-1"
        assert d["header"]["version"] == "v1"


class TestBuildExecutionReport:
    def test_basic(self):
        report = build_execution_report(
            project="PROJ-1",
            report_type="API执行报告",
            run_results=[
                {"case_id": "TC-001", "case_title": "查询", "result": "通过", "runner": "api"},
                {"case_id": "TC-002", "case_title": "写入", "result": "通过", "runner": "api"},
            ],
        )
        assert report.header.project == "PROJ-1"
        assert report.header.report_type == "API执行报告"
        s = report.summary()
        assert s["total"] == 2
        assert s["passed"] == 2
        assert "全部通过" in report.footer.conclusion

    def test_web_and_api_mixed(self):
        report = build_execution_report(
            project="PROJ-1",
            report_type="双轨执行报告",
            run_results=[
                {"case_id": "TC-001", "case_title": "页面登录", "result": "通过", "runner": "web"},
                {"case_id": "TC-002", "case_title": "API查询", "result": "通过", "runner": "api"},
            ],
        )
        assert len(report.sections) == 2
        titles = [s.title for s in report.sections]
        assert "Web 执行结果" in titles
        assert "API 执行结果" in titles

    def test_infer_runner_from_evidence(self):
        report = build_execution_report(
            project="PROJ-1",
            report_type="测试",
            run_results=[
                {"case_id": "TC-001", "case_title": "截图测试", "result": "通过",
                 "evidence": [{"type": "screenshot", "path": "/a.png"}]},
            ],
        )
        s = report.sections[0]
        assert s.records[0].runner == "web"

    def test_auto_conclusion_with_defect(self):
        report = build_execution_report(
            project="PROJ-1",
            report_type="测试",
            run_results=[
                {"case_id": "TC-001", "case_title": "T1", "result": "通过"},
                {"case_id": "TC-002", "case_title": "T2", "result": "缺陷"},
            ],
        )
        assert "缺陷" in report.footer.conclusion

    def test_auto_conclusion_partial(self):
        report = build_execution_report(
            project="PROJ-1",
            report_type="测试",
            run_results=[
                {"case_id": "TC-001", "case_title": "T1", "result": "通过"},
                {"case_id": "TC-002", "case_title": "T2", "result": "待复核"},
            ],
        )
        assert "待复核" in report.footer.conclusion or "部分" in report.footer.conclusion

    def test_empty_results(self):
        report = build_execution_report(
            project="PROJ-1",
            report_type="测试",
            run_results=[],
        )
        assert report.footer.conclusion == "无执行记录"

    def test_custom_conclusion(self):
        report = build_execution_report(
            project="PROJ-1",
            report_type="测试",
            run_results=[{"case_id": "TC-001", "case_title": "T1", "result": "通过"}],
            conclusion="自定义结论",
        )
        assert report.footer.conclusion == "自定义结论"


class TestRenderReportMarkdown:
    def test_contains_header(self):
        report = build_execution_report(
            project="PROJ-1",
            report_type="API执行报告",
            run_results=[{"case_id": "TC-001", "case_title": "T1", "result": "通过", "runner": "api"}],
        )
        md = render_report_markdown(report)
        assert "# API执行报告" in md
        assert "PROJ-1" in md

    def test_contains_summary(self):
        report = build_execution_report(
            project="PROJ-1",
            report_type="测试",
            run_results=[
                {"case_id": "TC-001", "case_title": "T1", "result": "通过"},
                {"case_id": "TC-002", "case_title": "T2", "result": "缺陷"},
            ],
        )
        md = render_report_markdown(report)
        assert "执行摘要" in md
        assert "总计" in md
        assert "通过率" in md

    def test_contains_defect_icon(self):
        report = build_execution_report(
            project="PROJ-1",
            report_type="测试",
            run_results=[{"case_id": "TC-001", "case_title": "T1", "result": "缺陷"}],
        )
        md = render_report_markdown(report)
        assert "❌" in md

    def test_contains_passed_icon(self):
        report = build_execution_report(
            project="PROJ-1",
            report_type="测试",
            run_results=[{"case_id": "TC-001", "case_title": "T1", "result": "通过"}],
        )
        md = render_report_markdown(report)
        assert "✅" in md
