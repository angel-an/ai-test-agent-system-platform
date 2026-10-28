"""
脚本执行器注册表与具体实现

支持 Playwright、场景测试、Web 测试等执行器，按 ScriptType 自动分发。
"""
"""
andan
"""


import json
import logging
import os
import re
import uuid
from abc import ABC, abstractmethod
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional
from uuid import UUID

from app.config.database import async_session_factory
from app.config.minio_client import MinIOClient
from app.config.settings import settings
from app.repositories.api_test_repo import (
    APITestRepository,
    APITestRunRepository,
    api_run_verdict,
)
from app.schemas.enums import JobStatus
from app.services.execution.models import ExecutionResult
from app.services.execution.runner import PlaywrightRunner
from app.services.web_failure_policy import (
    ENVIRONMENT_BLOCK_MARKER,
    classify_environment_block,
    flake_retry_budget,
    flake_retry_note,
    run_attempts_with_policy,
)
from app.services.api_runtime_data_service import (
    resolve_api_runtime_config,
    sanitize_api_execution_config,
)

import asyncio
import tempfile
import zipfile
from sqlalchemy import select
from sqlalchemy.orm import selectinload
from app.models.test_scenario import ScenarioRun, ScenarioStepResult, TestScenario
from app.services.execution.scenario_report import generate_scenario_report

logger = logging.getLogger(__name__)

#: 只有确实没有任何错误信息、且全部未执行时，才允许用"跳过"解释
WEB_SKIPPED_HINT = "Web 测试已跳过：缺少运行时登录凭据或脚本前置条件不满足。"


def summarize_web_run_result(result: Dict[str, Any]) -> Dict[str, Any]:
    """把 WebTestService 的返回映射为作业判定结果（纯函数，便于回归测试）。

    关键点：
    - 有错误信息时**不得**判为"跳过"：浏览器/页面被关闭、定位器超时等必须暴露真实原因；
    - 通过运行同样保留 stdout，否则编译断言的逐条证据无法复核。
    """
    summary = {
        "total": result.get("total_tests", 0) or 0,
        "passed": result.get("passed_tests", 0) or 0,
        "failed": result.get("failed_tests", 0) or 0,
        "skipped": result.get("skipped_tests", 0) or 0,
    }
    error_msg = result.get("error_message") or result.get("error")
    success = result.get("status") != "failed"
    skipped_only = (
        not error_msg
        and summary["total"] > 0
        and summary["passed"] == 0
        and summary["failed"] == 0
        and summary["skipped"] >= summary["total"]
    )
    return {
        "success": success,
        "status": (
            JobStatus.SKIPPED.value if skipped_only
            else JobStatus.COMPLETED.value if success
            else JobStatus.FAILED.value
        ),
        "error_message": (
            WEB_SKIPPED_HINT if skipped_only else None if success else error_msg
        ),
        "stdout": result.get("stdout", "")[:50000] if result.get("stdout") else None,
        "stderr": result.get("stderr", "")[:50000] if result.get("stderr") else None,
        "result_summary": summary,
        "preflight": result.get("preflight"),
    }


def _detect_script_language(content: str) -> str:
    """根据脚本内容特征检测语言类型

    用于防止 Python 代码被保存为 .spec.ts 导致 Playwright 解析失败。
    """
    content_stripped = content.strip()
    first_lines = '\n'.join(content_stripped.split('\n')[:20]).lower()

    python_indicators = [
        'import asyncio', 'import json', 'import os', 'import sys',
        'from playwright', 'from datetime import', 'import pytest',
        'def test_', 'async def ', 'if __name__ == ',
        'import requests', 'from typing import',
    ]
    ts_indicators = [
        'import { test', 'import { expect }', 'import test from',
        'test.describe(', 'test.beforeeach(', 'test.aftereach(',
        'import { chromium', 'import { page', 'const { test',
        'playwright.config', 'page.goto(', 'page.click(',
        'page.fill(', 'page.locator(', 'expect(page',
    ]

    python_score = sum(1 for ind in python_indicators if ind in first_lines)
    ts_score = sum(1 for ind in ts_indicators if ind in first_lines)

    if python_score >= 2 and python_score > ts_score:
        return "python"
    if ts_score >= 2 and ts_score > python_score:
        return "typescript"
    return "typescript"  # 默认


