# -*- coding: utf-8 -*-
"""去业务化回归测试（rev63）：主用例选择 + 脚本模板匹配。

覆盖两个真实缺陷：
1. `_select_test_case` 曾用业务关键词 `["储值免单", "新建活动", "营销活动", "活动管理", "B端"]`
   打分挑主用例 → PR-9 的 CS Module 批次会被选中储值类用例；
2. `_web_test_label` / `_generate_playwright_script` 曾把「储值免单」当**通用兜底**
   → 未识别业务产出"归属正确、内容错误"的脚本（实测 WT-3751DDA0 / TR-83）。

要求（复核意见）：
- 只用「请求显式指定 → 子功能名称反查 → 既有 WebTest 显式关系 → 确定性排序」，不得再用业务词；
- 无匹配模板时**结构化报错**，不得静默套用其它业务模板；
- 选择依据/模板键必须可审计（写入 generation_params）。
"""

from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.services.web_case_compiler import (
    RECHARGE_FORM_LOCATORS,
    build_form_plan,
    build_form_plan_for_template,
    compile_cases,
    resolve_form_locators,
)
from app.services.web_case_link_service import (
    WebCaseLinkService,
    build_case_asset_bindings,
    normalize_route_text,
    select_primary_case,
)


def _case(identifier: str, name: str, module: str = "", created: datetime | None = None,
          assertions: list[dict] | None = None):
    custom: dict = {}
    if assertions:
        custom["assertions"] = assertions
    return SimpleNamespace(
        id=uuid4(),
        identifier=identifier,
        name=name,
        module=module,
        description="",
        created_at=created or datetime(2026, 9, 1, tzinfo=timezone.utc),
        custom_fields=custom,
    )


#: 旧实现会因为业务关键词命中而选中的用例（储值免单 + 新建活动 + B端 = 3 分）
WRITEOFF_CASE = _case("TC-XY-1", "B端储值免单活动新建入口冒烟", "营销云-营销活动-储值免单活动")
#: 新实现应当选中的用例（批次里真正的业务：CS Module）
CS_CASE = _case("TC-CS-1", "CS Module 工单列表入口冒烟", "CS Module")
RECHARGE_CASE = _case("TC-RC-1", "B端企业储值充值-储值账户列表校验", "小杨生煎-B端储值充值")

#: 储值免单模板的用例标题（用于判定"是否套了写错的业务模板"）
_WRITEOFF_TITLE = "B端储值免单活动新建入口冒烟"


class TestPrimaryCaseSelection:
    """主用例选择必须与业务无关。"""

    def test_explicit_request_wins(self):
        case, audit = select_primary_case(
            [WRITEOFF_CASE, CS_CASE], primary_test_case_id=WRITEOFF_CASE.id
        )
        assert case is WRITEOFF_CASE
        assert audit["strategy"] == "explicit_request"

    def test_explicit_request_outside_batch_is_rejected(self):
        with pytest.raises(ValueError, match=r"\[primary_case_not_in_batch\]"):
            select_primary_case([CS_CASE], primary_test_case_id=uuid4())

    def test_sub_function_reverse_lookup_beats_business_keywords(self):
        """子功能名称反查优先：CS 批次不再被"储值免单/B端"业务词抢走。"""
        case, audit = select_primary_case(
            [WRITEOFF_CASE, CS_CASE],
            sub_function_names=("CS工单管理", "CS Module", "Customer Service"),
        )
        assert case is CS_CASE
        assert audit["strategy"] == "sub_function_match"
        assert audit["best_score"] > 0

    def test_existing_relation_used_when_no_primary_or_sub_function(self):
        case, audit = select_primary_case(
            [CS_CASE, RECHARGE_CASE], related_case_ids={RECHARGE_CASE.id}
        )
        assert case is RECHARGE_CASE
        assert audit["strategy"] == "existing_relation"

    def test_generic_order_is_deterministic_and_business_blind(self):
        """既无显式指定、又无子功能/既有关系时：按 created_at → identifier 排序，不猜业务。"""
        older = _case("TC-Z", "随便一条用例", created=datetime(2026, 8, 1, tzinfo=timezone.utc))
        newer_writeoff = _case(
            "TC-A", "B端储值免单活动新建入口冒烟", created=datetime(2026, 9, 1, tzinfo=timezone.utc)
        )
        case, audit = select_primary_case([newer_writeoff, older])
        assert case is older
        assert audit["strategy"] == "generic_order"

    def test_tie_break_by_identifier(self):
        created = datetime(2026, 9, 1, tzinfo=timezone.utc)
        b = _case("TC-B", "用例B", created=created)
        a = _case("TC-A", "用例A", created=created)
        case, _ = select_primary_case([b, a])
        assert case is a

    def test_empty_batch(self):
        case, audit = select_primary_case([])
        assert case is None
        assert audit["strategy"] == "none"

    def test_audit_carries_candidates_for_review(self):
        _, audit = select_primary_case(
            [WRITEOFF_CASE, CS_CASE],
            sub_function_names=("CS工单管理", "CS Module"),
        )
        assert audit["selected"] == CS_CASE.identifier
        assert [c["identifier"] for c in audit["candidates"]][0] == CS_CASE.identifier
        assert all("score" in c for c in audit["candidates"])


