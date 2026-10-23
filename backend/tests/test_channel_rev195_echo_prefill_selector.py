# -*- coding: utf-8 -*-
"""rev195：echo 预填项必须复用字段定位器的精确选择器。

背景（2026-10-23，TR-806）：保存被前端拦住，SUT 提示「题目1：请输入标题」。
根因不是配置缺项，而是**标签对不上**——规则字段名是「题目标题」，问卷题目卡片里
`ant-form-item` 的 label 是「标题」；预填按 label 找字段行，找不到就静默跳过，
题目标题空着 ⇒ 保存必然被拦。字段定位器里本就有精确选择器
（`[class*=questionCard] input[maxlength="50"]`），却在预填通道里没用上。

本用例锁：① label → fields 键的显式映射（含 aliases），选择器随计划下发；
② 未配置选择器时行为不变（退回标签匹配）；③ 执行器的取用优先级与 via 标注。
"""

from __future__ import annotations

import app.services.web_case_compiler as m
from app.services.web_case_compiler import build_form_plan


def _loc(fields=None, echo=None, aliases=None):
    loc = {
        "name": "问卷",
        "fields": fields if fields is not None else {
            "题目标题": {"kind": "input", "scope": "page",
                     "selector_hint": '[class*=questionCard] input[maxlength="50"]'},
            "业务类型": {"kind": "checkbox_group", "scope": "page",
                     "selector_hint": ".ant-form-item:has(label:text-is('业务类型'))"},
        },
        "echo_checks": echo if echo is not None else [{
            "name": "echo:适用门店",
            "case_id": "TC-1",
            "required_fields": [
                {"label": "题目标题", "kind": "input", "value": "回显题"},
                {"label": "业务类型", "kind": "input", "value": "外卖"},
                {"label": "不存在的字段", "kind": "input", "value": "x"},
            ],
            "fields": [{"label": "适用门店", "kind": "multi_select", "gate": "指定门店",
                        "picker": "modal", "picker_button": "选择", "confirm_button": "添加并关闭"}],
        }],
    }
    if aliases:
        loc["aliases"] = aliases
    return loc


def _plan(loc=None):
    return build_form_plan({}, loc or _loc())


def _req(plan, label):
    for item in plan["checks"]:
        if item.get("type") != "echo_check":
            continue
        for rf in item["required_fields"]:
            if rf["label"] == label:
                return rf
    raise AssertionError(f"required_field 未进入计划：{label}")


class TestSelectorMapping:
    def test_selector_and_scope_are_attached(self):
        rf = _req(_plan(), "题目标题")
        assert rf["selector"] == '[class*=questionCard] input[maxlength="50"]'
        assert rf["scope"] == "page"

    def test_field_kind_corrects_default_input(self):
        """配置只写 input、字段侧声明 checkbox_group 时要纠正（否则会去 fill 一个复选组）。"""
        rf = _req(_plan(), "业务类型")
        assert rf["kind"] == "checkbox_group"
        assert rf["field_kind"] == "checkbox_group"

    def test_explicit_kind_wins(self):
        loc = _loc(echo=[{
            "name": "echo:x", "case_id": "TC-2",
            "required_fields": [{"label": "题目标题", "kind": "textarea", "value": "v"}],
            "fields": [{"label": "适用门店", "kind": "multi_select", "gate": "指定门店",
                        "picker": "modal", "picker_button": "选择", "confirm_button": "添加并关闭"}],
        }])
        assert _req(build_form_plan({}, loc), "题目标题")["kind"] == "textarea"

    def test_unknown_label_keeps_label_matching(self):
        rf = _req(_plan(), "不存在的字段")
        assert "selector" not in rf

    def test_alias_is_honoured(self):
        loc = _loc(aliases={"题目": "题目标题"},
                   echo=[{
                       "name": "echo:x", "case_id": "TC-3",
                       "required_fields": [{"label": "题目", "kind": "input", "value": "v"}],
                       "fields": [{"label": "适用门店", "kind": "multi_select", "gate": "指定门店",
                                   "picker": "modal", "picker_button": "选择", "confirm_button": "添加并关闭"}],
                   }])
        rf = _req(build_form_plan({}, loc), "题目")
        assert rf["selector"].startswith("[class*=questionCard]")


class TestExecutorConsumesSelector:
    def test_prefers_selector_then_falls_back(self):
        ts = m._FORM_EXECUTOR_TS
        assert "const anchor = selHint ? page.locator(selHint).first() : item;" in ts
        assert "via: selHint ? 'selector' : 'label'" in ts

    def test_selector_miss_is_reported(self):
        ts = m._FORM_EXECUTOR_TS
        assert "选择器未命中" in ts

    def test_rev_at_least_195(self):
        assert int(m.COMPILER_REV.replace("rev", "")) >= 195
