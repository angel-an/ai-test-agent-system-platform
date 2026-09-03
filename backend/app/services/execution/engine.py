"""
统一脚本执行引擎

协调测试运行中所有脚本作业的执行，支持顺序/并行调度、
状态追踪、取消操作和结果汇总。
"""

import asyncio
import logging
from datetime import datetime
from typing import Any, Dict, List, Optional, Set
from uuid import UUID

from sqlalchemy import select

from app.config.database import async_session_factory
from app.models.test_run import TestRunScriptJob, TestRunTestCase
from app.repositories.test_run_repo import (
    TestRunRepository,
    TestRunScriptJobRepository,
)
from app.schemas.enums import AutomationStatus, ExecutionMode, JobStatus, ScriptType, TestResultStatus, TestRunState
from app.services.execution.executors import ExecutorRegistry
from app.services.execution.models import ExecutionResult
from app.services.execution.schedulers import (
    ParallelScheduler,
    SequentialScheduler,
)

logger = logging.getLogger(__name__)

# 模块级取消状态，保证跨实例共享（cancel_run 可能由不同 TestExecutionService 实例调用）
_cancelled_runs: Set[UUID] = set()
_active_executors: Dict[UUID, Any] = {}


def _now_for_job_timestamp() -> datetime:
    """脚本作业时间列是无时区类型，写入本地时间避免驱动时区转换错误。"""
    return datetime.now()


class ScriptExecutionEngine:
    """统一脚本执行引擎"""
# pragma: no cover  MC80OmFIVnBZMlhscm9ua3VMazZVRzlHVWc9PTozNDZjMWJmYg==

    def __init__(self, mongodb: Any = None):
        self.mongodb = mongodb

    async def execute_run(
        self,
        test_run_id: UUID,
        trigger: str = "manual",
    ) -> Dict[str, Any]:
        """
        执行整个测试运行。

        工作流程：
        1. 加载 TestRun 和 ScriptJobs
        2. 更新 TestRun 状态为 IN_PROGRESS
        3. 按 execution_mode 执行所有 jobs
        4. 汇总结果并更新 TestRun 状态
        """
        # 1. 加载 TestRun 和 Jobs
        async with async_session_factory() as session:
            run_repo = TestRunRepository(session)
            job_repo = TestRunScriptJobRepository(session)

            test_run = await run_repo.get_by_id(test_run_id)
            if not test_run:
                raise ValueError(f"测试运行不存在: {test_run_id}")

            # 加载所有脚本作业
            jobs, _ = await job_repo.get_by_test_run(test_run_id)
            await self._reset_run_before_execution(session, test_run, jobs)
            await session.commit()

            if not jobs:
                # 没有脚本作业，直接标记为完成
                test_run.run_state = TestRunState.DONE
                await run_repo.update(test_run)
                await session.commit()
                return {
                    "test_run_id": str(test_run_id),
                    "status": "done",
                    "message": "没有脚本作业需要执行",
                }

        # 提取执行配置（在 session 外访问标量属性是安全的，expire_on_commit=False）
        execution_mode = test_run.execution_mode or ExecutionMode.SEQUENTIAL
        max_concurrency = test_run.max_concurrency or 5
        project_id = test_run.project_id

        # 2. 执行作业
        try:
            if execution_mode == ExecutionMode.PARALLEL:
                scheduler = ParallelScheduler()
            else:
                scheduler = SequentialScheduler()
# noqa  MS80OmFIVnBZMlhscm9ua3VMazZVRzlHVWc9PTozNDZjMWJmYg==

            results = await scheduler.schedule(
                project_id=project_id,
                jobs=jobs,
                run_job=lambda job: self._run_job(test_run_id, job),
                max_concurrency=max_concurrency,
            )

            # 清除取消标记
            _cancelled_runs.discard(test_run_id)

            # 3. 汇总结果并更新 TestRun 状态
            success_count = sum(1 for r in results if r.success)
            failed_count = len(results) - success_count

            async with async_session_factory() as session:
                run_repo = TestRunRepository(session)
                test_run = await run_repo.get_by_id(test_run_id)

                if failed_count > 0:
                    test_run.run_state = TestRunState.REJECTED
                else:
                    test_run.run_state = TestRunState.DONE

                await run_repo.update(test_run)
                await run_repo.update_counts_from_jobs(test_run_id)
                await session.commit()

            logger.info(
                "[ScriptExecutionEngine] 测试运行 %s 执行完成: "
                "total=%s, passed=%s, failed=%s",
                test_run_id,
                len(results),
                success_count,
                failed_count,
            )

            return {
                "test_run_id": str(test_run_id),
                "status": "done" if failed_count == 0 else "rejected",
                "total": len(results),
                "passed": success_count,
                "failed": failed_count,
            }
