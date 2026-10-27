# -*- coding: utf-8 -*-
"""P0-1：缺陷类型（defect_type）——决策结果归类、IDP 载荷与描述携带。"""
from app.services.defect_decision_service import (
    DefectDecision,
    DefectDecisionResult,
    DefectDecisionService,
)


def _make(**kw):
    base = dict(
        decision="create", priority="high", priority_code="priority-1", priority_id=1,
        reason="r", error_type="assertion_failed", failure_summary="f",
    )
    base.update(kw)
    return base


class TestDefectTypeClassification:
    def test_validation_missing(self):
        t = DefectDecisionService._classify_defect_type(
            "test_failed", "modify 接受了冲突商品 11281，缺少唯一性校验")
        assert t == "数据校验缺失"

    def test_validation_missing_variant(self):
        t = DefectDecisionService._classify_defect_type(
            "assertion_failed", "服务端未执行校验，应拒绝但接受了非法输入")
        assert t == "数据校验缺失"

    def test_security(self):
        t = DefectDecisionService._classify_defect_type(
            "test_failed", "越权访问其他租户数据")
        assert t == "安全缺陷"

    def test_robustness(self):
        t = DefectDecisionService._classify_defect_type(
            "test_failed", "分页接口越界页返回 data:null，响应结构不一致")
        assert t == "接口健壮性"

    def test_performance(self):
        t = DefectDecisionService._classify_defect_type(
            "test_failed", "接口响应时间超过性能阈值")
        assert t == "性能问题"

    def test_default_functional(self):
        t = DefectDecisionService._classify_defect_type("assertion_failed", "列表字段缺失")
        assert t == "功能缺陷"

    def test_none_and_skip_empty(self):
        assert DefectDecisionService._classify_defect_type("none", "x") == ""
        assert DefectDecisionService._classify_defect_type("skip", "x") == ""

    def test_backward_compatible_default(self):
        # 旧构造方式（无 defect_type 参数）不破坏
        r = DefectDecisionResult(**_make())
        assert r.defect_type == ""


class TestDecideCarriesDefectType:
    def test_create_branch(self):
        result = DefectDecisionService.decide(
            test_status="failed",
            error_message="modify 接受了冲突商品，缺少校验",
            response_status_code=None,
            expected_status_code=None,
        )
        assert result.defect_type == "数据校验缺失"

    def test_skip_branch_empty(self):
        result = DefectDecisionService.decide(
            test_status="failed",
            error_message="网络不可达，连接超时",
            response_status_code=None,
            expected_status_code=None,
        )
        assert result.defect_type == ""

    def test_passed_branch_empty(self):
        result = DefectDecisionService.decide(test_status="passed", error_message=None, response_status_code=None, expected_status_code=None)
        assert result.defect_type == ""


