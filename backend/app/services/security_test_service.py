"""
安全测试服务

处理安全测试（渗透测试）相关的业务逻辑
"""

import json
from datetime import datetime
from typing import Optional
from uuid import UUID, uuid4

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.security_test import SecurityTest, SecurityVulnerability, SecurityReport
from app.repositories.security_test_repo import (
    SecurityTestRepository,
    SecurityVulnerabilityRepository,
    SecurityReportRepository,
)
from app.repositories.project_repo import ProjectRepository
from app.utils.exceptions import NotFoundException
from app.config.settings import settings
from app.services.defect_registration_service import DefectRegistrationService


class SecurityTestService:
    """安全测试服务类"""

    def __init__(self, session: AsyncSession, mongodb=None):
        self.session = session
        self.mongodb = mongodb
        self.security_test_repo = SecurityTestRepository(session)
        self.vulnerability_repo = SecurityVulnerabilityRepository(session)
        self.report_repo = SecurityReportRepository(session)
        self.project_repo = ProjectRepository(session)

    async def _get_project_by_identifier(self, identifier: str):
        """获取项目，不存在则抛出异常"""
        project = await self.project_repo.get_by_identifier(identifier)
        if not project:
            raise NotFoundException(resource_type="项目", resource_id=identifier)
        return project

    # ==================== 安全测试任务管理 ====================

    async def create_security_test(
        self,
        project_identifier: str,
        name: str,
        target: str,
        description: Optional[str] = None,
        scan_config: Optional[dict] = None,
        folder_id: Optional[str] = None,
    ) -> dict:
        """创建安全测试任务"""
        project = await self._get_project_by_identifier(project_identifier)

        # 生成标识符
        identifier = f"ST-{uuid4().hex[:8].upper()}"

        security_test = await self.security_test_repo.create(
            project_id=project.id,
            folder_id=UUID(folder_id) if folder_id else None,
            identifier=identifier,
            name=name,
            target=target,
            description=description,
            status="pending",
            scan_config=scan_config or {},
            total_vulnerabilities=0,
            critical_count=0,
            high_count=0,
            medium_count=0,
            low_count=0,
            info_count=0,
        )

        return {
            "id": str(security_test.id),
            "identifier": security_test.identifier,
            "name": security_test.name,
            "target": security_test.target,
            "status": security_test.status,
            "created_at": security_test.created_at.isoformat() if security_test.created_at else None,
        }

    async def list_security_tests(
        self,
        project_identifier: str,
        page: int = 1,
        page_size: int = 20,
        search: Optional[str] = None,
        status: Optional[str] = None,
    ) -> dict:
        """获取安全测试列表"""
        project = await self._get_project_by_identifier(project_identifier)

        offset = (page - 1) * page_size
        items, total = await self.security_test_repo.get_by_project(
            project_id=project.id,
            offset=offset,
            limit=page_size,
            search=search,
            status=status,
        )

        return {
            "items": [
                {
                    "id": str(item.id),
                    "identifier": item.identifier,
                    "name": item.name,
                    "target": item.target,
                    "status": item.status,
                    "total_vulnerabilities": item.total_vulnerabilities or 0,
                    "critical_count": item.critical_count or 0,
                    "high_count": item.high_count or 0,
                    "medium_count": item.medium_count or 0,
                    "low_count": item.low_count or 0,
                    "info_count": item.info_count or 0,
                    "risk_score": item.risk_score,
                    "thread_id": item.thread_id,
                    "created_at": item.created_at.isoformat() if item.created_at else None,
                    "updated_at": item.updated_at.isoformat() if item.updated_at else None,
                }
                for item in items
            ],
            "total": total,
            "page": page,
            "page_size": page_size,
            "total_pages": (total + page_size - 1) // page_size,
        }

    async def get_security_test(
        self,
        project_identifier: str,
        security_test_id: str,
    ) -> dict:
        """获取安全测试详情"""
        project = await self._get_project_by_identifier(project_identifier)

        # 尝试按 ID 或标识符查询
        try:
            test_uuid = UUID(security_test_id)
            security_test = await self.security_test_repo.get_by_id_with_relations(test_uuid)
        except ValueError:
            security_test = await self.security_test_repo.get_by_identifier(security_test_id)

        if not security_test or security_test.project_id != project.id:
            raise NotFoundException(resource_type="安全测试", resource_id=security_test_id)

        return {
            "id": str(security_test.id),
            "identifier": security_test.identifier,
            "name": security_test.name,
            "target": security_test.target,
            "description": security_test.description,
            "status": security_test.status,
            "scan_config": security_test.scan_config,
            "total_vulnerabilities": security_test.total_vulnerabilities or 0,
            "critical_count": security_test.critical_count or 0,
            "high_count": security_test.high_count or 0,
            "medium_count": security_test.medium_count or 0,
            "low_count": security_test.low_count or 0,
            "info_count": security_test.info_count or 0,
            "risk_score": security_test.risk_score,
            "thread_id": security_test.thread_id,
            "vulnerabilities": [
                {
                    "id": str(v.id),
                    "vuln_id": v.vuln_id,
                    "title": v.title,
                    "severity": v.severity,
                    "vuln_type": v.vuln_type,
                    "status": v.status,
                }
                for v in (security_test.vulnerabilities or [])
            ],
            "reports": [
                {
                    "id": str(r.id),
                    "name": r.name,
                    "report_type": r.report_type,
                    "format": r.format,
                    "risk_score": r.risk_score,
                }
                for r in (security_test.reports or [])
            ],
            "created_at": security_test.created_at.isoformat() if security_test.created_at else None,
            "updated_at": security_test.updated_at.isoformat() if security_test.updated_at else None,
        }

    async def update_security_test_status(
        self,
        project_identifier: str,
        security_test_id: str,
        status: str,
    ) -> dict:
        """更新安全测试状态"""
        project = await self._get_project_by_identifier(project_identifier)

        try:
            test_uuid = UUID(security_test_id)
            security_test = await self.security_test_repo.get_by_id(test_uuid)
        except ValueError:
            security_test = await self.security_test_repo.get_by_identifier(security_test_id)

        if not security_test or security_test.project_id != project.id:
            raise NotFoundException(resource_type="安全测试", resource_id=security_test_id)

        updated = await self.security_test_repo.update(
            security_test,
            status=status,
        )

        return {
            "id": str(updated.id),
            "identifier": updated.identifier,
            "status": updated.status,
        }

    async def update_security_test_thread_id(
        self,
        project_identifier: str,
        security_test_id: str,
        thread_id: str,
    ) -> dict:
        """更新安全测试关联的对话线程 ID"""
        project = await self._get_project_by_identifier(project_identifier)

        try:
            test_uuid = UUID(security_test_id)
            security_test = await self.security_test_repo.get_by_id(test_uuid)
        except ValueError:
            security_test = await self.security_test_repo.get_by_identifier(security_test_id)

        if not security_test or security_test.project_id != project.id:
            raise NotFoundException(resource_type="安全测试", resource_id=security_test_id)

        updated = await self.security_test_repo.update(
            security_test,
            thread_id=thread_id,
        )

        return {
            "id": str(updated.id),
            "identifier": updated.identifier,
            "thread_id": updated.thread_id,
        }

    async def update_security_test(
        self,
        project_identifier: str,
        security_test_id: str,
        name: Optional[str] = None,
        target: Optional[str] = None,
        description: Optional[str] = None,
    ) -> dict:
        """更新安全测试任务基本信息"""
        project = await self._get_project_by_identifier(project_identifier)

        try:
            test_uuid = UUID(security_test_id)
            security_test = await self.security_test_repo.get_by_id(test_uuid)
        except ValueError:
            security_test = await self.security_test_repo.get_by_identifier(security_test_id)

        if not security_test or security_test.project_id != project.id:
            raise NotFoundException(resource_type="安全测试", resource_id=security_test_id)

        update_data = {}
        if name is not None:
            update_data["name"] = name
        if target is not None:
            update_data["target"] = target
        if description is not None:
            update_data["description"] = description

        updated = await self.security_test_repo.update(
            security_test,
            **update_data
        )

        return {
            "id": str(updated.id),
            "identifier": updated.identifier,
            "name": updated.name,
            "target": updated.target,
            "description": updated.description,
        }

    async def delete_security_test(
        self,
        project_identifier: str,
        security_test_id: str,
    ) -> dict:
        """删除安全测试"""
        project = await self._get_project_by_identifier(project_identifier)

        try:
            test_uuid = UUID(security_test_id)
            security_test = await self.security_test_repo.get_by_id(test_uuid)
        except ValueError:
            security_test = await self.security_test_repo.get_by_identifier(security_test_id)

        if not security_test or security_test.project_id != project.id:
            raise NotFoundException(resource_type="安全测试", resource_id=security_test_id)

        await self.security_test_repo.delete(security_test)

        return {"message": f"安全测试 '{security_test.name}' 已删除"}

    # ==================== 漏洞管理 ====================

    async def add_vulnerability(
        self,
        project_identifier: str,
        security_test_id: str,
        vuln_id: str,
        title: str,
        severity: str,
        vuln_type: Optional[str] = None,
        affected_url: Optional[str] = None,
        parameter: Optional[str] = None,
        description: Optional[str] = None,
        reproduction: Optional[str] = None,
        evidence: Optional[str] = None,
        remediation: Optional[str] = None,
        cvss_score: Optional[float] = None,
    ) -> dict:
        """添加漏洞发现"""
        project = await self._get_project_by_identifier(project_identifier)

        try:
            test_uuid = UUID(security_test_id)
            security_test = await self.security_test_repo.get_by_id(test_uuid)
        except ValueError:
            security_test = await self.security_test_repo.get_by_identifier(security_test_id)

        if not security_test or security_test.project_id != project.id:
            raise NotFoundException(resource_type="安全测试", resource_id=security_test_id)

        vulnerability = await self.vulnerability_repo.create(
            security_test_id=security_test.id,
            vuln_id=vuln_id,
            title=title,
            severity=severity,
            vuln_type=vuln_type,
            affected_url=affected_url,
            parameter=parameter,
            description=description,
            reproduction=reproduction,
            evidence=evidence,
            remediation=remediation,
            cvss_score=cvss_score,
            status="open",
        )

        # IDP 缺陷登记（安全漏洞自动登记）
        try:
            registration_service = DefectRegistrationService(self.session)
            await registration_service.register_from_security_finding(
                test_run_id=security_test.id,
                test_case_id=None,
                source_project_key=project_identifier,
                vulnerability_name=title,
                target_url=affected_url or security_test.target,
                severity=severity,
                description=description,
                reproduction_steps=reproduction or "",
                evidence=evidence,
            )
        except Exception as idp_err:
            # IDP 登记失败不影响漏洞记录保存
            logger.warning(
                "[SecurityTest] IDP 缺陷登记失败（不影响漏洞记录）: %s", idp_err
            )

        # 更新统计
        severity_counts = await self.vulnerability_repo.count_by_severity(security_test.id)
        total = sum(severity_counts.values())
        await self.security_test_repo.update(
            security_test,
            total_vulnerabilities=total,
            critical_count=severity_counts.get("Critical", 0),
            high_count=severity_counts.get("High", 0),
            medium_count=severity_counts.get("Medium", 0),
            low_count=severity_counts.get("Low", 0),
            info_count=severity_counts.get("Info", 0),
        )

        return {
            "id": str(vulnerability.id),
            "vuln_id": vulnerability.vuln_id,
            "title": vulnerability.title,
            "severity": vulnerability.severity,
            "message": f"漏洞 '{vulnerability.vuln_id}' 添加成功",
        }

    async def list_vulnerabilities(
        self,
        project_identifier: str,
        security_test_id: str,
        page: int = 1,
        page_size: int = 50,
        severity: Optional[str] = None,
        status: Optional[str] = None,
    ) -> dict:
        """获取漏洞列表"""
        project = await self._get_project_by_identifier(project_identifier)

        try:
            test_uuid = UUID(security_test_id)
            security_test = await self.security_test_repo.get_by_id(test_uuid)
        except ValueError:
            security_test = await self.security_test_repo.get_by_identifier(security_test_id)

        if not security_test or security_test.project_id != project.id:
            raise NotFoundException(resource_type="安全测试", resource_id=security_test_id)

        offset = (page - 1) * page_size
        items, total = await self.vulnerability_repo.get_by_security_test(
            security_test_id=security_test.id,
            offset=offset,
            limit=page_size,
            severity=severity,
            status=status,
        )

        return {
            "items": [
                {
                    "id": str(item.id),
                    "vuln_id": item.vuln_id,
                    "title": item.title,
                    "severity": item.severity,
                    "vuln_type": item.vuln_type,
                    "affected_url": item.affected_url,
                    "parameter": item.parameter,
                    "description": item.description,
                    "status": item.status,
                    "cvss_score": item.cvss_score,
                    "created_at": item.created_at.isoformat() if item.created_at else None,
                }
                for item in items
            ],
            "total": total,
            "page": page,
            "page_size": page_size,
            "total_pages": (total + page_size - 1) // page_size,
        }

    # ==================== 报告管理 ====================

    async def save_report(
        self,
        project_identifier: str,
        security_test_id: str,
        name: str,
        report_type: str,
        format: str,
        content: str,
        file_path: Optional[str] = None,
        risk_score: Optional[float] = None,
        summary: Optional[str] = None,
    ) -> dict:
        """保存渗透测试报告"""
        project = await self._get_project_by_identifier(project_identifier)

        try:
            test_uuid = UUID(security_test_id)
            security_test = await self.security_test_repo.get_by_id(test_uuid)
        except ValueError:
            security_test = await self.security_test_repo.get_by_identifier(security_test_id)

        if not security_test or security_test.project_id != project.id:
            raise NotFoundException(resource_type="安全测试", resource_id=security_test_id)

        summary_data = json.loads(summary) if summary else None

        report = await self.report_repo.create(
            security_test_id=security_test.id,
            name=name,
            report_type=report_type,
            format=format,
            content=content,
            file_path=file_path,
            risk_score=risk_score,
            summary=summary_data,
        )

        # 自动更新安全测试状态为 completed（报告已保存表示测试完成）
        if security_test.status != "completed":
            await self.security_test_repo.update(
                security_test,
                status="completed",
            )
            print(f"[SecurityTestService] 安全测试 {security_test_id} 状态已自动更新为 completed（报告已保存）")

        return {
            "id": str(report.id),
            "name": report.name,
            "report_type": report.report_type,
            "format": report.format,
            "message": f"报告 '{report.name}' 保存成功",
        }

    async def list_reports(
        self,
        project_identifier: str,
        security_test_id: str,
        page: int = 1,
        page_size: int = 20,
    ) -> dict:
        """获取报告列表"""
        project = await self._get_project_by_identifier(project_identifier)

        try:
            test_uuid = UUID(security_test_id)
            security_test = await self.security_test_repo.get_by_id(test_uuid)
        except ValueError:
            security_test = await self.security_test_repo.get_by_identifier(security_test_id)

        if not security_test or security_test.project_id != project.id:
            raise NotFoundException(resource_type="安全测试", resource_id=security_test_id)

        offset = (page - 1) * page_size
        items, total = await self.report_repo.get_by_security_test(
            security_test_id=security_test.id,
            offset=offset,
            limit=page_size,
        )

        return {
            "items": [
                {
                    "id": str(item.id),
                    "name": item.name,
                    "report_type": item.report_type,
                    "format": item.format,
                    "risk_score": item.risk_score,
                    "file_path": item.file_path,
                    "content": item.content,
                    "created_at": item.created_at.isoformat() if item.created_at else None,
                }
                for item in items
            ],
            "total": total,
            "page": page,
            "page_size": page_size,
            "total_pages": (total + page_size - 1) // page_size,
        }

    async def get_report(
        self,
        project_identifier: str,
        report_id: str,
    ) -> dict:
        """获取报告详情"""
        project = await self._get_project_by_identifier(project_identifier)

        try:
            report_uuid = UUID(report_id)
            report = await self.report_repo.get_by_id(report_uuid)
        except ValueError:
            raise NotFoundException(resource_type="报告", resource_id=report_id)

        if not report:
            raise NotFoundException(resource_type="报告", resource_id=report_id)

        # 验证报告所属的安全测试是否属于当前项目
        security_test = await self.security_test_repo.get_by_id(report.security_test_id)
        if not security_test or security_test.project_id != project.id:
            raise NotFoundException(resource_type="报告", resource_id=report_id)

        return {
            "id": str(report.id),
            "name": report.name,
            "report_type": report.report_type,
            "format": report.format,
            "content": report.content,
            "file_path": report.file_path,
            "risk_score": report.risk_score,
            "summary": report.summary,
            "created_at": report.created_at.isoformat() if report.created_at else None,
        }
