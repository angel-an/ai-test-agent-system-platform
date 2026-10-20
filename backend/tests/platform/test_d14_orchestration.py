# -*- coding: utf-8 -*-
"""D14 测试：编排入口路由 + 双轨执行验证。"""

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.test_platform.orchestrator import (
    OrchestratorConfig,
    DualTrackRunner,
    select_scenarios,
    aggregate_results,
    run_orchestration,
)
from app.test_platform.execution_gates import GateConfig

client = TestClient(app)


class TestOrchestratorUnit:
    """编排器单元测试"""

    def test_select_scenarios_empty(self):
        """空条件返回全部"""
        cases = [
            {"id": "TC-1", "keyword": "正向"},
            {"id": "TC-2", "keyword": "反向"},
        ]
        result = select_scenarios(cases)
        assert len(result) == 2

    def test_select_scenarios_by_keyword(self):
        """按关键词筛选"""
        cases = [
            {"id": "TC-1", "keyword": "正向"},
            {"id": "TC-2", "keyword": "反向"},
        ]
        result = select_scenarios(cases, {"keywords": ["正向"]})
        assert len(result) == 1
        assert result[0]["id"] == "TC-1"

    def test_select_scenarios_by_ids(self):
        """按 ID 筛选"""
        cases = [
            {"id": "TC-1"},
            {"id": "TC-2"},
            {"id": "TC-3"},
        ]
        result = select_scenarios(cases, {"ids": ["TC-1", "TC-3"]})
        assert len(result) == 2

    def test_dual_track_runner_skip_api(self):
        """API 轨未启用时跳过"""
        config = OrchestratorConfig(
            project="PROJ-1",
            run_api=False,
            run_web=False,
        )
        runner = DualTrackRunner(config)
        results = runner.run([])
        assert len(results) == 0

    def test_dual_track_runner_safe_mode(self):
        """安全模式下 API/Web 被跳过"""
        config = OrchestratorConfig(
            project="PROJ-1",
            run_api=True,
            run_web=True,
            gate_config=GateConfig(live_api=False, live_web=False),
        )
        runner = DualTrackRunner(config)
        results = runner.run([])
        assert len(results) == 2
        assert results[0].status == "skipped"
        assert results[1].status == "skipped"

    def test_dual_track_runner_live_mode(self):
        """live 模式下可执行"""
        config = OrchestratorConfig(
            project="PROJ-1",
            run_api=True,
            run_web=True,
            gate_config=GateConfig(live_api=True, live_web=True),
        )
        runner = DualTrackRunner(config)
        results = runner.run([])
        assert results[0].status == "completed"
        assert results[1].status == "completed"

    def test_run_orchestration_full_flow(self):
        """完整编排流程"""
        cases = [
            {"id": "TC-1", "keyword": "正向", "module": "登录"},
            {"id": "TC-2", "keyword": "反向", "module": "登录"},
        ]
        config = OrchestratorConfig(
            project="PROJ-1",
            run_api=True,
            run_web=False,
            gate_config=GateConfig(live_api=True),
        )
        result = run_orchestration(cases, config)
        assert result["project"] == "PROJ-1"
        assert result["scenarios_selected"] == 2
        assert "track_results" in result
        assert "report" in result


class TestOrchestrationRoutes:
    """编排路由联调测试"""

    def test_get_status(self):
        """GET 编排状态"""
        response = client.get("/api/v2/projects/PROJ-1/orchestrate/status")
        assert response.status_code == 200

        data = response.json()
        assert data["success"] is True
        assert "gate_status" in data["data"]
        assert "safe_mode" in data["data"]

    def test_execute_safe_mode(self):
        """安全模式下执行"""
        response = client.post(
            "/api/v2/projects/PROJ-1/orchestrate/execute",
            json={
                "cases": [
                    {"id": "TC-1", "keyword": "正向"},
                ],
                "run_api": True,
                "run_web": False,
                "live_api": False,
            },
        )
        assert response.status_code == 200

        data = response.json()["data"]
        api_result = next((r for r in data["track_results"] if r["track"] == "api"), None)
        assert api_result is not None
        # 安全模式下 API 分层被 skip，但编排器标记为 completed（执行了分层 runner）
        # 实际分层结果中有 skipped 层
        assert api_result["status"] in ("completed", "skipped")

    def test_execute_live_mode(self):
        """live 模式下执行"""
        response = client.post(
            "/api/v2/projects/PROJ-1/orchestrate/execute",
            json={
                "cases": [
                    {"id": "TC-1", "keyword": "正向"},
                ],
                "run_api": True,
                "run_web": False,
                "live_api": True,
            },
        )
        assert response.status_code == 200

        data = response.json()["data"]
        api_result = next((r for r in data["track_results"] if r["track"] == "api"), None)
        assert api_result is not None
        assert api_result["status"] == "completed"

    def test_execute_with_scenario_filter(self):
        """带场景筛选执行"""
        response = client.post(
            "/api/v2/projects/PROJ-1/orchestrate/execute",
            json={
                "cases": [
                    {"id": "TC-1", "keyword": "正向"},
                    {"id": "TC-2", "keyword": "反向"},
                ],
                "selected_scenarios": ["TC-1"],
                "run_api": True,
                "live_api": True,
            },
        )
        assert response.status_code == 200
        data = response.json()["data"]
        assert data["scenarios_selected"] == 1

    def test_execute_dual_track(self):
        """双轨执行"""
        response = client.post(
            "/api/v2/projects/PROJ-1/orchestrate/execute",
            json={
                "cases": [
                    {"id": "TC-1"},
                ],
                "run_api": True,
                "run_web": True,
                "live_api": True,
                "live_web": True,
                "base_url": "http://example.com",
            },
        )
        assert response.status_code == 200
        data = response.json()["data"]
        assert len(data["track_results"]) == 2

    def test_execute_report_structure(self):
        """报告结构验证"""
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

    def test_success_response_wrapper(self):
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
