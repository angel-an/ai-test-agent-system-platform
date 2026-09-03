"""
Android App 功能仓储

处理 Android App 功能和子功能相关的数据库操作
"""

from typing import Optional
from uuid import UUID

from sqlalchemy import Integer, cast, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.repositories.base import BaseRepository
from app.models.android_app import AndroidApp, AndroidSubFunction

class AndroidAppRepository(BaseRepository[AndroidApp]):
    """Android App 功能仓储类"""

    def __init__(self, session: AsyncSession):
        super().__init__(AndroidApp, session)

    async def get_by_identifier(self, identifier: str) -> Optional[AndroidApp]:
        """
        根据标识符获取 Android App 功能

        Args:
            identifier: Android App 功能标识符 (AF-xxx)

        Returns:
            Optional[AndroidApp]: Android App 功能实例或 None
        """
        result = await self.session.execute(
            select(AndroidApp)
            .options(selectinload(AndroidApp.sub_functions))
            .options(selectinload(AndroidApp.android_tests))
            .where(AndroidApp.identifier == identifier)
        )
        return result.scalar_one_or_none()

    async def get_by_id_with_relations(self, id: UUID) -> Optional[AndroidApp]:
        """根据 ID 获取 Android App 功能（包含关联数据）"""
        result = await self.session.execute(
            select(AndroidApp)
            .options(selectinload(AndroidApp.sub_functions))
            .options(selectinload(AndroidApp.android_tests))
            .options(selectinload(AndroidApp.project))
            .where(AndroidApp.id == id)
        )
        return result.scalar_one_or_none()

    async def get_by_project(
        self,
        project_id: UUID,
        offset: int = 0,
        limit: int = 20,
        search: Optional[str] = None,
    ) -> tuple[list[AndroidApp], int]:
        """
        获取项目下的 Android App 功能列表

        Args:
            project_id: 项目 ID
            offset: 偏移量
            limit: 限制数量
            search: 搜索关键词

        Returns:
            tuple[list[AndroidApp], int]: Android App 功能列表和总数
        """
        query = select(AndroidApp).where(AndroidApp.project_id == project_id)

        # 搜索过滤
        if search:
            query = query.where(
                (AndroidApp.name.ilike(f"%{search}%")) |
                (AndroidApp.identifier.ilike(f"%{search}%")) |
                (AndroidApp.display_name.ilike(f"%{search}%"))
            )

        # 获取总数
        count_result = await self.session.execute(
            select(func.count()).select_from(query.subquery())
        )
        total = count_result.scalar_one()

        # 获取数据
        query = query.order_by(AndroidApp.sort_order, AndroidApp.created_at.desc())
        query = query.offset(offset).limit(limit)

        result = await self.session.execute(query)
        items = list(result.scalars().all())

        return items, total

    async def get_by_folder(
        self,
        folder_id: UUID,
        offset: int = 0,
        limit: int = 20,
        search: Optional[str] = None,
    ) -> tuple[list[AndroidApp], int]:
        """
        获取文件夹下的 Android App 功能列表

        Args:
            folder_id: 文件夹 ID
            offset: 偏移量
            limit: 限制数量
            search: 搜索关键词

        Returns:
            tuple[list[AndroidApp], int]: Android App 功能列表和总数
        """
        query = select(AndroidApp).where(AndroidApp.folder_id == folder_id)

        # 搜索过滤
        if search:
            query = query.where(
                (AndroidApp.name.ilike(f"%{search}%")) |
                (AndroidApp.identifier.ilike(f"%{search}%")) |
                (AndroidApp.display_name.ilike(f"%{search}%"))
            )

        # 获取总数
        count_result = await self.session.execute(
            select(func.count()).select_from(query.subquery())
        )
        total = count_result.scalar_one()

        # 获取数据
        query = query.order_by(AndroidApp.sort_order, AndroidApp.created_at.desc())
        query = query.offset(offset).limit(limit)

        result = await self.session.execute(query)
        items = list(result.scalars().all())

        return items, total

    async def get_next_identifier(self, project_id: UUID) -> str:
        """
        生成下一个 Android App 功能标识符

        格式: AF-1001, AF-1002, ...

        通过 PG advisory 事务锁串行化"同 project 同资源"的并发写入，
        提交/回滚时锁自动释放，从根本上避免唯一约束冲突。

        Args:
            project_id: 项目 ID

        Returns:
            str: 下一个标识符
        """
        await self._acquire_xact_lock(f"af_identifier:{project_id}")

        # 取数字后缀的最大值（按 INT 比较，避免字符串排序在跨位数时翻车）
        numeric_part = cast(
            func.regexp_replace(AndroidApp.identifier, r"^\D+", ""),
            Integer,
        )
        result = await self.session.execute(
            select(func.max(numeric_part))
            .where(AndroidApp.project_id == project_id)
            .where(AndroidApp.identifier.op("~")(r"^AF-\d+$"))
        )
        max_number = result.scalar_one_or_none()
        next_number = (max_number or 1000) + 1

        # 检查生成的标识符是否已存在（可能被其他会话创建）
        # 如果存在，递增直到找到可用的
        from sqlalchemy import exists
        for _ in range(100):  # 最多尝试 100 次，防止无限循环
            candidate = f"AF-{next_number}"
            exists_result = await self.session.execute(
                select(exists().where(AndroidApp.identifier == candidate))
            )
            if not exists_result.scalar():
                return candidate
            next_number += 1

        # 如果 100 次都没找到，返回一个带时间戳的标识符作为后备
        import time
        return f"AF-{int(time.time())}"

    async def get_count_by_project(self, project_id: UUID) -> int:
        """获取项目下的 Android App 功能总数"""
        result = await self.session.execute(
            select(func.count()).select_from(AndroidApp).where(
                AndroidApp.project_id == project_id
            )
        )
        return result.scalar_one()

