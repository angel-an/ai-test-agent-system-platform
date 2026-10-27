"""
API 测试执行器

负责异步执行 API 测试并收集结果
"""

import asyncio
import json
import logging
import os
import subprocess
import tempfile
import shutil
from pathlib import Path
from datetime import datetime, timezone
from typing import Optional, Any, Dict, List
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config.database import async_session_factory
from app.models.api_test import APITest, APITestRun, APITestResult
from app.models.project import Project
from app.repositories.api_test_repo import (
    APITestRepository,
    APITestRunRepository,
    APITestResultRepository,
)
from app.config.minio_client import MinIOClient
from app.schemas.enums import TestResultStatus
from app.models.mongodb.api_test_log import APITestDetailLog
from app.services.defect_registration_service import DefectRegistrationService
from app.services.trace_evidence import extract_from_result as extract_trace_exchange
from app.services.web_failure_policy import classify_environment_block

logger = logging.getLogger(__name__)

class APITestExecutor:
    """
    API 测试执行器

    负责执行 Playwright API 测试并收集结果
    """

    def __init__(self, session: AsyncSession, mongodb=None):
        self.session = session
        self.mongodb = mongodb
        self.api_test_repo = APITestRepository(session)
        self.api_test_run_repo = APITestRunRepository(session)
        self.api_test_result_repo = APITestResultRepository(session)
        # 持有后台任务强引用：asyncio 事件循环只保留弱引用，
        # 不持引用的任务可能在执行中被 GC 回收（Python 官方文档明确要求保存引用）
        self._background_tasks: set = set()

    async def execute_test(
        self,
        api_test_id: UUID,
        execution_config: Optional[Dict[str, Any]] = None,
    ) -> str:
        """
        执行 API 测试（异步）

        Args:
            api_test_id: API 测试 ID
            execution_config: 执行配置

        Returns:
            str: 测试运行 ID
        """
        # 1. 获取 API 测试
        api_test = await self.api_test_repo.get_by_id(api_test_id)
        if not api_test:
            raise ValueError(f"API 测试不存在: {api_test_id}")
# noqa  MC80OmFIVnBZMlhscm9ua3VMazZja1JKUWc9PTo1ZDNlZmUwMw==

        # 2. 创建测试运行记录
        identifier = await self.api_test_run_repo.get_next_identifier(api_test_id)
        test_run = await self.api_test_run_repo.create(
            project_id=api_test.project_id,
            api_test_id=api_test_id,
            identifier=identifier,
            status="pending",
            execution_config=execution_config or {},
            total_tests=0,
            passed_tests=0,
            failed_tests=0,
            skipped_tests=0,
        )

        run_id = test_run.id

        # 立即提交：后台任务使用独立会话，若该行未提交，后台任务读不到 run 记录
        await self.session.commit()

        # 3. 在后台执行测试（保存强引用，防止任务被 GC）
        task = asyncio.create_task(
            self._execute_in_background(
                run_id=run_id,
                api_test=api_test,
                execution_config=execution_config or {},
            )
        )
        self._background_tasks.add(task)
        task.add_done_callback(self._background_tasks.discard)

        return str(run_id)

    async def _execute_in_background(
        self,
        run_id: UUID,
        api_test: APITest,
        execution_config: Dict[str, Any],
    ):
        """
        Execute test in background.

        Workflow:
        1. Update status to RUNNING
        2. Download test script from MinIO
        3. Prepare execution environment
        4. Run Playwright test
        5. Parse test results
        6. Save results to database
        7. Update run status
        """
        # 后台任务必须使用独立新会话：execute_test 返回后请求作用域的 session 已结束，
        # 复用它会在首次 SQL 时抛 InvalidRequestError(session in 'prepared' state)，
        # 且 except 分支同样失败 → run 永停 pending、error_message 为 null（2026-10-23 实测堆栈）。
        async with async_session_factory() as bg_session:
            self.session = bg_session
            self.api_test_repo = APITestRepository(bg_session)
            self.api_test_run_repo = APITestRunRepository(bg_session)
            self.api_test_result_repo = APITestResultRepository(bg_session)
            # api_test 是请求 session 里的 ORM 对象，挂载到后台会话（load=False 不发 SQL）
            api_test = await bg_session.merge(api_test, load=False)
            try:
                await self._execute_in_background_locked(run_id, api_test, execution_config)
                # 后台会话无请求级依赖注入代为提交，必须显式 commit，
                # 否则 close 时整个事务 ROLLBACK，run 状态永远停在 pending（2026-10-23 实测）
                await bg_session.commit()
            except Exception as outer_err:
                await bg_session.rollback()
                logger.error("API 测试后台执行失败 run_id=%s: %s", run_id, outer_err, exc_info=True)
                try:
                    row = await self.api_test_run_repo.get_by_id(run_id)
                    if row is not None:
                        await self.api_test_run_repo.update(
                            row, status="failed", error_message=str(outer_err)
                        )
                        await bg_session.commit()
                except Exception as write_err:
                    logger.error("回写 run 失败状态时出错 run_id=%s: %s", run_id, write_err)

    async def _execute_in_background_locked(
        self,
        run_id: UUID,
        api_test: APITest,
        execution_config: Dict[str, Any],
    ):
        """在后台会话内执行测试主体（由 _execute_in_background 调用）。"""
        try:
            # 1. 更新状态为 RUNNING
            await self.api_test_run_repo.update(
                await self.api_test_run_repo.get_by_id(run_id),
                status="running"
            )

            # 2. 下载测试脚本
            script_content = MinIOClient.download_file(api_test.script_path)
            script_content = script_content.decode("utf-8")

            # 3. 准备执行环境
            with tempfile.TemporaryDirectory() as temp_dir:
                temp_path = Path(temp_dir)
