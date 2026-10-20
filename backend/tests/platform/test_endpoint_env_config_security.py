# -*- coding: utf-8 -*-
"""端点 env_config 密钥安全与执行器环境变量覆盖回归测试。

背景：为让 AI 智能体 execute_api_script 复用执行环境，env_config（base_url/
appid/appsecret/测试数据）持久化到 api_endpoints.custom_config.env_config。
由此引入两个安全/正确性要求：

1. 接口响应不得返回明文密钥 —— APIEndpointResponse 序列化时把 env_config 里的
   敏感值（appsecret/secret/token 等）脱敏为 ***；存储侧用 env_crypto 加密
   （enc:v1: 前缀），执行器在服务端解密还原真实值注入子进程。
2. 执行器 _build_subprocess_env 的覆盖规则 —— 配置值非空则覆盖、空/空白继承
   后端环境变量（避免空串覆盖导致脚本 test.skip 假执行）。
"""

from __future__ import annotations

from app.schemas.api_endpoint import APIEndpointResponse
from app.services.api_endpoint_script_executor import _build_subprocess_env
from app.utils.env_crypto import decrypt_value, encrypt_value, is_encrypted


def _endpoint_with_env_config(env_config: dict) -> dict:
    """构造一个带 custom_config.env_config 的最小端点字典，用于 response 校验。"""
    return {
        "id": "e118dfd5-4e7f-4c76-a78e-355daa2b1f25",
        "project_id": "2727138d-017f-4fd5-8bec-af158c16bd8f",
        "folder_id": None,
        "schema_file_id": None,
        "display_name": "POST /cop/json",
        "path": "/cop/json",
        "method": "POST",
        "summary": None,
        "description": None,
        "tag_group": None,
        "parameters": None,
        "request_body": None,
        "responses": None,
        "security": None,
        "tags": None,
        "custom_config": {"env_config": env_config},
        "total_test_cases": 0,
        "total_test_runs": 0,
        "last_run_status": None,
        "created_at": "2026-09-17T00:00:00",
        "updated_at": None,
    }


class TestResponseNeverReturnsPlaintextSecret:
    """接口响应不得返回明文密钥（发现 1 的回归）。"""

    def test_appsecret_redacted_to_stars(self):
        ep = _endpoint_with_env_config(
            {"base_url": "https://uat.example.com", "appid": "AID", "appsecret": "SECRET123"}
        )
        out = APIEndpointResponse.model_validate(ep).model_dump(mode="json")
        ec = out["custom_config"]["env_config"]
        assert ec["appsecret"] == "***"
        assert "SECRET123" not in str(out)

    def test_non_sensitive_fields_preserved(self):
        ep = _endpoint_with_env_config(
            {"base_url": "https://uat.example.com", "appid": "AID", "appsecret": "SECRET"}
        )
        out = APIEndpointResponse.model_validate(ep).model_dump(mode="json")
        ec = out["custom_config"]["env_config"]
        # 非敏感字段保留，便于前端回显
        assert ec["base_url"] == "https://uat.example.com"
        assert ec["appid"] == "AID"

    def test_nested_env_secret_redacted(self):
        ep = _endpoint_with_env_config(
            {"base_url": "https://x", "env": {"api_token": "TOK", "TEST_MEMBER_ID": "M1"}}
        )
        out = APIEndpointResponse.model_validate(ep).model_dump(mode="json")
        env = out["custom_config"]["env_config"]["env"]
        assert env["api_token"] == "***"
        assert env["TEST_MEMBER_ID"] == "M1"  # 非敏感测试数据保留

    def test_no_env_config_passes_through(self):
        ep = _endpoint_with_env_config({})
        ep["custom_config"] = {"other": "value"}
        out = APIEndpointResponse.model_validate(ep).model_dump(mode="json")
        assert out["custom_config"] == {"other": "value"}


class TestEnvCryptoRoundtrip:
    """存储加密 ↔ 执行器解密 往返一致性。"""

    def test_encrypt_decrypt_roundtrip(self):
        secret = "REAL_APPSECRET_456"
        assert decrypt_value(encrypt_value(secret)) == secret

    def test_encrypted_has_prefix(self):
        assert is_encrypted(encrypt_value("x"))

    def test_plaintext_passthrough(self):
        # 历史明文数据（无前缀）原样返回，兼容迁移期
        assert decrypt_value("plain-value") == "plain-value"

    def test_executor_decrypts_encrypted_secret(self):
        stored = {"appid": "AID", "appsecret": encrypt_value("REAL_SECRET")}
        env = _build_subprocess_env(stored, base_env={}, node_bin="")
        assert env["MX_APPSECRET"] == "REAL_SECRET"
        assert env["MX_APPID"] == "AID"


class TestBuildSubprocessEnvOverrideRules:
    """执行器环境变量覆盖规则（发现：空值继承、非空覆盖）。"""

    def test_non_empty_config_overrides_base(self):
        env = _build_subprocess_env(
            {"appid": "CFG_AID", "appsecret": "CFG_SEC"},
            base_env={"MX_APPID": "BASE_AID", "MX_APPSECRET": "BASE_SEC"},
            node_bin="",
        )
        assert env["MX_APPID"] == "CFG_AID"
        assert env["MX_APPSECRET"] == "CFG_SEC"

    def test_empty_config_inherits_base(self):
        # 配置缺失 → 继承后端环境变量，不用空串覆盖（避免脚本 test.skip 假执行）
        env = _build_subprocess_env(
            {}, base_env={"MX_APPID": "BASE_AID", "MX_APPSECRET": "BASE_SEC"}, node_bin=""
        )
        assert env["MX_APPID"] == "BASE_AID"
        assert env["MX_APPSECRET"] == "BASE_SEC"

    def test_blank_config_inherits_base(self):
        env = _build_subprocess_env(
            {"appid": "   ", "appsecret": ""},
            base_env={"MX_APPID": "BASE_AID", "MX_APPSECRET": "BASE_SEC"},
            node_bin="",
        )
        assert env["MX_APPID"] == "BASE_AID"
        assert env["MX_APPSECRET"] == "BASE_SEC"

    def test_base_url_and_test_vars_passed(self):
        env = _build_subprocess_env(
            {
                "base_url": "https://uat.example.com",
                "existing_mobile": "13800000000",
                "env": {"TEST_MEMBER_ID": "M1", "TEST_SESSION_KEY": "SK"},
            },
            base_env={},
            node_bin="",
        )
        assert env["TEST_MEMBER_ID"] == "M1"
        assert env["TEST_SESSION_KEY"] == "SK"
        assert env["EXISTING_MOBILE"] == "13800000000"

    def test_encrypted_test_var_decrypted(self):
        env = _build_subprocess_env(
            {"env": {"api_secret": encrypt_value("DEC_SECRET")}},
            base_env={},
            node_bin="",
        )
        assert env["api_secret"] == "DEC_SECRET"
