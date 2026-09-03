"""
iOS App 功能仓储

处理 iOS App 功能和子功能相关的数据库操作
"""

from typing import Optional
from uuid import UUID

from sqlalchemy import Integer, cast, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.repositories.base import BaseRepository
from app.models.ios_app import IOSApp, IOSSubFunction

class IOSAppRepository(BaseRepository[IOSApp]):
    """iOS App 功能仓储类"""

    def __init__(self, session: AsyncSession):
        super().__init__(IOSApp, session)

    async def get_by_identifier(self, identifier: str) -> Optional[IOSApp]:
        """
        根据标识符获取 iOS App 功能

        Args:
            identifier: iOS App 功能标识符 (IF-xxx)

        Returns:
            Optional[IOSApp]: iOS App 功能实例或 None
        """
        result = await self.session.execute(
            select(IOSApp)
            .options(selectinload(IOSApp.sub_functions))
            .options(selectinload(IOSApp.ios_tests))
            .where(IOSApp.identifier == identifier)
        )
        return result.scalar_one_or_none()

    async def get_by_id_with_relations(self, id: UUID) -> Optional[IOSApp]:
        """根据 ID 获取 iOS App 功能（包含关联数据）"""
        result = await self.session.execute(
            select(IOSApp)
            .options(selectinload(IOSApp.sub_functions))
            .options(selectinload(IOSApp.ios_tests))
            .options(selectinload(IOSApp.project))
            .where(IOSApp.id == id)
        )
        return result.scalar_one_or_none()

    async def get_by_project(
        self,
        project_id: UUID,
        offset: int = 0,
        limit: int = 20,
        search: Optional[str] = None,
    ) -> tuple[list[IOSApp], int]:
        """
        获取项目下的 iOS App 功能列表

        Args:
            project_id: 项目 ID
            offset: 偏移量
            limit: 限制数量
            search: 搜索关键词

        Returns:
            tuple[list[IOSApp], int]: iOS App 功能列表和总数
        """
        query = select(IOSApp).where(IOSApp.project_id == project_id)

        # 搜索过滤
        if search:
            query = query.where(
                (IOSApp.name.ilike(f"%{search}%")) |
                (IOSApp.identifier.ilike(f"%{search}%")) |
                (IOSApp.display_name.ilike(f"%{search}%"))
            )

        # 获取总数
        count_result = await self.session.execute(
            select(func.count()).select_from(query.subquery())
        )
        total = count_result.scalar_one()

        # 获取数据
        query = query.order_by(IOSApp.sort_order, IOSApp.created_at.desc())
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
    ) -> tuple[list[IOSApp], int]:
        """
        获取文件夹下的 iOS App 功能列表

        Args:
            folder_id: 文件夹 ID
            offset: 偏移量
            limit: 限制数量
            search: 搜索关键词

        Returns:
            tuple[list[IOSApp], int]: iOS App 功能列表和总数
        """
        query = select(IOSApp).where(IOSApp.folder_id == folder_id)

        # 搜索过滤
        if search:
            query = query.where(
                (IOSApp.name.ilike(f"%{search}%")) |
                (IOSApp.identifier.ilike(f"%{search}%")) |
                (IOSApp.display_name.ilike(f"%{search}%"))
            )

        # 获取总数
        count_result = await self.session.execute(
            select(func.count()).select_from(query.subquery())
        )
        total = count_result.scalar_one()

        # 获取数据
        query = query.order_by(IOSApp.sort_order, IOSApp.created_at.desc())
        query = query.offset(offset).limit(limit)

        result = await self.session.execute(query)
        items = list(result.scalars().all())

        return items, total

    async def get_next_identifier(self, project_id: UUID) -> str:
        """
        生成下一个 iOS App 功能标识符

        格式: IF-1001, IF-1002, ...

        通过 PG advisory 事务锁串行化"同 project 同资源"的并发写入，
        提交/回滚时锁自动释放，从根本上避免唯一约束冲突。

        Args:
            project_id: 项目 ID

        Returns:
            str: 下一个标识符
        """
        await self._acquire_xact_lock(f"if_identifier:{project_id}")

        # 取数字后缀的最大值（按 INT 比较，避免字符串排序在跨位数时翻车）
        numeric_part = cast(
            func.regexp_replace(IOSApp.identifier, r"^\D+", ""),
            Integer,
        )
        result = await self.session.execute(
            select(func.max(numeric_part))
            .where(IOSApp.project_id == project_id)
            .where(IOSApp.identifier.op("~")(r"^IF-\d+$"))
        )
        max_number = result.scalar_one_or_none()
        next_number = (max_number or 1000) + 1

        # 检查生成的标识符是否已存在（可能被其他会话创建）
        # 如果存在，递增直到找到可用的
        from sqlalchemy import exists
        for _ in range(100):  # 最多尝试 100 次，防止无限循环
            candidate = f"IF-{next_number}"
            exists_result = await self.session.execute(
                select(exists().where(IOSApp.identifier == candidate))
            )
            if not exists_result.scalar():
                return candidate
            next_number += 1

        # 如果 100 次都没找到，返回一个带时间戳的标识符作为后备
        import time
        return f"IF-{int(time.time())}"

    async def get_count_by_project(self, project_id: UUID) -> int:
        """获取项目下的 iOS App 功能总数"""
        result = await self.session.execute(
            select(func.count()).select_from(IOSApp).where(
                IOSApp.project_id == project_id
            )
        )
        return result.scalar_one()

