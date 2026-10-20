"""
测试运行服务

处理测试运行相关的业务逻辑
参考: https://www.browserstack.com/docs/test-management/api-reference/test-runs
"""

import logging
from typing import Optional
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload
from sqlalchemy import select

from app.models.test_run import TestRun, TestRunTestCase, TestRunScriptJob

logger = logging.getLogger(__name__)
from app.models.test_case import TestCase
from app.repositories.test_run_repo import TestRunRepository, TestRunTestCaseRepository, TestRunScriptJobRepository
from app.repositories.project_repo import ProjectRepository
from app.repositories.test_case_repo import TestCaseRepository
from app.schemas.test_run import (
    TestRunCreate,
    TestRunUpdate,
    TestRunInfo,
    TestRunListInfo,
    TestRunTestCaseInfo,
    TestRunLinks,
    OverallProgress,
    TestRunScriptJobInfo,
    AddTestCasesRequest,
    RemoveTestCasesRequest,
    TestRunAssigneeUpdate,
    CloseTestRunRequest,
)
from app.schemas.enums import TestRunActiveState, TestResultStatus, ExecutionMode
from app.utils.exceptions import NotFoundException, BadRequestException
from app.config.settings import settings
# pragma: no cover  MC80OmFIVnBZMlhscm9ua3VMazZXa1ZUYXc9PTo4NjgwY2U1Zg==

