"""
Android 测试执行工具（企业级增强版）

合并了新版简化执行 + 旧版企业级特性：
- 失败重试与可重试错误识别
- Midscene 报告解析（三层降级：JSON dump → HTML → stdout）
- 报告 ZIP 打包上传 MinIO
- 跨平台支持（Windows/Linux 命令差异）

对外工具：
- execute_android_test: 执行测试脚本（支持重试、报告收集）
- collect_android_report: 收集 Midscene HTML 报告并上传 MinIO
- parse_android_test_report: 解析 HTML 报告提取统计信息
- batch_execute_android_tests: 批量执行多个脚本（错误隔离）
"""

import json
import os
import re
import subprocess
import sys
import traceback
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from langchain_core.tools import tool

from app.config import settings
from app.config.minio_client import MinIOClient
from app.models.attachment import Attachment, AttachmentEntityType
from app.config.database import async_session_factory


# ============================================================================
# 重试策略（从旧版迁移）
# ============================================================================

RETRYABLE_PATTERNS = [
    re.compile(r"element\s+not\s+found", re.IGNORECASE),
    re.compile(r"timed?\s*out", re.IGNORECASE),
    re.compile(r"ai\s+(?:plan|think|action)\s+(?:fail|timeout)", re.IGNORECASE),
    re.compile(r"INJECT_EVENTS", re.IGNORECASE),
    re.compile(r"screenshot\s+fail", re.IGNORECASE),
    re.compile(r"could\s+not\s+locate", re.IGNORECASE),
    re.compile(r"no\s+android\s+device\s+connected", re.IGNORECASE),
    re.compile(r"device\s+offline", re.IGNORECASE),
]

NON_RETRYABLE_PATTERNS = [
    re.compile(r"SyntaxError"),
    re.compile(r"ReferenceError"),
    re.compile(r"TypeError"),
    re.compile(r"MODULE_NOT_FOUND"),
    re.compile(r"Cannot\s+find\s+module", re.IGNORECASE),
    re.compile(r"app\s+not\s+installed", re.IGNORECASE),
]


def _is_retryable_error(stdout: str, stderr: str, return_code: int) -> bool:
    """
    判断错误是否可重试。

    可重试：视觉定位抖动、AI 规划超时、设备瞬断、弹窗干扰等
    不可重试：脚本语法错误、模块缺失、App 未安装等
    """
    combined = f"{stdout}\n{stderr}"

    if any(p.search(combined) for p in NON_RETRYABLE_PATTERNS):
        return False

    if return_code != 0 and any(p.search(combined) for p in RETRYABLE_PATTERNS):
        return True

    # 未知错误默认不重试，避免无限消耗资源
    return False


def _build_retry_context(attempt: int) -> str:
    """生成重试时注入的额外上下文提示"""
    if attempt == 1:
        return "如果因弹窗或页面未加载导致失败，请增加 aiWaitFor 等待。"
    return "已连续失败，建议降低截图缩放因子或增加 replanningCycleLimit。"


# ============================================================================
# 报告解析（从旧版迁移）
# ============================================================================

def _find_midscene_report_dir(project_root: Path, report_file_name: Optional[str] = None) -> Optional[Path]:
    """定位 Midscene 报告目录"""
    report_root = project_root / "midscene_run" / "report"
    if report_root.exists() and any(report_root.iterdir()):
        return report_root
    return None


def _parse_midscene_results(
    project_root: Path,
    report_file_name: Optional[str] = None
) -> List[Dict[str, Any]]:
    """
    解析 Midscene 执行结果。

    优先查找 persistExecutionDump 生成的 JSON dump，
    否则尝试从报告目录的 HTML 文件名和 stdout 推断。
    """
    results: List[Dict[str, Any]] = []

    # 1. 查找 JSON dump
    dump_candidates = [
        project_root / "midscene_run" / "dump" / f"{report_file_name}.json",
        project_root / "midscene_run" / "dump" / "execution-dump.json",
        project_root / "midscene_run" / "dump.json",
    ]
    for dump_path in dump_candidates:
        if dump_path.exists():
            try:
                with open(dump_path, "r", encoding="utf-8") as f:
                    dump = json.load(f)
                return _normalize_dump_results(dump)
            except Exception:
                pass

    # 2. 查找 HTML 报告文件，按文件名推断用例
    report_dir = _find_midscene_report_dir(project_root, report_file_name)
    if report_dir:
        html_files = sorted(report_dir.glob("*.html"))
        for html_file in html_files:
            results.append({
                "case_id": html_file.stem,
                "scenario_name": html_file.stem,
                "status": "unknown",
                "duration_ms": None,
                "error_message": None,
                "screenshot_path": None,
                "test_summary": {"report_file": html_file.name},
            })

    return results