class ScriptExecutor(ABC):
    """脚本执行器抽象基类"""

    @abstractmethod
    async def execute(self, script_id: UUID, config: Dict[str, Any]) -> ExecutionResult:
        """执行单个脚本"""
        ...

    @abstractmethod
    async def cancel(self) -> None:
        """取消当前执行"""
        ...


class PlaywrightExecutor(ScriptExecutor):
    """Playwright API 测试执行器"""
# fmt: off  MC80OmFIVnBZMlhscm9ua3VMazZjbVZPWWc9PToyMjQ0NGNkNA==

    def __init__(self, mongodb=None):
        self.mongodb = mongodb
        self._cancelled = False
        self.workspace_dir = Path(settings.api_workspace_root).resolve()
        self.runner = PlaywrightRunner(self.workspace_dir)

    async def execute(self, script_id: UUID, config: Dict[str, Any]) -> ExecutionResult:
        """
        执行 Playwright API 测试脚本。

        流程：
        1. 加载 APITest 并创建 APITestRun
        2. 从 MinIO 下载脚本到 workspace/tests/
        3. 使用 PlaywrightRunner 异步执行
        4. 更新 APITestRun 状态并返回结果
        """
        start_time = datetime.now(timezone.utc)
        run_id: Optional[UUID] = None

        # 1. 加载 APITest 并创建运行记录
        async with async_session_factory() as session:
            api_test_repo = APITestRepository(session)
            run_repo = APITestRunRepository(session)

            api_test = await api_test_repo.get_by_id(script_id)
            if not api_test:
                return ExecutionResult(
                    success=False,
                    status=JobStatus.FAILED.value,
                    error_message=f"API 测试不存在: {script_id}",
                )

            identifier = await run_repo.get_next_identifier(script_id)
            test_run = await run_repo.create(
                project_id=api_test.project_id,
                api_test_id=script_id,
                identifier=identifier,
                status="pending",
                execution_config=sanitize_api_execution_config(config),
                total_tests=0,
                passed_tests=0,
                failed_tests=0,
                skipped_tests=0,
            )
            run_id = test_run.id
            await session.commit()

        # 2. 更新为 RUNNING（独立 session，避免长事务）
        if run_id:
            try:
                async with async_session_factory() as session:
                    run_repo = APITestRunRepository(session)
                    run_record = await run_repo.get_by_id(run_id)
                    if run_record:
                        await run_repo.update(run_record, status="running")
                        await session.commit()
            except Exception as e:
                logger.warning("[PlaywrightExecutor] 更新运行状态为 running 失败: %s", e)

        # 3. 下载脚本
        script_file_path: Optional[Path] = None
        try:
            script_content = MinIOClient.download_file(api_test.script_path)
            script_content = script_content.decode("utf-8")

            # 自动检测脚本语言，确保文件扩展名与实际内容匹配
            detected_lang = _detect_script_language(script_content)
            if detected_lang == "python":
                file_extension = ".py"
                logger.warning(
                    "[PlaywrightExecutor] 检测到脚本实际为 Python 代码，"
                    "将使用 .py 扩展名执行"
                )
            else:
                file_extension = ".spec.ts"

            # 写入 workspace/tests/，使用唯一文件名避免冲突
            safe_name = f"run_{run_id}_{uuid.uuid4().hex[:8]}{file_extension}"
            script_file_path = self.workspace_dir / "tests" / safe_name
            script_file_path.write_text(script_content, encoding="utf-8")
            logger.info(
                "[PlaywrightExecutor] 脚本已下载: %s (检测语言: %s)",
                script_file_path, detected_lang
            )
        except Exception as e:
            logger.exception("[PlaywrightExecutor] 下载脚本失败")
            await self._update_run_failed(run_id, f"下载脚本失败: {e}")
            return ExecutionResult(
                success=False,
                status=JobStatus.FAILED.value,
                error_message=f"下载脚本失败: {e}",
            )

        # 4. 执行测试（同时生成 list + html 报告）。数据配置只在当前
        #    进程内解析，不能回写到 APITestRun.execution_config。
        runtime_config = resolve_api_runtime_config(config, api_test.test_config)
        config_with_reporter = {**runtime_config, "reporter": "list,html"}
        runner_result = await self.runner.run(
            script_path=script_file_path,
            config=config_with_reporter,
            timeout=runtime_config.get("timeout", 300),
        )

        # 5. 将 HTML 报告打包上传到 MinIO
        report_path = runner_result.report_path
        if report_path and Path(report_path).exists():
            try:
                import zipfile
                report_dir = Path(report_path)
                zip_path = report_dir.parent / f"report-{run_id}.zip"
                with zipfile.ZipFile(zip_path, 'w', zipfile.ZIP_DEFLATED) as zipf:
                    for file in report_dir.rglob('*'):
                        if file.is_file():
                            zipf.write(file, file.relative_to(report_dir))
                minio_path = f"playwright-reports/{run_id}/report.zip"
                with open(zip_path, 'rb') as f:
                    MinIOClient.upload_bytes(
                        object_name=minio_path,
                        data=f.read(),
                        content_type="application/zip",
                    )
                report_path = minio_path
                try:
                    zip_path.unlink()
                except Exception:
                    pass
            except Exception as e:
                logger.warning("[PlaywrightExecutor] 上传报告失败: %s", e)

        # 6. 清理临时脚本文件
        if script_file_path and script_file_path.exists():
            try:
                script_file_path.unlink()
            except Exception as e:
                logger.warning("[PlaywrightExecutor] 清理临时脚本失败: %s", e)

        # 7. 更新 APITestRun 结果
        duration_ms = runner_result.duration_ms
        if self._cancelled:
            await self._update_run_status(
                run_id, "cancelled", duration_ms=duration_ms
            )
            return ExecutionResult(
                success=False,
                status=JobStatus.CANCELLED.value,
                duration_ms=duration_ms,
                error_message="执行已被取消",
            )

        if runner_result.success:
            await self._update_run_completed(
                run_id,
                duration_ms=duration_ms,
                total=runner_result.result_summary.get("total", 0),
                passed=runner_result.result_summary.get("passed", 0),
                failed=runner_result.result_summary.get("failed", 0),
                skipped=runner_result.result_summary.get("skipped", 0),
                report_path=report_path,
            )
            return ExecutionResult(
                success=True,
                status=JobStatus.COMPLETED.value,
                duration_ms=duration_ms,
                stdout=runner_result.stdout[:50000] if runner_result.stdout else "",
                stderr=runner_result.stderr[:50000] if runner_result.stderr else "",
                report_path=report_path,
                result_summary=runner_result.result_summary,
                detail_run_id=str(run_id),
            )
        else:
            await self._update_run_failed(
                run_id,
                error_message=runner_result.error_message,
                duration_ms=duration_ms,
                report_path=report_path,
            )
            return ExecutionResult(
                success=False,
                status=JobStatus.FAILED.value,
                duration_ms=duration_ms,
                error_message=runner_result.error_message,
                stdout=runner_result.stdout[:50000] if runner_result.stdout else "",
                stderr=runner_result.stderr[:50000] if runner_result.stderr else "",
                report_path=report_path,
                detail_run_id=str(run_id),
            )

    async def cancel(self) -> None:
        self._cancelled = True

    async def _update_run_status(
        self,
        run_id: Optional[UUID],
        status: str,
        duration_ms: Optional[int] = None,
    ) -> None:
        if not run_id:
            return
        try:
            async with async_session_factory() as session:
                run_repo = APITestRunRepository(session)
                run_record = await run_repo.get_by_id(run_id)
                if run_record:
                    kwargs: Dict[str, Any] = {"status": status}
                    if duration_ms is not None:
                        kwargs["duration_ms"] = duration_ms
                    await run_repo.update(run_record, **kwargs)
                    await session.commit()
        except Exception as e:
            logger.warning("[PlaywrightExecutor] 更新运行状态失败: %s", e)

    async def _update_run_completed(
        self,
        run_id: Optional[UUID],
        duration_ms: int,
        total: int,
        passed: int,
        failed: int,
        skipped: int,
        report_path: Optional[str] = None,
    ) -> None:
        if not run_id:
            return
        try:
            async with async_session_factory() as session:
                run_repo = APITestRunRepository(session)
                run_record = await run_repo.get_by_id(run_id)
                if run_record:
                    await run_repo.update(
                        run_record,
                        status="completed",
                        duration_ms=duration_ms,
                        total_tests=total,
                        passed_tests=passed,
                        failed_tests=failed,
                        skipped_tests=skipped,
                        verdict=api_run_verdict(
                            total=total, passed=passed, failed=failed, skipped=skipped
                        ),
                        report_path=report_path,
                    )
                    await session.commit()
        except Exception as e:
            logger.warning("[PlaywrightExecutor] 更新运行完成状态失败: %s", e)

    async def _update_run_failed(
        self,
        run_id: Optional[UUID],
        error_message: Optional[str],
        duration_ms: Optional[int] = None,
        report_path: Optional[str] = None,
    ) -> None:
        if not run_id:
            return
        try:
            async with async_session_factory() as session:
                run_repo = APITestRunRepository(session)
                run_record = await run_repo.get_by_id(run_id)
                if run_record:
                    kwargs: Dict[str, Any] = {
                        "status": "failed",
                        "error_message": error_message,
                        # 失败路径不改计数：按行上已有计数推导（零结果 → empty）
                        "verdict": api_run_verdict(
                            total=run_record.total_tests,
                            passed=run_record.passed_tests,
                            failed=run_record.failed_tests,
                            skipped=run_record.skipped_tests,
                        ),
                    }
                    if duration_ms is not None:
                        kwargs["duration_ms"] = duration_ms
                    if report_path is not None:
                        kwargs["report_path"] = report_path
                    await run_repo.update(run_record, **kwargs)
                    await session.commit()
        except Exception as e:
            logger.warning("[PlaywrightExecutor] 更新运行失败状态失败: %s", e)

