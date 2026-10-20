# -*- coding: utf-8 -*-
"""用例 → 执行脚本 编译层（v1）回归测试。

覆盖：
1. 清单编译：用例 → 规则编号 → 断言，并按"能否执行"分级（soft / pending_locator）；
2. 清单内嵌：脚本里出现 const COMPILED_CASES 与覆盖度输出；
3. 无清单时行为不变（不注入任何新代码）；
4. 血缘摘要：generation_params 用的紧凑摘要字段正确。
"""

from __future__ import annotations

from types import SimpleNamespace

from app.services.web_case_compiler import (
    RECHARGE_FORM_LOCATORS,
    build_form_plan,
    compile_cases,
    render_coverage_snippet,
    render_form_executor,
    render_manifest_block,
)
from app.services.web_case_link_service import WebCaseLinkService


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


RULE_CASES = [
    _case("TC-1", "企业账户充值金额-边界值与格式校验", rule_id="FR-1", field="企业账户充值金额",
          assertions=[{"kind": "boundary", "field": "企业账户充值金额", "data": "0",
                       "expect": "reject", "rule": "(0,99999]"},
                      {"kind": "precision", "data": "1.222", "expect": "reject_or_truncate",
                       "rule": "小数点后最多保留2位"}]),
    _case("TC-2", "企业账户充值金额-条件显示校验", rule_id="FR-1", field="企业账户充值金额",
          assertions=[{"kind": "visibility", "when": "变动类型=企业储值充值", "expect": "visible"}]),
    _case("TC-3", "会员手机号-边界值与格式校验", rule_id="FR-2", field="会员手机号",
          assertions=[{"kind": "max_count", "data": "11个手机号", "expect": "reject",
                       "rule": "最多输入10个手机号"}]),
]
NARRATIVE_CASE = _case("TC-9", "B2B储值订单不能退款", rule_id="RQ-9")


class TestCompileManifest:
    def test_counts_and_lineage(self):
        manifest = compile_cases(RULE_CASES + [NARRATIVE_CASE])
        assert manifest["case_count"] == 4
        assert manifest["rule_case_count"] == 3
        assert manifest["rule_ids"] == ["FR-1", "FR-1", "FR-2", "RQ-9"]
        assert manifest["field_labels"] == ["企业账户充值金额", "会员手机号"]
        # 3 条需要定位器 + 1 条软校验（visibility）
        assert manifest["pending_locator_count"] == 3
        assert manifest["soft_assertion_count"] == 1
        assert manifest["assertion_count"] == 4

    def test_case_payload_keeps_steps_and_assertions(self):
        manifest = compile_cases(RULE_CASES)
        first = manifest["cases"][0]
        assert first["identifier"] == "TC-1"
        assert first["rule_id"] == "FR-1"
        assert first["kind"] == "rule"
        assert first["steps"] == [{"action": "输入 0", "expected": "被拒绝"}]
        assert first["assertions"][0]["rule"] == "(0,99999]"

    def test_empty_batch(self):
        manifest = compile_cases([])
        assert manifest["case_count"] == 0
        assert manifest["rule_ids"] == []
        assert manifest["field_labels"] == []


class TestRenderBlocks:
    def test_manifest_block_is_embeddable_ts(self):
        block = render_manifest_block(compile_cases(RULE_CASES))
        assert "const COMPILED_CASES" in block
        assert "FR-1" in block and "企业账户充值金额" in block
        assert "as const" in block

    def test_coverage_snippet_reports_pending_locator(self):
        snippet = render_coverage_snippet(form_executable_checks=21, assertion_checks=19)
        assert "COMPILED_COVERAGE" in snippet
        for key in ("pending_locator", "soft_label_checks", "form_executable_checks",
                    "field_labels_found"):
            assert key in snippet
        # 已由表单执行器执行的断言不得再计入 pending_locator
        assert "COMPILED_CASES.pending_locator_count - 19" in snippet


