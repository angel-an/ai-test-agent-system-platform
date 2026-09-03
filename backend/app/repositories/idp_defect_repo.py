"""
IDP 缺陷记录仓储

处理 IDP 缺陷记录相关的数据库操作
"""

from typing import Optional
from uuid import UUID

from sqlalchemy import and_, desc, select

from app.repositories.base import BaseRepository
from app.models.idp_defect_record import IDPDefectRecord


class IDPDefectRecordRepository(BaseRepository[IDPDefectRecord]):
    """IDP 缺陷记录仓储类"""

    def __init__(self, session):
        super().__init__(IDPDefectRecord, session)

    async def get_by_fingerprint_and_run(
        self,
        fingerprint: str,
        source_run_id: UUID,
    ) -> Optional[IDPDefectRecord]:
        """
        根据指纹和来源运行 ID 获取记录

        用于同一次测试运行内的去重判断
        """
        result = await self.session.execute(
            select(IDPDefectRecord)
            .where(
                and_(
                    IDPDefectRecord.fingerprint == fingerprint,
                    IDPDefectRecord.source_run_id == source_run_id,
                )
            )
        )
        return result.scalar_one_or_none()

    async def get_duplicate_by_fingerprint(
        self,
        fingerprint: str,
        idp_project_id: int,
    ) -> Optional[IDPDefectRecord]:
        """
        根据指纹和 IDP 项目 ID 获取已创建的重复记录

        用于跨测试运行的去重判断（查找之前已成功创建的缺陷）。
        成功状态包括：created, verified, written_back。
        """
        result = await self.session.execute(
            select(IDPDefectRecord)
            .where(
                and_(
                    IDPDefectRecord.fingerprint == fingerprint,
                    IDPDefectRecord.idp_project_id == idp_project_id,
                    IDPDefectRecord.create_status.in_(
                        ["created", "verified", "written_back"]
                    ),
                )
            )
            .order_by(IDPDefectRecord.created_at.desc())
        )
        return result.scalar_one_or_none()

    async def get_by_source_run(
        self,
        source_run_id: UUID,
    ) -> list[IDPDefectRecord]:
        """
        获取指定来源运行的所有 IDP 缺陷记录

        Args:
            source_run_id: 来源测试运行 ID

        Returns:
            list[IDPDefectRecord]: 缺陷记录列表
        """
        result = await self.session.execute(
            select(IDPDefectRecord)
            .where(IDPDefectRecord.source_run_id == source_run_id)
            .order_by(IDPDefectRecord.created_at)
        )
        return list(result.scalars().all())

    # 向后兼容：保留按 test_run_id 查询
    async def get_by_test_run(
        self,
        test_run_id: UUID,
    ) -> list[IDPDefectRecord]:
        """
        获取指定 API 测试运行的所有 IDP 缺陷记录（向后兼容）
        """
        result = await self.session.execute(
            select(IDPDefectRecord)
            .where(
                and_(
                    IDPDefectRecord.test_run_id == test_run_id,
                    IDPDefectRecord.source_type == "api",
                )
            )
            .order_by(IDPDefectRecord.created_at)
        )
        return list(result.scalars().all())

    async def get_by_status(
        self,
        source_run_id: UUID,
        status: str,
    ) -> list[IDPDefectRecord]:
        """根据状态获取缺陷记录"""
        result = await self.session.execute(
            select(IDPDefectRecord)
            .where(
                and_(
                    IDPDefectRecord.source_run_id == source_run_id,
                    IDPDefectRecord.create_status == status,
                )
            )
        )
        return list(result.scalars().all())

    async def get_all_records(
        self,
        status: Optional[str] = None,
        source_type: Optional[str] = None,
        source_project_key: Optional[str] = None,
        offset: int = 0,
        limit: int = 20,
    ) -> list[IDPDefectRecord]:
        """
        获取所有缺陷记录，支持过滤和分页

        Args:
            status: 按状态过滤
            source_type: 按来源类型过滤 (api/web/security)
            source_project_key: 按项目标识符过滤
            offset: 偏移量
            limit: 限制数量

        Returns:
            list[IDPDefectRecord]: 缺陷记录列表
        """
        query = select(IDPDefectRecord)

        conditions = []
        if status:
            conditions.append(IDPDefectRecord.create_status == status)
        if source_type:
            conditions.append(IDPDefectRecord.source_type == source_type)
        if source_project_key:
            conditions.append(IDPDefectRecord.source_project_key == source_project_key)

        if conditions:
            query = query.where(and_(*conditions))

        query = query.order_by(desc(IDPDefectRecord.created_at))
        query = query.offset(offset).limit(limit)

        result = await self.session.execute(query)
        return list(result.scalars().all())

    async def get_by_source_type(
        self,
        source_type: str,
        offset: int = 0,
        limit: int = 20,
    ) -> list[IDPDefectRecord]:
        """按来源类型获取缺陷记录"""
        result = await self.session.execute(
            select(IDPDefectRecord)
            .where(IDPDefectRecord.source_type == source_type)
            .order_by(desc(IDPDefectRecord.created_at))
            .offset(offset)
            .limit(limit)
        )
        return list(result.scalars().all())
