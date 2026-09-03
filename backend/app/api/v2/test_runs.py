"""
测试运行 API 路由

提供测试运行相关的 RESTful API 接口
参考: https://www.browserstack.com/docs/test-management/api-reference/test-runs
"""

from typing import Optional, Any, Dict
import io
import re
from datetime import datetime, timedelta
from uuid import UUID

from fastapi import APIRouter, Query, status, HTTPException
from fastapi.responses import StreamingResponse
from sqlalchemy import select, func

from app.api.deps import (
    TestRunServiceDep,
    PaginationDep,
    DbSessionDep,
)
from app.schemas.common import SuccessResponse, MessageResponse
from app.schemas.pagination import PaginatedResponse, PaginationInfo
from app.schemas.test_run import (
    TestRunCreate,
    TestRunUpdate,
    TestRunInfo,
    TestRunListInfo,
    TestRunTestCaseInfo,
    AddTestCasesRequest,
    RemoveTestCasesRequest,
    TestRunAssigneeUpdate,
    CloseTestRunRequest,
    ReportSummary,
    PriorityDistributionItem,
    TypeDistributionItem,
    RecentTestRunItem,
    TestRunScriptJobInfo,
    ScriptSelection,
)
from app.schemas.enums import AutomationStatus, TestRunActiveState, TestResultStatus, Priority, TestCaseType, TestRunState, ScriptType, JobStatus
from app.models.project import Project
from app.models.test_case import TestCase
from app.models.test_run import TestRun, TestRunScriptJob

from app.services.execution.engine import ScriptExecutionEngine

router = APIRouter(
    prefix="/projects/{project_identifier}/test-runs",
    tags=["测试运行"],
)

# 全局执行引擎实例（简化版本，不需要 mongodb）
_execution_engine: ScriptExecutionEngine | None = None

def _get_execution_engine() -> ScriptExecutionEngine:
    global _execution_engine
    if _execution_engine is None:
        _execution_engine = ScriptExecutionEngine(mongodb=None)
    return _execution_engine


async def _sync_script_test_case_automation_status(
    db: Any,
    project_id: UUID,
    script_type: ScriptType | str,
    script_id: UUID,
) -> None:
    """脚本加入测试运行后，同步关联用例为已自动化。"""
    from app.models.api_test import APITest
    from app.models.web_test import WebTest

    script_type_value = script_type.value if hasattr(script_type, "value") else str(script_type)
    if script_type_value == ScriptType.API_TEST.value:
        result = await db.execute(
            select(APITest.test_case_id)
            .where(APITest.id == script_id)
            .where(APITest.project_id == project_id)
        )
    elif script_type_value == ScriptType.WEB_TEST.value:
        result = await db.execute(
            select(WebTest.test_case_id)
            .where(WebTest.id == script_id)
            .where(WebTest.project_id == project_id)
        )
    else:
        return

    test_case_id = result.scalar_one_or_none()
    if not test_case_id:
        return

    test_case = await db.get(TestCase, test_case_id)
    if test_case:
        test_case.automation_status = AutomationStatus.AUTOMATED

@router.post(
    "/{test_run_identifier}/execute",
    response_model=SuccessResponse[Dict[str, Any]],
    summary="执行测试运行",
    description="触发测试运行中的所有脚本作业执行",
)
async def execute_test_run(
    project_identifier: str,
    test_run_identifier: str,
    service: TestRunServiceDep,
    db: DbSessionDep,
) -> SuccessResponse[Dict[str, Any]]:
    """
    执行测试运行

    - 将测试运行状态更新为进行中
    - 按 execution_mode 顺序或并行执行所有脚本作业
    - 返回执行结果汇总
    """
    # 获取测试运行详情
    test_run = await service.get_by_identifier(project_identifier, test_run_identifier)

    # 检查状态
    if test_run.run_state == TestRunState.IN_PROGRESS:
        return SuccessResponse(
            success=False,
            data={"error": "测试运行已在进行中"},
        )

    # 启动执行（后台异步执行）
    engine = _get_execution_engine()
    # 使用 asyncio.create_task 后台执行，避免阻塞响应
    import asyncio
    asyncio.create_task(engine.execute_run(test_run.id, trigger="manual"))

    return SuccessResponse(
        success=True,
        data={
            "test_run_id": str(test_run.id),
            "status": "in_progress",
            "message": "测试运行已开始执行",
        },
    )

@router.post(
    "/{test_run_identifier}/cancel",
    response_model=SuccessResponse[Dict[str, Any]],
    summary="取消测试运行",
    description="取消正在执行的测试运行",
)
async def cancel_test_run(
    project_identifier: str,
    test_run_identifier: str,
    service: TestRunServiceDep,
    db: DbSessionDep,
) -> SuccessResponse[Dict[str, Any]]:
    """
    取消测试运行

    - 取消正在执行的测试运行
    """
    test_run = await service.get_by_identifier(project_identifier, test_run_identifier)

    engine = _get_execution_engine()
    await engine.cancel_run(test_run.id)

    return SuccessResponse(
        success=True,
        data={
            "test_run_id": str(test_run.id),
            "status": "cancelled",
            "message": "测试运行已取消",
        },
    )

@router.get(
    "",
    response_model=PaginatedResponse[TestRunListInfo],
    summary="获取测试运行列表",
    description="获取项目下的所有测试运行",
)
async def list_test_runs(
    project_identifier: str,
    service: TestRunServiceDep,
    pagination: PaginationDep,
    active_state: Optional[TestRunActiveState] = Query(
        default=None,
        description="活跃状态过滤: active, closed"
    ),
    search: Optional[str] = Query(default=None, description="搜索关键词"),
) -> PaginatedResponse[TestRunListInfo]:
    """
    获取测试运行列表
    
    - **active_state**: 活跃状态过滤
    - **search**: 按名称或标识符搜索
    """
    items, total = await service.get_list(
        project_identifier,
        active_state=active_state,
        search=search,
        offset=pagination.offset,
        limit=pagination.limit,
    )
    return PaginatedResponse(
        success=True,
        data=items,
        pagination=PaginationInfo(
            total=total,
            page=pagination.page,
            page_size=pagination.limit,
        ),
    )