class TestScriptInjection:
    def _service(self) -> WebCaseLinkService:
        return WebCaseLinkService.__new__(WebCaseLinkService)

    def test_script_embeds_manifest_and_coverage(self):
        svc = self._service()
        case = _case("TC-100", "B端储值充值冒烟", rule_id="FR-1", field="企业账户充值金额")
        manifest = compile_cases(RULE_CASES)
        script = svc._generate_playwright_script(
            case, "https://example.invalid/login", ["登录", "忠诚度", "储值账户列表"],
            manifest=manifest,
        )
        assert "const COMPILED_CASES" in script
        assert "COMPILED_COVERAGE" in script
        assert "FR-1" in script
        # 自伤元断言不得回归
        assert "expect(WEB_VALIDATION_PATH).toContain" not in script

    def test_script_without_manifest_is_unchanged(self):
        svc = self._service()
        case = _case("TC-101", "B端储值充值冒烟")
        script = svc._generate_playwright_script(
            case, "https://example.invalid/login", ["登录", "储值账户列表"],
        )
        assert "COMPILED_CASES" not in script
        assert "COMPILED_COVERAGE" not in script
        assert "手机号码" in script  # 原有功能断言仍在

    def test_lost_control_value_is_not_recorded_as_pass(self):
        """rev93 回归：控件"先显示后回滚"（如 antd RangePicker）不得记 pass。

        实测依据 artifacts/b_limit_period_input.json：键入 2026-10-01 时 inputValue 可读到该文本，
        失焦后回滚为空。旧逻辑 `truncated = actual !== probeValue` 会把"控件没接受输入"记成
        "被截断 → pass"（假通过），故值丢失须单独成类并记 unverified。
        """
        svc = self._service()
        case = _case("TC-102", "B端储值充值冒烟", rule_id="FR-1", field="企业账户充值金额")
        script = svc._generate_playwright_script(
            case, "https://example.invalid/login", ["登录", "忠诚度", "储值账户列表"],
            manifest=compile_cases(RULE_CASES),
        )
        # 执行器正文来自 render_form_executor（脚本装配路径不含它）
        executor = render_form_executor(
            build_form_plan(compile_cases(RULE_CASES), RECHARGE_FORM_LOCATORS)
        )
        assert script  # 脚本仍可生成（回归：新守卫不得破坏装配）
        assert "const valueLost = probeValue !== '' && actual === ''" in executor
        assert "const truncated = !valueLost && actual !== probeValue" in executor
        # 负向（expect reject）与正向（expect accept）两条分支都必须把"值丢失"排除在 pass 之外
        assert "(invalid || valueLost) ? 'unverified' : 'pass'" in executor
        assert "控件未保留输入（失焦后为空）" in executor

    def test_direct_url_navigates_the_page_that_owns_scope(self):
        """T-04 回归：`direct_url` 必须导航"拥有 scope 的页面"，而不是固定 `pg`。

        实测依据（TR-172 / 单号 jf-20260122392-01-2963）：交易云是"门户卡片 → 新开标签页"的应用，
        `pg`（门户页）与 `scope`（交易云页）属不同文档 → 旧实现导航 A 页却在 B 页等 `.ant-form`，
        确定性 15s 超时；而失败快照取自 `pg`，显示"页面是对的"，误导排障。
        """
        locators = dict(RECHARGE_FORM_LOCATORS)
        locators["direct_url"] = "https://example.invalid/trade/#/decoration/purchaselimit_add"
        executor = render_form_executor(build_form_plan(compile_cases(RULE_CASES), locators))
        # 统一页面对象：scope 是 Page 则用 scope；否则（FrameLocator，CS CRM 流程）保持用 pg
        assert "direct_url_page" in executor
        assert "((typeof (scope as any).goto === 'function') ? scope : pg)" in executor
        # direct_url 分支只能用 scopePage，不得再出现对 pg 的导航
        assert "const frames = scopePage.frames();" in executor
        assert "await scopePage.goto(target," in executor
        assert "await pg.goto(target," not in executor
        # 跨页留证
        assert "DIRECT_URL_DIAG" in executor
        for key in ("navigated_page", "primary_page", "same_page", "scope_kind"):
            assert key in executor

    def test_container_timeout_error_carries_both_page_urls(self):
        """T-04 回归：容器等待失败必须带"定位所在文档 / 导航所用页面 / 主页面 / 页面清单"。"""
        executor = render_form_executor(
            build_form_plan(compile_cases(RULE_CASES), RECHARGE_FORM_LOCATORS)
        )
        assert "无法打开表单容器：等待" in executor
        assert "定位所在文档=" in executor
        assert "direct_url 导航所用页面=" in executor
        assert "测试主页面 pg=" in executor
        assert "上下文页面=[" in executor
        # 必须保留"定位器可能失效"——web_failure_policy 以它判定为**结构性不可重试**
        assert "定位器可能失效" in executor

    def test_manifest_summary_for_generation_params(self):
        summary = WebCaseLinkService._manifest_summary(compile_cases(RULE_CASES))
        assert summary["case_count"] == 3
        assert summary["rule_case_count"] == 3
        assert summary["pending_locator_count"] == 3
        assert summary["executed_assertion_count"] == 1
        assert summary["case_identifiers"] == ["TC-1", "TC-2", "TC-3"]
        assert summary["rule_ids"] == ["FR-1", "FR-1", "FR-2"]
        # 无清单时不得写入空壳字段
        assert WebCaseLinkService._manifest_summary(None) == {}
        assert WebCaseLinkService._manifest_summary({"case_count": 0}) == {}


class TestFormPlan:
    """定位器层：断言 → 可执行表单校验（只读）。"""

    CASES = [
        _case("TC-1", "企业账户充值金额-边界值", rule_id="FR-ad045b977a", field="企业账户充值金额",
              assertions=[
                  {"kind": "boundary", "data": "0", "expect": "reject", "rule": "(0,99999]"},
                  {"kind": "boundary", "data": "0.01", "expect": "accept", "rule": "(0,99999]"},
                  {"kind": "boundary", "data": "99999.01", "expect": "reject", "rule": "(0,99999]"},
                  {"kind": "precision", "data": "1.222", "expect": "reject_or_truncate",
                   "rule": "小数点后最多保留2位"},
              ]),
        _case("TC-2", "会员手机号-必填", rule_id="FR-46695b3c54", field="会员手机号",
              assertions=[{"kind": "required", "data": "", "expect": "reject", "rule": "是否必填=是"}]),
        _case("TC-3", "上传文件（企业充值）-边界值", rule_id="FR-0d87a61ead", field="上传文件（企业充值）",
              assertions=[{"kind": "file_format", "data": ".exe", "expect": "reject", "rule": "仅支持 xlsx"}]),
    ]

    def test_boundary_assertions_become_checks(self):
        plan = build_form_plan(compile_cases(self.CASES), RECHARGE_FORM_LOCATORS)
        assert plan is not None
        kinds = {}
        for check in plan["checks"]:
            kinds.setdefault(check["type"], []).append(check.get("data") or check.get("option"))
        # kind="boundary" 必须被识别，否则边界断言会被静默跳过
        assert "0" in kinds["input_validation"]
        assert "99999.01" in kinds["input_validation"]
        assert "1.222" in kinds["input_validation"]
        assert kinds["required_error"] == [None]

    def test_visibility_and_default_checks_from_locators(self):
        plan = build_form_plan(compile_cases(self.CASES), RECHARGE_FORM_LOCATORS)
        options = [c["option"] for c in plan["checks"] if c["type"] == "visibility"]
        assert "批量导入" in options                     # 变更方式=批量导入 → 上传文件出现
        assert "企业储值退款（仅扣减储值不退款）" in options  # 变动类型=退款 → 退款金额出现
        defaults = [c["option"] for c in plan["checks"] if c["type"] == "default_selected"]
        assert defaults == ["企业储值充值", "手动输入"]

    def test_upload_assertions_are_skipped_not_faked(self):
        plan = build_form_plan(compile_cases(self.CASES), RECHARGE_FORM_LOCATORS)
        assert plan["skipped_count"] == 1
        assert plan["skipped"][0]["field"] == "上传文件（企业充值）"

    def test_defaults_are_checked_before_any_click(self):
        """默认选中必须在切换单选之前观测，否则点击会先改掉状态造成假 unverified。"""
        plan = build_form_plan(compile_cases(self.CASES), RECHARGE_FORM_LOCATORS)
        types = [c["type"] for c in plan["checks"]]
        assert types.index("default_selected") < types.index("visibility")
        assert types.index("default_selected") < types.index("input_validation")

    def test_assertion_check_count_excludes_locator_generated_checks(self):
        """覆盖度要用"来自断言"的校验数扣减，条件显示/默认值不算在内。"""
        manifest = compile_cases(self.CASES)
        plan = build_form_plan(manifest, RECHARGE_FORM_LOCATORS)
        from_locators = sum(1 for c in plan["checks"]
                            if c["type"] in {"visibility", "visible_now", "default_selected"})
        assert plan["assertion_check_count"] == plan["check_count"] - from_locators
        assert plan["assertion_check_count"] <= manifest["pending_locator_count"]
        snippet = render_coverage_snippet(
            form_executable_checks=plan["check_count"],
            assertion_checks=plan["assertion_check_count"],
        )
        assert "executed_assertions" not in snippet     # 旧口径（软校验冒充已执行）不得回归
        assert "soft_label_checks" in snippet
        assert f"Math.max(0, COMPILED_CASES.pending_locator_count - {plan['assertion_check_count']})" in snippet

    def test_unknown_field_stays_pending(self):
        cases = self.CASES + [_case("TC-9", "字段X-必填", rule_id="FR-x", field="文档里才有的字段",
                                    assertions=[{"kind": "required", "data": "", "expect": "reject"}])]
        plan = build_form_plan(compile_cases(cases), RECHARGE_FORM_LOCATORS)
        assert any(s["field"] == "文档里才有的字段" for s in plan["skipped"])

    def test_locator_override(self):
        custom = {"fields": {"我的字段": {"kind": "input"}}, "form_button": ["打开"],
                  "filter_phone_placeholder": "手机号", "search_button": "查询", "modal": ".x",
                  "toggles": [], "defaults": []}
        cases = [_case("TC-1", "我的字段-必填", rule_id="FR-1", field="我的字段",
                       assertions=[{"kind": "required", "data": "", "expect": "reject"}])]
        plan = build_form_plan(compile_cases(cases), custom)
        assert [c["field"] for c in plan["checks"]] == ["我的字段"]


