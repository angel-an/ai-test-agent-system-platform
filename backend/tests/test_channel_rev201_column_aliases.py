# -*- coding: utf-8 -*-
"""rev201：列名别名通道 + 列契约失败不中断排序。

TR-813 实测（2026-10-26，UAT 解封后首轮真实通道）：
1. 页面表头全英文（Store Code / Brand / Operation Time），规则文本是繁体中文
   （店鋪編號 / 所屬品牌 / 更新時間）——没有别名映射，COLUMN_DIFF matched 恒为 0，
   15 条契约列全部「missing」，整批假失败；
2. columnDiag 在 mismatch 时立即 ``expect``，整条脚本中止——SORT_PHASE 没跑到，
   排序证据全部丢失。

本文件守四件事：
1. ``field_locators.column_aliases`` 合进**合成**契约的 item.aliases（str 与 list 两种形态）；
2. **显式**契约的条目按 key/header_text 补别名（对上才补，不发明）；
3. ``sort_checks`` 每条带 ``aliases``，执行侧先逐候选精确、再逐候选包含；
4. 列契约失败改为记账（COLUMN_FAIL），排序断言跑完后再统一判定——
   YL 与储值（cs-list）两个模板都要有这个统一判定点。
"""

from __future__ import annotations

import json
import re
from types import SimpleNamespace

from app.services.web_case_compiler import (
    COMPILER_REV,
    build_form_plan,
    compile_cases,
)
from app.services.web_case_link_service import render_column_diag, render_sort_checks

STORE_15 = (
    "「店鋪」列表顯示欄位（15列）：店鋪編號、店鋪名稱、店鋪名稱(EN)、店鋪內部名稱、"
    "所屬品牌、所屬菜係、優先級、是否在APP顯示、營業狀態、排隊、訂位、外賣、"
    "所屬組織、操作人、更新時間"
)
SORT_RULE = "「店鋪」列表按更新時間近→遠"

#: TR-813 实测的真实表头映射（部分）：優先級/是否在APP顯示/所屬組織 页面没有，
#: **不给映射**——它们是列契约该浮出的真实差异，不是语言问题。
STORE_ALIASES = {
    "店鋪編號": "Store Code",
    "店鋪名稱": ["Store Name (CN)", "Store Name"],
    "更新時間": "Operation Time",
}

LOCATORS_BASE = {
    "name": "probe",
    "fields": {"店鋪名稱(CN）": {"kind": "input"}},
}


def _rev_num() -> int:
    return int(re.search(r"rev(\d+)", COMPILER_REV).group(1))


def _plan(locators: dict) -> dict:
    case = SimpleNamespace(identifier="TC-X", name="列表顯示-店鋪", custom_fields={
        "requirement_rule_id": "R-1",
        "requirement_rule": {"field": "列表顯示欄位", "rule": STORE_15},
    })
    manifest = compile_cases([case])
    return build_form_plan(manifest, locators) or {}


def test_rev_at_least_201():
    assert _rev_num() >= 201


# --------------------------------------------------------------------------
# 1) 合成契约吃 column_aliases
# --------------------------------------------------------------------------

def test_rule_contract_items_carry_string_alias():
    loc = {**LOCATORS_BASE, "column_aliases": {"店鋪編號": "Store Code"}}
    items = (_plan(loc).get("columns") or {}).get("items") or []
    by_key = {i["key"]: i for i in items}
    assert by_key["店鋪編號"]["aliases"] == ["Store Code"]
    # 没配映射的列保持空别名——不发明、不猜翻译
    assert by_key["所屬品牌"]["aliases"] == []


def test_rule_contract_items_carry_list_alias():
    loc = {**LOCATORS_BASE, "column_aliases": {"店鋪名稱": ["Store Name (CN)", "Store Name"]}}
    items = (_plan(loc).get("columns") or {}).get("items") or []
    by_key = {i["key"]: i for i in items}
    assert by_key["店鋪名稱"]["aliases"] == ["Store Name (CN)", "Store Name"]


def test_explicit_contract_items_get_aliases_merged():
    explicit = {"items": [{"key": "店鋪編號", "header_text": "Store Code"},
                          {"key": "店鋪名稱", "header_text": "Store Name (CN)"}],
                "source": "explicit"}
    loc = {**LOCATORS_BASE, "columns": explicit,
           "column_aliases": {"店鋪編號": "Store Code", "店鋪名稱": "Store Name (CN)"}}
    items = (_plan(loc).get("columns") or {}).get("items") or []
    # parse_columns 既有行为会把 key 加进候选；column_aliases 的英文候选必须追加进来
    assert "Store Code" in items[0]["aliases"]
    assert "Store Name (CN)" in items[1]["aliases"]


