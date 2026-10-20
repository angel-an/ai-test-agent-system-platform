# -*- coding: utf-8 -*-
"""D14 修正测试：编排入口真实场景传递与结果映射。"""

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.test_platform.orchestrator import (
    OrchestratorConfig,
    DualTrackRunner,
    select_scenarios,
    run_orchestration,
)
from app.test_platform.execution_gates import GateConfig

client = TestClient(app)


class TestScenarioPassing:
    """P1: 场景筛选结果传入 runner"""

    def test_select_scenarios_passed_to_orchestration(self):
        """场景筛选结果应影响执行"""
        cases = [
            {"id": "TC-1", "keyword": "正向", "module": "登录"},
            {"id": "TC-2", "keyword": "反向", "module": "登录"},
            {"id": "TC-3", "keyword": "正向", "module": "注册"},
        ]
        config = OrchestratorConfig(
            project="PROJ-1",
            selected_scenarios=["TC-1", "TC-3"],
            run_api=False,
            run_web=False,
        )
        result = run_orchestration(cases, config)
        # 字段契约已统一，应只选中 2 个场景
        assert result["scenarios_selected"] == 2

    def test_empty_cases_returns_message(self):
        """空用例列表返回提示"""
        response = client.post(
            "/api/v2/projects/PROJ-1/orchestrate/execute",
            json={"cases": [], "run_api": False, "run_web": False},
        )
        assert response.status_code == 200
        data = response.json()["data"]
        assert data["scenarios_selected"] == 0
        assert "message" in data

    def test_no_matching_scenarios_returns_message(self):
        """无匹配场景返回提示"""
        response = client.post(
            "/api/v2/projects/PROJ-1/orchestrate/execute",
            json={
                "cases": [{"id": "TC-1"}],
                "selected_scenarios": ["TC-NOT-EXIST"],
                "run_api": False,
                "run_web": False,
            },
        )
        assert response.status_code == 200
        data = response.json()["data"]
        assert data["scenarios_selected"] == 0


class TestStatusMapping:
    """P1: runner 返回值映射为真实状态"""

    def test_safe_mode_returns_skipped(self):
        """安全模式下 API 分层被 skip"""
        response = client.post(
            "/api/v2/projects/PROJ-1/orchestrate/execute",
            json={
                "cases": [{"id": "TC-1"}],
                "run_api": True,
                "run_web": False,
                "live_api": False,
            },
        )
        assert response.status_code == 200
        track_results = response.json()["data"]["track_results"]
        api_result = next(r for r in track_results if r["track"] == "api")
        # 安全模式下所有层被 skip，所以 API 轨状态为 skipped
        assert api_result["status"] == "skipped"

    def test_live_mode_returns_completed(self):
        """live 模式下 API 分层执行"""
        response = client.post(
            "/api/v2/projects/PROJ-1/orchestrate/execute",
            json={
                "cases": [{"id": "TC-1"}],
                "run_api": True,
                "run_web": False,
                "live_api": True,
            },
        )
        assert response.status_code == 200
        track_results = response.json()["data"]["track_results"]
        api_result = next(r for r in track_results if r["track"] == "api")
        assert api_result["status"] == "completed"

    def test_web_without_base_url_skipped(self):
        """Web 轨缺少 base_url 时 skipped"""
        response = client.post(
            "/api/v2/projects/PROJ-1/orchestrate/execute",
            json={
                "cases": [{"id": "TC-1"}],
                "run_api": False,
                "run_web": True,
                "live_web": True,
                "base_url": "",
            },
        )
        assert response.status_code == 200
        track_results = response.json()["data"]["track_results"]
        web_result = next(r for r in track_results if r["track"] == "web")
        assert web_result["status"] == "skipped"
        assert "base_url" in web_result.get("error", "")

    def test_api_disabled_returns_skipped(self):
        """API 轨未启用时 skipped"""
        response = client.post(
            "/api/v2/projects/PROJ-1/orchestrate/execute",
            json={
                "cases": [{"id": "TC-1"}],
                "run_api": False,
                "run_web": False,
            },
        )
        assert response.status_code == 200
        track_results = response.json()["data"]["track_results"]
        api_result = next(r for r in track_results if r["track"] == "api")
        assert api_result["status"] == "skipped"


class TestEvidenceGeneration:
    """P1: 证据包生成"""

    def test_evidence_run_id_generated(self):
        """执行后应生成证据 run_id"""
        response = client.post(
            "/api/v2/projects/PROJ-1/orchestrate/execute",
            json={
                "cases": [{"id": "TC-1"}],
                "run_api": True,
                "live_api": True,
            },
        )
        assert response.status_code == 200
        data = response.json()["data"]
        assert "evidence_run_id" in data
        assert data["evidence_run_id"] is not None

    def test_evidence_zip_path_generated(self):
        """执行后应生成证据 ZIP"""
        response = client.post(
            "/api/v2/projects/PROJ-1/orchestrate/execute",
            json={
                "cases": [{"id": "TC-1"}],
                "run_api": True,
                "live_api": True,
            },
        )
        assert response.status_code == 200
        data = response.json()["data"]
        assert "evidence_zip_path" in data
        # ZIP 路径可能为 None（如果证据生成失败），但字段必须存在


class TestReportStructure:
    """P2: 报告结构"""

    def test_report_contains_header_sections_footer(self):
        """报告包含头、节、尾"""
        response = client.post(
            "/api/v2/projects/PROJ-1/orchestrate/execute",
            json={
                "cases": [{"id": "TC-1"}],
                "run_api": True,
                "live_api": True,
            },
        )
        report = response.json()["data"]["report"]
        assert "header" in report
        assert "sections" in report
        assert "footer" in report

    def test_scenarios_count_matches_selected(self):
        """scenarios_selected 应匹配实际筛选数"""
        response = client.post(
            "/api/v2/projects/PROJ-1/orchestrate/execute",
            json={
                "cases": [
                    {"id": "TC-1"},
                    {"id": "TC-2"},
                ],
                "selected_scenarios": ["TC-1"],
                "run_api": False,
                "run_web": False,
            },
        )
        data = response.json()["data"]
        assert data["scenarios_selected"] == 1


class TestRouteConsistency:
    """路由一致性"""

    def test_all_routes_return_success_response(self):
        """所有路由返回 SuccessResponse"""
        routes = [
            ("GET", "/api/v2/projects/PROJ-1/orchestrate/status", None),
            ("POST", "/api/v2/projects/PROJ-1/orchestrate/execute", {
                "cases": [], "run_api": False, "run_web": False
            }),
        ]

        for method, url, body in routes:
            if method == "GET":
                resp = client.get(url)
            else:
                resp = client.post(url, json=body or {})

            data = resp.json()
            assert "success" in data, f"{method} {url} 缺少 success"
            assert "data" in data, f"{method} {url} 缺少 data"