class TestPlanLevelAttribution:
    """计划级校验（默认值/条件显示/单选枚举）必须**带归属**。

    真实缺陷（TR-62 复核时发现）：这 4 条计划级断言执行了并通过了，却不属任何用例，
    于是 ①落库断言数少于脚本断言行（证据黑洞）②声明了这些规则的用例被**低报**为未执行。
    """

    ENUM_ALLOWED = ["企业储值充值", "企业储值退款（仅扣减储值不退款）"]

    CASES = [
        _case("TC-r1", "变动类型-必填/选项/默认值", rule_id="FR-r1", field="变动类型",
              assertions=[
                  {"kind": "required", "field": "变动类型", "data": "", "expect": "reject",
                   "rule": "是否必填=是"},
                  {"kind": "enum", "field": "变动类型", "data": "不存在的枚举值", "expect": "reject",
                   "allowed": ENUM_ALLOWED, "rule": "选项范围"},
                  {"kind": "enum", "field": "变动类型", "data": "企业储值充值", "expect": "accept",
                   "allowed": ENUM_ALLOWED, "rule": "选项范围"},
                  {"kind": "default", "field": "变动类型", "data": "",
                   "expect": "default=企业储值充值", "rule": "默认选中：企业储值充值"},
              ]),
        _case("TC-r2", "企业账户充值金额-条件显示", rule_id="FR-r2", field="企业账户充值金额",
              assertions=[
                  {"kind": "visibility", "field": "企业账户充值金额", "expect": "visible",
                   "when": "当变动类型=企业储值充值 且 变更方式=手动输入，显示该字段"},
                  {"kind": "visibility", "field": "企业账户充值金额", "expect": "hidden",
                   "when": "不满足上述条件"},
              ]),
        _case("TC-r3", "上传文件（企业充值）-条件显示", rule_id="FR-r3", field="上传文件（企业充值）",
              assertions=[
                  {"kind": "visibility", "field": "上传文件（企业充值）", "expect": "visible",
                   "when": "当变动类型=企业储值充值 且 变更方式=批量导入，显示该字段"},
              ]),
    ]

    def _plan(self):
        return build_form_plan(compile_cases(self.CASES), RECHARGE_FORM_LOCATORS)

    def test_default_check_carries_owner_and_is_not_reported_unsupported(self):
        plan = self._plan()
        check = next(c for c in plan["checks"]
                     if c["type"] == "default_selected" and c["option"] == "企业储值充值")
        assert [o["case_id"] for o in check["owners"]] == ["TC-r1"]
        assert not any("default" in s["reason"] for s in plan["skipped"])

    def test_default_state_visibility_is_attributed_to_its_case(self):
        plan = self._plan()
        check = next(c for c in plan["checks"] if c["type"] == "visible_now")
        assert check["case_id"] == "TC-r2"          # 默认态 = 充值 + 手动输入
        assert check["expect_labels"] == ["企业账户充值金额"]

    def test_toggle_carries_owner_only_for_fully_matching_state(self):
        plan = self._plan()
        toggles = [c for c in plan["checks"] if c["type"] == "visibility"]
        owners_of_batch_import = {o["case_id"] for c in toggles
                                  if c["option"] == "批量导入" and c["mode"].get("变动类型") == "企业储值充值"
                                  for o in (c.get("owners") or [])}
        refund_owners = {o["case_id"] for c in toggles
                         if c["option"] == "企业储值退款（仅扣减储值不退款）"
                         for o in (c.get("owners") or [])}
        assert owners_of_batch_import == {"TC-r3"}
        assert refund_owners == set()               # 没有用例声明该条件 → 不得凭空归属

    def test_unbuildable_visibility_is_reported_not_silently_dropped(self):
        plan = self._plan()
        assert any("条件显示组合未在计划中构建" in s["reason"] for s in plan["skipped"])

    def test_radio_required_is_explicitly_unverified_not_skipped(self):
        plan = self._plan()
        check = next(c for c in plan["checks"] if c["type"] == "submit_only")
        assert check["case_id"] == "TC-r1"
        assert "提交" in check["reason"]
        assert not any("单选组" in s["reason"] for s in plan["skipped"])

    def test_radio_enum_becomes_readonly_option_check(self):
        plan = self._plan()
        checks = [c for c in plan["checks"] if c["type"] == "radio_options"]
        assert [c["expect"] for c in checks] == ["reject", "accept"]
        assert all(c["case_id"] == "TC-r1" for c in checks)
        assert checks[0]["allowed"] == self.ENUM_ALLOWED

    def test_every_case_assertion_is_attributed_or_explicitly_skipped(self):
        """核心不变量：用例声明的断言要么落到带归属的检查上，要么出现在 skipped 里。

        （定位器自身配置的条件显示/默认项可能没有用例声明，那种"无主"是合法的；
        真正不允许的是**用例声明的断言**既没执行、也没登记。）
        """
        plan = self._plan()
        manifest = compile_cases(self.CASES)
        attributed = {c.get("case_id") for c in plan["checks"] if c.get("case_id")}
        attributed |= {o["case_id"] for c in plan["checks"] for o in (c.get("owners") or [])}
        skipped_fields = {s["field"] for s in plan["skipped"]}
        for case in manifest["cases"]:
            if case["identifier"] in attributed:
                continue
            assert case["field"] in skipped_fields, (
                "用例 %s 的断言既没有归属检查、也没有登记为跳过" % case["identifier"])

    def test_executor_emits_one_assertion_per_owner(self):
        svc = WebCaseLinkService.__new__(WebCaseLinkService)
        manifest = compile_cases(self.CASES)
        script = svc._generate_playwright_script(
            _case("TC-100", "B端储值充值冒烟", rule_id="FR-1", field="企业账户充值金额"),
            "https://example.invalid/login", ["登录", "储值账户列表"],
            manifest=manifest, form_plan=build_form_plan(manifest, RECHARGE_FORM_LOCATORS),
        )
        assert "recAll(" in script and "ownersOf(" in script
        assert "visible_now" in script and "radio_options" in script and "submit_only" in script