class TestFormLocatorResolution:
    """定位器来源显式化（2026-09-29 复核 P1，通用化）。

    历史实现是"全局默认表"（储值那张）：任何未配置定位器的新业务模板都会套用它，
    去操作目标页面根本没有的控件 → 定位超时、整批 unverified。
    实测两次同一根因：CS Module（2026-09-24）、会员限购商品管理（2026-09-29）。
    现在的规则与业务无关：子功能配置优先；否则用**本模板自带**的表；都没有就不生成表单检查。
    """

    def test_no_cross_business_fallback(self):
        from app.services.web_case_compiler import resolve_form_locators

        for key in ("cs_module", "purchase_limit", "storage_writeoff", "", None):
            table, source = resolve_form_locators(key)
            assert table is None, key
            assert source == "none", key

    def test_template_owned_table_is_used(self):
        from app.services.web_case_compiler import resolve_form_locators

        table, source = resolve_form_locators("enterprise_storage_recharge")
        assert table and table.get("fields")
        assert source == "template_default"

    def test_sub_function_override_wins(self):
        from app.services.web_case_compiler import resolve_form_locators

        override = {"fields": {"任意字段": {"kind": "input"}}}
        table, source = resolve_form_locators("cs_module", override)
        assert table is override
        assert source == "sub_function_override"
        # 覆盖表为空（没有 fields）时**不**当作有效配置，避免"配了个空壳"变成静默通过
        assert resolve_form_locators("cs_module", {"fields": {}})[1] == "none"

    def test_cs_case_without_locator_source_gets_no_form_plan(self):
        svc = WebCaseLinkService.__new__(WebCaseLinkService)
        # 带断言的 CS 用例：断言能被编译，但**没有定位器来源** → 不生成表单检查
        case = _case("TC-CS-9", "CS Module 工单表单校验", "CS Module",
                     assertions=[{"kind": "required", "field": "Case Title",
                                  "expect": "reject", "rule": "必填"}])
        case.custom_fields["requirement_rule"] = {"field": "Case Title"}
        manifest = compile_cases([case])
        web_template_key = svc._resolve_web_template_key(case)
        assert web_template_key == "cs_module"

        plan, source = build_form_plan_for_template(web_template_key, manifest, None)
        assert plan is None
        assert source == "none"

        # 显式给出定位器表（模拟子功能 custom_config.field_locators）→ 生成计划
        plan2, source2 = build_form_plan_for_template(
            web_template_key, manifest, {"fields": {"Case Title": {"kind": "input"}}},
        )
        assert source2 == "sub_function_override"
        assert isinstance(plan2, dict) and plan2.get("checks")

        # 无定位器表时 build_form_plan 不得跨业务回退到内置储值表
        assert build_form_plan(manifest, None) is None
        assert build_form_plan(manifest, RECHARGE_FORM_LOCATORS) is not None


