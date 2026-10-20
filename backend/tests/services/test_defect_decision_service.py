"""
缺陷决策服务单元测试

测试缺陷判断规则、优先级规则
"""

import pytest

from app.services.defect_decision_service import (
    DefectDecisionService,
    DefectDecision,
    DefectPriority,
    extract_assertion_expectation,
    extract_failure_headline,
)


class TestDefectDecisionService:
    """缺陷决策服务测试"""

    def test_passed_test_no_defect(self):
        """测试通过的测试不创建缺陷"""
        result = DefectDecisionService.decide(
            test_status="passed",
            error_message=None,
            response_status_code=200,
            expected_status_code=200,
        )
        assert result.decision == DefectDecision.SKIP
        assert result.reason == "测试通过"

    def test_network_unreachable_skip(self):
        """测试网络不可达时跳过"""
        result = DefectDecisionService.decide(
            test_status="failed",
            error_message="Connection refused",
            response_status_code=None,
            expected_status_code=200,
        )
        assert result.decision == DefectDecision.SKIP
        assert "跳过" in result.reason

    def test_timeout_skip(self):
        """测试超时时跳过"""
        result = DefectDecisionService.decide(
            test_status="failed",
            error_message="连接超时",
            response_status_code=None,
            expected_status_code=200,
        )
        assert result.decision == DefectDecision.SKIP

    def test_auth_error_skip(self):
        """测试认证错误时跳过"""
        result = DefectDecisionService.decide(
            test_status="failed",
            error_message="认证配置错误",
            response_status_code=401,
            expected_status_code=200,
        )
        assert result.decision == DefectDecision.SKIP

    def test_too_many_retries_skip(self):
        """测试重试次数过多时跳过"""
        result = DefectDecisionService.decide(
            test_status="failed",
            error_message="Some error",
            response_status_code=500,
            expected_status_code=200,
            retry_count=3,
        )
        assert result.decision == DefectDecision.SKIP
        assert "重试" in result.reason

    def test_status_mismatch_create(self):
        """测试状态码不匹配时创建缺陷"""
        result = DefectDecisionService.decide(
            test_status="failed",
            error_message="Status mismatch",
            response_status_code=500,
            expected_status_code=200,
        )
        assert result.decision == DefectDecision.CREATE
        assert result.error_type == "status_mismatch"

    def test_server_error_medium_priority(self):
        """测试 5xx 错误为中优先级"""
        result = DefectDecisionService.decide(
            test_status="failed",
            error_message="Internal Server Error",
            response_status_code=500,
            expected_status_code=200,
        )
        assert result.decision == DefectDecision.CREATE
        assert result.priority == DefectPriority.MEDIUM

    def test_assertion_failed_medium_priority(self):
        """测试断言失败为中优先级"""
        result = DefectDecisionService.decide(
            test_status="failed",
            error_message="Assertion failed",
            response_status_code=200,
            expected_status_code=200,
            assertion_results=[{"passed": False, "message": "Expected true"}],
        )
        assert result.decision == DefectDecision.CREATE
        assert result.error_type == "assertion_failed"
        assert result.priority == DefectPriority.MEDIUM

    def test_data_loss_high_priority(self):
        """测试数据丢失为高优先级"""
        result = DefectDecisionService.decide(
            test_status="failed",
            error_message="数据丢失",
            response_status_code=500,
            expected_status_code=200,
        )
        assert result.decision == DefectDecision.CREATE
        assert result.priority == DefectPriority.HIGH

    def test_security_issue_high_priority(self):
        """测试安全问题为高优先级"""
        result = DefectDecisionService.decide(
            test_status="failed",
            error_message="发现安全漏洞",
            response_status_code=403,
            expected_status_code=200,
        )
        assert result.decision == DefectDecision.CREATE
        assert result.priority == DefectPriority.HIGH

    def test_4xx_client_error_medium_priority(self):
        """测试 4xx 错误为中优先级"""
        result = DefectDecisionService.decide(
            test_status="failed",
            error_message="Bad Request",
            response_status_code=400,
            expected_status_code=200,
        )
        assert result.decision == DefectDecision.CREATE
        assert result.priority == DefectPriority.MEDIUM

    def test_unknown_error_low_priority(self):
        """测试未知错误为低优先级"""
        result = DefectDecisionService.decide(
            test_status="failed",
            error_message="Something went wrong",
            response_status_code=200,
            expected_status_code=200,
        )
        assert result.decision == DefectDecision.CREATE
        assert result.priority == DefectPriority.LOW

    def test_not_a_defect_skip(self):
        """测试明确标记为非缺陷时跳过"""
        result = DefectDecisionService.decide(
            test_status="failed",
            error_message="This is not_a_defect",
            response_status_code=500,
            expected_status_code=200,
        )
        assert result.decision == DefectDecision.SKIP


