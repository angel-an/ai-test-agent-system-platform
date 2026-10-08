# -*- coding: utf-8 -*-
"""缺陷登记决策的**单一口径**回归测试。

背景（2026-10-08）
------------------
"这次失败该不该登记缺陷单"这个问题，历史上在本仓库有两处实现：

* `web_failure_policy.should_register_defect()`（策略层）—— 语义其实只是
  "非环境阻断"（`classify_environment_block` 的取反），命名却承诺回答"是否登记"；
* `defect_decision_service.DefectDecisionService.decide()`（判定层）—— 真正的登记决策，
  带"非 SUT 失败规则表"与 SUT 侧证据门控。

用 12 个真实失败样本同时喂给两侧，**11 个结论相反**（策略层说"要登记"、判定层说"跳过"）。
真正的活链路是**两道闸的组合**（见 `executors.py` WebTestExecutor）：

    blocked, reason = classify_environment_block(error, stdout)   # 闸门一
    if failed and not blocked:  → register_failure_defect() → decide()   # 闸门二
    elif failed and blocked:    → 不登记

所以问题不是"两个口径都活着且打架"，而是**策略层那个函数已经死了、命名却仍在许诺
登记决策**：谁把它接进链路，就会以为登记决策只有一处，从而删掉闸门二。

本文件钉住三件事：

1. 策略层不得再导出任何"登记决策"符号（防第二口径复活）；
2. 登记决策的唯一权威是 `DefectDecisionService.decide()`，两条登记链路都必经它；
3. 闸门组合后的**最终登记结论**对一批真实样本逐条正确（含 2963 / 2964 文案必不登记、
   真业务断言必登记、有异常状态码时反向仍开单）。
"""

from __future__ import annotations

import inspect
import io
import tokenize

import pytest

from app.services.defect_decision_service import DefectDecisionService
from app.services.defect_registration_service import DefectRegistrationService
from app.services import web_failure_policy
from app.services.web_failure_policy import classify_environment_block


# ---------------------------------------------------------------------------
# 1) 单一来源
# ---------------------------------------------------------------------------
class TestRegistrationDecisionHasSingleSource:
    def test_policy_layer_no_longer_exposes_a_registration_decision(self):
        """策略层只答"可重试性"和"环境阻断"，不答"是否登记"。"""
        assert not hasattr(web_failure_policy, "should_register_defect"), (
            "web_failure_policy 不应再提供登记决策函数：登记口径统一在 "
            "DefectDecisionService.decide()，此处新增会造成第二份口径"
        )
        assert "should_register_defect" not in getattr(web_failure_policy, "__all__", [])

    @pytest.mark.parametrize(
        "method_name",
        ["register_from_web_failure", "register_from_api_failure"],
    )
    def test_registration_paths_go_through_the_decision_layer(self, method_name):
        """两条登记链路（Web / API）都必须经由判定层，不得自行判定。"""
        source = inspect.getsource(getattr(DefectRegistrationService, method_name))
        assert "DefectDecisionService.decide(" in source, (
            f"{method_name} 必须调用 DefectDecisionService.decide()；"
            "否则等于绕开唯一的登记口径"
        )

    def test_environment_block_is_gate_one_not_a_registration_decision(self):
        """闸门一（环境阻断）只负责短路，不构成第二份登记规则。"""
        # 阻断信号 → 不登记（由执行器短路保证）
        assert classify_environment_block("ENVIRONMENT_BLOCKED {}")[0] is True
        # 但"不是阻断"并不等于"要登记"——后者只有判定层说了算
        assert classify_environment_block("登录未完成")[0] is False
        assert DefectDecisionService.decide(
            test_status="failed",
            error_message="登录未完成（3 次尝试后仍停留在登录页）",
            response_status_code=None,
            expected_status_code=None,
        ).decision.value == "skip"