class TestCsFormExecutionWiring:
    """CS 模板接入表单执行器（2026-09-24）：flow 驱动 + 未配定位器时不硬套储值表。

    实测背景：CS 批次若套默认（储值）定位器表，会生成 5 条 `变动类型/变更方式` 检查，
    这两个字段在 CS 工单表单里不存在 → 执行时 `blocked` → 执行器
    `expect(summary.blocked).toBe(0)` 直接**硬失败**。
    """

    def _service(self) -> WebCaseLinkService:
        return WebCaseLinkService.__new__(WebCaseLinkService)

    def test_cs_template_without_locators_has_no_form_executor(self):
        from app.services.web_case_compiler import build_form_plan, compile_cases

        svc = self._service()
        manifest = compile_cases([CS_CASE])
        # 直接模拟 create_minimal_loop 的护栏：CS + 无专用定位器 → form_plan=None
        script = svc._generate_playwright_script(
            CS_CASE, "https://example.invalid/login", ["登录", "CRM"],
            manifest=manifest, form_plan=None,
        )
        assert "FORM_PLAN" not in script
        assert "runFormChecks" not in script
        # 默认表仍能生成储值检查 → 正是护栏要挡掉的东西
        assert build_form_plan(manifest, RECHARGE_FORM_LOCATORS) is not None

    def test_cs_template_with_locators_embeds_form_executor(self):
        from app.services.web_case_compiler import build_form_plan, compile_cases

        svc = self._service()
        cs_locators = {
            "name": "cs_module_case_create",
            "flow": "create_page",
            "form_button": ["Create"],
            "modal": "form",
            "fields": {"Case Title": {"kind": "input"}, "Member": {"kind": "input"}},
        }
        cs_case = _case("TC-CS-2", "CS Module 工单表单校验", "CS Module",
                        assertions=[{"kind": "required", "field": "Case Title", "expect": "required"}])
        # 表单检查按**用例级 field**（custom_fields.requirement_rule.field）生成，夹具必须给全
        cs_case.custom_fields["requirement_rule"] = {"field": "Case Title", "required": True}
        plan = build_form_plan(compile_cases([cs_case]), cs_locators)
        script = svc._generate_playwright_script(
            cs_case, "https://example.invalid/login", ["登录", "CRM"],
            manifest=compile_cases([cs_case]), form_plan=plan,
        )
        assert "FORM_PLAN" in script
        assert "runFormChecks" in script
        # 定位器表整体替换：**载荷**里不得残留储值字段（注释文本不算）
        assert '"Case Title"' in script
        assert '"变动类型"' not in script
        assert "cs_module_case_create" in script

    def test_executor_open_is_flow_driven(self):
        """开表单逻辑必须由 flow 驱动：储值走手机号筛选，CS 直接点 Create。"""
        from app.services.web_case_compiler import build_form_plan, compile_cases

        svc = self._service()
        recharge_plan = build_form_plan(compile_cases([RECHARGE_CASE]), RECHARGE_FORM_LOCATORS)
        script = svc._generate_playwright_script(
            RECHARGE_CASE, "https://example.invalid/login", ["登录", "储值账户列表"],
            manifest=compile_cases([RECHARGE_CASE]), form_plan=recharge_plan,
        )
        assert "function openForm(" in script
        assert "flow === 'recharge_modal'" in script
        assert "openRechargeForm" not in script
        assert "无法打开表单（flow=" in script