def _normalize_dump_results(dump: Any) -> List[Dict[str, Any]]:
    """将 Midscene dump 归一化为内部结果格式"""
    results: List[Dict[str, Any]] = []
    if not isinstance(dump, dict):
        return results

    tasks = dump.get("tasks") or dump.get("actions") or []
    for idx, task in enumerate(tasks):
        if not isinstance(task, dict):
            continue
        status = "passed" if task.get("success") or task.get("status") == "success" else "failed"
        error = task.get("error") or task.get("errorMessage") or task.get("thought")
        results.append({
            "case_id": task.get("name") or task.get("id") or f"step_{idx+1}",
            "scenario_name": task.get("name") or f"Step {idx+1}",
            "status": status,
            "duration_ms": task.get("durationMs") or task.get("duration") or task.get("time"),
            "error_message": error if status == "failed" else None,
            "screenshot_path": task.get("screenshot") or task.get("screenshotAfter"),
            "test_summary": task,
        })
    return results


def _extract_results_from_stdout(stdout: str) -> List[Dict[str, Any]]:
    """stdout 降级解析：用于没有 dump 的情况"""
    results: List[Dict[str, Any]] = []

    # 匹配 "=== Test: xxx ===" 或 "Step N: xxx"
    step_pattern = re.compile(r"(?:===\s*Test:\s*(.+?)\s*===|Step\s+(\d+)\s*:\s*(.+))", re.IGNORECASE)
    error_pattern = re.compile(r"(?:Error|❌|Test failed):?\s*(.+)", re.IGNORECASE)

    steps = step_pattern.findall(stdout)
    errors = error_pattern.findall(stdout)

    if not steps:
        # 整体作为一条结果
        has_error = bool(errors)
        results.append({
            "case_id": "main",
            "scenario_name": "Main Test",
            "status": "failed" if has_error else "passed",
            "duration_ms": None,
            "error_message": errors[0] if errors else None,
            "screenshot_path": None,
            "test_summary": {"stdout_steps": 0},
        })
        return results

    for name_from_test, step_num, name_from_step in steps:
        scenario_name = (name_from_test or name_from_step or f"Step {step_num}").strip()
        case_id = f"step_{step_num or 0}"
        results.append({
            "case_id": case_id,
            "scenario_name": scenario_name,
            "status": "passed",  # 默认通过，后续根据错误覆盖
            "duration_ms": None,
            "error_message": None,
            "screenshot_path": None,
            "test_summary": {"source": "stdout"},
        })

    # 如果有错误但未定位到具体步骤，标记最后一步失败
    if errors and results:
        results[-1]["status"] = "failed"
        results[-1]["error_message"] = errors[0]

    return results


# ============================================================================
# 执行核心（从旧版迁移 + 适配新版）
# ============================================================================

async def _execute_once(
    script_path: str,
    script_filename: str,
    project_root: str,
    extra_env: Optional[Dict[str, str]] = None,
) -> Dict[str, Any]:
    """单次执行脚本（支持跨平台）"""
    start_time = datetime.now(timezone.utc)
    is_windows = sys.platform == "win32"

    # 使用 npx tsx 执行（新版的标准方式）
    if is_windows:
        cmd = f'npx tsx "{script_filename}"'
    else:
        cmd = ["npx", "tsx", script_filename]

    env = os.environ.copy()
    env["CI"] = "1"
    if extra_env:
        env.update(extra_env)

    result = subprocess.run(
        cmd,
        cwd=project_root,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=600,
        shell=is_windows,
        env=env,
    )

    end_time = datetime.now(timezone.utc)
    duration = (end_time - start_time).total_seconds()

    report_dir = Path(project_root) / "midscene_run" / "report"
    report_path = str(report_dir) if report_dir.exists() and any(report_dir.iterdir()) else None

    return {
        "success": result.returncode == 0,
        "return_code": result.returncode,
        "duration": duration,
        "stdout": result.stdout,
        "stderr": result.stderr,
        "report_path": report_path,
        "start_time": start_time.isoformat(),
        "end_time": end_time.isoformat(),
    }


