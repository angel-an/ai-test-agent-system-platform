"""
API 端点脚本执行器

从 attachments 表取测试脚本，在已安装 Playwright 的 workspace 目录下执行。
"""

import asyncio
import json
import os
import subprocess
import sys
from pathlib import Path
from app.services.api_contract_profiles import validate_contract_for_execution
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
    """下载端点测试脚本并在 workspace 中以 Playwright 执行，返回 run_id。

    执行前环境配置检查：execution_config 中 requires_env=true 时，校验 base_url
    与鉴权/测试数据引用；缺失则创建 run 并置为 blocked，不执行脚本。
    """
    run_repo = APITestRunRepository(session)

    # 环境配置检查（缺项 -> blocked，不执行）
    missing = check_env_requirements(execution_config)
    if missing:
        identifier = f"SR-EP-{attachment.entity_id}"
        run = await run_repo.create(
            project_id=project_id,
            api_test_id=api_test_id,
            identifier=identifier,
            status="blocked",
            execution_config=redact_config(execution_config),
            total_tests=0,
            passed_tests=0,
            failed_tests=0,
            skipped_tests=0,
            error_message="缺执行环境配置: " + ", ".join(missing),
        )
        return str(run.id)

    script_content = MinIOClient.download_file(attachment.object_name).decode("utf-8")

    identifier = f"SR-EP-{attachment.entity_id}"
    run = await run_repo.create(
        project_id=project_id,
        api_test_id=api_test_id,
        identifier=identifier,
        status="running",
        execution_config=redact_config(execution_config),
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


# 需要脱敏的敏感键（不进入脚本与报告的明文）
_SENSITIVE_KEYS = {"appsecret", "appsercret", "secret", "password", "token", "authorization", "apikey", "api_key"}
# 环境配置必填项（requires_env 时）
_ENV_REQUIRED = ["base_url"]


def check_env_requirements(execution_config: Dict[str, Any]) -> list[str]:
    """执行前环境配置检查，返回缺失项列表（空表示通过）。

    仅当 execution_config.requires_env 为 true 时启用；密钥/数据通过引用
    （env:XXX / data:XXX）注入，缺失引用即阻断。
    """
    if not execution_config.get("requires_env"):
        return []
    missing: list[str] = []
    missing.extend(validate_contract_for_execution(execution_config))
    for key in _ENV_REQUIRED:
        if not execution_config.get(key):
            missing.append(key)
    # 鉴权引用（如 appid_ref/secret_ref）与测试数据引用（如 existing_mobile_ref）
    for key in ("appid_ref", "secret_ref", "existing_mobile_ref", "not_existing_mobile_ref"):
        if key in execution_config and not execution_config.get(key):
            missing.append(key)
    return missing


def redact_config(execution_config: Dict[str, Any]) -> Dict[str, Any]:
    """脱敏 execution_config：敏感键替换为 ***，密钥明文不进入脚本与报告。"""
    out: Dict[str, Any] = {}
    for k, v in execution_config.items():
        if isinstance(v, str) and any(s in k.lower() for s in _SENSITIVE_KEYS):
            out[k] = "***"
        elif isinstance(v, dict):
            out[k] = redact_config(v)
        else:
            out[k] = v
    return out


def _pick_env_value(*vals: Any) -> str:
    """从候选值里挑出第一个非空白字符串；全空返回 ""。"""
    for v in vals:
        if v is None:
            continue
        s = str(v).strip()
        if s:
            return s
    return ""


def _build_subprocess_env(
    execution_config: Dict[str, Any],
    base_env: Dict[str, str] | None = None,
    node_bin: str = "",
) -> Dict[str, str]:
    """构建 Playwright 子进程的环境变量。

    规则：
    - 配置值非空 → 注入并覆盖继承值；
    - 配置值为空/空白 → 继承 base_env（默认 os.environ）里的同名变量，
      不用空串覆盖（值可能由后端运行环境的真实环境变量提供；强行置空会
      导致脚本读到空 APPID/APPSECRET 而把所有用例 test.skip，假执行）；
    - existing_mobile / not_existing_mobile 非空时透传为大写变量；
    - execution_config.env 中的自定义键值对（如 {"api-sign": "xxx", "url": "https://..."}）
      直接注入子进程环境，供脚本通过 process.env.XXX 引用。
    """
    env = dict(base_env) if base_env is not None else os.environ.copy()
    if node_bin:
        env["PATH"] = node_bin + os.pathsep + env.get("PATH", "")
    # env_config 里的敏感值可能以 enc:v1: 前缀加密存储（见 app.utils.env_crypto），
    # 服务端读取时解密还原真实值再注入子进程；非密文原样返回，兼容历史明文。
    from app.utils.env_crypto import decrypt_value
    appid = _pick_env_value(execution_config.get("appid"), execution_config.get("MX_APPID"))
    appsecret = _pick_env_value(execution_config.get("appsecret"), execution_config.get("MX_APPSECRET"))
    if appsecret:
        appsecret = decrypt_value(appsecret)
    if appid:
        env["MX_APPID"] = appid
    if appsecret:
        env["MX_APPSECRET"] = appsecret
    for key in ("existing_mobile", "not_existing_mobile"):
        val = _pick_env_value(execution_config.get(key))
        if val:
            env[key.upper()] = val
    # base_url → API_BASE_URL：`api_case_link_service` 生成的脚本读的是
    # `process.env.API_BASE_URL`，不映射就会回退到脚本里的 http://localhost:8000 占位 ——
    # 于是所有断言打的是本机，跑出的"通过/失败"与目标环境无关。
    # 与 runner.py / script_execution_tools.py 的既有做法一致；
    # 显式 execution_config.env.API_BASE_URL 仍在下面注入、优先级更高。
    base_url = _pick_env_value(execution_config.get("base_url"))
    if base_url:
        env["API_BASE_URL"] = str(base_url).rstrip("/")
    # 自定义环境变量：execution_config.env 中的键值对直接注入。
    # 这些值来自前端执行配置对话框或 API 调用方，在执行时透传给 Playwright 子进程。
    custom_env = execution_config.get("env") or {}
    if isinstance(custom_env, dict):
        for key, value in custom_env.items():
            if key and value is not None:
                env[str(key)] = str(decrypt_value(value)) if isinstance(value, str) else str(value)
    # 入参/出参留证开关（生成脚本读取 PW_LOG_REQUESTS）：默认开启，
    # execution_config.env 显式给 0 时关闭。
    env.setdefault("PW_LOG_REQUESTS", "1")
    return env


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
    total = passed = failed = skipped = 0
    detail_rows: list[dict] = []

    print(f"[endpoint_executor] run={run_id} workspace={workspace}")

    try:
        script_file.parent.mkdir(parents=True, exist_ok=True)
        script_file.write_text(script_content, encoding="utf-8")
        print(f"[endpoint_executor] run={run_id} 写入脚本: {script_file.name}")

        rel = script_file.relative_to(workspace).as_posix()
        timeout = int(execution_config.get("timeout", 300))

        # 注入环境变量：MX_APPID/MX_APPSECRET 用于 mx_md5 签名，
        # 也支持 other env_config key=value 透传。空配置继承后端环境变量，不空串覆盖。
        env = _build_subprocess_env(
            execution_config,
            base_env=os.environ.copy(),
            node_bin=str(workspace / "node_modules" / ".bin"),
        )

        result: subprocess.CompletedProcess = await asyncio.to_thread(
            subprocess.run,
            [_NPX, "playwright", "test", rel, "--reporter=json"],
            cwd=str(workspace),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            env=env,
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
                total, passed, failed, skipped, detail_rows = _parse_playwright_json(report)
            except json.JSONDecodeError as e:
                print(f"[endpoint_executor] run={run_id} JSON 解析失败: {e}")

        # 状态以 Playwright JSON 报告的实际用例结果为准，而非进程退出码。
        # 退出码为 0 不代表业务测试成功（例如全部用例被 skip 时退出码也是 0）。
        if failed > 0:
            status = "failed"
            error_message = (stderr.strip() or stdout.strip() or "存在失败用例")[:2000]
        elif passed > 0:
            status = "completed"
        elif skipped > 0:
            # 全部用例被跳过：明确标记为 skipped，不得显示为通过
            status = "skipped"
            error_message = "全部用例被跳过（缺前置条件/数据，或脚本内 skip）"
        else:
            # 没有任何用例结果（如脚本编译失败、0 个测试）
            status = "failed"
            error_message = (stderr.strip() or stdout.strip() or "未产生任何用例结果")[:2000]

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
                    skipped_tests=skipped,
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

            # 更新端点的 last_run_status
            try:
                from app.models.api_endpoint import APIEndpoint
                from sqlalchemy import select as sa_select
                result = await update_session.execute(
                    sa_select(APIEndpoint).where(APIEndpoint.api_test_ids.contains([str(api_test_id)]))
                )
                endpoints = result.scalars().all()
                for ep in endpoints:
                    ep.last_run_status = status
                    ep.total_test_runs = (ep.total_test_runs or 0)
                    ep.total_test_cases = total
            except Exception as e:
                print(f"[endpoint_executor] 更新端点状态失败: {e}")

            await update_session.commit()
            print(
                f"[endpoint_executor] run={run_id} 状态更新: {status} "
                f"(total={total} passed={passed} failed={failed} skipped={skipped} details={len(detail_rows)})"
            )
    except Exception as e:
        print(f"[endpoint_executor] run={run_id} 状态更新失败: {e}")


def _parse_playwright_json(report: dict) -> tuple[int, int, int, int, List[dict]]:
    """解析 Playwright JSON 报告，返回 (total, passed, failed, skipped, detail_rows)。"""
    import re

    total = passed = failed = skipped = 0
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
        nonlocal total, passed, failed, skipped
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
                        skipped += 1
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
    return total, passed, failed, skipped, rows