@router.get(
    "/{test_run_identifier}",
    response_model=SuccessResponse[TestRunInfo],
    summary="获取测试运行详情",
    description="根据标识符获取测试运行详情",
)
async def get_test_run(
    project_identifier: str,
    test_run_identifier: str,
    service: TestRunServiceDep,
) -> SuccessResponse[TestRunInfo]:
    """
    获取测试运行详情
    
    - **test_run_identifier**: 测试运行标识符，如 TR-1
    """
    test_run = await service.get_by_identifier(project_identifier, test_run_identifier)
    return SuccessResponse(success=True, data=test_run)

@router.post(
    "",
    response_model=SuccessResponse[TestRunInfo],
    status_code=status.HTTP_201_CREATED,
    summary="创建测试运行",
    description="创建新的测试运行",
)
async def create_test_run(
    project_identifier: str,
    data: TestRunCreate,
    service: TestRunServiceDep,
    db: DbSessionDep,
) -> SuccessResponse[TestRunInfo]:
    """
    创建测试运行
    
    - **name**: 测试运行名称
    - **test_cases**: 要包含的测试用例标识符列表
    - **configurations**: 配置 ID 列表
    """
    test_run = await service.create(project_identifier, data)
    await db.commit()
    return SuccessResponse(success=True, data=test_run)

@router.patch(
    "/{test_run_identifier}",
    response_model=SuccessResponse[TestRunInfo],
    summary="更新测试运行",
    description="部分更新测试运行信息",
)
async def update_test_run(
    project_identifier: str,
    test_run_identifier: str,
    data: TestRunUpdate,
    service: TestRunServiceDep,
    db: DbSessionDep,
) -> SuccessResponse[TestRunInfo]:
    """
    更新测试运行 (PATCH)

    只更新提供的字段
    """
    test_run = await service.update(project_identifier, test_run_identifier, data)
    await db.commit()
    return SuccessResponse(success=True, data=test_run)
# type: ignore  MS80OmFIVnBZMlhscm9ua3VMazZUR0prUVE9PToyZjY1MzkyOQ==

# noqa  Mi80OmFIVnBZMlhscm9ua3VMazZUR0prUVE9PToyZjY1MzkyOQ==

@router.post(
    "/{test_run_identifier}/delete",
    response_model=MessageResponse,
    summary="删除测试运行",
    description="删除指定的测试运行（符合 BrowserStack API: POST /test-runs/{test_run_id}/delete）",
)
async def delete_test_run(
    project_identifier: str,
    test_run_identifier: str,
    service: TestRunServiceDep,
    db: DbSessionDep,
) -> MessageResponse:
    """
    删除测试运行

    - **test_run_identifier**: 测试运行标识符

    注意: 根据 BrowserStack API 规范，删除操作使用 POST 方法而非 DELETE
    """
    await service.delete(project_identifier, test_run_identifier)
    await db.commit()
    return MessageResponse(success=True, message=f"Test Run {test_run_identifier} has been deleted successfully")

@router.post(
    "/{test_run_identifier}/close",
    response_model=SuccessResponse[TestRunInfo],
    summary="关闭测试运行",
    description="关闭测试运行，将 active_state 设置为 closed",
)
async def close_test_run(
    project_identifier: str,
    test_run_identifier: str,
    data: CloseTestRunRequest,
    service: TestRunServiceDep,
    db: DbSessionDep,
) -> SuccessResponse[TestRunInfo]:
    """
    关闭测试运行

    - **active_state**: closed
    """
    test_run = await service.close_test_run(
        project_identifier, test_run_identifier, data
    )
    await db.commit()
    return SuccessResponse(success=True, data=test_run)

# =============== 测试用例管理接口 ===============

@router.get(
    "/{test_run_identifier}/test-cases",
    response_model=PaginatedResponse[TestRunTestCaseInfo],
    summary="获取测试运行中的测试用例",
    description="获取测试运行中包含的测试用例列表及其状态",
)
async def list_test_run_test_cases(
    project_identifier: str,
    test_run_identifier: str,
    service: TestRunServiceDep,
    pagination: PaginationDep,
    status: Optional[TestResultStatus] = Query(default=None, description="状态过滤"),
    assignee: Optional[str] = Query(default=None, description="负责人过滤"),
    search: Optional[str] = Query(default=None, description="搜索关键词"),
) -> PaginatedResponse[TestRunTestCaseInfo]:
    """
    获取测试运行中的测试用例列表

    - **status**: 状态过滤 (untested, passed, failed, etc.)
    - **assignee**: 负责人邮箱过滤
    """
    items, total = await service.get_test_cases(
        project_identifier,
        test_run_identifier,
        status=status,
        assignee=assignee,
        search=search,
        offset=pagination.offset,
        limit=pagination.limit,
    )
    return PaginatedResponse(
        success=True,
        data=items,
        pagination=PaginationInfo(
            total=total,
            page=pagination.page,
            page_size=pagination.limit,
        ),
    )

@router.post(
    "/{test_run_identifier}/test-cases",
    response_model=SuccessResponse[TestRunInfo],
    summary="添加测试用例到测试运行",
    description="向测试运行中添加测试用例",
)
async def add_test_cases_to_run(
    project_identifier: str,
    test_run_identifier: str,
    data: AddTestCasesRequest,
    service: TestRunServiceDep,
    db: DbSessionDep,
) -> SuccessResponse[TestRunInfo]:
    """
    添加测试用例到测试运行

    - **test_cases**: 测试用例标识符列表
    - **configuration_ids**: 配置 ID 列表
    - **assignee**: 负责人邮箱
    """
    test_run = await service.add_test_cases(
        project_identifier, test_run_identifier, data
    )
    await db.commit()
    return SuccessResponse(success=True, data=test_run)

