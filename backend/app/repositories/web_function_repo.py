"""
Web 功能仓储

处理 Web 功能和子功能相关的数据库操作
"""

from typing import Optional
from uuid import UUID

from sqlalchemy import Integer, cast, func, not_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.repositories.base import BaseRepository
from app.models.web_function import WebFunction, WebSubFunction

class WebFunctionRepository(BaseRepository[WebFunction]):
    """Web 功能仓储类"""

    def __init__(self, session: AsyncSession):
        super().__init__(WebFunction, session)

    async def get_by_project(
        self,
        project_id: UUID,
        offset: int = 0,
        limit: int = 20,
        search: Optional[str] = None,
    ) -> tuple[list[WebFunction], int]:
        """
        获取项目下的 Web 功能列表

        Args:
            project_id: 项目 ID
            offset: 偏移量
            limit: 限制数量
            search: 搜索关键词

        Returns:
            tuple[list[WebFunction], int]: Web 功能列表和总数
        """
        query = select(WebFunction).where(WebFunction.project_id == project_id)

        # 搜索过滤
        if search:
            query = query.where(
                (WebFunction.name.ilike(f"%{search}%")) |
                (WebFunction.identifier.ilike(f"%{search}%")) |
                (WebFunction.display_name.ilike(f"%{search}%"))
            )

        # 获取总数
        count_result = await self.session.execute(
            select(func.count()).select_from(query.subquery())
        )
        total = count_result.scalar_one()

        # 获取数据
        query = query.order_by(WebFunction.sort_order, WebFunction.created_at.desc())
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
    ) -> tuple[list[WebFunction], int]:
        """
        获取文件夹下的 Web 功能列表

        Args:
            folder_id: 文件夹 ID
            offset: 偏移量
            limit: 限制数量
            search: 搜索关键词

        Returns:
            tuple[list[WebFunction], int]: Web 功能列表和总数
        """
        query = select(WebFunction).where(WebFunction.folder_id == folder_id)

        # 搜索过滤
        if search:
            query = query.where(
                (WebFunction.name.ilike(f"%{search}%")) |
                (WebFunction.identifier.ilike(f"%{search}%")) |
                (WebFunction.display_name.ilike(f"%{search}%"))
            )

        # 获取总数
        count_result = await self.session.execute(
            select(func.count()).select_from(query.subquery())
        )
        total = count_result.scalar_one()

        # 获取数据
        query = query.order_by(WebFunction.sort_order, WebFunction.created_at.desc())
        query = query.offset(offset).limit(limit)

        result = await self.session.execute(query)
        items = list(result.scalars().all())

        return items, total

    async def get_by_identifier(self, identifier: str) -> Optional[WebFunction]:
        """
        根据标识符获取 Web 功能

        Args:
            identifier: Web 功能标识符 (WF-xxx)

        Returns:
            Optional[WebFunction]: Web 功能实例或 None
        """
        result = await self.session.execute(
            select(WebFunction)
            .options(selectinload(WebFunction.sub_functions))
            .where(WebFunction.identifier == identifier)
        )
        return result.scalar_one_or_none()

    async def get_by_id_with_relations(self, id: UUID) -> Optional[WebFunction]:
        """根据 ID 获取 Web 功能（包含关联数据）"""
        result = await self.session.execute(
            select(WebFunction)
            .options(selectinload(WebFunction.sub_functions))
            .options(selectinload(WebFunction.project))
            .where(WebFunction.id == id)
        )
        return result.scalar_one_or_none()

    async def count_by_project(self, project_id: UUID) -> int:
        """统计项目下的 Web 功能数量"""
        result = await self.session.execute(
            select(func.count()).select_from(WebFunction).where(
                WebFunction.project_id == project_id
            )
        )
        return result.scalar_one()

    async def get_next_identifier(self, project_id: UUID) -> str:
        """
        生成下一个 Web 功能标识符

        格式: WF-1001, WF-1002, ...

        通过 PG advisory 事务锁串行化并发写入。

        Args:
            project_id: 项目 ID（保留参数以保持接口兼容，但标识符全局唯一）

        Returns:
            str: 标识符 (WF-xxx)
        """
        await self._acquire_xact_lock("wf_identifier:global")

        numeric_part = cast(
            func.regexp_replace(WebFunction.identifier, r"^\D+", ""),
            Integer,
        )
        result = await self.session.execute(
            select(func.max(numeric_part))
            .where(WebFunction.identifier.op("~")(r"^WF-\d+$"))
        )
        max_number = result.scalar_one_or_none()
        next_number = (max_number or 1000) + 1

        return f"WF-{next_number}"