async def _execute_with_retry(
    script_path: str,
    script_filename: str,
    project_root: str,
    max_retries: int = 2,
) -> Dict[str, Any]:
    """内部执行：支持重试"""
    last_result: Optional[Dict[str, Any]] = None
    attempt = 0

    while attempt <= max_retries:
        attempt += 1
        print(f"[Android Test Execution] 执行尝试 {attempt}/{max_retries + 1}: {script_filename}")

        extra_env = {}
        if attempt > 1:
            extra_env["MIDSCENE_RETRY_ATTEMPT"] = str(attempt)
            extra_env["MIDSCENE_RETRY_HINT"] = _build_retry_context(attempt - 1)

        last_result = await _execute_once(
            script_path=script_path,
            script_filename=script_filename,
            project_root=project_root,
            extra_env=extra_env,
        )

        if last_result.get("success"):
            last_result["attempts"] = attempt
            last_result["retried"] = attempt > 1
            return last_result

        if attempt <= max_retries and _is_retryable_error(
            last_result.get("stdout", ""),
            last_result.get("stderr", ""),
            last_result.get("return_code", -1),
        ):
            print(f"[Android Test Execution] 检测到可重试错误，准备第 {attempt + 1} 次尝试")
            continue
        else:
            print("[Android Test Execution] 错误不可重试或已达最大重试次数")
            break

    last_result = last_result or {"success": False, "error": "未执行"}
    last_result["attempts"] = attempt
    last_result["retried"] = attempt > 1
    return last_result


# ============================================================================
# 报告上传（从旧版迁移 + 适配新版）
# ============================================================================

async def _save_android_test_report_zip(
    project_identifier: str,
    report_path: str,
    execution_result: Dict[str, Any],
    project_root: str,
) -> Optional[str]:
    """保存 Android 测试报告到 MinIO（ZIP 打包）并创建附件记录"""
    try:
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        zip_filename = f"android_test_report_{timestamp}.zip"
        zip_path = Path(project_root) / zip_filename

        print(f"[Android Report] 打包测试报告: {report_path} -> {zip_path}")

        with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zipf:
            report_dir = Path(report_path)
            for file_path in report_dir.rglob("*"):
                if file_path.is_file():
                    arcname = file_path.relative_to(report_dir)
                    zipf.write(file_path, arcname)

        with open(zip_path, "rb") as f:
            zip_bytes = f.read()

        object_name = f"android-tests/{project_identifier}/reports/test-report-{timestamp}.zip"
        MinIOClient.upload_bytes(
            object_name=object_name,
            data=zip_bytes,
            content_type="application/zip",
        )

        print(f"[Android Report] 报告已上传到 MinIO: {object_name}")

        duration = execution_result.get("duration", 0)
        stdout = execution_result.get("stdout", "")
        passed_count = stdout.count("Test passed") + stdout.count("passed")
        failed_count = stdout.count("Test failed") + stdout.count("failed")

        description = "Android 测试报告\n"
        description += f"执行时间: {duration:.2f}秒\n"
        if passed_count > 0 or failed_count > 0:
            description += f"通过: {passed_count} | 失败: {failed_count}"

        async with async_session_factory() as session:
            attachment = Attachment(
                entity_type=AttachmentEntityType.ANDROID_TEST_REPORT,
                entity_id=UUID("00000000-0000-0000-0000-000000000001"),  # 默认实体ID
                project_id=UUID("00000000-0000-0000-0000-000000000001"),  # 默认项目ID
                file_name=f"android-test-report-{timestamp}.zip",
                file_size=len(zip_bytes),
                content_type="application/zip",
                object_name=object_name,
                description=description,
                created_by="android-agent",
            )
            session.add(attachment)
            await session.commit()
            await session.refresh(attachment)

        try:
            zip_path.unlink()
        except Exception as e:
            print(f"[Android Report] 清理临时 ZIP 文件失败: {e}")

        return object_name

    except Exception as e:
        print(f"[Android Report] 保存测试报告失败: {e}")
        traceback.print_exc()
        return None


# ============================================================================
# 对外工具
# ============================================================================

