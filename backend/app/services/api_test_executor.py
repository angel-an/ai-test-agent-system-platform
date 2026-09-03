"""
API 测试执行器

负责异步执行 API 测试并收集结果
"""

import asyncio
import json
import logging
import subprocess
import tempfile
import shutil
from pathlib import Path
from datetime import datetime, timezone
from typing import Optional, Any, Dict, List
from uuid import UUID, uuid4

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.api_test import APITest, APITestRun, APITestResult
from app.repositories.api_test_repo import (
    APITestRepository,
    APITestRunRepository,
    APITestResultRepository,
)
from app.config.minio_client import MinIOClient
from app.schemas.enums import TestResultStatus
from app.models.mongodb.api_test_log import APITestDetailLog
from app.services.defect_decision_service import DefectDecisionService
from app.services.defect_registration_service import DefectRegistrationService

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

        # 3. 在后台执行测试
        asyncio.create_task(
            self._execute_in_background(
                run_id=run_id,
                api_test=api_test,
                execution_config=execution_config or {},
            )
        )

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

                # 4. 执行测试
                result = await self._run_playwright_test(
                    temp_dir, execution_config
                )

                # 5. 生成 Allure 报告（先生成，后续处理 IDP 时传入 report_url）
                report_path = await self._generate_allure_report(
                    run_id=run_id,
                    work_dir=temp_dir,
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

        except Exception as e:
            # 更新为失败状态
            await self.api_test_run_repo.update(
                await self.api_test_run_repo.get_by_id(run_id),
                status="failed",
                error_message=str(e)
            )
            # 记录错误日志
            print(f"测试执行失败: {e}")

    def _generate_playwright_config(self, execution_config: Dict[str, Any]) -> str:
        """生成 Playwright 配置文件"""
        return f"""
import {{ defineConfig, devices }} from '@playwright/test';

export default defineConfig({{
  testDir: './',
  fullyParallel: true,
  forbidOnly: false,
  retries: process.env.CI ? 2 : 0,
  use: {{
    launchOptions: {{
      slowMo: 3000,
    }},
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
            # 检查 npx 是否可用
            npx_check = subprocess.run(
                ["npx", "--version"],
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

            # 运行 Playwright 测试，使用 Allure 报告
            result = subprocess.run(
                [
                    "npx", "playwright", "test",
                    "--reporter=@playwright/test/allure-playwright",  # 使用 Allure 报告器
                    f"--output={work_dir}/allure-results",  # Allure 结果目录
                ],
                cwd=work_dir,
                capture_output=True,
                text=True,
                encoding='utf-8',
                errors='replace',
                timeout=execution_config.get("timeout", 300),  # 5 分钟超时
            )

            # 读取 JSON 格式的测试结果（如果同时配置了 json 报告器）
            json_results_file = work_dir / "results.json"
            if json_results_file.exists():
                with open(json_results_file, "r", encoding="utf-8") as f:
                    return json.load(f)
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

                        status = TestResultStatus.PASSED
                        if test.get("ok", False):
                            passed_tests += 1
                        else:
                            failed_tests += 1
                            status = TestResultStatus.FAILED

                        # 保存测试结果
                        await self._save_test_result(
                            run_id=run_id,
                            api_test=api_test,
                            test_name=test.get("title", ""),
                            status=status,
                            results=test.get("results", []),
                        )

                        # IDP 缺陷处理：仅对失败的测试
                        if status == TestResultStatus.FAILED:
                            await self._process_idp_defect(
                                run_id=run_id,
                                api_test=api_test,
                                test_name=test.get("title", ""),
                                results=test.get("results", []),
                                report_url=report_url,
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
        _results: List[Dict[str, Any]],
    ):
        """
        保存单个测试结果

        Args:
            run_id: 测试运行 ID
            api_test: API 测试
            test_name: 测试名称
            status: 测试状态
            _results: 测试结果详情（预留，用于提取断言和执行时间）
        """
        try:
            # 提取端点和 HTTP 方法（从测试名称中解析）
            endpoint, method = self._parse_endpoint_from_test_name(test_name)

            # 创建测试结果记录
            await self.api_test_result_repo.create(
                test_run_id=run_id,
                api_test_id=api_test.id,
                scenario_name=test_name,
                endpoint=endpoint,
                method=method,
                status=status,
                request_summary={
                    "url": api_test.test_config.get("base_url", ""),
                    "method": method,
                },
                response_summary={
                    "status_code": 200 if status == TestResultStatus.PASSED else 500,
                },
                error_message=None if status == TestResultStatus.PASSED else "测试失败",
                duration_ms=0,  # TODO: 从测试结果中提取
                retry_count=0,
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

    async def _process_idp_defect(
        self,
        run_id: UUID,
        api_test: APITest,
        test_name: str,
        results: List[Dict[str, Any]],
        report_url: Optional[str] = None,
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
            request_summary = None
            response_summary = None
            endpoint = None
            method = None
            has_complete_evidence = False

            if results:
                last_result = results[-1]
                error_message = last_result.get("error", {}).get("message", "")

                # 尝试从 Playwright 的 request/response 结构中提取
                pw_request = last_result.get("request", {})
                pw_response = last_result.get("response", {})

                # 提取真实的 URL 和 method（优先从 request 对象获取）
                actual_url = pw_request.get("url", "")
                actual_method = pw_request.get("method", "")

                if actual_url and actual_method:
                    has_complete_evidence = True
                    # 从完整 URL 中提取 endpoint（去掉 base_url 部分）
                    base_url = api_test.test_config.get("base_url", "")
                    endpoint = actual_url
                    if base_url and actual_url.startswith(base_url):
                        endpoint = actual_url[len(base_url):]
                    method = actual_method

                    # 提取请求头
                    request_headers = pw_request.get("headers", {})
                    # 提取请求体
                    request_body = pw_request.get("postData", "")
                    if isinstance(request_body, str) and request_body:
                        try:
                            request_body = json.loads(request_body)
                        except json.JSONDecodeError:
                            pass  # 保持字符串

                    # 提取响应信息
                    response_status = pw_response.get("status", 0)
                    response_headers = pw_response.get("headers", {})
                    response_body = pw_response.get("body", "")
                    if isinstance(response_body, str) and response_body:
                        try:
                            response_body = json.loads(response_body)
                        except json.JSONDecodeError:
                            pass  # 保持字符串

                    # 从响应头或响应体中提取 reqid
                    reqid = response_headers.get("x-request-id", "") or response_headers.get("reqid", "")
                    if not reqid and isinstance(response_body, dict):
                        reqid = response_body.get("reqid", "") or response_body.get("requestId", "")

                    request_summary = {
                        "url": actual_url,
                        "base_url": base_url,
                        "method": actual_method,
                        "headers": request_headers,
                        "body": request_body,
                        "expected_status_code": None,  # 无法从 Playwright 结果中可靠获取
                    }
                    response_summary = {
                        "status_code": response_status,
                        "headers": response_headers,
                        "body": response_body,
                        "reqid": reqid,
                    }
                else:
                    # 降级：从测试标题和配置中解析（可靠性较低）
                    endpoint, method = self._parse_endpoint_from_test_name(test_name)
                    request_summary = {
                        "url": api_test.test_config.get("base_url", ""),
                        "method": method,
                        "headers": {},
                        "body": {},
                    }
                    response_summary = {
                        "status_code": last_result.get("status", 500),
                        "headers": {},
                        "body": {},
                    }

            # 2. 缺陷决策判断
            actual_status_code = response_summary.get("status_code") if response_summary else None
            expected_status_code = request_summary.get("expected_status_code") if request_summary else None

            # 如果没有完整请求证据，降低自动创建置信度
            if not has_complete_evidence:
                logger.warning(
                    "[IDPDefect] 测试 '%s' 缺少完整的请求/响应证据，"
                    "将创建 pending 状态记录而非自动创建缺陷",
                    test_name,
                )

            decision = DefectDecisionService.decide(
                test_status="failed",
                error_message=error_message,
                response_status_code=actual_status_code,
                expected_status_code=expected_status_code or 200,
            )

            # 3. 如果决策为跳过，不创建缺陷
            if decision.decision.value == "skip":
                logger.info("[IDPDefect] 跳过创建缺陷: %s", decision.reason)
                return

            # 4. 如果缺少完整证据，标记为 pending（不自动创建）
            if not has_complete_evidence:
                # 创建 pending 记录，等待人工确认
                idp_service = IDPDefectService(self.session)
                await idp_service.process_test_failure(
                    test_run_id=run_id,
                    test_case_id=api_test.test_case_id,
                    source_project_key=api_test.project.identifier if api_test.project else "UNKNOWN",
                    decision=decision,
                    scenario_name=test_name,
                    endpoint=endpoint or "unknown",
                    method=method or "GET",
                    request_summary=request_summary,
                    response_summary=response_summary,
                    report_url=None,
                    allow_create=False,  # 缺少完整证据，禁止自动创建
                )
                logger.info("[IDPDefect] 记录已创建为 pending 状态，等待人工确认")
                return

            # 5. 获取项目标识符
            source_project_key = None
            if api_test.project and hasattr(api_test.project, "identifier"):
                source_project_key = api_test.project.identifier

            if not source_project_key:
                logger.warning(
                    "[IDPDefect] 跳过创建缺陷: api_test.project 不存在或没有 identifier"
                )
                # 创建 skipped 记录，标记为 project_mapping_missing
                idp_service = IDPDefectService(self.session)
                await idp_service.process_test_failure(
                    test_run_id=run_id,
                    test_case_id=api_test.test_case_id,
                    source_project_key="UNKNOWN",
                    decision=decision,
                    scenario_name=test_name,
                    endpoint=endpoint or "unknown",
                    method=method or "GET",
                    request_summary=request_summary,
                    response_summary=response_summary,
                    report_url=None,
                )
                return

            # 6. 调用统一缺陷登记服务
            registration_service = DefectRegistrationService(self.session)
            await registration_service.register_from_api_failure(
                test_run_id=run_id,
                test_case_id=api_test.test_case_id,
                source_project_key=source_project_key,
                scenario_name=test_name,
                endpoint=endpoint or "unknown",
                method=method or "GET",
                request_summary=request_summary,
                response_summary=response_summary,
                error_message=error_message,
                report_url=report_url,
            )

        except Exception as e:
            # IDP 异常不影响测试结果保存，仅记录日志
            logger.error("[IDPDefect] 处理缺陷创建时发生错误（不影响测试结果）: %s", e)

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
