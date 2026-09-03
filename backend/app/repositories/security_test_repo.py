"""
安全测试仓储

处理安全测试相关的数据库操作
"""

from datetime import datetime
from typing import Optional
from uuid import UUID

from sqlalchemy import and_, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.repositories.base import BaseRepository
from app.models.security_test import SecurityTest, SecurityVulnerability, SecurityReport


class SecurityTestRepository(BaseRepository[SecurityTest]):
    """安全测试仓储类"""

    def __init__(self, session: AsyncSession):
        super().__init__(SecurityTest, session)

    async def get_by_identifier(self, identifier: str) -> Optional[SecurityTest]:
        """根据标识符获取安全测试"""
        result = await self.session.execute(
            select(SecurityTest)
            .options(selectinload(SecurityTest.vulnerabilities))
            .options(selectinload(SecurityTest.reports))
            .where(SecurityTest.identifier == identifier)
        )
        return result.scalar_one_or_none()

    async def get_by_id_with_relations(self, id: UUID) -> Optional[SecurityTest]:
        """根据 ID 获取安全测试（包含关联数据）"""
        result = await self.session.execute(
            select(SecurityTest)
            .options(selectinload(SecurityTest.vulnerabilities))
            .options(selectinload(SecurityTest.reports))
            .options(selectinload(SecurityTest.project))
            .where(SecurityTest.id == id)
        )
        return result.scalar_one_or_none()

    async def get_by_project(
        self,
        project_id: UUID,
        offset: int = 0,
        limit: int = 20,
        search: Optional[str] = None,
        status: Optional[str] = None,
    ) -> tuple[list[SecurityTest], int]:
        """获取项目下的安全测试列表"""
        from sqlalchemy import text

        # 使用原始 SQL 查询，避免 SQLAlchemy ORM 缓存问题
        where_clause = "WHERE project_id = :project_id"
        params = {"project_id": str(project_id)}

        if search:
            where_clause += " AND (name ILIKE :search OR identifier ILIKE :search OR target ILIKE :search OR description ILIKE :search)"
            params["search"] = f"%{search}%"

        if status:
            where_clause += " AND status = :status"
            params["status"] = status

        # 获取总数
        count_sql = text(f"SELECT COUNT(*) FROM security_tests {where_clause}")
        count_result = await self.session.execute(count_sql, params)
        total = count_result.scalar_one()

        # 获取列表
        items_sql = text(f"""
            SELECT id, project_id, folder_id, identifier, name, target, description,
                   status, scan_config, total_vulnerabilities, critical_count,
                   high_count, medium_count, low_count, info_count, risk_score,
                   thread_id, created_at, updated_at
            FROM security_tests
            {where_clause}
            ORDER BY created_at DESC
            OFFSET :offset LIMIT :limit
        """)
        params["offset"] = offset
        params["limit"] = limit

        result = await self.session.execute(items_sql, params)
        rows = result.all()

        # 将结果转换为 SecurityTest 对象
        items = []
        for row in rows:
            item = SecurityTest(
                id=row.id,
                project_id=row.project_id,
                folder_id=row.folder_id,
                identifier=row.identifier,
                name=row.name,
                target=row.target,
                description=row.description,
                status=row.status,
                scan_config=row.scan_config,
                total_vulnerabilities=row.total_vulnerabilities,
                critical_count=row.critical_count,
                high_count=row.high_count,
                medium_count=row.medium_count,
                low_count=row.low_count,
                info_count=row.info_count,
                risk_score=row.risk_score,
                thread_id=row.thread_id,
                created_at=row.created_at,
                updated_at=row.updated_at,
            )
            items.append(item)

        return items, total


class SecurityVulnerabilityRepository(BaseRepository[SecurityVulnerability]):
    """安全漏洞仓储类"""

    def __init__(self, session: AsyncSession):
        super().__init__(SecurityVulnerability, session)

    async def get_by_security_test(
        self,
        security_test_id: UUID,
        offset: int = 0,
        limit: int = 50,
        severity: Optional[str] = None,
        status: Optional[str] = None,
    ) -> tuple[list[SecurityVulnerability], int]:
        """获取安全测试下的漏洞列表"""
        query = select(SecurityVulnerability).where(
            SecurityVulnerability.security_test_id == security_test_id
        )

        if severity:
            query = query.where(SecurityVulnerability.severity == severity)
        if status:
            query = query.where(SecurityVulnerability.status == status)

        count_result = await self.session.execute(
            select(func.count()).select_from(query.subquery())
        )
        total = count_result.scalar_one()

        query = query.order_by(
            SecurityVulnerability.created_at.desc()
        )
        query = query.offset(offset).limit(limit)

        result = await self.session.execute(query)
        items = list(result.scalars().all())

        return items, total

    async def count_by_severity(self, security_test_id: UUID) -> dict:
        """按风险等级统计漏洞数量"""
        result = await self.session.execute(
            select(
                SecurityVulnerability.severity,
                func.count()
            )
            .where(SecurityVulnerability.security_test_id == security_test_id)
            .group_by(SecurityVulnerability.severity)
        )
        return {row[0]: row[1] for row in result.all()}


class SecurityReportRepository(BaseRepository[SecurityReport]):
    """安全报告仓储类"""

    def __init__(self, session: AsyncSession):
        super().__init__(SecurityReport, session)

    async def get_by_security_test(
        self,
        security_test_id: UUID,
        offset: int = 0,
        limit: int = 20,
    ) -> tuple[list[SecurityReport], int]:
        """获取安全测试下的报告列表"""
        query = select(SecurityReport).where(
            SecurityReport.security_test_id == security_test_id
        )

        count_result = await self.session.execute(
            select(func.count()).select_from(query.subquery())
        )
        total = count_result.scalar_one()

        query = query.order_by(SecurityReport.created_at.desc())
        query = query.offset(offset).limit(limit)

        result = await self.session.execute(query)
        items = list(result.scalars().all())

        return items, total
