# -*- coding: utf-8 -*-
"""「列表可见」断言（``extra_config.list_checks``，2026-10-16）声明式层测试。

背景：YL 三模块三条「新建-必填全填-真实落库**并在列表可见**」用例，
【预期结果】写了"列表中可查询到"，但正式通道只机检服务端 resultCode=0；
列表可见性靠人工探针补验、不在回归里。本类型把该断言机检化。

两类风险（与 negative_scenarios 同源）：

1. **配置正确性**：合法检查完整解析并进入计划（``checks[].type == "list_visible"``）；
   非法检查（缺 value / expect 非法 / 重复）必须在计划期被拒并带原因。
2. **不回归**：未配置 ``list_checks`` 的模板，计划里不得出现 ``list_visible``；
   且被拒条目**不得**静默丢弃（要有 ``list_check_rejects`` 且运行期可见）。
"""

from __future__ import annotations

from types import SimpleNamespace

from app.services.web_case_compiler import (
    LIST_CHECK_EXPECTS,
    NON_LOCATOR_CONFIG_KEYS,
    build_form_plan,
    parse_list_checks,
    render_form_executor,
)

CASE = SimpleNamespace(
    identifier="TC-900101", name="新建門店-必填項全填寫-真實落庫並在列表可見", module="local",
    description="",
    custom_fields={"requirement_rule_id": "", "requirement_rule": {}, "assertions": []},
    test_case_steps=[],
)

BASE_LOCATORS = {
    "name": "yl_store", "flow": "create_page", "form_button": ["Add Store"],
    "modal": "body", "fields": {"店鋪名稱(CN)": {"kind": "input"},
                                "店鋪編號": {"kind": "input"}},
    "field_modes": {}, "default_field_mode": {}, "defaults": [], "toggles": [],
    "upload_fields": {},
}

VALID_CHECK = {
    "name": "店鋪列表可見：YLUAT_TEST_01",
    "value": "YLUAT_TEST_01",
    "list_url": "https://mkt.uat.eatizen.com.hk/saas-imarketing-web-pc/#/mainData/shopManage",
    "search_placeholder": ["店鋪編號", "Store Code"],
    "expect": "found",
    "case_id": "TC-900101",
    "rule_id": "",
    "rule_ref": "新建門店_必填全填寫_真實落庫",
}


def _plan(extra: dict) -> dict:
    locators = {**BASE_LOCATORS, **extra}
    manifest = {"cases": [], "skipped": []}
    return build_form_plan(manifest, locators)


# --------------------------------------------------------------------------- #
# 1. 配置解析
# --------------------------------------------------------------------------- #
def test_parse_list_checks_keeps_valid_and_reports_rejects() -> None:
    raw = [
        VALID_CHECK,
        {"value": ""},                       # 缺 value
        {"value": "X1", "expect": "existing"},   # expect 非法
        {"value": "YLUAT_TEST_01"},          # 与第 1 条 value+expect 重复
        "not-a-dict",                        # 类型错
    ]
    items, rejects = parse_list_checks(raw)
    assert len(items) == 1
    assert items[0]["value"] == "YLUAT_TEST_01"
    assert items[0]["max_pages"] == 3 and items[0]["expect"] == "found"
    reasons = " ".join(r["reason"] for r in rejects)
    assert "缺少 value" in reasons
    assert "expect 非法" in reasons
    assert "重复" in reasons
    assert "检查必须是对象" in reasons
    # 被拒条目必须带可定位信息（value 或 index），否则用户不知道改哪一条
    assert all("reason" in r and ("value" in r or "index" in r.get("detail", "")) for r in rejects)


def test_parse_list_checks_tolerates_empty_and_malformed_root() -> None:
    assert parse_list_checks(None) == ([], [])
    assert parse_list_checks("") == ([], [])
    items, rejects = parse_list_checks({"value": "X"})
    assert items == []
    assert rejects and "必须是数组" in rejects[0]["reason"]


def test_list_check_expects_are_closed_set() -> None:
    assert LIST_CHECK_EXPECTS == frozenset({"found", "absent"})


# --------------------------------------------------------------------------- #
# 2. 计划生成
# --------------------------------------------------------------------------- #
def test_build_form_plan_emits_list_visible_check_with_owner() -> None:
    plan = _plan({"list_checks": [VALID_CHECK]})
    assert plan and plan["executable"] is True
    checks = [c for c in plan["checks"] if c["type"] == "list_visible"]
    assert len(checks) == 1
    check = checks[0]
    assert check["value"] == "YLUAT_TEST_01"
    assert check["case_id"] == "TC-900101"
    assert check["expect"] == "found"
    assert check["source"] == "locator_config"
    assert check["search_placeholder"] == ["店鋪編號", "Store Code"]
    assert plan["list_checks"][0]["name"].startswith("店鋪列表可見")
    # 归属：台账据此把执行结果记到用例上（否则"执行了却无用例拿到证据"）
    assert check["case_id"] == CASE.identifier


def test_build_form_plan_falls_back_to_locator_level_list_url() -> None:
    item = {k: v for k, v in VALID_CHECK.items() if k != "list_url"}
    plan = _plan({"list_checks": [item], "list_url": "https://example.test/list"})
    check = next(c for c in plan["checks"] if c["type"] == "list_visible")
    assert check["list_url"] == "https://example.test/list"


def test_build_form_plan_without_list_checks_has_no_list_visible() -> None:
    plan = _plan({})
    assert plan is None or not [c for c in plan["checks"] if c["type"] == "list_visible"]
    executor = render_form_executor(plan or {"locators": BASE_LOCATORS, "checks": []})
    # 未配置时不引入阶段开销：函数体存在（可复用），但 configured 恒为 0
    assert "list_phase" in executor


def test_rejected_list_check_is_visible_in_plan_and_executor() -> None:
    plan = _plan({"list_checks": [{"value": ""}, VALID_CHECK]})
    assert plan["list_check_rejects"], "被拒检查必须进计划（不得静默丢弃）"
    executor = render_form_executor(plan)
    assert "[list-skip]" in executor
    assert "LIST_VISIBLE_TRACE" in executor
    assert "LIST_PHASE" in executor


# --------------------------------------------------------------------------- #
# 3. 分类口径（与默认值检查同源：配置驱动，不是"本该有归属却没有"）
# --------------------------------------------------------------------------- #
def test_list_checks_key_is_not_a_locator_key() -> None:
    assert "list_checks" in NON_LOCATOR_CONFIG_KEYS