class WebSubFunctionRepository(BaseRepository[WebSubFunction]):
    """Web 子功能仓储类"""

    def __init__(self, session: AsyncSession):
        super().__init__(WebSubFunction, session)

    async def get_by_identifier(self, identifier: str) -> Optional[WebSubFunction]:
        """
        根据标识符获取 Web 子功能

        Args:
            identifier: Web 子功能标识符 (WSF-xxx)

        Returns:
            Optional[WebSubFunction]: Web 子功能实例或 None
        """
        result = await self.session.execute(
            select(WebSubFunction)
            .options(selectinload(WebSubFunction.function))
            .options(selectinload(WebSubFunction.web_tests))
            .where(WebSubFunction.identifier == identifier)
        )
        return result.scalar_one_or_none()

    async def get_by_id_with_relations(self, id: UUID) -> Optional[WebSubFunction]:
        """根据 ID 获取 Web 子功能（包含关联数据）"""
        result = await self.session.execute(
            select(WebSubFunction)
            .options(selectinload(WebSubFunction.function))
            .options(selectinload(WebSubFunction.web_tests))
            .options(selectinload(WebSubFunction.project))
            .where(WebSubFunction.id == id)
        )
        return result.scalar_one_or_none()

    async def get_by_function(
        self,
        function_id: UUID,
        offset: int = 0,
        limit: int = 20,
        search: Optional[str] = None,
    ) -> tuple[list[WebSubFunction], int]:
        """
        获取功能下的子功能列表

        Args:
            function_id: 功能 ID
            offset: 偏移量
            limit: 限制数量
            search: 搜索关键词

        Returns:
            tuple[list[WebSubFunction], int]: 子功能列表和总数
        """
        # 排除重复的子功能：创客中心(WSF-1035) 和 忠诚度计划(WSF-1030)
        query = select(WebSubFunction).where(
            WebSubFunction.function_id == function_id,
            not_(WebSubFunction.identifier.in_(["WSF-1035", "WSF-1030"]))
        )

        # 搜索过滤
        if search:
            query = query.where(
                (WebSubFunction.name.ilike(f"%{search}%")) |
                (WebSubFunction.identifier.ilike(f"%{search}%")) |
                (WebSubFunction.display_name.ilike(f"%{search}%"))
            )

        # 获取总数
        count_result = await self.session.execute(
            select(func.count()).select_from(query.subquery())
        )
        total = count_result.scalar_one()

        # 获取数据
        query = query.order_by(WebSubFunction.sort_order, WebSubFunction.created_at.desc())
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
    ) -> tuple[list[WebSubFunction], int]:
        """
        获取多个功能下的子功能列表（用于按文件夹查询）

        Args:
            function_ids: 功能 ID 列表
            offset: 偏移量
            limit: 限制数量
            search: 搜索关键词

        Returns:
            tuple[list[WebSubFunction], int]: 子功能列表和总数
        """
        # 排除重复的子功能：创客中心(WSF-1035) 和 忠诚度计划(WSF-1030)
        query = select(WebSubFunction).where(
            WebSubFunction.function_id.in_(function_ids),
            not_(WebSubFunction.identifier.in_(["WSF-1035", "WSF-1030"]))
        )

        # 搜索过滤
        if search:
            query = query.where(
                (WebSubFunction.name.ilike(f"%{search}%")) |
                (WebSubFunction.identifier.ilike(f"%{search}%")) |
                (WebSubFunction.display_name.ilike(f"%{search}%"))
            )

        # 获取总数
        count_result = await self.session.execute(
            select(func.count()).select_from(query.subquery())
        )
        total = count_result.scalar_one()

        # 获取数据
        query = query.order_by(WebSubFunction.sort_order, WebSubFunction.created_at.desc())
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
    ) -> tuple[list[WebSubFunction], int]:
        """
        获取项目下的子功能列表

        Args:
            project_id: 项目 ID
            offset: 偏移量
            limit: 限制数量
            search: 搜索关键词

        Returns:
            tuple[list[WebSubFunction], int]: 子功能列表和总数
        """
        # 排除重复的子功能：创客中心(WSF-1035) 和 忠诚度计划(WSF-1030)
        query = select(WebSubFunction).where(
            WebSubFunction.project_id == project_id,
            not_(WebSubFunction.identifier.in_(["WSF-1035", "WSF-1030"]))
        )

        # 搜索过滤
        if search:
            query = query.where(
                (WebSubFunction.name.ilike(f"%{search}%")) |
                (WebSubFunction.identifier.ilike(f"%{search}%")) |
                (WebSubFunction.display_name.ilike(f"%{search}%"))
            )

        # 获取总数
        count_result = await self.session.execute(
            select(func.count()).select_from(query.subquery())
        )
        total = count_result.scalar_one()

        # 获取数据
        query = query.order_by(WebSubFunction.sort_order, WebSubFunction.created_at.desc())
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
    ) -> tuple[list[WebSubFunction], int]:
        """
        获取文件夹下的子功能列表

        Args:
            folder_id: 文件夹 ID
            offset: 偏移量
            limit: 限制数量
            search: 搜索关键词

        Returns:
            tuple[list[WebSubFunction], int]: 子功能列表和总数
        """
        # 排除重复的子功能：创客中心(WSF-1035) 和 忠诚度计划(WSF-1030)
        query = select(WebSubFunction).where(
            WebSubFunction.folder_id == folder_id,
            not_(WebSubFunction.identifier.in_(["WSF-1035", "WSF-1030"]))
        )

        # 搜索过滤
        if search:
            query = query.where(
                (WebSubFunction.name.ilike(f"%{search}%")) |
                (WebSubFunction.identifier.ilike(f"%{search}%")) |
                (WebSubFunction.display_name.ilike(f"%{search}%"))
            )

        # 获取总数
        count_result = await self.session.execute(
            select(func.count()).select_from(query.subquery())
        )
        total = count_result.scalar_one()

        # 获取数据
        query = query.order_by(WebSubFunction.sort_order, WebSubFunction.created_at.desc())
        query = query.offset(offset).limit(limit)

        result = await self.session.execute(query)
        items = list(result.scalars().all())

        return items, total

    async def get_next_identifier(self, project_id: UUID) -> str:
        """
        生成下一个 Web 子功能标识符

        格式: WSF-1001, WSF-1002, ...

        通过 PG advisory 事务锁串行化并发写入。

        Args:
            project_id: 项目 ID（保留参数以保持接口兼容，但标识符全局唯一）

        Returns:
            str: 标识符 (WSF-xxx)
        """
        await self._acquire_xact_lock("wsf_identifier:global")

        numeric_part = cast(
            func.regexp_replace(WebSubFunction.identifier, r"^\D+", ""),
            Integer,
        )
        result = await self.session.execute(
            select(func.max(numeric_part))
            .where(WebSubFunction.identifier.op("~")(r"^WSF-\d+$"))
        )
        max_number = result.scalar_one_or_none()
        next_number = (max_number or 1000) + 1

        return f"WSF-{next_number}"
