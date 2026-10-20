# -*- coding: utf-8 -*-
"""复核 F1 回归：信封字段必须**按数据解析**，不得写死 resultCode。

缺陷（2026-10-01 复核实测）：生成器把「信封字段＝resultCode 且 === '0'」写进基础模板，
于是把 PR-2 的响应结构套到了所有项目上。平台上 APIEndpoint 共 1892 条、分布在 14 个项目
（PR-2 只有 17 条），非 PR-2 项目声明的是 `Code` —— 已复现"SUT 正常返回成功信封、平台报断言失败"。

本文件锁住修复后的行为：
* 解析顺序：接口 custom_config 显式声明 → 已导入 responses 推断 → 未声明（返回 None）；
* 歧义（两个已知键都命中）→ None（宁可不判，也不猜错）；
* 未声明时脚本**降级并留机读痕迹**（ENVELOPE_CHECK_SKIPPED），而不是硬失败、也不是静默通过；
* 分页契约接口仍按契约声明判 resultCode（P5 的越界页保护不退化）。
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.services.api_case_link_service import APICaseLinkService
from app.services.api_pagination_contracts import (
    PAGINATION_CONTRACTS,
    PaginationContract,
    resolve_pagination_contract,
)
from app.services.api_response_envelope import resolve_envelope_field


def _endpoint(path: str, *, custom_config=None, responses=None) -> SimpleNamespace:
    return SimpleNamespace(method="GET", path=path, request_body=None, parameters=[],
                           custom_config=custom_config or {}, responses=responses or {})


def _render(endpoint: SimpleNamespace) -> str:
    svc = object.__new__(APICaseLinkService)
    return svc._generate_playwright_script(endpoint)


# --------------------------------------------------------------------------- #
# 一、解析规则
# --------------------------------------------------------------------------- #
class TestResolveEnvelopeField:
    def test_custom_config_declaration_wins(self):
        ep = _endpoint("/v1/x", custom_config={"response_envelope_field": "resultCode"},
                       responses={"Code": {}})
        assert resolve_envelope_field(ep) == "resultCode"

    def test_blank_declaration_is_ignored(self):
        ep = _endpoint("/v1/x", custom_config={"response_envelope_field": "   "}, responses={"Code": {}})
        assert resolve_envelope_field(ep) == "Code"

    def test_infers_mx_code_shape(self):
        """MX/Aigens 系：响应结构里声明 Code（整数）→ 推断为 Code，而不是 resultCode。"""
        ep = _endpoint("/api/json/api_memLogin", responses={"200": {"Code": {"type": "Integer"}}})
        assert resolve_envelope_field(ep) == "Code"

    def test_infers_result_code_shape(self):
        ep = _endpoint("/v1/x", responses={"200": {"resultCode": {"type": "String"}}})
        assert resolve_envelope_field(ep) == "resultCode"

    def test_field_list_style_is_recognized(self):
        """契约式字段清单写法（[{'name': 'Code'}]）同样能识别。"""
        ep = _endpoint("/v1/x", responses={"fields": [{"name": "Code", "type": "Integer"}]})
        assert resolve_envelope_field(ep) == "Code"

    def test_ambiguous_shape_is_not_guessed(self):
        """两个已知键都命中 → 不猜（返回 None），免得把结论张冠李戴。"""
        ep = _endpoint("/v1/x", responses={"a": {"Code": {}}, "b": {"resultCode": {}}})
        assert resolve_envelope_field(ep) is None

    def test_unknown_shape_returns_none(self):
        assert resolve_envelope_field(_endpoint("/v1/x", responses={"data": {"list": []}})) is None
        assert resolve_envelope_field(_endpoint("/v1/x")) is None


# --------------------------------------------------------------------------- #
# 二、渲染结果（写出脚本长什么样）
# --------------------------------------------------------------------------- #
class TestRenderedEnvelope:
    def test_mx_endpoint_asserts_its_own_field_not_result_code(self):
        script = _render(_endpoint("/api/json/api_memLogin", responses={"Code": {}}))
        assert 'const ENVELOPE_FIELD: string | null = "Code";' in script
        assert "ENVELOPE_FIELD]=undefined" not in script  # 语法自检：注入的是字段名而非拼坏的下标
        assert "${{ENVELOPE_FIELD}}=" not in script

    def test_undeclared_endpoint_degrades_with_a_machine_readable_marker(self):
        """未声明信封形状 → 不硬判业务结果，但**必须**留机读痕迹（不静默通过）。"""
        script = _render(_endpoint("/v1/some/other/endpoint"))
        assert 'const ENVELOPE_FIELD: string | null = null;' in script
        assert "ENVELOPE_CHECK_SKIPPED" in script
        assert "envelope_field_not_declared" in script
        # 降级不是"少判一层就完事"：可解析性仍然判
        assert "响应信封可解析" in script

    def test_undeclared_endpoint_emits_report_annotation_not_just_console(self):
        """② 降级可见性：未声明信封时必须在**报告**里留注解（黄灯），不能只有 console.log。

        同一文件里两种降级口径曾不一致：分页契约降级 push annotation，信封降级只写
        console.log → 报告里看不出"这轮根本没判业务结果"，表现为绿灯假通过。
        """
        script = _render(_endpoint("/v1/some/other/endpoint"))
        assert "testInfo.annotations.push" in script
        assert "envelope_check_skipped" in script
        # 要 push 注解就必须注入 testInfo；漏注入会让渲染产物无法编译。
        assert "async ({ request }, testInfo)" in script

    def test_every_rendered_script_injects_test_info(self):
        """反向对照（编译约束）：降级注解是**静态模板文本**，任何产物都引用 testInfo。

        因此所有产物都必须注入 testInfo —— 漏注入会让"信封字段已声明"的端点也编译出
        TS2304（这正是 `test_rendered_scripts_compile` 抓到的回归；不能只给降级端点注入）。
        """
        for ep in (
            _endpoint("/v1/x", custom_config={"response_envelope_field": "resultCode"}),
            _endpoint("/v1/some/other/endpoint"),
            _endpoint("/v1/purchaseLimit/list"),
        ):
            script = _render(ep)
            assert "async ({ request }, testInfo)" in script

    def test_declared_endpoint_keeps_the_business_result_assertion(self):
        script = _render(_endpoint("/v1/x", custom_config={"response_envelope_field": "resultCode"}))
        assert 'const ENVELOPE_FIELD: string | null = "resultCode";' in script
        assert "AUTH_OR_BUSINESS_ENVELOPE" in script
        # 业务码取自**声明字段**，而不是写死的 resultCode 下标
        assert "envelope[ENVELOPE_FIELD] !== undefined" in script
        assert "envelope.resultCode" not in script

    def test_pagination_endpoint_uses_the_declared_contract_field(self):
        """P5 的越界页保护不退化：契约声明的 resultCode 仍被判定。"""
        contract = resolve_pagination_contract("/v1/purchaseLimit/list")
        assert contract is not None and contract.envelope_field == "resultCode"
        script = _render(_endpoint("/v1/purchaseLimit/list"))
        assert 'const ENVELOPE_FIELD: string | null = "resultCode";' in script
        oor = script.split("const oorCode =", 1)[1]
        before_contract = oor.split("checkPaginationContract(", 1)[0]
        assert "AUTH_OR_BUSINESS_ENVELOPE" in before_contract
        assert "toBe('0')" in before_contract


# --------------------------------------------------------------------------- #
# 三、配置层：契约声明本身要合法
# --------------------------------------------------------------------------- #
class TestContractEnvelopeDeclaration:
    def test_all_contracts_declare_an_envelope_field(self):
        assert all(c.envelope_field.strip() for c in PAGINATION_CONTRACTS)

    def test_envelope_field_is_exported_in_to_dict(self):
        payload = PaginationContract("/v1/x", "generic_params", "structure").to_dict()
        assert payload["envelope_field"] == "resultCode"

    def test_envelope_field_is_overridable_per_contract(self):
        contract = PaginationContract("/v1/x", "generic_params", "structure", envelope_field="Code")
        assert contract.to_dict()["envelope_field"] == "Code"


@pytest.mark.parametrize("path", ["/v1/purchaseLimit/list", "/v1/store/binding/list"])
def test_no_hard_coded_result_code_in_pagination_render(path):
    """渲染产物里不得再出现"写死的 resultCode 下标判定"。"""
    script = _render(_endpoint(path))
    assert "oorEnvelope.resultCode" not in script
    assert "envelope.resultCode" not in script
