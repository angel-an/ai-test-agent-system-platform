"""定时运行仓储"""

from typing import Optional
from uuid import UUID

from sqlalchemy import Integer, cast, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.repositories.base import BaseRepository
from app.models.scheduled_run import ScheduledRun, ScheduledRunExecution


class ScheduledRunRepository(BaseRepository[ScheduledRun]):
    def __init__(self, session: AsyncSession):
        super().__init__(ScheduledRun, session)

    async def get_by_project(
        self, project_id: UUID, offset: int = 0, limit: int = 30
    ) -> tuple[list[ScheduledRun], int]:
        count = await self.session.execute(
            select(func.count()).select_from(ScheduledRun)
            .where(ScheduledRun.project_id == project_id)
        )
        total = count.scalar_one()

        result = await self.session.execute(
            select(ScheduledRun)
            .where(ScheduledRun.project_id == project_id)
            .order_by(ScheduledRun.created_at.desc())
            .offset(offset).limit(limit)
        )
        return list(result.scalars().all()), total

    async def get_all_active(self) -> list[ScheduledRun]:
        result = await self.session.execute(
            select(ScheduledRun).where(ScheduledRun.is_active == True)
        )
        return list(result.scalars().all())

    async def get_next_identifier(self, project_id: UUID) -> str:
        await self._acquire_xact_lock(f"sr_identifier:{project_id}")
        numeric_part = cast(
            func.regexp_replace(ScheduledRun.identifier, r"^\D+", ""), Integer
        )
        result = await self.session.execute(
            select(func.max(numeric_part))
            .where(ScheduledRun.project_id == project_id)
            .where(ScheduledRun.identifier.op("~")(r"^SR-\d+$"))
        )
        max_num = result.scalar_one_or_none() or 1000
        return f"SR-{max_num + 1}"


class ScheduledRunExecutionRepository(BaseRepository[ScheduledRunExecution]):
    def __init__(self, session: AsyncSession):
        super().__init__(ScheduledRunExecution, session)

    async def get_by_scheduled_run(
        self, scheduled_run_id: UUID, offset: int = 0, limit: int = 20
    ) -> tuple[list[ScheduledRunExecution], int]:
        count = await self.session.execute(
            select(func.count()).select_from(ScheduledRunExecution)
            .where(ScheduledRunExecution.scheduled_run_id == scheduled_run_id)
        )
        total = count.scalar_one()

        result = await self.session.execute(
            select(ScheduledRunExecution)
            .where(ScheduledRunExecution.scheduled_run_id == scheduled_run_id)
            .order_by(ScheduledRunExecution.created_at.desc())
            .offset(offset).limit(limit)
        )
        return list(result.scalars().all()), total

    async def get_next_identifier(self, scheduled_run_id: UUID) -> str:
        result = await self.session.execute(
            select(func.count()).select_from(ScheduledRunExecution)
            .where(ScheduledRunExecution.scheduled_run_id == scheduled_run_id)
        )
        count = result.scalar_one()
        return f"SRE-{count + 1:04d}"
