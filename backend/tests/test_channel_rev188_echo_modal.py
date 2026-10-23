# -*- coding: utf-8 -*-
"""rev187：echo 通道支持真实关联选择器形态。

背景（问卷实测 2026-10-23）：真实 SUT 的关联字段不是「表单项内 .ant-select」，而是
radio 门控（指定门店/指定商品）→ 按钮（选择门店/选择商品）唤起弹窗 → 勾行 →
底部「添加并关闭」→ 表单下方展开明细子表回显；弹窗底部**没有「确定」**。
rev186 的 echo 阶段在这种页面上恒 unverified，且与「定位器配错」不可区分。

本测试锁定：
1. parse 层新增键的语义与缺省（picker/echo_scope 的合法域、非法值必须被拒）；
2. required_fields 支持 checkbox_group（复选组必填不能 fill）；
3. 执行器模板里三条新分支存在（门控、弹窗确认、子表回显）；
4. 缺省值保持旧行为（存量配置零影响）。
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))

from app.services.web_case_compiler import (  # noqa: E402
    COMPILER_REV,
    build_form_plan,
    parse_echo_checks,
)


def _py() -> str:
    return (BACKEND / "app" / "services" / "web_case_compiler.py").read_text(encoding="utf-8")


BASE_ENTRY = {"fields": [{"label": "適用門店", "kind": "multi_select", "pick": 2}]}


# ---------------------------------------------------------------- parse 语义
class TestRev187ParseShape:
    def test_defaults_keep_old_behaviour(self):
        """不填新键 = 旧行为（picker=select / echo_scope=item）——存量配置零影响。"""
        items, rejects = parse_echo_checks([dict(BASE_ENTRY)])
        assert not rejects
        f = items[0]["fields"][0]
        assert f["picker"] == "select"
        assert f["echo_scope"] == "item"
        assert f["gate"] == ""
        assert f["picker_button"] and f["confirm_button"]
        assert f["row_text_column"] == "" and f["echo_column"] == ""

    def test_modal_picker_normalized(self):
        items, rejects = parse_echo_checks([{
            "fields": [{
                "label": "適用門店", "kind": "multi_select", "pick": 2,
                "gate": "指定門店", "picker": "modal",
                "picker_button": r"選\s*擇門店",
                "confirm_button": r"添加並關閉",
                "row_text_column": "店鋪名稱",
                "echo_scope": "subtable", "echo_column": "店鋪名稱",
            }],
        }])
        assert not rejects
        f = items[0]["fields"][0]
        assert f["gate"] == "指定門店"
        assert f["picker"] == "modal"
        assert f["picker_button"] == r"選\s*擇門店"
        assert f["confirm_button"] == r"添加並關閉"
        assert f["row_text_column"] == "店鋪名稱"
        assert f["echo_scope"] == "subtable"
        assert f["echo_column"] == "店鋪名稱"

    def test_invalid_picker_rejected(self):
        _, rejects = parse_echo_checks([{
            "fields": [{"label": "適用門店", "picker": "popup"}],
        }])
        assert rejects and "picker" in rejects[0]["reason"]

    def test_invalid_echo_scope_rejected(self):
        _, rejects = parse_echo_checks([{
            "fields": [{"label": "適用門店", "echo_scope": "page"}],
        }])
        assert rejects and "echo_scope" in rejects[0]["reason"]

    def test_checkbox_group_required_field(self):
        items, rejects = parse_echo_checks([{
            "fields": [{"label": "適用門店"}],
            "required_fields": [{"label": "業務類型", "kind": "checkbox_group",
                                 "value": "外賣、堂食"}],
        }])
        assert not rejects
        rf = items[0]["required_fields"][0]
        assert rf["kind"] == "checkbox_group"
        assert rf["value"] == "外賣、堂食"

    def test_invalid_required_kind_rejected(self):
        _, rejects = parse_echo_checks([{
            "fields": [{"label": "適用門店"}],
            "required_fields": [{"label": "業務類型", "kind": "radio_group"}],
        }])
        assert rejects and "kind" in rejects[0]["reason"]


# ---------------------------------------------------------------- 计划下发
class TestRev187PlanCarriesShape:
    def test_plan_check_keeps_modal_keys(self):
        plan = build_form_plan(
            locators={
                "flow": "route_form",
                "name": "survey",
                "modal": ".ant-form",
                "list_url": "#/survey/list",
                "fields": {"問卷標題": {"kind": "input"}},
                "echo_checks": [{
                    "list_url": "#/survey/list",
                    "fields": [{
                        "label": "適用門店", "kind": "multi_select", "gate": "指定門店",
                        "picker": "modal", "echo_scope": "subtable", "echo_column": "店鋪名稱",
                        "row_text_column": "店鋪名稱",
                    }],
                    "required_fields": [{"label": "問卷標題", "value": "ECHO-x"}],
                }],
            },
            manifest={"assertion_count": 0},
        )
        echo = [c for c in (plan or {}).get("checks", []) if c.get("type") == "echo_check"]
        assert echo, "echo_check 未进计划"
        f = echo[0]["fields"][0]
        assert f["picker"] == "modal"
        assert f["gate"] == "指定門店"
        assert f["echo_scope"] == "subtable"
        assert f["echo_column"] == "店鋪名稱"


# ---------------------------------------------------------------- 执行器模板
class TestRev187ExecutorTemplate:
    def test_gate_branch_present(self):
        src = _py()
        seg = src.split("if (String(f.picker || 'select') === 'modal')")[1][:2600]
        assert "hasText: new RegExp(escRe(gateTxt))" in seg
        assert "pickerBtn" in seg

    def test_confirm_regex_configured(self):
        src = _py()
        assert "f.confirm_button || '添加并关闭" in src
        assert "确认后弹窗未关闭" in src

    def test_subtable_echo_branch_present(self):
        src = _py()
        assert "String(arg.scope) === 'subtable'" in src
        assert "arg.col" in src

    def test_checkbox_group_fill_branch_present(self):
        src = _py()
        assert "String(rf.kind) === 'checkbox_group'" in src
        assert "split(/[、,，;；]/)" in src

    def test_rev_at_least_187(self):
        num = int(COMPILER_REV.replace("rev", ""))
        assert num >= 188
