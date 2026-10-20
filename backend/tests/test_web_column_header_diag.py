# -*- coding: utf-8 -*-
"""N3 第 2 步（2026-10-18）：列头**只读**比对。

为什么先做这个而不是"列检查执行器"：
  · 148 个子功能里只有 1 个配了列清单，没有真实列头就无法判断配置对不对；
  · 列头是读出来的（innerText），不点击、不提交、不造数据 —— 对被测系统零副作用，
    所以可以先上线采集，等配置与真实列头对齐后再决定要不要判失败。

本文件守四件事：
1. 两个 CRM 模板都注入列契约，并在**列表渲染断言之后**调用一次比对；
2. 未配列时契约是 ``null``（仍会输出 COLUMN_HEADERS，作为采集真实列头的入口）；
3. 比对块里**没有**任何写操作原语（click/fill/press/setInputFiles/check）；
4. 默认不因列头不一致判失败，只有配置显式写 ``assert_on_mismatch: true`` 才判。
"""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from app.services.web_case_compiler import parse_columns
from app.services.web_case_link_service import (
    WebCaseLinkService,
    _COLUMN_DIAG_TEMPLATE,
    render_column_diag,
)

_CONTRACT = {
    "table_scope": ".ant-table-thead th",
    "column_count": 2,
    "source": "explicit",
    "notes": [],
    "items": [
        {"key": "工单编号", "header_text": "Case No.", "aliases": ["Case No."]},
        {"key": "状态", "header_text": "Status", "aliases": []},
    ],
}


def _case():
    return SimpleNamespace(
        identifier="TC-COL01", name="店铺-新建", module="YL-主數據管理-店鋪",
        feature="店鋪", scenario="", background="", description="",
        custom_fields={}, test_case_steps=[],
    )


def _render(kind: str, columns_json: str = "null") -> str:
    svc = WebCaseLinkService.__new__(WebCaseLinkService)
    if kind == "yl":
        return svc._generate_yl_masterdata_script(
            _case(), "https://uat.example", ["CRM", "Master Data Management"],
            columns_json=columns_json,
        )
    return svc._generate_cs_module_script(
        _case(), "https://uat.example", ["CRM", "Customer Service"],
        project_identifier="PR-9", columns_json=columns_json,
    )


class TestColumnDiagBlock:
    def test_block_outputs_both_lines(self):
        js = render_column_diag()
        assert "'COLUMN_HEADERS '" in js
        assert "'COLUMN_DIFF '" in js

    def test_block_is_read_only(self):
        """只读是这条能力的**前提**：有写操作就不能随便在别人的环境里跑。"""
        js = render_column_diag()
        for forbidden in (".click(", ".fill(", ".press(", "setInputFiles", ".check(", ".uncheck("):
            assert forbidden not in js, forbidden

    def test_block_skips_scrollbar_cell(self):
        """antd 固定列的滚动条占位格没有文本，不剔除会把列数恒定算多 1。"""
        assert "ant-table-cell-scrollbar" in render_column_diag()

    def test_block_reads_first_header_row_only(self):
        """分组表头的第二行是子列，混进来会把列数算错。"""
        assert "thead > tr:first-child > th" in render_column_diag()

    def test_block_is_scoped_to_first_table(self):
        """列表页上方常带统计小表：不限表会把多张表的表头拼成一条。"""
        js = render_column_diag()
        assert "root: '.ant-table'" in js
        assert "scope.locator(plan.root).first()" in js

    def test_placeholder_replaced(self):
        js = render_column_diag(json.dumps(_CONTRACT, ensure_ascii=False))
        assert "@@COLUMNS_JSON@@" not in js
        assert '"header_text": "Case No."' in js

    def test_null_contract_by_default(self):
        assert "const COLUMN_CONTRACT: ColumnContract = null;" in render_column_diag()

    def test_default_does_not_assert(self):
        """默认只报不判：配置列数与真实表头尚未对齐，判失败会变成整批假失败。"""
        js = render_column_diag()
        assert "assert_on_mismatch === true" in js


class TestTemplatesCarryColumnDiag:
    @pytest.mark.parametrize("kind,label", [("yl", "yl-list"), ("cs", "cs-list")])
    def test_template_injects_contract_and_calls_once(self, kind, label):
        script = _render(kind, json.dumps(_CONTRACT, ensure_ascii=False))
        assert script.count("async function columnDiag") == 1
        # 必须**真的被调用**：只判"字符串出现过"会被注释行骗过
        #（变异验证实测：把调用注释掉，子串断言仍然通过）。
        lines = [line.strip() for line in script.splitlines()]
        assert f"await columnDiag(scope, '{label}');" in lines
        assert '"header_text": "Case No."' in script
        # 契约对象要能被 JS 解析：取出赋值右侧反序列化
        line = next(l for l in script.splitlines() if l.startswith("const COLUMN_CONTRACT: ColumnContract = "))
        payload = line.split("= ", 1)[1].rstrip(";")
        assert json.loads(payload)["column_count"] == 2

    @pytest.mark.parametrize("kind,label", [("yl", "yl-list"), ("cs", "cs-list")])
    def test_call_sits_after_render_assertion(self, kind, label):
        """列表没渲染时读不到表头；必须放在渲染断言之后，避免把"没渲染"报成"列缺失"。"""
        script = _render(kind)
        call_at = script.index(f"await columnDiag(scope, '{label}');")
        ready_at = script.index("列表未渲染（15s 轮询后")
        assert call_at > ready_at

    @pytest.mark.parametrize("kind", ["yl", "cs"])
    def test_no_contract_still_dumps_headers(self, kind):
        script = _render(kind)
        assert "const COLUMN_CONTRACT: ColumnContract = null;" in script
        # 未配列也要输出真实列头（采集入口）
        assert "COLUMN_HEADERS" in script

    @pytest.mark.parametrize("kind", ["yl", "cs"])
    def test_no_double_brace_leak(self, kind):
        script = _render(kind)
        assert "{{" not in script
        assert "}}" not in script


class TestPlanPassesColumnsToScript:
    def test_generate_playwright_script_forwards_contract(self):
        svc = WebCaseLinkService.__new__(WebCaseLinkService)
        script = svc._generate_playwright_script(
            _case(), "https://uat.example", ["CRM", "Master Data Management"],
            form_plan={"columns": _CONTRACT, "check_count": 0},
            web_template_key="yl_masterdata",
        )
        assert '"header_text": "Case No."' in script

    def test_generate_playwright_script_without_plan_is_null(self):
        svc = WebCaseLinkService.__new__(WebCaseLinkService)
        script = svc._generate_playwright_script(
            _case(), "https://uat.example", ["CRM", "Master Data Management"],
            web_template_key="yl_masterdata",
        )
        assert "const COLUMN_CONTRACT: ColumnContract = null;" in script


class TestAssertSwitch:
    def test_default_contract_has_no_assert_flag(self):
        contract, _ = parse_columns({"columns": {"items": ["A", "B"]}})
        assert "assert_on_mismatch" not in contract

    def test_explicit_flag_is_forwarded(self):
        contract, _ = parse_columns({"columns": {"items": ["A"], "assert_on_mismatch": True}})
        assert contract["assert_on_mismatch"] is True

    def test_non_true_value_is_ignored(self):
        """只认严格的 true：字符串 "true"/1 不打开硬门禁，避免配置笔误把整批判红。"""
        contract, _ = parse_columns({"columns": {"items": ["A"], "assert_on_mismatch": "true"}})
        assert "assert_on_mismatch" not in contract
