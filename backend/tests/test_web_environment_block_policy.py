# -*- coding: utf-8 -*-
"""环境阻断（WAF/网关）判定回归测试。

背景（2026-09-24 实测）：CS 的 UAT 域名被 WAF 拦截返回 **403**，登录页不渲染，
脚本只能报 `locator.waitFor: Timeout` —— 会被当成产品缺陷：
既触发无意义的重试，又**登记 IDP 缺陷单**。本组测试钉住新口径：

1. WAF/网关信号 → 判定为环境阻断（`classify_environment_block`）；
2. 环境阻断 **不重试**（IP 拦截重试无用，连续自动登录还可能加重拦截）；
3. 环境阻断 **不建缺陷单** —— 由**两道闸**共同保证：闸门一即本模块的
   `classify_environment_block`（执行器命中即短路），闸门二是判定层的
   `DefectDecisionService.decide`；两者的组合结论由
   `test_defect_registration_decision.py` 专门钉住；
4. 普通超时/网络抖动仍按原来的"可重试"处理（不回归）。
"""

from __future__ import annotations

from app.services.defect_decision_service import DefectDecisionService
from app.services.web_failure_policy import (
    classify_environment_block,
    is_retryable_web_failure,
    should_retry_web_attempt,
)

#: 实测的真实失败文本（TR-84 那次 WAF 拦截）
REAL_WAF_FAILURE = (
    "TimeoutError: locator.waitFor: Timeout 30000ms exceeded.\n"
    "Call log:\n"
    "  - waiting for locator('#login_username') to be visible\n"
)

WAF_HEADERS = (
    "HTTP/1.1 403 Forbidden\n"
    "Server: ESA\n"
    "Set-Cookie: acw_tc=9b66d89e17902264950924707ef56838df133b66ca8b7d15c53e4246d0\n"
    "X-Site-Cache-Status: HIT\n"
)


class TestEnvironmentBlockDetection:
    def test_script_reported_marker(self):
        blocked, reason = classify_environment_block(
            "ENVIRONMENT_BLOCKED {\"reason\": \"login_http_status\", \"status\": 403}"
        )
        assert blocked is True
        assert reason == "script_reported"

    def test_login_page_not_rendered_is_environment_block(self):
        """WAF 拦截的典型表现：登录框等不到（网络其实通）→ 必须判为环境阻断。"""
        blocked, reason = classify_environment_block(REAL_WAF_FAILURE)
        assert blocked is True
        assert reason == "login_page_not_rendered"

    def test_waf_headers_are_environment_block(self):
        blocked, reason = classify_environment_block("403 Forbidden", WAF_HEADERS)
        assert blocked is True
        assert reason in {"waf_forbidden", "waf_cookie", "waf_esa"}

    def test_esa_rule_id_is_environment_block(self):
        blocked, reason = classify_environment_block(
            "blocked by ESA RuleID 23009130 (http_custom)"
        )
        assert blocked is True
        assert reason == "waf_esa"

    def test_gateway_5xx_is_environment_block(self):
        """网关 5xx 阻断**只认网关私有特征**（2026-10-08 收紧后）。

        样本刻意**不含** ``Service Unavailable``：旧样本里三种特征混在一句，
        即使把 ``orig response 5xx`` / ``X-Swift-Error`` 全删掉，
        裸 reason phrase 仍会让它通过 —— 那是"因错误理由而绿"。
        """
        for text in (
            "503 X-Swift-Error: upstream connect error",
            "503 orig response 5xx",
        ):
            blocked, reason = classify_environment_block(text)
            assert blocked is True, text
            assert reason == "gateway_5xx", text

    def test_plain_sut_503_is_not_environment_block(self):
        """⚠️ 反向保护：**真实 SUT 503 不得被判环境阻断**（2026-10-08 收紧）。

        ``Service Unavailable`` 是标准 HTTP reason phrase，与网关 503 在文本上
        **不可区分**。旧口径把它当网关特征 → 真实后端 503 被判"环境阻断" →
        web/API 两侧都不登记 ⇒ **漏报真缺陷**（与"把缺陷写成没问题"同族）。
        口径取"宁可漏拦、不吞真缺陷"。
        """
        for text in (
            "503 Service Unavailable",
            "HTTP/1.1 503 Service Unavailable",
            "Received: 503 Service Unavailable",
            "Error: 退出登录接口异常 HTTP 503 Service Unavailable",
        ):
            blocked, reason = classify_environment_block(text)
            assert blocked is False, f"{text!r} 被误判为环境阻断（reason={reason}）"

    def test_sut_503_adjacent_forms_are_still_not_blocked(self):
        """邻近形态边界（收紧前后都不命中，防止正则被"顺手放大"）。"""
        for text in (
            "503 Service Temporarily Unavailable",
            "Service Unavailable",  # 无 503
            "503\nService Unavailable",  # `.` 不跨行
        ):
            blocked, _ = classify_environment_block(text)
            assert blocked is False, text

    def test_plain_assertion_failure_is_not_environment_block(self):
        blocked, reason = classify_environment_block(
            "Error: expect(received).toBeTruthy()\n未找到工单列表子菜单"
        )
        assert blocked is False and reason == ""

    def test_empty_message(self):
        assert classify_environment_block(None, None) == (False, "")