# pylint: disable  MS80OmFIVnBZMlhscm9ua3VMazZjbVZPWWc9PToyMjQ0NGNkNA==

class ScenarioExecutor(ScriptExecutor):
    """场景测试执行器（委托给现有 ScenarioExecutionEngine）"""

    def __init__(self):
        self._cancelled = False

    async def execute(self, script_id: UUID, config: Dict[str, Any]) -> ExecutionResult:
        from app.services.scenario_execution_engine import ScenarioExecutionEngine

        start_time = datetime.now(timezone.utc)

        async with async_session_factory() as session:
            engine = ScenarioExecutionEngine(session)
            try:
                variables = config.get("variables", {})
                base_url = config.get("base_url", "")

                scenario_run = await engine.execute(
                    scenario_id=script_id,
                    variables=variables,
                    base_url=base_url,
                )

                duration_ms = int(
                    (datetime.now(timezone.utc) - start_time).total_seconds() * 1000
                )

                if self._cancelled:
                    return ExecutionResult(
                        success=False,
                        status=JobStatus.CANCELLED.value,
                        duration_ms=duration_ms,
                        error_message="执行已被取消",
                    )

                success = getattr(scenario_run, "status", "failed") == "completed"
                run_status = JobStatus.COMPLETED.value if success else JobStatus.FAILED.value
