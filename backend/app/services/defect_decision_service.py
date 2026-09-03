"""
缺陷决策服务

MVP 采用规则判断，不引入复杂模型。

创建缺陷的条件：
- API 断言失败
- 实际状态码与预期不一致
- 响应结构与接口契约不一致
- 业务结果明显错误
- 同一问题具备可复现证据

跳过创建的情况：
- 网络不可达
- 测试环境服务未启动
- 测试脚本语法错误
- 认证配置错误
- 测试数据准备失败
- 非稳定性偶发错误
- 已明确标记为非缺陷
"""

import logging
import re
from dataclasses import dataclass
from enum import Enum
from typing import Any, Optional

logger = logging.getLogger(__name__)


class DefectPriority(str, Enum):
    """缺陷优先级"""
    LOW = "low"       # 低优先级
    MEDIUM = "medium"  # 中优先级
    HIGH = "high"     # 高优先级


class DefectDecision(str, Enum):
    """缺陷决策结果"""
    CREATE = "create"    # 创建缺陷
    SKIP = "skip"        # 跳过
    PENDING = "pending"  # 待判断


@dataclass
class DefectDecisionResult:
    """缺陷决策结果"""
    decision: DefectDecision
    priority: DefectPriority
    priority_code: str
    priority_id: int
    reason: str
    error_type: str
    failure_summary: str