class TestUploadChecks:
    """上传文件类断言：**默认关闭**（服务端副作用风险），开启后也只验证客户端规则。

    判定逻辑已搬到服务端 ``web_upload_evidence``（见 ``test_web_upload_evidence.py``），
    这里只锁定：计划生成口径、样本字节数、隔离策略、以及"浏览器侧不产生结论"。
    """

    FIELD = "上传文件（企业充值）"

    CASES = [
        _case("TC-u1", "上传文件（企业充值）-边界值与格式校验", rule_id="FR-u1", field=FIELD,
              assertions=[
                  {"kind": "file_format", "data": ".xlsx", "expect": "accept",
                   "rule": "文件格式仅支持：xlsx、xls、csv"},
                  {"kind": "file_format", "data": ".exe", "expect": "reject",
                   "rule": "文件格式仅支持：xlsx、xls、csv"},
                  {"kind": "file_size", "data": "<=1M", "expect": "accept", "rule": "文件大小1M"},
                  {"kind": "file_size", "data": ">1M", "expect": "reject", "rule": "文件大小1M"},
              ]),
        _case("TC-u2", "上传文件（企业充值）-必填与取值校验", rule_id="FR-u2", field=FIELD,
              assertions=[{"kind": "required", "data": "", "expect": "reject", "rule": "是否必填=是"}]),
    ]

    def test_default_off_skips_upload_assertions_explicitly(self):
        plan = build_form_plan(compile_cases(self.CASES), RECHARGE_FORM_LOCATORS)
        assert not [c for c in plan["checks"] if c["type"] == "upload_file"]
        reasons = [s for s in plan["skipped"] if s["field"] == self.FIELD]
        assert len(reasons) == 4                       # 4 条格式/大小断言逐条登记
        assert all("allow_upload_checks=false" in s["reason"] for s in reasons)

    def test_required_on_upload_is_explicitly_unverified(self):
        plan = build_form_plan(compile_cases(self.CASES), RECHARGE_FORM_LOCATORS)
        check = next(c for c in plan["checks"]
                     if c["type"] == "submit_only" and c["field"] == self.FIELD)
        assert check["case_id"] == "TC-u2"
        assert "上传控件" in check["reason"]

    def test_opt_in_generates_evidence_typed_checks(self):
        plan = build_form_plan(compile_cases(self.CASES), RECHARGE_FORM_LOCATORS, allow_upload=True)
        uploads = [c for c in plan["checks"] if c["type"] == "upload_file"]
        # 阈值已确认 + 允许格式 xlsx/xls/csv → 断言按边界与格式展开
        assert [(c["evidence_kind"], c["expect"], c["sample"]["kind"]) for c in uploads] == [
            ("format", "accept", "valid_workbook"),
            ("format", "accept", "valid_xls"),
            ("format", "accept", "valid_csv"),
            ("format", "reject", "disallowed_extension"),
            ("size", "accept", "boundary_minus"),
            ("size", "accept", "boundary"),
            ("size", "reject", "boundary_plus"),
        ]
        assert all(c["case_id"] == "TC-u1" for c in uploads)
        assert all(c["mode"].get("变更方式") == "批量导入" for c in uploads)
        assert uploads[3]["sample"]["name"].endswith(".exe")
        assert not [s for s in plan["skipped"]
                    if s["field"] == self.FIELD and "allow_upload_checks" in s["reason"]]

    def test_size_samples_do_not_assume_the_threshold(self):
        """阈值未确认时：不得伪造临界样本，且要显式标注未确认。"""
        loc = dict(RECHARGE_FORM_LOCATORS)
        loc.pop("upload_size_limit_bytes", None)
        plan = build_form_plan(compile_cases(self.CASES), loc, allow_upload=True)
        size_reject = next(c for c in plan["checks"] if c["type"] == "upload_file"
                           and c["evidence_kind"] == "size" and c["expect"] == "reject")
        assert size_reject["sample"]["threshold_confirmed"] is False
        assert size_reject["sample"]["kind"] == "oversize_obvious"
        assert size_reject["sample"]["bytes"] == 2 * 1024 * 1024

    def test_confirmed_threshold_expands_to_boundary_samples(self):
        """用户已确认 1M = 1,048,576（前后端同口径）→ 大小断言按 N−1 / N / N+1 展开。"""
        loc = dict(RECHARGE_FORM_LOCATORS)
        loc["upload_size_limit_bytes"] = 1_048_576
        plan = build_form_plan(compile_cases(self.CASES), loc, allow_upload=True)
        sizes = [(c["sample"]["kind"], c["sample"]["bytes"], c["expect"])
                 for c in plan["checks"] if c["type"] == "upload_file"
                 and c["evidence_kind"] == "size"]
        assert ("boundary_minus", 1_048_575, "accept") in sizes
        assert ("boundary", 1_048_576, "accept") in sizes
        assert ("boundary_plus", 1_048_577, "reject") in sizes
        assert all(c["sample"]["threshold_confirmed"] is True
                   for c in plan["checks"] if c["type"] == "upload_file"
                   and c["evidence_kind"] == "size")

    def test_allowed_formats_expand_and_all_have_valid_samples(self):
        """声明允许的三种格式都要有**有效样本**并可执行（含 .xls，用户要求覆盖）。"""
        plan = build_form_plan(compile_cases(self.CASES), RECHARGE_FORM_LOCATORS, allow_upload=True)
        formats = [c["sample"]["name"] for c in plan["checks"]
                   if c["type"] == "upload_file" and c["evidence_kind"] == "format"
                   and c["expect"] == "accept"]
        assert formats == ["upload_valid.xlsx", "upload_valid.xls", "upload_valid.csv"]
        assert not [s for s in plan["skipped"] if s.get("format")]
        sources = {c["sample"]["name"]: c["sample"]["source"] for c in plan["checks"]
                   if c["type"] == "upload_file"}
        assert sources["upload_valid.xlsx"] == "workbook_stored"
        assert sources["upload_valid.xls"] == "xls_bytes"      # 有效 BIFF 样本
        assert sources["upload_valid.csv"] == "csv_text"

    def test_declared_but_unbuildable_format_is_registered(self):
        """若某声明格式没有有效样本，必须显式登记为未执行（不得算通过）。"""
        loc = dict(RECHARGE_FORM_LOCATORS)
        loc["upload_allowed_formats"] = ["xlsx", "xls", "csv", "ods"]
        loc["upload_unsupported_sample_formats"] = {"ods": "无法构造有效的 .ods 样本"}
        plan = build_form_plan(compile_cases(self.CASES), loc, allow_upload=True)
        assert any(s.get("format") == "ods" and "无可用的有效样本" in s["reason"]
                   for s in plan["skipped"])

    def test_csv_sample_is_text_not_fake_workbook(self):
        plan = build_form_plan(compile_cases(self.CASES), RECHARGE_FORM_LOCATORS, allow_upload=True)
        csv_check = next(c for c in plan["checks"] if c["type"] == "upload_file"
                         and c["sample"]["name"] == "upload_valid.csv")
        assert csv_check["sample"]["source"] == "csv_text"
        assert csv_check["sample"]["mime"] == "text/csv"

    def test_executor_collects_evidence_and_does_not_decide(self):
        """浏览器侧**只采集证据**：不得出现 pass/未验证判定代码。"""
        svc = WebCaseLinkService.__new__(WebCaseLinkService)
        manifest = compile_cases(self.CASES)
        script = svc._generate_playwright_script(
            _case("TC-100", "B端储值充值冒烟", rule_id="FR-1", field="企业账户充值金额"),
            "https://example.invalid/login", ["登录", "储值账户列表"],
            manifest=manifest, form_plan=build_form_plan(manifest, RECHARGE_FORM_LOCATORS, allow_upload=True),
        )
        assert "UPLOAD_EVIDENCE" in script
        assert "messages_before" in script and "messages_after" in script
        assert "timed_out" in script and "control_reset" in script
        # 判定相关的旧代码不得回归（它们正是"假通过"的来源）
        assert "formatOrSize" not in script
        assert "after <= before ? 'pass'" not in script
        assert "uploadGuardBlocked" not in script

    def test_executor_isolates_by_allowlist_and_blocks_multipart(self):
        """隔离判定依据 = 放行清单 + 默认拒绝；上传关键词只用于**事后分类**。"""
        svc = WebCaseLinkService.__new__(WebCaseLinkService)
        manifest = compile_cases(self.CASES)
        script = svc._generate_playwright_script(
            _case("TC-100", "B端储值充值冒烟", rule_id="FR-1", field="企业账户充值金额"),
            "https://example.invalid/login", ["登录", "储值账户列表"],
            manifest=manifest, form_plan=build_form_plan(manifest, RECHARGE_FORM_LOCATORS, allow_upload=True),
            upload_allow_hosts=["console-uat.example.com"],
        )
        assert "ISOLATION_BLOCKED" in script
        assert "allow_hosts" in script and "console-uat.example.com" in script
        assert "await route.abort()" in script
        # 只读声明：断言随执行器注释同步（旧文案「不会点击「确定」」已在 rev67 口径改写）
        assert "客户端层断言全程只读" in script
        # 文件上传的传输特征被无条件阻断（与放行清单无关）
        decision = script.split("function isolationDecision", 1)[1].split("function blockedKind", 1)[0]
        assert "multipart/form-data" in decision
        # 关键词匹配不得进入"决策"函数（只在分类函数里做归因）
        assert "oss|minio" not in decision
        assert "oss|minio" in script

    def test_executor_classifies_blocked_requests(self):
        """三类阻断可区分：上传 / 业务接口 / 未知。"""
        svc = WebCaseLinkService.__new__(WebCaseLinkService)
        manifest = compile_cases(self.CASES)
        script = svc._generate_playwright_script(
            _case("TC-100", "B端储值充值冒烟", rule_id="FR-1", field="企业账户充值金额"),
            "https://example.invalid/login", ["登录", "储值账户列表"],
            manifest=manifest, form_plan=build_form_plan(manifest, RECHARGE_FORM_LOCATORS, allow_upload=True),
        )
        for kind in ("upload_blocked", "non_upload_api_blocked", "unknown_request_blocked"):
            assert kind in script
        assert "isolation_blocked_kinds" in script