class TestAntdSelectChecks:
    """antd Select（下拉）断言支持（2026-09-24）。

    背景：CS 批次里 Brand/Service Mode 的 `enum` 断言原先落进"本版不支持类型"被跳过，
    `default` 也只在配置了默认观测项时才算证据。现支持：
    - `enum` → `select_options`（展开下拉读选项，合法值须存在、非法值须不存在）；
    - `default` → `default_selected`（读 `.ant-select-selection-item` 当前值）。
    真实浏览器验证见 artifacts `select_exec_probe_r2_20260924.log`（pass 5 / blocked 0）。
    """

    CS_LOCATORS = {
        "name": "cs_module_case_create",
        "flow": "create_page",
        "form_button": ["Create"],
        "modal": "form",
        "fields": {"Brand": {"kind": "select"}, "Service Mode": {"kind": "select"}},
        "defaults": [{"field": "Brand", "option": "YL"}],
    }

    def _select_case(self, identifier: str, field: str, assertions: list[dict]):
        case = _case(identifier, f"{field} 校验", "CS Module", assertions=assertions)
        case.custom_fields["requirement_rule"] = {"field": field}
        return case

    def test_enum_on_select_becomes_select_options(self):
        from app.services.web_case_compiler import build_form_plan, compile_cases

        cases = [
            self._select_case("TC-B1", "Brand",
                              [{"kind": "enum", "data": "ETZ", "expect": "accept", "rule": "支持選擇ETZ"}]),
            self._select_case("TC-B2", "Brand",
                              [{"kind": "enum", "data": "不存在的枚举值", "expect": "reject", "rule": "支持選擇ETZ"}]),
        ]
        plan = build_form_plan(compile_cases(cases), self.CS_LOCATORS)
        assert plan is not None
        types = [c["type"] for c in plan["checks"]]
        assert types.count("select_options") == 2
        # 原先这类断言会被判"本版不支持类型" → 现在不再落入 skipped
        assert plan["skipped_count"] == 0
        assert plan["assertion_check_count"] == 2

    def test_select_checks_carry_case_attribution(self):
        from app.services.web_case_compiler import build_form_plan, compile_cases

        cases = [self._select_case("TC-S1", "Service Mode",
                                   [{"kind": "enum", "data": "Dine-in", "expect": "accept",
                                     "rule": "枚舉内容Dine-in"}]),
                 self._select_case("TC-S2", "Service Mode",
                                   [{"kind": "default", "data": "", "expect": "default=YL",
                                     "rule": "默認YL選項"}])]
        plan = build_form_plan(compile_cases(cases), self.CS_LOCATORS)
        checks = {c["type"]: c for c in plan["checks"]}
        sel = [c for c in plan["checks"] if c["type"] == "select_options"][0]
        assert sel["case_id"] == "TC-S1"
        assert sel["data"] == "Dine-in"

    def test_executor_reads_select_value_and_scopes_visible_dropdown(self):
        """执行器要点：读选中值 + 只取**可见**下拉（否则会读到隔壁字段的选项）。"""
        from app.services.web_case_compiler import (
            build_form_plan,
            compile_cases,
            render_form_executor,
    RECHARGE_FORM_LOCATORS,
)

        plan = build_form_plan(
            compile_cases([self._select_case("TC-B1", "Brand",
                                             [{"kind": "enum", "data": "ETZ", "expect": "accept"}])]),
            self.CS_LOCATORS,
        )
        script = render_form_executor(plan)
        assert "function selectRoot(" in script
        assert "async function selectDisplayValue(" in script
        assert "async function selectOptionLabels(" in script
        assert ".ant-select-selection-item" in script
        assert "'.ant-select-dropdown:visible'" in script
        assert "check.type === 'select_options'" in script
        # 默认值分支必须能走 select 路径
        assert "if (def0.kind === 'select')" in script

    def test_radio_path_unchanged(self):
        """储值单选的检查类型不得被 Select 改动影响。"""
        from app.services.web_case_compiler import build_form_plan, compile_cases

        recharge_case = _case("TC-R1", "变动类型-枚举", "小杨生煎-B端储值充值",
                              assertions=[{"kind": "enum", "data": "企业储值充值",
                                           "expect": "accept", "rule": "选项范围",
                                           "field": "变动类型"}])
        recharge_case.custom_fields["requirement_rule"] = {"field": "变动类型"}
        plan = build_form_plan(compile_cases([recharge_case]), RECHARGE_FORM_LOCATORS)
        types = [c["type"] for c in plan["checks"]]
        assert "radio_options" in types
        assert "select_options" not in types


class TestEnvironmentBlockGuardInTemplates:
    """登录页 4xx/5xx 必须在脚本里显式判为环境阻断（而不是等定位器超时）。"""

    def _service(self) -> WebCaseLinkService:
        return WebCaseLinkService.__new__(WebCaseLinkService)

    def test_cs_template_reports_http_status_as_environment_block(self):
        svc = self._service()
        script = svc._generate_playwright_script(
            CS_CASE, "https://example.invalid/login", ["登录", "CRM"]
        )
        assert "ENVIRONMENT_BLOCKED" in script
        assert "loginResponse.status()" in script
        assert "loginStatus === 403" in script

    def test_recharge_template_reports_http_status_as_environment_block(self):
        svc = self._service()
        script = svc._generate_playwright_script(
            RECHARGE_CASE, "https://example.invalid/login", ["登录", "储值账户列表"]
        )
        assert "ENVIRONMENT_BLOCKED" in script
        assert "loginResponse.status()" in script