# ---------------------------------------------------------------------------
# 2) 闸门组合后的最终结论
# ---------------------------------------------------------------------------
def _live_registration_outcome(error_message: str, stdout: str | None = None) -> str:
    """镜像 `executors.py` WebTestExecutor 的登记判断（闸门一 + 闸门二）。

    与执行器里的 ``if result.status == "failed" and not blocked:`` 等价：
    环境阻断短路 → 不登记；否则交给判定层。

    注：Web 路径的 `response_summary` 恒为 `{}`（不伪造 HTTP 状态码，见
    `web_test_service.py`），故状态码/断言结果恒为 None —— 这正是 web 侧
    登记结论完全由文本规则决定的原因。
    """
    blocked, _ = classify_environment_block(error_message, stdout)
    if blocked:
        return "blocked"  # 不登记
    decision = DefectDecisionService.decide(
        test_status="failed",
        error_message=error_message,
        response_status_code=None,
        expected_status_code=None,
    )
    return decision.decision.value  # "skip" | "create"


#: (样本, 期望的活链路结论)。"blocked"/"skip" = 不登记，"create" = 登记。
LIVE_OUTCOME_CASES = [
    # --- 2963 / 2964 的原始文案：这两条误报单就是被它们产出的，必须不登记 ---
    ("登录未完成（3 次尝试后仍停留在登录页：账号密码未通过或被页面清空）", "skip"),
    ("TimeoutError: locator.waitFor: Timeout 15000ms exceeded.", "skip"),
    # --- 自动化交互层 / 执行器类 ---
    ("Target page, context or browser has been closed", "skip"),
    ("Execution context was destroyed", "skip"),
    # --- 平台前置条件 / 脚本自身问题 ---
    ("[script_not_registered] 脚本来源授权拒绝（三要素绑定不成立）", "skip"),
    ("缺少运行时登录凭据", "skip"),
    ("SyncError: 脚本语法 SyntaxError: Unexpected token", "skip"),
    ("Error: 未找到或无法点击：点击运营菜单", "skip"),
    # --- 环境阻断（闸门一命中）---
    (
        "TimeoutError: locator.waitFor: Timeout 30000ms exceeded.\n"
        "  - waiting for locator('#login_username') to be visible",
        "blocked",
    ),
    ('ENVIRONMENT_BLOCKED {"reason": "login_http_status", "status": 403}', "blocked"),
    # --- 反向保护：真业务断言失败仍然开单（不是"一律不登记"）---
    ("Error: expect(received).toBe(200)\n未找到工单列表子菜单", "create"),
]


class TestLiveRegistrationOutcome:
    @pytest.mark.parametrize(
        "error_message,expected",
        LIVE_OUTCOME_CASES,
        ids=[c[1] + "-" + str(i) for i, c in enumerate(LIVE_OUTCOME_CASES)],
    )
    def test_live_outcome(self, error_message, expected):
        assert _live_registration_outcome(error_message) == expected

    def test_sut_side_evidence_still_files_even_for_login_failure(self):
        """反向保护：登录未完成若同时观测到**异常**状态码（如登录接口 502），仍开单。

        这条保证"非 SUT 规则"不会把真缺陷一并吞掉 —— 也是"只登记确认为 SUT BUG 的"
        这一口径能够成立的前提。
        """
        decision = DefectDecisionService.decide(
            test_status="failed",
            error_message="登录未完成（3 次尝试后仍停留在登录页）",
            response_status_code=502,
            expected_status_code=200,
        )
        assert decision.decision.value == "create"

    def test_http_200_is_not_sut_evidence(self):
        """HTTP 200 不算证据：否则"页面能打开"会把非 SUT 失败重新开成缺陷单。"""
        decision = DefectDecisionService.decide(
            test_status="failed",
            error_message="登录未完成（3 次尝试后仍停留在登录页）",
            response_status_code=200,
            expected_status_code=200,
        )
        assert decision.decision.value == "skip"


