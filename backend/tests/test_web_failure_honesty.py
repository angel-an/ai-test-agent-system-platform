# -*- coding: utf-8 -*-
"""Web 失败判定的"诚实性"回归测试。

覆盖三类此前会误导人的行为：
1. 失败 run 的"跳过"判定不能覆盖真实错误（浏览器/页面被关闭、定位器超时）；
2. 缺陷标题不能凭空造出"状态码不匹配：预期 200，实际 500"；
3. 通过 run 的 stdout 必须保留（编译断言的逐条证据只存在于这里）。
"""

from __future__ import annotations

from types import SimpleNamespace

from app.services.defect_decision_service import DefectDecision, DefectDecisionService
from app.services.defect_registration_service import DefectRegistrationService
from app.services.execution.executors import summarize_web_run_result
from app.services.web_failure_policy import (
    flake_retry_budget,
    flake_retry_note,
    is_retryable_web_failure,
    run_attempts_with_policy,
    should_retry_web_attempt,
)

BROWSER_CLOSED = "Error: locator.count: Target page, context or browser has been closed"


class TestDecisionKeepsRealError:
    """缺陷判定必须回落到真实错误信息。

    口径变更（2026-10-07，业务方确认）：`Target page/browser has been closed`、
    `Test timeout of Nms exceeded` 这类**环境/执行器类**失败不再自动开单
    （此前判 CREATE，标题只能是技术报错串）。本类测试的**原立意不变**：
    判定必须回落到真实错误、不得伪造成"状态码不匹配"。
    """

    def test_missing_status_code_is_not_reported_as_status_mismatch(self):
        """没有观测到状态码时，不得判成 status_mismatch（现改为不自动开单）。"""
        result = DefectDecisionService.decide(
            test_status="failed",
            error_message=BROWSER_CLOSED,
            response_status_code=None,
            expected_status_code=None,
        )
        assert result.decision == DefectDecision.SKIP
        assert result.error_type == "skip"
        assert result.error_type != "status_mismatch"
        assert "状态码不匹配" not in result.failure_summary
        # 诚实性：跳过理由里仍保留原始错误首行
        assert "browser has been closed" in result.failure_summary
        assert "环境/执行器类失败" in result.reason

    def test_environment_failure_with_observed_status_is_still_filed(self):
        """反向保护：环境类错误字样 + **已观测到状态码不匹配** ⇒ 不得被跳过（真缺陷仍要开单）。"""
        result = DefectDecisionService.decide(
            test_status="failed",
            error_message=BROWSER_CLOSED,
            response_status_code=502,
            expected_status_code=200,
        )
        assert result.decision == DefectDecision.CREATE
        assert result.error_type == "status_mismatch"

    def test_real_status_mismatch_still_detected(self):
        """真观测到状态码不一致时，仍要能识别出来（不是把检查关掉）。"""
        result = DefectDecisionService.decide(
            test_status="failed",
            error_message="服务器返回错误",
            response_status_code=500,
            expected_status_code=200,
        )
        assert result.error_type == "status_mismatch"


class TestWebRegistrationDoesNotFabricateStatus:
    """register_from_web_failure 不得伪造 HTTP 状态码。"""

    def _service(self) -> tuple[DefectRegistrationService, list]:
        captured: list = []

        class _FakeIDP:
            async def process_test_failure(self, **kwargs):
                captured.append(kwargs)
                return SimpleNamespace(identifier="IDF-TEST")

        svc = DefectRegistrationService.__new__(DefectRegistrationService)
        svc.session = None
        svc.idp_service = _FakeIDP()
        return svc, captured

    async def _register(self, response_summary):
        import uuid

        svc, captured = self._service()
        await svc.register_from_web_failure(
            test_run_id=uuid.uuid4(),
            test_case_id=None,
            source_project_key="PR-2",
            scenario_name="储值充值",
            page_url="https://example.invalid/#/login",
            action="EXECUTE",
            request_summary={"url": "https://example.invalid/#/login"},
            response_summary=response_summary,
            error_message=BROWSER_CLOSED,
        )
        return captured

    def test_empty_response_summary_keeps_real_error(self):
        """环境类失败不再登记为缺陷；但"不得伪造状态码/状态不匹配"的保证必须仍在。"""
        import asyncio

        captured = asyncio.run(self._register({}))
        # 口径变更（2026-10-07）：环境/执行器类失败不自动开单 → 不提交 IDP
        assert captured == [], "环境/执行器类失败不应被登记为缺陷"

        # 诚实性保证：判定回落到真实错误，且不得凭空造状态不匹配
        decision = DefectDecisionService.decide(
            test_status="failed",
            error_message=BROWSER_CLOSED,
            response_status_code=None,
            expected_status_code=None,
        )
        assert decision.error_type != "status_mismatch"
        assert "状态码不匹配" not in decision.failure_summary
        assert "browser has been closed" in decision.failure_summary

    def test_observed_status_mismatch_is_kept(self):
        import asyncio

        captured = asyncio.run(self._register({"status_code": 502}))
        decision = captured[0]["decision"]
        assert decision.error_type == "status_mismatch"
        assert "502" in decision.failure_summary