def test_explicit_contract_merges_without_duplicates():
    explicit = {"items": [{"key": "店鋪編號", "header_text": "Code", "aliases": ["Store Code"]}],
                "source": "explicit"}
    loc = {**LOCATORS_BASE, "columns": explicit, "column_aliases": {"店鋪編號": "Store Code"}}
    items = (_plan(loc).get("columns") or {}).get("items") or []
    assert items[0]["aliases"].count("Store Code") == 1  # 去重，不重复追加


# --------------------------------------------------------------------------
# 2) sort_checks 带别名
# --------------------------------------------------------------------------

def test_sort_checks_carry_aliases():
    case = SimpleNamespace(identifier="TC-S", name="列表-排序", custom_fields={
        "requirement_rule_id": "R-2",
        "requirement_rule": {"field": "列表", "rule": SORT_RULE},
    })
    loc = {**LOCATORS_BASE, "column_aliases": {"更新時間": "Operation Time"}}
    plan = build_form_plan(compile_cases([case]), loc) or {}
    checks = ((plan.get("locators") or {}).get("sort_checks")) or []
    assert checks and checks[0]["column"] == "更新時間"
    assert checks[0]["aliases"] == ["Operation Time"]


def test_sort_checks_without_alias_config_stay_empty():
    case = SimpleNamespace(identifier="TC-S", name="列表-排序", custom_fields={
        "requirement_rule_id": "R-2",
        "requirement_rule": {"field": "列表", "rule": SORT_RULE},
    })
    plan = build_form_plan(compile_cases([case]), dict(LOCATORS_BASE)) or {}
    checks = ((plan.get("locators") or {}).get("sort_checks")) or []
    assert checks and checks[0]["aliases"] == []


# --------------------------------------------------------------------------
# 3) 执行侧模板：排序找列吃别名；列契约失败记账不中止
# --------------------------------------------------------------------------

def test_sort_executor_iterates_alias_candidates():
    js = render_sort_checks(json.dumps(
        [{"column": "更新時間", "direction": "desc", "aliases": ["Operation Time"]}],
        ensure_ascii=False))
    assert "cfg.aliases" in js
    # 先逐候选精确，再逐候选包含——两段循环都要在
    assert js.index("heads.indexOf(cand)") < js.index("heads.findIndex")
    # TR-814 回归：aliases 必须经参数传入（旧写法引用未定义的 check 会 ReferenceError）
    assert "maxRows: number, aliases: string[] = []" in js
    assert "check.aliases || []" in js
    assert "sortColumnSample(scope, check.column, Number(check.max_rows || 30))" not in js
    # TR-815 回归：iframe 场景 scope 是 FrameLocator（无 evaluate），须经 ownerFrame()
    # → TR-816 再回归：ownerFrame 兜底在 UAT 不可靠，最终口径是 locator('body').evaluate
    assert "locator('body').evaluate" in js
    assert "el.ownerDocument || document" in js


def test_column_diag_records_column_fail_instead_of_asserting():
    js = render_column_diag('[{"key":"店鋪編號","header_text":"店鋪編號","aliases":["Store Code"]}]')
    assert "COLUMN_FAIL" in js
    # 旧写法（契约失败立即 expect）必须消失——它把后面的排序断言全部吞掉
    assert "expect(verdict" not in js


def test_unified_verdict_point_after_sort_in_both_templates():
    from app.services.web_case_link_service import WebCaseLinkService
    src_file = re.compile(r"await columnDiag\(scope, '(yl-list|cs-list)'\);")
    text = open("app/services/web_case_link_service.py", encoding="utf-8").read()
    hits = src_file.findall(text)
    assert sorted(hits) == ["cs-list", "yl-list"]
    # 每个调用点之后都要有统一判定点（COLUMN_FAIL 的 expect）
    for label in ("yl-list", "cs-list"):
        tail = text.split(f"await columnDiag(scope, '{label}');", 1)[1]
        assert "if (COLUMN_FAIL)" in tail.split("await columnDiag(", 1)[0], label
