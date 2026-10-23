# -*- coding: utf-8 -*-
"""rev199：列表域用例从「0 断言」到「可机检」——规则文本 → 列契约 / 排序。

背景（2026-10-23 全量扫描 2890 条）：``requirement_rule.field`` 属列表域的 29 条，
**带断言的 0 条**。执行侧早已就绪（列契约 + ``COLUMN_DIFF`` + ``assert_on_mismatch``、
``filter_checks``、``list_checks``），缺的是上游把规则文本翻成断言。

本文件守四件事：
1. **只翻结构化形态**：声明了列数的列清单、给全"列名+方向"的排序；
   声明与解析数不符必须**拒绝并说明差在哪**（不补齐、不猜列名）；
2. 翻不动的必须留痕（``untranslatable`` / ``needs_input``）——
   "为什么这 29 条还没断言"要能从回执里读出来；
3. 合成断言**单列口径**（``synthesized`` / ``synthesized_assertion_count``）：
   需求给的断言与平台推的断言不能混成一个数；
4. 列契约变成会判失败的断言时，**多出的列不算失败**（需求只声明"要显示哪 N 列"）。
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from types import SimpleNamespace

import pytest

from app.services.web_case_compiler import (
    COMPILER_REV,
    LIST_CHECK_KINDS,
    build_form_plan,
    compile_cases,
    parse_list_rule,
)

STORE_15 = (
    "「店鋪」列表顯示欄位（15列）：店鋪編號、店鋪名稱、店鋪名稱(EN)、店鋪內部名稱、"
    "所屬品牌、所屬菜係、優先級、是否在APP顯示、營業狀態、排隊、訂位、外賣、"
    "所屬組織、操作人、更新時間"
)
GOODS_8 = (
    "「商品」列表顯示欄位（8列）：商品編號、商品名稱(CN)、商品名稱(EN)、"
    "商品內部名稱、商品規格編號、創建人、創建時間、更新人"
)
LOCATORS = {
    "name": "probe",
    "fields": {"店鋪名稱(CN）": {"kind": "input"}},
}


def _rev_num() -> int:
    return int(re.search(r"rev(\d+)", COMPILER_REV).group(1))


def _case(identifier: str, field: str, rule: str, *, assertions=None):
    cf = {"requirement_rule_id": "R-1",
          "requirement_rule": {"field": field, "rule": rule}}
    if assertions is not None:
        cf["assertions"] = assertions
    return SimpleNamespace(identifier=identifier, name=identifier, custom_fields=cf)


def test_rev_at_least_199():
    assert _rev_num() >= 199


# --------------------------------------------------------------------------
# 1) 解析层：只翻能核对的形态
# --------------------------------------------------------------------------

def test_column_list_parsed_when_declared_count_matches():
    parsed = parse_list_rule("列表顯示欄位", STORE_15)
    assert parsed["column_count_declared"] == 15
    assert len(parsed["columns"]) == 15
    assert parsed["columns"][0] == "店鋪編號"
    assert parsed["columns"][-1] == "更新時間"
    kinds = [a["kind"] for a in parsed["assertions"]]
    assert kinds == ["column_contract"]
    assert all(a["origin"] == "rule_text" for a in parsed["assertions"])
    assert parsed["subject"] == "店鋪"


def test_declared_count_mismatch_is_refused_not_patched():
    """声明 15 列但只写了 14 个 → 拒绝。补齐就等于把"文本与实现不一致"抹掉。"""
    text = "显示15欄位（編號/名稱/內部名稱/品牌/菜係/優先級/APP顯示/營業狀態/排隊/訂位/外賣/組織/操作人/更新時間）"
    parsed = parse_list_rule("列表", text)
    assert parsed["assertions"] == []
    reasons = " ".join(u["reason"] for u in parsed["untranslatable"])
    assert "声明 15 列" in reasons
    assert "14" in reasons


def test_sort_order_needs_both_column_and_direction():
    ok = parse_list_rule("列表", "商品/支付列表均按更新時間近→遠；支付編號由外部系統生成")
    assert ok["sort"] == {"column": "更新時間", "direction": "desc",
                          "token": "近→遠"}
    assert [a["kind"] for a in ok["assertions"]] == ["sort_order"]

    asc = parse_list_rule("列表", "任務列表：按創建時間遠→近")
    assert asc["sort"]["direction"] == "asc"

    # 只说"结果有序"，没给列名与方向 —— 不能猜
    vague = parse_list_rule("列表排序", "点击列头触发排序查询且结果有序")
    assert vague["assertions"] == []
    assert vague["needs_input"] and "点哪一列" in vague["needs_input"][0]["need"]


def test_search_rule_is_recorded_as_needs_input():
    parsed = parse_list_rule("列表搜索", "Edit Filters 按 Case No. 过滤应返回匹配结果")
    assert parsed["assertions"] == []
    assert parsed["needs_input"]
    assert "真实检索值" in parsed["needs_input"][0]["need"]


def test_coarse_filter_spec_is_untranslatable_with_reason():
    parsed = parse_list_rule(
        "篩選", "支付：編號精準/名稱模糊/更新人模糊/更新時間範圍；多維度組合篩選，條件變更後需點查詢生效")
    assert parsed["assertions"] == []
    assert parsed["untranslatable"]
    assert "未给可断言的输入输出对" in parsed["untranslatable"][0]["reason"]


# --------------------------------------------------------------------------
# 2) 编译层：合成断言单列口径
# --------------------------------------------------------------------------

def test_compile_marks_synthesized_separately():
    manifest = compile_cases([
        _case("TC-1", "列表顯示欄位", STORE_15),
        _case("TC-2", "列表", "「商品」列表顯示欄位（8列）：" + GOODS_8.split("：", 1)[1]),
    ])
    assert manifest["case_status_counts"]["synthesized"] == 2
    assert manifest["synthesized_case_count"] == 2
    assert manifest["synthesized_assertion_count"] == 2
    assert manifest["list_assertion_count"] == 2
    # 合成的断言**不进**"需求断言数"：那是另一个口径
    assert manifest["assertion_count"] == 0
    rows = {r["identifier"]: r for r in manifest["list_rule_synthesis"]}
    assert rows["TC-1"]["assertions"] == ["column_contract"]
    assert rows["TC-1"]["columns"] == 15


def test_explicit_assertions_win_over_synthesis():
    explicit = [{"kind": "required", "field": "店鋪名稱(CN）", "rule": "必填"}]
    manifest = compile_cases([_case("TC-1", "列表顯示欄位", STORE_15, assertions=explicit)])
    assert manifest["case_status_counts"]["synthesized"] == 0
    assert manifest["case_status_counts"]["compiled"] == 1
    assert manifest["cases"][0]["assertions"] == explicit
    # 有显式断言时不必留合成流水（没尝试过，就不该声称"翻不动"）
    assert manifest["list_rule_synthesis"] == []


def test_untranslatable_case_keeps_reason_in_manifest():
    manifest = compile_cases([_case("TC-9", "列表排序", "点击列头触发排序查询且结果有序")])
    rows = manifest["list_rule_synthesis"]
    assert rows and rows[0]["assertions"] == []
    assert rows[0]["needs_input"]


# --------------------------------------------------------------------------
# 3) 计划层：列契约会判失败，但不多判"多出列"
# --------------------------------------------------------------------------

def _plan(rule: str, field: str = "列表顯示欄位", loc=None):
    return build_form_plan(compile_cases([_case("TC-1", field, rule)]), loc or LOCATORS)


def test_plan_builds_asserting_column_contract_from_rule_text():
    plan = _plan(STORE_15)
    assert plan is not None, "只有列契约也必须产出计划，否则产物里连契约都没有"
    columns = plan["columns"]
    assert columns["source"] == "rule_text"
    assert columns["assert_on_mismatch"] is True
    assert columns["allow_extra_columns"] is True
    assert [i["header_text"] for i in columns["items"]] == parse_list_rule("列表顯示欄位", STORE_15)["columns"]
    assert plan["column_assert_source"] == "rule_text"
    assert plan["column_contract_conflicts"] == []
    # 没有字段检查 ⇒ 不打开表单（executable 只表达"是否注入表单执行器"）
    assert plan["executable"] is False


def test_explicit_columns_are_not_overwritten():
    loc = {**LOCATORS,
           "columns": {"items": [{"key": "編號", "header_text": "Case No."}]}}
    plan = _plan(STORE_15, loc=loc)
    assert [i["header_text"] for i in plan["columns"]["items"]] == ["Case No."]
    assert plan["columns"]["source"] == "explicit"
    # 只把"要不要判失败"打开：需求要求列清单，就该验
    assert plan["columns"]["assert_on_mismatch"] is True
    assert plan["column_assert_source"] == "explicit+rule_text"


def test_conflicting_column_sets_are_reported_not_merged():
    loc = LOCATORS
    manifest = compile_cases([
        _case("TC-1", "列表顯示欄位", STORE_15),
        _case("TC-2", "列表顯示欄位", GOODS_8),
    ])
    plan = build_form_plan(manifest, loc)
    assert len(plan["columns"]["items"]) == 15          # 以第一条为准
    assert len(plan["column_contract_conflicts"]) == 1  # 不一致的如实报
    assert plan["column_contract_conflicts"][0]["case_id"] == "TC-2"


def test_sort_checks_dedupe_and_merge_owners():
    manifest = compile_cases([
        _case("TC-1", "列表", "商品列表均按更新時間近→遠"),
        _case("TC-2", "列表", "支付列表均按更新時間近→遠"),
        _case("TC-3", "列表", "任务列表按創建時間近→遠"),
    ])
    plan = build_form_plan(manifest, LOCATORS)
    checks = plan["locators"]["sort_checks"]
    assert len(checks) == 2
    by_col = {c["column"]: c for c in checks}
    assert [o["case_id"] for o in by_col["更新時間"]["owners"]] == ["TC-1", "TC-2"]
    assert by_col["更新時間"]["direction"] == "desc"
    assert plan["sort_check_count"] == 2


def test_list_kinds_are_separate_from_field_kinds():
    """列表断言不依赖字段定位器表 ⇒ 不能混进 LOCATOR_DEPENDENT_KINDS（会抬高 pending）。"""
    from app.services.web_case_compiler import LOCATOR_DEPENDENT_KINDS, SOFT_CHECK_KINDS
    assert LIST_CHECK_KINDS.isdisjoint(LOCATOR_DEPENDENT_KINDS)
    assert LIST_CHECK_KINDS.isdisjoint(SOFT_CHECK_KINDS)


# --------------------------------------------------------------------------
# 4) Node 实跑：排序比较器（判定核心，纯函数）
# --------------------------------------------------------------------------

NODE = shutil.which("node")


def _sort_fns() -> str:
    from app.services.web_case_link_service import _SORT_DIAG_TEMPLATE
    src = _SORT_DIAG_TEMPLATE
    start = src.index("function normSortValue")
    end = src.index("async function sortColumnSample")
    body = src[start:end]
    return (body
            .replace("(value: unknown): string", "(value)")
            .replace("(a: string, b: string): number | null", "(a, b)"))


@pytest.mark.skipif(not NODE, reason="需要 node 才能验证比较器语义")
def test_sort_compare_semantics():
    harness = _sort_fns() + """
const out = {
  numeric: sortCompare('9', '10'),
  money: sortCompare('1,200', '900'),
  date: sortCompare('2026-10-01 12:00', '2026-10-02 09:00'),
  text: sortCompare('A-2', 'A-10'),
  emoji: sortCompare('😀', 'abc'),
  equal: sortCompare('5', '5'),
};
console.log(JSON.stringify(out));
"""
    out = subprocess.run([NODE, "-e", harness], capture_output=True, text=True,
                         encoding="utf-8", errors="replace", env=dict(os.environ))
    assert out.returncode == 0, out.stderr[-400:]
    got = json.loads(out.stdout.strip().splitlines()[-1])
    assert got["numeric"] < 0        # 9 < 10
    assert got["money"] > 0          # 1200 > 900（去千分位）
    assert got["date"] < 0           # 早的在前
    assert got["text"] > 0           # 字典序：A-2 > A-10
    assert got["emoji"] is None      # 不可比较 ⇒ 不判失败（unverified）
    assert got["equal"] == 0