@router.delete(
    "/{test_run_identifier}/test-cases",
    response_model=SuccessResponse[TestRunInfo],
    summary="从测试运行移除测试用例",
    description="从测试运行中移除测试用例",
)
async def remove_test_cases_from_run(
    project_identifier: str,
    test_run_identifier: str,
    data: RemoveTestCasesRequest,
    service: TestRunServiceDep,
    db: DbSessionDep,
) -> SuccessResponse[TestRunInfo]:
    """
    从测试运行移除测试用例

    - **test_cases**: 测试用例标识符列表
    - **configuration_ids**: 配置 ID 列表 (可选)
    """
    test_run = await service.remove_test_cases(
        project_identifier, test_run_identifier, data
    )
    await db.commit()
    return SuccessResponse(success=True, data=test_run)
# noqa  My80OmFIVnBZMlhscm9ua3VMazZUR0prUVE9PToyZjY1MzkyOQ==

@router.patch(
    "/{test_run_identifier}/assignees",
    response_model=SuccessResponse[TestRunInfo],
    summary="更新测试用例分配",
    description="批量更新测试运行中测试用例的负责人",
)
async def update_test_case_assignees(
    project_identifier: str,
    test_run_identifier: str,
    data: TestRunAssigneeUpdate,
    service: TestRunServiceDep,
    db: DbSessionDep,
) -> SuccessResponse[TestRunInfo]:
    """
    更新测试用例分配

    - **assign_to**: 分配列表，包含 test_case_id, configuration_id, assignee
    """
    test_run = await service.update_assignees(
        project_identifier, test_run_identifier, data
    )
    await db.commit()
    return SuccessResponse(success=True, data=test_run)


# ============================================================================
# 报告统计接口
# ============================================================================

@router.get(
    "/reports/summary",
    response_model=SuccessResponse[ReportSummary],
    summary="获取报告统计摘要",
    description="获取项目级别的测试报告统计数据，包括用例数、通过率、分布等",
)
async def get_report_summary(
    project_identifier: str,
    db: DbSessionDep,
    date_range: Optional[str] = Query(default="all", description="时间范围: 7d, 30d, 90d, all"),
) -> SuccessResponse[ReportSummary]:
    """
    获取报告统计摘要

    - **date_range**: 时间范围过滤
        - `7d`: 最近 7 天
        - `30d`: 最近 30 天
        - `90d`: 最近 90 天
        - `all`: 全部时间
    """
    # 1. 获取项目
    project_result = await db.execute(
        select(Project).where(Project.identifier == project_identifier)
    )
    project = project_result.scalar_one_or_none()
    if not project:
        return SuccessResponse(success=True, data=ReportSummary())

    project_id = project.id

    # 2. 计算时间范围
    now = datetime.utcnow()
    date_filter = None
    if date_range == "7d":
        date_filter = now - timedelta(days=7)
    elif date_range == "30d":
        date_filter = now - timedelta(days=30)
    elif date_range == "90d":
        date_filter = now - timedelta(days=90)

    # 3. 统计测试用例总数
    tc_count_stmt = select(func.count()).select_from(TestCase).where(TestCase.project_id == project_id)
    tc_count_result = await db.execute(tc_count_stmt)
    total_test_cases = tc_count_result.scalar() or 0

    # 4. 统计测试运行总数
    tr_count_stmt = select(func.count()).select_from(TestRun).where(TestRun.project_id == project_id)
    if date_filter:
        tr_count_stmt = tr_count_stmt.where(TestRun.created_at >= date_filter)
    tr_count_result = await db.execute(tr_count_stmt)
    total_test_runs = tr_count_result.scalar() or 0

    # 5. 计算整体通过率（基于所有测试运行的汇总）
    pass_rate_stmt = select(
        func.coalesce(func.sum(TestRun.passed_count), 0),
        func.coalesce(func.sum(TestRun.test_cases_count), 0),
    ).where(TestRun.project_id == project_id)
    if date_filter:
        pass_rate_stmt = pass_rate_stmt.where(TestRun.created_at >= date_filter)
    pass_rate_result = await db.execute(pass_rate_stmt)
    total_passed, total_cases = pass_rate_result.one_or_none() or (0, 0)
    pass_rate = round((total_passed / total_cases) * 100, 1) if total_cases > 0 else 0.0

    # 6. 统计优先级分布
    priority_stmt = select(
        TestCase.priority,
        func.count(),
    ).where(TestCase.project_id == project_id).group_by(TestCase.priority)
    priority_result = await db.execute(priority_stmt)
    priority_rows = priority_result.all()
    priority_distribution = []
    if total_test_cases > 0:
        for row in priority_rows:
            priority_val = row[0]
            count = row[1]
            priority_distribution.append(PriorityDistributionItem(
                priority=priority_val.value if priority_val else "unknown",
                count=count,
                percentage=round((count / total_test_cases) * 100),
            ))
    # 补充缺失的优先级
    existing_priorities = {item.priority for item in priority_distribution}
    for p in Priority:
        if p.value not in existing_priorities:
            priority_distribution.append(PriorityDistributionItem(
                priority=p.value, count=0, percentage=0,
            ))
    priority_distribution.sort(key=lambda x: {"critical": 0, "high": 1, "medium": 2, "low": 3}.get(x.priority, 99))

    # 7. 统计类型分布
    type_stmt = select(
        TestCase.test_case_type,
        func.count(),
    ).where(TestCase.project_id == project_id).group_by(TestCase.test_case_type)
    type_result = await db.execute(type_stmt)
    type_rows = type_result.all()
    type_distribution = []
    for row in type_rows:
        type_val = row[0]
        count = row[1]
        type_distribution.append(TypeDistributionItem(
            type=type_val.value if type_val else "unknown",
            count=count,
        ))
    type_distribution.sort(key=lambda x: x.count, reverse=True)

    # 8. 获取最近测试运行
    recent_tr_stmt = select(TestRun).where(
        TestRun.project_id == project_id
    ).order_by(TestRun.created_at.desc()).limit(10)
    recent_tr_result = await db.execute(recent_tr_stmt)
    recent_test_runs = []
    for tr in recent_tr_result.scalars().all():
        total = tr.test_cases_count or 0
        passed = tr.passed_count or 0
        rate = round((passed / total) * 100) if total > 0 else 0
        recent_test_runs.append(RecentTestRunItem(
            id=tr.id,
            identifier=tr.identifier,
            name=tr.name,
            passed=passed,
            failed=tr.failed_count or 0,
            skipped=tr.skipped_count or 0,
            blocked=tr.blocked_count or 0,
            total=total,
            pass_rate=rate,
            run_state=tr.run_state.value if tr.run_state else "unknown",
            created_at=tr.created_at,
        ))

    # 9. 组装响应
    summary = ReportSummary(
        total_test_cases=total_test_cases,
        total_test_runs=total_test_runs,
        pass_rate=pass_rate,
        open_defects=0,  # 当前系统无缺陷跟踪，固定为 0
        avg_execution_time="-",
        priority_distribution=priority_distribution,
        type_distribution=type_distribution,
        recent_test_runs=recent_test_runs,
    )

    return SuccessResponse(success=True, data=summary)


