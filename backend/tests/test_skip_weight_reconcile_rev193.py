# -*- coding: utf-8 -*-
"""rev193：计划期跳过必须与 planned 同口径 —— 对账恒等式不产生假缺口。

背景（2026-10-23，TR-801 实测 `COVERAGE_RECONCILE unexplained_plan=9`）：
  * planned 侧数的是**断言条数**（`compile_cases.assertion_count`）；
  * 跳过侧数的却是**记录条数**（`FORM_PLAN.skipped_count`）；
  * 而"一条用例整条落不了地"只写 1 条 skip 记录、却承载该用例 N 条断言
    ⇒ 这 N−1 条断言既没生成检查、也没被计数器认领，恒等式报成静默缺口。
实测：planned=36 / executable=17 / skipped_no_locator=10 ⇒ 差额 9 =
8 条 case 级 skip 覆盖 17 条断言所折叠掉的部分。

同轮还暴露**反向**缺口：`range` 断言 `build_form_plan` 明确支持（生成
input_validation 检查），但 `assertion_count` 不含它 ⇒ planned 少 1、检查多 1，
`unexplained_plan=-1`。两个方向都是"两个数用了两个集合"，本用例锁住两侧统一到
`FORM_CHECK_KINDS`。
"""

from __future__ import annotations

from types import SimpleNamespace

from app.services.web_case_compiler import (
    COMPILER_REV,
    FORM_CHECK_KINDS,
    assertion_weight,
    build_form_plan,
    compile_cases,
    plan_assertion_count,
)

LOCATORS = {"name": "probe", "fields": {"已配字段": {"kind": "input", "selector_hint": "#x"}}}


def _case(identifier, field, assertions, rule_id="R-1"):
    return SimpleNamespace(
        identifier=identifier,
        name=identifier,
        custom_fields={
            "requirement_rule_id": rule_id,
            "requirement_rule": {"field": field},
            "assertions": assertions,
        },
    )


def _unlanded(n):
    """一条用例 n 个断言，全部指向未配置定位器的字段。"""
    assertions = [{"kind": "required", "field": "未配字段", "rule": "是否必填=是"}]
    assertions += [
        {"kind": "max_length", "field": "未配字段", "rule": "长度<=10", "data": "10"}
        for _ in range(n - 1)
    ]
    return _case("TC-UNLANDED", "未配字段", assertions)


def _gap(plan):
    """对账差额：planned − 检查 − 跳过（按断言条数）。0 才算平衡。"""
    return plan["planned_assertions"] - len(plan["checks"]) - plan["skipped_assertion_count"]


class TestSkipIsAssertionWeighted:
    def test_case_level_skip_carries_all_assertions(self):
        for n in (1, 2, 3, 5):
            plan = build_form_plan(compile_cases([_unlanded(n)]), LOCATORS)
            assert plan["skipped_count"] == 1, "整条落不了地只应有 1 条记录（口径不变）"
            assert plan["skipped_assertion_count"] == n, "权重必须是断言条数"
            assert _gap(plan) == 0, f"{n} 条断言时不应有假缺口"

    def test_skip_record_declares_its_weight(self):
        plan = build_form_plan(compile_cases([_unlanded(3)]), LOCATORS)
        rec = plan["skipped"][0]
        assert rec["assertions"] == 3
        assert rec["case_id"] == "TC-UNLANDED"

    def test_partial_landing_weights_one_each(self):
        case = _case("TC-PART", "已配字段", [
            {"kind": "required", "field": "已配字段", "rule": "是否必填=是"},
            {"kind": "max_length", "field": "未配字段", "rule": "长度<=10", "data": "10"},
        ])
        plan = build_form_plan(compile_cases([case]), LOCATORS)
        assert plan["skipped_count"] == 1
        assert plan["skipped_assertion_count"] == 1
        assert _gap(plan) == 0

    def test_unsupported_kind_weights_zero(self):
        """写法错误的 kind 不进 planned，权重必须 0（否则制造负缺口）。"""
        case = _case("TC-X", "已配字段",
                     [{"kind": "input_validation", "field": "已配字段", "rule": "x"}])
        manifest = compile_cases([case])
        plan = build_form_plan(manifest, LOCATORS)
        assert manifest["assertion_count"] == 0
        assert plan["planned_assertions"] == 0
        assert plan["skipped_count"] == 1
        assert plan["skipped_assertion_count"] == 0
        assert _gap(plan) == 0


class TestRangeIsCountedOnPlanSide:
    def test_range_assertion_balances(self):
        """range 能生成检查 ⇒ planned 必须数它，否则 -1。"""
        case = _case("TC-R", "已配字段",
                     [{"kind": "range", "field": "已配字段", "rule": "1-10", "data": "1~10"}])
        manifest = compile_cases([case])
        plan = build_form_plan(manifest, LOCATORS)
        assert manifest["assertion_count"] == 0, "编译断言数指标口径不变"
        assert plan["planned_assertions"] == 1, "计划侧数的是可执行断言"
        assert len(plan["checks"]) == 1
        assert _gap(plan) == 0

    def test_plan_assertion_count_matches_form_check_kinds(self):
        case = _case("TC-MIX", "已配字段", [
            {"kind": "required", "field": "已配字段", "rule": "是"},
            {"kind": "range", "field": "已配字段", "rule": "1-10", "data": "1~10"},
            {"kind": "input_validation", "field": "已配字段", "rule": "写法错误"},
        ])
        manifest = compile_cases([case])
        assert plan_assertion_count(manifest) == 2
        assert assertion_weight([]) == 0
        assert "range" in FORM_CHECK_KINDS


class TestRevAndWhitelist:
    def test_rev_at_least_193(self):
        assert int(COMPILER_REV.replace("rev", "")) >= 193

    def test_skipped_assertion_count_is_rendered(self):
        """payload 是白名单：漏登记 ⇒ 执行器读不到 ⇒ TR-801 假缺口复现。"""
        from app.services.web_case_compiler import render_form_executor

        plan = build_form_plan(compile_cases([_unlanded(3)]), LOCATORS)
        script = render_form_executor(plan)
        assert '"skipped_assertion_count": 3' in script
        assert "skipped_assertions: (" in script, "执行器 summary 必须输出断言级跳过数"
        assert "const __skipA = summary.skipped_assertions;" in script
