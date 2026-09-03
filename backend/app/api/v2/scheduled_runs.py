"""定时运行 API"""

from typing import Any, Dict, List, Optional
from uuid import UUID

import asyncio

from fastapi import APIRouter, Body, HTTPException, Query
from pydantic import BaseModel

from app.api.deps import DbSessionDep, PaginationDep
from app.schemas.common import SuccessResponse
from app.services.scheduled_run_service import ScheduledRunService
from app.services.scheduler import add_job, remove_job
from app.repositories.scheduled_run_repo import ScheduledRunRepository

router = APIRouter(prefix="/projects/{project_identifier}/scheduled-runs")


class CreateScheduledRunRequest(BaseModel):
    name: str
    cron_expression: str
    api_endpoint_ids: List[str]
    description: Optional[str] = None
    execution_config: Optional[Dict[str, Any]] = None
    timezone: str = "Asia/Shanghai"


class UpdateScheduledRunRequest(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    cron_expression: Optional[str] = None
    api_endpoint_ids: Optional[List[str]] = None
    execution_config: Optional[Dict[str, Any]] = None
    timezone: Optional[str] = None
    is_active: Optional[bool] = None


@router.get("", response_model=SuccessResponse, summary="获取定时运行列表")
async def list_scheduled_runs(
    project_identifier: str,
    db: DbSessionDep,
    pagination: PaginationDep,
):
    service = ScheduledRunService(db)
    result = await service.list_scheduled_runs(
        project_identifier, pagination.p, pagination.page_size
    )
    return SuccessResponse(data=result)


@router.post("", response_model=SuccessResponse, summary="创建定时运行")
async def create_scheduled_run(
    project_identifier: str,
    body: CreateScheduledRunRequest,
    db: DbSessionDep,
):
    service = ScheduledRunService(db)
    result = await service.create_scheduled_run(
        project_identifier=project_identifier,
        name=body.name,
        cron_expression=body.cron_expression,
        api_endpoint_ids=body.api_endpoint_ids,
        description=body.description,
        execution_config=body.execution_config,
        timezone=body.timezone,
    )
    # 注册到 scheduler
    repo = ScheduledRunRepository(db)
    run = await repo.get_by_id(UUID(result["id"]))
    if run:
        add_job(run)
    return SuccessResponse(data=result)


@router.patch("/{run_id}", response_model=SuccessResponse, summary="更新定时运行")
async def update_scheduled_run(
    project_identifier: str,
    run_id: str,
    body: UpdateScheduledRunRequest,
    db: DbSessionDep,
):
    service = ScheduledRunService(db)
    update_data = body.model_dump(exclude_none=True)
    result = await service.update_scheduled_run(project_identifier, run_id, **update_data)
    # 重新注册 job
    repo = ScheduledRunRepository(db)
    run = await repo.get_by_id(UUID(run_id))
    if run:
        add_job(run)
    return SuccessResponse(data=result)


@router.delete("/{run_id}", response_model=SuccessResponse, summary="删除定时运行")
async def delete_scheduled_run(
    project_identifier: str,
    run_id: str,
    db: DbSessionDep,
):
    service = ScheduledRunService(db)
    await service.delete_scheduled_run(project_identifier, run_id)
    remove_job(run_id)
    return SuccessResponse(data=None)


@router.post("/{run_id}/trigger", response_model=SuccessResponse, summary="手动触发定时运行")
async def trigger_scheduled_run(
    project_identifier: str,
    run_id: str,
    db: DbSessionDep,
):
    from app.services.scheduler import _execute_scheduled_run
    repo = ScheduledRunRepository(db)
    run = await repo.get_by_id(UUID(run_id))
    if not run:
        raise HTTPException(status_code=404, detail="定时运行不存在")
    asyncio.create_task(_execute_scheduled_run(run_id))
    return SuccessResponse(data={"run_id": run_id, "status": "triggered"})


@router.get("/{run_id}/executions", response_model=SuccessResponse, summary="获取执行历史")
async def list_executions(
    project_identifier: str,
    run_id: str,
    db: DbSessionDep,
    pagination: PaginationDep,
):
    service = ScheduledRunService(db)
    result = await service.list_executions(
        project_identifier, run_id, pagination.p, pagination.page_size
    )
    return SuccessResponse(data=result)


@router.get(
    "/{run_id}/executions/{execution_id}",
    response_model=SuccessResponse,
    summary="获取执行详情",
)
async def get_execution(
    project_identifier: str,
    run_id: str,
    execution_id: str,
    db: DbSessionDep,
):
    service = ScheduledRunService(db)
    result = await service.get_execution(execution_id)
    return SuccessResponse(data=result)
