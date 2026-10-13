# -*- coding: utf-8 -*-
"""P1-3 阶段 B：规则级负路径场景的**声明式配置层**测试（不启动浏览器）。

覆盖两类风险：

1. **配置正确性**：合法场景被完整解析；非法场景（缺 scenario_id / 缺 api_path_hint /
   未知 setup 动作 / 无判定依据 / id 重复）必须**在计划期被拒并说明原因**——
   静默丢弃会变成"配置了但从未执行"，比当场报错危险得多。
2. **不回归**：未配置 negative_scenarios 的模板，计划与渲染产物必须与改动前一致。

浏览器级行为（真实提交 → 服务端响应判定）见
``test_web_negative_scenario_browser_local.py``。
"""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from app.services.web_case_compiler import (
    SETUP_ACTIONS,
    build_form_plan,
    compile_cases,
    parse_negative_scenarios,
    render_form_executor,
)

# --------------------------------------------------------------------------- #
# 夹具
# --------------------------------------------------------------------------- #
CASE = SimpleNamespace(
    identifier="TC-200998", name="限购商品管理-编辑追加重复商品", module="local",
    description="",
    custom_fields={
        "requirement_rule_id": "FR-PL-004",
        "requirement_rule": {"field": "限购商品"},
        "assertions": [{"kind": "visibility", "expect": "visible", "field": "限购商品"}],
    },
    test_case_steps=[],
)

BASE_LOCATORS = {
    "name": "purchase_limit", "flow": "modal", "form_button": ["新建"],
    "modal": ".ant-modal", "fields": {"限购商品": {"kind": "input"}},
    "field_modes": {}, "default_field_mode": {}, "defaults": [], "toggles": [],
    "upload_fields": {},
}

VALID = {
    "scenario_id": "NEG-DUP-GOODS-EDIT",
    "rule_ref": "同一商品仅能属于一条活动",
    "target_entry": "edit",
    "case_id": "TC-200998",
    "rule_id": "FR-PL-004",
    "setup": [
        {"action": "click", "labels": ["编辑"]},
        {"action": "wait", "ms": 1500},
        {"action": "click", "labels": ["选择限购商品"]},
        {"action": "select_row", "row_index": 1},
        {"action": "click", "labels": ["确 认", "确认"]},
    ],
    "submit_label": ["提 交", "提交"],
    "expect_reject": {
        "http_ok": True, "result_code_not": "0",
        "message_contains": ["已新建", "已存在", "重复"],
        # 响应匹配用的是 URL **子串**
        "api_path_hint": "/v1/purchaseLimit/modify",
        # 写隔离放行用的是 host **后缀** + path **前缀**：真实请求打在网关主机上，
        # 路径带网关前缀，两者与上面的子串并不等价（实测 DEFECT-001）。
        "allow": {
            "host_suffix": "imarketing-uat.xysjg.com",
            "path_prefix": "/yundt-saas-gateway/api/mini-mgmt-app/v1/purchaseLimit/modify",
            "methods": ["PUT"],
        },
    },
}


def _plan(negative: object, *, locators: dict | None = None) -> dict:
    loc = {**BASE_LOCATORS, **(locators or {})}
    loc["negative_scenarios"] = negative
    plan = build_form_plan(compile_cases([CASE]), loc)
    assert plan is not None
    return plan