# ============================================================================
# 脚本作业子资源接口
# ============================================================================

@router.get(
    "/{test_run_identifier}/script-jobs",
    response_model=SuccessResponse[dict],
    summary="获取脚本作业列表",
    description="获取测试运行下的所有脚本作业",
)
async def list_script_jobs(
    project_identifier: str,
    test_run_identifier: str,
    service: TestRunServiceDep,
    db: DbSessionDep,
    script_type: Optional[ScriptType] = Query(default=None, description="脚本类型过滤"),
    page: int = Query(default=1, ge=1, description="页码"),
    page_size: int = Query(default=100, ge=1, le=500, description="每页数量"),
) -> SuccessResponse[dict]:
    """获取测试运行中的脚本作业列表"""
    project = await db.execute(
        select(Project).where(Project.identifier == project_identifier)
    )
    project_obj = project.scalar_one_or_none()
    if not project_obj:
        raise HTTPException(status_code=404, detail="项目不存在")

    test_run = await service.repo.get_by_identifier(test_run_identifier)
    if not test_run or test_run.project_id != project_obj.id:
        raise HTTPException(status_code=404, detail="测试运行不存在")

    from app.repositories.test_run_repo import TestRunScriptJobRepository
    job_repo = TestRunScriptJobRepository(db)
    items, total = await job_repo.get_by_test_run(
        test_run_id=test_run.id,
        status=None,
        offset=(page - 1) * page_size,
        limit=page_size,
    )

    # 过滤脚本类型
    if script_type:
        items = [j for j in items if j.script_type == script_type]

    job_infos = [
        TestRunScriptJobInfo(
            id=job.id,
            test_run_id=job.test_run_id,
            script_type=job.script_type,
            script_id=job.script_id,
            script_identifier=job.script_identifier,
            script_name=job.script_name,
            execution_order=job.execution_order,
            execution_mode=job.execution_mode,
            status=job.status.value if hasattr(job.status, 'value') else str(job.status),
            started_at=job.started_at,
            completed_at=job.completed_at,
            duration_ms=job.duration_ms,
            result_summary=job.result_summary,
            error_message=job.error_message,
            report_path=job.report_path,
            retry_count=job.retry_count,
            max_retries=job.max_retries,
            created_at=job.created_at,
            updated_at=job.updated_at,
        )
        for job in items
    ]

    return SuccessResponse(
        success=True,
        data={
            "items": job_infos,
            "total": total,
            "page": page,
            "page_size": page_size,
        },
    )


@router.post(
    "/{test_run_identifier}/script-jobs",
    response_model=SuccessResponse[TestRunInfo],
    summary="添加脚本作业",
    description="向测试运行添加脚本作业",
)
async def add_script_jobs(
    project_identifier: str,
    test_run_identifier: str,
    data: list[ScriptSelection],
    service: TestRunServiceDep,
    db: DbSessionDep,
) -> SuccessResponse[TestRunInfo]:
    """添加脚本作业到测试运行"""
    project = await db.execute(
        select(Project).where(Project.identifier == project_identifier)
    )
    project_obj = project.scalar_one_or_none()
    if not project_obj:
        raise HTTPException(status_code=404, detail="项目不存在")

    test_run = await service.repo.get_by_identifier(test_run_identifier)
    if not test_run or test_run.project_id != project_obj.id:
        raise HTTPException(status_code=404, detail="测试运行不存在")

    from app.repositories.test_run_repo import TestRunScriptJobRepository
    job_repo = TestRunScriptJobRepository(db)

    for idx, script in enumerate(data):
        try:
            script_id_uuid = UUID(script.script_id)
        except ValueError:
            continue

        job = TestRunScriptJob(
            test_run_id=test_run.id,
            script_type=script.script_type,
            script_id=script_id_uuid,
            script_identifier=script.script_identifier or "",
            script_name=script.script_name,
            execution_order=script.execution_order or idx,
            execution_mode=script.execution_mode or test_run.execution_mode,
            status=JobStatus.PENDING,
            max_retries=script.max_retries or 0,
            execution_config=script.execution_config,
        )
        await job_repo.create(job)
        await _sync_script_test_case_automation_status(
            db,
            project_obj.id,
            script.script_type,
            script_id_uuid,
        )

    await db.commit()
    return SuccessResponse(success=True, data=await service._to_info(test_run, project_identifier))


