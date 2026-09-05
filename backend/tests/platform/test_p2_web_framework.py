# -*- coding: utf-8 -*-
"""P2-2 测试：Web 稳定层。"""

import tempfile
from pathlib import Path

import pytest

from app.platform.web_test_framework import (
    WebRegressionLayer,
    WebRunConfig,
    WebResultWriteback,
    WebRegressionRunner,
    WEB_LAYER_DESCRIPTIONS,
)


class TestWebRegressionLayer:
    def test_enum_values(self):
        assert WebRegressionLayer.MAIN_FLOW.value == "main_flow"
        assert WebRegressionLayer.LAYERED.value == "layered"
        assert WebRegressionLayer.NEGATIVE.value == "negative"
        assert WebRegressionLayer.SANITY.value == "sanity"

    def test_descriptions(self):
        assert "主链路" in WEB_LAYER_DESCRIPTIONS[WebRegressionLayer.MAIN_FLOW]
        assert "负向" in WEB_LAYER_DESCRIPTIONS[WebRegressionLayer.NEGATIVE]


class TestWebRunConfig:
    def test_default(self):
        cfg = WebRunConfig("https://example.com")
        assert cfg.base_url == "https://example.com"
        assert cfg.headless is True
        assert cfg.viewport == {"width": 1680, "height": 950}

    def test_custom_layers(self):
        cfg = WebRunConfig(
            "https://example.com",
            layers=[WebRegressionLayer.MAIN_FLOW, WebRegressionLayer.SANITY],
        )
        assert len(cfg.layers) == 2

    def test_to_dict(self):
        cfg = WebRunConfig("https://example.com")
        d = cfg.to_dict()
        assert d["base_url"] == "https://example.com"
        assert d["headless"] is True


class TestWebResultWriteback:
    def test_write_result(self):
        with tempfile.TemporaryDirectory() as tmp:
            wb = WebResultWriteback("run-1", tmp)
            result = wb.write_result(
                status="passed",
                steps=[{"name": "登录", "ok": True}],
                screenshots=["01_login.png"],
            )
            assert result["execution_status"] == "passed"
            assert Path(tmp) / "self_reflect_result.json"

    def test_write_result_with_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            wb = WebResultWriteback("run-2", tmp)
            result = wb.write_result(
                status="failed",
                steps=[],
                screenshots=[],
                error_message="超时",
            )
            assert result["error_message"] == "超时"


class TestWebRegressionRunner:
    def test_run_layer_abstract(self):
        cfg = WebRunConfig("https://example.com", layers=[WebRegressionLayer.SANITY])
        runner = WebRegressionRunner(cfg)
        result = runner.run_layer(WebRegressionLayer.SANITY)
        assert result["status"] == "skipped"
        assert "抽象接口" in result["reason"]

    def test_run_all(self):
        cfg = WebRunConfig(
            "https://example.com",
            layers=[WebRegressionLayer.SANITY, WebRegressionLayer.MAIN_FLOW],
        )
        runner = WebRegressionRunner(cfg)
        results = runner.run_all()
        assert len(results) == 2

    def test_should_run_negative_separately(self):
        cfg = WebRunConfig(
            "https://example.com",
            layers=[WebRegressionLayer.MAIN_FLOW, WebRegressionLayer.NEGATIVE],
        )
        runner = WebRegressionRunner(cfg)
        assert runner.should_run_negative_separately() is True

    def test_should_not_run_negative(self):
        cfg = WebRunConfig(
            "https://example.com",
            layers=[WebRegressionLayer.MAIN_FLOW],
        )
        runner = WebRegressionRunner(cfg)
        assert runner.should_run_negative_separately() is False