# pragma: no cover  Mi80OmFIVnBZMlhscm9ua3VMazZjbVZPWWc9PToyMjQ0NGNkNA==

                # 加载场景名称
                scenario = await session.get(TestScenario, scenario_run.scenario_id)
                scenario_name = scenario.name if scenario else "未知场景"

                # 加载步骤结果
                step_results_stmt = (
                    select(ScenarioStepResult)
                    .where(ScenarioStepResult.run_id == scenario_run.id)
                    .order_by(ScenarioStepResult.step_order)
                    .options(selectinload(ScenarioStepResult.step))
                )
                step_results_raw = await session.execute(step_results_stmt)
                step_results_orm = step_results_raw.scalars().all()

                step_results_data = []
                for sr in step_results_orm:
                    step_results_data.append({
                        "step_order": sr.step_order,
                        "step_name": sr.step.name if sr.step else None,
                        "status": sr.status,
                        "duration_ms": sr.duration_ms,
                        "error_message": sr.error_message,
                        "request_data": sr.request_data,
                        "response_data": sr.response_data,
                        "extracted_data": sr.extracted_data,
                        "assertion_results": sr.assertion_results,
                    })

                result_summary = {
                    "total": getattr(scenario_run, "total_steps", 0),
                    "passed": getattr(scenario_run, "passed_steps", 0),
                    "failed": getattr(scenario_run, "failed_steps", 0),
                    "skipped": getattr(scenario_run, "skipped_steps", 0),
                }

                # 生成 HTML 报告并上传
                report_path: Optional[str] = None
                try:
                    report_html = generate_scenario_report(
                        scenario_name=scenario_name,
                        run_identifier=scenario_run.identifier,
                        run_status=scenario_run.status,
                        total_steps=result_summary["total"],
                        passed_steps=result_summary["passed"],
                        failed_steps=result_summary["failed"],
                        skipped_steps=result_summary["skipped"],
                        duration_ms=getattr(scenario_run, "duration_ms", duration_ms),
                        error_message=scenario_run.error_message,
                        step_results=step_results_data,
                    )

                    with tempfile.TemporaryDirectory() as tmpdir:
                        report_dir = Path(tmpdir) / "scenario-report"
                        report_dir.mkdir()
                        index_path = report_dir / "index.html"
                        index_path.write_text(report_html, encoding="utf-8")

                        zip_path = Path(tmpdir) / f"scenario-report-{scenario_run.id}.zip"
                        with zipfile.ZipFile(zip_path, 'w', zipfile.ZIP_DEFLATED) as zf:
                            for file in report_dir.rglob('*'):
                                if file.is_file():
                                    zf.write(file, file.relative_to(report_dir))

                        minio_path = f"scenario-reports/{scenario_run.id}/report.zip"
                        with open(zip_path, 'rb') as f:
                            MinIOClient.upload_bytes(
                                object_name=minio_path,
                                data=f.read(),
                                content_type="application/zip",
                            )
                        report_path = minio_path

                    # 更新 ScenarioRun 的报告路径
                    from sqlalchemy import update as sa_update
                    await session.execute(
                        sa_update(ScenarioRun)
                        .where(ScenarioRun.id == scenario_run.id)
                        .values(report_path=report_path)
                    )
                    await session.commit()
                except Exception as e:
                    logger.warning("[ScenarioExecutor] 生成/上传报告失败: %s", e)

                return ExecutionResult(
                    success=success,
                    status=run_status,
                    duration_ms=duration_ms,
                    error_message=scenario_run.error_message if not success else None,
                    report_path=report_path,
                    result_summary=result_summary,
                    detail_run_id=str(scenario_run.id),
                )

            except asyncio.CancelledError:
                duration_ms = int(
                    (datetime.now(timezone.utc) - start_time).total_seconds() * 1000
                )
                return ExecutionResult(
                    success=False,
                    status=JobStatus.CANCELLED.value,
                    duration_ms=duration_ms,
                    error_message="执行被取消",
                )
            except Exception as e:
                duration_ms = int(
                    (datetime.now(timezone.utc) - start_time).total_seconds() * 1000
                )
                return ExecutionResult(
                    success=False,
                    status=JobStatus.FAILED.value,
                    duration_ms=duration_ms,
                    error_message=str(e),
                )

    async def cancel(self) -> None:
        self._cancelled = True