class TestAssertionExpectationExtraction:
    """从 Playwright 失败消息反推期望/实际值。

    这是自动提单「预期结果」唯一正当的来源——旧实现恒写
    「HTTP 200 OK / 响应结构符合接口契约 / 业务逻辑正确」，
    对"业务失败也返 HTTP 200"的信封式被测系统，等于把缺陷写成"现状即预期"。
    """

    def test_parses_playwright_expected_received_block(self):
        """Playwright 标准的 Expected/Received 块（实测样本取自仓库内 error-context.md）。"""
        msg = (
            "Error: expect(received).toBeGreaterThanOrEqual(expected)\n\n"
            "Expected: >= 400\n"
            "Received:    200\n"
        )
        expected, observed, expr = extract_assertion_expectation(msg)
        assert expected == ">= 400"
        assert observed == "200"
        assert expr == "expect(...).toBeGreaterThanOrEqual()"

    def test_strips_ansi_escape(self):
        """Playwright 输出带 ANSI 颜色码，不得写进工单。"""
        msg = '\x1b[31mExpected: "0"\x1b[39m\n\x1b[31mReceived:    "401"\x1b[39m'
        expected, observed, _ = extract_assertion_expectation(msg)
        assert expected == '"0"'
        assert observed == '"401"'
        assert "\x1b" not in (expected + observed)

    def test_negated_matcher_and_null_observer(self):
        """`not.toBeNull()` 的 `Received: null` 不算实测值。"""
        msg = "Error: expect(received).not.toBeNull()\n\nExpected: not null\nReceived: null"
        expected, observed, expr = extract_assertion_expectation(msg)
        assert expected == "not null"
        assert observed is None
        assert expr == "expect(...).not.toBeNull()"

    def test_returns_none_when_no_assertion_block(self):
        """没有断言块时返回 None —— 调用方须如实写"待人工补充"，不得回落到模板套话。"""
        assert extract_assertion_expectation("测试执行超时") == (None, None, None)
        assert extract_assertion_expectation(None) == (None, None, None)
        assert extract_assertion_expectation("") == (None, None, None)

    def test_decide_carries_expectation(self):
        """decide() 必须把反推出的期望/实测值带在结果里（供提单描述使用）。"""
        result = DefectDecisionService.decide(
            test_status="failed",
            error_message='Error: expect(received).toBe(expected)\n\nExpected: "0"\nReceived:    "401"\n',
            response_status_code=200,
            expected_status_code=200,
        )
        assert result.decision == DefectDecision.CREATE
        assert result.expected_result == '"0"'
        assert result.observed_result == '"401"'


