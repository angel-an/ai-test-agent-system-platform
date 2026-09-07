# -*- coding: utf-8 -*-
"""P2-1 测试：API 分层。"""

import pytest

from app.test_platform.api_test_framework.api_layer import (
    ApiLayer,
    LayerConfig,
    DEFAULT_LAYER_CONFIGS,
    LAYER_ORDER,
    LAYER_DESCRIPTIONS,
    generate_test_skeleton,
    assert_envelope_ok,
    assert_delta_within,
    assert_field_exists,
    BaseApiClient,
    HttpApiClient,
)


class TestApiLayer:
    def test_enum_values(self):
        assert ApiLayer.AUTH.value == "p0_auth"
        assert ApiLayer.QUERY.value == "p1_query"
        assert ApiLayer.WRITE.value == "p1_write"
        assert ApiLayer.COMPOSITE.value == "p2_composite"
        assert ApiLayer.NEGATIVE.value == "p2_negative"
        assert ApiLayer.CLEANUP.value == "p3_cleanup"

    def test_layer_order(self):
        assert LAYER_ORDER[0] == ApiLayer.AUTH
        assert LAYER_ORDER[-1] == ApiLayer.CLEANUP
        assert len(LAYER_ORDER) == 6

    def test_layer_descriptions(self):
        assert "准入" in LAYER_DESCRIPTIONS[ApiLayer.AUTH]
        assert "查询" in LAYER_DESCRIPTIONS[ApiLayer.QUERY]
        assert "写入" in LAYER_DESCRIPTIONS[ApiLayer.WRITE]


class TestLayerConfig:
    def test_default(self):
        cfg = LayerConfig(ApiLayer.QUERY)
        assert cfg.enabled is True
        assert cfg.requires_live is False
        assert cfg.is_risky is False

    def test_risky_layer(self):
        cfg = LayerConfig(ApiLayer.NEGATIVE, is_risky=True)
        assert cfg.is_risky is True

    def test_to_dict(self):
        cfg = LayerConfig(ApiLayer.AUTH, test_files=["test_p0_auth.py"])
        d = cfg.to_dict()
        assert d["layer"] == "p0_auth"
        assert d["test_files"] == ["test_p0_auth.py"]
        assert "准入" in d["description"]


class TestDefaultLayerConfigs:
    def test_safe_mode(self):
        # 安全模式：只读层默认启用，写入层需显式开启
        assert DEFAULT_LAYER_CONFIGS[ApiLayer.AUTH].enabled is True
        assert DEFAULT_LAYER_CONFIGS[ApiLayer.QUERY].enabled is True
        assert DEFAULT_LAYER_CONFIGS[ApiLayer.WRITE].enabled is False
        assert DEFAULT_LAYER_CONFIGS[ApiLayer.NEGATIVE].enabled is False

    def test_all_require_live(self):
        for layer, cfg in DEFAULT_LAYER_CONFIGS.items():
            if layer != ApiLayer.CLEANUP:
                assert cfg.requires_live is True


class TestGenerateTestSkeleton:
    def test_generates_content(self):
        result = generate_test_skeleton("PROJ-1", ApiLayer.AUTH)
        assert "test_file" in result
        assert "conftest" in result
        assert "p0_auth" in result["test_file"]
        assert "pytest" in result["test_file"]

    def test_contains_skipif(self):
        result = generate_test_skeleton("PROJ-1", ApiLayer.QUERY)
        assert "skipif" in result["test_file"]
        assert "LIVE_API" in result["test_file"]


class TestAssertionHelpers:
    def test_assert_envelope_ok(self):
        assert_envelope_ok({"resultCode": "0", "data": {}})

    def test_assert_envelope_ok_fail(self):
        with pytest.raises(AssertionError):
            assert_envelope_ok({"resultCode": "1", "resultMsg": "错误"})

    def test_assert_envelope_ok_none(self):
        with pytest.raises(AssertionError):
            assert_envelope_ok(None)

    def test_assert_delta_within(self):
        assert_delta_within(0.01, 0.01)
        assert_delta_within(0.011, 0.01)  # 边界

    def test_assert_delta_within_fail(self):
        with pytest.raises(AssertionError):
            assert_delta_within(0.1, 0.01)

    def test_assert_field_exists(self):
        body = {"data": {"list": [{"phone": "13900139000"}]}}
        val = assert_field_exists(body, "data.list.0.phone")
        assert val == "13900139000"

    def test_assert_field_exists_missing(self):
        with pytest.raises(AssertionError):
            assert_field_exists({"a": 1}, "a.b.c")


class TestBaseApiClient:
    def test_is_configured(self):
        client = BaseApiClient("https://api.example.com")
        assert client.is_configured() is True

    def test_not_configured_empty(self):
        client = BaseApiClient("")
        assert client.is_configured() is False

    def test_full_url(self):
        client = BaseApiClient("https://api.example.com/")
        assert client._full_url("/test") == "https://api.example.com/test"


class TestHttpApiClient:
    def test_build_headers_with_token(self):
        client = HttpApiClient("https://api.example.com", access_token="tok123")
        headers = client._build_headers()
        assert headers["access-token"] == "tok123"

    def test_build_headers_without_token(self):
        client = HttpApiClient("https://api.example.com")
        headers = client._build_headers()
        assert "access-token" not in headers
