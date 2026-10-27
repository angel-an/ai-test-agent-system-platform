"""
API 测试仓储

处理 API 测试相关的数据库操作
"""

from datetime import datetime, timezone
from typing import Iterable, Optional
from uuid import UUID

from sqlalchemy import Integer, and_, cast, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.repositories.base import BaseRepository
from app.models.api_test import APITest, APITestRun, APITestResult

# type: ignore  MC80OmFIVnBZMlhscm9ua3VMazZUakpvU0E9PTo2NTQzNzJhMg==

# API run 被中断时的 error_message 措辞。
# 必须和"用例失败"在字面上就能区分：否则统计时会把进程崩溃当成被测系统问题。
API_RUN_INTERRUPTED_MESSAGE = "执行被中断（服务进程退出或重启），非用例失败"


def next_api_run_identifier(existing_identifiers: Iterable[str], date_str: str) -> str:
    """从「当天已用编号」推算下一个 ``ATR-YYYYMMDD-NNN``。

    纯函数（repo 负责取数 + 加锁），便于用表驱动用例锁住编号口径。

    只认严格匹配 ``ATR-<date_str>-<数字>`` 的标识符；不合格式的忽略而不是报错——
    历史上存在过 ``SR-EP-*`` 之类的异构值混在同一列。
    """
    prefix = f"ATR-{date_str}-"
    used: set[int] = set()
    for identifier in existing_identifiers:
        if not identifier or not identifier.startswith(prefix):
            continue
        tail = identifier[len(prefix):]
        if not tail.isdigit():
            continue
        used.add(int(tail))
    number = max(used) + 1 if used else 1
    return f"{prefix}{number:03d}"


class APITestRepository(BaseRepository[APITest]):
    """API 测试仓储类"""

    def __init__(self, session: AsyncSession):
        super().__init__(APITest, session)

    async def get_by_identifier(self, identifier: str) -> Optional[APITest]:
        """
        根据标识符获取 API 测试

        Args:
            identifier: API 测试标识符 (AT-xxx)

        Returns:
            Optional[APITest]: API 测试实例或 None
        """
        result = await self.session.execute(
            select(APITest)
            .options(selectinload(APITest.test_runs))
            .where(APITest.identifier == identifier)
        )
        return result.scalar_one_or_none()

    async def get_by_id_with_relations(self, id: UUID) -> Optional[APITest]:
        """根据 ID 获取 API 测试（包含关联数据）"""
        result = await self.session.execute(
            select(APITest)
            .options(selectinload(APITest.test_case))
            .options(selectinload(APITest.project))
            .options(selectinload(APITest.test_runs))
            .where(APITest.id == id)
        )
        return result.scalar_one_or_none()

    async def get_by_project(
        self,
        project_id: UUID,
        offset: int = 0,
        limit: int = 20,
        search: Optional[str] = None,
        script_format: Optional[str] = None,
    ) -> tuple[list[APITest], int]:
        """
        获取项目下的 API 测试列表

        Args:
            project_id: 项目 ID
            offset: 偏移量
            limit: 限制数量
            search: 搜索关键词
            script_format: 脚本格式过滤

        Returns:
            tuple[list[APITest], int]: API 测试列表和总数
        """
        query = select(APITest).where(APITest.project_id == project_id)

        # 搜索过滤
        if search:
            query = query.where(
                (APITest.name.ilike(f"%{search}%")) |
                (APITest.identifier.ilike(f"%{search}%")) |
                (APITest.description.ilike(f"%{search}%"))
            )
# type: ignore  MS80OmFIVnBZMlhscm9ua3VMazZUakpvU0E9PTo2NTQzNzJhMg==

        # 格式过滤
        if script_format:
            query = query.where(APITest.script_format == script_format)

        # 获取总数
        count_result = await self.session.execute(
            select(func.count()).select_from(query.subquery())
        )
        total = count_result.scalar_one()

        # 获取数据
        query = query.order_by(APITest.created_at.desc())
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
    ) -> tuple[list[APITest], int]:
        """
        获取文件夹下的 API 测试列表

        Args:
            folder_id: 文件夹 ID
            offset: 偏移量
            limit: 限制数量
            search: 搜索关键词

        Returns:
            tuple[list[APITest], int]: API 测试列表和总数
        """
        query = select(APITest).where(APITest.folder_id == folder_id)

        # 搜索过滤
        if search:
            query = query.where(
                (APITest.name.ilike(f"%{search}%")) |
                (APITest.identifier.ilike(f"%{search}%")) |
                (APITest.description.ilike(f"%{search}%"))
            )

        # 获取总数
        count_result = await self.session.execute(
            select(func.count()).select_from(query.subquery())
        )
        total = count_result.scalar_one()

        # 获取数据
        query = query.order_by(APITest.created_at.desc())
        query = query.offset(offset).limit(limit)

        result = await self.session.execute(query)
        items = list(result.scalars().all())

        return items, total

    async def get_next_identifier(self, project_id: UUID) -> str:
        """
        生成下一个 API 测试标识符

        格式: AT-1001, AT-1002, ...

        identifier 在数据库层是全局唯一约束（见 APITest.identifier unique=True），
        因此编号必须在全局范围内递增，而不能按项目各自起号——否则两个项目都会
        从 AT-1001 起步，第二个项目插入时撞全局唯一约束。

        通过 PG advisory 事务锁串行化全局并发写入（key 与 project 无关），
        提交/回滚时自动释放。使用 MAX 而非 COUNT，避免删除记录后编号冲突。

        Args:
            project_id: 项目 ID（保留参数以兼容调用方，不再参与编号计算）

        Returns:
            str: 标识符 (AT-xxx)
        """
        await self._acquire_xact_lock("at_identifier:global")

        numeric_part = cast(
            func.regexp_replace(APITest.identifier, r"^\D+", ""),
            Integer,
        )
        result = await self.session.execute(
            select(func.max(numeric_part))
            .where(APITest.identifier.op("~")(r"^AT-\d+$"))
        )
        max_number = result.scalar_one_or_none()
        next_number = (max_number or 1000) + 1

        return f"AT-{next_number}"

