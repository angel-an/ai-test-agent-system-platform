"""
API 端点脚本执行器

从 attachments 表取测试脚本，在已安装 Playwright 的 workspace 目录下执行。
"""

import asyncio
import json
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, List
from uuid import UUID, uuid4

from sqlalchemy.ext.asyncio import AsyncSession

from app.config.database import async_session_factory
from app.config.settings import settings
from app.config.minio_client import MinIOClient
from app.models.api_test import APITestResult
from app.models.attachment import Attachment
from app.repositories.api_test_repo import APITestRunRepository
from app.schemas.enums import TestResultStatus


# Windows 上 npx 的实际可执行文件是 npx.cmd
_NPX = "npx.cmd" if sys.platform == "win32" else "npx"


async def execute_endpoint_script(
    session: AsyncSession,
    attachment: Attachment,
    project_id: UUID,
    api_test_id: UUID,
    execution_config: Dict[str, Any],
) -> str:
    """下载端点测试脚本并在 workspace 中以 Playwright 执行，返回 run_id。"""
    run_repo = APITestRunRepository(session)
    script_content = MinIOClient.download_file(attachment.object_name).decode("utf-8")

    identifier = f"SR-EP-{attachment.entity_id}"
    run = await run_repo.create(
        project_id=project_id,
        api_test_id=api_test_id,
        identifier=identifier,
        status="running",
        execution_config=execution_config,
        total_tests=0,
        passed_tests=0,
        failed_tests=0,
        skipped_tests=0,
    )
    run_id = run.id

    asyncio.create_task(
        _run_in_background(
            run_id=run_id,
            script_content=script_content,
            execution_config=execution_config,
            api_test_id=api_test_id,
        )
    )
    return str(run_id)


async def _run_in_background(
    run_id: UUID,
    script_content: str,
    execution_config: Dict[str, Any],
    api_test_id: UUID,
):
    workspace = Path(settings.api_workspace_root).resolve()
    script_file = workspace / "tests" / f"sr_{run_id}_{uuid4().hex[:8]}.spec.ts"

    status = "failed"
    error_message: str | None = None
    total = passed = failed = 0
    detail_rows: list[dict] = []

    print(f"[endpoint_executor] run={run_id} workspace={workspace}")

    try:
        script_file.parent.mkdir(parents=True, exist_ok=True)
        script_file.write_text(script_content, encoding="utf-8")
        print(f"[endpoint_executor] run={run_id} 写入脚本: {script_file.name}")

        rel = script_file.relative_to(workspace).as_posix()
        timeout = int(execution_config.get("timeout", 300))

        result: subprocess.CompletedProcess = await asyncio.to_thread(
            subprocess.run,
            [_NPX, "playwright", "test", rel, "--reporter=json"],
            cwd=str(workspace),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
        )
        stdout = result.stdout or ""
        stderr = result.stderr or ""
        print(
            f"[endpoint_executor] run={run_id} returncode={result.returncode} "
            f"stdout_len={len(stdout)} stderr_len={len(stderr)}"
        )

        if stdout.strip():
            try:
                report = json.loads(stdout)
                total, passed, failed, detail_rows = _parse_playwright_json(report)
            except json.JSONDecodeError as e:
                print(f"[endpoint_executor] run={run_id} JSON 解析失败: {e}")

        if result.returncode == 0:
            status = "completed"
        else:
            status = "failed"
            error_message = (stderr.strip() or stdout.strip() or "执行失败")[:2000]

    except subprocess.TimeoutExpired:
        error_message = f"执行超时 ({execution_config.get('timeout', 300)}s)"
        print(f"[endpoint_executor] run={run_id} 超时")
    except Exception as e:
        print(f"[endpoint_executor] run={run_id} 执行异常: {e}")
        error_message = str(e)
    finally:
        try:
            if script_file.exists():
                script_file.unlink()
        except Exception:
            pass

    try:
        async with async_session_factory() as update_session:
            update_repo = APITestRunRepository(update_session)
            run = await update_repo.get_by_id(run_id)
            if run:
                await update_repo.update(
                    run,
                    status=status,
                    error_message=error_message,
                    total_tests=total,
                    passed_tests=passed,
                    failed_tests=failed,
                )

            # 写入每个用例的明细 (APITestResult)
            for row in detail_rows:
                update_session.add(APITestResult(
                    test_run_id=run_id,
                    api_test_id=api_test_id,
                    scenario_name=row["scenario_name"],
                    endpoint=row["endpoint"],
                    method=row["method"],
                    status=row["status"],
                    request_summary=row.get("request_summary"),
                    response_summary=row.get("response_summary"),
                    error_message=row.get("error_message"),
                ))

            await update_session.commit()
            print(
                f"[endpoint_executor] run={run_id} 状态更新: {status} "
                f"(total={total} passed={passed} failed={failed} details={len(detail_rows)})"
            )
    except Exception as e:
        print(f"[endpoint_executor] run={run_id} 状态更新失败: {e}")


