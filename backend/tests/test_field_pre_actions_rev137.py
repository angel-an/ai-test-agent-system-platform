"""rev137 回归：字段检查**前置动作**（``field_locators.pre_actions``）。

背景（2026-10-20，TR-752/753 实测 + 只读探针）：
问卷「题目」区在点「新增题目」之前**不存在**（探针：点击前 `[class*=questionCard]` = 0，
页面上只有一个 `#sbjTypeName`，点击后新增 `input[maxlength="50"]` 与
`input[maxlength="20"]`）。定位器 `[class*=questionCard] input[...]` 本身是对的，
缺的是"让这块区域渲染出来"的那一次点击 —— 旧口径下只能整批记 unverified，
而 `unverified` 与"选择器配错"在回执上完全同形，配置方会去反复改正确的定位器。

本文件的断言分三层（缺任何一层，这个机制都会退化成"配了但没生效"）：
1. **解析层**：配置归一化与上下限（times/wait_ms），畸形配置进 rejects 而不是静默丢掉；
2. **计划层**：`pre_actions` 进计划、`pre_actions` 不被数成定位器键；
3. **产物层**：`render_form_executor` 的 payload 是**白名单**，漏登记就运行期 undefined
   （rev131 的教训）；并要有 `PRE_ACTION` 自证行与 `for_fields` 按需触发逻辑。
"""

from __future__ import annotations

import json

import pytest

from app.services.web_case_compiler import (
    NON_LOCATOR_CONFIG_KEYS,
    build_form_plan,
    parse_pre_actions,
    render_form_executor,
)


def _loc(**extra):
    base = {
        "fields": {
            "问卷标题": {"kind": "input", "max_length": 20},
            "题目标题": {"kind": "input", "max_length": 50,
                         "selector_hint": '[class*=questionCard] input[maxlength="50"]'},
            "选项1": {"kind": "input", "max_length": 20,
                      "selector_hint": '[class*=questionCard] input[maxlength="20"]'},
        }
    }
    base.update(extra)
    return base


def _manifest(*cases):
    return {"assertion_count": sum(len(c["assertions"]) for c in cases), "cases": list(cases)}


class TestParse:
    def test_full_action_is_normalized(self):
        actions, rejects = parse_pre_actions([
            {"labels": ["新增题目", "添加题目"], "for_fields": ["题目标题", "选项1"],
             "times": 1, "wait_ms": 600, "reason": "题目卡片默认不渲染"},
        ])
        assert rejects == []
        assert actions == [{
            "labels": ["新增题目", "添加题目"],
            "for_fields": ["题目标题", "选项1"],
            "times": 1,
            "wait_ms": 600,
            "reason": "题目卡片默认不渲染",
        }]

    def test_string_shorthand_and_click_alias(self):
        """``pre_actions: ["新增题目"]`` 与 ``{"click": "..."}`` 都应收敛到同一结构。

        为什么要这个便利写法：配置界面通常是手填 JSON，一个字符串能表达的事
        逼人写三层嵌套，最后多半写成错的。
        """
        assert parse_pre_actions("新增题目")[0][0]["labels"] == ["新增题目"]
        assert parse_pre_actions([{"click": "新增题目"}])[0][0]["labels"] == ["新增题目"]
        assert parse_pre_actions({"labels": ["新增题目"]})[0][0]["labels"] == ["新增题目"]

    def test_times_and_wait_are_clamped(self):
        """上下限是硬约束：每项都在检查循环里同步等待，写错会让整批超时。"""
        actions, rejects = parse_pre_actions([{"labels": ["x"], "times": 99, "wait_ms": 999999}])
        assert rejects == []
        assert actions[0]["times"] == 5
        assert actions[0]["wait_ms"] == 10000

        low, _ = parse_pre_actions([{"labels": ["x"], "times": 0, "wait_ms": -20}])
        assert low[0]["times"] == 1
        assert low[0]["wait_ms"] == 0, "显式 0 是合法值（点完不等），不能被当成缺省"

    def test_malformed_items_are_rejected_with_index(self):
        actions, rejects = parse_pre_actions([{}, {"labels": ""}, "   ", 123, {"labels": ["ok"]}])
        assert [a["labels"] for a in actions] == [["ok"]]
        reasons = {r["detail"] for r in rejects}
        assert reasons == {"index=0", "index=1", "index=2", "index=3"}, reasons

    def test_empty_config_means_no_action(self):
        assert parse_pre_actions(None) == ([], [])
        assert parse_pre_actions("") == ([], [])
        assert parse_pre_actions([]) == ([], [])

    def test_non_list_scalar_is_rejected_not_crashing(self):
        actions, rejects = parse_pre_actions(42)
        assert actions == []
        assert rejects and "必须是数组" in rejects[0]["reason"]