class TestTimelineCapability:
    """timeline（详情页富文本）能力 + 长度断言的**探针值合成**。

    真实浏览器验证：artifacts `timeline_exec_probe_r3_20260924.log`
    （pass 5 / unverified 0 / blocked 0：500 字符通过、501 字符被截断、100 字符通过、
    101 字符出现校验提示、必填填后清空出现提示）。
    """

    def test_probe_value_uses_data_as_sample_length(self):
        """data 是**样本描述**（'501字'= 501 字符样本），不是上限；上限来自 rule。"""
        from app.services.web_case_compiler import probe_value_for

        accept = probe_value_for({"kind": "max_length", "data": "500字",
                                  "expect": "accept", "rule": "限制500個字"})
        reject = probe_value_for({"kind": "max_length", "data": "501字",
                                  "expect": "reject", "rule": "限制500個字"})
        assert len(accept[0]) == 500 and "上限 500" in accept[1]
        assert len(reject[0]) == 501 and "上限 500" in reject[1]

    def test_probe_value_legacy_count_wording(self):
        """PR-2 的 '11个手机号' 同样按样本长度处理（不回归）。"""
        from app.services.web_case_compiler import probe_value_for

        value, note = probe_value_for({"kind": "max_count", "data": "11个手机号",
                                       "expect": "reject", "rule": "最多输入10个手机号"})
        assert len(value) == 11
        assert "上限 10" in note

    def test_non_length_kinds_get_no_probe(self):
        """boundary 的 data 是**字面值**（'0'），必须保持原样，不得被换成探针。"""
        from app.services.web_case_compiler import probe_value_for

        assert probe_value_for({"kind": "boundary", "data": "0", "expect": "reject"}) == ("", "")
        assert probe_value_for({"kind": "enum", "data": "ETZ", "expect": "accept"}) == ("", "")

    def test_plan_carries_probe_value_and_attribution(self):
        from app.services.web_case_compiler import build_form_plan, compile_cases

        case = _case("TC-SUB-R", "Subject 长度", "CS Module",
                     assertions=[{"kind": "max_length", "data": "501字", "expect": "reject",
                                  "rule": "限制500個字"}])
        case.custom_fields["requirement_rule"] = {"field": "Subject"}
        plan = build_form_plan(compile_cases([case]), {
            "name": "cs_timeline", "flow": "detail_timeline", "entry_labels": ["View"],
            "form_button": ["Add"], "modal": "#editor",
            "fields": {"Subject": {"kind": "richtext"}},
        })
        check = plan["checks"][0]
        assert check["type"] == "input_validation"
        assert len(check["probe_value"]) == 501
        assert check["case_id"] == "TC-SUB-R"

    def test_executor_supports_richtext_and_reads_value_by_tag(self):
        """要点：富文本定位 + 按标签读值（contenteditable 的 inputValue() 不可靠）。"""
        from app.services.web_case_compiler import (
            build_form_plan,
            compile_cases,
            render_form_executor,
        )

        case = _case("TC-SUB-A", "Subject 长度", "CS Module",
                     assertions=[{"kind": "max_length", "data": "500字", "expect": "accept",
                                  "rule": "限制500個字"}])
        case.custom_fields["requirement_rule"] = {"field": "Subject"}
        plan = build_form_plan(compile_cases([case]), {
            "name": "cs_timeline", "flow": "detail_timeline", "entry_labels": ["View"],
            "form_button": ["Add"], "modal": "#editor",
            "fields": {"Subject": {"kind": "richtext"}},
        })
        script = render_form_executor(plan)
        assert "function fieldControl(" in script
        assert "async function controlValue(" in script
        assert "'[contenteditable=\"true\"], .ql-editor" in script
        assert "controlValue(input)" in script
        # 富文本不能再走 inputValue()（实测返回长度错乱）
        assert "input.inputValue()" not in script

    def test_open_form_supports_entry_labels(self):
        from app.services.web_case_compiler import (
            build_form_plan,
            compile_cases,
            render_form_executor,
        )

        case = _case("TC-1", "Subject 长度", "CS Module",
                     assertions=[{"kind": "max_length", "data": "500字", "expect": "accept",
                                  "rule": "限制500個字"}])
        case.custom_fields["requirement_rule"] = {"field": "Subject"}
        plan = build_form_plan(compile_cases([case]), {
            "name": "cs_timeline", "flow": "detail_timeline", "entry_labels": ["View"],
            "form_button": ["Add"], "modal": "#editor",
            "fields": {"Subject": {"kind": "richtext"}},
        })
        assert plan is not None
        script = render_form_executor(plan)
        assert "FORM_PLAN.locators.entry_labels" in script