# --------------------------------------------------------------------------- #
# 合法场景
# --------------------------------------------------------------------------- #
class TestScenarioParsing:
    def test_valid_scenario_is_fully_normalized(self):
        plan = _plan([VALID])
        scenarios = plan["negative_scenarios"]
        assert len(scenarios) == 1
        sc = scenarios[0]
        assert sc["scenario_id"] == "NEG-DUP-GOODS-EDIT"
        assert sc["rule_ref"] == "同一商品仅能属于一条活动"
        assert sc["target_entry"] == "edit"
        assert sc["case_id"] == "TC-200998" and sc["rule_id"] == "FR-PL-004"
        #: 归属来源标记：台账据此把"场景证据"与"字段级检查"分开归类
        assert sc["source"] == "negative_scenario"
        assert [s["action"] for s in sc["setup"]] == [
            "click", "wait", "click", "select_row", "click"]
        assert sc["setup"][3]["row_index"] == 1
        assert sc["submit_label"] == ["提 交", "提交"]
        assert sc["expect_reject"]["api_path_hint"] == "/v1/purchaseLimit/modify"
        assert sc["expect_reject"]["result_code_not"] == "0"
        assert sc["expect_reject"]["message_contains"] == ["已新建", "已存在", "重复"]
        assert plan["negative_scenario_rejects"] == []

    def test_scenarios_are_not_reported_as_locators(self):
        """``negative_scenarios`` 是行为配置，不是定位器。

        —— 它必须从预览的"定位器键"列表里剔除：列进去会让配置方以为
        多注册了一个叫这个名字的定位器（实测于 P6 预览接口）。
        剔除用 ``NON_LOCATOR_CONFIG_KEYS``（执行侧同一份），而不是只剔
        ``REGISTRATION_META_KEYS``，否则两端又会分叉。
        """
        from app.services.web_case_compiler import (
            NON_LOCATOR_CONFIG_KEYS,
            REGISTRATION_META_KEYS,
        )
        assert "negative_scenarios" in NON_LOCATOR_CONFIG_KEYS
        # 元信息集合保持"只放来源信息"的语义，不因新增配置键而膨胀
        assert "negative_scenarios" not in REGISTRATION_META_KEYS
        assert set(REGISTRATION_META_KEYS) <= set(NON_LOCATOR_CONFIG_KEYS)

    def test_labels_accept_single_string_and_drop_blanks(self):
        raw = dict(VALID, submit_label="提 交",
                   setup=[{"action": "click", "labels": "编辑"}])
        scenarios, rejects = parse_negative_scenarios([raw])
        assert rejects == []
        assert scenarios[0]["submit_label"] == ["提 交"]
        assert scenarios[0]["setup"][0]["labels"] == ["编辑"]

    def test_scenario_without_setup_is_allowed(self):
        """无前置条件（例如直接在一个已打开的表单里提交）是合法用法。"""
        raw = dict(VALID)
        raw.pop("setup")
        scenarios, rejects = parse_negative_scenarios([raw])
        assert rejects == [] and scenarios[0]["setup"] == []

    def test_setup_actions_are_a_closed_set(self):
        assert SETUP_ACTIONS == frozenset({"click", "select_row", "append", "fill", "wait"})

    def test_all_declared_setup_actions_are_parseable(self):
        steps = [{"action": a, "labels": ["x"], "field": "限购商品", "value": "1"}
                 for a in sorted(SETUP_ACTIONS)]
        raw = dict(VALID, setup=steps)
        scenarios, rejects = parse_negative_scenarios([raw])
        assert rejects == []
        assert [s["action"] for s in scenarios[0]["setup"]] == sorted(SETUP_ACTIONS)


# --------------------------------------------------------------------------- #
# 非法场景：**拒绝并说明原因**，绝不静默丢弃
# --------------------------------------------------------------------------- #
class TestScenarioRejection:
    def test_missing_scenario_id_is_rejected(self):
        raw = {k: v for k, v in VALID.items() if k != "scenario_id"}
        scenarios, rejects = parse_negative_scenarios([raw])
        assert scenarios == []
        assert rejects and rejects[0]["reason"] == "缺少 scenario_id"

    def test_missing_api_path_hint_is_rejected(self):
        """没有接口路径 → 写隔离无法收窄 → 提交必被阻断 → 计划期就拒绝（fail-loud）。"""
        raw = dict(VALID, expect_reject={"result_code_not": "0",
                                         "message_contains": ["已新建"]})
        scenarios, rejects = parse_negative_scenarios([raw])
        assert scenarios == []
        assert rejects[0]["reason"] == "缺少 expect_reject.api_path_hint"
        assert "收窄到单接口" in rejects[0]["detail"]

    def test_unknown_setup_action_is_rejected(self):
        raw = dict(VALID, setup=[{"action": "teleport", "labels": ["编辑"]}])
        scenarios, rejects = parse_negative_scenarios([raw])
        assert scenarios == []
        assert rejects[0]["reason"] == "setup 步骤非法"
        assert "teleport" in rejects[0]["detail"]

    def test_expect_without_any_criterion_is_rejected(self):
        """两个判据都空 → 任何结果都能被解释成"拒绝"，属臆造，必须拒绝。"""
        raw = dict(VALID, expect_reject={"api_path_hint": "/v1/x/modify",
                                         "result_code_not": "", "message_contains": []})
        scenarios, rejects = parse_negative_scenarios([raw])
        assert scenarios == []
        assert rejects[0]["reason"] == "expect_reject 无有效判据"

    def test_duplicate_scenario_id_is_rejected(self):
        """重复 id 会让断言归属歧义（同一 id 两条结论不知道记哪条）。"""
        scenarios, rejects = parse_negative_scenarios([VALID, dict(VALID, rule_ref="另一条")])
        assert len(scenarios) == 1
        assert rejects[0]["reason"] == "scenario_id 重复"

    def test_illegal_target_entry_is_rejected(self):
        scenarios, rejects = parse_negative_scenarios([dict(VALID, target_entry="hack")])
        assert scenarios == []
        assert rejects[0]["reason"] == "target_entry 非法"

    def test_non_list_payload_is_rejected_not_crashed(self):
        scenarios, rejects = parse_negative_scenarios({"scenario_id": "x"})
        assert scenarios == []
        assert rejects[0]["reason"] == "negative_scenarios 必须是数组"

    def test_malformed_entry_does_not_break_the_rest(self):
        """畸形输入不得阻断编译：好场景照常可用，坏场景逐条报因。"""
        scenarios, rejects = parse_negative_scenarios(
            ["不是对象", VALID, {"scenario_id": "X", "expect_reject": 5}])
        assert [s["scenario_id"] for s in scenarios] == ["NEG-DUP-GOODS-EDIT"]
        assert len(rejects) == 2

    def test_empty_payload_yields_nothing_and_no_noise(self):
        assert parse_negative_scenarios(None) == ([], [])
        assert parse_negative_scenarios([]) == ([], [])

    def test_rejects_are_visible_to_the_executor(self):
        """被拒场景必须随计划下发（→ `[negative-skip]` 行），否则"配了没跑"不可见。"""
        plan = _plan([VALID, {"scenario_id": "BAD",
                              "expect_reject": {"result_code_not": "0"}}])
        assert [s["scenario_id"] for s in plan["negative_scenarios"]] == ["NEG-DUP-GOODS-EDIT"]
        assert [r["scenario_id"] for r in plan["negative_scenario_rejects"]] == ["BAD"]
        script = render_form_executor(plan)
        assert "negative_scenario_rejects" in script
        assert "[negative-skip]" in script


