"""Postman Environment / Collection 解析器测试。

使用内联 fixture（非真实美心文件），验证：
- Collection: folder 展开、URL/method/headers/body 提取、脚本断言提取
- Environment: 变量解析、secret 标记、UAT/生产识别、空值/禁用检查
- 安全: 日志中不出现 secret 明文
"""

from __future__ import annotations

import json
import logging
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.services.postman_collection_parser import PostmanCollectionParser
from app.services.postman_environment_parser import PostmanEnvironmentParser


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

COLLECTION_V21 = json.dumps({
    "info": {
        "name": "MX CRM API",
        "schema": "https://schema.getpostman.com/json/collection/v2.1.0/collection.json",
    },
    "item": [
        {
            "name": "会员",
            "item": [
                {
                    "name": "会员登录",
                    "request": {
                        "method": "POST",
                        "header": [
                            {"key": "Content-Type", "value": "application/json"},
                            {"key": "appid", "value": "{{appid}}"},
                        ],
                        "url": {
                            "raw": "{{base_url}}/api/json/memLogin",
                            "host": ["{{base_url}}"],
                            "path": ["api", "json", "memLogin"],
                        },
                        "body": {
                            "mode": "raw",
                            "raw": '{"LoginName":"test","Password":"pwd123"}',
                            "options": {"raw": {"language": "json"}},
                        },
                    },
                    "event": [
                        {
                            "listen": "prerequest",
                            "script": {
                                "exec": [
                                    "var ts = new Date().toISOString();",
                                    "pm.environment.set('ts', ts);",
                                ]
                            },
                        },
                        {
                            "listen": "test",
                            "script": {
                                "exec": [
                                    "var jsonData = pm.response.json();",
                                    "pm.expect(jsonData.Code).to.eql(0);",
                                    "pm.expect(jsonData.Data.SessionKey).to.exist;",
                                    "pm.environment.set('SessionKey', jsonData.Data.SessionKey);",
                                ]
                            },
                        },
                    ],
                },
                {
                    "name": "会员退出",
                    "request": {
                        "method": "POST",
                        "header": [{"key": "Content-Type", "value": "application/json"}],
                        "url": {
                            "raw": "{{base_url}}/api/json/memLogout",
                            "host": ["{{base_url}}"],
                            "path": ["api", "json", "memLogout"],
                        },
                        "body": {
                            "mode": "raw",
                            "raw": '{"SessionKey":"{{SessionKey}}"}',
                        },
                    },
                    "event": [
                        {
                            "listen": "test",
                            "script": {
                                "exec": [
                                    "pm.response.to.have.status(200);",
                                ]
                            },
                        },
                    ],
                },
            ],
        },
        {
            "name": "检查手机",
            "request": {
                "method": "POST",
                "url": {
                    "raw": "{{base_url}}/api/json/checkMobileDuplicate?CountryCode=852",
                    "host": ["{{base_url}}"],
                    "path": ["api", "json", "checkMobileDuplicate"],
                    "query": [{"key": "CountryCode", "value": "852"}],
                },
                "body": {
                    "mode": "raw",
                    "raw": '{"Mobile":"91234567"}',
                },
            },
        },
    ],
    "variable": [
        {"key": "base_url", "value": "https://uat.maxims.com"},
        {"key": "appid", "value": "mx-app-001"},
    ],
})

ENVIRONMENT_UAT = json.dumps({
    "name": "MX CRM UAT",
    "values": [
        {"key": "base_url", "value": "https://uat.maxims.com", "type": "default", "enabled": True},
        {"key": "appid", "value": "mx-app-001", "type": "default", "enabled": True},
        {"key": "appsecret", "value": "s3cr3t-v@lue!", "type": "secret", "enabled": True},
        {"key": "empty_var", "value": "", "type": "default", "enabled": True},
        {"key": "disabled_var", "value": "x", "type": "default", "enabled": False},
        {"key": "MX_APPID", "value": "mx-app-001", "type": "default", "enabled": True},
        {"key": "MX_APPSECRET", "value": "super-secret-key", "type": "default", "enabled": True},
    ],
})

ENVIRONMENT_PROD = json.dumps({
    "name": "MX CRM Production",
    "values": [
        {"key": "base_url", "value": "https://api.maxims.com", "type": "default"},
    ],
})


# ---------------------------------------------------------------------------
# Collection Parser
# ---------------------------------------------------------------------------