@tool
async def execute_android_test(
    local_script_path: str,
    device_udid: Optional[str] = None,
    reporter: str = "verbose",
    timeout: int = 600,
    max_retries: int = 2,
    project_identifier: str = "",
    app_package: str = "",
) -> str:
    """
    执行 Android 测试脚本（企业级增强版）

    使用 npx vitest run 或 npx tsx 执行已下载到本地测试目录的 Midscene Android 测试脚本。
    支持失败重试、报告自动收集和上传。

    Args:
        local_script_path: 本地测试脚本路径（相对于 workspace_root 或绝对路径）
        device_udid: 可选，指定设备序列号（多设备时使用）
        reporter: 报告格式 (verbose, json, html, dot)
        timeout: 测试执行超时时间（秒），默认 10 分钟
        max_retries: 最大重试次数（默认 2），对可重试错误自动重试
        project_identifier: 项目标识符，用于保存测试报告
        app_package: 被测应用包名，用于保存测试报告

    Returns:
        JSON 格式的测试执行结果，包含执行详情、解析结果、报告路径

    Example:
        >>> result = await execute_android_test(
        ...     local_script_path="tests/login_test_20250613_143000.test.ts",
        ...     device_udid="abc123",
        ...     max_retries=2,
        ...     project_identifier="proj_001",
        ...     app_package="com.example.app"
        ... )
    """
    try:
        # 1. 解析脚本路径
        script_path = Path(local_script_path)
        if not script_path.is_absolute():
            workspace_root = Path(settings.android_workspace_root).resolve()
            script_path = workspace_root / local_script_path

        if not script_path.exists():
            return json.dumps({
                "success": False,
                "error": f"测试脚本不存在: {local_script_path}",
                "resolved_path": str(script_path.resolve()),
            }, ensure_ascii=False, indent=2)

        # 确保脚本在 workspace 目录内
        workspace_root = Path(settings.android_workspace_root).resolve()
        try:
            script_path.relative_to(workspace_root)
        except ValueError:
            return json.dumps({
                "success": False,
                "error": "测试脚本必须在 workspace 目录内"
            }, ensure_ascii=False, indent=2)

        # 2. 设置环境变量
        env = os.environ.copy()
        if device_udid:
            env["ANDROID_SERIAL"] = device_udid

        # 3. 执行脚本（带重试）
        execution_result = await _execute_with_retry(
            script_path=str(script_path),
            script_filename=script_path.name,
            project_root=str(workspace_root),
            max_retries=max_retries,
        )

        # 4. 解析结果（三层降级）
        parsed_results = _parse_midscene_results(workspace_root)
        if not parsed_results:
            parsed_results = _extract_results_from_stdout(execution_result.get("stdout", ""))

        # 5. 收集并上传报告（如果配置了项目标识符）
        report_object_name: Optional[str] = None
        if project_identifier and execution_result.get("report_path"):
            try:
                report_object_name = await _save_android_test_report_zip(
                    project_identifier=project_identifier,
                    report_path=execution_result["report_path"],
                    execution_result=execution_result,
                    project_root=str(workspace_root),
                )
            except Exception as e:
                print(f"[Android Test Execution] 报告上传失败: {e}")
                traceback.print_exc()

        # 6. 构建返回结果
        result_payload = {
            "success": execution_result.get("success", False),
            "script_path": str(script_path),
            "script_filename": script_path.name,
            "attempts": execution_result.get("attempts", 1),
            "retried": execution_result.get("retried", False),
            "device_udid": device_udid,
            "reporter": reporter,
            "report_object_name": report_object_name,
            "execution_result": {
                "return_code": execution_result.get("return_code"),
                "duration": execution_result.get("duration"),
                "stdout": execution_result.get("stdout", "")[-4000:],
                "stderr": execution_result.get("stderr", "")[-2000:],
                "report_path": execution_result.get("report_path"),
            },
            "parsed_results": [
                {
                    "case_id": r.get("case_id"),
                    "scenario_name": r.get("scenario_name"),
                    "status": r.get("status"),
                    "error_message": r.get("error_message"),
                }
                for r in parsed_results
            ],
            "timestamp": datetime.now().isoformat(),
        }

        return json.dumps(result_payload, ensure_ascii=False, indent=2)

    except subprocess.TimeoutExpired:
        return json.dumps({
            "success": False,
            "error": f"测试执行超时（{timeout}秒）"
        }, ensure_ascii=False, indent=2)
    except FileNotFoundError:
        return json.dumps({
            "success": False,
            "error": "npx 命令未找到，请确保 Node.js 和 npm 已安装"
        }, ensure_ascii=False, indent=2)
    except Exception as e:
        traceback.print_exc()
        return json.dumps({
            "success": False,
            "error": f"测试执行失败: {str(e)}"
        }, ensure_ascii=False, indent=2)


