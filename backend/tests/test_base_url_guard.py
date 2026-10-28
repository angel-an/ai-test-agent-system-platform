"""base_url 覆盖防呆的口径与接入点测试。

真因回顾：调用方传域级 base_url 覆盖掉用例配置里的网关段，请求打到不存在的路径，
网关回空 body 404 → 断言层判定"HTTP 4xx" → 自动登记成被测系统缺陷。
（2026-10-28 ATR-20261028-004 → IDP jf-20260122392-01-3229）
"""

from types import SimpleNamespace

import pytest

from app.services.api_test_executor import APITestExecutor
from app.services.base_url_guard import (
    ALLOW_BASE_URL_OVERRIDE_KEY,
    check_base_url_override,
)
from app.utils.exceptions import BadRequestException

GATEWAY = "https://imarketing-uat.xysjg.com/yundt-saas-gateway"


class TestCheckBaseUrlOverride:
    def test_no_override_passes(self):
        assert check_base_url_override(GATEWAY, None) is None
        assert check_base_url_override(GATEWAY, "") is None

    def test_identical_passes(self):
        assert check_base_url_override(GATEWAY, GATEWAY) is None

    def test_trailing_slash_normalized(self):
        assert check_base_url_override(GATEWAY, GATEWAY + "/") is None

    def test_missing_configured_passes(self):
        # 配置本来就没写 base_url，没有"前缀"可丢
        assert check_base_url_override(None, "https://a.example.com") is None
        assert check_base_url_override("", "https://a.example.com") is None

    def test_domain_only_override_rejected(self):
        """本次事故的原型：传域级地址，吃掉 /yundt-saas-gateway。"""
        reason = check_base_url_override(
            GATEWAY, "https://imarketing-uat.xysjg.com"
        )
        assert reason is not None
        assert "/yundt-saas-gateway" in reason
        assert ALLOW_BASE_URL_OVERRIDE_KEY in reason

    def test_partial_prefix_rejected(self):
        # 只砍掉路径的一段也算丢前缀（请求一样打不到真接口）
        reason = check_base_url_override(GATEWAY, "https://imarketing-uat.xysjg.com/yundt")
        assert reason is not None
        assert "/yundt-saas-gateway" in reason

    def test_more_specific_path_passes(self):
        # 同一前缀下更具体的地址不算丢前缀，不阻断
        assert (
            check_base_url_override(
                "https://a.example.com/api",
                "https://a.example.com/api/v2",
            )
            is None
        )

    def test_different_host_rejected(self):
        reason = check_base_url_override(GATEWAY, "https://sit.example.com/yundt-saas-gateway")
        assert reason is not None
        assert "不同目标" in reason

    def test_port_change_rejected(self):
        reason = check_base_url_override(
            "https://a.example.com:443/api", "https://a.example.com:8443/api"
        )
        assert reason is not None

    def test_scheme_change_rejected(self):
        reason = check_base_url_override(GATEWAY, GATEWAY.replace("https://", "http://"))
        assert reason is not None

    def test_allow_flag_bypasses_both_checks(self):
        assert (
            check_base_url_override(
                GATEWAY, "https://other.example.com", allow=True
            )
            is None
        )
        assert (
            check_base_url_override(
                GATEWAY, "https://imarketing-uat.xysjg.com", allow=True
            )
            is None
        )

    def test_relative_or_hostless_passes(self):
        # 占位符、相对地址解析不出 host：交给执行期兜底，不在这里阻断
        assert check_base_url_override(GATEWAY, "{{BASE_URL}}") is None


class _ExplodingRunRepo:
    """走到建 run 就算失败：防呆必须在它之前生效。"""

    async def get_next_identifier(self, api_test_id):  # noqa: D401
        raise AssertionError("建 run 之前就应该被拦下")


class _StubTestRepo:
    def __init__(self, api_test):
        self._api_test = api_test

    async def get_by_id(self, _id):
        return self._api_test


class TestExecutorGate:
    @staticmethod
    def _executor(api_test):
        executor = APITestExecutor.__new__(APITestExecutor)
        executor.api_test_repo = _StubTestRepo(api_test)
        executor.api_test_run_repo = _ExplodingRunRepo()
        return executor

    @staticmethod
    def _api_test():
        return SimpleNamespace(
            id="11111111-1111-1111-1111-111111111111",
            identifier="AT-1",
            project_id="22222222-2222-2222-2222-222222222222",
            test_config={"base_url": GATEWAY},
        )

    @pytest.mark.asyncio
    async def test_rejects_before_creating_run(self):
        executor = self._executor(self._api_test())
        with pytest.raises(BadRequestException) as exc:
            await executor.execute_test(
                "11111111-1111-1111-1111-111111111111",
                {"base_url": "https://imarketing-uat.xysjg.com"},
            )
        assert "/yundt-saas-gateway" in str(exc.value.message)

    @pytest.mark.asyncio
    async def test_explicit_allow_reaches_run_creation(self):
        """显式开关生效后应继续往下走（这里由 create 抛 AssertionError 证明已越过防呆）。"""
        executor = self._executor(self._api_test())
        with pytest.raises(AssertionError):
            await executor.execute_test(
                "11111111-1111-1111-1111-111111111111",
                {
                    "base_url": "https://imarketing-uat.xysjg.com",
                    ALLOW_BASE_URL_OVERRIDE_KEY: True,
                },
            )