class DefectDecisionService:
    """
    缺陷决策服务

    基于规则判断是否需要创建缺陷
    """

    # 跳过创建的关键词/模式
    SKIP_PATTERNS = [
        r"网络不可达",
        r"连接超时",
        r"Connection refused",
        r"Connection timeout",
        r"Network is unreachable",
        r"测试环境.*未启动",
        r"服务.*未启动",
        r"语法错误",
        r"SyntaxError",
        r"认证配置错误",
        r"认证失败",
        r"Unauthorized",
        r"测试数据.*失败",
        r"数据准备.*失败",
        r"非缺陷",
        r"not_a_defect",
        r"脚本错误",
        r"测试脚本",
    ]

    # 高优先级关键词
    HIGH_PRIORITY_PATTERNS = [
        r"数据.*丢失",
        r"数据.*损坏",
        r"安全.*漏洞",
        r"越权",
        r"未授权访问",
        r"SQL 注入",
        r"XSS",
        r"服务.*崩溃",
        r"内存.*溢出",
        r"死锁",
    ]

    @classmethod
    def decide(
        cls,
        test_status: str,
        error_message: Optional[str],
        response_status_code: Optional[int],
        expected_status_code: Optional[int],
        assertion_results: Optional[list[dict]] = None,
        retry_count: int = 0,
    ) -> DefectDecisionResult:
        """
        判断是否需要创建缺陷

        Args:
            test_status: 测试状态 (passed/failed/skipped/blocked)
            error_message: 错误信息
            response_status_code: 实际响应状态码
            expected_status_code: 预期状态码
            assertion_results: 断言结果列表
            retry_count: 重试次数

        Returns:
            DefectDecisionResult: 决策结果
        """
        # 1. 测试通过，不创建缺陷
        if test_status == "passed":
            return DefectDecisionResult(
                decision=DefectDecision.SKIP,
                priority=DefectPriority.LOW,
                priority_code="priority-2",
                priority_id=2,
                reason="测试通过",
                error_type="none",
                failure_summary="测试通过，无需创建缺陷",
            )

        # 2. 检查是否属于跳过的情况
        skip_reason = cls._check_skip_conditions(error_message, retry_count)
        if skip_reason:
            return DefectDecisionResult(
                decision=DefectDecision.SKIP,
                priority=DefectPriority.LOW,
                priority_code="priority-2",
                priority_id=2,
                reason=skip_reason,
                error_type="skip",
                failure_summary=skip_reason,
            )

        # 3. 分析错误类型和优先级
        error_type, failure_summary = cls._analyze_error(
            error_message,
            response_status_code,
            expected_status_code,
            assertion_results,
        )

        priority, priority_code, priority_id = cls._determine_priority(
            error_type,
            error_message,
            response_status_code,
        )

        return DefectDecisionResult(
            decision=DefectDecision.CREATE,
            priority=priority,
            priority_code=priority_code,
            priority_id=priority_id,
            reason=f"检测到缺陷: {failure_summary}",
            error_type=error_type,
            failure_summary=failure_summary,
        )

    @classmethod
    def _check_skip_conditions(
        cls,
        error_message: Optional[str],
        retry_count: int,
    ) -> Optional[str]:
        """
        检查是否应该跳过创建缺陷

        Returns:
            Optional[str]: 跳过原因，不需要跳过时返回 None
        """
        if not error_message:
            return None

        error_lower = error_message.lower()

        # 检查跳过模式
        for pattern in cls.SKIP_PATTERNS:
            if re.search(pattern, error_message, re.IGNORECASE) or re.search(
                pattern, error_lower, re.IGNORECASE
            ):
                return f"跳过创建：匹配到跳过规则 '{pattern}'"

        # 重试次数过多（可能是偶发错误）
        if retry_count >= 3:
            return f"跳过创建：已重试 {retry_count} 次，判定为非稳定性偶发错误"

        return None

    @classmethod
    def _analyze_error(
        cls,
        error_message: Optional[str],
        response_status_code: Optional[int],
        expected_status_code: Optional[int],
        assertion_results: Optional[list[dict]],
    ) -> tuple[str, str]:
        """
        分析错误类型

        Returns:
            tuple[str, str]: (error_type, failure_summary)
        """
        # 状态码不匹配
        if (
            response_status_code is not None
            and expected_status_code is not None
            and response_status_code != expected_status_code
        ):
            return (
                "status_mismatch",
                f"状态码不匹配：预期 {expected_status_code}，实际 {response_status_code}",
            )

        # 断言失败
        if assertion_results:
            failed_assertions = [
                a for a in assertion_results if not a.get("passed", True)
            ]
            if failed_assertions:
                first = failed_assertions[0]
                return (
                    "assertion_failed",
                    f"断言失败: {first.get('message', '未知断言')}",
                )

        # 5xx 错误
        if response_status_code and response_status_code >= 500:
            return (
                "server_error",
                f"服务器错误: HTTP {response_status_code}",
            )

        # 默认错误类型
        error_type = "test_failed"
        failure_summary = error_message or "测试失败（未知原因）"
        if len(failure_summary) > 200:
            failure_summary = failure_summary[:200] + "..."

        return error_type, failure_summary

    @classmethod
    def _determine_priority(
        cls,
        error_type: str,
        error_message: Optional[str],
        response_status_code: Optional[int],
    ) -> tuple[DefectPriority, str, int]:
        """
        确定缺陷优先级

        pageNum=9999 规则：
        - 返回合法空数据或规范错误码：低优先级
        - 稳定返回 500：中优先级
        - 影响服务稳定性或数据正确性：高优先级

        Returns:
            tuple[DefectPriority, str, int]: (priority, priority_code, priority_id)
        """
        error_text = (error_message or "").lower()

        # 检查高优先级模式
        for pattern in cls.HIGH_PRIORITY_PATTERNS:
            if re.search(pattern, error_text, re.IGNORECASE):
                return (DefectPriority.HIGH, "priority-1", 1)

        # 5xx 服务器错误 → 中优先级
        if response_status_code and response_status_code >= 500:
            return (DefectPriority.MEDIUM, "priority-2", 2)

        # 4xx 客户端错误 → 中优先级（可能是接口问题）
        if response_status_code and response_status_code >= 400:
            return (DefectPriority.MEDIUM, "priority-2", 2)

        # 断言失败 → 中优先级
        if error_type == "assertion_failed":
            return (DefectPriority.MEDIUM, "priority-2", 2)

        # 其他情况 → 低优先级
        return (DefectPriority.LOW, "priority-3", 3)

    @classmethod
    def decide_from_api_result(
        cls,
        status: str,
        error_message: Optional[str],
        request_summary: Optional[dict],
        response_summary: Optional[dict],
    ) -> DefectDecisionResult:
        """
        从 API 测试结果中判断是否需要创建缺陷

        便捷方法，从 request_summary 和 response_summary 中提取信息
        """
        response_status_code = None
        expected_status_code = None

        if response_summary:
            response_status_code = response_summary.get("status_code")

        if request_summary:
            expected_status_code = request_summary.get("expected_status_code")

        return cls.decide(
            test_status=status,
            error_message=error_message,
            response_status_code=response_status_code,
            expected_status_code=expected_status_code,
        )
