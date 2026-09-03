"""
测试运行仓储

提供测试运行数据访问层
参考: https://www.browserstack.com/docs/test-management/api-reference/test-runs
"""

from typing import Optional, Any
from uuid import UUID
from datetime import datetime
# noqa  MC80OmFIVnBZMlhscm9ua3VMazZNblZYZHc9PToxMzcyZDdkNw==

from sqlalchemy import select, func, and_, or_, update, delete
from sqlalchemy.orm import selectinload, joinedload
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.test_run import TestRun, TestRunTestCase, TestRunScriptJob
from app.models.test_case import TestCase
from app.models.project import Project
from app.schemas.enums import TestRunState, TestRunActiveState, TestResultStatus, JobStatus

class TestRunRepository:
    """测试运行数据仓储"""
    
    def __init__(self, session: AsyncSession):
        self.session = session
    
    async def get_by_id(self, test_run_id: UUID) -> Optional[TestRun]:
        """根据 ID 获取测试运行"""
        stmt = select(TestRun).where(TestRun.id == test_run_id)
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()
    
    async def get_by_identifier(self, identifier: str) -> Optional[TestRun]:
        """根据标识符获取测试运行"""
        stmt = select(TestRun).where(TestRun.identifier == identifier)
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()
    
    async def get_list(
        self,
        project_id: UUID,
        active_state: Optional[TestRunActiveState] = None,
        run_state: Optional[TestRunState] = None,
        search: Optional[str] = None,
        offset: int = 0,
        limit: int = 30,
    ) -> tuple[list[TestRun], int]:
        """获取测试运行列表"""
        stmt = select(TestRun).where(TestRun.project_id == project_id)
        count_stmt = select(func.count()).select_from(TestRun).where(TestRun.project_id == project_id)
        
        # 过滤条件
        if active_state:
            stmt = stmt.where(TestRun.active_state == active_state)
            count_stmt = count_stmt.where(TestRun.active_state == active_state)
        
        if run_state:
            stmt = stmt.where(TestRun.run_state == run_state)
            count_stmt = count_stmt.where(TestRun.run_state == run_state)
        
        if search:
            search_filter = or_(
                TestRun.name.ilike(f"%{search}%"),
                TestRun.identifier.ilike(f"%{search}%"),
            )
            stmt = stmt.where(search_filter)
            count_stmt = count_stmt.where(search_filter)
        
        # 排序和分页
        stmt = stmt.order_by(TestRun.created_at.desc()).offset(offset).limit(limit)
        
        result = await self.session.execute(stmt)
        count_result = await self.session.execute(count_stmt)
        
        return list(result.scalars().all()), count_result.scalar() or 0
    
    async def create(self, test_run: TestRun) -> TestRun:
        """创建测试运行"""
        self.session.add(test_run)
        await self.session.flush()
        return test_run
    
    async def update(self, test_run: TestRun) -> TestRun:
        """更新测试运行"""
        await self.session.flush()
        await self.session.refresh(test_run)
        return test_run
    
    async def delete(self, test_run: TestRun) -> None:
        """删除测试运行"""
        await self.session.delete(test_run)
        await self.session.flush()
    
    async def generate_identifier(self, project_id: UUID) -> str:
        """生成测试运行标识符

        使用项目内最大序号 + 1 的方式生成唯一标识符，避免并发冲突和删除后重复问题。
        由于数据库中 identifier 是全局唯一的（不是按项目隔离），需要确保生成的标识符在所有项目中唯一。
        """
        # 获取所有已存在的 TR- 标识符（全局查询，因为数据库索引是全局唯一的）
        stmt = select(TestRun.identifier).where(
            TestRun.identifier.like("TR-%")
        )
        result = await self.session.execute(stmt)
        existing_identifiers = set(result.scalars().all())

        # 找出最大序号
        max_num = 0
        for identifier in existing_identifiers:
            try:
                num = int(identifier.replace("TR-", ""))
                if num > max_num:
                    max_num = num
            except ValueError:
                continue

        # 生成新标识符，确保全局唯一
        new_num = max_num + 1
        while True:
            new_identifier = f"TR-{new_num}"
            if new_identifier not in existing_identifiers:
                return new_identifier
            new_num += 1
            # 安全限制：最多重试 1000 次
            if new_num > max_num + 1000:
                raise RuntimeError(f"无法生成唯一的测试运行标识符，已有太多测试运行")
    
    async def update_counts(self, test_run_id: UUID) -> None:
        """更新测试运行的统计数据"""
        # 统计各状态数量
        status_counts = {}
        for status in TestResultStatus:
            stmt = select(func.count()).select_from(TestRunTestCase).where(
                and_(
                    TestRunTestCase.test_run_id == test_run_id,
                    TestRunTestCase.latest_status == status
                )
            )
            result = await self.session.execute(stmt)
            status_counts[status] = result.scalar() or 0