class WebTestExecutor(ScriptExecutor):
    """Web 测试执行器（委托给现有 WebTestService）"""

    def __init__(self):
        self._cancelled = False

    @staticmethod
    def _with_runtime_credentials(
        config: Dict[str, Any],
        test_config: Optional[Dict[str, Any]],
    ) -> Dict[str, Any]:
        """Inject a configured login profile without storing secrets in a job."""
        from app.services.web_preflight import resolve_runtime_credentials

        resolved = dict(config or {})
        # 与预检共用同一套解析逻辑（含 login_profile 派生键），避免两处判据漂移
        username, password = resolve_runtime_credentials(resolved, test_config)

        if username and password:
            env = dict(resolved.get("env") or resolved.get("environment_variables") or {})
            env.setdefault("WEB_TEST_USERNAME", username)
            env.setdefault("WEB_TEST_PASSWORD", password)
            resolved["env"] = env

        return resolved

    async def execute(self, script_id: UUID, config: Dict[str, Any]) -> ExecutionResult:
        from app.repositories.project_repo import ProjectRepository
        from app.repositories.web_test_repo import WebTestRepository
        from app.services.web_test_service import WebTestService

        start_time = datetime.now(timezone.utc)

        async with async_session_factory() as session:
            web_test_repo = WebTestRepository(session)
            project_repo = ProjectRepository(session)

            web_test = await web_test_repo.get_by_id(script_id)
            if not web_test:
                return ExecutionResult(
                    success=False,
                    status=JobStatus.FAILED.value,
                    error_message=f"Web 测试不存在: {script_id}",
                )

            # 检查 script_path 是否为空
            if not web_test.script_path:
                return ExecutionResult(
                    success=False,
                    status=JobStatus.FAILED.value,
                    error_message=f"Web 测试 {web_test.identifier} 的脚本路径为空，无法执行。请先为该测试生成或上传脚本。",
                )

            project = await project_repo.get_by_id(web_test.project_id)
            if not project:
                return ExecutionResult(
                    success=False,
                    status=JobStatus.FAILED.value,
                    error_message=f"项目不存在: {web_test.project_id}",
                )