class IOSSubFunctionRepository(BaseRepository[IOSSubFunction]):
    """iOS App 子功能仓储类"""

    def __init__(self, session: AsyncSession):
        super().__init__(IOSSubFunction, session)

    async def get_by_identifier(self, identifier: str) -> Optional[IOSSubFunction]:
        """
        根据标识符获取 iOS App 子功能

        Args:
            identifier: iOS App 子功能标识符 (ISF-xxx)

        Returns:
            Optional[IOSSubFunction]: iOS App 子功能实例或 None
        """
        result = await self.session.execute(
            select(IOSSubFunction)
            .options(selectinload(IOSSubFunction.function))
            .options(selectinload(IOSSubFunction.ios_tests))
            .where(IOSSubFunction.identifier == identifier)
        )
        return result.scalar_one_or_none()

    async def get_by_id_with_relations(self, id: UUID) -> Optional[IOSSubFunction]:
        """根据 ID 获取 iOS App 子功能（包含关联数据）"""
        result = await self.session.execute(
            select(IOSSubFunction)
            .options(selectinload(IOSSubFunction.function))
            .options(selectinload(IOSSubFunction.ios_tests))
            .options(selectinload(IOSSubFunction.project))
            .where(IOSSubFunction.id == id)
        )
        return result.scalar_one_or_none()

    async def get_by_function(
        self,
        function_id: UUID,
        offset: int = 0,
        limit: int = 20,
        search: Optional[str] = None,
    ) -> tuple[list[IOSSubFunction], int]:
        """
        获取功能下的子功能列表

        Args:
            function_id: 功能 ID
            offset: 偏移量
            limit: 限制数量
            search: 搜索关键词

        Returns:
            tuple[list[IOSSubFunction], int]: 子功能列表和总数
        """
        query = select(IOSSubFunction).where(IOSSubFunction.function_id == function_id)

        # 搜索过滤
        if search:
            query = query.where(
                (IOSSubFunction.name.ilike(f"%{search}%")) |
                (IOSSubFunction.identifier.ilike(f"%{search}%")) |
                (IOSSubFunction.display_name.ilike(f"%{search}%"))
            )

        # 获取总数
        count_result = await self.session.execute(
            select(func.count()).select_from(query.subquery())
        )
        total = count_result.scalar_one()

        # 获取数据
        query = query.order_by(IOSSubFunction.sort_order, IOSSubFunction.created_at.desc())
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
    ) -> tuple[list[IOSSubFunction], int]:
        """
        获取多个功能下的子功能列表（用于按文件夹查询）

        Args:
            function_ids: 功能 ID 列表
            offset: 偏移量
            limit: 限制数量
            search: 搜索关键词

        Returns:
            tuple[list[IOSSubFunction], int]: 子功能列表和总数
        """
        query = select(IOSSubFunction).where(IOSSubFunction.function_id.in_(function_ids))

        # 搜索过滤
        if search:
            query = query.where(
                (IOSSubFunction.name.ilike(f"%{search}%")) |
                (IOSSubFunction.identifier.ilike(f"%{search}%")) |
                (IOSSubFunction.display_name.ilike(f"%{search}%"))
            )

        # 获取总数
        count_result = await self.session.execute(
            select(func.count()).select_from(query.subquery())
        )
        total = count_result.scalar_one()

        # 获取数据
        query = query.order_by(IOSSubFunction.sort_order, IOSSubFunction.created_at.desc())
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
    ) -> tuple[list[IOSSubFunction], int]:
        """
        获取项目下的子功能列表

        Args:
            project_id: 项目 ID
            offset: 偏移量
            limit: 限制数量
            search: 搜索关键词

        Returns:
            tuple[list[IOSSubFunction], int]: 子功能列表和总数
        """
        query = select(IOSSubFunction).where(IOSSubFunction.project_id == project_id)

        # 搜索过滤
        if search:
            query = query.where(
                (IOSSubFunction.name.ilike(f"%{search}%")) |
                (IOSSubFunction.identifier.ilike(f"%{search}%")) |
                (IOSSubFunction.display_name.ilike(f"%{search}%"))
            )

        # 获取总数
        count_result = await self.session.execute(
            select(func.count()).select_from(query.subquery())
        )
        total = count_result.scalar_one()

        # 获取数据
        query = query.order_by(IOSSubFunction.sort_order, IOSSubFunction.created_at.desc())
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
    ) -> tuple[list[IOSSubFunction], int]:
        """
        获取文件夹下的子功能列表

        Args:
            folder_id: 文件夹 ID
            offset: 偏移量
            limit: 限制数量
            search: 搜索关键词

        Returns:
            tuple[list[IOSSubFunction], int]: 子功能列表和总数
        """
        query = select(IOSSubFunction).where(IOSSubFunction.folder_id == folder_id)

        # 搜索过滤
        if search:
            query = query.where(
                (IOSSubFunction.name.ilike(f"%{search}%")) |
                (IOSSubFunction.identifier.ilike(f"%{search}%")) |
                (IOSSubFunction.display_name.ilike(f"%{search}%"))
            )

        # 获取总数
        count_result = await self.session.execute(
            select(func.count()).select_from(query.subquery())
        )
        total = count_result.scalar_one()

        # 获取数据
        query = query.order_by(IOSSubFunction.sort_order, IOSSubFunction.created_at.desc())
        query = query.offset(offset).limit(limit)

        result = await self.session.execute(query)
        items = list(result.scalars().all())

        return items, total

    async def get_next_identifier(self, project_id: UUID) -> str:
        """
        生成下一个 iOS App 子功能标识符

        格式: ISF-1001, ISF-1002, ...

        通过 PG advisory 事务锁串行化"同 project 同资源"的并发写入，
        提交/回滚时锁自动释放，从根本上避免唯一约束冲突。

        Args:
            project_id: 项目 ID

        Returns:
            str: 下一个标识符
        """
        await self._acquire_xact_lock(f"isf_identifier:{project_id}")

        numeric_part = cast(
            func.regexp_replace(IOSSubFunction.identifier, r"^\D+", ""),
            Integer,
        )
        result = await self.session.execute(
            select(func.max(numeric_part))
            .where(IOSSubFunction.project_id == project_id)
            .where(IOSSubFunction.identifier.op("~")(r"^ISF-\d+$"))
        )
        max_number = result.scalar_one_or_none()
        next_number = (max_number or 1000) + 1

        # 检查生成的标识符是否已存在（可能被其他会话创建）
        from sqlalchemy import exists
        for _ in range(100):
            candidate = f"ISF-{next_number}"
            exists_result = await self.session.execute(
                select(exists().where(IOSSubFunction.identifier == candidate))
            )
            if not exists_result.scalar():
                return candidate
            next_number += 1

        import time
        return f"ISF-{int(time.time())}"

    async def get_count_by_project(self, project_id: UUID) -> int:
        """获取项目下的子功能总数"""
        result = await self.session.execute(
            select(func.count()).select_from(IOSSubFunction).where(
                IOSSubFunction.project_id == project_id
            )
        )
        return result.scalar_one()