# pragma: no cover  Mi80OmFIVnBZMlhscm9ua3VMazZVRzlHVWc9PTozNDZjMWJmYg==

        except Exception as e:
            logger.exception("[ScriptExecutionEngine] 执行测试运行时异常")
            async with async_session_factory() as session:
                run_repo = TestRunRepository(session)
                test_run = await run_repo.get_by_id(test_run_id)
                test_run.run_state = TestRunState.REJECTED
                await run_repo.update(test_run)
                await run_repo.update_counts_from_jobs(test_run_id)
                await session.commit()

            return {
                "test_run_id": str(test_run_id),
                "status": "failed",
                "error": str(e),
            }

    async def _reset_run_before_execution(
        self,
        session: Any,
        test_run: Any,
        jobs: list[TestRunScriptJob],
    ) -> None:
        """重新执行前清理上一次执行结果，避免页面混入旧状态。"""
        test_run.run_state = TestRunState.IN_PROGRESS
        for job in jobs:
            job.status = JobStatus.PENDING
            job.started_at = None
            job.completed_at = None
            job.duration_ms = None
            job.result_summary = None
            job.error_message = None
            job.stdout = None
            job.stderr = None
            job.report_path = None

        result = await session.execute(
            select(TestRunTestCase).where(TestRunTestCase.test_run_id == test_run.id)
        )
        run_cases = list(result.scalars().all())
        for run_case in run_cases:
            run_case.latest_status = TestResultStatus.NOT_EXECUTED

        test_run.test_cases_count = len(run_cases)
        test_run.passed_count = 0
        test_run.failed_count = 0
        test_run.skipped_count = 0
        test_run.blocked_count = 0
        test_run.not_executed_count = len(run_cases)

    async def _run_job(self, test_run_id: UUID, job: TestRunScriptJob) -> ExecutionResult:
        """执行单个作业并更新其状态"""
        start_time = _now_for_job_timestamp()

        # 检查是否已被取消
        if test_run_id in _cancelled_runs:
            return ExecutionResult(
                success=False,
                status=JobStatus.CANCELLED.value,
                error_message="测试运行已被取消",
            )

        # 更新作业状态为 RUNNING
        await self._update_job_status(
            job.id,
            JobStatus.RUNNING,
            started_at=start_time,
        )
        await self._sync_test_case_automation_status(
            job,
            AutomationStatus.IN_PROGRESS,
        )

        executor = None
        try:
            executor = ExecutorRegistry.get(job.script_type, self.mongodb)
            _active_executors[test_run_id] = executor
            config = job.execution_config or {}