class TestRetryAndDefectPolicy:
    def test_environment_block_is_not_retried(self):
        assert is_retryable_web_failure(REAL_WAF_FAILURE) is False
        assert is_retryable_web_failure(
            "ENVIRONMENT_BLOCKED {\"reason\": \"login_http_status\", \"status\": 403}"
        ) is False

    def test_environment_block_is_gate_one_and_blocks_registration(self):
        """闸门一：环境阻断命中即**不登记**（执行器据此短路，不再进判定层）。

        注意这里断言的是 `classify_environment_block` —— 它才是活链路上的登记闸。
        2026-10-08 前这里断言的是一个 `should_register_defect()` 包装函数，
        其语义只是本函数的取反，却以"是否登记"命名，易被误当作登记决策入口，
        已删除（登记口径统一到 `DefectDecisionService.decide`）。
        """
        for msg, out in (
            (REAL_WAF_FAILURE, None),
            ('ENVIRONMENT_BLOCKED {"reason": "login_http_status", "status": 403}', ""),
            ("403 Forbidden", WAF_HEADERS),
        ):
            blocked, _ = classify_environment_block(msg, out)
            assert blocked is True, msg

    def test_plain_timeout_retryable_but_no_longer_registered(self):
        """口径澄清：普通定位器超时**仍会重试**，但**不再建缺陷单**。

        2026-10-08 起，"定位器未出现/不可见"归入自动化交互层失败（无 SUT 侧证据），
        由判定层 `NON_SUT_FAILURE_RULES` 跳过 —— 这正是修掉 2963 那类误报单的口径。
        （旧断言曾用 `should_register_defect(plain_timeout) is True` 表示"仍建单"，
        那是只看闸门一、不看判定层得出的结论，已随该函数一并废止。）
        """
        plain_timeout = (
            "TimeoutError: locator.click: Timeout 30000ms exceeded.\n"
            "waiting for getByRole('button', { name: 'Create' })"
        )
        assert is_retryable_web_failure(plain_timeout) is True
        decision = DefectDecisionService.decide(
            test_status="failed",
            error_message=plain_timeout,
            response_status_code=None,
            expected_status_code=None,
        )
        assert decision.decision.value == "skip"

    def test_locator_failure_is_not_retryable(self):
        """定位器硬失败（我方脚本问题）仍不可重试。"""
        assert is_retryable_web_failure("无法打开企业充值/退款表单（定位器可能失效）") is False


class TestLoginFlakePolicy:
    """登录偶发失败（单号 jf-20260122392-01-2964）应可重试。

    实测（2026-10-07）：TR-181 报「登录未完成（3 次尝试后仍停留在登录页…）」→ FAILED；
    紧接着 TR-182 / TR-183 用**同一账号**连续通过（25.7s / 21.5s）⇒ 属偶发，重试有意义。
    修复前该文案不在任何可重试模式里 → 被判为结构性失败、**一次都不重试**。
    """

    LOGIN_INCOMPLETE = "Error: 登录未完成（3 次尝试后仍停留在登录页：账号密码未通过或被页面清空）"

    def test_login_incomplete_is_retryable(self):
        assert is_retryable_web_failure(self.LOGIN_INCOMPLETE) is True

    def test_login_incomplete_retry_is_bounded_and_traced(self):
        """重试必须**有界**且留痕（不得伪装成一次通过）。"""
        retry, reason = should_retry_web_attempt(
            {"status": "failed", "error_message": self.LOGIN_INCOMPLETE}, attempt=1, max_attempts=2,
        )
        assert retry is True and "第" in reason
        # 达到上限即停止
        retry2, _ = should_retry_web_attempt(
            {"status": "failed", "error_message": self.LOGIN_INCOMPLETE}, attempt=2, max_attempts=2,
        )
        assert retry2 is False

    def test_missing_credentials_still_not_retryable(self):
        """反向保护：**真凭据缺失/配置错误**不得靠重试掩盖。"""
        for msg in (
            "Web 测试已跳过：缺少运行时登录凭据或脚本前置条件不满足。",
            "WEB_TEST_USERNAME 未配置",
            "placeholder_base_url 未配置",
        ):
            assert is_retryable_web_failure(msg) is False, msg
