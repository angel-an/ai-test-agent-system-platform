# -*- coding: utf-8 -*-
"""A1 / rev119：**字段级**上传放行（``field_locators.upload_allow``）。

背景（实测缺口，2026-10-19 TR e76befd0 / 787e459a）：
场景通道的 ``negative_scenarios[].expect_accept.allow_upload`` 只对**规则级场景**生效；
附件类用例（``file_size`` / ``file_format`` → ``upload_file`` 检查）走**字段级**通道、
压根没有场景 ⇒ 历史上从来拿不到任何 multipart 放行规则，上传请求被写隔离 abort
（``ISOLATION_BLOCKED ... kind=upload_blocked POST https://static.uat.eatizen.com.hk/``），
表现为「附件断言恒 unverified」。

本文件锁定三件事：
1. 声明了就**真的下发**到执行器（不是只写进计划）；
2. 没声明时**不自动放宽**（安全边界与历史逐字一致），但必须**报缺口**；
3. 写错了（host 单段 / 路径非绝对）必须当场可见，不得静默丢弃。
"""

from __future__ import annotations

import json
from types import SimpleNamespace

from app.services.web_case_compiler import (
    RECHARGE_FORM_LOCATORS,
    build_form_plan,
    compile_cases,
    render_form_executor,
)

FIELD = "上传文件（企业充值）"
GOOD_RULE = {"host_suffix": "static.uat.eatizen.com.hk", "path_prefix": "/",
             "methods": ["POST"]}


def _case(identifier: str, name: str, *, rule_id: str = "", field: str = "",
          assertions: list[dict] | None = None) -> SimpleNamespace:
    custom: dict = {}
    if rule_id:
        custom["requirement_rule_id"] = rule_id
    if field:
        custom["requirement_rule"] = {"field": field, "required": True}
    if assertions:
        custom["assertions"] = assertions
    return SimpleNamespace(
        identifier=identifier, name=name, module="小杨生煎-B端储值充值",
        description="", custom_fields=custom,
        test_case_steps=[SimpleNamespace(step="输入 0", result="被拒绝")],
    )


UPLOAD_CASES = [
    _case("TC-a1", "上传文件-边界值与格式校验", rule_id="FR-a1", field=FIELD,
          assertions=[
              {"kind": "file_size", "data": "<=1M", "expect": "accept", "rule": "文件大小1M"},
              {"kind": "file_size", "data": ">1M", "expect": "reject", "rule": "文件大小1M"},
          ]),
]
PLAIN_CASES = [
    _case("TC-a2", "备注-必填校验", rule_id="FR-a2", field="备注",
          assertions=[{"kind": "required", "data": "", "expect": "reject", "rule": "是否必填=是"}]),
]


def _plan(*, upload_allow=None, cases=None, allow_upload=True):
    loc = dict(RECHARGE_FORM_LOCATORS)
    loc["upload_size_limit_bytes"] = 1_048_576
    if upload_allow is not None:
        loc["upload_allow"] = upload_allow
    return build_form_plan(compile_cases(cases or UPLOAD_CASES), loc, allow_upload=allow_upload)


def test_missing_upload_allow_is_reported_as_a_gap_but_does_not_block():
    """没声明 ⇒ 不阻断编译，但必须把「上传会被拦」写进计划（否则只剩 unverified）。"""
    plan = _plan()
    assert [c for c in plan["checks"] if c["type"] == "upload_file"]   # 检查照常生成
    assert plan["upload_allow"] == []
    assert plan["upload_check_count"] >= 2
    assert "未声明 upload_allow" in plan["upload_policy_gap"]
    assert "恒为 unverified" in plan["upload_policy_gap"]


def test_declared_rule_reaches_the_executor_payload():
    """声明了就必须真的到执行器 —— 这是本次修的核心（不是只落在计划里）。"""
    plan = _plan(upload_allow=[GOOD_RULE])
    assert plan["upload_policy_gap"] == ""
    assert plan["upload_allow"] == [GOOD_RULE]

    script = render_form_executor(plan)
    assert "UPLOAD_POLICY" in script
    # 产物里真的能解析出这条规则（逐字）
    assert GOOD_RULE["host_suffix"] in script
    payload = script.split("const FORM_PLAN = ", 1)[1].split(";\n", 1)[0]
    plan_in_script = json.loads(payload)
    assert plan_in_script["upload_policy"]["allow_upload_rules"] == [GOOD_RULE]
    assert plan_in_script["upload_policy_gap"] == ""


def test_no_upload_assertion_means_no_gap_noise():
    """普通用例不该被这条缺口提示打扰。"""
    plan = _plan(cases=PLAIN_CASES, allow_upload=False)
    assert plan["upload_check_count"] == 0
    assert plan["upload_policy_gap"] == ""


def test_invalid_declaration_is_reported_not_silently_dropped():
    """host 单段（过宽）必须报「配置非法」，且不产生规则。"""
    plan = _plan(upload_allow=[{"host_suffix": "eatizen", "path_prefix": "/"}])
    assert plan["upload_allow"] == []
    assert "配置非法" in plan["upload_policy_gap"]
    assert "过宽" in plan["upload_policy_gap"]


def test_relative_path_prefix_is_rejected():
    plan = _plan(upload_allow=[{"host_suffix": "static.uat.eatizen.com.hk",
                                "path_prefix": "upload"}])
    assert plan["upload_allow"] == []
    assert "绝对路径前缀" in plan["upload_policy_gap"]


def test_default_payload_still_blocks_every_upload():
    """安全边界：没有任何声明时，产物里的 allow_upload_rules 必须是空数组。"""
    script = render_form_executor(_plan())
    payload = script.split("const FORM_PLAN = ", 1)[1].split(";\n", 1)[0]
    assert json.loads(payload)["upload_policy"]["allow_upload_rules"] == []


def test_upload_allow_is_not_listed_as_a_locator_key():
    """它是**行为配置**，不是选择器；预览接口不能把它当定位器键报出去。"""
    from app.services.web_case_compiler import NON_LOCATOR_CONFIG_KEYS
    assert "upload_allow" in NON_LOCATOR_CONFIG_KEYS
