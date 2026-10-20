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
from app.services.web_case_result_mapper import stdout_has_executable_assertions
from app.services.execution.schedulers import (
    ParallelScheduler,
    SequentialScheduler,
)

logger = logging.getLogger(__name__)

# 模块级取消状态，保证跨实例共享（cancel_run 可能由不同 TestExecutionService 实例调用）
_cancelled_runs: Set[UUID] = set()
_active_executors: Dict[UUID, Any] = {}


def classify_job_failure(error_message: str) -> tuple[str, str]:
    """作业失败分类（确定性关键词规则，无 LLM）——决定用例级终态映射。

    返回 ``(kind, label)``：

    - ``precondition``（登录/凭据/授权类前置）→ 用例 BLOCKED，如"执行前置失败"；
    - ``environment``（网络/超时/限流/WAF/服务不可用）→ 用例 BLOCKED，如"环境阻断"；
    - ``script``（脚本运行期错误：ReferenceError/TypeError/语法/选择器）→ 用例 FAILED；
    - ``unknown``（未分类）→ 用例 FAILED（保留原始错误，不包装成前置失败）。

    2026-09-29 复核：FAILED 无断言**不能统一**映射 BLOCKED——脚本崩溃与登录前置
    失败必须区分，且只有确认前置/环境类才归 BLOCKED。
    """
    text = (error_message or "").lower()
    if not text.strip():
        return "unknown", "未分类失败"
    if any(k in text for k in (
        "登录", "登入", "login", "验证码", "授权", "凭据", "凭证", "未登录",
        "口令", "密码错误", "oauth", "token", "身份",
    )):
        return "precondition", "执行前置失败"
    # 脚本类先于环境类：定位器超时（locator … timed out）是脚本问题，
    # 不能被环境类的 "timed out" 关键词抢走（2026-09-29 分类顺序修正）。
    if any(k in text for k in (
        "referenceerror", "typeerror", "syntaxerror", "is not defined",
        "cannot read", "is not a function", "selector", "locator", "定位器",
    )):
        return "script", "脚本错误"
    if any(k in text for k in (
        "超时", "timeout", "timed out", "网络", "连接", "connection", "refused",
        "econn", "enotfound", "dns", "waf", "阻断", "被拦截", "环境", "预检",
        "前置", "服务不可用", "502", "503", "504",
    )):
        return "environment", "环境阻断"
    return "unknown", "未分类失败"


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

            # 失败用例终态（2026-09-29 复核）：必须在全部作业完成后、update_counts 之前
            # 执行——顺序保证不被后续兄弟作业的细粒度落库覆盖；只升级仍 NOT_EXECUTED
            # 的用例（有有效断言结果的绝不动），并按失败分类映射 BLOCKED/FAILED。
            try:
                await self._finalize_failed_job_cases(test_run_id, jobs)
            except Exception as exc:  # noqa: BLE001 —— 终态回写失败不得影响运行结论
                logger.warning("[ScriptExecutionEngine] 失败用例终态回写失败（忽略）: %s", exc)

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
                # rev59：计数口径统一为"用例级"（TestRunTestCase.latest_status 汇总；
                # 作业结果已由 _sync_test_run_case_status 回写到其绑定的用例）。
                # 未绑定具体用例的作业，执行结果以作业列表/报告呈现，不冒充用例通过数。
                await run_repo.update_counts(test_run_id)
                # R5：两类 × 5 指标的物化（明细仍走 /assertion-ledger）。
                # 失败不得影响运行结论——指标缺失可重算，运行结论丢了不可恢复。
                try:
                    from app.services.run_assertion_metrics import refresh_run_assertion_metrics

                    await refresh_run_assertion_metrics(session, test_run_id)
                except Exception as exc:  # noqa: BLE001
                    logger.warning("[ScriptExecutionEngine] 断言指标落库失败（忽略）: %s", exc)
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
                # rev59：计数口径统一为"用例级"（TestRunTestCase.latest_status 汇总；
                # 作业结果已由 _sync_test_run_case_status 回写到其绑定的用例）。
                # 未绑定具体用例的作业，执行结果以作业列表/报告呈现，不冒充用例通过数。
                await run_repo.update_counts(test_run_id)
                # R5：两类 × 5 指标的物化（明细仍走 /assertion-ledger）。
                # 失败不得影响运行结论——指标缺失可重算，运行结论丢了不可恢复。
                try:
                    from app.services.run_assertion_metrics import refresh_run_assertion_metrics

                    await refresh_run_assertion_metrics(session, test_run_id)
                except Exception as exc:  # noqa: BLE001
                    logger.warning("[ScriptExecutionEngine] 断言指标落库失败（忽略）: %s", exc)
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
            # 把作业层重试上下文交给执行器：外层的"重试"与执行器内的 flake 重试必须协同，
            # 否则同一次失败会被两套机制各记一次（重复尝试 / 重复建单）。
            config = {
                **config,
                "job_retry_count": int(job.retry_count or 0),
                "job_test_run_id": str(test_run_id),
                # N2：用例结论写入守卫需要"本轮是谁、第几轮"——否则重跑/重试的旧作业
                # 完成后仍能覆盖新轮次结论（没有任何顺序保证）。
                "job_id": str(job.id),
                "job_execution_order": int(job.execution_order or 0),
            }