@tool
async def collect_android_report(
    report_dir: Optional[str] = None,
    project_identifier: str = "",
    app_package: str = "",
) -> str:
    """
    收集 Midscene Android 测试生成的报告

    Midscene 默认在 midscene_run/report/ 目录生成 HTML 报告。
    此工具扫描报告目录，收集所有报告文件并上传到 MinIO。

    Args:
        report_dir: 报告目录路径（可选，默认为 workspace/midscene_run/report/）
        project_identifier: 项目标识符
        app_package: 被测应用包名

    Returns:
        JSON 格式的报告收集结果

    Example:
        >>> result = await collect_android_report(
        ...     project_identifier="proj_001",
        ...     app_package="com.example.app"
        ... )
    """
    try:
        # 确定报告目录
        if report_dir:
            report_path = Path(report_dir)
        else:
            workspace_root = Path(settings.android_workspace_root).resolve()
            report_path = workspace_root / "midscene_run" / "report"

        if not report_path.exists():
            return json.dumps({
                "success": False,
                "error": f"报告目录不存在: {report_path}",
                "hint": "请确认测试已执行，Midscene 已生成报告",
            }, ensure_ascii=False, indent=2)

        # 收集报告文件
        collected_files = []
        for file_path in report_path.rglob("*"):
            if file_path.is_file():
                relative_path = file_path.relative_to(report_path)

                # 确定 content type
                suffix = file_path.suffix.lower()
                content_type_map = {
                    ".html": "text/html",
                    ".css": "text/css",
                    ".js": "application/javascript",
                    ".png": "image/png",
                    ".jpg": "image/jpeg",
                    ".jpeg": "image/jpeg",
                    ".gif": "image/gif",
                    ".json": "application/json",
                    ".svg": "image/svg+xml",
                }
                content_type = content_type_map.get(suffix, "application/octet-stream")

                # 生成 MinIO 对象名称
                object_name = f"android-tests/{project_identifier}/apps/{app_package}/reports/{relative_path.as_posix()}"

                # 上传文件
                file_size = file_path.stat().st_size
                with open(file_path, "rb") as f:
                    MinIOClient.upload_file(
                        object_name=object_name,
                        data=f,
                        length=file_size,
                        content_type=content_type,
                    )

                collected_files.append({
                    "file_name": file_path.name,
                    "relative_path": relative_path.as_posix(),
                    "object_name": object_name,
                    "file_size": file_size,
                    "content_type": content_type,
                })

        # 查找主报告文件（HTML）
        main_report = None
        for f in collected_files:
            if f["file_name"].endswith(".html"):
                main_report = f
                break

        return json.dumps({
            "success": True,
            "report_dir": str(report_path),
            "collected_files_count": len(collected_files),
            "collected_files": collected_files,
            "main_report": main_report,
            "message": f"已收集 {len(collected_files)} 个报告文件到 MinIO",
        }, ensure_ascii=False, indent=2)

    except Exception as e:
        return json.dumps({
            "success": False,
            "error": f"收集报告失败: {str(e)}"
        }, ensure_ascii=False, indent=2)


