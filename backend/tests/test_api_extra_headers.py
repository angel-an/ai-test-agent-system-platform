# -*- coding: utf-8 -*-
"""额外请求头（API_TEST_EXTRA_HEADERS）：真实 SUT 需要时的注入通道。

动机（2026-10-07 真实 UAT 实测）：小杨生煎网关按 **referer** 识别实例 ——
不带 referer 时返回 `SaasUserInfoInterceptor 应用识别失败 by hostname:...`（HTTP 200），
看着像接口问题，实为平台没带够头。平台已有 `data_profile → API_TEST_<PROFILE>_*` 注入通道，
但只覆盖 BASE_URL / AUTHORIZATION / AUTH_HEADER / DATA_JSON，没有"额外头"。

覆盖：注入映射、敏感项不落库、生成脚本里的合并与配置写坏时的显式报错。
"""

from __future__ import annotations

from types import SimpleNamespace

from app.services.api_case_link_service import APICaseLinkService
from app.services.api_runtime_data_service import (
    resolve_api_runtime_config,
    sanitize_api_execution_config,
)
from app.services.api_response_envelope import CUSTOM_CONFIG_KEY  # noqa: F401  (保持同源导入习惯)


def _render(path: str) -> str:
    svc = object.__new__(APICaseLinkService)
    endpoint = SimpleNamespace(method="GET", path=path, request_body=None, parameters=[],
                               custom_config={}, responses={})
    return svc._generate_playwright_script(endpoint)


class TestExtraHeadersInjection:
    def test_profile_maps_extra_headers_env(self):
        cfg = resolve_api_runtime_config(
            {"data_profile": "XYSJG_UAT_READONLY", "base_url": "https://x"},
            None,
            environ={
                "API_TEST_XYSJG_UAT_READONLY_BASE_URL": "https://uat.example.com",
                "API_TEST_XYSJG_UAT_READONLY_EXTRA_HEADERS":
                    '{"referer": "https://uat.example.com/front/app/"}',
            },
        )
        assert cfg["env"]["API_BASE_URL"] == "https://uat.example.com"
        assert cfg["env"]["API_TEST_EXTRA_HEADERS"] == '{"referer": "https://uat.example.com/front/app/"}'

    def test_extra_headers_are_not_persisted(self):
        """额外头可能含鉴权信息 → 与 AUTHORIZATION 同等对待，不落库/不进报告。"""
        sanitized = sanitize_api_execution_config(
            {"env": {"API_TEST_EXTRA_HEADERS": '{"referer": "x"}', "API_TEST_AUTH_HEADER": "access-token"}}
        )
        assert "API_TEST_EXTRA_HEADERS" not in (sanitized.get("env") or {})
        assert (sanitized.get("env") or {}).get("API_TEST_AUTH_HEADER") == "access-token"

    def test_rendered_script_merges_extra_headers(self):
        script = _render("/v1/purchaseLimit/list")
        assert "process.env.API_TEST_EXTRA_HEADERS" in script
        assert "resolvedOptions.headers = { ...(resolvedOptions.headers || {}), ...extraHeaders };" in script

    def test_broken_config_is_reported_as_platform_error(self):
        """配置写坏时必须**明说这是平台配置问题**，否则又会被归因成接口问题。"""
        script = _render("/v1/purchaseLimit/list")
        assert "PLATFORM_CONFIG_ERROR" in script
        assert "不是被测接口问题" in script