class AndroidSubFunctionRepository(BaseRepository[AndroidSubFunction]):
    """Android App 子功能仓储类"""

    def __init__(self, session: AsyncSession):
        super().__init__(AndroidSubFunction, session)

    async def get_by_identifier(self, identifier: str) -> Optional[AndroidSubFunction]:
        """
        根据标识符获取 Android App 子功能

        Args:
            identifier: Android App 子功能标识符 (ASF-xxx)

        Returns:
            Optional[AndroidSubFunction]: Android App 子功能实例或 None
        """
        result = await self.session.execute(
            select(AndroidSubFunction)
            .options(selectinload(AndroidSubFunction.function))
            .options(selectinload(AndroidSubFunction.android_tests))
            .where(AndroidSubFunction.identifier == identifier)
        )
        return result.scalar_one_or_none()

    async def get_by_id_with_relations(self, id: UUID) -> Optional[AndroidSubFunction]:
        """根据 ID 获取 Android App 子功能（包含关联数据）"""
        result = await self.session.execute(
            select(AndroidSubFunction)
            .options(selectinload(AndroidSubFunction.function))
            .options(selectinload(AndroidSubFunction.android_tests))
            .options(selectinload(AndroidSubFunction.project))
            .where(AndroidSubFunction.id == id)
        )
        return result.scalar_one_or_none()

    async def get_by_function(
        self,
        function_id: UUID,
        offset: int = 0,
        limit: int = 20,
        search: Optional[str] = None,
    ) -> tuple[list[AndroidSubFunction], int]:
        """
        获取功能下的子功能列表

        Args:
            function_id: 功能 ID
            offset: 偏移量
            limit: 限制数量
            search: 搜索关键词

        Returns:
            tuple[list[AndroidSubFunction], int]: 子功能列表和总数
        """
        query = select(AndroidSubFunction).where(AndroidSubFunction.function_id == function_id)

        # 搜索过滤
        if search:
            query = query.where(
                (AndroidSubFunction.name.ilike(f"%{search}%")) |
                (AndroidSubFunction.identifier.ilike(f"%{search}%")) |
                (AndroidSubFunction.display_name.ilike(f"%{search}%"))
            )

        # 获取总数
        count_result = await self.session.execute(
            select(func.count()).select_from(query.subquery())
        )
        total = count_result.scalar_one()

        # 获取数据
        query = query.order_by(AndroidSubFunction.sort_order, AndroidSubFunction.created_at.desc())
        query = query.offset(offset).limit(limit)

        result = await self.session.execute(query)
        items = list(result.scalars().all())

        return items, total

    async def get_by_function_ids(
        self,
        function_ids: list[UUID],
        offset: int = 0,
        limit: int = 20,
        search: Optional[str] = None,
    ) -> tuple[list[AndroidSubFunction], int]:
        """
        获取多个功能下的子功能列表（用于按文件夹查询）

        Args:
            function_ids: 功能 ID 列表
            offset: 偏移量
            limit: 限制数量
            search: 搜索关键词

        Returns:
            tuple[list[AndroidSubFunction], int]: 子功能列表和总数
        """
        query = select(AndroidSubFunction).where(AndroidSubFunction.function_id.in_(function_ids))

        # 搜索过滤
        if search:
            query = query.where(
                (AndroidSubFunction.name.ilike(f"%{search}%")) |
                (AndroidSubFunction.identifier.ilike(f"%{search}%")) |
                (AndroidSubFunction.display_name.ilike(f"%{search}%"))
            )

        # 获取总数
        count_result = await self.session.execute(
            select(func.count()).select_from(query.subquery())
        )
        total = count_result.scalar_one()

        # 获取数据
        query = query.order_by(AndroidSubFunction.sort_order, AndroidSubFunction.created_at.desc())
        query = query.offset(offset).limit(limit)

        result = await self.session.execute(query)
        items = list(result.scalars().all())

        return items, total

    async def get_by_project(
        self,
        project_id: UUID,
        offset: int = 0,
        limit: int = 20,
        search: Optional[str] = None,
    ) -> tuple[list[AndroidSubFunction], int]:
        """
        获取项目下的子功能列表

        Args:
            project_id: 项目 ID
            offset: 偏移量
            limit: 限制数量
            search: 搜索关键词

        Returns:
            tuple[list[AndroidSubFunction], int]: 子功能列表和总数
        """
        query = select(AndroidSubFunction).where(AndroidSubFunction.project_id == project_id)

        # 搜索过滤
        if search:
            query = query.where(
                (AndroidSubFunction.name.ilike(f"%{search}%")) |
                (AndroidSubFunction.identifier.ilike(f"%{search}%")) |
                (AndroidSubFunction.display_name.ilike(f"%{search}%"))
            )

        # 获取总数
        count_result = await self.session.execute(
            select(func.count()).select_from(query.subquery())
        )
        total = count_result.scalar_one()

        # 获取数据
        query = query.order_by(AndroidSubFunction.sort_order, AndroidSubFunction.created_at.desc())
        query = query.offset(offset).limit(limit)

        result = await self.session.execute(query)
        items = list(result.scalars().all())

        return items, total

    async def get_by_folder(
        self,
        folder_id: UUID,
        offset: int = 0,
        limit: int = 20,
        search: Optional[str] = None,
    ) -> tuple[list[AndroidSubFunction], int]:
        """
        获取文件夹下的子功能列表

        Args:
            folder_id: 文件夹 ID
            offset: 偏移量
            limit: 限制数量
            search: 搜索关键词

        Returns:
            tuple[list[AndroidSubFunction], int]: 子功能列表和总数
        """
        query = select(AndroidSubFunction).where(AndroidSubFunction.folder_id == folder_id)

        # 搜索过滤
        if search:
            query = query.where(
                (AndroidSubFunction.name.ilike(f"%{search}%")) |
                (AndroidSubFunction.identifier.ilike(f"%{search}%")) |
                (AndroidSubFunction.display_name.ilike(f"%{search}%"))
            )

        # 获取总数
        count_result = await self.session.execute(
            select(func.count()).select_from(query.subquery())
        )
        total = count_result.scalar_one()

        # 获取数据
        query = query.order_by(AndroidSubFunction.sort_order, AndroidSubFunction.created_at.desc())
        query = query.offset(offset).limit(limit)

        result = await self.session.execute(query)
        items = list(result.scalars().all())

        return items, total

    async def get_next_identifier(self, project_id: UUID) -> str:
        """
        生成下一个 Android App 子功能标识符

        格式: ASF-1001, ASF-1002, ...

        通过 PG advisory 事务锁串行化"同 project 同资源"的并发写入，
        提交/回滚时锁自动释放，从根本上避免唯一约束冲突。

        Args:
            project_id: 项目 ID

        Returns:
            str: 下一个标识符
        """
        await self._acquire_xact_lock(f"asf_identifier:{project_id}")

        numeric_part = cast(
            func.regexp_replace(AndroidSubFunction.identifier, r"^\D+", ""),
            Integer,
        )
        result = await self.session.execute(
            select(func.max(numeric_part))
            .where(AndroidSubFunction.project_id == project_id)
            .where(AndroidSubFunction.identifier.op("~")(r"^ASF-\d+$"))
        )
        max_number = result.scalar_one_or_none()
        next_number = (max_number or 1000) + 1

        # 检查生成的标识符是否已存在（可能被其他会话创建）
        from sqlalchemy import exists
        for _ in range(100):
            candidate = f"ASF-{next_number}"
            exists_result = await self.session.execute(
                select(exists().where(AndroidSubFunction.identifier == candidate))
            )
            if not exists_result.scalar():
                return candidate
            next_number += 1

        import time
        return f"ASF-{int(time.time())}"

    async def get_count_by_project(self, project_id: UUID) -> int:
        """获取项目下的子功能总数"""
        result = await self.session.execute(
            select(func.count()).select_from(AndroidSubFunction).where(
                AndroidSubFunction.project_id == project_id
            )
        )
        return result.scalar_one()
