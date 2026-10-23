# -*- coding: utf-8 -*-
"""rev197：对账行要能回答「跳过的那 N 条**是哪几条**」。

背景（2026-10-23，TR-801 实测）：运行期只打出
``skipped_by_reason: {"无定位器": 8}`` —— 知道"有 8 条因无定位器被跳过"，
但不知道是哪 8 个字段。为了定位，只能另写探针把 54 条用例和子功能配置读出来重编，
而这份信息编译期本来就有（``FORM_PLAN.skipped`` 里带 field/case_id/kinds/assertions）。

本版补两个**只读视图**，不改任何判定：
  * ``skipped_fields``：字段 → {weight, count, reasons}，进 ``COVERAGE_RECONCILE``，
    直接回答"该改哪一行 fields"；
  * ``skipped_detail``：逐条记录（限 40 条 + truncated），留在 ``COMPILED_ASSERTION_SUMMARY``
    ——明细几十条，塞进对账行会把恒等式本身挤到看不见。

本文件守四件事：
1. 两个视图都存在，且**明细有上限**（payload 要随脚本下发，不能无界增长）；
2. 对账行只带聚合视图、不带逐条明细（防止日志膨胀）；
3. 明细项的字段齐全，且与计划里的 skip 记录同源（不是另造一套）；
4. **判定未被改动** —— 恒等式公式、跳过口径都不因这次"加观测"而变化。
"""

from __future__ import annotations

import re
from types import SimpleNamespace

from app.services.web_case_compiler import (
    COMPILER_REV,
    _FORM_EXECUTOR_TS,
    build_form_plan,
    compile_cases,
)

LOCATORS = {
    "name": "probe",
    "fields": {"已配字段": {"kind": "input", "selector_hint": "#x"}},
}


def _rev_num() -> int:
    return int(re.search(r"rev(\d+)", COMPILER_REV).group(1))


def _case(identifier, field, assertions):
    return SimpleNamespace(
        identifier=identifier,
        name=identifier,
        custom_fields={
            "requirement_rule_id": "R-1",
            "requirement_rule": {"field": field},
            "assertions": assertions,
        },
    )


def _plan_with_skips():
    """一条用例的 3 条断言全部指向未配定位器的字段 ⇒ 至少 1 条 skip 记录。"""
    case = _case("TC-SKIP", "未配字段", [
        {"kind": "required", "field": "未配字段", "rule": "是否必填=是"},
        {"kind": "max_length", "field": "未配字段", "rule": "长度<=10", "data": "10"},
        {"kind": "enum", "field": "未配字段", "rule": "枚举=甲/乙", "data": "甲|乙"},
    ])
    return build_form_plan(compile_cases([case]), LOCATORS)


# ---------------------------------------------------------------------------
# 1) 两个视图存在，且明细有上限
# ---------------------------------------------------------------------------

def test_rev_at_least_197():
    assert _rev_num() >= 197, COMPILER_REV


def test_summary_exposes_both_views():
    ts = _FORM_EXECUTOR_TS
    assert "skipped_fields: (() =>" in ts, "缺少字段级聚合视图"
    assert "skipped_detail: (() =>" in ts, "缺少逐条明细视图"


def test_detail_is_capped_and_marks_truncation():
    ts = _FORM_EXECUTOR_TS
    start = ts.index("skipped_detail: (() =>")
    body = ts[start:start + 1200]
    assert re.search(r"const cap = (\d+);", body), "明细必须有上限（payload 随脚本下发）"
    cap = int(re.search(r"const cap = (\d+);", body).group(1))
    assert 10 <= cap <= 100, f"上限 {cap} 不合理：太小查不到问题，太大撑爆日志"
    assert "truncated: all.length > items.length" in body, "截断必须显式标记，不能静默少给"


def test_detail_items_carry_the_six_keys():
    ts = _FORM_EXECUTOR_TS
    start = ts.index("skipped_detail: (() =>")
    body = ts[start:start + 1200]
    for key in ("field", "case_id", "scope", "kinds", "weight", "reason"):
        assert f"{key}:" in body, f"明细项缺 {key}"


def test_skipped_fields_aggregates_weight_and_reasons():
    ts = _FORM_EXECUTOR_TS
    start = ts.index("skipped_fields: (() =>")
    body = ts[start:start + 900]
    assert "weight: 0, count: 0" in body
    assert "cur.weight += Number(s.assertions || 0)" in body, "权重必须按断言条数累加（与 rev193 同口径）"
    assert "cur.count += 1" in body
    assert "reasons" in body and "indexOf(reason) < 0" in body, "同类原因要去重，否则一屏都是重复词"


# ---------------------------------------------------------------------------
# 2) 对账行只带聚合视图
# ---------------------------------------------------------------------------

def test_reconcile_line_carries_aggregate_not_detail():
    ts = _FORM_EXECUTOR_TS
    start = ts.index("COVERAGE_RECONCILE")
    # 取到这一条 console.log 结束（`}));`），不要用固定长度窗口 ——
    # 注释一长就会把断言截在窗口外，红的原因变成"测试窗口太小"。
    end = ts.index("}));", start)
    tail = ts[start:end]
    assert "skipped_fields: summary.skipped_fields" in tail, "对账行必须带字段级视图"
    assert "skipped_detail" not in tail, (
        "逐条明细不该进对账行：几十条记录会把恒等式挤到看不见（明细在 summary 里）"
    )
    assert "skipped_by_reason: summary.skipped_by_reason" in tail, "旧视图不能删（下游可能在看）"


# ---------------------------------------------------------------------------
# 3) 明细与计划同源：计划里的 skip 记录确实带这些字段
# ---------------------------------------------------------------------------

def test_plan_skip_records_have_detail_fields():
    plan = _plan_with_skips()
    skipped = plan.get("skipped") or []
    assert skipped, "样本应当产生 skip 记录"
    for item in skipped:
        assert "reason" in item, "每条 skip 必须有原因（否则明细无意义）"
        assert "assertions" in item, "每条 skip 必须带权重（rev193 起）"
    # 至少要有一条带得出「哪个字段」——case 级 skip 的 field 为空，不能强求每条都有
    assert any(str(i.get("field") or "") for i in skipped), "样本里没有任何一条能指到字段"


def test_plan_skip_records_carry_case_id_when_known():
    plan = _plan_with_skips()
    skipped = plan.get("skipped") or []
    assert any(i.get("case_id") == "TC-SKIP" for i in skipped), (
        "skip 记录应带 case_id，否则明细里无法回指用例"
    )


# ---------------------------------------------------------------------------
# 4) 判定未被改动
# ---------------------------------------------------------------------------

def test_identity_formula_unchanged():
    """rev197 只加观测：恒等式必须与 rev193 完全一致。"""
    ts = _FORM_EXECUTOR_TS
    assert (
        "const __unexplainedPlan = __plannedA - ((__execA - __configA) + __defaultCovA + __skipA);"
        in ts
    ), "plan 侧恒等式被改动 —— rev197 不该动判定"


def test_skip_count_semantics_unchanged():
    """跳过侧仍按断言条数（skipped_assertions），记录条数另存。"""
    ts = _FORM_EXECUTOR_TS
    assert "const __skipA = summary.skipped_assertions;" in ts
    assert "const __skipEntries = summary.skipped_no_locator;" in ts
