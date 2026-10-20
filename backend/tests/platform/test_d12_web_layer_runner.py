# -*- coding: utf-8 -*-
"""D12 测试：Web 分层运行器 + 路由契约验证。"""

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.test_platform.web_test_framework import WebRegressionLayer
from app.test_platform.web_test_framework.runner import (
    WebLayerRunner,
    WebLayerExecutionResult,
    run_web_layers,
    get_web_layer_config,
)
from app.test_platform.execution_gates import GateConfig

client = TestClient(app)


class TestWebLayerRunner:
    """测试 WebLayerRunner"""

    def test_init(self):
        runner = WebLayerRunner("PROJ-1", "http://example.com")
        assert runner.project == "PROJ-1"
        assert runner.base_url == "http://example.com"

    def test_should_skip_layer_safe_mode(self):
        config = GateConfig(live_web=False)
        runner = WebLayerRunner("PROJ-1", "http://example.com", gate_config=config)
        should_skip, reason = runner._should_skip_layer(WebRegressionLayer.SANITY)
        assert should_skip is True
        assert any(k in str(reason) for k in ["LIVE_WEB", "未开启"])

    def test_should_skip_layer_live_mode(self):
        config = GateConfig(live_web=True)
        runner = WebLayerRunner("PROJ-1", "http://example.com", gate_config=config)
        should_skip, reason = runner._should_skip_layer(WebRegressionLayer.SANITY)
        assert should_skip is False

    def test_should_skip_negative_risky_denied(self):
        config = GateConfig(live_web=True, run_risky_negative=False)
        runner = WebLayerRunner("PROJ-1", "http://example.com", gate_config=config)
        should_skip, reason = runner._should_skip_layer(WebRegressionLayer.NEGATIVE)
        assert should_skip is True

    def test_should_skip_negative_risky_allowed(self):
        config = GateConfig(live_web=True, run_risky_negative=True)
        runner = WebLayerRunner("PROJ-1", "http://example.com", gate_config=config)
        should_skip, reason = runner._should_skip_layer(WebRegressionLayer.NEGATIVE)
        assert should_skip is False

    def test_layer_result_to_dict(self):
        result = WebLayerExecutionResult(
            layer=WebRegressionLayer.SANITY,
            status="passed",
            duration_ms=2000,
            screenshot_count=3,
        )
        d = result.to_dict()
        assert d["layer"] == "sanity"
        assert d["status"] == "passed"
        assert d["screenshot_count"] == 3


class TestGetWebLayerConfig:
    """测试配置获取"""

    def test_returns_four_layers(self):
        config = get_web_layer_config()
        assert len(config) == 4

    def test_layer_fields(self):
        config = get_web_layer_config()
        for layer in config:
            assert "layer" in layer
            assert "layer_name" in layer
            assert "requires_live" in layer
            assert "is_risky" in layer

    def test_negative_is_risky(self):
        config = get_web_layer_config()
        negative = next(l for l in config if l["layer"] == "negative")
        assert negative["is_risky"] is True


class TestRunWebLayers:
    """测试便捷函数"""

    def test_safe_mode_skips_all(self):
        config = GateConfig(live_web=False)
        results = run_web_layers("PROJ-1", "http://example.com", gate_config=config)

        for r in results:
            assert r["status"] == "skipped"

    def test_default_runs_sanity(self):
        config = GateConfig(live_web=False)
        results = run_web_layers("PROJ-1", "http://example.com", gate_config=config)
        assert len(results) == 1
        assert results[0]["layer"] == "sanity"

    def test_with_layers_filter(self):
        config = GateConfig(live_web=False)
        results = run_web_layers(
            "PROJ-1", "http://example.com",
            layers=["sanity", "main_flow"],
            gate_config=config,
        )
        assert len(results) == 2


class TestWebLayerRoutes:
    """路由联调测试"""

    def test_get_config(self):
        response = client.get("/api/v2/projects/PROJ-1/web-layers")
        assert response.status_code == 200

        data = response.json()
        assert data["success"] is True
        assert "data" in data
        assert "layers" in data["data"]
        assert "gate_status" in data["data"]
        assert "safe_mode" in data["data"]

    def test_get_layers_count(self):
        response = client.get("/api/v2/projects/PROJ-1/web-layers")
        layers = response.json()["data"]["layers"]
        assert len(layers) == 4

    def test_execute_safe_mode(self):
        response = client.post(
            "/api/v2/projects/PROJ-1/web-layers/execute",
            json={
                "base_url": "http://example.com",
                "live_web": False,
            },
        )
        assert response.status_code == 200

        results = response.json()["data"]
        assert isinstance(results, list)
        for r in results:
            assert r["status"] == "skipped"

    def test_execute_with_layers(self):
        response = client.post(
            "/api/v2/projects/PROJ-1/web-layers/execute",
            json={
                "base_url": "http://example.com",
                "layers": ["sanity"],
                "live_web": False,
            },
        )
        assert response.status_code == 200
        results = response.json()["data"]
        assert len(results) == 1
        assert results[0]["layer"] == "sanity"

    def test_single_layer_execute(self):
        response = client.post(
            "/api/v2/projects/PROJ-1/web-layers/sanity/execute",
            json={
                "base_url": "http://example.com",
                "live_web": False,
            },
        )
        assert response.status_code == 200
        result = response.json()["data"]
        assert result["layer"] == "sanity"

    def test_invalid_layer(self):
        response = client.post(
            "/api/v2/projects/PROJ-1/web-layers/invalid/execute",
            json={"base_url": "http://example.com"},
        )
        assert response.status_code == 400

    def test_success_response_wrapper(self):
        routes = [
            ("GET", "/api/v2/projects/PROJ-1/web-layers", None),
            ("POST", "/api/v2/projects/PROJ-1/web-layers/execute", {"base_url": "http://example.com", "live_web": False}),
            ("POST", "/api/v2/projects/PROJ-1/web-layers/sanity/execute", {"base_url": "http://example.com", "live_web": False}),
        ]

        for method, url, body in routes:
            if method == "GET":
                resp = client.get(url)
            else:
                resp = client.post(url, json=body or {})

            data = resp.json()
            assert "success" in data, f"{method} {url} 缺少 success 字段"
            assert "data" in data, f"{method} {url} 缺少 data 字段"