class TestPostmanCollectionParser:
    def test_parse_v21_name_and_version(self):
        result = PostmanCollectionParser().parse(COLLECTION_V21)
        assert result.name == "MX CRM API"
        assert result.version == "2.1"

    def test_folder_expansion(self):
        result = PostmanCollectionParser().parse(COLLECTION_V21)
        # 3 个请求：会员登录、会员退出、检查手机
        assert len(result.requests) == 3
        # folder 路径保留
        login = next(r for r in result.requests if r.name == "会员登录")
        assert login.folder_path == ["会员"]
        # 顶层请求 folder 为空
        check = next(r for r in result.requests if r.name == "检查手机")
        assert check.folder_path == []

    def test_url_extraction(self):
        result = PostmanCollectionParser().parse(COLLECTION_V21)
        login = next(r for r in result.requests if "memLogin" in (r.url_path or ""))
        assert login.method == "POST"
        assert login.url_path == "/api/json/memLogin"
        assert login.url_host == "{{base_url}}"

    def test_query_params(self):
        result = PostmanCollectionParser().parse(COLLECTION_V21)
        check = next(r for r in result.requests if "checkMobile" in (r.url_path or ""))
        assert len(check.query) == 1
        assert check.query[0]["key"] == "CountryCode"
        assert check.query[0]["value"] == "852"

    def test_headers(self):
        result = PostmanCollectionParser().parse(COLLECTION_V21)
        login = next(r for r in result.requests if "memLogin" in (r.url_path or ""))
        header_keys = [h["key"] for h in login.headers]
        assert "Content-Type" in header_keys
        assert "appid" in header_keys

    def test_body_raw_json(self):
        result = PostmanCollectionParser().parse(COLLECTION_V21)
        login = next(r for r in result.requests if "memLogin" in (r.url_path or ""))
        assert login.body["mode"] == "raw"
        assert login.body["json"]["LoginName"] == "test"

    def test_scripts_extracted(self):
        result = PostmanCollectionParser().parse(COLLECTION_V21)
        login = next(r for r in result.requests if "memLogin" in (r.url_path or ""))
        assert login.pre_request_script is not None
        assert "pm.environment.set" in login.pre_request_script
        assert login.test_script is not None
        assert "jsonData.Code" in login.test_script

    def test_assertion_extraction(self):
        result = PostmanCollectionParser().parse(COLLECTION_V21)
        login = next(r for r in result.requests if "memLogin" in (r.url_path or ""))
        # 至少提取到 Code == 0 的断言
        code_asserts = [a for a in login.assertions if a["type"] == "json_path" and "Code" in a["expression"]]
        assert len(code_asserts) >= 1
        assert code_asserts[0]["expected"] == "0"

    def test_variable_write_extraction(self):
        result = PostmanCollectionParser().parse(COLLECTION_V21)
        login = next(r for r in result.requests if "memLogin" in (r.url_path or ""))
        write_vars = [w["variable"] for w in login.variable_writes]
        assert "SessionKey" in write_vars
        assert "ts" in write_vars

    def test_status_assertion(self):
        result = PostmanCollectionParser().parse(COLLECTION_V21)
        logout = next(r for r in result.requests if "memLogout" in (r.url_path or ""))
        status_asserts = [a for a in logout.assertions if a["type"] == "status"]
        assert len(status_asserts) >= 1
        assert status_asserts[0]["expected"] == "200"

    def test_collection_variables(self):
        result = PostmanCollectionParser().parse(COLLECTION_V21)
        var_keys = [v["key"] for v in result.variables]
        assert "base_url" in var_keys
        assert "appid" in var_keys

    def test_base_url_inferred(self):
        result = PostmanCollectionParser().parse(COLLECTION_V21)
        assert result.base_url == "{{base_url}}"

    def test_invalid_json_raises(self):
        with pytest.raises(json.JSONDecodeError):
            PostmanCollectionParser().parse(b"not json")

    def test_v20_schema(self):
        v20 = json.dumps({
            "info": {"name": "T", "schema": "https://schema.getpostman.com/json/collection/v2.0.0/collection.json"},
            "item": [],
        })
        result = PostmanCollectionParser().parse(v20)
        assert result.version == "2.0"


# ---------------------------------------------------------------------------
# Environment Parser
# ---------------------------------------------------------------------------

class TestPostmanEnvironmentParser:
    def test_parse_name(self):
        result = PostmanEnvironmentParser().parse(ENVIRONMENT_UAT)
        assert result.name == "MX CRM UAT"

    def test_uat_detection(self):
        result = PostmanEnvironmentParser().parse(ENVIRONMENT_UAT)
        assert result.environment == "uat"

    def test_production_detection(self):
        result = PostmanEnvironmentParser().parse(ENVIRONMENT_PROD)
        assert result.environment == "production"

    def test_secret_by_type(self):
        result = PostmanEnvironmentParser().parse(ENVIRONMENT_UAT)
        appsecret = next(v for v in result.variables if v.key == "appsecret")
        assert appsecret.is_secret is True

    def test_secret_by_key_pattern(self):
        """MX_APPSECRET 的 type 是 default，但 key 含 SECRET，应识别为敏感。"""
        result = PostmanEnvironmentParser().parse(ENVIRONMENT_UAT)
        mx_secret = next(v for v in result.variables if v.key == "MX_APPSECRET")
        assert mx_secret.is_secret is True

    def test_non_secret_variable(self):
        result = PostmanEnvironmentParser().parse(ENVIRONMENT_UAT)
        base_url = next(v for v in result.variables if v.key == "base_url")
        assert base_url.is_secret is False
        assert base_url.value_ref == "env:base_url"

    def test_empty_value_detected(self):
        result = PostmanEnvironmentParser().parse(ENVIRONMENT_UAT)
        assert "empty_var" in result.empty_keys

    def test_disabled_detected(self):
        result = PostmanEnvironmentParser().parse(ENVIRONMENT_UAT)
        assert "disabled_var" in result.disabled_keys

    def test_secret_keys_listed_no_values(self):
        result = PostmanEnvironmentParser().parse(ENVIRONMENT_UAT)
        assert "appsecret" in result.secret_keys
        assert "MX_APPSECRET" in result.secret_keys
        # 确认 value_ref 不含明文
        for v in result.variables:
            assert "s3cr3t" not in v.value_ref
            assert "super-secret" not in v.value_ref

    def test_value_ref_format(self):
        result = PostmanEnvironmentParser().parse(ENVIRONMENT_UAT)
        for v in result.variables:
            assert v.value_ref.startswith("env:")

    def test_no_plaintext_in_logs(self, caplog):
        """验证日志中不出现 secret 明文。"""
        parser = PostmanEnvironmentParser()
        with caplog.at_level(logging.INFO, logger="app.services.postman_environment_parser"):
            parser.parse(ENVIRONMENT_UAT)
        log_text = caplog.text
        assert "s3cr3t-v@lue!" not in log_text
        assert "super-secret-key" not in log_text

    def test_invalid_json_raises(self):
        with pytest.raises(json.JSONDecodeError):
            PostmanEnvironmentParser().parse(b"not json")