# pragma: no cover  MS80OmFIVnBZMlhscm9ua3VMazZNblZYZHc9PToxMzcyZDdkNw==

        # 更新测试运行
        total = sum(status_counts.values())
        update_stmt = (
            update(TestRun)
            .where(TestRun.id == test_run_id)
            .values(
                test_cases_count=total,
                passed_count=status_counts.get(TestResultStatus.PASSED, 0),
                failed_count=status_counts.get(TestResultStatus.FAILED, 0),
                skipped_count=status_counts.get(TestResultStatus.SKIPPED, 0),
                blocked_count=status_counts.get(TestResultStatus.BLOCKED, 0),
                not_executed_count=status_counts.get(TestResultStatus.NOT_EXECUTED, 0),
            )
        )
        await self.session.execute(update_stmt)
        await self.session.flush()

    async def get_by_test_plan_id(
        self,
        test_plan_id: UUID,
        offset: int = 0,
        limit: int = 30,
    ) -> list[TestRun]:
        """
        根据测试计划 ID 获取测试运行列表

        Args:
            test_plan_id: 测试计划 ID
            offset: 偏移量
            limit: 限制数量

        Returns:
            list[TestRun]: 测试运行列表
        """
        stmt = (
            select(TestRun)
            .where(TestRun.test_plan_id == test_plan_id)
            .order_by(TestRun.created_at.desc())
            .offset(offset)
            .limit(limit)
        )
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def count_by_test_plan_id(self, test_plan_id: UUID) -> int:
        """
        获取测试计划下测试运行总数

        Args:
            test_plan_id: 测试计划 ID

        Returns:
            int: 测试运行总数
        """
        stmt = (
            select(func.count())
            .select_from(TestRun)
            .where(TestRun.test_plan_id == test_plan_id)
        )
        result = await self.session.execute(stmt)
        return result.scalar() or 0

class TestRunTestCaseRepository:
    """测试运行测试用例仓储"""

    def __init__(self, session: AsyncSession):
        self.session = session

    async def get_by_id(self, id: UUID) -> Optional[TestRunTestCase]:
        """根据 ID 获取关联"""
        stmt = select(TestRunTestCase).where(TestRunTestCase.id == id)
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def get_by_test_run_and_case(
        self,
        test_run_id: UUID,
        test_case_id: UUID,
        configuration_id: Optional[int] = None,
    ) -> Optional[TestRunTestCase]:
        """获取特定测试运行中的测试用例"""
        conditions = [
            TestRunTestCase.test_run_id == test_run_id,
            TestRunTestCase.test_case_id == test_case_id,
        ]
        if configuration_id is not None:
            conditions.append(TestRunTestCase.configuration_id == configuration_id)