class TestFormExecutorSafety:
    """执行器必须是**只读**的：任何提交类点击都是红线。"""
    SUBMIT_PATTERNS = ("确 定", "确定", "保存", "提交", "ant-btn-primary")

    def _script(self, manifest_cases=None) -> str:
        svc = WebCaseLinkService.__new__(WebCaseLinkService)
        cases = manifest_cases or TestFormPlan.CASES
        manifest = compile_cases(cases)
        plan = build_form_plan(manifest, RECHARGE_FORM_LOCATORS)
        case = _case("TC-100", "B端储值充值冒烟", rule_id="FR-1", field="企业账户充值金额")
        return svc._generate_playwright_script(
            case, "https://example.invalid/login", ["登录", "储值账户列表"],
            manifest=manifest, form_plan=plan,
        )

    def test_script_embeds_form_plan_and_executor(self):
        script = self._script()
        assert "const FORM_PLAN" in script
        assert "await runFormChecks()" in script
        assert "COMPILED_ASSERTION" in script
        assert "PR2_STORAGE_ACCOUNT_NO" in script

    def test_executor_never_clicks_submit(self):
        import re

        script = self._script()
        clicks = re.findall(r"\.click\([^)]*\)", script)
        dangerous = [c for c in clicks if any(p in c for p in self.SUBMIT_PATTERNS)]
        assert not dangerous, f"出现提交类点击：{dangerous}"

    def test_executor_declares_readonly_intent(self):
        script = self._script()
        # 与执行器注释文案同步：只读声明（客户端层只读 + 负路径提交层口径）
        assert "客户端层断言全程只读" in script
        assert "blocked" in script and "expect(summary.blocked" in script