class TestWebTemplateSelection:
    """脚本模板必须正向匹配业务；无匹配即结构化报错。"""

    def _service(self) -> WebCaseLinkService:
        return WebCaseLinkService.__new__(WebCaseLinkService)

    def test_known_businesses_resolve_to_their_own_template(self):
        svc = self._service()
        assert svc._resolve_web_template_key(CS_CASE) == "cs_module"
        assert svc._resolve_web_template_key(RECHARGE_CASE) == "enterprise_storage_recharge"
        assert svc._resolve_web_template_key(WRITEOFF_CASE) == "storage_writeoff"

    def test_labels_match_template(self):
        svc = self._service()
        assert svc._web_test_label(CS_CASE) == "CS工单管理"
        assert svc._web_test_label(RECHARGE_CASE) == "B端企业储值充值"
        assert svc._web_test_label(WRITEOFF_CASE) == "B端储值免单活动"

    def test_unknown_business_is_rejected_not_silently_couponed(self):
        """回归：未识别业务此前会静默产出「储值免单」脚本（WT-3751DDA0 同级缺陷）。"""
        svc = self._service()
        unrelated = _case("TC-P15-1", "商品中心-商品列表分页校验", "PR-15 商品中心")
        with pytest.raises(ValueError, match=r"\[unsupported_web_template\]"):
            svc._resolve_web_template_key(unrelated)
        with pytest.raises(ValueError, match=r"\[unsupported_web_template\]"):
            svc._web_test_label(unrelated)
        with pytest.raises(ValueError, match=r"\[unsupported_web_template\]"):
            svc._generate_playwright_script(unrelated, "https://example.invalid/login", ["登录"])

    def test_unknown_template_key_is_rejected(self):
        svc = self._service()
        with pytest.raises(ValueError, match=r"\[unsupported_web_template\]"):
            svc._web_test_label(CS_CASE, "no_such_template")

    def test_cs_template_expands_menu_idempotently(self):
        """rev64：antd SubMenu 是开关语义，盲点父菜单会把已展开的菜单**折叠**。

        实测 TR-84：失败快照显示 Customer Service 未展开、全页无 Cases 文本，
        根因就是"再点一次父菜单"的重试。
        """
        svc = self._service()
        script = svc._generate_playwright_script(
            CS_CASE, "https://example.invalid/login", ["登录", "CRM", "Customer Service", "Cases"]
        )
        assert "type Locator" in script
        assert "async function expandSubmenu" in script
        assert "async function clickMenuItem" in script
        # 展开前先读取展开状态；已展开就不点击
        assert "aria-expanded" in script
        # 旧实现的“再点一次父菜单”必须消失（重复点击=折叠）
        assert "clickByText(scope, ['Customer Service'], 5000)" not in script
        # 失败时必须落盘可见菜单与子菜单文本，便于定位真实标签
        assert "CS_MENU_DUMP" in script
        assert "CS_SUBMENU_DUMP" in script
        # 子菜单标签给候选集（UAT 改词/多语言时不至于必然失败）
        for label in ("Case List", "Case Management"):
            assert label in script

    def test_writeoff_template_still_generated_for_writeoff_business(self):
        svc = self._service()
        script = svc._generate_playwright_script(
            WRITEOFF_CASE, "https://example.invalid/login", ["登录", "营销活动管理"]
        )
        assert "储值免单" in script
        assert "营销活动管理" in script or "活动菜单" in script

    def test_cs_business_gets_cs_template_not_writeoff(self):
        svc = self._service()
        script = svc._generate_playwright_script(
            CS_CASE, "https://example.invalid/login", ["登录", "CRM", "Customer Service"]
        )
        assert "Customer Service" in script
        # 模板特征串：储值免单模板的用例标题（注释里提到邻近业务词不算套错模板）
        assert _WRITEOFF_TITLE not in script

    def test_recharge_business_gets_recharge_template_not_writeoff(self):
        svc = self._service()
        script = svc._generate_playwright_script(
            RECHARGE_CASE, "https://example.invalid/login", ["登录", "储值账户列表"]
        )
        assert "储值账户列表" in script
        assert _WRITEOFF_TITLE not in script


