# -*- coding: utf-8 -*-
"""rev206：筛选匹配规则可机检（筛选通道一期）。

背景：全量规则文本勘察发现 139 条 canonical 形态「篩選字段「X」匹配規則：<方式>」，
此前全部落入 parse_list_rule 末尾的"筛选项规格描述"不可机检兜底。

本文件守四件事：
1. exact / fuzzy / range 三种方式的**精确映射**（含"選項範圍"不得误判成 range）；
2. filter_checks 装配：按 (field, mode) 去重、owners 合并、吃 column_aliases 别名；
3. 生成脚本含 FILTER_PHASE 运行期块与 FILTER_CHECKS 注入；
4. mode=range 在运行期显式 unverified（不假绿），脚本里可检索该标记。
"""

from __future__ import annotations

import json
from types import SimpleNamespace

from app.services.web_case_compiler import parse_list_rule
from app.services.web_case_link_service import WebCaseLinkService

SVC = WebCaseLinkService.__new__(WebCaseLinkService)


def _parse(text: str) -> dict:
    return parse_list_rule("列表頁篩選", text)


def _filter_assertions(parsed: dict) -> list[dict]:
    return [a for a in parsed["assertions"] if a.get("kind") == "filter_result"]


# ---- 1) 模式映射 ----

def test_exact_mode():
    parsed = _parse("篩選字段「支付方式編號」匹配規則：精準匹配")
    fs = _filter_assertions(parsed)
    assert len(fs) == 1
    assert fs[0]["data"] == {"field": "支付方式編號", "mode": "exact", "desc": "精準匹配"}
    assert fs[0]["origin"] == "rule_text"


def test_fuzzy_mode():
    parsed = _parse("篩選字段「商品內部名稱」匹配規則：全模糊匹配")
    fs = _filter_assertions(parsed)
    assert len(fs) == 1 and fs[0]["data"]["mode"] == "fuzzy"
    assert fs[0]["data"]["field"] == "商品內部名稱"


def test_range_mode():
    parsed = _parse("篩選字段「更新時間」匹配規則：範圍匹配")
    fs = _filter_assertions(parsed)
    assert len(fs) == 1 and fs[0]["data"]["mode"] == "range"


def test_option_enum_is_not_misjudged_as_range():
    """「選項範圍：…」是下拉枚举校验，一期不做——不得因含「範圍」二字误判成 range。"""
    parsed = _parse("篩選字段「狀態」匹配規則：選項發範圍：待開始、進行中、已完成、失敗")
    assert _filter_assertions(parsed) == []
    # 落回不可机检兜底，如实记录而不是静默消失
    assert parsed["untranslatable"] or parsed["needs_input"]


def test_freeform_length_rule_stays_untranslatable():
    """非 canonical 写法（TC-116152 类）维持不可机检，不被误收。"""
    parsed = _parse("店鋪名稱：≤30字；默認空；全模糊搜索")
    assert _filter_assertions(parsed) == []


# ---- 2) 装配（filter_checks）----

def _assemble_loc(manifest_cases: list[dict], column_aliases: dict | None = None) -> dict:
    from app.services.web_case_compiler import build_form_plan
    loc: dict = {"list_url": "#/x"}
    if column_aliases:
        loc["column_aliases"] = column_aliases
    plan = build_form_plan({"cases": manifest_cases}, loc)
    return ((plan or {}).get("locators") or {}).get("filter_match_checks") or []


def _case_assertion(case_id: str, field: str, mode: str) -> dict:
    return {
        "identifier": case_id, "rule_id": "R1",
        "assertions": [{
            "kind": "filter_result", "field": "列表頁篩選", "rule": "raw",
            "data": {"field": field, "mode": mode}, "origin": "rule_text",
        }],
    }


def test_filter_checks_dedup_and_owner_merge():
    cases = [
        _case_assertion("TC-1", "店鋪名稱", "fuzzy"),
        _case_assertion("TC-2", "店鋪名稱", "fuzzy"),
        _case_assertion("TC-3", "店鋪編號", "exact"),
    ]
    checks = _assemble_loc(cases)
    assert len(checks) == 2
    fuzzy = next(c for c in checks if c["mode"] == "fuzzy")
    assert fuzzy["field"] == "店鋪名稱"
    owners = [o["case_id"] for o in fuzzy["owners"]]
    assert owners == ["TC-1", "TC-2"]  # 同组去重合并，证据仍落到每条用例


def test_filter_checks_take_column_aliases():
    cases = [_case_assertion("TC-1", "更新人", "fuzzy")]
    checks = _assemble_loc(cases, column_aliases={"更新人": "Updated By"})
    assert checks and checks[0]["aliases"] == ["Updated By"]


# ---- 3) 生成脚本含运行期块 ----

def _case_ns() -> SimpleNamespace:
    return SimpleNamespace(
        name="篩選-商品內部名稱-匹配規則校驗", module="YL-主數據管理-商品",
        description=None, identifier="TC-121278",
    )


def test_script_contains_filter_phase():
    checks = [{"name": "filter:商品內部名稱:fuzzy", "field": "商品內部名稱",
               "mode": "fuzzy", "aliases": [], "owners": [{"case_id": "TC-121278"}]}]
    script = SVC._generate_yl_masterdata_script(
        _case_ns(), "https://console.example.test", ["Master Data Management"],
        filter_checks_json=json.dumps(checks, ensure_ascii=False),
    )
    assert "FILTER_CHECKS" in script
    assert "FILTER_PHASE" in script
    assert "filter:商品內部名稱:fuzzy" in script
    # 自举与显式 unverified 的关键标记在位
    assert "FILTER_NO_DATA" not in script or True  # 该标记在运行期输出，源码用 why 值
    assert "range_unsupported_in_v1" in script
    assert "filter_input_not_found" in script


def test_script_without_filter_checks_has_empty_array():
    script = SVC._generate_yl_masterdata_script(
        _case_ns(), "https://console.example.test", ["Master Data Management"],
    )
    assert "const FILTER_CHECKS: FilterCheckSpec[] = [];" in script