class APITestRunRepository(BaseRepository[APITestRun]):
    """API 测试运行仓储类"""
# fmt: off  Mi80OmFIVnBZMlhscm9ua3VMazZUakpvU0E9PTo2NTQzNzJhMg==

    def __init__(self, session: AsyncSession):
        super().__init__(APITestRun, session)

    async def get_by_identifier(self, identifier: str) -> Optional[APITestRun]:
        """根据标识符获取 API 测试运行"""
        result = await self.session.execute(
            select(APITestRun)
            .options(selectinload(APITestRun.test_results))
            .options(selectinload(APITestRun.api_test))
            .where(APITestRun.identifier == identifier)
        )
        return result.scalar_one_or_none()

    async def get_by_api_test(
        self,
        api_test_id: UUID,
        offset: int = 0,
        limit: int = 20,
    ) -> tuple[list[APITestRun], int]:
        """
        获取 API 测试的运行历史

        Args:
            api_test_id: API 测试 ID
            offset: 偏移量
            limit: 限制数量

        Returns:
            tuple[list[APITestRun], int]: 运行列表和总数
        """
        # 获取总数
        count_result = await self.session.execute(
            select(func.count())
            .select_from(APITestRun)
            .where(APITestRun.api_test_id == api_test_id)
        )
        total = count_result.scalar_one()

        # 获取数据
        result = await self.session.execute(
            select(APITestRun)
            .where(APITestRun.api_test_id == api_test_id)
            .order_by(APITestRun.created_at.desc())
            .offset(offset)
            .limit(limit)
        )
        items = list(result.scalars().all())

        return items, total

    async def get_next_identifier(self, api_test_id: UUID | None = None) -> str:
        """生成下一个运行标识符

        格式: ATR-YYYYMMDD-NNN

        ⚠️ 编号粒度是**全局按日期**，不是按 api_test。

        历史实现按 ``api_test_id`` 计数（锁 key 与 MAX 都带 api_test_id），
        于是同一项目下 N 个 api_test 每天各自从 001 起号：`ATR-20260824-001`
        被 3 条不同记录共用，实测全库 **33 组重号**。追溯时只凭编号无法定位
        是哪一次执行，编号等于失效。web 侧 ``TR-`` 一直全局递增，这里对齐。

        ``api_test_id`` 参数保留只为兼容调用方，不再参与编号计算。
        """
        today = datetime.now(timezone.utc).date()
        date_str = today.strftime("%Y%m%d")
        prefix = f"ATR-{date_str}-"
# pragma: no cover  My80OmFIVnBZMlhscm9ua3VMazZUakpvU0E9PTo2NTQzNzJhMg==

        # 锁粒度必须与编号粒度一致：按日期全局串行。
        # 仍按 api_test 加锁的话，两个 api_test 会同时读到同一个 max 而撞号。
        await self._acquire_xact_lock(f"atr_identifier:{date_str}")

        result = await self.session.execute(
            select(APITestRun.identifier).where(APITestRun.identifier.like(f"{prefix}%"))
        )
        return next_api_run_identifier(result.scalars().all(), date_str)

class APITestResultRepository(BaseRepository[APITestResult]):
    """API 测试结果仓储类"""

    def __init__(self, session: AsyncSession):
        super().__init__(APITestResult, session)

    async def get_by_test_run(
        self,
        test_run_id: UUID,
        offset: int = 0,
        limit: int = 50,
    ) -> tuple[list[APITestResult], int]:
        """
        获取测试运行的结果列表

        Args:
            test_run_id: 测试运行 ID
            offset: 偏移量
            limit: 限制数量

        Returns:
            tuple[list[APITestResult], int]: 结果列表和总数
        """
        # 获取总数
        count_result = await self.session.execute(
            select(func.count())
            .select_from(APITestResult)
            .where(APITestResult.test_run_id == test_run_id)
        )
        total = count_result.scalar_one()

        # 获取数据
        result = await self.session.execute(
            select(APITestResult)
            .where(APITestResult.test_run_id == test_run_id)
            .order_by(APITestResult.created_at)
            .offset(offset)
            .limit(limit)
        )
        items = list(result.scalars().all())

        return items, total

    async def get_by_status(
        self,
        test_run_id: UUID,
        status: str,
    ) -> list[APITestResult]:
        """根据状态获取测试结果"""
        result = await self.session.execute(
            select(APITestResult)
            .where(
                and_(
                    APITestResult.test_run_id == test_run_id,
                    APITestResult.status == status
                )
            )
        )
        return list(result.scalars().all())
