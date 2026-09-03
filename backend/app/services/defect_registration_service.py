"""
统一缺陷登记服务

API / Web / 安全测试均调用此服务完成缺陷登记。
封装 IDPDefectService，提供统一的入口和适配逻辑。
"""

import logging
from typing import Optional
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.services.defect_decision_service import (
    DefectDecisionService,
    DefectDecisionResult,
    DefectDecision,
    DefectPriority,
)
from app.services.idp_defect_service import IDPDefectService

logger = logging.getLogger(__name__)


class DefectRegistrationService:
    """
    统一缺陷登记服务

    为 API 测试、Web 测试、安全测试提供统一的缺陷登记入口。
    负责：
    - 适配不同测试类型的证据格式
    - 调用 IDPDefectService 创建缺陷
    - 记录来源类型（api/web/security）
    """

    def __init__(self, session: AsyncSession):
        self.session = session
        self.idp_service = IDPDefectService(session)

    # ========================================================================
    # API 测试失败登记
    # ========================================================================

    async def register_from_api_failure(
        self,
        test_run_id: UUID,
        test_case_id: Optional[UUID],
        source_project_key: str,
        scenario_name: str,
        endpoint: str,
        method: str,
        request_summary: Optional[dict],
        response_summary: Optional[dict],
        error_message: Optional[str],
        report_url: Optional[str] = None,
        allow_create: bool = True,
    ):
        """
        从 API 测试失败登记缺陷

        Args:
            test_run_id: API 测试运行 ID
            test_case_id: 测试用例 ID
            source_project_key: 项目标识符
            scenario_name: 场景名称
            endpoint: API 端点
            method: HTTP 方法
            request_summary: 请求摘要
            response_summary: 响应摘要
            error_message: 错误信息
            report_url: 报告地址
            allow_create: 是否允许自动创建
        """
        # 决策判断
        decision = DefectDecisionService.decide(
            test_status="failed",
            error_message=error_message,
            response_status_code=response_summary.get("status_code") if response_summary else None,
            expected_status_code=request_summary.get("expected_status_code") if request_summary else None,
        )

        if decision.decision.value == "skip":
            logger.info("[DefectRegistration] API 测试跳过登记: %s", decision.reason)
            return None

        return await self.idp_service.process_test_failure(
            test_run_id=test_run_id,
            test_case_id=test_case_id,
            source_project_key=source_project_key,
            decision=decision,
            scenario_name=scenario_name,
            endpoint=endpoint,
            method=method,
            request_summary=request_summary,
            response_summary=response_summary,
            report_url=report_url,
            allow_create=allow_create,
            source_type="api",
        )

    # ========================================================================
    # Web 测试失败登记
    # ========================================================================

    async def register_from_web_failure(
        self,
        test_run_id: UUID,
        test_case_id: Optional[UUID],
        source_project_key: str,
        scenario_name: str,
        page_url: str,
        action: str,
        request_summary: Optional[dict],
        response_summary: Optional[dict],
        error_message: Optional[str],
        screenshot_url: Optional[str] = None,
        report_url: Optional[str] = None,
        allow_create: bool = True,
    ):
        """
        从 Web 测试失败登记缺陷

        Args:
            test_run_id: Web 测试运行 ID
            test_case_id: 测试用例 ID
            source_project_key: 项目标识符
            scenario_name: 场景名称
            page_url: 页面 URL
            action: 操作（点击/输入/导航等）
            request_summary: 请求摘要
            response_summary: 响应摘要
            error_message: 错误信息
            screenshot_url: 截图地址
            report_url: 报告地址
            allow_create: 是否允许自动创建
        """
        # 决策判断
        decision = DefectDecisionService.decide(
            test_status="failed",
            error_message=error_message,
            response_status_code=response_summary.get("status_code") if response_summary else None,
            expected_status_code=200,
        )

        if decision.decision.value == "skip":
            logger.info("[DefectRegistration] Web 测试跳过登记: %s", decision.reason)
            return None

        # 构建 Web 测试特有的请求摘要
        web_request_summary = {
            "url": page_url,
            "method": action,
            "headers": request_summary.get("headers", {}) if request_summary else {},
            "body": request_summary.get("body", {}) if request_summary else {},
            "screenshot_url": screenshot_url,
        }

        web_response_summary = {
            "status_code": response_summary.get("status_code", 500) if response_summary else 500,
            "headers": response_summary.get("headers", {}) if response_summary else {},
            "body": response_summary.get("body", {}) if response_summary else {},
        }

        return await self.idp_service.process_test_failure(
            test_run_id=test_run_id,
            test_case_id=test_case_id,
            source_project_key=source_project_key,
            decision=decision,
            scenario_name=scenario_name,
            endpoint=page_url,
            method=action,
            request_summary=web_request_summary,
            response_summary=web_response_summary,
            report_url=report_url,
            allow_create=allow_create,
            source_type="web",
        )

    # ========================================================================
    # 安全测试漏洞登记
    # ========================================================================

    async def register_from_security_finding(
        self,
        test_run_id: UUID,
        test_case_id: Optional[UUID],
        source_project_key: str,
        vulnerability_name: str,
        target_url: str,
        severity: str,
        description: str,
        reproduction_steps: str,
        evidence: Optional[dict] = None,
        report_url: Optional[str] = None,
        allow_create: bool = True,
    ):
        """
        从安全测试漏洞发现登记缺陷

        Args:
            test_run_id: 安全测试运行 ID
            test_case_id: 测试用例 ID
            source_project_key: 项目标识符
            vulnerability_name: 漏洞名称
            target_url: 目标 URL
            severity: 严重程度 (Critical/High/Medium/Low/Info)
            description: 漏洞描述
            reproduction_steps: 复现步骤
            evidence: 证据（请求/响应/截图等）
            report_url: 报告地址
            allow_create: 是否允许自动创建
        """
        # 将安全漏洞严重程度映射为缺陷优先级
        severity_to_priority = {
            "Critical": ("high", "priority-1", 1),
            "High": ("high", "priority-1", 1),
            "Medium": ("medium", "priority-2", 2),
            "Low": ("low", "priority-3", 3),
            "Info": ("low", "priority-3", 3),
        }
        priority, priority_code, priority_id = severity_to_priority.get(
            severity, ("medium", "priority-2", 2)
        )

        decision = DefectDecisionResult(
            decision=DefectDecision.CREATE,
            priority=DefectPriority(priority),
            priority_code=priority_code,
            priority_id=priority_id,
            reason=f"安全测试发现漏洞: {vulnerability_name}",
            error_type="security_vulnerability",
            failure_summary=description,
        )

        # 构建安全测试特有的请求摘要
        security_request_summary = {
            "url": target_url,
            "method": "SCAN",
            "headers": {},
            "body": {},
            "vulnerability_name": vulnerability_name,
            "severity": severity,
            "reproduction_steps": reproduction_steps,
        }

        security_response_summary = {
            "status_code": 200,
            "headers": {},
            "body": evidence or {},
        }

        return await self.idp_service.process_test_failure(
            test_run_id=test_run_id,
            test_case_id=test_case_id,
            source_project_key=source_project_key,
            decision=decision,
            scenario_name=vulnerability_name,
            endpoint=target_url,
            method="SCAN",
            request_summary=security_request_summary,
            response_summary=security_response_summary,
            report_url=report_url,
            allow_create=allow_create,
            source_type="security",
        )

    # ========================================================================
    # 人工确认后处理 pending 记录
    # ========================================================================

    async def process_pending_record(
        self,
        record_id: UUID,
    ):
        """
        人工确认后处理 pending 记录

        将证据不足或 pending 状态的记录重新提交到 IDP 创建。
        """
        from app.repositories.idp_defect_repo import IDPDefectRecordRepository
        from app.schemas.enums import IDPDefectStatus

        repo = IDPDefectRecordRepository(self.session)
        record = await repo.get_by_id(record_id)

        if not record:
            raise ValueError(f"记录不存在: {record_id}")

        if record.create_status not in (
            IDPDefectStatus.PENDING.value,
            IDPDefectStatus.INSUFFICIENT_EVIDENCE.value,
        ):
            raise ValueError(f"记录状态 {record.create_status} 不支持处理")

        # TODO: 重新调用 IDP 创建
        # 三期实现：调用 idp_client.create_issue 并校验
        logger.info("[DefectRegistration] 处理 pending 记录: %s", record_id)
        return record