class TestFormExecutorCodegenHygiene:
    """生成产物必须能通过 TS 解析。

    ★ 关键背景：``_FORM_EXECUTOR_TS`` 是 **raw string 常量**，经 ``.replace("@@…@@")``
    渲染，**不经过 f-string 求值**。因此模板内必须写**单花括号**——写成 ``{{`` 会原样
    输出到产物，产生 TS1xxx 语法错误（TS1131/TS1005/TS1109/TS1128），整个脚本无法解析。

    这个坑真实发生过：P1-3 阶段 A 新增 requestId 采集时误按 f-string 规则写了 ``{{``，
    导致产物出现 6 处语法错误。本测试即为该 bug 的回归防线。
    """

    def _script(self) -> str:
        svc = WebCaseLinkService.__new__(WebCaseLinkService)
        manifest = compile_cases(TestFormPlan.CASES)
        plan = build_form_plan(manifest, RECHARGE_FORM_LOCATORS)
        case = _case("TC-100", "B端储值充值冒烟", rule_id="FR-1", field="企业账户充值金额")
        return svc._generate_playwright_script(
            case, "https://example.invalid/login", ["登录", "储值账户列表"],
            manifest=manifest, form_plan=plan,
        )

    def _code_lines_with_double_brace(self, script: str) -> list[str]:
        """挑出「疑似转义残留」的行。

        需要排除两类**合法**的双花括号：
          * JSON 数据行（``const FORM_PLAN = {...}``）：JSON 里的 ``{}`` 是数据内容；
          * JS 正则/字符串里有意保留的 ``{{key}}`` 占位符语法。
        判定方式是看该 ``{{`` 是否出现在**代码结构位置**（声明、控制流、调用）。
        """
        structural = ("Array<", "= {", "});", "=> {", "for (", "catch {", "try {")
        out: list[str] = []
        for line in script.splitlines():
            if "{{" not in line and "}}" not in line:
                continue
            stripped = line.strip()
            if stripped.startswith("const FORM_PLAN"):
                continue          # JSON 数据行
            if "/{{" in line or "'{{" in line or '"{{' in line:
                continue          # 有意的模板占位符语法
            if any(k in line for k in structural):
                out.append(stripped)
        return out

    def test_no_brace_escape_residue_in_code_positions(self):
        """模板写 {{ 会在产物里原样残留 → 必须有 0 处（本条是转义 bug 的回归防线）。"""
        script = self._script()
        residue = self._code_lines_with_double_brace(script)
        assert not residue, (
            f"产物中 {len(residue)} 处代码结构位置出现双花括号，疑似 f-string 转义残留"
            f"（_FORM_EXECUTOR_TS 是 raw string，应写单花括号）：\n"
            + "\n".join(f"  {l[:120]}" for l in residue[:6])
        )

    def test_executor_collects_request_id_for_defect_evidence(self):
        """P1-3 阶段 A：负路径证据必须能直接给出 requestId（缺陷单必需字段）。"""
        script = self._script()
        # 采集层
        assert "const pickRequestId" in script
        assert "pickRequestId(r)" in script
        assert "body: string; requestId: string" in script
        # 候选响应头（网关命名不一致，需逐个尝试）
        for header in ("requestid", "x-request-id", "request-id", "traceid", "x-trace-id"):
            assert header in script, f"缺少候选响应头 {header}"
        # 消费层：证据串与坐实分支
        assert "requestId:${n.requestId" in script
        assert "定位凭据：requestId=${accRid}" in script
        assert "未在响应头暴露" in script   # 取不到时显式暴露，不伪造
        # 零捕获时的地面真相里也带上
        assert "r: n.requestId" in script