class TestWebRunClassification:
    """WebTestService 返回值 → 作业判定。"""

    def test_error_is_not_rewritten_as_skipped(self):
        """T=1/P=0/F=0/S=1 但带错误（浏览器被关闭）必须判失败并保留真实原因。"""
        mapped = summarize_web_run_result({
            "status": "failed",
            "total_tests": 1, "passed_tests": 0, "failed_tests": 0, "skipped_tests": 1,
            "error_message": BROWSER_CLOSED,
        })
        assert mapped["status"] == "failed"
        assert mapped["success"] is False
        assert mapped["error_message"] == BROWSER_CLOSED
        assert "缺少运行时登录凭据" not in (mapped["error_message"] or "")

    def test_all_skipped_without_error_still_reports_skip_hint(self):
        """确实没有任何错误时，保留原有"跳过"解释（凭据缺失场景）。"""
        mapped = summarize_web_run_result({
            "status": "completed",
            "total_tests": 2, "passed_tests": 0, "failed_tests": 0, "skipped_tests": 2,
        })
        assert mapped["status"] == "skipped"
        assert "缺少运行时登录凭据" in mapped["error_message"]

    def test_passed_run_keeps_stdout_evidence(self):
        """通过运行必须保留 stdout，否则断言证据无法复核。"""
        mapped = summarize_web_run_result({
            "status": "completed",
            "total_tests": 1, "passed_tests": 1, "failed_tests": 0, "skipped_tests": 0,
            "stdout": 'COMPILED_ASSERTION {"name":"x","outcome":"pass"}',
        })
        assert mapped["status"] == "completed"
        assert mapped["error_message"] is None
        assert "COMPILED_ASSERTION" in (mapped["stdout"] or "")


class TestFlakeRetryPolicy:
    """有界重试：只重试执行环境类失败，且不留痕不罢休。"""

    def test_environment_failures_are_retryable(self):
        for err in (
            "Error: locator.count: Target page, context or browser has been closed",
            "Error: locator.fill: Test timeout of 30000ms exceeded.",
            "page.goto: net::ERR_CONNECTION_TIMED_OUT",
        ):
            assert is_retryable_web_failure(err) is True, err

    def test_structural_failures_are_not_retryable(self):
        for err in (
            "无法打开企业充值/退款表单（定位器可能失效）",
            "Error: expect(received).toBeTruthy()\n编译断言执行受阻：2 项",
            "SyntaxError: Unexpected token",
            "Web 测试已跳过：缺少运行时登录凭据或脚本前置条件不满足。",
            None,
            "",  # 未知/空错误一律不重试，避免消耗资源
            "一些没见过的错误",
        ):
            assert is_retryable_web_failure(err) is False, err

    def test_budget_is_bounded_and_configurable(self, monkeypatch):
        monkeypatch.delenv("WEB_TEST_FLAKE_RETRY", raising=False)
        assert flake_retry_budget(None) == 1          # 默认 1 次
        assert flake_retry_budget({}) == 1
        assert flake_retry_budget({"flake_retry": 0}) == 0
        assert flake_retry_budget({"flake_retry": 5}) == 2   # 上限 2，避免无限消耗
        assert flake_retry_budget({"flake_retry": "bad"}) == 1
        monkeypatch.setenv("WEB_TEST_FLAKE_RETRY", "2")
        assert flake_retry_budget(None) == 2

    def test_job_level_retry_disables_inner_flake_retry(self, monkeypatch):
        """作业层重试已介入时，执行器内不再自动重试（一次失败只被一套机制计数）。"""
        monkeypatch.delenv("WEB_TEST_FLAKE_RETRY", raising=False)
        assert flake_retry_budget({"job_retry_count": 1}) == 0
        assert flake_retry_budget({"job_retry_count": 2, "flake_retry": 2}) == 0
        # 首次执行（job_retry_count=0）仍保留自动 flake 重试
        assert flake_retry_budget({"job_retry_count": 0}) == 1

    def test_job_retry_means_single_attempt(self, monkeypatch):
        """协同后的总尝试次数：首次 1+flake，作业层重试每次 1 次。"""
        import asyncio

        monkeypatch.delenv("WEB_TEST_FLAKE_RETRY", raising=False)
        flake = {"status": "failed",
                 "error_message": "Target page, context or browser has been closed"}
        calls: list[int] = []

        async def run_once():
            calls.append(1)
            return flake

        first = asyncio.run(run_attempts_with_policy(
            run_once, max_attempts=1 + flake_retry_budget({"job_retry_count": 0})))
        assert first["attempts"] == 2          # 首次：1 + 自动重试 1

        retried = asyncio.run(run_attempts_with_policy(
            run_once, max_attempts=1 + flake_retry_budget({"job_retry_count": 1})))
        assert retried["attempts"] == 1        # 作业层重试：只 1 次，不叠加

    def test_should_retry_only_when_failed_and_budget_left(self):
        flake = {"status": "failed", "error_message": "Target page, context or browser has been closed"}
        structural = {"status": "failed", "error_message": "无法打开企业充值/退款表单（定位器可能失效）"}

        assert should_retry_web_attempt(flake, 1, 2)[0] is True
        # 预算用尽不得再试
        assert should_retry_web_attempt(flake, 2, 2)[0] is False
        # 结构性失败即使有预算也不重试
        assert should_retry_web_attempt(structural, 1, 2)[0] is False
        # 未失败不重试
        assert should_retry_web_attempt({"status": "completed"}, 1, 2)[0] is False

    def test_retry_leaves_a_trace(self):
        """重试成功也不能伪装成一次通过：必须留下 FLAKE_RETRY 痕迹。"""
        note = flake_retry_note(2, "Target page, context or browser has been closed")
        assert note.startswith("FLAKE_RETRY ")
        assert '"attempts": 2' in note
        assert "browser has been closed" in note


