# -*- coding: utf-8 -*-
"""rev203：列表域断言归属 —— 列契约 / 排序结论必须进断言账本。

背景（2026-10-26 基线刷新前置评估）：
`assertion_ledger` 只认 stdout 里的 ``COMPILED_ASSERTION {name,outcome,case_id,rule_id}``
行，并把带 ``case_id`` 的检查归为 ``case_attributed``——**用例结论的唯一依据**。
而列表域的列契约与排序此前只输出 ``COLUMN_DIFF`` / ``SORT_TRACE`` / ``SORT_PHASE``
三行诊断日志，于是实测：

    TR-820（支付，日志 matched 7/7 + 排序 pass、job completed）
        → test_results.status = NOT_EXECUTED
    TR-813 ~ TR-828（全部 16 个 YL run）
        → case_attributed_total = 0

"日志全绿、用例级未执行"就是这么来的。本文件守三件事：

1. 列契约合成时带 ``owners``（一份列清单常被多条用例重复声明 → 合并去重）；
2. 模板把列契约 / 排序的结论按 owners 逐条输出 ``COMPILED_ASSERTION``；
3. **只有会判失败的契约才记账**（``assert_on_mismatch``）：纯诊断的配置漂移
   塞进账本，会把"配置没对齐"伪装成用例失败。
"""

from __future__ import annotations

import json

from app.services import web_case_compiler as compiler
from app.services.web_case_link_service import WebCaseLinkService

SVC = WebCaseLinkService.__new__(WebCaseLinkService)

_GOODS_COLS = (
    "「商品」列表顯示欄位（6列）：商品編號、商品名稱(CN)、商品名稱(EN)、"
    "商品內部名稱、商品規格編號、更新人"
)


def _case(identifier: str, rule: str, field: str, rule_id: str = ""):
    from types import SimpleNamespace

    return SimpleNamespace(
        identifier=identifier,
        name="列表顯示-商品-欄位完整性校驗",
        module="YL-主數據管理-商品",
        description=None,
        custom_fields={
            "assertions": [],
            "requirement_rule": {"rule": rule, "field": field},
            "requirement_rule_id": rule_id,
        },
    )


def _plan(cases, locators=None):
    manifest = compiler.compile_cases(cases)
    loc = {"name": "yl_商品_form", "flow": "create_page", "modal": "body", "fields": {},
           "column_aliases": {"商品編號": "Item Code", "更新時間": "Update Time"}}
    loc.update(locators or {})
    return compiler.build_form_plan(manifest, loc) or {}


# ---------------------------------------------------------------- 编译层：owners


def test_column_contract_carries_owner_of_declaring_case():
    """列契约必须带上声明它的用例，否则运行时无从归属。"""
    plan = _plan([_case("TC-135088", _GOODS_COLS, "列表顯示欄位", "LST-0286bdfb44")])
    columns = plan.get("columns") or {}
    assert columns.get("source") == "rule_text"
    assert columns.get("assert_on_mismatch") is True
    assert columns.get("owners") == [
        {"case_id": "TC-135088", "rule_id": "LST-0286bdfb44"}
    ]


def test_duplicate_column_declarations_are_merged_not_dropped():
    """同一份列清单被多条用例重复声明时，owners 要合并（每条用例都要拿到证据）。

    实测：店铺 15 列被 3 条用例各写一次；只留第一条会让另外 2 条永远是"未执行"。
    """
    plan = _plan([
        _case("TC-1", _GOODS_COLS, "列表顯示欄位", "R-1"),
        _case("TC-2", _GOODS_COLS, "列表顯示欄位", "R-2"),
    ])
    owners = (plan.get("columns") or {}).get("owners") or []
    assert [o["case_id"] for o in owners] == ["TC-1", "TC-2"]


def test_same_case_declared_twice_is_deduplicated():
    """同一条用例重复出现时不得把 owners 撑成两条（会虚增断言数）。"""
    plan = _plan([
        _case("TC-1", _GOODS_COLS, "列表顯示欄位", "R-1"),
        _case("TC-1", _GOODS_COLS, "列表顯示欄位", "R-1"),
    ])
    owners = (plan.get("columns") or {}).get("owners") or []
    assert len(owners) == 1


def test_sort_check_keeps_its_owners():
    """排序检查（rev199 起就带 owners）不能被这次改动破坏。

    注：列契约走 plan **顶层** ``columns``，排序走 ``locators.sort_checks``
    （FORM_PLAN 是白名单字典，locators 原样透传，新增顶层键要单独登记）。
    """
    plan = _plan([
        _case("TC-568711", "商品/支付列表均按更新時間近→遠", "列表", "FR-YL-LIST-GP")
    ])
    checks = (plan.get("locators") or {}).get("sort_checks") or []
    assert len(checks) == 1
    assert checks[0]["owners"] == [{"case_id": "TC-568711", "rule_id": "FR-YL-LIST-GP"}]


# ---------------------------------------------------------------- 模板层：输出