# fmt: off  My80OmFIVnBZMlhscm9ua3VMazZjbVZPWWc9PToyMjQ0NGNkNA==

            config = self._with_runtime_credentials(config, web_test.test_config)
            service = WebTestService(session)
            try:
                # 有界重试：只针对执行环境类（flake）失败，避免把偶发环境问题当产品缺陷。
                # 每次尝试都不在服务内部建单，等确定"不再重试"后再统一登记，
                # 这样既不会为同一次 flake 重复建单，也不会漏登记。
                outcome = await run_attempts_with_policy(
                    lambda: service.run_web_test(
                        project_identifier=project.identifier,
                        web_test_id=str(script_id),
                        execution_config=config,
                        register_defect=False,
                        parent_test_run_id=config.get("job_test_run_id"),
                    ),
                    max_attempts=1 + flake_retry_budget(config),
                    should_stop=lambda: self._cancelled,
                )
                result = outcome["result"]
                attempts = outcome["attempts"]

                if self._cancelled:
                    return ExecutionResult(
                        success=False,
                        status=JobStatus.CANCELLED.value,
                        error_message="执行已被取消",
                    )

                # 结果摘要先算出来（失败分类需要 stdout 里的证据）
                mapped = summarize_web_run_result(result)
                blocked, blocked_reason = classify_environment_block(
                    str(result.get("error_message") or result.get("error") or ""),
                    mapped.get("stdout"),
                )
                if result.get("status") == "failed" and not blocked:
                    await service.register_failure_defect(
                        project_identifier=project.identifier,
                        web_test=web_test,
                        test_run_id=result.get("run_id") or None,
                        error_msg=str(
                            result.get("error_message") or result.get("error") or "未知错误"
                        ),
                        report_attachment_id=result.get("report_path"),
                        parent_test_run_id=config.get("job_test_run_id"),
                    )
                elif result.get("status") == "failed" and blocked:
                    # 环境阻断（WAF/网关/IP 白名单）：**不建缺陷单**，只如实标记。
                    # 这既避免持续产出垃圾单，也让报告能说清"不是产品失败"。
                    print(
                        "[WebExecutor] 环境阻断，已跳过缺陷登记: %s" % blocked_reason
                    )
                elif result.get("status") == "completed" and attempts > 1:
                    # 重试成功：不留痕就等于伪装成一次通过
                    print("[WebExecutor] 执行环境类失败后重试成功（已留 FLAKE_RETRY 痕迹）")

                run_id = result.get("run_id")
                # WebTestService 已内部创建 WebTestRun 并执行
                # 这里直接按返回结果判断
                if blocked:
                    mapped["result_summary"]["environment_blocked"] = True
                    mapped["result_summary"]["environment_blocked_reason"] = blocked_reason
                    marker = (
                        f"{ENVIRONMENT_BLOCK_MARKER} "
                        f"{json.dumps({'reason': blocked_reason}, ensure_ascii=False)}"
                    )
                    mapped["stdout"] = marker + "\n" + (mapped["stdout"] or "")
                if mapped.get("preflight"):
                    mapped["result_summary"]["preflight"] = mapped["preflight"]
                if attempts > 1:
                    note = flake_retry_note(attempts, outcome["first_error"])
                    mapped["stdout"] = note + "\n" + (mapped["stdout"] or "")
                    mapped["result_summary"]["attempts"] = attempts
                    mapped["result_summary"]["retried"] = True
                duration_ms = int(
                    (datetime.now(timezone.utc) - start_time).total_seconds() * 1000
                )
                report_path = result.get("report_path")
                return ExecutionResult(
                    success=mapped["success"],
                    status=mapped["status"],
                    duration_ms=duration_ms,
                    error_message=mapped["error_message"],
                    stdout=mapped["stdout"],
                    stderr=mapped["stderr"],
                    report_path=report_path,
                    result_summary=mapped["result_summary"],
                    detail_run_id=str(run_id) if run_id else None,
                )

            except asyncio.CancelledError:
                return ExecutionResult(
                    success=False,
                    status=JobStatus.CANCELLED.value,
                    error_message="执行被取消",
                )
            except Exception as e:
                duration_ms = int(
                    (datetime.now(timezone.utc) - start_time).total_seconds() * 1000
                )
                return ExecutionResult(
                    success=False,
                    status=JobStatus.FAILED.value,
                    duration_ms=duration_ms,
                    error_message=str(e),
                )

    async def cancel(self) -> None:
        self._cancelled = True


class ExecutorRegistry:
    """执行器注册表：按 ScriptType 分发给具体执行器"""

    _executors: Dict[str, Any] = {}

    @classmethod
    def get(cls, script_type: str, mongodb: Any = None) -> ScriptExecutor:
        """获取对应类型的执行器实例"""
        if script_type == "api_test":
            return PlaywrightExecutor(mongodb=mongodb)
        elif script_type == "scenario":
            return ScenarioExecutor()
        elif script_type == "web_test":
            return WebTestExecutor()
        else:
            raise ValueError(f"不支持的脚本类型: {script_type}")
