# -*- coding: utf-8 -*-
"""N3（2026-10-18）：列表域识别 + 列契约解析。

为什么要专门测这两个纯函数：
1. 真实库里"列表类用例"72/72 断言数为 0，只有规则体里的列名文字。历史实现把它们当
   narrative 静默吞掉，skip 原因还统一写"无定位器"——**把人引向错误的修法**（去配 fields）。
   所以"列表域"的识别与分账必须可回归。
2. 列配置在 CS 侧是 `columns_22` / `requirement_fields_16` 这类**一次性键名**（带列数后缀），
   不在 field_locators 里。契约解析要能读出它们，并且在读不出来时**说清原因**，
   而不是返回空数组让人以为"没配"。
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.services.web_case_compiler import (
    build_form_plan,
    compile_cases,
    is_list_scope_field,
    parse_columns,
)


class TestIsListScopeField:
    @pytest.mark.parametrize("name", [
        "列表顯示欄位", "列表", "列表排序", "列表搜索", "篩選區", "筛选", "分頁", "分页页码",
    ])
    def test_list_scope_names(self, name):
        assert is_list_scope_field(name) is True

    @pytest.mark.parametrize("name", ["店鋪名稱(EN)", "商品編號", "Attachment", "", None])
    def test_form_fields_are_not_list_scope(self, name):
        assert is_list_scope_field(name) is False

    @pytest.mark.parametrize("name", [
        "地圖搜索框", "筛选值", "搜索關鍵詞", "搜索框", "排序值",
    ])
    def test_real_form_controls_are_not_list_scope(self, name):
        """真实配置里这几类**是表单控件**（148 个子功能定位器实测）。

        按关键词包含匹配会把它们判成"列表域"，于是"缺定位器"被错报成"缺生成器"，
        改配置的人会去建列契约而不是补 fields —— 方向直接错。
        """
        assert is_list_scope_field(name) is False


class TestParseColumnsExplicit:
    def test_dict_contract_with_items(self):
        loc = {"columns": {
            "table_scope": ".ant-table-thead th",
            "items": [
                {"key": "商品編號", "header_text": "Item Code"},
                {"header_text": "Item Name"},
            ],
        }}
        contract, rejects = parse_columns(loc)
        assert rejects == []
        assert contract["source"] == "explicit"
        assert contract["table_scope"] == ".ant-table-thead th"
        assert [i["header_text"] for i in contract["items"]] == ["Item Code", "Item Name"]
        # key 缺省时取 header_text；显式 key 会进 aliases（供中文断言名匹配）
        assert contract["items"][1]["key"] == "Item Name"
        assert "商品編號" in contract["items"][0]["aliases"]
        assert contract["column_count"] == 2

    def test_plain_string_list(self):
        contract, rejects = parse_columns({"columns": ["Case No.", "Status"]})
        assert rejects == []
        assert contract["column_count"] == 2
        assert contract["items"][0] == {"key": "Case No.", "header_text": "Case No.", "aliases": []}

    def test_table_scope_falls_back_to_locators(self):
        contract, _ = parse_columns({
            "columns": ["A"], "locators": {"table_thead": ".ant-table-thead th"},
        })
        assert contract["table_scope"] == ".ant-table-thead th"

    def test_duplicate_header_kept_once(self):
        contract, _ = parse_columns({"columns": ["Status", {"header_text": "Status"}]})
        assert contract["column_count"] == 1


class TestParseColumnsLegacyCS:
    def _loc(self):
        return {
            "columns_22": ["（勾选列）", "Case No.", "Status", "Owner"],
            "requirement_fields_16": {"工单编号": "Case No.", "工单状态": "Status",
                                      "创建人名称": "Owner", "备注": "Remark"},
            "sortable_columns": "实测 22 列表头全部 sorter=true",
            "locators": {"table_thead": ".ant-table-thead th"},
        }

    def test_legacy_keys_are_read(self):
        contract, rejects = parse_columns(self._loc())
        assert rejects == []
        assert contract["source"] == "legacy_cs"
        assert contract["column_count"] == 4
        by_header = {i["header_text"]: i for i in contract["items"]}
        # 中文名进 key，英文表头进 header_text —— 断言里写中文也能对上
        assert by_header["Case No."]["key"] == "工单编号"
        # 没有中文映射的列不丢，key 退回表头
        assert by_header["（勾选列）"]["key"] == "（勾选列）"
        assert "（勾选列）" in by_header

    def test_legacy_miss_is_reported_not_swallowed(self):
        _, _ = parse_columns(self._loc())
        contract, _ = parse_columns(self._loc())
        # 映射里有 Remark、表头数组里没有 → 必须留痕（否则配置方以为少了一列是正常的）
        assert any("Remark" in n for n in contract["notes"]), contract["notes"]

    def test_sortable_is_note_not_column(self):
        contract, _ = parse_columns(self._loc())
        assert all("sorter" not in i["header_text"] for i in contract["items"])
        assert any("sortable_columns" in n for n in contract["notes"])

    def test_legacy_via_columns_legacy_carrier(self):
        """编译链把 custom_config 顶层的旧键装在 columns_legacy 里带进来。"""
        contract, _ = parse_columns({"columns_legacy": self._loc()})
        assert contract["source"] == "legacy_cs"
        assert contract["column_count"] == 4
        assert contract["table_scope"] == ".ant-table-thead th"


class TestParseColumnsEdgeCases:
    def test_no_config_returns_none(self):
        assert parse_columns({}) == (None, [])
        assert parse_columns(None) == (None, [])

    def test_malformed_items_do_not_raise(self):
        contract, rejects = parse_columns({"columns": {"items": [123, {"nope": 1}, "Good Col"]}})
        assert contract["column_count"] == 1          # 只有合法那条
        assert len(rejects) == 2                       # 畸形条目逐条登记
        assert all("reason" in r for r in rejects)

    def test_items_not_a_list_is_rejected(self):
        contract, rejects = parse_columns({"columns": {"items": "oops"}})
        assert contract is None
        assert rejects and "items" in rejects[0]["reason"]


def _case(identifier: str, *, field: str, rule_id: str = "", assertions=None):
    custom: dict = {}
    if rule_id:
        custom["requirement_rule_id"] = rule_id
    if field:
        custom["requirement_rule"] = {"field": field, "required": False}
    if assertions:
        custom["assertions"] = assertions
    return SimpleNamespace(
        identifier=identifier, name=identifier, module="YL-主數據管理-店鋪",
        description="", custom_fields=custom, test_case_steps=[],
    )


FIELDS = {"fields": {"店鋪名稱(EN)": {"kind": "input", "selector_hint": "input[name='storeNameEn']"}}}


class TestListScopeSkipRouting:
    def test_list_rule_without_assertions_is_explicit_skip(self):
        """列表域规则（无断言）不得静默：必须登记 scope=list 且说明缺生成器。"""
        plan = build_form_plan(
            compile_cases([_case("TC-L1", field="列表顯示欄位", rule_id="LST-1")]), FIELDS,
        )
        assert plan is not None
        assert plan["check_count"] == 0
        assert plan["executable"] is False
        assert plan["skipped_by_scope"] == {"list": 1}
        entry = plan["skipped"][0]
        assert entry["scope"] == "list"
        assert "生成器" in entry["reason"]
        # 关键：不能再说"无定位器"——那会把人引向去配 fields
        assert "无定位器" not in entry["reason"]

    def test_narrative_case_still_silent(self):
        """真正的叙述型用例（无列表域字段）保持静默，不制造噪声。"""
        assert build_form_plan(
            compile_cases([_case("TC-N1", field="營業狀態", rule_id="FR-9")]), FIELDS,
        ) is None

    def test_list_assertion_field_is_not_reported_as_missing_locator(self):
        plan = build_form_plan(
            compile_cases([_case("TC-L2", field="列表排序",
                                 assertions=[{"kind": "order", "field": "列表排序",
                                              "expect": "desc"}])]),
            FIELDS,
        )
        assert plan["skipped_by_scope"] == {"list": 1}
        entry = plan["skipped"][0]
        assert entry["scope"] == "list"
        assert "无定位器" not in entry["reason"]
        # 列表域不提示"相近字段"——表单定位器里不可能有列
        assert entry["suggestions"] == []

    def test_form_missing_locator_keeps_old_semantics(self):
        """表单字段缺定位器仍走原口径（含相近字段建议），不能被列表域逻辑带偏。"""
        plan = build_form_plan(
            compile_cases([_case("TC-F1", field="店鋪名稱",
                                 assertions=[{"kind": "max_length", "field": "店鋪名稱",
                                              "data": "31", "expect": "reject"}])]),
            FIELDS,
        )
        assert plan["skipped_by_scope"] == {"form": 1}
        entry = plan["skipped"][0]
        assert entry["reason"] == "无定位器"
        assert entry["scope"] == "form"

    def test_columns_declared_count_reaches_skip(self):
        loc = {"fields": dict(FIELDS["fields"]), "columns": ["店鋪編號", "店鋪名稱"]}
        plan = build_form_plan(
            compile_cases([_case("TC-L3", field="列表顯示欄位", rule_id="LST-2")]), loc,
        )
        assert plan["skipped"][0]["columns_declared"] == 2
        assert plan["columns"]["column_count"] == 2
        # 契约存在但**不产生检查**：本期没有列表执行器，不能让人以为已经验了
        assert plan["check_count"] == 0


class TestLegacyColumnCountConsistency:
    """旧键名自带列数（columns_22），与实际解析列数不一致时必须报出来。

    真实库实测：WSF-48576974 的 columns_22 实含 23 项（多一个「（勾选列）」）。
    若按 22 列去做断言，后面每一列都会错位一格，而配置本身看起来"没问题"。
    """

    def test_declared_count_mismatch_is_reported(self):
        contract, _ = parse_columns({
            "columns_22": ["（勾选列）", "A", "B"],   # 键名说 22，实际 3
            "requirement_fields_16": {},
        })
        assert any("声明 22 列" in n and "实际解析出 3 列" in n for n in contract["notes"]), \
            contract["notes"]

    def test_matching_count_is_silent(self):
        contract, _ = parse_columns({
            "columns_2": ["A", "B"],
            "requirement_fields_16": {},
        })
        assert not any("声明" in n for n in contract["notes"]), contract["notes"]