# noqa  MS80OmFIVnBZMlhscm9ua3VMazZja1JKUWc9PTo1ZDNlZmUwMw==

                # 写入测试脚本
                script_file = temp_path / "api-test.spec.ts"
                script_file.write_text(script_content, encoding="utf-8")

                # 创建配置文件（如果需要）
                playwright_config = self._generate_playwright_config(execution_config)
                config_file = temp_path / "playwright.config.ts"
                config_file.write_text(playwright_config, encoding="utf-8")

                # 4. 执行测试（传 Path：子方法内做 work_dir / "xxx" 拼接，str 会 TypeError）
                result = await self._run_playwright_test(
                    temp_path, execution_config
                )

                # 5. 生成 Allure 报告（先生成，后续处理 IDP 时传入 report_url）
                report_path = await self._generate_allure_report(
                    run_id=run_id,
                    work_dir=temp_path,
                )

                # 6. 解析结果并保存（此时 report_path 已生成，可传入缺陷描述）
                report_url = f"/api/v2/test-runs/{run_id}/report" if report_path else None
                await self._process_test_results(
                    run_id=run_id,
                    api_test=api_test,
                    test_result=result,
                    report_url=report_url,
                )

                # 7. 回写 IDP 缺陷登记结果到报告
                if report_path:
                    await self._append_defect_report_to_allure(
                        run_id=run_id,
                        report_path=report_path,
                    )

                # 8. 更新为完成状态
                await self.api_test_run_repo.update(
                    await self.api_test_run_repo.get_by_id(run_id),
                    status="completed",
                    report_path=report_path,
                )

                # 排查开关：执行目录在 with 结束后就被删除，想复看 trace.zip / results.json
                # 只能靠它整份拷出来（`API_TEST_KEEP_WORKDIR=/some/dir`）。默认关闭，
                # 不影响正常执行与磁盘占用。
                keep_dir = os.environ.get("API_TEST_KEEP_WORKDIR")
                if keep_dir:
                    try:
                        target = Path(keep_dir) / str(run_id)
                        shutil.copytree(temp_path, target, dirs_exist_ok=True)
                        logger.info("[APITest] 执行目录已保留: %s", target)
                    except Exception as copy_err:  # noqa: BLE001
                        logger.warning("[APITest] 保留执行目录失败: %s", copy_err)

        except Exception as e:
            # 更新为失败状态（回写自身也要兜底，绝不让异常逃出后台任务）
            try:
                await self.api_test_run_repo.update(
                    await self.api_test_run_repo.get_by_id(run_id),
                    status="failed",
                    error_message=str(e)
                )
            except Exception as update_err:
                logger.error(
                    "回写 run 失败状态时出错 run_id=%s: %s（原始错误: %s）",
                    run_id, update_err, e,
                )
            logger.error("API 测试后台执行失败 run_id=%s: %s", run_id, e, exc_info=True)

    def _generate_playwright_config(self, execution_config: Dict[str, Any]) -> str:
        """生成 Playwright 配置文件。

        reporter 写在配置里而非命令行：``--reporter=[["json",{...}]]`` 这种数组形式
        在 Windows 下会被 CLI 拆错（实测报 ``Cannot find module '[["json"'``），
        配置文件的 ``reporter`` 字段是官方支持写法。

        Allure 是**第三方** reporter，未安装时 Playwright 解析阶段直接抛 MODULE_NOT_FOUND、
        一个测试都不跑，因此用 ``PW_ALLURE_REPORTER`` 开关控制：调用方先带它跑，
        仅在"因 reporter 缺失而失败"时关掉重跑（json 是内置的，始终保留）。

        ``trace: 'retain-on-failure'`` 是**请求/响应证据的唯一来源**：JSON 报告里没有
        request/response 结构（2026-10-27 实测），trace 里的 ``*-trace.network``
        才是 HAR 风格的完整往返记录。只在失败时落盘，通过的用例零额外开销。
        原先的 ``slowMo: 3000`` 已移除 —— API 用例不启浏览器，该参数从不生效，
        留着只会让人误以为执行被拖慢。
        """
        return f"""
import {{ defineConfig, devices }} from '@playwright/test';

const reporters: any = [['json', {{ outputFile: 'results.json' }}]];
if (process.env.PW_ALLURE_REPORTER === '1') {{
  reporters.push(['@playwright/test/allure-playwright']);
}}

export default defineConfig({{
  testDir: './',
  reporter: reporters,
  fullyParallel: true,
  forbidOnly: false,
  retries: process.env.CI ? 2 : 0,
  use: {{
    trace: 'retain-on-failure',
  }},
  projects: [
    {{
      name: 'api-tests',
      use: {{
        baseURL: '{execution_config.get('base_url', 'http://localhost:8000')}',
      }},
    }},
  ],
}});
"""

    #: Allure 是**第三方** reporter（json 是内置的）；未安装时 Playwright 解析阶段即退出。
    _ALLURE_REPORTER = "@playwright/test/allure-playwright"
    #: 控制配置文件里是否挂 allure reporter（值写进子进程环境，不落盘到脚本）。
    _ALLURE_ENV = "PW_ALLURE_REPORTER"

    def _run_playwright_process(
        self,
        npx_exe: str,
        work_dir: Path,
        execution_config: Dict[str, Any],
        *,
        with_allure: bool = True,
    ):
        """执行一次 ``npx playwright test``（allure 开关走环境变量，便于缺包时降级重跑）。"""
        env = {**os.environ, self._ALLURE_ENV: "1" if with_allure else "0"}
        # base_url → API_BASE_URL：平台生成的脚本读 `process.env.API_BASE_URL`，
        # 不映射就会回退到脚本里的 http://localhost:8000 占位 —— 所有断言打的都是本机，
        # 「通过/失败」与目标环境无关（新执行器 api_endpoint_script_executor 已做同样映射）。
        base_url = execution_config.get("base_url")
        if base_url:
            env["API_BASE_URL"] = str(base_url).rstrip("/")
        # 自定义环境变量（前端执行配置/调用方传入）优先级更高，显式覆盖上面的默认值。
        custom_env = execution_config.get("env") or {}
        if isinstance(custom_env, dict):
            for key, value in custom_env.items():
                if key and value is not None:
                    env[str(key)] = str(value)
        return subprocess.run(
            [
                npx_exe, "playwright", "test",
                f"--output={work_dir}/allure-results",  # Allure 结果目录
            ],
            cwd=work_dir,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=execution_config.get("timeout", 300),
            env=env,
        )

    @staticmethod
    def _allure_reporter_missing(result: Any) -> bool:
        """判断失败是否**仅因** allure reporter 不可用 —— 只有这种失败才值得降级重跑。

        实测到的三种形态（不同 Node/Playwright 版本措辞不同）：
        ``Cannot find module '@playwright/test/allure-playwright'``、
        ``Package subpath './allure-playwright' is not defined by "exports"``、
        ``resolveReporters`` 栈。共同点是都发生在**解析 reporter** 阶段、且点名 allure。
        其它失败（用例真红、超时）重跑一次既费时又会掩盖现场，一律不重试。
        """
        if getattr(result, "returncode", 0) == 0:
            return False
        text = f"{getattr(result, 'stdout', '') or ''}\n{getattr(result, 'stderr', '') or ''}"
        low = text.lower()
        if "allure-playwright" not in low:
            return False
        markers = (
            "cannot find module",
            "module_not_found",
            "is not defined by",
            "err_package_path_not_exported",
            "resolvereporters",
        )
        return any(marker in low for marker in markers)

    async def _run_playwright_test(
        self,
        work_dir: Path,
        execution_config: Dict[str, Any],
    ) -> Dict[str, Any]:
        """
        运行 Playwright 测试并生成 Allure 报告

        Args:
            work_dir: 工作目录
            execution_config: 执行配置

        Returns:
            dict: 测试结果
        """
        try:
            # 解析 npx 绝对路径：Windows 下 CreateProcess 不解析 .cmd，
            # 裸 "npx" 会 FileNotFoundError（2026-10-23 实测）
            npx_exe = shutil.which("npx") or shutil.which("npx.cmd")
            if not npx_exe:
                return {
                    "status": "failed",
                    "error": "npx 不可用：PATH 中未找到 Node.js/npx，请安装 Node.js 或配置 PATH",
                }

            # 检查 npx 是否可用
            npx_check = subprocess.run(
                [npx_exe, "--version"],
                capture_output=True,
                text=True,
                encoding='utf-8',
                errors='replace',
                timeout=10
            )

            if npx_check.returncode != 0:
                return {
                    "status": "failed",
                    "error": "npx 不可用，请确保 Node.js 已安装",
                    "stdout": npx_check.stdout,
                }

            # 运行 Playwright 测试，同时产出 JSON（机读结果）+ Allure（人读报告）。
            # 历史坑（实测 101 个 run 全零）：只配 allure 时 results.json 永不生成，
            # suites=[] → total_tests=0、无 per-test 结果、失败→缺陷登记链路空转；
            # 更早一步的坑：allure-playwright 这个**第三方 reporter 没装**时，
            # Playwright 在解析 reporter 阶段就抛 MODULE_NOT_FOUND，一个测试都没跑。
            # 因此 json（内置，必带）与 allure（可选，失败降级）分开处理：
            # 先带 allure 跑一次，若因 reporter 不可用而失败，则降级为仅 json 重跑。
            # subprocess.run 是**阻塞**调用：直接在事件循环里跑会把整个服务冻住。
            # 实测后果（2026-10-27）：被测接口就在本机时，用例请求打进来没人处理 →
            # 用例必然失败，而同一脚本在服务外用同样参数单独跑是 passed。
            # 放到线程里执行，事件循环继续服务其它请求。
            result = await asyncio.to_thread(
                self._run_playwright_process, npx_exe, work_dir, execution_config
            )
            allure_missing = self._allure_reporter_missing(result)
            if allure_missing:
                logger.warning(
                    "allure-playwright 报告器不可用（未安装），降级为仅 JSON 报告重跑：%s",
                    (result.stderr or result.stdout or "")[:300],
                )
                result = await asyncio.to_thread(
                    self._run_playwright_process,
                    npx_exe,
                    work_dir,
                    execution_config,
                    with_allure=False,
                )

            # 读取 JSON 格式的测试结果（如果同时配置了 json 报告器）
            json_results_file = work_dir / "results.json"
            if json_results_file.exists():
                with open(json_results_file, "r", encoding="utf-8") as f:
                    report = json.load(f)
                # 进程级 stdout 是**环境证据的唯一载体**：`acw_tc` / `cdn_sec_tc` /
                # `ESA RuleID` / `X-Swift-Error` 这些 WAF/网关签名只出现在这里，
                # 不在 Playwright 报告的错误文本里。缺陷登记前的环境阻断闸必须拿到它，
                # 否则该闸会把最典型的 WAF 形态全部漏掉（见 _process_idp_defect 第 3.5 步）。
                # setdefault：Playwright 报告里 per-test 也有同名 `stdout` 键，**绝不覆盖**。
                if isinstance(report, dict):
                    report.setdefault("stdout", result.stdout)
                return report
            else:
                # 如果没有 JSON 结果，返回执行状态
                return {
                    "status": "passed" if result.returncode == 0 else "failed",
                    "error": result.stderr,
                    "stdout": result.stdout,
                }

        except subprocess.TimeoutExpired as e:
            logger.error(f"测试执行超时: {str(e)}")
            return {
                "status": "failed",
                "error": f"测试执行超时: {str(e)}",
            }
        except FileNotFoundError as e:
            logger.error(f"找不到命令或文件: {str(e)}")
            return {
                "status": "failed",
                "error": f"找不到命令或文件: {str(e)}",
            }
        except PermissionError as e:
            logger.error(f"权限不足: {str(e)}")
            return {
                "status": "failed",
                "error": f"权限不足: {str(e)}",
            }
        except Exception as e:
            logger.error(f"测试执行失败: {str(e)}", exc_info=True)
            return {
                "status": "failed",
                "error": f"测试执行失败: {str(e)}",
            }

    @staticmethod
    def _map_test_status(test: dict[str, Any]) -> TestResultStatus:
        """把 Playwright JSON 报告的 test.status 映射为平台状态。

        ⚠️ Playwright JSON 的 ``status`` **不是** passed/failed，而是
        ``expected``（通过）/ ``unexpected``（失败）/ ``flaky``（重试后通过）/ ``skipped``
        —— 实测确认（2026-10-27，npx playwright 1.x json reporter）。
        口径：expected/flaky→PASSED、skipped→SKIPPED、其余→FAILED；
        ``status`` 缺失时回退布尔 ``ok``（兼容旧格式报告）。
        """
        raw = str(test.get("status") or "").strip().lower().replace(" ", "")
        if raw in {"expected", "flaky", "passed"}:
            return TestResultStatus.PASSED
        if raw == "skipped":
            return TestResultStatus.SKIPPED
        if raw in {"unexpected", "failed", "timedout", "interrupted"}:
            return TestResultStatus.FAILED
        return TestResultStatus.PASSED if test.get("ok") else TestResultStatus.FAILED

    async def _process_test_results(
        self,
        run_id: UUID,
        api_test: APITest,
        test_result: Dict[str, Any],
        report_url: Optional[str] = None,
    ):
        """
        处理测试结果

        Args:
            run_id: 测试运行 ID
            api_test: API 测试
            test_result: Playwright 测试结果
            report_url: 测试报告 URL（报告生成后传入）
        """
        try:
            # 解析 Playwright 结果
            suites = test_result.get("suites", [])
            total_tests = 0
            passed_tests = 0
            failed_tests = 0
            skipped_tests = 0

            for suite in suites:
                specs = suite.get("specs", [])
                for spec in specs:
                    tests = spec.get("tests", [])
                    for test in tests:
                        total_tests += 1
                        # Playwright JSON 里**标题在 spec 上、tests[].title 恒为 null**
                        # （2026-10-27 实测）——取 test.title 会让每条结果的名字都是空串。
                        test_name = spec.get("title") or test.get("title") or ""

                        status = self._map_test_status(test)
                        if status == TestResultStatus.PASSED:
                            passed_tests += 1
                        elif status == TestResultStatus.SKIPPED:
                            skipped_tests += 1
                        else:
                            failed_tests += 1

                        # 请求/响应证据**只提取一次**，保存结果与缺陷登记共用：
                        # trace 解析要解 zip，同一用例提两次纯属浪费，日志也会刷两遍。
                        # 通过用例不开 trace（retain-on-failure），无需提取。
                        result_details = test.get("results", [])
                        evidence = None
                        if status != TestResultStatus.PASSED:
                            evidence = self._extract_http_evidence(
                                result_details, api_test, test_name
                            )

                        # 保存测试结果：**单条失败不拖垮整段统计**。
                        # 历史坑：这里抛一次异常就被外层 except 吞掉，counts 永不更新，
                        # run 显示 completed + total_tests=0，看着像"平台没跑"，实际是落库报错。
                        try:
                            await self._save_test_result(
                                run_id=run_id,
                                api_test=api_test,
                                test_name=test_name,
                                status=status,
                                results=result_details,
                                evidence=evidence,
                            )
                        except Exception as save_err:  # noqa: BLE001
                            logger.error(
                                "保存单条测试结果失败（不影响计数）run_id=%s name=%s: %s",
                                run_id, test_name, save_err,
                            )

                        # IDP 缺陷处理：仅对失败的测试
                        if status == TestResultStatus.FAILED:
                            await self._process_idp_defect(
                                run_id=run_id,
                                api_test=api_test,
                                test_name=test_name,
                                results=result_details,
                                report_url=report_url,
                                # 进程级 stdout 透传给环境阻断闸（`_run_playwright_test`
                                # 正常路径用 setdefault 挂在报告 dict 上）
                                runner_stdout=test_result.get("stdout"),
                                evidence=evidence,
                            )

            # 更新运行统计
            await self.api_test_run_repo.update(
                await self.api_test_run_repo.get_by_id(run_id),
                total_tests=total_tests,
                passed_tests=passed_tests,
                failed_tests=failed_tests,
                skipped_tests=skipped_tests,
            )

        except Exception as e:
            logger.error("处理测试结果失败: %s", e)

    async def _save_test_result(
        self,
        run_id: UUID,
        api_test: APITest,
        test_name: str,
        status: TestResultStatus,
        results: List[Dict[str, Any]],
        evidence: Optional[Dict[str, Any]] = None,
    ):
        """
        保存单个测试结果

        Args:
            run_id: 测试运行 ID
            api_test: API 测试
            test_name: 测试名称
            status: 测试状态
            results: 测试结果详情
            evidence: 已提取的请求/响应证据（调用方批量提取时传入，避免重复解 trace）
        """
        try:
            # 端点和 HTTP 方法优先取**真实往返**（trace），取不到再退回标题解析
            fallback_endpoint, fallback_method = self._parse_endpoint_from_test_name(test_name)
            if evidence is None:
                if status == TestResultStatus.PASSED:
                    # 通过的用例不开 trace（retain-on-failure），无需提取；
                    # 更不该像历史实现那样把状态码编成 200。
                    evidence = {
                        "request_summary": {
                            "url": (getattr(api_test, "test_config", None) or {}).get("base_url", ""),
                            "method": fallback_method,
                        },
                        "response_summary": {},
                        "endpoint": fallback_endpoint,
                        "method": fallback_method,
                    }
                else:
                    evidence = self._extract_http_evidence(results, api_test, test_name)
            endpoint = evidence["endpoint"] or fallback_endpoint
            method = evidence["method"] or fallback_method

            # 历史实现这里写死 `status_code = 200 if passed else 500` —— 那是**编出来的**
            # 数据：会把"通过"的用例记成 200、"失败"的记成 500，看着像有响应证据，
            # 实际既没请求过也没收到过。宁可 None（未知），也不能造假。
            last_result = results[-1] if results and isinstance(results[-1], dict) else {}
            raw_error = (last_result.get("error") or {}).get("message") or ""
            duration = last_result.get("duration")
            if not isinstance(duration, (int, float)):
                duration = 0

            # 创建测试结果记录
            await self.api_test_result_repo.create(
                test_run_id=run_id,
                api_test_id=api_test.id,
                scenario_name=test_name,
                endpoint=endpoint,
                method=method,
                status=status,
                request_summary=evidence["request_summary"],
                response_summary=evidence["response_summary"],
                error_message=(
                    None if status == TestResultStatus.PASSED
                    else (raw_error[:2000] if raw_error else "测试失败")
                ),
                duration_ms=int(duration),
                retry_count=last_result.get("retry") or 0,
            )

        except Exception as e:
            logger.error("保存测试结果失败: %s", e)