class TestRunService:
    """测试运行服务类"""
    
    def __init__(self, session: AsyncSession):
        self.session = session
        self.repo = TestRunRepository(session)
        self.tc_repo = TestRunTestCaseRepository(session)
        self.script_job_repo = TestRunScriptJobRepository(session)
        self.project_repo = ProjectRepository(session)
        self.test_case_repo = TestCaseRepository(session)
    
    async def _get_project_by_identifier(self, project_identifier: str):
        """根据标识符获取项目"""
        project = await self.project_repo.get_by_identifier(project_identifier)
        if not project:
            raise NotFoundException(resource_type="项目", resource_id=project_identifier)
        return project
    
    async def _to_info(self, test_run: TestRun, project_identifier: str) -> TestRunInfo:
        """将模型转换为响应信息"""
        # 直接查询数据库获取脚本作业，避免懒加载问题
        from app.repositories.test_run_repo import TestRunScriptJobRepository
        script_job_repo = TestRunScriptJobRepository(self.session)
        script_jobs_list, _ = await script_job_repo.get_by_test_run(
            test_run_id=test_run.id,
            offset=0,
            limit=1000,
        )

        script_jobs = [
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
            for job in script_jobs_list
        ] if script_jobs_list else None

        return TestRunInfo(
            id=test_run.id,
            identifier=test_run.identifier,
            name=test_run.name,
            description=test_run.description,
            run_state=test_run.run_state,
            active_state=test_run.active_state,
            assignee=test_run.assignee,
            test_cases_count=test_run.test_cases_count,
            tags=test_run.tags,
            issues=test_run.issues,
            configurations=test_run.configurations,
            overall_progress=OverallProgress(
                passed=test_run.passed_count,
                failed=test_run.failed_count,
                skipped=test_run.skipped_count,
                blocked=test_run.blocked_count,
                not_executed=test_run.not_executed_count,
            ),
            execution_mode=test_run.execution_mode,
            max_concurrency=test_run.max_concurrency,
            script_jobs=script_jobs,
            created_at=test_run.created_at,
            updated_at=test_run.updated_at,
            links=TestRunLinks(
                self=f"{settings.api_prefix}/projects/{project_identifier}/test-runs/{test_run.identifier}",
                test_cases=f"{settings.api_prefix}/projects/{project_identifier}/test-runs/{test_run.identifier}/test-cases",
            ),
        )

    async def _to_list_info(self, test_run: TestRun) -> TestRunListInfo:
        """将模型转换为列表项信息"""
        return TestRunListInfo(
            id=test_run.id,
            identifier=test_run.identifier,
            name=test_run.name,
            run_state=test_run.run_state,
            active_state=test_run.active_state,
            assignee=test_run.assignee,
            test_cases_count=test_run.test_cases_count,
            overall_progress=OverallProgress(
                passed=test_run.passed_count,
                failed=test_run.failed_count,
                skipped=test_run.skipped_count,
                blocked=test_run.blocked_count,
                not_executed=test_run.not_executed_count,
            ),
            execution_mode=test_run.execution_mode,
            max_concurrency=test_run.max_concurrency,
            created_at=test_run.created_at,
        )
    
    async def get_list(
        self,
        project_identifier: str,
        active_state: Optional[TestRunActiveState] = None,
        search: Optional[str] = None,
        offset: int = 0,
        limit: int = 30,
    ) -> tuple[list[TestRunListInfo], int]:
        """获取测试运行列表"""
        project = await self._get_project_by_identifier(project_identifier)
        
        test_runs, total = await self.repo.get_list(
            project_id=project.id,
            active_state=active_state,
            search=search,
            offset=offset,
            limit=limit,
        )
        
        items = [await self._to_list_info(tr) for tr in test_runs]
        return items, total

    async def get_by_identifier(
        self,
        project_identifier: str,
        test_run_identifier: str,
    ) -> TestRunInfo:
        """获取测试运行详情"""
        project = await self._get_project_by_identifier(project_identifier)

        test_run = await self.repo.get_by_identifier(test_run_identifier)
        if not test_run or test_run.project_id != project.id:
            raise NotFoundException(resource_type="测试运行", resource_id=test_run_identifier)

        return await self._to_info(test_run, project_identifier)

    async def create(
        self,
        project_identifier: str,
        data: TestRunCreate,
    ) -> TestRunInfo:
        """创建测试运行"""
        project = await self._get_project_by_identifier(project_identifier)

        # 生成标识符
        identifier = await self.repo.generate_identifier(project.id)

        # 创建测试运行
        test_run = TestRun(
            project_id=project.id,
            identifier=identifier,
            name=data.name,
            description=data.description,
            run_state=data.run_state,
            assignee=data.assignee,
            tags=data.tags or [],
            issues=data.issues or [],
            configurations=data.configurations or [],
            execution_mode=data.execution_mode or ExecutionMode.SEQUENTIAL,
            max_concurrency=data.max_concurrency or 5,
        )
        test_run = await self.repo.create(test_run)

        # 添加脚本作业
        if data.scripts:
            for idx, script in enumerate(data.scripts):
                try:
                    script_id_uuid = UUID(script.script_id)
                except ValueError:
                    # 如果 script_id 不是有效的 UUID，跳过
                    continue

                job = TestRunScriptJob(
                    test_run_id=test_run.id,
                    script_type=script.script_type,
                    script_id=script_id_uuid,
                    script_identifier=script.script_identifier or "",
                    script_name=script.script_name,
                    execution_order=script.execution_order or idx,
                    execution_mode=script.execution_mode or data.execution_mode or ExecutionMode.SEQUENTIAL,
                    status="pending",
                    max_retries=script.max_retries or 0,
                    execution_config=script.execution_config,
                )
                await self.script_job_repo.create(job)

            # 如果没有传入测试用例，将脚本数量同步到 test_cases_count
            # 这样前端进度条可以正确显示基于脚本的进度
            if not data.test_cases:
                test_run.test_cases_count = len(data.scripts)
                await self.repo.update(test_run)

        # 添加测试用例
        if data.test_cases:
            await self._add_test_cases_by_identifiers(
                test_run.id,
                project.id,
                data.test_cases,
                data.configurations,
                data.test_case_assignee or data.assignee,
            )  # 创建路径：新生成用例不会是 deprecated；排除项不阻断创建
            await self.repo.update_counts(test_run.id)
            await self.session.refresh(test_run)

        return await self._to_info(test_run, project_identifier)

    async def _add_test_cases_by_identifiers(
        self,
        test_run_id: UUID,
        project_id: UUID,
        test_case_identifiers: list[str],
        configurations: Optional[list[int]] = None,
        assignee: Optional[str] = None,
    ) -> list[TestRunTestCase]:
        """通过标识符添加测试用例"""
        added = []
        excluded_deprecated: list[str] = []
        for identifier in test_case_identifiers:
            test_case = await self.test_case_repo.get_by_identifier(identifier)
            if not test_case or test_case.project_id != project_id:
                continue  # 跳过无效的测试用例

            # deprecated=true 的废弃用例不得进入新建/更新的 TestRun
            # （2026-09-29 复核："已废弃但仍可被执行"是不允许的；历史 run 中的行不动）。
            # 语义是"拒绝并显式反馈"，不是静默跳过：调用方从返回值拿到排除清单。
            from app.services.deprecation import is_deprecated
            if is_deprecated(test_case):
                logger.warning(
                    "[test_run] 拒绝已废弃用例 %s（deprecated=true，不加入测试运行）",
                    identifier,
                )
                excluded_deprecated.append(identifier)
                continue

            if configurations:
                # 为每个配置创建一条记录
                for config_id in configurations:
                    existing = await self.tc_repo.get_by_test_run_and_case(
                        test_run_id, test_case.id, config_id
                    )
                    if not existing:
                        trtc = TestRunTestCase(
                            test_run_id=test_run_id,
                            test_case_id=test_case.id,
                            configuration_id=config_id,
                            assignee=assignee,
                        )
                        added.append(trtc)
            else:
                existing = await self.tc_repo.get_by_test_run_and_case(
                    test_run_id, test_case.id
                )
                if not existing:
                    trtc = TestRunTestCase(
                        test_run_id=test_run_id,
                        test_case_id=test_case.id,
                        assignee=assignee,
                    )
                    added.append(trtc)

        if added:
            await self.tc_repo.add_test_cases(test_run_id, added)
        return added, excluded_deprecated

    async def update(
        self,
        project_identifier: str,
        test_run_identifier: str,
        data: TestRunUpdate,
    ) -> TestRunInfo:
        """更新测试运行 (PATCH)"""
        project = await self._get_project_by_identifier(project_identifier)

        test_run = await self.repo.get_by_identifier(test_run_identifier)
        if not test_run or test_run.project_id != project.id:
            raise NotFoundException(resource_type="测试运行", resource_id=test_run_identifier)

        # 更新字段
        update_data = data.model_dump(exclude_unset=True)
        for key, value in update_data.items():
            if hasattr(test_run, key):
                setattr(test_run, key, value)

        test_run = await self.repo.update(test_run)
        return await self._to_info(test_run, project_identifier)

    async def delete(
        self,
        project_identifier: str,
        test_run_identifier: str,
    ) -> None:
        """删除测试运行"""
        project = await self._get_project_by_identifier(project_identifier)

        test_run = await self.repo.get_by_identifier(test_run_identifier)
        if not test_run or test_run.project_id != project.id:
            raise NotFoundException(resource_type="测试运行", resource_id=test_run_identifier)

        await self.repo.delete(test_run)

    async def close_test_run(
        self,
        project_identifier: str,
        test_run_identifier: str,
        data: CloseTestRunRequest,
    ) -> TestRunInfo:
        """关闭测试运行"""
        project = await self._get_project_by_identifier(project_identifier)

        test_run = await self.repo.get_by_identifier(test_run_identifier)
        if not test_run or test_run.project_id != project.id:
            raise NotFoundException(resource_type="测试运行", resource_id=test_run_identifier)

        test_run.active_state = data.active_state
        test_run = await self.repo.update(test_run)
        return await self._to_info(test_run, project_identifier)

    async def get_test_cases(
        self,
        project_identifier: str,
        test_run_identifier: str,
        status: Optional[TestResultStatus] = None,
        assignee: Optional[str] = None,
        search: Optional[str] = None,
        offset: int = 0,
        limit: int = 30,
    ) -> tuple[list[TestRunTestCaseInfo], int]:
        """获取测试运行中的测试用例列表"""
        project = await self._get_project_by_identifier(project_identifier)

        test_run = await self.repo.get_by_identifier(test_run_identifier)
        if not test_run or test_run.project_id != project.id:
            raise NotFoundException(resource_type="测试运行", resource_id=test_run_identifier)

        items, total = await self.tc_repo.get_list(
            test_run_id=test_run.id,
            status=status,
            assignee=assignee,
            search=search,
            offset=offset,
            limit=limit,
        )