@router.delete(
    "/{test_run_identifier}/script-jobs/{job_id}",
    response_model=SuccessResponse[TestRunInfo],
    summary="移除脚本作业",
    description="从测试运行中移除指定脚本作业",
)
async def remove_script_job(
    project_identifier: str,
    test_run_identifier: str,
    job_id: str,
    service: TestRunServiceDep,
    db: DbSessionDep,
) -> SuccessResponse[TestRunInfo]:
    """从测试运行移除脚本作业"""
    project = await db.execute(
        select(Project).where(Project.identifier == project_identifier)
    )
    project_obj = project.scalar_one_or_none()
    if not project_obj:
        raise HTTPException(status_code=404, detail="项目不存在")

    test_run = await service.repo.get_by_identifier(test_run_identifier)
    if not test_run or test_run.project_id != project_obj.id:
        raise HTTPException(status_code=404, detail="测试运行不存在")

    from app.repositories.test_run_repo import TestRunScriptJobRepository
    job_repo = TestRunScriptJobRepository(db)

    try:
        job_uuid = UUID(job_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="无效的 job ID")

    job = await job_repo.get_by_id(job_uuid)
    if not job or job.test_run_id != test_run.id:
        raise HTTPException(status_code=404, detail="脚本作业不存在")

    await job_repo.delete(job)
    await db.commit()

    return SuccessResponse(success=True, data=await service._to_info(test_run, project_identifier))


@router.get(
    "/{test_run_identifier}/script-jobs/{job_id}/logs",
    response_model=SuccessResponse[dict],
    summary="获取脚本作业日志",
    description="获取脚本作业的标准输出和标准错误日志",
)
async def get_script_job_logs(
    project_identifier: str,
    test_run_identifier: str,
    job_id: str,
    service: TestRunServiceDep,
    db: DbSessionDep,
) -> SuccessResponse[dict]:
    """获取脚本作业日志"""
    project = await db.execute(
        select(Project).where(Project.identifier == project_identifier)
    )
    project_obj = project.scalar_one_or_none()
    if not project_obj:
        raise HTTPException(status_code=404, detail="项目不存在")

    test_run = await service.repo.get_by_identifier(test_run_identifier)
    if not test_run or test_run.project_id != project_obj.id:
        raise HTTPException(status_code=404, detail="测试运行不存在")

    from app.repositories.test_run_repo import TestRunScriptJobRepository
    job_repo = TestRunScriptJobRepository(db)

    try:
        job_uuid = UUID(job_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="无效的 job ID")

    job = await job_repo.get_by_id(job_uuid)
    if not job or job.test_run_id != test_run.id:
        raise HTTPException(status_code=404, detail="脚本作业不存在")

    return SuccessResponse(
        success=True,
        data={
            "stdout": job.stdout or "",
            "stderr": job.stderr or "",
        },
    )


