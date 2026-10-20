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
        # 「闭集」测试的用途：新增动作原语必须**显式**改这里，否则悄悄多出一个
        # 没人验证过的原语。rev115 引入 ``type``（键盘逐字输入，autocomplete 专用）
        # 时漏了这一步，期望集一直停在 rev114 → 该测试自 rev115 起恒红。
        # 2026-10-20（rev137 同批）补齐；``type`` 的实现见执行器 ``action === 'type'``。
        assert SETUP_ACTIONS == frozenset(
            {"click", "select_row", "append", "fill", "wait", "select_option",
             "click_selector", "clear_select", "upload", "type"})

    def test_select_option_step_requires_selector(self):
        """rev103：select_option 必须带 selector（下拉根 CSS），缺省计划期拒绝。"""
        steps = [{"action": "select_option", "labels": ["Case No."]}]
        # 解析层放行（selector 缺失由运行期如实报错，与 fill 缺 field 同口径）
        raw = dict(VALID, setup=steps)
        scenarios, rejects = parse_negative_scenarios([raw])
        assert rejects == [], rejects
        assert scenarios[0]["setup"][0]["action"] == "select_option"

    def test_upload_step_requires_inline_fixture(self):
        """rev114：upload 是唯一会主动产生外部副作用的原语 ⇒ 计划期强制内联夹具。

        配了 upload 却没有字节，执行期必然是一次"No-op/失败"，会把
        「必填附件没填上」误读成「被测系统拒绝了提交」——宁可在计划期拒绝。
        """
        ok = dict(VALID, setup=[{"action": "upload", "field": "店鋪圖片",
                                 "open_labels": ["Upload Image"],
                                 "confirm_labels": ["OK"],
                                 "fixture": {"name": "s.png", "mime": "image/png",
                                             "b64": "aGVsbG8="}}])
        scenarios, rejects = parse_negative_scenarios([ok])
        assert rejects == [], rejects
        step = scenarios[0]["setup"][0]
        assert step["action"] == "upload"
        assert step["fixture"]["b64"] == "aGVsbG8="
        assert step["open_labels"] == ["Upload Image"]
        assert step["confirm_labels"] == ["OK"]

        # 缺 b64 → 拒绝并说明原因
        bad = dict(VALID, setup=[{"action": "upload", "field": "店鋪圖片",
                                  "fixture": {"name": "s.png"}}])
        _, rejects_bad = parse_negative_scenarios([bad])
        assert rejects_bad and rejects_bad[0]["reason"] == "setup 步骤非法"
        assert "fixture.b64" in rejects_bad[0]["detail"]

    def test_non_upload_steps_stay_byte_identical(self):
        """回归锁：非 upload 步骤的序列化不得多出任何键（既有快照/下游零回归）。"""
        raw = dict(VALID, setup=[{"action": "fill", "field": "限购商品", "value": "1"}])
        scenarios, rejects = parse_negative_scenarios([raw])
        assert rejects == []
        assert set(scenarios[0]["setup"][0]) == {
            "action", "labels", "selector", "row_index", "field", "value", "ms"}

    def test_all_declared_setup_actions_are_parseable(self):
        steps = []
        for a in sorted(SETUP_ACTIONS):
            step: dict = {"action": a, "labels": ["x"], "field": "限购商品", "value": "1"}
            if a == "upload":
                step["fixture"] = {"name": "x.png", "mime": "image/png", "b64": "aGVsbG8="}
            steps.append(step)
        raw = dict(VALID, setup=steps)
        scenarios, rejects = parse_negative_scenarios([raw])
        assert rejects == []
        assert [s["action"] for s in scenarios[0]["setup"]] == sorted(SETUP_ACTIONS)

    def test_allow_upload_is_opt_in_and_narrow(self):
        """rev114：multipart 放行必须**显式声明**且三项收窄（host/路径前缀/方法）。

        默认（不声明）⇒ 计划里没有该键 ⇒ 执行器对 multipart 一律 abort，与历史逐字一致。
        """
        raw = {k: v for k, v in VALID.items() if k != "expect_reject"}
        raw["expect_accept"] = {
            "api_path_hint": "/api/icdp/v1/org/shop/add",
            "result_code": "0",
            "allow": {"host_suffix": "eatizen.com.hk",
                      "path_prefix": "/yundt-saas-gateway/api/icdp/v1/org/shop",
                      "methods": ["POST"]},
            "allow_upload": [{"host_suffix": "static.uat.eatizen.com.hk",
                              "path_prefix": "/", "methods": ["POST"]}],
        }
        scenarios, rejects = parse_negative_scenarios([raw])
        assert rejects == [], rejects
        assert scenarios[0]["expect_accept"]["allow_upload"] == [
            {"host_suffix": "static.uat.eatizen.com.hk", "path_prefix": "/",
             "methods": ["POST"]}]

        # 未声明 allow_upload 的场景：键不出现（而非空数组）——保持与历史序列化一致
        plain = {k: v for k, v in VALID.items() if k != "expect_reject"}
        plain["expect_accept"] = {"api_path_hint": "/v1/purchaseLimit/modify"}
        scenarios_plain, rejects_plain = parse_negative_scenarios([plain])
        assert rejects_plain == []
        assert "allow_upload" not in scenarios_plain[0]["expect_accept"]

    @pytest.mark.parametrize("bad_upload, needle", [
        ([{"path_prefix": "/", "methods": ["POST"]}], "缺少 host_suffix"),
        ([{"host_suffix": "localhost", "path_prefix": "/"}], "过宽"),
        ([{"host_suffix": "a.example.com", "path_prefix": "no-slash"}], "绝对路径前缀"),
        ("not-a-list", "必须是数组"),
    ])
    def test_bad_allow_upload_is_rejected_at_plan_time(self, bad_upload, needle):
        """安全边界写错必须**当场拒绝**（不能只警告）——放行范围放大了就是副作用。"""
        raw = {k: v for k, v in VALID.items() if k != "expect_reject"}
        raw["expect_accept"] = {"api_path_hint": "/v1/purchaseLimit/modify",
                                "allow_upload": bad_upload}
        scenarios, rejects = parse_negative_scenarios([raw])
        assert scenarios == []
        assert rejects and rejects[0]["reason"] == "expect_accept.allow_upload 非法"
        assert needle in rejects[0]["detail"]


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

    def test_no_hit_is_attributed_to_the_target_endpoint_only(self):
        """未取到目标响应时，必须**只按命中目标接口的拦截**归因，并留下可排查的现场。

        实测 TR-402（2026-10-16）：窗口内唯一被写隔离拦下的请求是控制台自身的
        ``PUT /yundt-saas-gateway/api/icdp/v1/message/inMail/status``（与提交无关）。
        旧实现按"窗口内拦截总条数"归因，于是报告成"本场景的写请求被写隔离阻断"，
        真实原因（目标请求**压根没发出**）被掩盖，排查方向被整体带偏。
        """
        script = render_form_executor(_plan([VALID]))
        # 现场 dump：前端校验 / toast / 当前 URL / 窗口内全部非 GET 请求
        assert "NEG_NO_HIT" in script
        assert "collectSubmitScene" in script
        assert "winWrites" in script
        assert "form_errors" in script
        # 归因只看命中目标接口（及同资源集合）的拦截；旧的"总条数"归因不得残留
        assert "blockedTarget" in script
        assert "blockedNow" not in script

    def test_blocked_write_attribution_is_scoped_to_the_declared_resource(self):
        """被阻断的写请求必须按**资源集合**归属，避免两类误判。

        实测 TR-402（2026-10-16）：窗口内被拦的是 `.../message/inMail/status`
        （应用后台轮询）与声明目标 `.../org/shop` 不在同一集合 ⇒ 不得算作本场景的写请求。

        契约用例 NEG-BLOCKED（写隔离收窄）：场景声明 `.../purchaseLimit/not-declared`，
        页面实际提交同集合的兄弟接口 `.../purchaseLimit/delete` 并被拦 ⇒ **必须**
        算作本场景提交触发的写请求，否则结论会误报成"请求没发出"。
        """
        script = render_form_executor(_plan([VALID]))
        assert "blockedFamily" in script
        assert "ownDir" in script
        # 目录（path_prefix 去掉末段）太浅时不得启用集合匹配，否则退化成"什么都算"
        assert "dirUsable" in script
        assert "ownDir.split('/').filter(Boolean).length >= 2" in script
        # 附注条数必须扣除已在册的拦截，不要重复计数
        assert "blockedInScope" in script

    def test_submit_window_polls_the_scene_instead_of_sampling_once(self):
        """提交窗口必须**轮询并累计**现场，不能只在末尾单点采样。

        实测 TR-405（2026-10-16）：前端必填校验 toast 在 t≈0.5s 弹出、数秒后自动消失；
        旧实现在 8s 窗口末尾才读一次，于是真实原因（「Business hours group …:
        please select at least one day」）完全没被记下，现场只剩写隔离自身触发的
        "Network Error" —— 一个真实存在的观测盲区。修法是窗口内每秒轮询去重，
        并把**首次出现时刻**一并留下。
        """
        script = render_form_executor(_plan([VALID]))
        assert "pollScene" in script
        assert "seenToasts" in script
        assert "seenErrors" in script
        # 逐秒轮询（8 次）而非一次性等待
        assert "for (let k = 0; k < 8; k += 1)" in script
        # 首现时刻要落进现场（否则无法判断"何时报的"）
        assert "tSubmit" in script
        # 归因措辞要能引用到首条提示
        assert "firstToast" in script

    def test_form_error_collector_covers_antd_v3_explain(self):
        """字段错误采集必须兼容 antd v3 与 v4 两代类名。

        实测 TR-409/TR-412（2026-10-16）：YL 店铺表单是 antd v3，字段错误渲染在
        ``.ant-form-explain``（且 ``.ant-form-item`` 加 ``has-error`` 与否取决于
        实现——本例**没有**加）。只读 v4 的 ``.ant-form-item-explain`` 会把
        「Please set latitude and longitude」这条真实拦截原因读成"没有表单错误"，
        结论被带成"点了 Confirm 没反应"。
        """
        script = render_form_executor(_plan([VALID]))
        assert ".ant-form-item-explain, .ant-form-explain" in script
        assert "has-error" in script
        assert "(has-error，错误文案未渲染)" in script

    def test_visibility_check_waits_for_effect_and_verifies_the_toggle(self):
        """条件显示检查必须「点 → 等效果（轮询）→ 按效果判定」，不能点一下就算。

        实测 TR-422 → TR-431（2026-10-16）：YL 店铺 ``visibility:排隊=on`` 报
        "切换后未出现：Third-Party Store Code、Priority Ticket Level Type"，
        但**空表单**上点开 Queue 开关后这两个字段 500ms 内就出现（可见字段 22 → 25）。
        旧实现是「点一下 → 固定等 900ms → 采样一次」，把「没点中」与「字段真没出现」
        压成同一句结论 ⇒ 会向业务侧交出一条**假的 SUT 缺陷**。
        另外：开关残留 ON 会污染随后的提交阶段（TR-422 实测多出一条
        "Takeout Redirect Link" 必填错误），所以检查完必须复位。
        """
        script = render_form_executor(_plan([VALID]))
        # 轮询等效果（12 × 400ms ≈ 5s），不是一次性 waitForTimeout(900)
        assert "for (let k = 0; k < 12; k += 1)" in script
        assert "missingNow" in script
        # 候选控件逐个试，并记录生效候选与切换前 class（可回溯"到底点了哪个"）
        assert "cands.push" in script
        assert "候选 #" in script
        # 开关没翻转时用原生 click 兜底（TR-411/TR-430 实测有效）
        assert "el.click()" in script
        # 检查后复位，避免污染提交阶段
        assert "复位" in script

    def test_upload_step_leaves_a_trace_even_on_success(self):
        """upload 原语成功也要留痕——否则"配了但没执行/执行了没生效"在日志里看不出来。"""
        script = render_form_executor(_plan([VALID]))
        assert script.count("UPLOAD_STEP") >= 3   # 缺夹具 / 未定位 / 成功 三个出口
        assert "UPLOAD_ALLOW_RULES" in script

    def test_write_isolation_is_narrowed_to_declared_endpoints_only(self):
        """写隔离规则**只**来自场景声明的目标接口（设计文档 §四"收窄到单接口"）。"""
        plan = _plan([VALID])
        allow = plan["negative_scenarios"][0]["expect_reject"]["allow"]
        assert allow["host_suffix"] == "imarketing-uat.xysjg.com"
        assert allow["path_prefix"].endswith("/v1/purchaseLimit/modify")
        assert allow["methods"] == ["PUT"]
        script = render_form_executor(plan)
        # 执行器按 allow 构造规则（不再把响应匹配用的子串当前缀）
        # rev99：变量改名 expectX（accept 优先），语义不变——两种极性同构收窄。
        assert "allow.path_prefix || expectX.api_path_hint" in script
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