# pylint: disable  My80OmFIVnBZMlhscm9ua3VMazZVRzlHVWc9PTozNDZjMWJmYg==

            # 作业总超时（2026-09-29 复核）：对整个作业（下载脚本/预检/运行器）设上限，
            # 防"OCR 重试循环"类无总超时脚本永久占用浏览器与登录请求。
            # 超时流程：取消协程（执行器协作式停止重试/关上下文）→ 终态回写
            # [作业超时]（走既有失败路径，用例级由 _finalize_failed_job_cases 归类）。
            from app.config.settings import settings as _settings
            job_timeout_s = int(getattr(_settings, "job_timeout_seconds", 1800) or 1800)
            try:
                result = await asyncio.wait_for(
                    executor.execute(script_id=job.script_id, config=config),
                    timeout=job_timeout_s,
                )
            except asyncio.TimeoutError:
                logger.error(
                    "[ScriptExecutionEngine] 作业 %s 超时（%ds），请求取消并终态回写",
                    job.id, job_timeout_s,
                )
                try:
                    maybe_coro = executor.cancel()
                    if asyncio.iscoroutine(maybe_coro):
                        await asyncio.wait_for(maybe_coro, timeout=10)
                except Exception as exc:  # noqa: BLE001 —— 清理失败不掩盖超时事实
                    logger.warning("[ScriptExecutionEngine] 超时后取消清理失败: %s", exc)
                result = ExecutionResult(
                    success=False,
                    status=JobStatus.FAILED.value,
                    error_message=f"[作业超时] 超过 {job_timeout_s} 秒，已请求取消并终止",
                    duration_ms=job_timeout_s * 1000,
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
                stdout=result.stdout,
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

    @staticmethod
    def _should_skip_coarse_case_sync(run_case: Any) -> bool:
        """是否应跳过"作业结果 → 用例状态"的粗粒度同步。

        注意：**有可执行断言的 WEB 作业已在 `_sync_test_run_case_status` 开头整体让位**
        （连进入本函数的机会都没有）。这里保留的判据只服务两类剩余情形：

        - 零断言作业（冒烟/旧资产），但该用例已被细粒度写入过明细；
        - 迁移前的历史行（没有 ``latest_writer``/轮次，只能靠 ``latest_result_id`` 判断）。

        历史语义（保留作背景）：该用例已有**明细**（``latest_result_id`` 指向一条
        TestResult），说明它由更细粒度的用例级落库写入过（逐条断言可解释）。
        此时粗粒度的"作业通过 → 该用例通过"会丢失语义甚至误判
        （实测 TR-60：只有叙述性内容的用例被标成 PASSED）。
        """
        return getattr(run_case, "latest_result_id", None) is not None

    async def _sync_test_run_case_status(
        self,
        test_run_id: UUID,
        job: TestRunScriptJob,
        status: JobStatus,
        result_summary: dict[str, Any],
        stdout: Optional[str] = None,
    ) -> None:
        """把脚本作业结果同步到测试运行内的用例状态。

        P1-①（2026-10-17）：WEB_TEST 作业新增**零断言守卫**——脚本里没有
        任何 ``COMPILED_ASSERTION``/上传证据时，playwright 的 ``1 passed``
        只说明"入口能打开"，不构成用例验证通过。此时 COMPLETED 不得映射
        PASSED，落 NOT_EXECUTED（与用例级落库 mapper 的语义一致：
        "没有可执行断言 → 无法自动执行"）。
        """
        try:
            from sqlalchemy import select

            from app.models.api_test import APITest
            from app.models.test_run import TestRunTestCase
            from app.models.web_test import WebTest

            script_type = job.script_type.value if hasattr(job.script_type, "value") else str(job.script_type)
            async with async_session_factory() as session:
                # N2 唯一写入者（2026-10-17）：**编译断言脚本**的用例结论只由 mapper 写。
                #
                # 依据是"该作业是否产出过可判定断言"（与 mapper 同源判据）。有断言时，
                # 场景级的"作业成功即用例通过"天然会丢失语义（1 pass + 1 unverified 的产物
                # 会被判 PASSED，而 mapper 判 SKIPPED——已实测复现同一产物两种结论）。
                # 因此这里不是"跳过覆盖"，而是**根本不参与写入**：即使 mapper 因配置问题
                # 一条都没写，用例也应停在未执行并暴露问题，而不是被兜底成通过。
                has_assertions = False
                if script_type == ScriptType.WEB_TEST.value:
                    has_assertions = stdout_has_executable_assertions(stdout or "")
                    if not has_assertions:
                        has_assertions = await self._job_stdout_has_assertions(job.id)
                    if has_assertions:
                        logger.info(
                            "[ScriptExecutionEngine] 作业 %s 含可执行断言，用例结论由 mapper "
                            "唯一写入，跳过场景级同步（脚本 %s）",
                            job.id, job.script_id,
                        )
                        return

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
                if run_case and self._should_skip_coarse_case_sync(run_case):
                    # 已有**更细粒度**的用例级结果（带断言明细，由用例级落库写入）→
                    # 粗粒度的"作业成功就算该用例通过"不得覆盖它。
                    # 否则会出现同一运行内"有的用例带明细、有的只带状态"，且把只有
                    # 叙述性内容的用例误标为 PASSED（实测 TR-60 的 TC-572042）。
                    #
                    # 注意（2026-09-29 复核）：作业失败且无断言证据时的用例升级
                    # **不得在这里做**——同步发生在每个作业结束后，后续兄弟作业的
                    # 细粒度落库会把结论冲掉（实测 blocked 被后执行作业覆盖回
                    # not_executed）。升级统一由 `_finalize_failed_job_cases`
                    # 在**全部作业完成后**执行（顺序保证 + 失败分类）。
                    logger.info(
                        "[ScriptExecutionEngine] 用例 %s 已有细粒度结果，跳过粗粒度同步",
                        test_case_id,
                    )
                    return

                # P1-① 零断言守卫：WEB_TEST 作业 COMPLETED 但脚本从未产出任何
                # 断言证据时，"1 passed"只代表入口冒烟通过，用例不得记 PASSED。
                # （上面已把"有断言"的作业整体让给 mapper，走到这里必然是零断言。）
                effective_status = self._job_to_case_result_status(
                    status,
                    result_summary,
                )
                if (
                    script_type == ScriptType.WEB_TEST.value
                    and status == JobStatus.COMPLETED
                    and effective_status == TestResultStatus.PASSED
                ):
                    effective_status = TestResultStatus.NOT_EXECUTED
                    logger.info(
                        "[ScriptExecutionEngine] 作业 %s 零断言（无 COMPILED_ASSERTION/"
                        "UPLOAD_EVIDENCE），用例 %s 落 NOT_EXECUTED（不冒充通过）",
                        job.id, test_case_id,
                    )

                # N2：场景级兜底同样受守卫约束——旧作业不得覆盖新轮次；同轮次里
                # 不得覆盖 mapper/manual 已写的结论（与完成顺序无关）。
                from app.repositories.test_run_repo import TestRunTestCaseRepository

                _row, decision = await TestRunTestCaseRepository(session).apply_case_result(
                    test_run_id=test_run_id,
                    test_case_id=test_case_id,
                    status=effective_status,
                    writer="engine",
                    job_id=job.id,
                    execution_order=job.execution_order,
                    retry_count=job.retry_count,
                    create_if_missing=True,
                )
                if not decision.allowed:
                    logger.info(
                        "[ScriptExecutionEngine] 用例 %s 场景级写入被拒绝（%s）：%s",
                        test_case_id, decision.reason, decision.detail,
                    )
                await session.commit()
        except Exception as e:
            logger.warning("[ScriptExecutionEngine] 同步运行用例状态失败: %s", e)

    async def _finalize_failed_job_cases(
        self,
        test_run_id: UUID,
        jobs: list[TestRunScriptJob],
    ) -> None:
        """全部作业完成后的失败用例终态回写（2026-09-29 复核）。

        规则：
        - 只处理 status=FAILED 且带错误信息的作业；
        - 只升级**仍为 NOT_EXECUTED** 的用例行——已有有效断言结果的绝不动；
        - 按 `classify_job_failure` 分类映射：precondition/environment → BLOCKED，
          script/unknown → FAILED（保留原始错误，不包装成前置失败）；
        - 作业错误写入最新 TestResult 明细（description 带分类标签），
          区分"未开始 / 执行前置失败 / 环境阻断 / 脚本错误"。
        """
        from app.models.api_test import APITest
        from app.models.test_result import TestResult
        from app.models.web_test import WebTest

        failed_jobs = [
            j for j in jobs
            if (j.status.value if hasattr(j.status, "value") else str(j.status)) == JobStatus.FAILED.value
            and (j.error_message or "").strip()
        ]
        if not failed_jobs:
            return
        async with async_session_factory() as session:
            for job in failed_jobs:
                kind, label = classify_job_failure(job.error_message)
                target = (
                    TestResultStatus.BLOCKED
                    if kind in ("precondition", "environment")
                    else TestResultStatus.FAILED
                )
                script_type = (
                    job.script_type.value if hasattr(job.script_type, "value")
                    else str(job.script_type)
                )
                test_case_id = await self._resolve_job_test_case_id(
                    session, script_type, job.script_id, APITest, WebTest,
                )
                if not test_case_id:
                    continue
                run_case = (
                    await session.execute(
                        select(TestRunTestCase)
                        .where(TestRunTestCase.test_run_id == test_run_id)
                        .where(TestRunTestCase.test_case_id == test_case_id)
                    )
                ).scalar_one_or_none()
                if run_case is None or run_case.latest_status != TestResultStatus.NOT_EXECUTED:
                    continue  # 无该行 or 已有有效结论 → 绝不覆盖

                # N2：终态升级也走守卫——旧作业（execution_order/retry_count 更小）的
                # 失败终态不得覆盖新轮次结论；只有能升到"新轮次"时才写。
                from app.repositories.test_run_repo import TestRunTestCaseRepository

                _row, decision = await TestRunTestCaseRepository(session).apply_case_result(
                    test_run_id=test_run_id,
                    test_case_id=test_case_id,
                    status=target,
                    writer="finalize",
                    result_id=run_case.latest_result_id,
                    job_id=job.id,
                    execution_order=job.execution_order,
                    retry_count=job.retry_count,
                    create_if_missing=False,
                )
                if not decision.allowed:
                    logger.info(
                        "[ScriptExecutionEngine] 失败终态被守卫拒绝：作业 %s → 用例 %s（%s）",
                        job.script_identifier, test_case_id, decision.reason,
                    )
                    continue
                run_case = _row or run_case
                detail = (
                    await session.get(TestResult, run_case.latest_result_id)
                    if run_case.latest_result_id is not None else None
                )
                if detail is not None:
                    if detail.status == TestResultStatus.NOT_EXECUTED:
                        detail.status = target
                    detail.description = (
                        f"[{label}] {str(job.error_message)[:500]}"
                    )
                logger.info(
                    "[ScriptExecutionEngine] 失败用例终态：作业 %s → 用例 %s = %s（%s）",
                    job.script_identifier, test_case_id, target.value, label,
                )
            await session.commit()

    async def _job_stdout_has_assertions(self, job_id: UUID) -> bool:
        """回落判据：从库里读作业 stdout（内存 job 对象在 _update_job_status
        之后可能仍是旧值，stdout 以落库为准）。"""
        try:
            async with async_session_factory() as session:
                row = await session.get(TestRunScriptJob, job_id)
                return bool(row is not None and stdout_has_executable_assertions(row.stdout or ""))
        except Exception as e:  # noqa: BLE001 —— 判据读取失败不阻断状态同步
            logger.warning("[ScriptExecutionEngine] 读取作业 stdout 失败（按无断言处理）: %s", e)
            return False

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