# pylint: disable  My80OmFIVnBZMlhscm9ua3VMazZVRzlHVWc9PTozNDZjMWJmYg==

            result = await executor.execute(
                script_id=job.script_id,
                config=config,
            )

            completed_at = _now_for_job_timestamp()
            duration_ms = result.duration_ms or int(
                (completed_at - start_time).total_seconds() * 1000
            )

            # 更新作业状态
            await self._update_job_status(
                job.id,
                JobStatus(result.status),
                completed_at=completed_at,
                duration_ms=duration_ms,
                error_message=result.error_message,
                stdout=result.stdout,
                stderr=result.stderr,
                report_path=result.report_path,
                result_summary=result.result_summary,
            )
            await self._sync_test_case_automation_status(
                job,
                AutomationStatus.AUTOMATED,
            )
            await self._sync_test_run_case_status(
                test_run_id,
                job,
                JobStatus(result.status),
                result.result_summary,
            )

            return result

        except Exception as e:
            logger.exception("[ScriptExecutionEngine] 执行作业 %s 异常", job.id)
            completed_at = _now_for_job_timestamp()
            duration_ms = int(
                (completed_at - start_time).total_seconds() * 1000
            )

            await self._update_job_status(
                job.id,
                JobStatus.FAILED,
                completed_at=completed_at,
                duration_ms=duration_ms,
                error_message=str(e),
            )
            await self._sync_test_case_automation_status(
                job,
                AutomationStatus.AUTOMATED,
            )
            await self._sync_test_run_case_status(
                test_run_id,
                job,
                JobStatus.FAILED,
                {},
            )

            return ExecutionResult(
                success=False,
                status=JobStatus.FAILED.value,
                duration_ms=duration_ms,
                error_message=str(e),
            )
        finally:
            _active_executors.pop(test_run_id, None)

    async def _update_job_status(
        self,
        job_id: UUID,
        status: JobStatus,
        **kwargs: Any,
    ) -> None:
        """更新作业状态"""
        try:
            async with async_session_factory() as session:
                job_repo = TestRunScriptJobRepository(session)
                await job_repo.update_status(job_id, status, **kwargs)
                await session.commit()
        except Exception as e:
            logger.warning("[ScriptExecutionEngine] 更新作业状态失败: %s", e)

    async def _sync_test_case_automation_status(
        self,
        job: TestRunScriptJob,
        status: AutomationStatus,
    ) -> None:
        """按脚本作业关联的用例同步自动化状态。"""
        try:
            from sqlalchemy import select

            from app.models.api_test import APITest
            from app.models.test_case import TestCase
            from app.models.web_test import WebTest

            script_type = job.script_type.value if hasattr(job.script_type, "value") else str(job.script_type)
            async with async_session_factory() as session:
                test_case_id = await self._resolve_job_test_case_id(
                    session,
                    script_type,
                    job.script_id,
                    APITest,
                    WebTest,
                )
                if not test_case_id:
                    return

                test_case = await session.get(TestCase, test_case_id)
                if not test_case:
                    return

                test_case.automation_status = status
                await session.commit()
        except Exception as e:
            logger.warning("[ScriptExecutionEngine] 同步用例自动化状态失败: %s", e)

    async def _sync_test_run_case_status(
        self,
        test_run_id: UUID,
        job: TestRunScriptJob,
        status: JobStatus,
        result_summary: dict[str, Any],
    ) -> None:
        """把脚本作业结果同步到测试运行内的用例状态。"""
        try:
            from sqlalchemy import select

            from app.models.api_test import APITest
            from app.models.test_run import TestRunTestCase
            from app.models.web_test import WebTest

            script_type = job.script_type.value if hasattr(job.script_type, "value") else str(job.script_type)
            async with async_session_factory() as session:
                test_case_id = await self._resolve_job_test_case_id(
                    session,
                    script_type,
                    job.script_id,
                    APITest,
                    WebTest,
                )
                if not test_case_id:
                    return

                result = await session.execute(
                    select(TestRunTestCase)
                    .where(TestRunTestCase.test_run_id == test_run_id)
                    .where(TestRunTestCase.test_case_id == test_case_id)
                )
                run_case = result.scalar_one_or_none()
                if not run_case:
                    run_case = TestRunTestCase(
                        test_run_id=test_run_id,
                        test_case_id=test_case_id,
                    )
                    session.add(run_case)
                    await session.flush()

                run_case.latest_status = self._job_to_case_result_status(
                    status,
                    result_summary,
                )
                await session.commit()
        except Exception as e:
            logger.warning("[ScriptExecutionEngine] 同步运行用例状态失败: %s", e)

    async def _resolve_job_test_case_id(
        self,
        session: Any,
        script_type: str,
        script_id: UUID,
        api_test_model: Any,
        web_test_model: Any,
    ) -> UUID | None:
        from sqlalchemy import select

        if script_type == ScriptType.API_TEST.value:
            result = await session.execute(
                select(api_test_model.test_case_id).where(api_test_model.id == script_id)
            )
            return result.scalar_one_or_none()
        if script_type == ScriptType.WEB_TEST.value:
            result = await session.execute(
                select(web_test_model.test_case_id).where(web_test_model.id == script_id)
            )
            return result.scalar_one_or_none()
        return None

    def _job_to_case_result_status(
        self,
        status: JobStatus,
        summary: dict[str, Any],
    ) -> TestResultStatus:
        def _to_int(value: Any) -> int:
            try:
                return int(value or 0)
            except (TypeError, ValueError):
                return 0

        total = _to_int(summary.get("total"))
        passed = _to_int(summary.get("passed"))
        failed = _to_int(summary.get("failed"))
        skipped = _to_int(summary.get("skipped"))

        if status == JobStatus.COMPLETED:
            if total > 0 and failed > 0:
                return TestResultStatus.FAILED
            if total > 0 and passed == 0 and skipped >= total:
                return TestResultStatus.SKIPPED
            return TestResultStatus.PASSED
        if status == JobStatus.FAILED:
            return TestResultStatus.FAILED
        if status in {JobStatus.SKIPPED, JobStatus.CANCELLED}:
            return TestResultStatus.SKIPPED
        return TestResultStatus.NOT_EXECUTED

    async def cancel_run(self, test_run_id: UUID) -> None:
        """取消测试运行"""
        _cancelled_runs.add(test_run_id)
        executor = _active_executors.get(test_run_id)
        if executor:
            try:
                await executor.cancel()
            except Exception as e:
                logger.warning("[ScriptExecutionEngine] 取消执行器失败: %s", e)
        logger.info("[ScriptExecutionEngine] 已标记取消测试运行 %s", test_run_id)