def _render(columns_json="null", sort_json="[]") -> str:
    from types import SimpleNamespace

    case = SimpleNamespace(name="列表顯示-商品-欄位完整性校驗",
                           module="YL-主數據管理-商品", description=None,
                           identifier="TC-135088")
    return SVC._generate_yl_masterdata_script(
        case, "https://console.uat.eatizen.com.hk", ["Master Data Management"],
        columns_json=columns_json, sort_checks_json=sort_json,
    )


def test_column_diag_emits_compiled_assertion_for_each_owner():
    """列契约结论要按 owners 逐条打 COMPILED_ASSERTION（账本的唯一入口）。"""
    contract = {
        "items": [{"key": "商品編號", "header_text": "商品編號", "aliases": ["Item Code"]}],
        "source": "rule_text", "assert_on_mismatch": True, "allow_extra_columns": True,
        "owners": [{"case_id": "TC-135088", "rule_id": "LST-0286bdfb44"},
                   {"case_id": "TC-852190", "rule_id": "LST-0286bdfb44"}],
    }
    script = _render(columns_json=json.dumps(contract, ensure_ascii=False))
    assert "function recordColumnCheck" in script
    assert "COMPILED_ASSERTION ' + JSON.stringify({" in script
    assert "recordColumnCheck(" in script
    # 结论三态都要落到同一入口：match→pass / mismatch→fail / 读不到表头→unverified
    assert "recordColumnCheck('unverified', 'no_header" in script
    assert "verdict === 'match' ? 'pass' : 'fail'" in script


def test_column_diag_does_not_record_diagnostic_only_contract():
    """不判失败的契约只诊断、不记账（否则配置漂移会变成假失败）。

    守的是 ``if (!COLUMN_CONTRACT || COLUMN_CONTRACT.assert_on_mismatch !== true) return;``
    这条早退——它在生成脚本里必须存在，因为运行时无从感知"这次要不要判失败"。
    """
    script = _render()
    assert "COLUMN_CONTRACT.assert_on_mismatch !== true) return;" in script


def test_sort_diag_emits_compiled_assertion_per_verdict():
    """排序的 pass/fail/unverified 三分支都必须记账。"""
    script = _render(sort_json=json.dumps([
        {"name": "sort:更新時間:desc", "column": "更新時間", "direction": "desc",
         "aliases": ["Update Time"], "max_rows": 30, "source": "rule_text",
         "owners": [{"case_id": "TC-568711", "rule_id": "FR-YL-LIST-GP"}]},
    ], ensure_ascii=False))
    assert "function recordSortCheck" in script
    assert "recordSortCheck(check, nm, 'pass'" in script
    assert "recordSortCheck(check, nm, 'fail'" in script
    assert "unverified: exception" in script
    # owners 为空时不出断言：没有归属的检查进不了 case_attributed，只会污染 unattributed
    assert "const owners = Array.isArray(check.owners) ? check.owners : [];" in script


def test_attribute_name_matches_between_log_and_ledger():
    """账本按 name 去重（_dedup_key 含 name）——名字必须稳定，不能带每次变化的观测值。"""
    script = _render()
    assert "name: 'column_contract:list'" in script


# --------------------------------------------------- 用例级状态：fail 必须落 FAILED


def test_fail_outcome_maps_to_failed_not_skipped():
    """``fail`` 必须落 FAILED。

    旧实现只认 ``blocked``，``fail`` 落进 else 分支被判 SKIPPED ——
    于是**用例级永远不会出现 FAILED**，真实失败被记成"存在断言但没能验证"。
    实测 TR-830：店铺列契约 12/15（缺 優先級/是否在APP顯示/所屬組織）、
    脚本 expect 抛出，5 条用例却全被记成 SKIPPED、run 级 failed=0。
    """
    from app.services.web_case_result_mapper import FAILED, aggregate_status

    assert aggregate_status(["fail"]) == FAILED
    assert aggregate_status(["pass", "fail"]) == FAILED
    assert aggregate_status(["fail", "unverified"]) == FAILED


def test_unverified_still_does_not_become_failed():
    """"没能验证"不得升级成"验过且不成立"——两边都是假结论。"""
    from app.services.web_case_result_mapper import PASSED, SKIPPED, aggregate_status

    assert aggregate_status(["unverified"]) == SKIPPED
    assert aggregate_status(["pass", "unverified"]) == SKIPPED
    assert aggregate_status(["pass"]) == PASSED


def test_list_contract_failure_reaches_case_status():
    """端到端：stdout 里的列契约 fail 行要能把该用例判成 FAILED。"""
    from app.services.web_case_result_mapper import FAILED, map_case_results

    stdout = (
        'COMPILED_ASSERTION {"name":"column_contract:list","outcome":"fail",'
        '"observed":"matched=12/15 missing=[優先級]","rule_id":"LST-1",'
        '"case_id":"TC-307483","source":"rule_text"}\n'
    )
    results = map_case_results(
        stdout, {}, case_ids=["case-uuid-1"],
        identifier_to_case={"TC-307483": "case-uuid-1"},
    )
    assert results["case-uuid-1"]["status"] == FAILED
    assert results["case-uuid-1"]["assertions"] == 1