@router.get(
    "/{test_run_identifier}/script-jobs/{job_id}/report",
    summary="下载脚本作业报告",
    description="下载脚本作业报告 zip 文件",
)
async def download_script_job_report(
    project_identifier: str,
    test_run_identifier: str,
    job_id: str,
    service: TestRunServiceDep,
    db: DbSessionDep,
) -> StreamingResponse:
    """下载脚本作业报告。"""
    project = await db.execute(
        select(Project).where(Project.identifier == project_identifier)
    )
    project_obj = project.scalar_one_or_none()
    if not project_obj:
        raise HTTPException(status_code=404, detail="项目不存在")

    test_run = await service.repo.get_by_identifier(test_run_identifier)
    if not test_run or test_run.project_id != project_obj.id:
        raise HTTPException(status_code=404, detail="测试运行不存在")

    from app.config.minio_client import MinIOClient
    from app.repositories.test_run_repo import TestRunScriptJobRepository

    job_repo = TestRunScriptJobRepository(db)
    try:
        job_uuid = UUID(job_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="无效的 job ID")

    job = await job_repo.get_by_id(job_uuid)
    if not job or job.test_run_id != test_run.id:
        raise HTTPException(status_code=404, detail="脚本作业不存在")
    if not job.report_path:
        raise HTTPException(status_code=404, detail="该脚本作业没有可下载的报告")

    try:
        data = MinIOClient.download_file(job.report_path)
    except Exception as exc:
        raise HTTPException(status_code=404, detail=f"报告文件不存在或无法读取: {exc}") from exc

    safe_identifier = re.sub(r"[^A-Za-z0-9_.-]+", "-", job.script_identifier or job_id).strip("-")
    filename = f"{safe_identifier or job_id}-report.zip"
    return StreamingResponse(
        io.BytesIO(data),
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.get(
    "/{test_run_identifier}/script-jobs/{job_id}/report-url",
    response_model=SuccessResponse[dict],
    summary="获取报告预签名 URL",
    description="获取脚本作业报告下载 URL",
)
async def get_script_job_report_url(
    project_identifier: str,
    test_run_identifier: str,
    job_id: str,
    service: TestRunServiceDep,
    db: DbSessionDep,
) -> SuccessResponse[dict]:
    """获取脚本作业报告 URL"""
    project = await db.execute(
        select(Project).where(Project.identifier == project_identifier)
    )
    project_obj = project.scalar_one_or_none()
    if not project_obj:
        raise HTTPException(status_code=404, detail="项目不存在")

    test_run = await service.repo.get_by_identifier(test_run_identifier)
    if not test_run or test_run.project_id != project_obj.id:
        raise HTTPException(status_code=404, detail="测试运行不存在")

    from app.repositories.test_run_repo import TestRunScriptJobRepository
    job_repo = TestRunScriptJobRepository(db)

    try:
        job_uuid = UUID(job_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="无效的 job ID")

    job = await job_repo.get_by_id(job_uuid)
    if not job or job.test_run_id != test_run.id:
        raise HTTPException(status_code=404, detail="脚本作业不存在")

    return SuccessResponse(
        success=True,
        data={
            "url": (
                f"/api/v2/projects/{project_identifier}/test-runs/"
                f"{test_run_identifier}/script-jobs/{job_id}/report"
                if job.report_path else ""
            ),
            "object_path": job.report_path or "",
            "expires_in": 3600,
        },
    )


@router.post(
    "/{test_run_identifier}/script-jobs/{job_id}/retry",
    response_model=SuccessResponse[TestRunScriptJobInfo],
    summary="重试脚本作业",
    description="重新执行失败的脚本作业",
)
async def retry_script_job(
    project_identifier: str,
    test_run_identifier: str,
    job_id: str,
    service: TestRunServiceDep,
    db: DbSessionDep,
) -> SuccessResponse[TestRunScriptJobInfo]:
    """重试脚本作业"""
    project = await db.execute(
        select(Project).where(Project.identifier == project_identifier)
    )
    project_obj = project.scalar_one_or_none()
    if not project_obj:
        raise HTTPException(status_code=404, detail="项目不存在")

    test_run = await service.repo.get_by_identifier(test_run_identifier)
    if not test_run or test_run.project_id != project_obj.id:
        raise HTTPException(status_code=404, detail="测试运行不存在")

    from app.repositories.test_run_repo import TestRunScriptJobRepository
    job_repo = TestRunScriptJobRepository(db)

    try:
        job_uuid = UUID(job_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="无效的 job ID")

    job = await job_repo.get_by_id(job_uuid)
    if not job or job.test_run_id != test_run.id:
        raise HTTPException(status_code=404, detail="脚本作业不存在")

    # 重置作业状态为 pending，增加重试计数
    job.status = JobStatus.PENDING
    job.retry_count = job.retry_count + 1
    job.started_at = None
    job.completed_at = None
    job.duration_ms = None
    job.error_message = None
    job.stdout = None
    job.stderr = None
    job.result_summary = None
    job.report_path = None
    await job_repo.update(job)
    await db.commit()

    return SuccessResponse(
        success=True,
        data=TestRunScriptJobInfo(
            id=job.id,
            test_run_id=job.test_run_id,
            script_type=job.script_type,
            script_id=job.script_id,
            script_identifier=job.script_identifier,
            script_name=job.script_name,
            execution_order=job.execution_order,
            execution_mode=job.execution_mode,
            status=job.status.value if hasattr(job.status, 'value') else str(job.status),
            started_at=job.started_at,
            completed_at=job.completed_at,
            duration_ms=job.duration_ms,
            result_summary=job.result_summary,
            error_message=job.error_message,
            report_path=job.report_path,
            retry_count=job.retry_count,
            max_retries=job.max_retries,
            created_at=job.created_at,
            updated_at=job.updated_at,
        ),
    )


@router.post(
    "/{test_run_identifier}/script-jobs/batch-retry",
    response_model=SuccessResponse[dict],
    summary="批量重试脚本作业",
    description="批量重试多个脚本作业",
)
async def batch_retry_script_jobs(
    project_identifier: str,
    test_run_identifier: str,
    data: dict,
    service: TestRunServiceDep,
    db: DbSessionDep,
) -> SuccessResponse[dict]:
    """批量重试脚本作业"""
    project = await db.execute(
        select(Project).where(Project.identifier == project_identifier)
    )
    project_obj = project.scalar_one_or_none()
    if not project_obj:
        raise HTTPException(status_code=404, detail="项目不存在")

    test_run = await service.repo.get_by_identifier(test_run_identifier)
    if not test_run or test_run.project_id != project_obj.id:
        raise HTTPException(status_code=404, detail="测试运行不存在")

    from app.repositories.test_run_repo import TestRunScriptJobRepository
    job_repo = TestRunScriptJobRepository(db)

    job_ids = data.get("job_ids", [])
    retried = 0
    retried_jobs = []

    for job_id_str in job_ids:
        try:
            job_uuid = UUID(job_id_str)
        except ValueError:
            continue

        job = await job_repo.get_by_id(job_uuid)
        if not job or job.test_run_id != test_run.id:
            continue

        job.status = JobStatus.PENDING
        job.retry_count = job.retry_count + 1
        job.started_at = None
        job.completed_at = None
        job.duration_ms = None
        job.error_message = None
        job.stdout = None
        job.stderr = None
        await job_repo.update(job)
        retried += 1
        retried_jobs.append(job)

    await db.commit()

    return SuccessResponse(
        success=True,
        data={
            "retried": retried,
            "jobs": [
                TestRunScriptJobInfo(
                    id=job.id,
                    test_run_id=job.test_run_id,
                    script_type=job.script_type,
                    script_id=job.script_id,
                    script_identifier=job.script_identifier,
                    script_name=job.script_name,
                    execution_order=job.execution_order,
                    execution_mode=job.execution_mode,
                    status=job.status.value if hasattr(job.status, 'value') else str(job.status),
                    started_at=job.started_at,
                    completed_at=job.completed_at,
                    duration_ms=job.duration_ms,
                    result_summary=job.result_summary,
                    error_message=job.error_message,
                    report_path=job.report_path,
                    retry_count=job.retry_count,
                    max_retries=job.max_retries,
                    created_at=job.created_at,
                    updated_at=job.updated_at,
                )
                for job in retried_jobs
            ],
        },
    )


@router.get(
    "/{test_run_identifier}/script-jobs/history",
    response_model=SuccessResponse[dict],
    summary="获取脚本执行历史",
    description="获取指定脚本的历史执行记录",
)
async def get_script_history(
    project_identifier: str,
    test_run_identifier: str,
    service: TestRunServiceDep,
    db: DbSessionDep,
    script_type: ScriptType = Query(..., description="脚本类型"),
    script_id: str = Query(..., description="脚本 ID"),
    limit: int = Query(default=30, ge=1, le=100, description="返回数量"),
) -> SuccessResponse[dict]:
    """获取脚本执行历史趋势"""
    project = await db.execute(
        select(Project).where(Project.identifier == project_identifier)
    )
    project_obj = project.scalar_one_or_none()
    if not project_obj:
        raise HTTPException(status_code=404, detail="项目不存在")

    test_run = await service.repo.get_by_identifier(test_run_identifier)
    if not test_run or test_run.project_id != project_obj.id:
        raise HTTPException(status_code=404, detail="测试运行不存在")

    try:
        script_id_uuid = UUID(script_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="无效的 script ID")

    from app.repositories.test_run_repo import TestRunScriptJobRepository
    job_repo = TestRunScriptJobRepository(db)

    # 查询该脚本的所有历史执行记录（跨所有测试运行）
    stmt = (
        select(TestRunScriptJob)
        .where(
            TestRunScriptJob.script_type == script_type,
            TestRunScriptJob.script_id == script_id_uuid,
        )
        .order_by(TestRunScriptJob.created_at.desc())
        .limit(limit)
    )
    result = await db.execute(stmt)
    jobs = result.scalars().all()

    history = []
    passed = 0
    failed = 0
    skipped = 0
    cancelled = 0

    for job in jobs:
        status = job.status.value if hasattr(job.status, 'value') else str(job.status)
        if status == "completed":
            passed += 1
        elif status == "failed":
            failed += 1
        elif status == "skipped":
            skipped += 1
        elif status == "cancelled":
            cancelled += 1

        history.append({
            "job_id": str(job.id),
            "test_run_id": str(job.test_run_id),
            "status": status,
            "result_summary": job.result_summary,
            "duration_ms": job.duration_ms,
            "started_at": job.started_at.isoformat() if job.started_at else None,
            "completed_at": job.completed_at.isoformat() if job.completed_at else None,
        })

    total_runs = len(jobs)
    success_rate = round((passed / total_runs) * 100, 1) if total_runs > 0 else 0

    return SuccessResponse(
        success=True,
        data={
            "script_type": script_type.value,
            "script_id": script_id,
            "total_runs": total_runs,
            "success_rate": success_rate,
            "passed": passed,
            "failed": failed,
            "skipped": skipped,
            "cancelled": cancelled,
            "history": history,
        },
    )


@router.get(
    "/{test_run_identifier}/script-jobs/benchmark",
    response_model=SuccessResponse[dict],
    summary="获取脚本性能基准",
    description="获取指定脚本的性能基准统计数据",
)
async def get_script_benchmark(
    project_identifier: str,
    test_run_identifier: str,
    service: TestRunServiceDep,
    db: DbSessionDep,
    script_type: ScriptType = Query(..., description="脚本类型"),
    script_id: str = Query(..., description="脚本 ID"),
    limit: int = Query(default=30, ge=1, le=100, description="返回数量"),
) -> SuccessResponse[dict]:
    """获取脚本性能基准"""
    project = await db.execute(
        select(Project).where(Project.identifier == project_identifier)
    )
    project_obj = project.scalar_one_or_none()
    if not project_obj:
        raise HTTPException(status_code=404, detail="项目不存在")

    test_run = await service.repo.get_by_identifier(test_run_identifier)
    if not test_run or test_run.project_id != project_obj.id:
        raise HTTPException(status_code=404, detail="测试运行不存在")

    try:
        script_id_uuid = UUID(script_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="无效的 script ID")

    # 查询该脚本的已完成执行记录
    stmt = (
        select(TestRunScriptJob)
        .where(
            TestRunScriptJob.script_type == script_type,
            TestRunScriptJob.script_id == script_id_uuid,
            TestRunScriptJob.duration_ms.isnot(None),
        )
        .order_by(TestRunScriptJob.created_at.desc())
        .limit(limit)
    )
    result = await db.execute(stmt)
    jobs = result.scalars().all()

    durations = [job.duration_ms for job in jobs if job.duration_ms is not None]
    total_runs = len(durations)

    if total_runs == 0:
        return SuccessResponse(
            success=True,
            data={
                "script_type": script_type.value,
                "script_id": script_id,
                "total_runs": 0,
                "avg_duration_ms": 0,
                "min_duration_ms": 0,
                "max_duration_ms": 0,
                "median_duration_ms": 0,
                "runs": [],
            },
        )

    avg_duration = sum(durations) / len(durations)
    min_duration = min(durations)
    max_duration = max(durations)
    sorted_durations = sorted(durations)
    median_duration = sorted_durations[len(sorted_durations) // 2]

    runs = []
    for job in jobs:
        status = job.status.value if hasattr(job.status, 'value') else str(job.status)
        runs.append({
            "job_id": str(job.id),
            "status": status,
            "duration_ms": job.duration_ms or 0,
            "date": job.completed_at.isoformat() if job.completed_at else None,
        })

    return SuccessResponse(
        success=True,
        data={
            "script_type": script_type.value,
            "script_id": script_id,
            "total_runs": total_runs,
            "avg_duration_ms": int(avg_duration),
            "min_duration_ms": min_duration,
            "max_duration_ms": max_duration,
            "median_duration_ms": median_duration,
            "runs": runs,
        },
    )


@router.get(
    "/{test_run_identifier}/script-jobs/{job_id}/report-preview",
    response_model=str,
    summary="获取报告预览 HTML",
    description="获取脚本作业报告的 HTML 预览内容",
)
async def get_script_job_report_preview(
    project_identifier: str,
    test_run_identifier: str,
    job_id: str,
    service: TestRunServiceDep,
    db: DbSessionDep,
) -> str:
    """获取脚本作业报告预览"""
    project = await db.execute(
        select(Project).where(Project.identifier == project_identifier)
    )
    project_obj = project.scalar_one_or_none()
    if not project_obj:
        raise HTTPException(status_code=404, detail="项目不存在")

    test_run = await service.repo.get_by_identifier(test_run_identifier)
    if not test_run or test_run.project_id != project_obj.id:
        raise HTTPException(status_code=404, detail="测试运行不存在")

    from app.repositories.test_run_repo import TestRunScriptJobRepository
    job_repo = TestRunScriptJobRepository(db)

    try:
        job_uuid = UUID(job_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="无效的 job ID")

    job = await job_repo.get_by_id(job_uuid)
    if not job or job.test_run_id != test_run.id:
        raise HTTPException(status_code=404, detail="脚本作业不存在")

    # 生成简单的 HTML 报告预览
    summary = job.result_summary or {}
    html = f"""<!DOCTYPE html>
<html>
<head>
    <meta charset="UTF-8">
    <title>报告预览 - {job.script_name or job.script_identifier}</title>
    <style>
        body {{ font-family: system-ui, sans-serif; padding: 20px; max-width: 800px; margin: 0 auto; }}
        .header {{ border-bottom: 2px solid #e5e7eb; padding-bottom: 16px; margin-bottom: 24px; }}
        .status {{ display: inline-block; padding: 4px 12px; border-radius: 9999px; font-size: 14px; font-weight: 500; }}
        .status-completed {{ background: #dcfce7; color: #166534; }}
        .status-failed {{ background: #fee2e2; color: #991b1b; }}
        .status-pending {{ background: #f3f4f6; color: #374151; }}
        .metric {{ display: flex; justify-content: space-between; padding: 12px 0; border-bottom: 1px solid #f3f4f6; }}
        .metric-label {{ color: #6b7280; }}
        .metric-value {{ font-weight: 600; }}
        pre {{ background: #f9fafb; padding: 16px; border-radius: 8px; overflow-x: auto; font-size: 13px; }}
    </style>
</head>
<body>
    <div class="header">
        <h1>{job.script_name or job.script_identifier}</h1>
        <span class="status status-{job.status.value if hasattr(job.status, 'value') else str(job.status)}">
            {job.status.value if hasattr(job.status, 'value') else str(job.status)}
        </span>
    </div>
    <div class="metrics">
        <div class="metric">
            <span class="metric-label">脚本类型</span>
            <span class="metric-value">{job.script_type.value if hasattr(job.script_type, 'value') else str(job.script_type)}</span>
        </div>
        <div class="metric">
            <span class="metric-label">执行耗时</span>
            <span class="metric-value">{job.duration_ms}ms</span>
        </div>
        <div class="metric">
            <span class="metric-label">通过</span>
            <span class="metric-value">{summary.get('passed', 0)}</span>
        </div>
        <div class="metric">
            <span class="metric-label">失败</span>
            <span class="metric-value">{summary.get('failed', 0)}</span>
        </div>
        <div class="metric">
            <span class="metric-label">跳过</span>
            <span class="metric-value">{summary.get('skipped', 0)}</span>
        </div>
        <div class="metric">
            <span class="metric-label">总计</span>
            <span class="metric-value">{summary.get('total', 0)}</span>
        </div>
    </div>
    {f'<h3>错误信息</h3><pre>{job.error_message}</pre>' if job.error_message else ''}
</body>
</html>"""
    return html


@router.post(
    "/{test_run_identifier}/map-jobs-to-cases",
    response_model=SuccessResponse[TestRunInfo],
    summary="映射作业到测试用例",
    description="将脚本作业结果映射到测试用例状态",
)
async def map_jobs_to_test_cases(
    project_identifier: str,
    test_run_identifier: str,
    service: TestRunServiceDep,
    db: DbSessionDep,
) -> SuccessResponse[TestRunInfo]:
    """将脚本作业结果映射到测试用例"""
    project = await db.execute(
        select(Project).where(Project.identifier == project_identifier)
    )
    project_obj = project.scalar_one_or_none()
    if not project_obj:
        raise HTTPException(status_code=404, detail="项目不存在")

    test_run = await service.repo.get_by_identifier(test_run_identifier)
    if not test_run or test_run.project_id != project_obj.id:
        raise HTTPException(status_code=404, detail="测试运行不存在")

    from app.models.api_test import APITest
    from app.models.web_test import WebTest
    from app.models.test_run import TestRunTestCase
    from app.repositories.test_run_repo import TestRunScriptJobRepository
    job_repo = TestRunScriptJobRepository(db)

    # 获取所有脚本作业
    jobs, _ = await job_repo.get_by_test_run(test_run.id, offset=0, limit=1000)

    def _job_result_status(job: TestRunScriptJob) -> TestResultStatus:
        job_status = job.status.value if hasattr(job.status, "value") else str(job.status)
        summary = job.result_summary or {}

        def _to_int(value: Any) -> int:
            try:
                return int(value or 0)
            except (TypeError, ValueError):
                return 0

        total = _to_int(summary.get("total"))
        passed = _to_int(summary.get("passed"))
        failed = _to_int(summary.get("failed"))
        skipped = _to_int(summary.get("skipped"))

        if job_status == JobStatus.COMPLETED.value:
            if total > 0 and failed > 0:
                return TestResultStatus.FAILED
            if total > 0 and passed == 0 and skipped >= total:
                return TestResultStatus.SKIPPED
            return TestResultStatus.PASSED
        if job_status == JobStatus.FAILED.value:
            return TestResultStatus.FAILED
        if job_status in {JobStatus.SKIPPED.value, JobStatus.CANCELLED.value}:
            return TestResultStatus.SKIPPED
        return TestResultStatus.NOT_EXECUTED

    async def _resolve_case_id(job: TestRunScriptJob) -> UUID | None:
        script_type = job.script_type.value if hasattr(job.script_type, "value") else str(job.script_type)
        if script_type == ScriptType.API_TEST.value:
            result = await db.execute(
                select(APITest.test_case_id)
                .where(APITest.id == job.script_id)
                .where(APITest.project_id == project_obj.id)
            )
            return result.scalar_one_or_none()
        if script_type == ScriptType.WEB_TEST.value:
            result = await db.execute(
                select(WebTest.test_case_id)
                .where(WebTest.id == job.script_id)
                .where(WebTest.project_id == project_obj.id)
            )
            return result.scalar_one_or_none()
        return None

    for job in jobs:
        test_case_id = await _resolve_case_id(job)
        if not test_case_id:
            continue

        result = await db.execute(
            select(TestRunTestCase)
            .where(TestRunTestCase.test_run_id == test_run.id)
            .where(TestRunTestCase.test_case_id == test_case_id)
        )
        run_case = result.scalar_one_or_none()
        if not run_case:
            run_case = TestRunTestCase(
                test_run_id=test_run.id,
                test_case_id=test_case_id,
            )
            db.add(run_case)
            await db.flush()

        run_case.latest_status = _job_result_status(job)

    await db.flush()
    await service.repo.update_counts(test_run.id)
    await db.refresh(test_run)
    await db.commit()
    return SuccessResponse(success=True, data=await service._to_info(test_run, project_identifier))