class TestCaseAssetBinding:
    """rev95：用例 → 资产**逐条**绑定回写（复核清单第 3 项）。

    此前只有主用例带 `web_test_id`，其余批次用例的断言明明被编译进同一资产，
    用例侧却查不到归属（PR-9 实测 85 条只有 7 条可追溯）。
    口径：绑定 = 血缘可追溯；**是否被自动覆盖**看 web_coverage.auto_executable。
    """

    WEB_TEST_ID = uuid4()
    SUB_FUNCTION_ID = uuid4()          #: 稳定标识：幂等比较包含它，helper 不能每次新生成

    def _cases(self):
        primary = SimpleNamespace(
            id=uuid4(), identifier="TC-1", custom_fields={"channel": "web"},
        )
        dangling = SimpleNamespace(
            id=uuid4(), identifier="TC-2", custom_fields={"keep": 1, "web_test_id": "old"},
        )
        narrative = SimpleNamespace(id=uuid4(), identifier="TC-3", custom_fields={})
        return primary, dangling, narrative

    def _manifest(self):
        return {
            "cases": [
                {"identifier": "TC-1", "rule_id": "FR-1", "compile_status": "compiled",
                 "assertions": [{"kind": "required"}, {"kind": "enum"}]},
                {"identifier": "TC-2", "rule_id": "FR-2", "compile_status": "dangling_rule",
                 "assertions": []},
            ]
        }

    def _bind(self, cases):
        return build_case_asset_bindings(
            web_test_id=self.WEB_TEST_ID,
            web_test_identifier="WT-PROBE",
            manifest=self._manifest(),
            batch_cases=list(cases),
            primary_case_identifier="TC-1",
            navigation_path=["登录", "CRM"],
            path_template_id="tpl-1",
            path_match_status="matched",
            base_url_source="path_config",
            login_profile="cs",
            sub_function_id=self.SUB_FUNCTION_ID,
            bound_at="2026-09-29T00:00:00",
        )

    def test_every_batch_case_gets_its_own_binding(self):
        primary, dangling, narrative = self._cases()
        bindings = self._bind([primary, dangling, narrative])
        assert [c.identifier for c, _ in bindings] == ["TC-1", "TC-2", "TC-3"]
        written = {c.identifier: f for c, f in bindings}
        assert written["TC-1"]["web_primary_case"] is True
        assert written["TC-2"]["web_primary_case"] is False
        assert written["TC-3"]["web_primary_case"] is False
        assert all(f["web_test_id"] == str(self.WEB_TEST_ID) for f in written.values())
        assert written["TC-2"]["web_validation_path"] == ["登录", "CRM"]

    def test_binding_is_not_mistaken_for_automatic_coverage(self):
        primary, dangling, narrative = self._cases()
        written = {c.identifier: f for c, f in self._bind([primary, dangling, narrative])}
        assert written["TC-1"]["web_coverage"]["auto_executable"] is True
        assert written["TC-1"]["web_coverage"]["assertion_count"] == 2
        # 悬空规则用例：绑定了资产，但**不可自动执行**（口径不得被读成"已覆盖"）
        assert written["TC-2"]["web_coverage"]["auto_executable"] is False
        assert written["TC-2"]["web_coverage"]["compile_status"] == "dangling_rule"
        # 不在编译清单里的用例同样如实标注
        assert written["TC-3"]["web_coverage"]["compile_status"] == "not_in_manifest"
        assert written["TC-3"]["web_coverage"]["assertion_count"] == 0

    def test_existing_custom_fields_are_preserved(self):
        primary, dangling, _narrative = self._cases()
        written = {c.identifier: f for c, f in self._bind([primary, dangling])}
        assert written["TC-2"]["keep"] == 1          # 旧字段不得被覆盖丢失
        assert written["TC-1"]["channel"] == "web"

    def test_second_call_is_idempotent(self):
        primary, dangling, narrative = self._cases()
        first = self._bind([primary, dangling, narrative])
        for case, fields in first:
            case.custom_fields = fields        # 模拟落库后再跑一次
        assert self._bind([primary, dangling, narrative]) == []

    def test_sub_function_change_forces_rewrite(self):
        """回归（实测发现）：首次绑定漏带子功能、later 补上时，必须重写而非沿用旧值。"""
        primary, _d, _n = self._cases()
        for case, fields in self._bind([primary]):
            case.custom_fields = fields
        # 同一资产、同样的编译状态，但这次带上了子功能
        rebound = build_case_asset_bindings(
            web_test_id=self.WEB_TEST_ID,
            web_test_identifier="WT-PROBE",
            manifest=self._manifest(),
            batch_cases=[primary],
            primary_case_identifier="TC-1",
            navigation_path=["登录", "CRM"],
            path_template_id="tpl-1",
            path_match_status="matched",
            base_url_source="path_config",
            login_profile="cs",
            sub_function_id=uuid4(),          # ← 新增来源
            bound_at="2026-09-29T02:00:00",
        )
        assert len(rebound) == 1, "来源字段变化必须触发重写"
        assert rebound[0][1]["web_coverage"]["sub_function_id"]

    def test_rebinding_to_another_asset_is_not_swallowed(self):
        """换资产（或断言数变化）必须重写，不能被幂等判据挡住。"""
        primary, _d, _n = self._cases()
        for case, fields in self._bind([primary]):
            case.custom_fields = fields
        other = build_case_asset_bindings(
            web_test_id=uuid4(),
            web_test_identifier="WT-OTHER",
            manifest=self._manifest(),
            batch_cases=[primary],
            primary_case_identifier="TC-1",
            navigation_path=["登录"],
            path_template_id="tpl-2",
            path_match_status="matched",
            base_url_source="path_config",
            login_profile="cs",
            sub_function_id=None,
            bound_at="2026-09-29T01:00:00",
        )
        assert len(other) == 1
        assert other[0][1]["web_test_identifier"] == "WT-OTHER"