class TestPlaywrightInteractionErrorsAreNotDefects:
    """TR-172 复核回归：定位器/交互超时缺 SUT 证据，不得开成缺陷单。

    实测案例（jf-20260122392-01-2963 的来源）：TR-172 / TC-321795 的失败文本为
      `TimeoutError: locator.waitFor: Timeout 15000ms exceeded.`
    平台据此把原始报错当"缺陷现象"，产出不合规标题。故该类错误必须跳过。
    """

    #: **与平台入库内容一致的原样文本**（只有首行！实测 TR-172 的 error_message）
    TR172_ERROR_STORED = "TimeoutError: locator.waitFor: Timeout 15000ms exceeded."

    #: 运行日志里的完整形态（含 Call log），也应命中
    TR172_ERROR_FULL = (
        "TimeoutError: locator.waitFor: Timeout 15000ms exceeded.\n"
        "Call log:\n"
        "  - waiting for locator('.ant-form').first() to be visible"
    )

    def test_tr172_stored_message_is_skipped(self):
        """回归要点：入库文本只有首行，规则必须对**该形态**生效。"""
        r = DefectDecisionService.decide(
            test_status="failed",
            error_message=self.TR172_ERROR_STORED,
            response_status_code=None,
            expected_status_code=None,
        )
        assert r.decision == DefectDecision.SKIP
        assert "自动化交互层失败" in r.reason
        # 跳过时不得带上缺陷类型/现象（否则仍可能被拼进标题）
        assert r.defect_type == ""

    def test_tr172_full_log_form_is_skipped(self):
        r = DefectDecisionService.decide(
            test_status="failed",
            error_message=self.TR172_ERROR_FULL,
            response_status_code=None,
            expected_status_code=None,
        )
        assert r.decision == DefectDecision.SKIP

    def test_other_interaction_errors_are_skipped(self):
        for msg in (
            "TimeoutError: locator.click: Timeout 30000ms exceeded.",
            "TimeoutError: locator.fill: Timeout 20000ms exceeded.",
            "Error: strict mode violation: locator('.ant-form') resolved to 2 elements",
        ):
            r = DefectDecisionService.decide(
                test_status="failed", error_message=msg,
                response_status_code=None, expected_status_code=None,
            )
            assert r.decision == DefectDecision.SKIP, msg
            # 跳过理由要说清"为什么不自动开单"，不能是空串
            assert "自动化交互层失败" in r.reason

    def test_environment_class_failures_are_now_skipped(self):
        """口径变更（2026-10-07 授权）：环境/整体超时类失败不再自动开单。

        此前这两条被 test_web_failure_honesty.py 固定为 CREATE；因标题只能落成
        技术报错串（无 SUT 侧证据），经确认后一起收紧。
        """
        for msg in (
            "Error: locator.count: Target page, context or browser has been closed",
            "Error: locator.fill: Test timeout of 30000ms exceeded.",
            "Error: Execution context was destroyed, most likely because of a navigation",
        ):
            r = DefectDecisionService.decide(
                test_status="failed", error_message=msg,
                response_status_code=None, expected_status_code=None,
            )
            assert r.decision == DefectDecision.SKIP, msg
            assert "环境/执行器类失败" in r.reason
            assert r.defect_type == ""

    def test_browser_native_crash_is_skipped(self):
        """Chromium 进程**原生崩溃**不得开成 SUT 缺陷单（2026-10-08 实测 TR-192）。

        报文首行即 `Stacktrace:`，后跟 `ptr1=…` / `failure_message_object=…` 原生栈帧。
        该形态此前经规则表全表未命中 → `decide()` 判 create，产出标题就是 `Stacktrace:`
        的垃圾单（`idp_defect_records.b6f950c5`）——平台把**执行器自身故障**当成了 SUT 现象。

        规则锚定**报文首行**（`^`，本函数用 re.search 且未开 MULTILINE），故此处同时
        固定三件事：① 首行原生栈帧 → SKIP；② 有 SUT 侧证据时不得被吞；③ 正文提及
        `Stacktrace:` 字样的合法断言消息不触发。
        """
        # 平台落库的真实形态（多行、\r\n 分隔）
        stored = (
            "Stacktrace:\r\n"
            "    ptr1=00000140ADA660A1\r\n"
            "    ptr2=00000140ADA66111\r\n"
            "    failure_message_object=00000038D39F6BF0"
        )
        r = DefectDecisionService.decide(
            test_status="failed", error_message=stored,
            response_status_code=None, expected_status_code=None,
        )
        assert r.decision == DefectDecision.SKIP, r.reason
        assert "浏览器进程原生崩溃" in r.reason
        assert r.defect_type == ""

        # 反向保护：一旦观测到 SUT 侧证据（>=400），原生崩溃字样不得把真缺陷一并吞掉
        r2 = DefectDecisionService.decide(
            test_status="failed", error_message=stored,
            response_status_code=500, expected_status_code=200,
        )
        assert r2.decision == DefectDecision.CREATE
        assert r2.error_type == "status_mismatch"

        # 边界：只认"首行即原生栈帧"，正文里**提及** Stacktrace 字样不触发本规则
        r3 = DefectDecisionService.decide(
            test_status="failed",
            error_message="Error: 工单状态应为「已关闭」，实际为「处理中」（详见 Stacktrace: 附件）",
            response_status_code=None, expected_status_code=None,
        )
        assert r3.decision == DefectDecision.CREATE, r3.reason

    def test_other_non_sut_classes_are_skipped(self):
        """其余非 SUT 类失败（依据平台历史缺陷单审计）：同样不得开成 SUT 缺陷单。"""
        cases = {
            "Error: 未找到或无法点击：点击运营菜单": "自动化导航助手失败",
            "[script_not_registered] 脚本来源授权拒绝（三要素绑定不成立）": "平台前置条件未满足",
            "[sub_function_unbound] Web 测试未绑定子功能": "平台前置条件未满足",
            "Error: 企业控制台登录失败（验证码 OCR 重试 10 次后仍未离开登录页）": "自动化登录助手失败",
            "Error: page.goto: net::ERR_CONNECTION_REFUSED at http://127.0.0.1:59999/": "环境类失败",
            "Error: No tests found.": "脚本采集失败",
            "Error Context: test-results-playwright-report-1790754074323-74dde0e5/…": "采集到的只是失败现场文件",
            "测试执行超时（超过 600 秒）": "环境/执行器类失败",
            "Error: 执行被中断（服务器重启或进程终止）": "平台侧执行中断",
            "Web 测试已跳过：缺少运行时登录凭据或脚本前置条件不满足。": "平台前置条件未满足",
            "Error: 编译断言执行受阻：2 项": "平台侧断言执行受阻",
            "playwright-report-1787538331703\\run_x--B端储值免单活动冒烟---TC-186482-chromium\\test-failed-1.png":
                "采集到的只是失败现场文件",
            "Error: 无法打开表单（flow=detail_timeline，定位器可能失效）": "自动化定位器/模板配置问题",
            "无法打开表单：定位器未配置表单入口（form_button 为空）": "自动化定位器/模板配置问题",
            "执行已被取消": "平台侧执行被取消",
            # 单号 jf-20260122392-01-2964：登录助手文案，此前会被开成「功能缺陷」
            "Error: 登录未完成（3 次尝试后仍停留在登录页：账号密码未通过或被页面清空）":
                "自动化登录助手失败",
        }
        for msg, label in cases.items():
            r = DefectDecisionService.decide(
                test_status="failed", error_message=msg,
                response_status_code=None, expected_status_code=None,
            )
            assert r.decision == DefectDecision.SKIP, msg
            assert label in r.reason, f"{msg} → {r.reason}"

    def test_login_failure_with_sut_evidence_is_still_filed(self):
        """反向保护：登录失败若**带 SUT 侧证据**（如登录接口 5xx），仍要开单。"""
        r = DefectDecisionService.decide(
            test_status="failed",
            error_message="Error: 登录未完成（3 次尝试后仍停留在登录页），登录接口返回 502",
            response_status_code=502,
            expected_status_code=200,
        )
        assert r.decision == DefectDecision.CREATE
        assert r.error_type == "status_mismatch"

    def test_normal_status_code_is_not_sut_evidence(self):
        """HTTP 200 **不算** SUT 侧证据：否则这两类自动化失败会被重新开单。

        实测（2026-10-08）：旧口径把「有没有状态码」当证据，只要链路上传进一个 200，
        「登录未完成」「定位器超时」就从 SKIP 变 CREATE。现口径下只有 **>=400** 才算证据。

        ⚠️ 适用范围更正（2026-10-08 二次复核，勿再引用错因果）：
        该门控**只对会传入真实状态码的链路生效**（API 侧 `api_test_executor` /
        `DefectRegistrationService` 主入口）。**Web 侧不经过它** ——
        `WebTestService.register_failure_defect()` 固定传 `response_summary={}`
        （该行为自 2026-09-29 起即在位，早于本次两条单），且从不传 `assertion_results`，
        故 web 失败恒为「无 SUT 证据」，判定完全由 `NON_SUT_FAILURE_RULES` 的文本规则决定。
        因此 jf-20260122392-01-2963 / 2964 的成因是**当时规则表缺对应模式**
        （2964 建单 09:08:32，规则文件 09:26 才更新），与本门控无关。
        """
        for msg, label in (
            ("Error: 登录未完成（3 次尝试后仍停留在登录页：账号密码未通过或被页面清空）",
             "自动化登录助手失败"),
            ("TimeoutError: locator.waitFor: Timeout 15000ms exceeded.",
             "自动化交互层失败"),
        ):
            r = DefectDecisionService.decide(
                test_status="failed", error_message=msg,
                response_status_code=200, expected_status_code=200,
            )
            assert r.decision == DefectDecision.SKIP, f"{msg}（status=200）→ {r.decision}"
            assert label in r.reason, r.reason

    def test_client_error_status_is_still_sut_evidence(self):
        """反向保护：4xx 仍属 SUT 侧证据，不得被非 SUT 规则吞掉。"""
        r = DefectDecisionService.decide(
            test_status="failed",
            error_message="TimeoutError: locator.waitFor: Timeout 15000ms exceeded.",
            response_status_code=401, expected_status_code=200,
        )
        assert r.decision == DefectDecision.CREATE
        assert r.error_type == "status_mismatch"

    def test_environment_failure_with_sut_evidence_is_still_filed(self):
        """反向保护：**已观测到 SUT 侧证据**时，环境类字样不得把真缺陷一并吞掉。"""
        # a) 观测到状态码不匹配
        r1 = DefectDecisionService.decide(
            test_status="failed",
            error_message="Error: locator.count: Target page, context or browser has been closed",
            response_status_code=502, expected_status_code=200,
        )
        assert r1.decision == DefectDecision.CREATE
        assert r1.error_type == "status_mismatch"
        # b) 有断言结果（断言失败本身即 SUT 侧证据）
        r2 = DefectDecisionService.decide(
            test_status="failed",
            error_message="Error: locator.fill: Test timeout of 30000ms exceeded.",
            response_status_code=None, expected_status_code=None,
            assertion_results=[{"passed": False, "message": "列表应显示新记录，实际未显示"}],
        )
        assert r2.decision == DefectDecision.CREATE
        assert r2.error_type == "assertion_failed"

    def test_real_ui_assertion_failure_is_still_filable(self):
        """反向保护：真实 UI 断言失败（元素应可见却不可见）不得被误跳过。"""
        r = DefectDecisionService.decide(
            test_status="failed",
            error_message="Error: expect(locator).toBeVisible() failed\nLocator: getByRole('button', name='保存')",
            response_status_code=None,
            expected_status_code=None,
        )
        assert r.decision == DefectDecision.CREATE

    def test_sut_side_failure_is_still_filable(self):
        """反向保护：带 SUT 侧证据的失败不得被误跳过（如报文/数据不符）。"""
        r = DefectDecisionService.decide(
            test_status="failed",
            error_message="modify 缺少校验：week_days=8,9 被接受且原记录已被修改",
            response_status_code=200,
            expected_status_code=200,
        )
        assert r.decision == DefectDecision.CREATE
        assert r.defect_type == "数据校验缺失"