class TestPriorityForBusinessRuleDefects:
    """业务规则/数据一致性类缺陷不得再兜底成「低」。"""

    def test_missing_uniqueness_check_is_high(self):
        """实测回归（DEFECT-001 / jf-20260122392-01-2960）。

        modify 入口未做商品唯一性校验、受理了已被占用的商品：
        HTTP 200 + resultCode=0（业务拦截缺失，没有服务器错误特征），
        原来会被兜底判成「低」，与"已产生存量脏数据"的风险倒挂。
        """
        result = DefectDecisionService.decide(
            test_status="failed",
            error_message=(
                'Error: expect(received).toBe(expected)\n\nExpected: "100000"\nReceived:    "0"\n'
                "服务端受理了已被占用的商品，缺少唯一性校验"
            ),
            response_status_code=200,
            expected_status_code=200,
        )
        assert result.decision == DefectDecision.CREATE
        assert result.priority == DefectPriority.HIGH
        assert result.priority_id == 1
        assert result.priority_code == "priority-1"
        assert result.defect_type == "数据校验缺失"

    def test_csrf_and_sensitive_leak_are_high(self):
        """安全类补齐：CSRF / 敏感信息泄露原先会漏到「低」。"""
        for message in ("存在 CSRF 风险", "响应中泄露敏感信息"):
            result = DefectDecisionService.decide(
                test_status="failed",
                error_message=message,
                response_status_code=200,
                expected_status_code=200,
            )
            assert result.decision == DefectDecision.CREATE
            assert result.defect_type == "安全缺陷"
            assert result.priority == DefectPriority.HIGH, message

    def test_generic_error_still_low(self):
        """不得把"不知道是什么"的失败也拔高成高优先级（保持原有保守兜底）。"""
        result = DefectDecisionService.decide(
            test_status="failed",
            error_message="Something went wrong",
            response_status_code=200,
            expected_status_code=200,
        )
        assert result.priority == DefectPriority.LOW

    def test_server_error_still_medium(self):
        """5xx 仍是中优先级，未被"数据正确性"规则截胡。"""
        result = DefectDecisionService.decide(
            test_status="failed",
            error_message="Internal Server Error",
            response_status_code=500,
            expected_status_code=200,
        )
        assert result.priority == DefectPriority.MEDIUM


class TestFailureHeadline:
    """「问题概述」必须是一行可读摘要，而不是整块 Playwright 报错。"""

    def test_prefers_custom_assertion_message(self):
        """有自定义断言消息时用它（Playwright 会原样放在 `Error: ` 之后）。"""
        msg = (
            "Error: AUTH_OR_BUSINESS_ENVELOPE resultCode=0 服务端受理了冲突商品\n\n"
            'Expected: "100000"\nReceived:    "0"\n'
        )
        assert extract_failure_headline(msg) == "AUTH_OR_BUSINESS_ENVELOPE resultCode=0 服务端受理了冲突商品"

    def test_skips_playwright_generic_line(self):
        """没有自定义消息时，`expect(received).toBe(expected)` 这种自动行不含信息，要跳过。"""
        msg = 'Error: expect(received).toBe(expected) // Object.is equality\n\nExpected: "0"\nReceived:    "401"\n'
        headline = extract_failure_headline(msg)
        assert headline is not None
        assert not headline.startswith("expect(")
        assert headline == '断言失败：期望 "0"，实际 "401"'

    def test_decision_summary_is_one_line_not_a_blob(self):
        """回归：整块报错不得再进「问题概述」（实测自动提单发生过）。

        Playwright 未收到自定义断言消息时，首行是自动生成的
        `expect(received).toBe(expected) // Object.is equality`，不含业务信息，
        应被概括成「期望 X，实际 Y」。
        """
        blob = (
            "Error: expect(received).toBe(expected) // Object.is equality\n\n"
            'Expected: "100000"\nReceived:    "0"\n'
        )
        result = DefectDecisionService.decide(
            test_status="failed",
            error_message=blob,
            response_status_code=200,
            expected_status_code=200,
        )
        assert "\n" not in result.failure_summary, "问题概述必须是单行"
        assert "Object.is equality" not in result.failure_summary
        assert result.failure_summary == '断言失败：期望 "100000"，实际 "0"'

    def test_none_when_no_message(self):
        assert extract_failure_headline(None) is None
        assert extract_failure_headline("") is None