class TestReadonlySelectorStaysUnverified:
    """只读选择器（readonly input）的「期望拒绝」断言：客户端与提交层都拿不到证据。

    rev94 曾两次尝试让 `required_error` 的只读分支入负路径提交层
    （见证据包 `code/web_case_compiler.rev94.md` 与 `verify/rev95-执行期可观测性/`）：

    - 第 1 次（10-12 下午）：重挂 `WT-BF18D125` 后连续 3 次执行超时
      （602s / 618s，上限 600s），唯一差别是脚本版本的同批次对照资产
      `WT-647CB3A1` 57s 正常完成 ⇒ 判定为改动引入并回滚。
    - 第 2 次（补了执行期落盘 rev95 后再试，短超时 180s）：仍是超时，但这次
      **拿到了现场**——日志停在入队后，进入提交层；`pw:api` 追踪显示脚本在
      提交层反复轮询（118× count / 46× waitForTimeout），最终停在
      `locator.evaluate`（`.ant-modal-footer button`）被 runner 上限杀掉。

    根因（实测）：TR-267 里 8 条 `submit_phase.queued=1` 的资产**全是 select 类**，
    被提交层的 `if (kind==='select') { … continue }` 短路，因此「真实提交」
    （clickSubmit → settleRound → 兜底填充 → 重提交）在 CS create_page 上
    **从未被执行成功过**；`Member`（kind=input）是第一条能走到该路径的字段
    ⇒ 首次触发即挂死（与 rev71e/rev90 注释记录的历史现象一致）。
    ⇒ 改动逻辑本身正确，但它依赖的路径不可用；本类只保留「不该顺手放开」的反向锁。
    """

    def test_readonly_probe_still_unverified(self):
        """反向锁：input_validation（注入非法探针值）的只读分支仍如实记 unverified。

        只读控件无法注入探针值，入队也构造不出「非法值已提交」的负路径，
        因此这一半**不该**改——防止将来把两处守卫一起"顺手"放开。
        """
        svc = WebCaseLinkService.__new__(WebCaseLinkService)
        manifest = compile_cases(TestFormPlan.CASES)
        plan = build_form_plan(manifest, RECHARGE_FORM_LOCATORS)
        case = _case("TC-101", "B端储值充值冒烟", rule_id="FR-1", field="企业账户充值金额")
        script = svc._generate_playwright_script(
            case, "https://example.invalid/login", ["登录", "储值账户列表"],
            manifest=manifest, form_plan=plan,
        )
        seg = script.split("const roAttr2 =", 1)[1]
        seg = seg[:seg.index("continue;")]
        assert "unverified" in seg
        assert "submitQueue.push" not in seg




class TestFieldAliases:
    """P1-② 字段别名（2026-10-17）：文档字段名带英文前缀时经显式别名表映射到定位器键。"""

    LOCATORS = {
        "fields": {
            "商品編碼": {"kind": "input", "selector": "#code"},
            "所屬品牌": {"kind": "select", "selector": "#brand"},
        },
        "aliases": {
            "Item Code商品編碼": "商品編碼",
            "Brand所屬品牌": "所屬品牌",
        },
        "field_modes": {"商品編碼": "clear"},
    }

    def test_alias_maps_document_field_to_locator_key(self):
        manifest = compile_cases([_case("TC-A", "商品編碼-必填校验", rule_id="FR-1",
                                        field="Item Code商品編碼",
                                        assertions=[{"field": "Item Code商品編碼", "kind": "required",
                                                     "rule": "是否必填=是", "data": "", "expect": "reject"}])])
        plan = build_form_plan(manifest, self.LOCATORS)
        assert plan is not None
        assert plan["checks"], "别名解析后必须产出检查"
        assert all(c["field"] == "商品編碼" for c in plan["checks"]), "检查必须落到定位器键上"
        assert plan.get("skipped") == []

    def test_no_alias_stays_pending_with_diagnostics(self):
        manifest = compile_cases([_case("TC-B", "未知字段-必填校验", rule_id="FR-2",
                                        field="未知字段X",
                                        assertions=[{"field": "未知字段X", "kind": "required",
                                                     "rule": "是否必填=是", "data": "", "expect": "reject"}])])
        plan = build_form_plan(manifest, self.LOCATORS)
        assert plan is not None
        assert plan["checks"] == []
        skipped = plan["skipped"]
        assert len(skipped) == 1 and skipped[0]["field"] == "未知字段X"
        assert skipped[0]["reason"] == "无定位器"

    def test_alias_target_must_exist_in_fields(self):
        locators = {"fields": {"商品編碼": {"kind": "input", "selector": "#code"}},
                    "aliases": {"幽灵字段": "不存在的键"}}
        manifest = compile_cases([_case("TC-C", "幽灵字段-必填校验", rule_id="FR-3",
                                        field="幽灵字段",
                                        assertions=[{"field": "幽灵字段", "kind": "required",
                                                     "rule": "是否必填=是", "data": "", "expect": "reject"}])])
        plan = build_form_plan(manifest, locators)
        assert plan is not None and plan["checks"] == [], "别名目标不存在时不得乱匹配"
        assert plan["skipped"][0]["field"] == "幽灵字段"


class TestCheckboxGroupSemantics:
    """P1-③（2026-10-17）：勾选组/开关不得走 fill —— 实测 TR-500「營業時間」
    是 7 个 checkbox，配置成 input 后 fill 抛 'Input of type "checkbox" cannot be filled'，
    整条作业判 blocked。"""

    LOCATORS = {
        "fields": {
            "營業時間": {"kind": "checkbox_group",
                      "selector_hint": '.ant-form-item:has-text("Business Hours")'},
            "營業狀態": {"kind": "switch"},
        },
    }

    def _plan(self, field: str, kind: str, rule: str = "是否必填=是", data: str = ""):
        manifest = compile_cases([_case("TC-CB", f"{field}-{kind}", rule_id="FR-CB", field=field,
                                        assertions=[{"field": field, "kind": kind, "rule": rule,
                                                     "data": data, "expect": "reject"}])])
        return build_form_plan(manifest, self.LOCATORS)

    def test_required_on_checkbox_group_is_submit_only(self):
        plan = self._plan("營業時間", "required")
        assert plan is not None
        types = [c["type"] for c in plan["checks"]]
        assert types == ["submit_only"], types
        assert "勾选组" in plan["checks"][0]["reason"]
        assert not any(c["type"] == "required_error" for c in plan["checks"]), "不得再走 fill 路径"

    def test_required_on_switch_is_submit_only(self):
        plan = self._plan("營業狀態", "required")
        types = [c["type"] for c in plan["checks"]]
        assert types == ["submit_only"], types
        assert "开关" in plan["checks"][0]["reason"]

    def test_enum_on_checkbox_group_is_choice_options(self):
        plan = self._plan("營業時間", "enum", rule="支持選擇7組，", data="7組")
        types = [c["type"] for c in plan["checks"]]
        assert types == ["choice_options"], types
        assert plan["checks"][0]["rule"] == "支持選擇7組，"

    def test_render_contains_type_guard_and_choice_handler(self):
        from app.services.web_case_compiler import render_form_executor
        plan = self._plan("營業時間", "required")
        ts = render_form_executor(plan)
        assert "控件实际类型为" in ts, "required_error 必须带控件类型守卫"
        assert "check.type === 'choice_options'" in ts, "必须渲染勾选组选项校验分支"
        assert "choiceOptionLabels" in ts


