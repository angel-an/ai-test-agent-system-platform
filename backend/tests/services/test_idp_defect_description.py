# -*- coding: utf-8 -*-
"""自动提单描述（IDP Delta）的渲染回归。

背景：平台自动创建的缺陷工单曾长期把「预期结果」写成**模板套话**
（``HTTP 状态码: 200 OK`` / ``响应结构符合接口契约`` / ``业务逻辑正确``）。
对「业务失败也返回 HTTP 200」的信封式被测系统，这三行恰好把**缺陷现状**
描述成了期望行为 —— 开发读工单会认为无需修复。
实例：jf-20260122392-01-2960（modify 未做商品唯一性校验）。

本文件锁定修复后的行为：
1. 有断言期望值时 → 写真实期望值；
2. 没有时 → 如实写「待人工补充」，**不得**回落成模板套话；
3. ``{}`` 这类"看不出是空还是没采集"的占位不再出现。
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock

from app.services.defect_decision_service import (
    DefectDecision,
    DefectDecisionResult,
    DefectPriority,
)
from app.services.idp_defect_service import IDPDefectService


def _decision(**overrides) -> DefectDecisionResult:
    base = dict(
        decision=DefectDecision.CREATE,
        priority=DefectPriority.HIGH,
        priority_code="priority-1",
        priority_id=1,
        error_type="test_failed",
        failure_summary="服务端接受了已被占用的商品 11281（TCCS001）",
        reason="检测到缺陷",
        defect_type="数据校验缺失",
    )
    base.update(overrides)
    return DefectDecisionResult(**base)


def _render(decision: DefectDecisionResult, **kw) -> str:
    params = dict(
        decision=decision,
        scenario_name="限购商品管理-负路径",
        endpoint="/v1/purchaseLimit/modify",
        method="PUT",
        request_summary={"body": {"id": "9", "goods_ids": "11165,11281"}},
        response_summary={"status_code": 200, "body": {"resultCode": "0", "resultMsg": "success"}},
        reqid="0A000578-MUM4VRA6-5657",
        test_run_id=MagicMock(),
        test_case_id=None,
        report_url=None,
        source_project_key="PR-2",
    )
    params.update(kw)
    return IDPDefectService._generate_description(**params)


def _plain(description: str) -> str:
    ops = json.loads(description)
    assert isinstance(ops, list), "IDP 要求顶层是 Delta 操作数组"
    return "".join(op.get("insert", "") for op in ops)


# ---------------------------------------------------------------------------
# 1) 有断言期望值 → 写真的
# ---------------------------------------------------------------------------

def test_expected_result_comes_from_assertion() -> None:
    """断言反推出的期望值必须出现在「预期结果」段。"""
    text = _plain(
        _render(
            _decision(
                expected_result='"100000"',
                observed_result='"0"',
                assertion_expr="expect(...).toBe()",
            )
        )
    )
    assert "预期结果" in text
    assert '断言期望值: "100000"' in text
    assert "断言表达式: expect(...).toBe()" in text
    assert "取自用例断言的期望值" in text
    # 实测值也要在「实际结果」段出现
    assert '断言实测值: "0"' in text


def test_hardcoded_boilerplate_is_gone() -> None:
    """模板套话必须彻底消失 —— 它在信封式 SUT 上等于把缺陷写没。"""
    text = _plain(_render(_decision(expected_result='"100000"', observed_result='"0"')))
    assert "业务逻辑正确" not in text
    assert "响应结构符合接口契约" not in text
    # 旧的"HTTP 状态码: 200 OK"式期望也不得再出现
    assert "HTTP 状态码: 200 OK" not in text


# ---------------------------------------------------------------------------
# 2) 没有期望值 → 如实说"待人工补充"，不得编
# ---------------------------------------------------------------------------

def test_missing_expectation_is_declared_not_invented() -> None:
    """提取不到期望值时，必须显式声明待补充，且不出现任何推断性表述。"""
    text = _plain(_render(_decision(expected_result=None, observed_result=None)))
    assert "未能从断言中提取期望值" in text
    assert "待人工补充" in text
    # 不得回落到模板套话
    assert "业务逻辑正确" not in text
    assert "响应结构符合接口契约" not in text


def test_expected_status_code_is_not_used_as_expectation() -> None:
    """调用方常见以 `expected_status_code or 200` 传参（那个 200 是猜的）。

    绝不能把它当成断言期望值写进工单 —— 否则等于**伪造**预期。
    """
    text = _plain(
        _render(
            _decision(expected_result=None),
            request_summary={"expected_status_code": 200, "body": {"id": "9"}},
            response_summary={"status_code": 200},
        )
    )
    section = _section(text, "预期结果\n", "实际结果\n")
    assert "HTTP 状态码" not in section, "不得把兜底传参的 200 冒充断言期望值"
    assert "未能从断言中提取期望值" in section


def _section(text: str, start: str, end: str) -> str:
    """截取 ``start`` 与 ``end`` 之间的描述片段（用于按段落精确断言）。"""
    i = text.index(start)
    j = text.index(end, i + len(start))
    return text[i:j]


# ---------------------------------------------------------------------------
# 3) 占位符不再是"看不出空还是没采集"的 {}
# ---------------------------------------------------------------------------

def test_empty_request_params_is_declared_not_dot_braces() -> None:
    """空请求参数要如实标缺（旧实现写 `{}`，读者分不清空对象还是没采集）。"""
    text = _plain(_render(_decision(expected_result="x"), request_summary={"body": {}, "headers": {}}))
    assert "请求参数:" in text
    assert "未采集到请求体" in text
    assert json.dumps({}, ensure_ascii=False) not in text.split("请求参数:")[1].split("响应信息")[0]


def test_sanitizer_marks_missing_instead_of_empty_object() -> None:
    """脱敏函数本身：空 dict 不再返回 `{}`。"""
    assert IDPDefectService._sanitize_request_params({"body": {}, "headers": {}}) == "（未采集到请求体/请求头）"
    assert IDPDefectService._sanitize_request_params(None) == "N/A"
    # 有内容时仍正常脱敏
    out = IDPDefectService._sanitize_request_params({"body": {"password": "p", "id": 1}})
    assert "***REDACTED***" in out
    assert '"id": 1' in out


# ---------------------------------------------------------------------------
# 4) 复现步骤：把能确证的写实
# ---------------------------------------------------------------------------

def test_repro_steps_are_concrete_when_data_available() -> None:
    """有请求体时应指向请求参数段；期望/实测值直接写进第 3 步。"""
    text = _plain(
        _render(_decision(expected_result='"100000"', observed_result='"0"'))
    )
    assert "复现步骤" in text
    assert "向 /v1/purchaseLimit/modify 发送 PUT 请求" in text
    assert "请求体见下方" in text
    assert '观察响应：期望 "100000"，实测 "0"' in text


def test_repro_steps_flag_missing_body() -> None:
    """没有请求体时如实标缺，不写"使用以下请求参数"这种指不到东西的话。"""
    text = _plain(
        _render(_decision(expected_result=None), request_summary={"body": {}, "headers": {}})
    )
    assert "未采集到请求体" in text
    assert "使用以下请求参数" not in text


def test_security_source_type_keeps_its_own_wording() -> None:
    """安全类缺陷走自己的措辞，不被 API 分支改写。"""
    text = _plain(
        _render(
            _decision(),
            source_type="security",
            request_summary={"vulnerability_name": "XSS", "severity": "高"},
        )
    )
    assert "不存在安全漏洞" in text
    assert "输入已正确过滤和校验" in text


# ---------------------------------------------------------------------------
# 5) 项目名取权威配置，不硬编码
# ---------------------------------------------------------------------------

def test_project_name_comes_from_authoritative_config() -> None:
    """回归：项目名曾硬编码为「小杨生煎储值免单活动」，与 idp_projects.yaml 的
    「小杨生煎」不一致，导致每张自动提单都写错项目名（实测工单 925695 中招）。
    """
    assert IDPDefectService._get_project_name("PR-2") == "小杨生煎"

    text = _plain(_render(_decision()))
    assert "PR-2 (小杨生煎)" in text
    assert "储值免单活动" not in text, "陈旧的硬编码项目名不得再出现"


def test_unknown_project_name_is_omitted_not_invented() -> None:
    """取不到项目名时省略括号，而不是编一个「未知项目」。"""
    assert IDPDefectService._get_project_name("NOT-A-REAL-KEY") == ""

    text = _plain(_render(_decision(), source_project_key="NOT-A-REAL-KEY"))
    assert "NOT-A-REAL-KEY\n" in text
    assert "未知项目" not in text
