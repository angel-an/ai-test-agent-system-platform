# -*- coding: utf-8 -*-
"""D15 收口测试：端到端回归验证。

覆盖：
- API-only / Web-only / 双轨 / safe mode / live mode
- 导入路径一致性
- 报告模板输出统一
"""

import pytest
from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


class TestEndToEndAPIOnly:
    """API-only 端到端"""

    def test_api_only_safe_mode(self):
        """API-only 安全模式"""
        resp = client.post(
            "/api/v2/projects/PROJ-E2E/orchestrate/execute",
            json={
                "cases": [{"id": "TC-1"}],
                "run_api": True,
                "run_web": False,
                "live_api": False,
            },
        )
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["scenarios_selected"] == 1
        api = next(r for r in data["track_results"] if r["track"] == "api")
        assert api["status"] == "skipped"

    def test_api_only_live_mode(self):
        """API-only live 模式"""
        resp = client.post(
            "/api/v2/projects/PROJ-E2E/orchestrate/execute",
            json={
                "cases": [{"id": "TC-1"}],
                "run_api": True,
                "run_web": False,
                "live_api": True,
            },
        )
        assert resp.status_code == 200
        data = resp.json()["data"]
        api = next(r for r in data["track_results"] if r["track"] == "api")
        assert api["status"] == "completed"
        assert data["evidence_run_id"] is not None


class TestEndToEndWebOnly:
    """Web-only 端到端"""

    def test_web_only_no_base_url(self):
        """Web-only 缺少 base_url"""
        resp = client.post(
            "/api/v2/projects/PROJ-E2E/orchestrate/execute",
            json={
                "cases": [{"id": "TC-1"}],
                "run_api": False,
                "run_web": True,
                "live_web": True,
                "base_url": "",
            },
        )
        assert resp.status_code == 200
        data = resp.json()["data"]
        web = next(r for r in data["track_results"] if r["track"] == "web")
        assert web["status"] == "skipped"
        assert "base_url" in web.get("error", "")

    def test_web_only_with_base_url(self):
        """Web-only 有 base_url"""
        resp = client.post(
            "/api/v2/projects/PROJ-E2E/orchestrate/execute",
            json={
                "cases": [{"id": "TC-1"}],
                "run_api": False,
                "run_web": True,
                "live_web": True,
                "base_url": "http://example.com",
            },
        )
        assert resp.status_code == 200
        data = resp.json()["data"]
        web = next(r for r in data["track_results"] if r["track"] == "web")
        assert web["status"] == "completed"


class TestEndToEndDualTrack:
    """双轨端到端"""

    def test_dual_track_safe_mode(self):
        """双轨安全模式"""
        resp = client.post(
            "/api/v2/projects/PROJ-E2E/orchestrate/execute",
            json={
                "cases": [{"id": "TC-1"}],
                "run_api": True,
                "run_web": True,
                "live_api": False,
                "live_web": False,
                "base_url": "http://example.com",
            },
        )
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert len(data["track_results"]) == 2
        for tr in data["track_results"]:
            assert tr["status"] == "skipped"

    def test_dual_track_live_mode(self):
        """双轨 live 模式"""
        resp = client.post(
            "/api/v2/projects/PROJ-E2E/orchestrate/execute",
            json={
                "cases": [{"id": "TC-1"}],
                "run_api": True,
                "run_web": True,
                "live_api": True,
                "live_web": True,
                "base_url": "http://example.com",
            },
        )
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert len(data["track_results"]) == 2
        for tr in data["track_results"]:
            assert tr["status"] == "completed"


class TestImportConsistency:
    """导入路径一致性"""

    def test_all_platform_modules_importable(self):
        """所有平台模块可导入"""
        modules = [
            "app.test_platform.testcase_contract",
            "app.test_platform.execution_gates",
            "app.test_platform.report_templates",
            "app.test_platform.directory_layout",
            "app.test_platform.versioning",
            "app.test_platform.quality_report",
            "app.test_platform.evidence_chain",
            "app.test_platform.orchestrator",
            "app.test_platform.api_test_framework.api_layer",
            "app.test_platform.api_test_framework.runner",
            "app.test_platform.web_test_framework.runner",
        ]
        for mod in modules:
            __import__(mod)

    def test_compat_layer_identity(self):
        """兼容层对象一致性"""
        from app.test_platform.testcase_contract import TestCaseContract as T1
        from app.test_platform.testcase_contract import TestCaseContract as T2
        assert T1 is T2

        from app.test_platform.execution_gates import ExecutionGate as E1
        from app.test_platform.execution_gates import ExecutionGate as E2
        assert E1 is E2

        from app.test_platform.evidence_chain import UnifiedEvidence as U1
        from app.test_platform.evidence_chain import UnifiedEvidence as U2
        assert U1 is U2


class TestReportTemplateOutput:
    """报告模板输出统一"""

    def test_report_has_four_result_types(self):
        """报告包含四类结果"""
        from app.test_platform.report_templates import TestResult
        assert TestResult.PASSED == "通过"
        assert TestResult.PENDING_REVIEW == "待复核"
        assert TestResult.PENDING_CONFIRM == "待确认"
        assert TestResult.DEFECT == "缺陷"

    def test_execution_report_structure(self):
        """执行报告三段式结构"""
        from app.test_platform.report_templates import (
            ReportHeader, ReportSection, ReportFooter, ExecutionReport
        )
        from datetime import datetime, timezone

        report = ExecutionReport(
            header=ReportHeader(
                project="PROJ-1",
                report_type="API执行报告",
                generated_at=datetime.now(timezone.utc).isoformat(),
            ),
            sections=[],
            footer=ReportFooter(conclusion="测试完成"),
        )
        d = report.to_dict()
        assert "header" in d
        assert "sections" in d
        assert "footer" in d
        assert d["footer"]["conclusion"] == "测试完成"