@tool
async def parse_android_test_report(
    report_object_name: str,
) -> str:
    """
    解析 Android 测试报告提取关键信息

    从 MinIO 下载 HTML 报告并解析其中的关键信息（测试用例数、通过率、失败原因等）。

    Args:
        report_object_name: MinIO 中的报告对象路径（HTML 文件）

    Returns:
        JSON 格式的解析结果

    Example:
        >>> result = await parse_android_test_report(
        ...     "android-tests/proj_001/apps/com.example.app/reports/report-123.html"
        ... )
    """
    try:
        # 从 MinIO 下载报告
        report_bytes = MinIOClient.download_file(report_object_name)
        report_html = report_bytes.decode('utf-8', errors='replace')

        # 解析 HTML 提取关键信息
        summary = {
            "total_tests": 0,
            "passed": 0,
            "failed": 0,
            "skipped": 0,
            "duration": "",
        }

        # 尝试提取各种格式的统计信息
        pass_match = re.search(r'(\d+)\s+tests?\s+passed', report_html, re.IGNORECASE)
        if pass_match:
            summary["passed"] = int(pass_match.group(1))

        fail_match = re.search(r'(\d+)\s+tests?\s+failed', report_html, re.IGNORECASE)
        if fail_match:
            summary["failed"] = int(fail_match.group(1))

        skip_match = re.search(r'(\d+)\s+skipped', report_html, re.IGNORECASE)
        if skip_match:
            summary["skipped"] = int(skip_match.group(1))

        # 计算总数
        summary["total_tests"] = summary["passed"] + summary["failed"] + summary["skipped"]

        # 提取执行时间
        duration_match = re.search(r'duration[:\s]+([\d:]+)', report_html, re.IGNORECASE)
        if duration_match:
            summary["duration"] = duration_match.group(1)

        # 提取失败详情
        failed_tests = []
        fail_sections = re.findall(
            r'<[^>]*class="[^"]*fail[^"]*"[^>]*>.*?<h[^>]*>(.*?)</h[^>]*>.*?<pre[^>]*>(.*?)</pre>',
            report_html,
            re.DOTALL | re.IGNORECASE
        )
        for title, error in fail_sections[:10]:  # 最多提取10个
            clean_title = re.sub(r'<[^>]+>', '', title).strip()
            clean_error = re.sub(r'<[^>]+>', '', error).strip()
            if clean_title:
                failed_tests.append({
                    "test_name": clean_title,
                    "error": clean_error[:500],
                })

        # 提取所有测试用例名称
        test_cases = []
        test_matches = re.findall(r'<[^>]*class="[^"]*test[^"]*"[^>]*>.*?<[^>]*>([^<]+)</[^>]*>', report_html, re.DOTALL)
        for match in test_matches[:50]:
            clean = re.sub(r'<[^>]+>', '', match).strip()
            if clean and len(clean) < 200:
                test_cases.append(clean)

        return json.dumps({
            "success": True,
            "summary": summary,
            "pass_rate": round(summary["passed"] / summary["total_tests"] * 100, 2) if summary["total_tests"] > 0 else 0,
            "failed_tests": failed_tests,
            "test_cases_found": len(test_cases),
            "test_cases": test_cases[:20],
        }, ensure_ascii=False, indent=2)

    except Exception as e:
        return json.dumps({
            "success": False,
            "error": f"解析测试报告失败: {str(e)}"
        }, ensure_ascii=False, indent=2)


@tool
async def batch_execute_android_tests(
    script_paths: list[str],
    device_udid: Optional[str] = None,
    timeout: int = 600,
    max_retries: int = 2,
    project_identifier: str = "",
) -> str:
    """
    批量执行多个 Android 测试脚本（增强版，支持重试和错误隔离）

    Args:
        script_paths: 本地测试脚本路径列表
        device_udid: 可选，指定设备序列号
        timeout: 每个测试的超时时间（秒）
        max_retries: 每个脚本最大重试次数
        project_identifier: 项目标识符，用于保存报告

    Returns:
        JSON 格式的批量执行结果

    Example:
        >>> result = await batch_execute_android_tests(
        ...     script_paths=["tests/login.test.ts", "tests/search.test.ts"],
        ...     device_udid="abc123",
        ...     max_retries=2
        ... )
    """
    try:
        results = []
        success_count = 0
        failed_count = 0

        for script_path in script_paths:
            result_json = await execute_android_test(
                local_script_path=script_path,
                device_udid=device_udid,
                timeout=timeout,
                max_retries=max_retries,
                project_identifier=project_identifier,
            )
            result_data = json.loads(result_json)
            results.append({
                "script_path": script_path,
                "result": result_data,
            })

            if result_data.get("success"):
                success_count += 1
            else:
                failed_count += 1

        return json.dumps({
            "success": True,
            "summary": {
                "total": len(script_paths),
                "success": success_count,
                "failed": failed_count,
            },
            "results": results,
        }, ensure_ascii=False, indent=2)

    except Exception as e:
        traceback.print_exc()
        return json.dumps({
            "success": False,
            "error": f"批量测试执行失败: {str(e)}"
        }, ensure_ascii=False, indent=2)