class TestRouteKeywordNormalization:
    """模板路由的分隔符归一化（2026-10-10）。

    根因：`test_case.module` 由上传需求文档的**文件名 stem** 派生
    （`api/v2/ai.py:_derive_requirement_module`），而 `_resolve_web_template_key`
    拿 module 文本去匹配**含空格**的硬编码关键词（如 `"CS Module"`）。
    实测 `CS_Module_方案説明2.0.pdf` 生成的 39 条用例在 `minimal-loop` 阶段
    **32/33 报 `[unsupported_web_template]`** —— 平台自己生成的用例自己编译不了；
    只把**上传文件名**改成含空格的 `CS Module 方案説明2.0.pdf`（PDF 内容不动）
    即 33/33 通过。

    本类锁死"分隔符等价"这一行为，避免再次退化为对空格敏感的字面量匹配。
    """

    @pytest.mark.parametrize(
        "module",
        [
            "CS_Module_方案説明2.0",      # 下划线（实测踩坑形态）
            "CS Module 方案説明2.0",      # 空格（原始可用形态）
            "CS-Module-方案説明2.0",      # 连字符
            "CS Module/方案説明2.0",      # 斜杠
        ],
    )
    def test_cs_module_routes_regardless_of_separators(self, module):
        svc = WebCaseLinkService.__new__(WebCaseLinkService)
        case = _case("TC-CS-N", "Service Mode", module)
        assert svc._resolve_web_template_key(case) == "cs_module"

    def test_normalization_keeps_diagnostics_faithful(self):
        """`.` 不算分隔符：版本号必须原样保留，否则报错信息会被拆成 `2 0`。"""
        assert normalize_route_text("CS_Module_方案説明2.0") == "CS Module 方案説明2.0"

    def test_unrelated_module_still_raises(self):
        """归一化不得变成新的兜底：业务对不上仍必须结构化报错。"""
        svc = WebCaseLinkService.__new__(WebCaseLinkService)
        case = _case("TC-X", "某个无关用例", "unrelated_module")
        with pytest.raises(ValueError, match=r"\[unsupported_web_template\]"):
            svc._resolve_web_template_key(case)

    def test_error_message_points_at_filename_coupling(self):
        """报错必须指向真实原因（文件名耦合），并打印归一化后的识别文本。"""
        svc = WebCaseLinkService.__new__(WebCaseLinkService)
        case = _case("TC-X", "某个无关用例", "unrelated_module")
        with pytest.raises(ValueError) as ei:
            svc._resolve_web_template_key(case)
        message = str(ei.value)
        assert "文件名" in message
        assert "归一化识别文本" in message


class TestCsTemplateRenderGuard:
    """CS 模板渲染护栏（2026-10-10）。

    本次把「固定 5s + 单次 `isVisible`」换成 **60s 轮询 + 失败取证**，
    模板里新增了 `{{ }}` 转义的花括号（该模板是 `str.format()` 文本）。
    本类确保：① `.format()` 能完成（少写一对花括号会直接 KeyError/IndexError）；
    ② 渲染产物里确有轮询与取证标记（防止改动被静默回退）。
    """

    def _render(self) -> str:
        svc = WebCaseLinkService.__new__(WebCaseLinkService)
        return svc._generate_cs_module_script(
            _case("TC-CS-1", "CS Module 工单列表入口冒烟", "CS Module"),
            "https://console.uat.eatizen.com.hk",
            ["登录", "CRM", "Customer Service"],
        )

    def test_template_renders_with_polling(self):
        script = self._render()
        assert "listDeadline" in script
        assert "listProbe" in script
        # 超时 15s：实测就绪仅 115–834ms，15s 已是近 18 倍余量；
        # 过长的轮询只会让真失败白等（需求已由 2026-10-10 实测数据定调）。
        assert "listT0 + 15000" in script
        # 就绪耗时埋点：用于区分「渲染慢」与「真不渲染」
        assert "CS_LIST_READY" in script

    def test_failure_evidence_is_captured(self):
        """失败时必须留取证（正文 dump + 截图），否则排障只能重跑。"""
        script = self._render()
        assert "CS_LIST_DUMP" in script
        assert "screenshot" in script

    def test_no_unescaped_placeholder_left(self):
        script = self._render()
        assert "{form_executor}" not in script
        assert "{coverage_snippet}" not in script

    def test_list_assertion_still_fails_loudly(self):
        """渲染判定仍必须**抛错**而不是静默通过 —— 只是等待更久、并留取证。"""
        script = self._render()
        assert "CS 工单列表未渲染" in script
        assert ".toBeTruthy()" in script
