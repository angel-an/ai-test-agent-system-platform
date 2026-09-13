# -*- coding: utf-8 -*-
"""端点 security 字段形状兼容性回归测试。

缺陷背景：统一导入曾把鉴权配置整体写入 ``api_endpoints.security``
（``{"type": "mx_md5", ...}``），而 ``APIEndpointResponse.security`` 声明为
``list[dict] | None``。只要库里存在一条 dict 形状的记录，
``GET /projects/{id}/api-endpoints`` 就会抛 ``ResponseValidationError``
→ 整表 500（实测 18 个端点触发）。该接口是前端「刷新后端点是否仍在」的
唯一入口，因此必须同时保证：
1. 写入侧统一存 list（``_security_as_list``）；
2. 读取侧对两种形状都兼容（``normalize_security``）。
"""

from __future__ import annotations

import pytest

from app.schemas.api_endpoint import APIEndpointResponse, normalize_security
from app.services.api_bundle_service import _security_as_list


class TestNormalizeSecurityReadSide:
    """读取侧：dict 形状必须被兼容，不能因为存量数据让列表接口 500。"""

    def test_dict_becomes_single_item_list(self):
        payload = {"type": "mx_md5", "appid_ref": "env:MX_APPID"}
        assert normalize_security(payload) == [payload]

    def test_empty_dict_degrades_to_none(self):
        assert normalize_security({}) is None

    def test_list_is_preserved(self):
        items = [{"type": "apiKey"}, {"type": "oauth2"}]
        assert normalize_security(items) == items

    def test_list_with_non_dict_items_filters_them(self):
        assert normalize_security([{"type": "apiKey"}, "bad", 3]) == [{"type": "apiKey"}]

    def test_list_of_only_non_dict_degrades_to_none(self):
        assert normalize_security(["bad", 3]) is None

    def test_none_stays_none(self):
        assert normalize_security(None) is None

    def test_other_type_is_not_silently_dropped(self):
        # 误写成字符串时不能变成 500，也不是静默丢弃
        assert normalize_security("mx_md5") == [{"value": "mx_md5"}]

    def test_response_model_accepts_dict_shape(self):
        """端到端：dict 形状经响应模型校验后应为 list，不再抛异常。"""
        model = APIEndpointResponse.model_validate({
            "id": "00000000-0000-0000-0000-000000000001",
            "project_id": "00000000-0000-0000-0000-000000000002",
            "folder_id": None,
            "schema_file_id": None,
            "display_name": "POST /api/demo",
            "path": "/api/demo",
            "method": "POST",
            "summary": None,
            "description": None,
            "tag_group": None,
            "parameters": None,
            "request_body": None,
            "responses": None,
            "security": {"type": "mx_md5", "secret_ref": "env:MX_APPSECRET"},
            "tags": None,
            "custom_config": None,
            "total_test_cases": 0,
            "total_test_runs": 0,
            "last_run_status": None,
            "created_at": "2026-09-13T00:00:00",
            "updated_at": None,
        })
        assert isinstance(model.security, list)
        assert model.security[0]["type"] == "mx_md5"

    def test_response_model_accepts_list_shape(self):
        model = APIEndpointResponse.model_validate({
            "id": "00000000-0000-0000-0000-000000000001",
            "project_id": "00000000-0000-0000-0000-000000000002",
            "folder_id": None,
            "schema_file_id": None,
            "display_name": "POST /api/demo",
            "path": "/api/demo",
            "method": "POST",
            "summary": None,
            "description": None,
            "tag_group": None,
            "parameters": None,
            "request_body": None,
            "responses": None,
            "security": [{"type": "mx_md5"}],
            "tags": None,
            "custom_config": None,
            "total_test_cases": 0,
            "total_test_runs": 0,
            "last_run_status": None,
            "created_at": "2026-09-13T00:00:00",
            "updated_at": None,
        })
        assert model.security == [{"type": "mx_md5"}]


class TestSecurityWriteSide:
    """写入侧：统一导入必须存 list，避免继续产生脏数据。"""

    def test_dict_auth_becomes_list(self):
        assert _security_as_list({"type": "mx_md5"}) == [{"type": "mx_md5"}]

    def test_none_auth_stays_none(self):
        assert _security_as_list(None) is None
        assert _security_as_list({}) is None

    def test_list_auth_preserved(self):
        assert _security_as_list([{"type": "apiKey"}]) == [{"type": "apiKey"}]

    def test_round_trip_is_stable(self):
        """写入侧与读取侧对同一输入必须给出相同结果（避免读出来形状漂移）。"""
        for auth in ({"type": "mx_md5"}, [{"type": "apiKey"}], None, {}):
            assert _security_as_list(auth) == normalize_security(auth)