# pragma: no cover  Mi80OmFIVnBZMlhscm9ua3VMazZja1JKUWc9PTo1ZDNlZmUwMw==

    def _parse_endpoint_from_test_name(self, test_name: str) -> tuple[str, str]:
        """
        Parse endpoint and HTTP method from test name.

        Input:  "GET /api/v1/users"
        Output: ("/api/v1/users", "GET")
        """
        import re

        # Try to match pattern: "METHOD /path" or "METHOD path"
        match = re.match(r'^(GET|POST|PUT|DELETE|PATCH|HEAD|OPTIONS)\s+(.+)$', test_name)
        if match:
            method = match.group(1)
            endpoint = match.group(2)
            return endpoint, method

        # Default to GET if no explicit method found
        return test_name, "GET"

    async def _generate_allure_report(
        self,
        run_id: UUID,
        work_dir: Path,
    ) -> Optional[str]:
        """
        生成 Allure 测试报告

        Args:
            run_id: 测试运行 ID
            work_dir: 工作目录（包含 allure-results）

        Returns:
            str: 报告目录路径 (MinIO)
        """
        try:
            allure_results_dir = work_dir / "allure-results"

            # 检查 Allure 结果是否存在
            if not allure_results_dir.exists():
                print("未找到 Allure 测试结果")
                return None

            # 生成 HTML 报告到临时目录
            allure_report_dir = work_dir / "allure-report"
            subprocess.run(
                ["allure", "generate", str(allure_results_dir), "-o", str(allure_report_dir), "--clean"],
                capture_output=True,
                timeout=30
            )

            # 将报告打包为 ZIP 并上传到 MinIO
            import zipfile
            zip_path = work_dir / "allure-report.zip"
            with zipfile.ZipFile(zip_path, 'w', zipfile.ZIP_DEFLATED) as zipf:
                for file in allure_report_dir.rglob('*'):
                    if file.is_file():
                        arcname = file.relative_to(allure_report_dir)
                        zipf.write(file, arcname)

            # 上传到 MinIO
            report_path = f"api-test-reports/{run_id}/allure-report.zip"
            with open(zip_path, 'rb') as f:
                MinIOClient.upload_bytes(
                    object_name=report_path,
                    data=f.read(),
                    content_type="application/zip",
                )

            return report_path

        except Exception as e:
            print(f"生成 Allure 报告失败: {e}")
            return None

    async def _append_defect_report_to_allure(
        self,
        run_id: UUID,
        report_path: str,
    ) -> None:
        """
        将 IDP 缺陷登记结果回写到 Allure 报告

        在 Allure 报告生成后，查询本次测试运行的 IDP 缺陷记录，
        生成 HTML 摘要并追加到报告中。
        """
        try:
            from app.services.defect_report_service import DefectReportService

            defect_service = DefectReportService(self.session)
            summary = await defect_service.get_defect_summary_for_run(run_id)

            if summary["total"] == 0:
                return

            # 生成 HTML 章节
            html_section = defect_service.generate_html_section(summary)

            # 创建一个 Allure 兼容的附加文件（通过写入 allure-results 的 container）
            # 由于 Allure 报告已经生成完毕，我们直接修改 HTML 报告
            # 下载报告、修改、重新上传
            report_data = MinIOClient.download_file(report_path)
            if not report_data:
                logger.warning("[DefectReport] 无法下载报告进行回写")
                return

            import zipfile
            import io

            # 解压 ZIP
            with zipfile.ZipFile(io.BytesIO(report_data), 'r') as zin:
                # 找到 index.html
                html_content = None
                for name in zin.namelist():
                    if name.endswith('index.html'):
                        html_content = zin.read(name).decode('utf-8')
                        break

            if not html_content:
                logger.warning("[DefectReport] 报告中未找到 index.html")
                return

            # 在 </body> 前插入 IDP 缺陷章节
            if "</body>" in html_content:
                html_content = html_content.replace(
                    "</body>",
                    f"<div style='padding: 20px; margin: 20px; border: 1px solid #ddd;'>\n{html_section}\n</div>\n</body>"
                )
            else:
                html_content += f"\n<div style='padding: 20px; margin: 20px; border: 1px solid #ddd;'>\n{html_section}\n</div>\n"

            # 重新打包 ZIP
            new_zip = io.BytesIO()
            with zipfile.ZipFile(io.BytesIO(report_data), 'r') as zin:
                with zipfile.ZipFile(new_zip, 'w', zipfile.ZIP_DEFLATED) as zout:
                    for item in zin.namelist():
                        if item.endswith('index.html'):
                            zout.writestr(item, html_content.encode('utf-8'))
                        else:
                            zout.writestr(item, zin.read(item))

            # 重新上传
            new_zip.seek(0)
            MinIOClient.upload_bytes(
                object_name=report_path,
                data=new_zip.read(),
                content_type="application/zip",
            )

            logger.info(
                "[DefectReport] 已回写 IDP 缺陷结果到报告: %s (总计: %s, 已登记: %s)",
                report_path,
                summary["total"],
                summary["created"],
            )

        except Exception as e:
            # 报告回写失败不影响测试执行结果
            logger.error("[DefectReport] 回写报告失败（不影响测试结果）: %s", e)

    async def _resolve_source_project_key(self, api_test: APITest) -> Optional[str]:
        """取 api_test 所属项目的标识符（用于 IDP 项目映射）。

        **不要写成 ``api_test.project.identifier``**：后台执行会话里的 api_test 来自
        ``session.merge(api_test, load=False)``，``project`` 关系处于未加载状态
        （默认 lazy="select"），在 async 上下文里访问会抛
        ``MissingGreenlet: greenlet_spawn has not been called``。
        该异常随后被 ``_process_idp_defect`` 外层"不影响测试结果"的 except 吞掉，
        外部表现是**登记服务一次都没被调用、失败自动建单整条链路静默空转**
        （2026-10-27 实测：探针里 ``register_from_api_failure`` 调用次数 0）。

        因此这里只做两件事：关系已加载就直接读；否则按 project_id 只查 identifier 一列。
        """
        loaded = api_test.__dict__.get("project")
        identifier = getattr(loaded, "identifier", None) if loaded is not None else None
        if identifier:
            return identifier
        # 两个"拿不到"的合法情形：关系未加载且没有 project_id（替身/脏数据），
        # 或执行器没有会话（纯逻辑测试）。都交给上层按「项目未映射」处理，不在这里抛。
        project_id = getattr(api_test, "project_id", None)
        if not project_id or self.session is None:
            return None
        result = await self.session.execute(
            select(Project.identifier).where(Project.id == project_id)
        )
        return result.scalar_one_or_none()

    @staticmethod
    def _evidence_from_exchange(exchange, base_url: str) -> Dict[str, Any]:
        """把 trace 里的一次真实往返转成登记服务认识的 request/response 摘要。"""
        headers = exchange.response_headers or {}
        reqid = (
            headers.get("x-request-id")
            or headers.get("reqid")
            or headers.get("x-trace-id")
            or headers.get("trace-id")
            or ""
        )
        body = exchange.response_body
        if not reqid and isinstance(body, dict):
            reqid = str(body.get("reqid") or body.get("requestId") or "")

        # endpoint 用 URL 的 path，而不是"按 base_url 前缀截断"：
        # 执行时的 base_url 来自本次调用参数，与 test_config 里存的值经常不一致，
        # 前缀匹配失败会把整条绝对 URL 写进缺陷标题。
        endpoint = exchange.path
        request_summary = {
            "url": exchange.url,
            "base_url": base_url,
            "method": exchange.method,
            "headers": exchange.request_headers or {},
            "body": exchange.request_body if exchange.request_body is not None else {},
            "query": exchange.query,
            # 期望状态码 Playwright 不记录，断言文本里解析也不可靠 —— 宁可为空，
            # 也不要用 200 顶替（会写出"预期 200"这种凭空捏造的预期）。
            "expected_status_code": None,
            "evidence_source": "trace",
        }
        response_summary = {
            "status_code": exchange.status_code,
            "headers": headers,
            "body": body if body is not None else {},
            "reqid": reqid,
            "duration_ms": exchange.duration_ms,
        }
        return {
            "request_summary": request_summary,
            "response_summary": response_summary,
            "endpoint": endpoint,
            "method": exchange.method,
            # 「完整证据」= 既知道请求、也知道真实的响应状态码。
            # 只有请求没有响应（超时 / 连接被重置）说明请求可能没到被测系统，
            # 这类结论要人工看，不自动建单。
            "complete": bool(exchange.method and exchange.url and exchange.status_code is not None),
            "source": "trace",
        }

    def _extract_http_evidence(
        self,
        results: List[Dict[str, Any]],
        api_test: APITest,
        test_name: str,
    ) -> Dict[str, Any]:
        """提取本次失败的**真实** HTTP 请求/响应。

        来源优先级：
        1. Playwright 结果里的 ``request``/``response`` 结构 —— 官方 JSON 报告其实**没有**
           这个结构（2026-10-27 实测 ``results[]`` 只有 status/error/attachments…），
           保留只为兼容可能出现的其它报告格式；
        2. **trace 附件**（``use.trace='retain-on-failure'``）里的 HAR 网络快照 ——
           实测唯一能拿到真实请求头/请求体/状态码/响应体的途径；
        3. 降级：从用例标题解析接口，**状态码留空**（不知道就是不知道）。

        降级分支历史实现拿 ``last_result["status"]`` 当状态码，但那是 Playwright 的
        **用例状态字符串**（"failed"/"timedOut"），一路传到决策层做 ``>= 500`` 比较会抛
        TypeError（2026-10-27 实测）。
        """
        base_url = (getattr(api_test, "test_config", None) or {}).get("base_url", "")

        if results:
            last_result = results[-1] if isinstance(results[-1], dict) else {}
            # 1. 报告内联结构（官方报告没有，兼容保留）
            pw_request = last_result.get("request") or {}
            pw_response = last_result.get("response") or {}
            if isinstance(pw_request, dict) and pw_request.get("url") and pw_request.get("method"):
                # 直接用内联结构拼装（不经过 trace）
                request_headers = pw_request.get("headers") or {}
                response_headers = pw_response.get("headers") or {}
                status = pw_response.get("status")
                return {
                    "request_summary": {
                        "url": pw_request.get("url"),
                        "base_url": base_url,
                        "method": pw_request.get("method"),
                        "headers": request_headers,
                        "body": pw_request.get("postData") or {},
                        "expected_status_code": None,
                        "evidence_source": "report",
                    },
                    "response_summary": {
                        "status_code": status if isinstance(status, int) else None,
                        "headers": response_headers,
                        "body": pw_response.get("body") or {},
                        "reqid": (response_headers or {}).get("x-request-id", ""),
                    },
                    "endpoint": pw_request.get("url"),
                    "method": pw_request.get("method"),
                    "complete": isinstance(status, int) and status > 0,
                    "source": "report",
                }

            # 2. trace 附件
            exchange = extract_trace_exchange(last_result)
            if exchange is not None:
                return self._evidence_from_exchange(exchange, base_url)

        # 3. 降级
        endpoint, method = self._parse_endpoint_from_test_name(test_name)
        logger.warning(
            "[TraceEvidence] 用例 '%s' 未取得真实请求/响应（无 trace 附件），"
            "登记将保持 pending 等待人工确认",
            test_name,
        )
        return {
            "request_summary": {
                "url": base_url,
                "method": method,
                "headers": {},
                "body": {},
            },
            "response_summary": {
                "status_code": None,
                "headers": {},
                "body": {},
            },
            "endpoint": endpoint,
            "method": method,
            "complete": False,
            "source": "fallback",
        }

    async def _process_idp_defect(
        self,
        run_id: UUID,
        api_test: APITest,
        test_name: str,
        results: List[Dict[str, Any]],
        report_url: Optional[str] = None,
        runner_stdout: Optional[str] = None,
        evidence: Optional[Dict[str, Any]] = None,
    ):
        """
        处理 IDP 缺陷创建

        在测试失败时判断是否创建 IDP 缺陷
        从 Playwright 结果中提取真实的请求/响应信息
        IDP 异常不影响测试结果保存
        """
        try:
            # 1. 从 Playwright 结果中提取真实的请求/响应信息
            error_message = None
            if results:
                last_result = results[-1]
                error_message = last_result.get("error", {}).get("message", "")

            if evidence is None:
                evidence = self._extract_http_evidence(results, api_test, test_name)
            request_summary = evidence["request_summary"]
            response_summary = evidence["response_summary"]
            endpoint = evidence["endpoint"]
            method = evidence["method"]
            has_complete_evidence = evidence["complete"]

            # 2. 证据完整性 —— 只决定"能否自动创建"，**登记决策不在此处做**。
            #
            #    登记决策的唯一权威是 DefectDecisionService.decide()，由
            #    DefectRegistrationService.register_from_api_failure() 在执行链路内调用。
            #    本方法刻意**不再**内联调用 decide()：历史版本在这里做了一次"预检"，
            #    并以 `expected_status_code or 200` 传参 —— 那个 200 是**猜的不是断言给的**，
            #    会让描述写出"状态码不匹配：预期 200，实际 500"这类凭空捏造的预期
            #    （登记侧注释已明确拒绝该写法）。预检与登记两次判定共用同一
            #    response_status_code，故 skip 结论本就冗余；删掉预检后口径只剩一处。
            if not has_complete_evidence:
                logger.warning(
                    "[IDPDefect] 测试 '%s' 缺少完整的请求/响应证据，"
                    "将创建 pending 状态记录而非自动创建缺陷",
                    test_name,
                )

            # 3. 项目标识符（缺失时交由登记服务按「项目未映射」落 skipped 记录）
            source_project_key = await self._resolve_source_project_key(api_test)
            if not source_project_key:
                logger.warning(
                    "[IDPDefect] 未能解析 api_test 所属项目标识符，"
                    "登记将按「项目未映射」落 skipped 记录"
                )

            # 3.5 环境阻断闸（镜像 web 侧 executors.py:663-683 的"只标记不登记"语义）
            # WAF / 网关 / IP 白名单拦截在 API 链路同样表现为"测试失败"，但它是**环境问题**：
            # 既不该建缺陷单（否则持续产出垃圾单），也不该被当成 SUT 现象。
            # 输入同时喂 error_message 与**进程 stdout** —— 后者才是 `acw_tc` /
            # `ESA RuleID` / `X-Swift-Error` 这些实战签名的主要形态（2026-09-24 起 UAT 实测）。
            # **不设"证据优先"例外**：WAF 403 在 API 链路正是以"真实观测到的响应"出现，
            # 给它例外等于把刚堵的洞重新打开；web 侧闸门本就是无条件短路，此处对齐。
            # 本闸只管"是否登记"，登记与否的**判定权威**仍在 DefectDecisionService.decide()。
            blocked, blocked_reason = classify_environment_block(error_message, runner_stdout)
            if blocked:
                logger.warning(
                    "[IDPDefect] 环境阻断（%s），已跳过缺陷登记（不建单）: %s",
                    blocked_reason,
                    test_name,
                )
                return

            # 4. 调用统一缺陷登记服务 —— API 链路的**唯一**决策点
            registration_service = DefectRegistrationService(self.session)
            await registration_service.register_from_api_failure(
                test_run_id=run_id,
                test_case_id=api_test.test_case_id,
                source_project_key=source_project_key or "UNKNOWN",
                scenario_name=test_name,
                endpoint=endpoint or "unknown",
                method=method or "GET",
                request_summary=request_summary,
                response_summary=response_summary,
                error_message=error_message,
                report_url=report_url,
                allow_create=has_complete_evidence,
            )

        except Exception as e:
            # IDP 异常不影响测试结果保存，仅记录日志。
            # 必须带堆栈：历史上一句无堆栈的 error 把 MissingGreenlet 藏了很久，
            # 表现只是"单子没建出来"，从日志完全看不出原因（2026-10-27 实测）。
            logger.error(
                "[IDPDefect] 处理缺陷创建时发生错误（不影响测试结果）: %s", e, exc_info=True
            )

    async def save_detail_log(
        self,
        test_result_id: UUID,
        test_run_id: UUID,
        api_test_id: UUID,
        scenario_name: str,
        endpoint: str,
        method: str,
        request: Dict[str, Any],
        response: Dict[str, Any],
        status: str,
        duration_ms: int,
    ) -> str:
        """
        保存详细日志到 MongoDB

        Args:
            test_result_id: 测试结果 ID
            test_run_id: 测试运行 ID
            api_test_id: API 测试 ID
            scenario_name: 场景名称
            endpoint: 端点
            method: HTTP 方法
            request: 请求数据
            response: 响应数据
            status: 状态
            duration_ms: 执行时长

        Returns:
            str: MongoDB 日志 ID
        """
        if not self.mongodb:
            return None

        try:
            log = APITestDetailLog(
                log_id=str(uuid4()),
                test_result_id=test_result_id,
                test_run_id=test_run_id,
                api_test_id=api_test_id,
                scenario_name=scenario_name,
                endpoint=endpoint,
                method=method,
                request=request,
                response=response,
                assertions=[],  # TODO: 从测试结果中提取断言
                started_at=datetime.now(timezone.utc),
                completed_at=datetime.now(timezone.utc),
                duration_ms=duration_ms,
                status=status,
                error=None if status == "passed" else {"message": "测试失败"},
            )

            # 保存到 MongoDB
            collection = self.mongodb.db.get_collection("api_test_logs")
            result = await collection.insert_one(log.to_document())
# pylint: disable  My80OmFIVnBZMlhscm9ua3VMazZja1JKUWc9PTo1ZDNlZmUwMw==

            return str(result.inserted_id)

        except Exception as e:
            print(f"保存详细日志失败: {e}")
            return None

    async def generate_test_report(
        self,
        run_id: UUID,
    ) -> Optional[str]:
        """
        生成测试报告（已废弃，使用 _generate_allure_report 代替）

        Args:
            run_id: 测试运行 ID

        Returns:
            str: 报告文件路径 (MinIO)
        """
        # 此方法已集成到 _execute_in_background 中
        # 保留是为了向后兼容
        test_run = await self.api_test_run_repo.get_by_id(run_id)
        if test_run and test_run.report_path:
            return test_run.report_path
        return None
