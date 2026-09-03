"""
缺陷决策服务单元测试

测试缺陷判断规则、优先级规则
"""

import pytest

from app.services.defect_decision_service import (
    DefectDecisionService,
    DefectDecision,
    DefectPriority,
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
