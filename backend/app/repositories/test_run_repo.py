"""
测试运行仓储

提供测试运行数据访问层
参考: https://www.browserstack.com/docs/test-management/api-reference/test-runs
"""

from typing import Optional, Any
from uuid import UUID
from datetime import datetime
import asyncio
import logging
import os
import time
# noqa  MC80OmFIVnBZMlhscm9ua3VMazZNblZYZHc9PToxMzcyZDdkNw==

from sqlalchemy import select, func, and_, or_, update, delete, text
from sqlalchemy.orm import selectinload, joinedload
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.test_run import TestRun, TestRunTestCase, TestRunScriptJob
from app.models.test_case import TestCase
from app.models.project import Project
from app.schemas.enums import TestRunState, TestRunActiveState, TestResultStatus, JobStatus
from app.utils.exceptions import ConflictException

logger = logging.getLogger(__name__)

#: TR 编号生成的事务级互斥键（"TRID"）。所有写入方共用同一把锁。
_IDENTIFIER_LOCK_KEY = 0x5452_4944
#: 等锁上限（秒）。超过则快速失败，不让创建接口静默挂死。
#: 锁从 generate 一直持有到调用方 commit —— 期间还包含脚本作业/用例的插入，
#: 因此创建大批用例时会占锁更久。默认 5s 覆盖常见规模（200 条用例插入 < 1s），
#: 极端批量场景可用 TR_IDENT_LOCK_TIMEOUT 调大。
_IDENTIFIER_LOCK_TIMEOUT = float(os.getenv("TR_IDENT_LOCK_TIMEOUT", "5") or 5)
#: 轮询间隔（秒）
_IDENTIFIER_LOCK_POLL = 0.02


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
    
    async def _acquire_identifier_lock(self) -> None:
        """把"读最大编号 → 插入"这段临界区串行化。

        rev97（2026-10-17）：``generate_identifier`` 原本是纯 max+1 —— 两个并发
        创建请求会读到同一个最大值，随后插入同一个 identifier，撞
        ``ix_test_runs_identifier`` 抛 IntegrityError 变成 HTTP 500。
        实测（并发 6、3 轮）18 次创建有 15 次失败，只有 3 次成功。

        为什么用**事务级**咨询锁 ``pg_advisory_xact_lock``：
        - 调用方（``test_run_service.create`` / 调度器 / 两条 link 服务）都在
          同一 session 内完成 generate → create → flush → commit，锁覆盖整个临界区，
          提交/回滚时由数据库自动释放，不需要手工 unlock，也就没有"漏放锁"的死锁风险；
        - 用 ``try`` 版本轮询而不是阻塞版：阻塞版会无限等待，一旦持锁事务卡住，
          创建接口就会静默挂死；这里超时后抛明确异常，调用方可以快速失败。

        非 PostgreSQL（SQLite 单测等）直接返回：这类库本身写串行，也没有事务级
        咨询锁，保持原行为即可。
        """
        try:
            dialect = self.session.get_bind().dialect.name
        except Exception:  # noqa: BLE001 - 拿不到方言就不加锁，不阻断主流程
            logger.warning("[TestRunRepo] 无法识别数据库方言，跳过编号互斥锁")
            return
        if dialect != "postgresql":
            return
        deadline = time.monotonic() + _IDENTIFIER_LOCK_TIMEOUT
        while True:
            got = (
                await self.session.execute(
                    text("SELECT pg_try_advisory_xact_lock(:key)"),
                    {"key": _IDENTIFIER_LOCK_KEY},
                )
            ).scalar()
            if got:
                return
            if time.monotonic() >= deadline:
                # 用 409 而不是裸 RuntimeError：这是"并发写冲突，稍后重试"，
                # 客户端可重试；裸异常会变成 500，看起来像服务端 bug。
                raise ConflictException(
                    f"获取测试运行编号互斥锁超时（{_IDENTIFIER_LOCK_TIMEOUT}s）："
                    "可能有其他创建请求持锁未提交，请稍后重试"
                )
            await asyncio.sleep(_IDENTIFIER_LOCK_POLL)

    async def generate_identifier(self, project_id: UUID) -> str:
        """生成测试运行标识符

        使用项目内最大序号 + 1 的方式生成唯一标识符，避免并发冲突和删除后重复问题。
        由于数据库中 identifier 是全局唯一的（不是按项目隔离），需要确保生成的标识符在所有项目中唯一。

        rev97：进入前先取事务级互斥锁（见 ``_acquire_identifier_lock``），
        否则并发创建会同时拿到同一个 max，插入时撞唯一键。
        """
        await self._acquire_identifier_lock()

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

    async def get_by_test_run_and_case(
        self,
        test_run_id: UUID,
        test_case_id: UUID,
    ) -> Optional[TestRunTestCase]:
        """按 (运行, 用例) 取关联行。"""
        stmt = select(TestRunTestCase).where(
            TestRunTestCase.test_run_id == test_run_id,
            TestRunTestCase.test_case_id == test_case_id,
        )
        return (await self.session.execute(stmt)).scalar_one_or_none()

    async def apply_case_result(
        self,
        *,
        test_run_id: UUID,
        test_case_id: UUID,
        status: TestResultStatus,
        writer: str,
        result_id: Optional[UUID] = None,
        job_id: Any = None,
        execution_order: Any = None,
        retry_count: Any = None,
        create_if_missing: bool = False,
    ) -> tuple[Optional[TestRunTestCase], Any]:
        """**受守卫保护的**用例结论写入（N2）。

        与 ``update_status`` 的区别：先判断"本次写入是否该生效"——旧作业不得覆盖
        新轮次，同轮次下低优先级的写入者（场景级兜底）不得覆盖细粒度结论。

        Returns:
            ``(row, decision)``。``decision.allowed=False`` 时**未做任何写入**，
            调用方据此跳过明细写入（避免"状态没变、明细却换了"的二次不一致）。
        """
        from app.services.case_result_write_guard import (
            CaseWriteState,
            build_attempt,
            decide_write,
        )

        attempt = build_attempt(
            writer,
            job_id=job_id,
            execution_order=execution_order,
            retry_count=retry_count,
        )

        def _job_uuid() -> Optional[UUID]:
            if not job_id:
                return None
            try:
                return UUID(str(job_id))
            except (TypeError, ValueError):
                return None

        row = await self.get_by_test_run_and_case(test_run_id, test_case_id)
        if row is None:
            if not create_if_missing:
                return None, decide_write(CaseWriteState(), attempt)
            row = TestRunTestCase(
                test_run_id=test_run_id,
                test_case_id=test_case_id,
                latest_status=status,
                latest_job_id=_job_uuid(),
                latest_execution_order=attempt.execution_order,
                latest_retry_count=attempt.retry_count,
                latest_writer=attempt.writer,
                latest_result_id=result_id,
            )
            self.session.add(row)
            await self.session.flush()
            return row, decide_write(CaseWriteState(), attempt)

        decision = decide_write(CaseWriteState.from_row(row), attempt)
        if not decision.allowed:
            logger.info(
                "[TestRunRepo] 拒绝写入用例结论 %s/%s（%s）：%s",
                test_run_id, test_case_id, decision.reason, decision.detail,
            )
            return row, decision

        row.latest_status = status
        row.latest_result_id = result_id
        row.latest_job_id = _job_uuid()
        row.latest_execution_order = attempt.execution_order
        row.latest_retry_count = attempt.retry_count
        row.latest_writer = attempt.writer
        await self.session.flush()
        return row, decision


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