class TestPlan:
    def test_pre_actions_land_in_plan(self):
        loc = _loc(pre_actions=[{"labels": ["新增题目"], "for_fields": ["题目标题"],
                                 "reason": "题目卡片默认不渲染"}])
        plan = build_form_plan(_manifest(
            {"identifier": "TC-Q1", "rule_id": "FR-1", "field": "题目标题",
             "assertions": [{"kind": "required", "field": "题目标题"}]},
        ), loc)
        assert plan["pre_actions"] == [{"labels": ["新增题目"], "for_fields": ["题目标题"],
                                        "times": 1, "wait_ms": 800,
                                        "reason": "题目卡片默认不渲染"}]
        assert plan["pre_action_rejects"] == []

    def test_pre_action_rejects_visible_in_plan(self):
        """被拒的动作必须进回执——否则配置方只看到"配了、还是 unverified"。"""
        loc = _loc(pre_actions=[{"labels": []}])
        plan = build_form_plan(_manifest(
            {"identifier": "TC-Q1", "rule_id": "FR-1", "field": "题目标题",
             "assertions": [{"kind": "required", "field": "题目标题"}]},
        ), loc)
        assert plan["pre_actions"] == []
        assert plan["pre_action_rejects"][0]["reason"].startswith("缺少 labels")

    def test_pre_actions_is_not_a_locator_key(self):
        """行为配置不得被数成定位器（与 aliases/defaults 同一条纪律）。"""
        assert "pre_actions" in NON_LOCATOR_CONFIG_KEYS


class TestRenderedArtifact:
    """产物层：payload 白名单 + 自证行 + 按需触发。"""

    def _render(self, pre_actions):
        loc = _loc(pre_actions=pre_actions)
        plan = build_form_plan(_manifest(
            {"identifier": "TC-Q1", "rule_id": "FR-1", "field": "题目标题",
             "assertions": [{"kind": "required", "field": "题目标题"}]},
        ), loc)
        return render_form_executor(plan)

    def test_payload_carries_pre_actions(self):
        """rev131 的教训：计划里有、payload 白名单没登记 ⇒ 运行期恒 undefined。"""
        script = self._render([{"labels": ["新增题目"], "for_fields": ["题目标题"]}])
        marker = script.split("const FORM_PLAN", 1)[1].split("\n", 1)[0]
        payload = json.loads(marker.split("=", 1)[1].strip().rstrip(";"))
        assert payload["pre_actions"][0]["labels"] == ["新增题目"]
        assert payload["pre_action_rejects"] == []

    def test_executor_has_self_evidence_and_scope_gate(self):
        script = self._render([{"labels": ["新增题目"], "for_fields": ["题目标题"]}])
        assert "PRE_ACTION ' + JSON.stringify" in script
        assert "PRE_ACTIONS_PLAN" in script
        # 按需触发：默认选中必须在任何点击之前观测，这个不变式靠 for_fields 守住
        assert "scopeFields.includes(field)" in script
        # 前置动作失败要报"前置动作未完成"，而不是"未定位到输入框"
        assert "前置动作未完成" in script

    def test_no_pre_actions_keeps_old_behavior(self):
        script = self._render(None)
        marker = script.split("const FORM_PLAN", 1)[1].split("\n", 1)[0]
        payload = json.loads(marker.split("=", 1)[1].strip().rstrip(";"))
        assert payload["pre_actions"] == []