# pylint: disable  Mi80OmFIVnBZMlhscm9ua3VMazZXa1ZUYXc9PTo4NjgwY2U1Zg==

        # 批量取最新明细原因：latest_result_id 指向 test_results 最新一行，
        # 一次 IN 查询避免 N+1；取 description（断言级摘要）+ custom_fields.unverified。
        detail_map: dict = {}
        result_ids = [item.latest_result_id for item in items if item.latest_result_id]
        if result_ids:
            from app.models.test_result import TestResult

            rows = (await self.session.execute(
                select(TestResult).where(TestResult.id.in_(result_ids))
            )).scalars().all()
            for r in rows:
                cf = r.custom_fields or {}
                detail_map[str(r.id)] = (
                    r.description,
                    cf.get("unverified") if isinstance(cf.get("unverified"), int) else None,
                )

        result = []
        for item in items:
            tc = item.test_case
            reason, unverified = detail_map.get(str(item.latest_result_id), (None, None)) \
                if item.latest_result_id else (None, None)
            result.append(TestRunTestCaseInfo(
                id=item.id,
                test_run_id=item.test_run_id,
                test_case_id=item.test_case_id,
                test_case_identifier=tc.identifier if tc else "",
                test_case_name=tc.name if tc else "",
                configuration_id=item.configuration_id,
                assignee=item.assignee,
                latest_status=item.latest_status,
                latest_reason=reason,
                latest_unverified=unverified,
                priority=tc.priority if tc else None,
                test_case_type=tc.test_case_type if tc else None,
                folder_id=tc.folder_id if tc else None,
            ))

        return result, total

    async def add_test_cases(
        self,
        project_identifier: str,
        test_run_identifier: str,
        data: AddTestCasesRequest,
    ) -> TestRunInfo:
        """添加测试用例到测试运行"""
        project = await self._get_project_by_identifier(project_identifier)

        test_run = await self.repo.get_by_identifier(test_run_identifier)
        if not test_run or test_run.project_id != project.id:
            raise NotFoundException(resource_type="测试运行", resource_id=test_run_identifier)

        added_rows, excluded_deprecated = await self._add_test_cases_by_identifiers(
            test_run.id,
            project.id,
            data.test_cases,
            data.configuration_ids,
            data.assignee,
        )

        # 全部被排除（如请求用例均为已废弃）→ 拒绝，不制造"看似成功"的空运行
        if data.test_cases and not added_rows:
            raise ValueError(
                "[all_cases_deprecated_or_invalid] 请求的用例均未纳入运行（已废弃："
                + "、".join(excluded_deprecated or data.test_cases[:10])
                + "）。请先移除废弃标记或更换用例；运行未被修改。"
            )

        await self.repo.update_counts(test_run.id)
        await self.session.refresh(test_run)
        info = await self._to_info(test_run, project_identifier)
        from app.schemas.test_run import TestRunAddCasesResult
        return TestRunAddCasesResult(
            **info.model_dump(),
            excluded_deprecated_cases=excluded_deprecated,
        )

    async def remove_test_cases(
        self,
        project_identifier: str,
        test_run_identifier: str,
        data: RemoveTestCasesRequest,
    ) -> TestRunInfo:
        """从测试运行移除测试用例"""
        project = await self._get_project_by_identifier(project_identifier)

        test_run = await self.repo.get_by_identifier(test_run_identifier)
        if not test_run or test_run.project_id != project.id:
            raise NotFoundException(resource_type="测试运行", resource_id=test_run_identifier)

        # 获取测试用例 ID
        test_case_ids = []
        for identifier in data.test_cases:
            tc = await self.test_case_repo.get_by_identifier(identifier)
            if tc and tc.project_id == project.id:
                test_case_ids.append(tc.id)

        if test_case_ids:
            await self.tc_repo.remove_test_cases(
                test_run.id,
                test_case_ids,
                data.configuration_ids,
            )

        await self.repo.update_counts(test_run.id)
        await self.session.refresh(test_run)
        return await self._to_info(test_run, project_identifier)

    async def update_assignees(
        self,
        project_identifier: str,
        test_run_identifier: str,
        data: TestRunAssigneeUpdate,
    ) -> TestRunInfo:
        """更新测试用例分配"""
        project = await self._get_project_by_identifier(project_identifier)

        test_run = await self.repo.get_by_identifier(test_run_identifier)
        if not test_run or test_run.project_id != project.id:
            raise NotFoundException(resource_type="测试运行", resource_id=test_run_identifier)

        assignments = []
        for item in data.assign_to:
            tc = await self.test_case_repo.get_by_identifier(item.test_case_id)
            if tc and tc.project_id == project.id:
                assignments.append({
                    "test_case_id": tc.id,
                    "configuration_id": item.configuration_id,
                    "assignee": item.assignee,
                })
# fmt: off  My80OmFIVnBZMlhscm9ua3VMazZXa1ZUYXc9PTo4NjgwY2U1Zg==

        if assignments:
            await self.tc_repo.update_assignees(test_run.id, assignments)

        return await self._to_info(test_run, project_identifier)