# fmt: off  Mi80OmFIVnBZMlhscm9ua3VMazZNblZYZHc9PToxMzcyZDdkNw==

        stmt = select(TestRunTestCase).where(and_(*conditions))
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def get_list(
        self,
        test_run_id: UUID,
        status: Optional[TestResultStatus] = None,
        assignee: Optional[str] = None,
        search: Optional[str] = None,
        offset: int = 0,
        limit: int = 30,
    ) -> tuple[list[TestRunTestCase], int]:
        """获取测试运行中的测试用例列表"""
        stmt = (
            select(TestRunTestCase)
            .options(joinedload(TestRunTestCase.test_case))
            .where(TestRunTestCase.test_run_id == test_run_id)
        )
        count_stmt = (
            select(func.count())
            .select_from(TestRunTestCase)
            .where(TestRunTestCase.test_run_id == test_run_id)
        )

        if status:
            stmt = stmt.where(TestRunTestCase.latest_status == status)
            count_stmt = count_stmt.where(TestRunTestCase.latest_status == status)

        if assignee:
            stmt = stmt.where(TestRunTestCase.assignee == assignee)
            count_stmt = count_stmt.where(TestRunTestCase.assignee == assignee)

        if search:
            stmt = stmt.join(TestCase).where(
                or_(
                    TestCase.name.ilike(f"%{search}%"),
                    TestCase.identifier.ilike(f"%{search}%"),
                )
            )
            count_stmt = count_stmt.join(TestCase).where(
                or_(
                    TestCase.name.ilike(f"%{search}%"),
                    TestCase.identifier.ilike(f"%{search}%"),
                )
            )

        stmt = stmt.order_by(TestRunTestCase.created_at.asc()).offset(offset).limit(limit)

        result = await self.session.execute(stmt)
        count_result = await self.session.execute(count_stmt)

        return list(result.scalars().unique().all()), count_result.scalar() or 0

    async def add_test_cases(
        self,
        test_run_id: UUID,
        test_cases: list[TestRunTestCase],
    ) -> list[TestRunTestCase]:
        """批量添加测试用例到测试运行"""
        for tc in test_cases:
            self.session.add(tc)
        await self.session.flush()
        return test_cases

    async def remove_test_cases(
        self,
        test_run_id: UUID,
        test_case_ids: list[UUID],
        configuration_ids: Optional[list[int]] = None,
    ) -> int:
        """批量移除测试用例"""
        conditions = [
            TestRunTestCase.test_run_id == test_run_id,
            TestRunTestCase.test_case_id.in_(test_case_ids),
        ]
        if configuration_ids:
            conditions.append(TestRunTestCase.configuration_id.in_(configuration_ids))

        stmt = delete(TestRunTestCase).where(and_(*conditions))
        result = await self.session.execute(stmt)
        await self.session.flush()
        return result.rowcount

    async def update_assignees(
        self,
        test_run_id: UUID,
        assignments: list[dict],
    ) -> int:
        """批量更新负责人"""
        count = 0
        for assignment in assignments:
            conditions = [
                TestRunTestCase.test_run_id == test_run_id,
                TestRunTestCase.test_case_id == assignment["test_case_id"],
            ]
            if assignment.get("configuration_id"):
                conditions.append(
                    TestRunTestCase.configuration_id == assignment["configuration_id"]
                )
# noqa  My80OmFIVnBZMlhscm9ua3VMazZNblZYZHc9PToxMzcyZDdkNw==

            stmt = (
                update(TestRunTestCase)
                .where(and_(*conditions))
                .values(assignee=assignment["assignee"])
            )
            result = await self.session.execute(stmt)
            count += result.rowcount

        await self.session.flush()
        return count

    async def update_status(
        self,
        id: UUID,
        status: TestResultStatus,
        result_id: Optional[UUID] = None,
    ) -> TestRunTestCase:
        """更新测试用例状态"""
        stmt = (
            update(TestRunTestCase)
            .where(TestRunTestCase.id == id)
            .values(latest_status=status, latest_result_id=result_id)
            .returning(TestRunTestCase)
        )
        result = await self.session.execute(stmt)
        await self.session.flush()
        return result.scalar_one()