class TestPlatformAndNetworkFailuresAreNotDefects:
    """平台自身故障 / 请求未到达被测系统 —— 都不是被测系统（SUT）缺陷。

    口径（2026-10-27 用户确认）：**只有被测系统的缺陷才登记 IDP**；
    平台（AI 测试框架自身）与环境类问题不登记，直接修。

    实测背景：把 11 类失败样本喂给 `decide()`，其中以下两类原本被判 `create`
    （会把平台/环境问题当 SUT 缺陷）：
      - `apiRequestContext.get: connect ECONNREFUSED 127.0.0.1:1`（连接层错误，
        API 测试不产生 `net::ERR_` 文案，原规则表未命中）
      - `Cannot find module '@playwright/test'` / `Package subpath './allure-playwright'`
        / `NoSuchKey ... script`（平台依赖与脚本资产缺失）
    """

    CONNECTION_LAYER = [
        "apiRequestContext.get: connect ECONNREFUSED 127.0.0.1:1",
        "apiRequestContext.post: read ECONNRESET",
        "connect ETIMEDOUT 10.0.0.8:443",
        "getaddrinfo ENOTFOUND uat.example.invalid",
        "socket hang up",
    ]

    PLATFORM_RUNTIME = [
        "Error: Cannot find module '@playwright/test'",
        "Error [ERR_MODULE_NOT_FOUND]: Cannot find package 'allure-playwright'",
        "Error: Package subpath './allure-playwright' is not defined by \"exports\"",
        "NoSuchKey: The specified key does not exist. (script_path=api-tests/x.ts)",
    ]

    def test_connection_layer_failures_are_skipped(self):
        for msg in self.CONNECTION_LAYER:
            r = DefectDecisionService.decide(
                test_status="failed",
                error_message=msg,
                response_status_code=None,
                expected_status_code=None,
            )
            assert r.decision == DefectDecision.SKIP, msg
            assert "环境类失败" in r.reason, msg

    def test_platform_runtime_failures_are_skipped(self):
        for msg in self.PLATFORM_RUNTIME:
            r = DefectDecisionService.decide(
                test_status="failed",
                error_message=msg,
                response_status_code=None,
                expected_status_code=None,
            )
            assert r.decision == DefectDecision.SKIP, msg
            assert "平台运行时故障" in r.reason, msg

    def test_real_sut_failures_still_filed(self):
        """反向保护：被测系统的真实失败不得被新规则误伤。"""
        for msg, code in (
            ("expect(received).toBe(expected) // 500 !== 200", 500),
            ("Internal Server Error", 500),
            ("expect(received).toBe(expected) // Object.is equality", None),
        ):
            r = DefectDecisionService.decide(
                test_status="failed",
                error_message=msg,
                response_status_code=code,
                expected_status_code=None,
            )
            assert r.decision == DefectDecision.CREATE, msg
