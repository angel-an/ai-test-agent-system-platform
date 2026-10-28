# -*- coding: utf-8 -*-
"""缺陷标题的「业务模块」位 + 描述里的 Base URL 口径。

两个都是 2026-10-28 从真实工单里反查出来的：

1. **模块位退化成接口串**：``scenario_name`` 对 API 场景就是
   ``POST /api/icdp/v1/memberCard/...``，历史实现"没有方括号就取前 10 个字符"，
   标题于是变成 ``【自动化测试】【API】【POST /api/...】断言失败…`` ——
   模块位等于没写，同模块的缺陷也无法按模块聚合。

2. **Base URL 写配置值**：``/runs/{id}/execute`` 的 body 是整体覆盖语义，
   覆盖后描述仍写 ``test_config`` 里的配置值（ATR-20261028-004：配置值带
   ``/yundt-saas-gateway``，实际打到域根），拿到工单的人按配置值复现不出问题。
"""

from __future__ import annotations

from app.services.idp_defect_service import IDPDefectService


class TestModuleExtraction:
    def test_api_scenario_falls_back_to_endpoint_path(self):
        """场景名是接口串时，模块取 endpoint 路径的最后三段。"""
        module = IDPDefectService._extract_module(
            "POST /api/icdp/v1/memberCard/storage-free-order/refund/calculate",
            "/api/icdp/v1/memberCard/storage-free-order/refund/calculate",
        )
        assert module == "storage-free-order/refund/calculate"

    def test_title_does_not_contain_http_verb_prefix(self):
        """回归：标题模块位不能是 `POST /api/...`。"""
        title = IDPDefectService._generate_title(
            scenario_name="POST /api/icdp/v1/memberCard/storage-free-order/refund/calculate",
            endpoint="/api/icdp/v1/memberCard/storage-free-order/refund/calculate",
            method="POST",
            failure_summary="断言失败：期望 < 300，实际 404",
            source_type="api",
        )
        assert "【POST /api" not in title
        assert "storage-free-order/refund/calculate" in title

    def test_bracketed_module_wins(self):
        assert (
            IDPDefectService._extract_module("【会员卡】退款试算 失败", "/api/x")
            == "会员卡"
        )

    def test_web_scenario_cuts_at_separator(self):
        """没有方括号也没有 endpoint 时按分隔符截断，而不是硬切 10 字。"""
        module = IDPDefectService._extract_module("限购商品管理-负路径，提交冲突商品")
        assert module == "限购商品管理"

    def test_unknown_when_nothing_usable(self):
        assert IDPDefectService._extract_module("") == "未知模块"

    def test_api_path_filters_noise_segments(self):
        """``api`` / ``v1`` / 纯数字段不算业务分段。"""
        assert (
            IDPDefectService._module_from_path("/api/v2/order/12345/detail")
            == "order/detail"
        )
        assert IDPDefectService._module_from_path("/api/v1/") is None

    def test_full_url_endpoint_is_parsed(self):
        assert (
            IDPDefectService._module_from_path(
                "https://sut.example.com/api/icdp/v1/goods/list?page=1"
            )
            == "icdp/goods/list"
        )


class TestBaseUrlLines:
    def test_executed_value_is_labelled_and_config_diff_is_flagged(self):
        lines = IDPDefectService._base_url_lines(
            {
                "base_url": "https://sut.example.com",
                "base_url_source": "executed",
                "configured_base_url": "https://sut.example.com/yundt-saas-gateway",
            }
        )
        text = "".join(lines)
        assert "https://sut.example.com（实际执行）" in text
        # 两者不一致必须点出来，否则读者会以为配置值就是实际执行值
        assert "配置 Base URL: https://sut.example.com/yundt-saas-gateway" in text
        assert "不一致" in text

    def test_single_line_when_same(self):
        lines = IDPDefectService._base_url_lines(
            {
                "base_url": "https://sut.example.com/gw",
                "base_url_source": "executed",
                "configured_base_url": "https://sut.example.com/gw/",
            }
        )
        assert len(lines) == 1
        assert "实际执行" in lines[0]

    def test_config_only_source_is_marked(self):
        lines = IDPDefectService._base_url_lines(
            {
                "base_url": "https://sut.example.com/gw",
                "base_url_source": "configured",
                "configured_base_url": "https://sut.example.com/gw",
            }
        )
        assert "配置值" in lines[0]

    def test_missing_base_url_is_explicit(self):
        lines = IDPDefectService._base_url_lines({})
        assert "N/A" in lines[0]


class TestRequestParamsRendering:
    def test_body_takes_precedence_over_headers(self):
        """请求体拿到后不该再和请求头混在一起（历史 bug 的可见症状）。"""
        out = IDPDefectService._sanitize_request_params(
            {
                "body": {"orderNo": "xxxxxxxxxxxxxx"},
                "headers": {"content-length": "28", "content-type": "application/json"},
            }
        )
        assert '"orderNo"' in out
        assert "content-length" not in out

    def test_headers_only_when_body_missing_are_labelled(self):
        out = IDPDefectService._sanitize_request_params(
            {"body": {}, "headers": {"content-type": "application/json"}}
        )
        assert out.startswith("（未采集到请求体，以下为请求头）")
        assert "content-type" in out

    def test_query_shown_for_bodiless_requests(self):
        out = IDPDefectService._sanitize_request_params(
            {"body": {}, "headers": {"accept": "*/*"}, "query": "page=2&size=10"}
        )
        assert "page=2&size=10" in out

    def test_non_json_body_string_is_kept(self):
        out = IDPDefectService._sanitize_request_params({"body": "orderNo=1&x=2"})
        assert "orderNo=1&x=2" in out
