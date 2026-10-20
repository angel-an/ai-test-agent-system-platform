"""rev138 回归：字段级**作用域**声明（``fields.<字段>.scope = "page"``）。

背景（2026-10-20，TR-754 实测 + 探针）：rev137 让「新增题目」真的点成了
（`PRE_ACTION ok=true`），但四条断言仍报「未定位到输入框」。探针给出结构原因：
**题目卡片渲染在 `<form class="ant-form">` 之外**——
`form.contains(card) === false`，同一选择器全文档命中 1、表单内命中 0。

所以这是两个不同故障，过去共用同一句日志而无法区分：
  * `pre_actions`（rev137）：区域**根本没渲染** → 需要一次点击；
  * `scope`（rev138）：渲染了，但**不在表单容器这一层** → 需要切定位层。

为什么是"按字段切层"而不是"把 modal 放宽到整页"：`modal` 同时是默认值/条件显示/
单选 `:has-text` 的匹配范围，放宽会让这些检查跨区域误命中。作用域必须跟着字段走。

本文件同时锁死「现场行诚实化」：`[observation]` 的 `tried` 必须写出**真正使用**的
`selector_hint`（旧实现只回显 `placeholder_hint`，未配时恒为 ''，看起来像"没配 hint"）。
"""

from __future__ import annotations

import json

from app.services.web_case_compiler import build_form_plan, render_form_executor


def _loc(**extra):
    base = {
        "fields": {
            "问卷标题": {"kind": "input", "max_length": 20, "selector_hint": "#sbjTypeName"},
            "题目标题": {"kind": "input", "max_length": 50, "scope": "page",
                         "selector_hint": '[class*=questionCard] input[maxlength="50"]'},
            "选项1": {"kind": "input", "max_length": 20, "scope": "page",
                      "selector_hint": '[class*=questionCard] input[maxlength="20"]'},
        }
    }
    base.update(extra)
    return base


def _manifest(*cases):
    return {"assertion_count": sum(len(c["assertions"]) for c in cases), "cases": list(cases)}


def _payload(script: str) -> dict:
    marker = script.split("const FORM_PLAN", 1)[1].split("\n", 1)[0]
    return json.loads(marker.split("=", 1)[1].strip().rstrip(";"))


class TestScope:
    def test_scope_survives_into_locator_payload(self):
        """`scope` 走 `locators.fields` 下发——执行器读不到就等于没声明。"""
        plan = build_form_plan(_manifest(
            {"identifier": "TC-Q1", "rule_id": "FR-1", "field": "题目标题",
             "assertions": [{"kind": "required", "field": "题目标题"}]},
        ), _loc())
        payload = _payload(render_form_executor(plan))
        fields = payload["locators"]["fields"]
        assert fields["题目标题"]["scope"] == "page"
        assert "scope" not in fields["问卷标题"], "未声明的字段不应被默认写上 scope"

    def test_executor_switches_layer_not_widens_modal(self):
        script = render_form_executor(build_form_plan(_manifest(
            {"identifier": "TC-Q1", "rule_id": "FR-1", "field": "题目标题",
             "assertions": [{"kind": "required", "field": "题目标题"}]},
        ), _loc()))
        assert "function scopeOf(" in script
        # hint 与 label 兜底都必须经过 scopeOf（只改一处会出现"配 hint 的能用、
        # 没配 hint 的仍落空"的半修状态）
        assert "scopeOf(modal, def).locator(h)" in script
        assert "fieldInput(sc, field, tag)" in script
        # 声明是否真的生效必须有自证行
        assert "LOCATE_SCOPE ' + JSON.stringify" in script
        assert "LOCATE_SCOPE_INVALID" in script

    def test_invalid_scope_is_reported_and_falls_back_to_form(self):
        """拼错的作用域不能静默当 form 用——否则"配了没生效"又变回不可见。"""
        script = render_form_executor(build_form_plan(_manifest(
            {"identifier": "TC-Q1", "rule_id": "FR-1", "field": "题目标题",
             "assertions": [{"kind": "required", "field": "题目标题"}]},
        ), _loc()))
        seg = script.split("function scopeOf(", 1)[1][:900]
        assert "accepted: ['form', 'page']" in seg
        assert "fell_back_to: 'form'" in seg


class TestObservationHonesty:
    def test_tried_reports_selector_hint_not_placeholder_hint(self):
        """现场行必须写**真正用过**的选择器。

        旧实现只回显 `placeholder_hint`；本 SUT 未配该键 ⇒ `hint: ''`，
        而执行时用的是 `selector_hint`（配了的）。读者会得出"没配 hint"的错误结论，
        于是去补一个本来就存在的配置——TR-754 的排障就是这样被带偏的。
        """
        script = render_form_executor(build_form_plan(_manifest(
            {"identifier": "TC-Q1", "rule_id": "FR-1", "field": "题目标题",
             "assertions": [{"kind": "required", "field": "题目标题"}]},
        ), _loc()))
        seg = script.split("async function reportLocatorMiss(", 1)[1][:1200]
        assert "locator: 'selector_hint'" in seg
        assert "hint: def.selector_hint" in seg
        # 定位层也要写明，否则"修选择器"和"修 scope"仍然只能靠猜
        assert "scope: String(def.scope || 'form')" in seg