class TestRunScriptJobRepository:
    """测试运行脚本作业仓储"""

    def __init__(self, session: AsyncSession):
        self.session = session

    async def get_by_id(self, job_id: UUID) -> Optional[TestRunScriptJob]:
        """根据 ID 获取脚本作业"""
        stmt = select(TestRunScriptJob).where(TestRunScriptJob.id == job_id)
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def get_by_test_run(
        self,
        test_run_id: UUID,
        status: Optional[JobStatus] = None,
        offset: int = 0,
        limit: int = 100,
    ) -> tuple[list[TestRunScriptJob], int]:
        """获取测试运行下的脚本作业列表"""
        stmt = select(TestRunScriptJob).where(
            TestRunScriptJob.test_run_id == test_run_id
        )
        count_stmt = (
            select(func.count())
            .select_from(TestRunScriptJob)
            .where(TestRunScriptJob.test_run_id == test_run_id)
        )

        if status:
            stmt = stmt.where(TestRunScriptJob.status == status)
            count_stmt = count_stmt.where(TestRunScriptJob.status == status)

        stmt = (
            stmt.order_by(TestRunScriptJob.execution_order.asc())
            .offset(offset)
            .limit(limit)
        )

        result = await self.session.execute(stmt)
        count_result = await self.session.execute(count_stmt)
        return list(result.scalars().all()), count_result.scalar() or 0

    async def create(self, job: TestRunScriptJob) -> TestRunScriptJob:
        """创建脚本作业"""
        self.session.add(job)
        await self.session.flush()
        return job

    async def update(self, job: TestRunScriptJob) -> TestRunScriptJob:
        """更新脚本作业"""
        await self.session.flush()
        await self.session.refresh(job)
        return job

    async def update_status(
        self,
        job_id: UUID,
        status: JobStatus,
        started_at: Optional[datetime] = None,
        completed_at: Optional[datetime] = None,
        duration_ms: Optional[int] = None,
        error_message: Optional[str] = None,
        stdout: Optional[str] = None,
        stderr: Optional[str] = None,
        report_path: Optional[str] = None,
        result_summary: Optional[dict] = None,
    ) -> None:
        """更新脚本作业状态"""
        values: dict[str, Any] = {"status": status}
        if started_at is not None:
            values["started_at"] = started_at
        if completed_at is not None:
            values["completed_at"] = completed_at
        if duration_ms is not None:
            values["duration_ms"] = duration_ms
        if error_message is not None:
            values["error_message"] = error_message
        else:
            # 成功时清空旧的错误信息
            values["error_message"] = None
        if stdout is not None:
            values["stdout"] = stdout
        if stderr is not None:
            values["stderr"] = stderr
        if report_path is not None:
            values["report_path"] = report_path
        if result_summary is not None:
            values["result_summary"] = result_summary

        stmt = (
            update(TestRunScriptJob)
            .where(TestRunScriptJob.id == job_id)
            .values(**values)
        )
        await self.session.execute(stmt)
        await self.session.flush()

    async def delete(self, job: TestRunScriptJob) -> None:
        """删除脚本作业"""
        await self.session.delete(job)
        await self.session.flush()


# 在 TestRunRepository 中添加 update_counts_from_jobs 方法
async def _update_counts_from_jobs(
    self,
    test_run_id: UUID,
) -> None:
    """根据脚本作业结果更新测试运行统计"""
    stmt = select(TestRunScriptJob).where(TestRunScriptJob.test_run_id == test_run_id)
    result = await self.session.execute(stmt)
    jobs = list(result.scalars().all())

    passed = 0
    failed = 0
    skipped = 0
    pending = 0
    for job in jobs:
        status = job.status
        if hasattr(status, "value"):
            status = status.value

        summary = job.result_summary or {}
        total = int(summary.get("total") or 0)
        summary_failed = int(summary.get("failed") or 0)
        summary_passed = int(summary.get("passed") or 0)
        summary_skipped = int(summary.get("skipped") or 0)

        if status == JobStatus.COMPLETED.value:
            if total > 0 and summary_failed > 0:
                failed += 1
            elif total > 0 and summary_passed == 0 and summary_skipped >= total:
                skipped += 1
            else:
                passed += 1
        elif status == JobStatus.FAILED.value:
            failed += 1
        elif status == JobStatus.SKIPPED.value:
            skipped += 1
        elif status in {JobStatus.PENDING.value, JobStatus.RUNNING.value}:
            pending += 1

    update_stmt = (
        update(TestRun)
        .where(TestRun.id == test_run_id)
        .values(
            passed_count=passed,
            failed_count=failed,
            skipped_count=skipped,
            not_executed_count=pending,
            # 同步脚本作业总数到 test_cases_count，确保前端进度条正确显示
            test_cases_count=passed + failed + skipped + pending,
        )
    )
    await self.session.execute(update_stmt)
    await self.session.flush()


# 将方法绑定到 TestRunRepository
TestRunRepository.update_counts_from_jobs = _update_counts_from_jobs