# ---------------------------------------------------------------------------
# 3) API 链路：预检不得复活（2026-10-08 收敛）
# ---------------------------------------------------------------------------
class TestApiPathHasSingleDecisionPoint:
    """API 执行器曾内联一次「预检」`decide()`，与登记侧构成两套证据口径。

    预检以 `expected_status_code or 200` 兜底，登记侧坚持 None 纪律（未观测即 None）。
    同一次失败因此被判两次：预检产出「状态码不匹配：预期 200，实际 500」
    （`error_type=status_mismatch`），登记侧产出「服务器错误: HTTP 500」
    （`error_type=server_error`）—— 描述、error_type、指纹与去重键都可能不同。

    收敛后：**执行器只传证据标志 `allow_create`，唯一的 `decide()` 在登记服务里。**
    """

    def _src(self) -> str:
        """取执行器 `_process_idp_defect` 的**代码文本**（剥离注释）。

        必须剥离注释：方法内刻意保留了「此处曾内联调用 decide() / 曾用
        `or 200` 兜底」的说明性注释；若连注释一并做字面匹配会假阳性
        （与"把注释里的'信封'当成断言"是同一类误判）。

        实现用 tokenize 定位注释的 (行, 列) 后按列截断，**保留代码原文**
        （不用 token 拼接，否则 `f(` 会被拆成 `f (`）。
        """
        from app.services.api_test_executor import APITestExecutor

        source = inspect.getsource(APITestExecutor._process_idp_defect)
        lines = source.splitlines()
        for tok in tokenize.generate_tokens(io.StringIO(source).readline):
            if tok.type == tokenize.COMMENT:
                row, col = tok.start
                lines[row - 1] = lines[row - 1][:col]
        return "\n".join(lines)

    def test_executor_does_not_call_decide_inline(self):
        """执行器不得再内联判定：两处判定共用同一 status_code，skip 结论本就冗余。"""
        assert "DefectDecisionService.decide(" not in self._src(), (
            "API 执行器不应再调用 decide()：登记决策唯一权威在 "
            "DefectRegistrationService.register_from_api_failure()"
        )

    def test_executor_does_not_fabricate_expected_status(self):
        """`or 200` 兜底是猜的预期，会把伪造值写进工单，不得复活。"""
        assert "expected_status_code or 200" not in self._src()

    def test_executor_delegates_evidence_via_allow_create(self):
        """证据完整性应以 allow_create 表达，而不是在执行器里另做一次决策。"""
        src = self._src()
        assert "register_from_api_failure(" in src
        assert "allow_create=has_complete_evidence" in src


class TestExpectedStatusIsNotFabricated:
    def test_missing_expected_does_not_produce_fake_expectation(self):
        """expected 未观测到（None）时，不得写出「预期 200」。"""
        decision = DefectDecisionService.decide(
            test_status="failed",
            error_message="API 请求失败",
            response_status_code=500,
            expected_status_code=None,
        )
        assert decision.decision.value == "create"
        assert "预期 200" not in decision.failure_summary
        assert decision.failure_summary == "服务器错误: HTTP 500"
        assert decision.error_type == "server_error"

    def test_contrast_or_200_fallback_would_fabricate(self):
        """对照：旧预检的 `or 200` 兜底确实会伪造预期（证明删除预检有价值）。"""
        stale = DefectDecisionService.decide(
            test_status="failed",
            error_message="API 请求失败",
            response_status_code=500,
            expected_status_code=200,  # 旧预检的兜底值
        )
        assert stale.failure_summary == "状态码不匹配：预期 200，实际 500"
        assert stale.error_type == "status_mismatch"

    @pytest.mark.parametrize("expected", [None, 200, 500])
    def test_skip_verdict_is_independent_of_expected_status(self, expected):
        """skip 结论只取决于 SUT 侧证据（状态码/断言），与 expected 无关。

        这是「删除预检**不会**改变是否登记这一结论」的依据：
        预检与登记传入的 `response_status_code` 完全相同，
        而 skip 判定只看 `_has_sut_evidence`，不看 expected。
        """
        decision = DefectDecisionService.decide(
            test_status="failed",
            error_message="TimeoutError: locator.waitFor: Timeout 15000ms exceeded.",
            response_status_code=None,
            expected_status_code=expected,
        )
        assert decision.decision.value == "skip"