def _parse_playwright_json(report: dict) -> tuple[int, int, int, List[dict]]:
    import re

    total = passed = failed = 0
    rows: list[dict] = []

    _verb_re = re.compile(r"\b(GET|POST|PUT|PATCH|DELETE|HEAD|OPTIONS)\b\s+(\S+)", re.IGNORECASE)
    _status_re = re.compile(r"(?:status[\s:=]+|→\s*|->\s*|=>\s*)(\d{3})\b", re.IGNORECASE)
    _json_re = re.compile(r"(\{[^{}\n]*\})")

    def _extract_endpoint_method(title: str) -> tuple[str, str]:
        if not title:
            return "-", "-"
        m = _verb_re.search(title)
        if m:
            return m.group(2)[:500], m.group(1).upper()
        return title[:500], "-"

    def _glean_from_stdout(stdout_entries):
        """从 result.stdout 数组中提取 status_code 和 request body 摘要"""
        status_code: str | None = None
        body: str | None = None
        endpoint: str | None = None
        method: str | None = None
        text = ""
        for entry in stdout_entries or []:
            t = entry.get("text") if isinstance(entry, dict) else str(entry)
            if t:
                text += t + "\n"
        if not text:
            return None, None, None, None
        s = _status_re.search(text)
        if s:
            status_code = s.group(1)
        v = _verb_re.search(text)
        if v:
            method = v.group(1).upper()
            endpoint = v.group(2)
        # 取第一段 {...} 作为请求/响应摘要
        j = _json_re.search(text)
        if j:
            body = j.group(1)[:200]
        return status_code, body, endpoint, method

    def walk(suite: dict, suite_title: str = ""):
        nonlocal total, passed, failed
        current_title = suite.get("title") or suite_title
        for spec in suite.get("specs", []):
            spec_title = spec.get("title") or current_title or "-"
            for test in spec.get("tests", []):
                for r in test.get("results", []):
                    total += 1
                    raw_status = r.get("status", "")
                    if raw_status == "passed":
                        passed += 1
                        st = TestResultStatus.PASSED
                    elif raw_status == "skipped":
                        st = TestResultStatus.SKIPPED
                    else:
                        failed += 1
                        st = TestResultStatus.FAILED

                    endpoint, method = _extract_endpoint_method(spec_title)
                    status_code, body, ep_out, mt_out = _glean_from_stdout(r.get("stdout"))
                    if ep_out:
                        endpoint = ep_out[:500]
                    if mt_out:
                        method = mt_out

                    err = r.get("error") or {}
                    err_msg = (err.get("message") or err.get("stack") or "")[:2000] or None
                    duration = r.get("duration")

                    response_summary = {}
                    if duration is not None:
                        response_summary["duration_ms"] = duration
                    if status_code:
                        response_summary["status_code"] = status_code

                    request_summary = None
                    if body:
                        request_summary = {"body_summary": body}

                    rows.append({
                        "scenario_name": (spec_title or "-")[:500],
                        "endpoint": endpoint,
                        "method": method,
                        "status": st,
                        "request_summary": request_summary,
                        "response_summary": response_summary or None,
                        "error_message": err_msg,
                    })
        for sub in suite.get("suites", []):
            walk(sub, current_title)

    for suite in report.get("suites", []):
        walk(suite)
    return total, passed, failed, rows
