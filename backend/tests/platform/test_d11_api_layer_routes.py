# -*- coding: utf-8 -*-
"""D11 路由联调测试：API 分层执行路由契约验证。"""

import pytest
from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


class TestGetAPILayerConfig:
    """GET /projects/{id}/api-layers"""

    def test_returns_success_response(self):
        response = client.get("/api/v2/projects/PROJ-1/api-layers")
        assert response.status_code == 200

        data = response.json()
        assert data["success"] is True
        assert "data" in data

    def test_response_structure(self):
        response = client.get("/api/v2/projects/PROJ-1/api-layers")
        data = response.json()["data"]

        # 必须包含三个顶层字段
        assert "layers" in data
        assert "gate_status" in data
        assert "safe_mode" in data

    def test_layers_count(self):
        response = client.get("/api/v2/projects/PROJ-1/api-layers")
        layers = response.json()["data"]["layers"]
        assert len(layers) == 6

    def test_layer_fields(self):
        response = client.get("/api/v2/projects/PROJ-1/api-layers")
        layers = response.json()["data"]["layers"]

        for layer in layers:
            assert "layer" in layer
            assert "layer_name" in layer
            assert "enabled" in layer
            assert "requires_live" in layer
            assert "is_risky" in layer

    def test_first_layer_is_auth(self):
        response = client.get("/api/v2/projects/PROJ-1/api-layers")
        layers = response.json()["data"]["layers"]
        assert layers[0]["layer"] == "p0_auth"

    def test_gate_status_fields(self):
        response = client.get("/api/v2/projects/PROJ-1/api-layers")
        gate = response.json()["data"]["gate_status"]

        assert "live_api" in gate
        assert "live_web" in gate
        assert "run_risky_negative" in gate
        assert "safe_mode" in gate

    def test_safe_mode_boolean(self):
        response = client.get("/api/v2/projects/PROJ-1/api-layers")
        safe_mode = response.json()["data"]["safe_mode"]
        assert isinstance(safe_mode, bool)


class TestExecuteAPILayers:
    """POST /projects/{id}/api-layers/execute"""

    def test_empty_body_runs_default(self):
        """空 body 执行默认启用的分层"""
        response = client.post("/api/v2/projects/PROJ-1/api-layers/execute", json={})
        assert response.status_code == 200

        data = response.json()
        assert data["success"] is True
        assert isinstance(data["data"], list)
        assert len(data["data"]) == 6

    def test_with_layers_filter(self):
        """指定分层列表"""
        response = client.post(
            "/api/v2/projects/PROJ-1/api-layers/execute",
            json={"layers": ["p1_query"]},
        )
        assert response.status_code == 200

        results = response.json()["data"]
        # 只有 p1_query 被执行，其他被 skip
        query_result = next(r for r in results if r["layer"] == "p1_query")
        assert query_result["status"] in ("passed", "skipped", "failed")

    def test_with_live_api_override(self):
        """覆盖 live_api 参数"""
        response = client.post(
            "/api/v2/projects/PROJ-1/api-layers/execute",
            json={"live_api": False, "layers": ["p1_query"]},
        )
        assert response.status_code == 200

        results = response.json()["data"]
        query_result = next(r for r in results if r["layer"] == "p1_query")
        # live_api=False 时，需要 live 的层被 skip
        assert query_result["status"] == "skipped"

    def test_body_with_all_fields(self):
        """完整 body 参数"""
        response = client.post(
            "/api/v2/projects/PROJ-1/api-layers/execute",
            json={
                "layers": ["p0_auth", "p1_query"],
                "live_api": True,
                "run_risky": False,
            },
        )
        assert response.status_code == 200
        assert response.json()["success"] is True

    def test_result_structure(self):
        """返回结果结构校验"""
        response = client.post(
            "/api/v2/projects/PROJ-1/api-layers/execute",
            json={"layers": ["p1_query"], "live_api": False},
        )
        results = response.json()["data"]

        for result in results:
            assert "layer" in result
            assert "layer_name" in result
            assert "status" in result
            assert "duration_ms" in result
            # status 必须是预定义值之一
            assert result["status"] in ("passed", "failed", "skipped", "error")


class TestExecuteSingleLayer:
    """POST /projects/{id}/api-layers/{layer}/execute"""

    def test_valid_layer(self):
        response = client.post(
            "/api/v2/projects/PROJ-1/api-layers/p1_query/execute",
            json={"live_api": False},
        )
        assert response.status_code == 200

        data = response.json()
        assert data["success"] is True
        assert data["data"]["layer"] == "p1_query"

    def test_invalid_layer(self):
        response = client.post(
            "/api/v2/projects/PROJ-1/api-layers/invalid_layer/execute",
            json={},
        )
        assert response.status_code == 400

    def test_single_layer_result_structure(self):
        response = client.post(
            "/api/v2/projects/PROJ-1/api-layers/p1_query/execute",
            json={"live_api": False},
        )
        result = response.json()["data"]

        assert result["layer"] == "p1_query"
        assert "layer_name" in result
        assert "status" in result
        assert "duration_ms" in result

    def test_body_with_live_override(self):
        response = client.post(
            "/api/v2/projects/PROJ-1/api-layers/p1_query/execute",
            json={"live_api": False, "run_risky": False},
        )
        assert response.status_code == 200
        assert response.json()["success"] is True


class TestRouteConsistency:
    """路由间一致性验证"""

    def test_get_and_execute_same_project(self):
        """GET 和 POST 使用相同的 project_identifier"""
        get_resp = client.get("/api/v2/projects/PROJ-TEST/api-layers")
        post_resp = client.post(
            "/api/v2/projects/PROJ-TEST/api-layers/execute",
            json={"live_api": False},
        )

        assert get_resp.status_code == 200
        assert post_resp.status_code == 200

        # 两个响应的 layers 数量一致
        get_layers = get_resp.json()["data"]["layers"]
        post_results = post_resp.json()["data"]
        assert len(get_layers) == len(post_results)

    def test_success_response_wrapper(self):
        """所有路由都返回 SuccessResponse 包装"""
        routes = [
            ("GET", "/api/v2/projects/PROJ-1/api-layers", None),
            ("POST", "/api/v2/projects/PROJ-1/api-layers/execute", {"live_api": False}),
            ("POST", "/api/v2/projects/PROJ-1/api-layers/p1_query/execute", {"live_api": False}),
        ]

        for method, url, body in routes:
            if method == "GET":
                resp = client.get(url)
            else:
                resp = client.post(url, json=body or {})

            data = resp.json()
            assert "success" in data, f"{method} {url} 缺少 success 字段"
            assert "data" in data, f"{method} {url} 缺少 data 字段"
