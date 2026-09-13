"""接口资产匹配与冲突检测引擎测试。

验证：
- URL 归一化：base_url/变量/尾斜杠/大小写
- method+path 主匹配、name 辅助
- 冲突分级：high/medium/low
- 覆盖：method/path/field/required/auth/assertion/env_missing/production_url/excel_only/collection_only
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.services.api_asset_matcher import (
    APIAssetMatcher,
    endpoint_key_from_path,
    extract_host,
    normalize_path,
)
from app.services.postman_collection_parser import PostmanCollectionParser


# ---------------------------------------------------------------------------
# URL 归一化
# ---------------------------------------------------------------------------

class TestNormalizePath:
    def test_full_url(self):
        assert normalize_path("https://uat.maxims.com/api/json/memLogin") == "/api/json/memlogin"

    def test_variable_prefix(self):
        assert normalize_path("{{base_url}}/api/json/memLogin") == "/api/json/memlogin"

    def test_trailing_slash(self):
        assert normalize_path("/api/json/memLogin/") == "/api/json/memlogin"

    def test_uppercase(self):
        assert normalize_path("/api/json/MemLogin") == "/api/json/memlogin"

    def test_query_string_removed(self):
        assert normalize_path("/api/json/checkMobile?country=852") == "/api/json/checkmobile"

    def test_empty(self):
        assert normalize_path("") == ""
        assert normalize_path(None) == ""

    def test_root(self):
        assert normalize_path("https://example.com/") == "/"

    def test_placeholder_host(self):
        assert normalize_path("webservices/api/json/memLogin") == "/api/json/memlogin"


class TestExtractHost:
    def test_real_host(self):
        assert extract_host("https://uat.maxims.com/api/x") == "uat.maxims.com"

    def test_placeholder(self):
        assert extract_host("https://example.com/api/x") is None

    def test_no_host(self):
        assert extract_host("/api/x") is None
        assert extract_host(None) is None


class TestEndpointKey:
    def test_basic(self):
        assert endpoint_key_from_path("/api/json/memLogin") == "api_memlogin"

    def test_already_prefixed(self):
        assert endpoint_key_from_path("/api/json/api_memLogin") == "api_memlogin"


# ---------------------------------------------------------------------------
# 匹配与冲突
# ---------------------------------------------------------------------------

def _excel_spec(name, method, path, req_fields=None, error_codes=None, auth=None):
    return {
        "name": name,
        "method_name": name,
        "http_method": method,
        "path": path,
        "request": {"fields": req_fields or []},
        "response": {"fields": [], "error_codes": error_codes or []},
        "auth": auth or {"type": "mx_md5"},
    }


COLLECTION = json.dumps({
    "info": {"name": "T", "schema": "https://schema.getpostman.com/json/collection/v2.1.0/collection.json"},
    "item": [
        {
            "name": "会员登录",
            "request": {
                "method": "POST",
                "header": [
                    {"key": "Content-Type", "value": "application/json"},
                    {"key": "appid", "value": "{{appid}}"},
                ],
                "url": {"raw": "{{base_url}}/api/json/memLogin", "host": ["{{base_url}}"], "path": ["api", "json", "memLogin"]},
                "body": {"mode": "raw", "raw": '{"LoginName":"t","Password":"x","TS":"{{ts}}"}'},
            },
            "event": [{"listen": "test", "script": {"exec": ["pm.expect(jsonData.Code).to.eql(0);"]}}],
        },
        {
            "name": "仅Collection接口",
            "request": {
                "method": "GET",
                "url": {"raw": "{{base_url}}/api/json/onlyInCollection", "host": ["{{base_url}}"], "path": ["api", "json", "onlyInCollection"]},
            },
        },
        {
            "name": "生产地址请求",
            "request": {
                "method": "POST",
                "header": [{"key": "appid", "value": "{{appid}}"}],
                "url": {"raw": "https://api.maxims.com/api/json/memLogin", "host": ["api.maxims.com"], "path": ["api", "json", "memLogin"]},
                "body": {"mode": "raw", "raw": '{"LoginName":"t"}'},
            },
        },
    ],
})


class TestMatcher:
    def _match(self, excel_specs, coll_json, env_vars=None):
        coll = PostmanCollectionParser().parse(coll_json) if coll_json else None
        return APIAssetMatcher().match(excel_specs, coll, env_vars)

    def test_exact_match(self):
        excel = [_excel_spec("api_memLogin", "POST", "/api/json/memLogin")]
        result = self._match(excel, COLLECTION, env_vars=["base_url", "appid", "ts"])
        # memLogin 匹配成功（Collection 有 3 个请求，其中 2 个 path 相同 method 相同）
        assert result.total >= 1
        contract = next(c for c in result.contracts if c["endpoint_key"] == "api_memlogin")
        assert "excel_mx" in contract["sources"]
        assert "postman_collection" in contract["sources"]

    def test_excel_only_conflict(self):
        excel = [_excel_spec("api_memLogout", "POST", "/api/json/memLogout")]
        result = self._match(excel, COLLECTION, env_vars=[])
        types = [c.conflict_type for c in result.conflicts]
        assert "excel_only" in types

    def test_collection_only_conflict(self):
        excel = [_excel_spec("api_x", "POST", "/api/json/x")]
        result = self._match(excel, COLLECTION, env_vars=[])
        types = [c.conflict_type for c in result.conflicts]
        assert "collection_only" in types

    def test_env_missing_high(self):
        excel = [_excel_spec("api_memLogin", "POST", "/api/json/memLogin")]
        result = self._match(excel, COLLECTION, env_vars=["base_url"])  # 缺 appid, ts
        env_conflicts = [c for c in result.conflicts if c.conflict_type == "env_missing"]
        assert len(env_conflicts) >= 1
        assert env_conflicts[0].severity == "high"

    def test_production_url_high(self):
        excel = [_excel_spec("api_memLogin", "POST", "/api/json/memLogin")]
        result = self._match(excel, COLLECTION, env_vars=["base_url", "appid", "ts"])
        prod_conflicts = [c for c in result.conflicts if c.conflict_type == "production_url"]
        assert len(prod_conflicts) >= 1
        assert prod_conflicts[0].severity == "high"

    def test_required_conflict(self):
        excel = [_excel_spec("api_memLogin", "POST", "/api/json/memLogin",
                             req_fields=[{"name": "LoginName", "required": True, "type": "String"},
                                          {"name": "MissingField", "required": True, "type": "String"}])]
        result = self._match(excel, COLLECTION, env_vars=["base_url", "appid", "ts"])
        req_conflicts = [c for c in result.conflicts if c.conflict_type == "required"]
        assert any("MissingField" in c.message for c in req_conflicts)
        # LoginName 在 body 中，不应报缺失
        assert not any("LoginName" in c.message for c in req_conflicts)

    def test_field_type_conflict(self):
        excel = [_excel_spec("api_memLogin", "POST", "/api/json/memLogin",
                             req_fields=[{"name": "LoginName", "required": True, "type": "Int"}])]
        result = self._match(excel, COLLECTION, env_vars=["base_url", "appid", "ts"])
        field_conflicts = [c for c in result.conflicts if c.conflict_type == "field"]
        assert any("LoginName" in c.message for c in field_conflicts)

    def test_no_conflict_when_aligned(self):
        """字段对齐且 env 齐全时，memLogin 契约（排除生产 URL 请求）无 high/medium。"""
        excel = [_excel_spec("api_memLogin", "POST", "/api/json/memLogin",
                             req_fields=[{"name": "LoginName", "required": True, "type": "String"}],
                             error_codes=[{"code": "0", "meaning": "OK"}])]
        result = self._match(excel, COLLECTION, env_vars=["base_url", "appid", "ts"])
        # 生产 URL 请求也归到同一契约，允许 production_url high；其余不应有 high/medium
        bad = [c for c in result.conflicts
               if c.contract_key == "POST /api/json/memlogin"
               and c.severity in ("high", "medium")
               and c.conflict_type != "production_url"]
        assert len(bad) == 0

    def test_conflicts_by_severity(self):
        """excel 与 collection 存在共同契约但 env 缺失时产生 high。"""
        excel = [_excel_spec("api_memLogin", "POST", "/api/json/memLogin")]
        result = self._match(excel, COLLECTION, env_vars=[])  # 缺 base_url/appid/ts
        counts = result.conflicts_by_severity()
        assert counts.get("high", 0) >= 1  # env_missing + production_url

    def test_no_collection(self):
        excel = [_excel_spec("api_a", "POST", "/api/json/a"),
                  _excel_spec("api_b", "POST", "/api/json/b")]
        result = self._match(excel, None, env_vars=[])
        assert result.total == 2
        assert all(c.conflict_type == "excel_only" for c in result.conflicts)