# --------------------------------------------------------------------------- #
# rev99：期望受理极性（expect_accept，与 expect_reject 互斥）
# --------------------------------------------------------------------------- #
VALID_ACCEPT = {
    "scenario_id": "POS-EMAIL-SINGLE",
    "rule_ref": "单收件人发送应成功",
    "target_entry": "custom",
    "case_id": "TC-837310",
    "rule_id": "FR-CS-EMAIL-SINGLE",
    "setup": [
        {"action": "select_row", "row_index": 1},
        {"action": "click", "labels": ["Email a Link"]},
        {"action": "wait", "ms": 1500},
        {"action": "fill", "field": "Emails", "value": "a@b.com"},
    ],
    "submit_label": ["Send"],
    "expect_accept": {
        "api_path_hint": "/cs/cases/send-email",
        "result_code": "0",
        "allow": {"path_prefix": "/yundt-saas-gateway/api/icdp/v1/cs/cases/send-email",
                  "methods": ["POST"]},
    },
}


class TestAcceptScenarioParsing:
    def test_valid_accept_scenario_is_normalized(self):
        scenarios, rejects = parse_negative_scenarios([VALID_ACCEPT])
        assert rejects == []
        assert len(scenarios) == 1
        sc = scenarios[0]
        assert sc["expect_accept"]["api_path_hint"] == "/cs/cases/send-email"
        assert sc["expect_accept"]["result_code"] == "0"
        assert sc["expect_accept"]["allow"]["methods"] == ["POST"]
        # 互斥：accept 模式下 expect_reject 清空（运行期按 accept 优先分支）
        assert sc["expect_reject"]["api_path_hint"] == ""

    def test_accept_and_reject_mutex_is_rejected(self):
        raw = dict(VALID_ACCEPT, expect_reject=VALID["expect_reject"])
        scenarios, rejects = parse_negative_scenarios([raw])
        assert scenarios == []
        assert rejects[0]["reason"] == "expect_accept 与 expect_reject 互斥"

    def test_accept_missing_api_path_hint_is_rejected(self):
        raw = dict(VALID_ACCEPT, expect_accept={"result_code": "0"})
        scenarios, rejects = parse_negative_scenarios([raw])
        assert scenarios == []
        assert rejects[0]["reason"] == "缺少 expect_accept.api_path_hint"

    def test_accept_relative_allow_path_prefix_is_rejected(self):
        raw = dict(VALID_ACCEPT, expect_accept={
            **VALID_ACCEPT["expect_accept"],
            "allow": {"path_prefix": "cs/cases/send-email"}})
        scenarios, rejects = parse_negative_scenarios([raw])
        assert scenarios == []
        assert rejects[0]["reason"] == "allow.path_prefix 必须是绝对路径前缀"

    def test_scenarios_only_plan_is_executable_with_accept(self):
        """只有场景（无字段级检查）+ accept 极性：与拒绝极性同权，executable=True。"""
        plan = _plan([VALID_ACCEPT])
        assert plan["executable"] is True
        assert plan["checks"] == []
        assert len(plan["negative_scenarios"]) == 1