# ============ R1（2026-10-17）：字段逐断言解析的契约 ============

R1_LOCATORS = {
    "fields": {
        "商品名稱": {"kind": "text", "locator": "input[name=name]", "label": "商品名稱"},
        "商品描述": {"kind": "text", "locator": "textarea[name=desc]", "label": "商品描述"},
        "商品編碼": {"kind": "text", "locator": "input[name=code]", "label": "商品編碼"},
    },
    "aliases": {"Item Code商品編碼": "商品編碼"},
}


def _r1_case(identifier: str, rule_field: str, assertions: list[dict]) -> SimpleNamespace:
    return SimpleNamespace(
        identifier=identifier, name=identifier, module="美心-商品",
        description="",
        custom_fields={"requirement_rule": {"field": rule_field, "type": "text"},
                       "assertions": assertions},
        test_case_steps=[],
    )


def _r1_assert(field, data="51字", kind="max_length"):
    return {"kind": kind, "field": field, "data": data, "expect": "reject", "rule": "长度上限"}


class TestPerAssertionField:
    """R1：断言自身 field 优先，规则字段仅兜底。"""

    def test_multi_field_assertions_land_on_their_own_fields(self):
        case = _r1_case("TC-M", "商品名稱", [
            _r1_assert("商品名稱", "51字"),
            _r1_assert("商品描述", "201字"),
            _r1_assert("商品編碼", "21字"),
        ])
        plan = build_form_plan(compile_cases([case]), R1_LOCATORS)
        pairs = [(c["field"], c.get("data")) for c in plan["checks"] if c["type"] == "input_validation"]
        assert pairs == [("商品名稱", "51字"), ("商品描述", "201字"), ("商品編碼", "21字")]

    def test_rule_field_without_locator_does_not_block_assertions(self):
        """规则字段无定位器、但断言字段有 → 必须能编译（旧实现整条丢弃）。"""
        case = _r1_case("TC-R", "商品標題", [_r1_assert("商品名稱", "51字")])
        plan = build_form_plan(compile_cases([case]), R1_LOCATORS)
        assert plan["check_count"] == 1
        assert plan["skipped_count"] == 0

    def test_partially_locatable_case_skips_only_bad_assertion(self):
        case = _r1_case("TC-P", "商品名稱", [
            _r1_assert("商品名稱", "51字"),
            _r1_assert("不存在欄位", "9字"),
        ])
        plan = build_form_plan(compile_cases([case]), R1_LOCATORS)
        assert plan["check_count"] == 1
        assert plan["skipped_count"] == 1
        assert plan["skipped"][0]["field"] == "不存在欄位"
        assert "该断言的字段" in plan["skipped"][0]["reason"]

    def test_alias_applies_to_assertion_field(self):
        case = _r1_case("TC-A", "商品名稱", [_r1_assert("Item Code商品編碼", "21字")])
        plan = build_form_plan(compile_cases([case]), R1_LOCATORS)
        assert plan["checks"][0]["field"] == "商品編碼"

    def test_assertion_without_field_is_reported_not_silent(self):
        """有断言却没有任何字段 → 必须给出原因（不得混入静默零检查）。"""
        case = _r1_case("TC-N", "", [
            {"kind": "max_length", "data": "51字", "expect": "reject", "rule": "长度上限"},
        ])
        plan = build_form_plan(compile_cases([case]), R1_LOCATORS)
        assert plan["check_count"] == 0
        assert plan["skipped_count"] == 1
        assert plan["skipped"][0]["case_id"] == "TC-N"
        assert "缺少 field" in plan["skipped"][0]["reason"]

    def test_case_without_assertions_stays_silent(self):
        """叙述型用例（无断言）仍然静默：本来就没有可执行内容。"""
        case = SimpleNamespace(identifier="TC-X", name="叙述", module="m", description="",
                               custom_fields={"requirement_rule_id": "RQ-1"},
                               test_case_steps=[])
        manifest = compile_cases([case])
        plan = build_form_plan(manifest, R1_LOCATORS)
        if plan is not None:
            assert all(s.get("case_id") != "TC-X" for s in plan.get("skipped") or [])


class TestConfigCheckSource:
    """R5（2026-10-17）：配置驱动的检查必须显式声明来源。

    不声明时，台账里"无 case_id 且无 source"的可见性检查会被判成未归属，
    每次运行报一条假告警（TR-697 实测 2 条 visibility:*）。
    """

    def test_visibility_checks_declare_config_source(self):
        plan = build_form_plan(compile_cases(RULE_CASES), RECHARGE_FORM_LOCATORS)
        visibility = [c for c in plan["checks"] if c["type"] == "visibility"]
        assert visibility, "定位器里有 toggles，应当生成 visibility 检查"
        assert all(c.get("source") == "locator_config" for c in visibility)

    def test_default_selected_checks_declare_config_source(self):
        plan = build_form_plan(compile_cases(RULE_CASES), RECHARGE_FORM_LOCATORS)
        defaults = [c for c in plan["checks"] if c["type"] == "default_selected"]
        assert defaults
        assert all(c.get("source") == "locator_config" for c in defaults)
