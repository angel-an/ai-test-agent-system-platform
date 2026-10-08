# -*- coding: utf-8 -*-
"""Web 执行前预检（preflight）回归测试。

要求：
1. 四个码稳定可得：sub_function_unbound / script_not_registered /
   missing_credentials / placeholder_base_url；
2. 归类不了时用兜底码，但**原始原因不丢**；
3. 不放过本来能跑的场景（凭据判据与脚本一致，占位判定不误伤真实域名）。
"""

from __future__ import annotations

from app.services.web_preflight import (
    CODE_MISSING_CREDENTIALS,
    CODE_PLACEHOLDER_BASE_URL,
    CODE_PROVENANCE_REJECTED,
    CODE_SCRIPT_NOT_REGISTERED,
    CODE_SUB_FUNCTION_UNBOUND,
    build_preflight,
    check_web_preflight,
    classify_provenance_reason,
    has_runtime_credentials,
    is_placeholder_base_url,
    preflight_provenance_reason,
    resolve_runtime_credentials,
)

CREDS = {"WEB_TEST_USERNAME": "u", "WEB_TEST_PASSWORD": "p"}
UAT = "https://console-uat.xysjg.com/front/dtcloud-console-web-pc/#/login"


class TestSubFunctionUnbound:
    def test_missing_sub_function_is_coded(self):
        got = check_web_preflight(sub_function_id=None, base_url=UAT,
                                  execution_config={"env": CREDS})
        assert got["code"] == CODE_SUB_FUNCTION_UNBOUND
        assert got["hint"]
        assert "sub_function_id" in got["message"]

    def test_bound_sub_function_passes_this_check(self):
        got = check_web_preflight(sub_function_id="b0a0b651-5ad2-4dfd-8763-8cb6265aa19b",
                                  base_url=UAT, execution_config={"env": CREDS})
        assert got is None


class TestPlaceholderBaseUrl:
    def test_placeholder_and_empty_are_caught(self):
        for url in ("https://example.invalid/console/login", "http://example.com/x",
                    "", None, "https://your-domain.test/app"):
            got = check_web_preflight(sub_function_id="sf", base_url=url,
                                      execution_config={"env": CREDS})
            assert got["code"] == CODE_PLACEHOLDER_BASE_URL, url

    def test_real_urls_are_not_flagged(self):
        for url in (UAT, "https://imarketing-uat.xysjg.com/front/x/#/login",
                    "http://127.0.0.1:3001/"):
            assert is_placeholder_base_url(url) is False, url
            assert check_web_preflight(sub_function_id="sf", base_url=url,
                                       execution_config={"env": CREDS}) is None


class TestCredentials:
    ENV = {"WEB_TEST_USERNAME": "envu", "WEB_TEST_PASSWORD": "envp"}

    def test_missing_everywhere_is_coded(self):
        got = check_web_preflight(sub_function_id="sf", base_url=UAT,
                                  execution_config={}, test_config={}, environ={})
        assert got["code"] == CODE_MISSING_CREDENTIALS
        assert got["hint"]

    def test_explicit_config_env_passes(self):
        got = check_web_preflight(
            sub_function_id="sf", base_url=UAT,
            execution_config={"env": CREDS}, test_config={}, environ={})
        assert got is None

    def test_process_env_passes(self):
        got = check_web_preflight(sub_function_id="sf", base_url=UAT,
                                  execution_config={}, test_config={}, environ=self.ENV)
        assert got is None

    def test_login_profile_derived_keys_are_honored(self):
        """test_config.login_profile 会派生 {PROFILE}_USERNAME/_PASSWORD（执行器同款逻辑）。"""
        environ = {"WEB_TEST_DEFAULT_USERNAME": "u", "WEB_TEST_DEFAULT_PASSWORD": "p"}
        assert has_runtime_credentials({}, {}, environ) is True
        assert has_runtime_credentials({}, {"login_profile": "WEB_TEST_DEFAULT"}, environ) is True
        # profile 名里的 '-' 会被转成 '_'（与执行器一致）
        assert has_runtime_credentials(
            {}, {"login_profile": "uat-console"}, {"UAT_CONSOLE_USERNAME": "u",
                                                  "UAT_CONSOLE_PASSWORD": "p"}) is True

    def test_partial_credentials_do_not_count(self):
        assert has_runtime_credentials({}, {}, {"WEB_TEST_USERNAME": "u"}) is False
        assert has_runtime_credentials({}, {}, {"WEB_TEST_PASSWORD": "p"}) is False
        assert has_runtime_credentials({}, {}, {"WEB_TEST_USERNAME": "u",
                                                "WEB_TEST_PASSWORD": ""}) is False

    def test_config_env_wins_over_process_env(self):
        """显式注入优先（执行器用 setdefault，不会覆盖已有值）。"""
        config = {"env": {"WEB_TEST_USERNAME": "cfg", "WEB_TEST_PASSWORD": "cfgp"}}
        assert resolve_runtime_credentials(config, {}, {}) == ("cfg", "cfgp")

    def test_environment_variables_key_is_supported(self):
        """执行器同时支持 config['environment_variables']。"""
        assert has_runtime_credentials(
            {"environment_variables": CREDS}, {}, {}) is True


class TestProvenanceClassification:
    def test_attachment_missing_maps_to_not_registered(self):
        reason = ("脚本附件不存在（object_name=web-tests/PR-2/x.spec.ts），无法建立三要素绑定"
                  "（请先 save_web_test_script 保存并登记后再执行）")
        got = preflight_provenance_reason(reason)
        assert got["code"] == CODE_SCRIPT_NOT_REGISTERED
        assert reason in got["message"]      # 原始原因必须保留
        assert got["hint"]

    def test_hash_or_unregistered_maps_to_not_registered(self):
        for reason in ("授权失败: 脚本未登记（hash 不在 web_script_registry）",
                       "子功能 x 授权失败: 哈希与登记值不一致",
                       "解析子功能 x 当前脚本附件失败: boom"):
            assert classify_provenance_reason(reason) == CODE_SCRIPT_NOT_REGISTERED, reason

    def test_sub_function_requirement_maps_to_unbound(self):
        reason = "严格模式要求 sub_function_id/sub_function_ids：脚本执行必须绑定子功能当前附件"
        assert classify_provenance_reason(reason) == CODE_SUB_FUNCTION_UNBOUND

    def test_unknown_reason_falls_back_but_keeps_text(self):
        reason = "某种从未见过的新拒绝原因"
        got = preflight_provenance_reason(reason)
        assert got["code"] == CODE_PROVENANCE_REJECTED
        assert "从未见过" in got["message"]


class TestPayload:
    def test_build_preflight_shape(self):
        payload = build_preflight("x", "m", "h")
        assert payload == {"code": "x", "message": "m", "hint": "h"}
        assert "hint" not in build_preflight("x", "m")
