"""定时运行服务"""

import asyncio
from typing import Any, Dict, List, Optional
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.config.database import async_session_factory
from app.config.minio_client import MinIOClient
from app.models.scheduled_run import ScheduledRun, ScheduledRunExecution
from app.repositories.scheduled_run_repo import (
    ScheduledRunRepository,
    ScheduledRunExecutionRepository,
)
from app.repositories.project_repo import ProjectRepository


class ScheduledRunService:
    def __init__(self, session: AsyncSession):
        self.session = session
        self.repo = ScheduledRunRepository(session)
        self.exec_repo = ScheduledRunExecutionRepository(session)
        self.project_repo = ProjectRepository(session)

    async def _get_project_id(self, project_identifier: str) -> UUID:
        project = await self.project_repo.get_by_identifier(project_identifier)
        if not project:
            raise ValueError(f"项目不存在: {project_identifier}")
        return project.id

    async def list_scheduled_runs(
        self, project_identifier: str, page: int = 1, page_size: int = 30
    ) -> dict:
        project_id = await self._get_project_id(project_identifier)
        offset = (page - 1) * page_size
        items, total = await self.repo.get_by_project(project_id, offset, page_size)
        return {
            "items": [_run_to_dict(r) for r in items],
            "total": total,
            "page": page,
            "page_size": page_size,
        }

    async def create_scheduled_run(
        self,
        project_identifier: str,
        name: str,
        cron_expression: str,
        api_endpoint_ids: List[str],
        description: Optional[str] = None,
        execution_config: Optional[Dict[str, Any]] = None,
        timezone: str = "Asia/Shanghai",
    ) -> dict:
        project_id = await self._get_project_id(project_identifier)
        identifier = await self.repo.get_next_identifier(project_id)
        run = await self.repo.create(
            project_id=project_id,
            identifier=identifier,
            name=name,
            description=description,
            api_endpoint_ids=api_endpoint_ids,
            execution_config=execution_config or {},
            cron_expression=cron_expression,
            timezone=timezone,
            is_active=True,
        )
        await self.session.commit()
        return _run_to_dict(run)

    async def update_scheduled_run(
        self, project_identifier: str, run_id: str, **kwargs
    ) -> dict:
        run = await self.repo.get_by_id(UUID(run_id))
        if not run:
            raise ValueError(f"定时运行不存在: {run_id}")
        run = await self.repo.update(run, **kwargs)
        await self.session.commit()
        return _run_to_dict(run)

    async def delete_scheduled_run(self, project_identifier: str, run_id: str) -> None:
        run = await self.repo.get_by_id(UUID(run_id))
        if not run:
            raise ValueError(f"定时运行不存在: {run_id}")
        await self.repo.delete(run)
        await self.session.commit()

    async def list_executions(
        self, project_identifier: str, run_id: str, page: int = 1, page_size: int = 20
    ) -> dict:
        offset = (page - 1) * page_size
        items, total = await self.exec_repo.get_by_scheduled_run(
            UUID(run_id), offset, page_size
        )
        return {
            "items": [_exec_to_dict(e) for e in items],
            "total": total,
            "page": page,
            "page_size": page_size,
        }

    async def get_execution(self, execution_id: str) -> dict:
        exec_ = await self.exec_repo.get_by_id(UUID(execution_id))
        if not exec_:
            raise ValueError(f"执行记录不存在: {execution_id}")
        return _exec_to_dict(exec_)


def _run_to_dict(r: ScheduledRun) -> dict:
    return {
        "id": str(r.id),
        "identifier": r.identifier,
        "name": r.name,
        "description": r.description,
        "api_endpoint_ids": r.api_endpoint_ids,
        "execution_config": r.execution_config,
        "cron_expression": r.cron_expression,
        "timezone": r.timezone,
        "is_active": r.is_active,
        "last_executed_at": r.last_executed_at,
        "created_at": r.created_at.isoformat() if r.created_at else None,
        "updated_at": r.updated_at.isoformat() if r.updated_at else None,
    }


def _exec_to_dict(e: ScheduledRunExecution) -> dict:
    return {
        "id": str(e.id),
        "identifier": e.identifier,
        "scheduled_run_id": str(e.scheduled_run_id),
        "status": e.status,
        "api_test_run_ids": e.api_test_run_ids,
        "total_tests": e.total_tests,
        "passed_tests": e.passed_tests,
        "failed_tests": e.failed_tests,
        "error_message": e.error_message,
        "report_path": e.report_path,
        "created_at": e.created_at.isoformat() if e.created_at else None,
    }