# --------------------------------------------------------------------------- #
# 计划 / 渲染层集成
# --------------------------------------------------------------------------- #
class TestPlanIntegration:
    def test_plan_keeps_field_checks_alongside_scenarios(self):
        """字段级检查与规则级场景**共存**：新增场景不得挤掉原有断言。

        用 `enum + radio` 拿一条只读可验证的 `radio_options` 检查（不产生提交），
        这样"共存"这件事与场景自身的提交行为互不干扰。
        """
        case = SimpleNamespace(
            identifier="TC-200998", name="限购商品管理-编辑追加重复商品", module="local",
            description="",
            custom_fields={
                "requirement_rule_id": "FR-PL-004",
                "requirement_rule": {"field": "活动类型"},
                "assertions": [{"kind": "enum", "field": "活动类型", "allowed": ["单品", "整单"],
                                "expect": "accept", "rule": "活动类型仅支持单品/整单"}],
            },
            test_case_steps=[],
        )
        loc = {**BASE_LOCATORS, "fields": {"活动类型": {"kind": "radio"}},
               "negative_scenarios": [VALID]}
        plan = build_form_plan(compile_cases([case]), loc)
        assert plan is not None
        assert plan["executable"] is True
        assert [c["type"] for c in plan["checks"]] == ["radio_options"]
        assert len(plan["negative_scenarios"]) == 1

    def test_scenarios_alone_make_the_plan_executable(self):
        """只有场景、没有字段级检查时也必须注入执行器，否则规则级负路径永远跑不到。

        DEFECT-001 就是这种形态：违规主体是"字段值与外部状态的冲突"，
        根本没有对应的字段级断言。
        """
        loc = {**BASE_LOCATORS, "negative_scenarios": [VALID],
               # 断言字段名与定位器表不一致 → 走 skipped 分支，checks 为空
               "fields": {"另一个字段": {"kind": "input"}}}
        plan = build_form_plan(compile_cases([CASE]), loc)
        assert plan is not None
        assert plan["checks"] == []
        assert plan["executable"] is True
        assert len(plan["negative_scenarios"]) == 1

    def test_only_rejects_still_return_a_non_executable_plan(self):
        """全部场景都被拒、且无字段检查：仍要返回计划（保留拒绝现场），但不可执行。"""
        loc = {**BASE_LOCATORS, "fields": {"另一个字段": {"kind": "input"}},
               "negative_scenarios": [{"scenario_id": "BAD"}]}
        plan = build_form_plan(compile_cases([CASE]), loc)
        assert plan is not None
        assert plan["executable"] is False
        assert plan["negative_scenarios"] == []
        assert [r["scenario_id"] for r in plan["negative_scenario_rejects"]] == ["BAD"]

    def test_render_payload_carries_scenarios_verbatim(self):
        plan = _plan([VALID])
        script = render_form_executor(plan)
        # payload 是 JSON 直插（@@FORM_PLAN@@ 由 .replace 替换），逐字段核对
        start = script.index("const FORM_PLAN = ") + len("const FORM_PLAN = ")
        end = script.index("\n", start)
        payload = json.loads(script[start:end].rstrip(";"))
        assert payload["negative_scenarios"] == plan["negative_scenarios"]
        assert payload["negative_scenario_rejects"] == []

    def test_render_adds_the_scenario_phase_and_counters(self):
        script = render_form_executor(_plan([VALID]))
        assert "runNegativeScenarios" in script
        # 三个判定出口必须都在（通过 / 疑似缺陷坐实 / 无法确认）
        assert "服务端按规则拒绝" in script
        assert "疑似缺陷坐实" in script
        assert "无法确认为本规则生效" in script
        # 计数器独立于字段级 submit_phase
        assert "negative_scenario_phase" in script
        assert "NEGATIVE_PHASE" in script

    def test_render_without_scenarios_still_emits_the_phase_guard(self):
        """不配场景的模板也必须能渲染（且不产生额外行为）。"""
        plan = _plan([])
        script = render_form_executor(plan)
        assert "runNegativeScenarios" in script
        assert plan["negative_scenarios"] == []

    def test_write_isolation_is_narrowed_to_declared_endpoints_only(self):
        """写隔离规则**只**来自场景声明的目标接口（设计文档 §四"收窄到单接口"）。"""
        plan = _plan([VALID])
        allow = plan["negative_scenarios"][0]["expect_reject"]["allow"]
        assert allow["host_suffix"] == "imarketing-uat.xysjg.com"
        assert allow["path_prefix"].endswith("/v1/purchaseLimit/modify")
        assert allow["methods"] == ["PUT"]
        script = render_form_executor(plan)
        # 执行器按 allow 构造规则（不再把响应匹配用的子串当前缀）
        assert "allow.path_prefix || expect.api_path_hint" in script
        assert "allow.host_suffix || pageHost" in script
        # 不装隔离就不提交（fail-closed）
        assert "写隔离策略未安装成功，为避免未知写副作用拒绝执行" in script

    def test_allow_defaults_collapse_to_the_simple_same_origin_case(self):
        """不写 allow 时退化为"同源直连、路径等于 hint"——对简单站点仍然可用。"""
        raw = dict(VALID, expect_reject={k: v for k, v in VALID["expect_reject"].items()
                                         if k != "allow"})
        scenario, rejects = parse_negative_scenarios([raw])
        assert rejects == []
        allow = scenario[0]["expect_reject"]["allow"]
        assert allow == {"host_suffix": "", "path_prefix": "/v1/purchaseLimit/modify",
                         "methods": ["POST", "PUT", "PATCH"]}

    def test_relative_allow_path_prefix_is_rejected(self):
        """放行规则按 path.startsWith 匹配 → 相对路径必然永远匹配不上，必须当场拒绝。"""
        raw = dict(VALID, expect_reject={
            **VALID["expect_reject"],
            "allow": {"path_prefix": "v1/purchaseLimit/modify"}})
        scenarios, rejects = parse_negative_scenarios([raw])
        assert scenarios == []
        assert rejects[0]["reason"] == "allow.path_prefix 必须是绝对路径前缀"

    def test_no_placeholder_collision_in_payload(self):
        """payload 里若出现 @@DEFAULT_PHONE@@ 会同名被替换（历史踩坑：replace 顺序）。"""
        raw = dict(VALID, rule_ref="含 @@FORM_PLAN@@ 的名字")
        scenario, rejects = parse_negative_scenarios([raw])
        assert rejects == []
        assert "@@FORM_PLAN@@" in scenario[0]["rule_ref"]  # 值本身合法

    def test_no_fields_means_no_plan(self):
        """定位器表没有字段 → 与改动前一致，返回 None（不跨业务回退）。

        注：`modal` / `form_button` 的完整性是**执行期**契约（TR-163 那次硬失败），
        本层只以 `fields` 为存在性判据（见 `resolve_form_locators`）——因此这里
        只对 `fields` 断言，不假装另外两个键也在这里把关。
        """
        loc = {**BASE_LOCATORS, "negative_scenarios": [VALID]}
        loc.pop("fields")
        assert build_form_plan(compile_cases([CASE]), loc) is None