class TestAttemptLoop:
    """重试循环的边界：次数、留痕、以及"最终失败必须可被登记"。"""

    FLAKE = "Error: locator.count: Target page, context or browser has been closed"
    STRUCTURAL = "无法打开企业充值/退款表单（定位器可能失效）"
    OK = None

    def _runner(self, results: list[dict]):
        calls: list[int] = []

        async def run_once():
            idx = min(len(calls), len(results) - 1)
            calls.append(idx)
            return results[idx]

        return run_once, calls

    def test_flake_then_success_stops_after_second_attempt(self):
        import asyncio

        run_once, calls = self._runner([
            {"status": "failed", "error_message": self.FLAKE},
            {"status": "completed", "passed_tests": 1, "total_tests": 1},
        ])
        out = asyncio.run(run_attempts_with_policy(run_once, max_attempts=2))
        assert out["attempts"] == 2
        assert out["result"]["status"] == "completed"
        assert "browser has been closed" in out["first_error"]
        assert len(calls) == 2

    def test_structural_failure_does_not_retry(self):
        import asyncio

        run_once, calls = self._runner([{"status": "failed", "error_message": self.STRUCTURAL}])
        out = asyncio.run(run_attempts_with_policy(run_once, max_attempts=2))
        assert out["attempts"] == 1
        assert len(calls) == 1
        # 关键：结构性失败虽不重试，但结果仍是 failed → 调用方必须登记缺陷
        assert out["result"]["status"] == "failed"

    def test_flake_failure_uses_full_budget_then_stops(self):
        import asyncio

        run_once, calls = self._runner([{"status": "failed", "error_message": self.FLAKE}])
        out = asyncio.run(run_attempts_with_policy(run_once, max_attempts=3))
        assert out["attempts"] == 3           # 用满预算
        assert len(calls) == 3
        assert out["result"]["status"] == "failed"

    def test_zero_budget_means_single_attempt(self):
        import asyncio

        run_once, calls = self._runner([{"status": "failed", "error_message": self.FLAKE}])
        out = asyncio.run(run_attempts_with_policy(run_once, max_attempts=1))
        assert out["attempts"] == 1
        assert len(calls) == 1

    def test_cancellation_stops_retrying(self):
        import asyncio

        run_once, calls = self._runner([{"status": "failed", "error_message": self.FLAKE}])
        out = asyncio.run(run_attempts_with_policy(
            run_once, max_attempts=3, should_stop=lambda: True))
        assert out["attempts"] == 1
        assert len(calls) == 1


class TestWebRunLogIsSelfEvidence:
    """执行日志必须能自证。

    真实缺陷（TR-63 复核时发现）：Web 路径把 stdout 截断到 5000 字符，实测正好卡在 5000
    字符处，末行 ``COMPILED_ASSERTION_SUMMARY`` 被切掉——于是出现"落库 31 条断言、
    日志只解析出 29 条"的无法自证状态。
    """

    def test_log_cap_is_large_enough(self):
        from app.services import web_test_service

        assert web_test_service._MAX_LOG_CHARS >= 20000

    def test_no_hardcoded_small_stdout_cap_remains(self):
        import inspect

        from app.services import web_test_service

        src = inspect.getsource(web_test_service)
        assert "stdout[:5000]" not in src
        assert "stderr[:2000]" not in src
