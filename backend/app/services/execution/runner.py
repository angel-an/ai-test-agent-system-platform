"""
Playwright 异步运行器

使用 asyncio.create_subprocess_exec 执行 Playwright 测试，
避免阻塞事件循环。
"""
"""
andan
"""

# noqa  MC80OmFIVnBZMlhscm9ua3VMazZSbTFrUnc9PTo4MmJmMzg4Yg==

import asyncio
import json
import logging
import os
import re
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional

from app.services.execution.models import RunnerResult

logger = logging.getLogger(__name__)


def _get_npx_cmd() -> list[str]:
    """
    获取平台相关的 npx 命令。

    Windows 上 asyncio.create_subprocess_exec 不经过 shell，
    需要显式使用 npx.cmd 或在 PATH 中能找到 .cmd 文件。
    """
    if os.name == "nt":  # Windows
        return ["npx.cmd"]
    return ["npx"]


def _candidate_node_modules(workspace_dir: Path) -> list[Path]:
    """返回可复用的 node_modules 目录，兼容历史 workspace 路径。"""
    backend_root = Path(__file__).resolve().parents[3]
    candidates = [
        workspace_dir / "node_modules",
        backend_root / "workspace" / "api" / "node_modules",
    ]
    seen: set[Path] = set()
    result: list[Path] = []
    for candidate in candidates:
        resolved = candidate.resolve()
        if resolved.exists() and resolved not in seen:
            seen.add(resolved)
            result.append(resolved)
    return result


def _get_playwright_cmd(workspace_dir: Path) -> list[str]:
    """优先使用已安装的 Playwright CLI，避免 npx 临时环境找不到依赖。"""
    bin_name = "playwright.cmd" if os.name == "nt" else "playwright"
    for node_modules in _candidate_node_modules(workspace_dir):
        local_cmd = node_modules / ".bin" / bin_name
        if local_cmd.exists():
            return [str(local_cmd)]
    return [*_get_npx_cmd(), "playwright"]


def _ensure_node_in_path(env: dict[str, str]) -> dict[str, str]:
    """确保 PATH 包含常见的 Node.js 安装目录。"""
    node_paths = [
        r"C:\Program Files\nodejs",
        r"C:\Program Files (x86)\nodejs",
        os.path.expanduser(r"~\AppData\Roaming\npm"),
        "/usr/local/bin",
        "/usr/bin",
    ]
    current_path = env.get("PATH", "")
    # 只添加当前 PATH 中不存在的路径
    paths_to_add = [p for p in node_paths if p not in current_path]
    if paths_to_add:
        env = {**env, "PATH": os.pathsep.join(paths_to_add + [current_path])}
    return env


def _read_self_reflect_status(script_path: Path) -> str | None:
    """读取脚本目录的 self_reflect_result.json 自评状态（webwright 模式产物）。

    rev35（P0 归因）：脚本自评 failed（业务步骤未通过但进程退出码 0）时，
    HTTP 运行链（WebTestService → PlaywrightRunner）不得按退出码误判成功。
    """
    data = _read_self_reflect_data(script_path)
    if not data:
        return None
    status = data.get("execution_status")
    return status if isinstance(status, str) else None


def _read_self_reflect_data(script_path: Path) -> dict | None:
    """读取 self_reflect_result.json 完整内容（rev51：统计映射需要步骤数）。

    契约扩展（rev51）：脚本除 execution_status 外可写入结构化统计——
    {"execution_status": "passed", "total": 20, "passed": 20, "failed": 0, "skipped": 0}，
    供 PlaywrightRunner 映射到 web_test_runs 运行统计（total/passed/failed/skipped）。

    rev51-fix（真实 E2E 暴露）：脚本执行 cwd 为 workspace 根（run_with_job cwd），
    自评文件可能写在 workspace 根而非 script_path.parent（workspace/tests/）——
    两处候选目录都检查；且 tests/ 目录可能存在**历史残留**（旧 webwright 格式
    total_steps/passed_steps，无新契约字段），**优先选择含新契约统计字段的
    本次执行产物**，避免旧数据掩盖统计映射。
    """
    best: dict | None = None
    for cand in _self_reflect_candidates(script_path):
        sr = cand / "self_reflect_result.json"
        if not sr.exists():
            continue
        try:
            data = json.loads(sr.read_text(encoding="utf-8"))
        except Exception:
            continue
        if not isinstance(data, dict):
            continue
        if any(k in data for k in ("total", "passed", "failed")) and "total_steps" not in data:
            return data  # 新契约结构化统计优先
        if best is None:
            best = data
    return best


def _find_self_reflect_dir(script_path: Path) -> Path | None:
    """定位本次执行 self_reflect 产物所在目录（用于 report_path）。

    优先新契约统计格式所在目录；无则回退第一个存在 self_reflect 的候选目录。
    """
    first: Path | None = None
    for cand in _self_reflect_candidates(script_path):
        sr = cand / "self_reflect_result.json"
        if not sr.exists():
            continue
        try:
            data = json.loads(sr.read_text(encoding="utf-8"))
        except Exception:
            data = None
        if isinstance(data, dict) and any(k in data for k in ("total", "passed", "failed")) \
                and "total_steps" not in data:
            return cand
        if first is None:
            first = cand
    return first


def _self_reflect_candidates(script_path: Path) -> list[Path]:
    """self_reflect_result.json 候选目录：脚本同目录（tests/）与 workspace 根。"""
    return [script_path.parent, script_path.parent.parent]


class PlaywrightRunner:
    """Playwright 测试异步运行器"""

    def __init__(self, workspace_dir: Path):
        self.workspace_dir = workspace_dir.resolve()
        self.tests_dir = self.workspace_dir / "tests"
        self.tests_dir.mkdir(parents=True, exist_ok=True)

    async def run(
        self,
        script_path: Path,
        config: Optional[Dict[str, Any]] = None,
        timeout: int = 600,
    ) -> RunnerResult:
        """
        异步执行单个 Playwright 测试脚本。

        Args:
            script_path: 测试脚本路径（相对于 workspace_dir 或绝对路径）
            config: 执行配置 {base_url, reporter, env, ...}
            timeout: 超时时间（秒）

        Returns:
            RunnerResult
        """
        start_time = datetime.now(timezone.utc)
        config = config or {}
        proc: Optional[asyncio.subprocess.Process] = None

        # 环境变量：确保 PATH 包含 Node.js
        env = _ensure_node_in_path({**os.environ})
        npm_cache_dir = self.workspace_dir / ".npm-cache"
        npm_cache_dir.mkdir(parents=True, exist_ok=True)
        env["npm_config_cache"] = str(npm_cache_dir)
        env["NPM_CONFIG_CACHE"] = str(npm_cache_dir)
        node_modules_dirs = _candidate_node_modules(self.workspace_dir)
        if node_modules_dirs:
            existing_node_path = env.get("NODE_PATH", "")
            env["NODE_PATH"] = os.pathsep.join(
                [str(path) for path in node_modules_dirs]
                + ([existing_node_path] if existing_node_path else [])
            )
            existing_path = env.get("PATH", "")
            env["PATH"] = os.pathsep.join(
                [str(path / ".bin") for path in node_modules_dirs]
                + ([existing_path] if existing_path else [])
            )
        if config.get("base_url"):
            env["API_BASE_URL"] = config["base_url"]
        if "html" in str(config.get("reporter", "list")):
            env["CI"] = "1"
        env_vars = config.get("env") or config.get("environment_variables")
        if env_vars:
            env.update(env_vars)

        playwright_cmd = _get_playwright_cmd(self.workspace_dir)

        # 1. 验证 Playwright CLI 可用
        # rev32：Windows 上预检同样经 Job Object（run_with_job），
        # 避免 node_modules/.bin 中的本地 CLI（可写 workspace 来源）绕过 B3 资源限制；
        # POSIX 保持原 create_subprocess_exec 语义。
        try:
            if os.name == "nt":
                from app.agents.tools.web.process_guard import run_with_job

                precheck = await asyncio.to_thread(
                    run_with_job,
                    [*playwright_cmd, "--version"],
                    str(self.workspace_dir),
                    env,
                    10,
                    False,
                )
                rc = precheck.returncode
                err_text = (precheck.stderr or b"").decode("utf-8", errors="replace")
            else:
                playwright_proc = await asyncio.create_subprocess_exec(
                    *playwright_cmd, "--version",
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                    env=env,
                )
                stdout, stderr = await asyncio.wait_for(playwright_proc.communicate(), timeout=10)
                rc = playwright_proc.returncode
                err_text = stderr.decode("utf-8", errors="replace")
            if rc != 0:
                return RunnerResult(
                    success=False,
                    error_message=f"Playwright CLI 不可用: {err_text}",
                )
        except subprocess.TimeoutExpired:
            return RunnerResult(success=False, error_message="Playwright CLI 检查超时")
        except asyncio.TimeoutError:
            return RunnerResult(success=False, error_message="Playwright CLI 检查超时")
        except Exception as e:
            return RunnerResult(success=False, error_message=f"Playwright CLI 检查失败: {e}")
# pylint: disable  MS80OmFIVnBZMlhscm9ua3VMazZSbTFrUnc9PTo4MmJmMzg4Yg==

        # 2. 解析脚本路径
        if not script_path.is_absolute():
            script_path = self.workspace_dir / script_path

        if not script_path.exists():
            return RunnerResult(
                success=False,
                error_message=f"脚本文件不存在: {script_path}",
            )

        relative_path = script_path.relative_to(self.workspace_dir)

        # 3. 根据文件扩展名确定执行方式
        # rev53（报告目录隔离）：python 脚本在工作目录下执行会导致 self_reflect/
        # 截图等产物写入共享 workspace 根，报告打包 rglob('*') 把历史残留一并
        # 打包（实测 284MB）。python 分支改为**脚本所在目录**为执行 cwd
        # （HTTP 链脚本现已放置于每次 run 的专属子目录），产物天然隔离；
        # 非 python（Playwright TS）仍需 workspace 根（配置文件/依赖解析）。
        is_python_script = script_path.suffix == '.py'
        reporter = config.get("reporter", "list")
        exec_cwd = str(self.workspace_dir)
        if is_python_script:
            # Python 脚本：使用 python 命令直接执行（绝对路径 + 脚本目录 cwd）
            exec_cwd = str(script_path.parent)
            cmd = [
                sys.executable or "python",
                str(script_path),
            ]
            logger.info(
                "[PlaywrightRunner] 检测到 Python 脚本，使用 python 执行（cwd=脚本目录）: %s",
                script_path,
            )
        else:
            # TypeScript/JavaScript 脚本：使用 Playwright 执行
            cmd = [
                *playwright_cmd, "test",
                relative_path.as_posix(),
                f"--reporter={reporter}",
            ]

        # 为 HTML 报告指定独立的输出目录，避免批量执行时报告被覆盖
        # 注意：Python 脚本不支持 Playwright HTML reporter
        report_dir_name = None
        json_report_path = None
        if not is_python_script and "html" in reporter:
            report_dir_name = f"playwright-report-{int(time.time() * 1000)}-{os.urandom(4).hex()}"
            report_dir = self.workspace_dir / report_dir_name
            env["PLAYWRIGHT_HTML_OUTPUT_DIR"] = str(report_dir)
            json_report_path = self.workspace_dir / f"{report_dir_name}.json"
            env["PLAYWRIGHT_JSON_OUTPUT_NAME"] = str(json_report_path)
            if "json" not in reporter.split(","):
                reporter = f"{reporter},json"
                cmd[3] = f"--reporter={reporter}"
            cmd.extend(["--output", f"test-results-{report_dir_name}"])
            logger.info(
                "[PlaywrightRunner] 使用独立报告目录: %s", report_dir_name
            )

        logger.info(
            "[PlaywrightRunner] 执行命令: %s, 工作目录: %s",
            " ".join(cmd),
            exec_cwd,
        )

        # 4. 启动异步子进程
        # rev31（执行治理层 2b-B3）：Windows 上统一经 Job Object 资源限制
        # （run_with_job：内存/活动进程上限 + KILL_ON_JOB_CLOSE + 超时杀整棵进程树），
        # 覆盖 HTTP 执行面（web-tests run / api-tests run 等经 PlaywrightRunner 的路径）；
        # POSIX 保持原 create_subprocess_exec 语义。
        duration_ms = int(
            (datetime.now(timezone.utc) - start_time).total_seconds() * 1000
        )
        try:
            if os.name == "nt":
                from app.agents.tools.web.process_guard import run_with_job

                completed = await asyncio.to_thread(
                    run_with_job, cmd, exec_cwd, env, timeout, False
                )
                stdout = (completed.stdout or b"").decode("utf-8", errors="replace")
                stderr = (completed.stderr or b"").decode("utf-8", errors="replace")
                returncode = completed.returncode
            else:
                proc = await asyncio.create_subprocess_exec(
                    *cmd,
                    cwd=exec_cwd,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                    env=env,
                )
                # 5. 等待执行完成（带超时）
                stdout_bytes, stderr_bytes = await asyncio.wait_for(
                    proc.communicate(), timeout=timeout
                )
                stdout = stdout_bytes.decode("utf-8", errors="replace")
                stderr = stderr_bytes.decode("utf-8", errors="replace")
                returncode = proc.returncode
        except asyncio.TimeoutError:
            logger.warning("[PlaywrightRunner] 执行超时，正在终止子进程...")
            if proc.returncode is None:
                try:
                    proc.kill()
                    await asyncio.wait_for(proc.wait(), timeout=5)
                except Exception:
                    pass
            duration_ms = int(
                (datetime.now(timezone.utc) - start_time).total_seconds() * 1000
            )
            return RunnerResult(
                success=False,
                error_message=f"测试执行超时（超过 {timeout} 秒）",
                duration_ms=duration_ms,
            )
        except subprocess.TimeoutExpired:
            # run_with_job 超时：TerminateJobObject 已杀整棵进程树
            logger.warning("[PlaywrightRunner] 执行超时（Job Object 已终止进程树）")
            duration_ms = int(
                (datetime.now(timezone.utc) - start_time).total_seconds() * 1000
            )
            return RunnerResult(
                success=False,
                error_message=f"测试执行超时（超过 {timeout} 秒）",
                duration_ms=duration_ms,
            )
        except Exception as e:
            duration_ms = int(
                (datetime.now(timezone.utc) - start_time).total_seconds() * 1000
            )
            return RunnerResult(
                success=False,
                error_message=f"启动子进程失败: {e}",
                duration_ms=duration_ms,
            )
# fmt: off  Mi80OmFIVnBZMlhscm9ua3VMazZSbTFrUnc9PTo4MmJmMzg4Yg==

        # 检查报告（支持 "list,html" 等多 reporter 组合）
        # 注意：Python 脚本不支持 Playwright HTML reporter，跳过 HTML 报告检查；
        # rev37（P1-2）：Python/webwright 脚本产物目录（含 self_reflect_result.json）
        # 仍设置 report_path，使 HTTP 报告链（index.html / ZIP / 附件）可达。
        report_path = None
        if not is_python_script and "html" in reporter:
            # 使用命令中指定的报告目录名称
            if report_dir_name:
                report_dir = self.workspace_dir / report_dir_name
                if (report_dir / "index.html").exists():
                    report_path = str(report_dir)
            # 兼容：检查默认目录
            if not report_path:
                default_dir = self.workspace_dir / "playwright-report"
                if (default_dir / "index.html").exists():
                    report_path = str(default_dir)
        elif is_python_script:
            # Python 脚本：产物目录 = 脚本所在目录或 workspace 根（rev51-fix：
            # 脚本 cwd 为 workspace 根，自评/截图可能落在任一候选目录）；
            # 仅当含 self_reflect_result.json（webwright 自评产物）时启用报告链；
            # 优先本次执行（新契约统计）所在目录，避免历史残留目录。
            sr_dir = _find_self_reflect_dir(script_path)
            if sr_dir is not None:
                report_path = str(sr_dir)
                logger.info(
                    "[PlaywrightRunner] rev37/51：Python 脚本产物目录设为报告路径: %s",
                    report_path,
                )

        # JSON reporter 保存测试级结果和断言步骤；无法生成时回退到 list 输出。
        result_summary = self._parse_playwright_json_report(json_report_path)
        if json_report_path:
            try:
                json_report_path.unlink(missing_ok=True)
            except OSError:
                logger.warning("[PlaywrightRunner] 无法清理 JSON 报告: %s", json_report_path)
        if not result_summary:
            result_summary = self._parse_summary(stdout)

        success = returncode == 0
        # rev50（真实环境暴露）：error_message 可能在整个作用域从未赋值——
        # rc≠0 且无 self_reflect 的失败路径进入 else 分支时 `error_message or ...`
        # 会触发 UnboundLocalError，掩盖真实失败原因。先显式初始化。
        error_message: str | None = None
        # rev35（P0 归因）+ rev51（统计映射）：Python 脚本（webwright 模式）执行后
        # 读取 self_reflect_result.json——
        #  - 自评 failed（业务步骤未通过但进程退出码 0）→ 不得判定成功；
        #  - 自评可携带结构化统计（total/passed/failed/skipped）→ 合并进
        #    result_summary，使 web_test_runs 运行统计与脚本步骤数一致（如 20/20）。
        _sr_data = None
        if is_python_script:
            _sr_data = _read_self_reflect_data(script_path)
            _sr_status = _sr_data.get("execution_status") if _sr_data else None
            if _sr_status == "failed" and success:
                success = False
                error_message = (
                    "脚本自评 failed（业务步骤未全部通过，进程退出码为 0），"
                    "详见报告 self_reflect_result.json"
                )
                logger.warning(
                    "[PlaywrightRunner] rev35：脚本自评 failed，判定为失败（退出码 0 不视为通过）"
                )
            if _sr_data:
                _sr_stats = {k: int(v) for k, v in _sr_data.items()
                             if k in ("total", "passed", "failed", "skipped")
                             and isinstance(v, (int, float))}
                if _sr_stats.get("total") or _sr_stats.get("passed") or _sr_stats.get("failed"):
                    result_summary = {**result_summary, **_sr_stats}
                    logger.info(
                        "[PlaywrightRunner] rev51：self_reflect 统计映射到运行统计: %s", _sr_stats
                    )
        if success:
            error_message = None
        else:
            # Playwright 错误通常在 stdout 中，尝试提取第一个失败的测试错误
            error_message = error_message or (self._extract_error(stdout)
                                              or stderr[:2000] or stdout[:2000]
                                              or "Playwright 测试执行失败（无详细错误信息）")

        logger.info(
            "[PlaywrightRunner] 执行完成, returncode=%s, duration_ms=%s, "
            "passed=%s, failed=%s",
            returncode,
            duration_ms,
            result_summary.get("passed", 0),
            result_summary.get("failed", 0),
        )

        return RunnerResult(
            success=success,
            stdout=stdout,
            stderr=stderr,
            returncode=returncode,
            report_path=report_path,
            duration_ms=duration_ms,
            error_message=error_message,
            result_summary=result_summary,
        )

    def _parse_summary(self, stdout: str) -> Dict[str, int]:
        """从 Playwright list reporter 输出解析测试统计。"""
        result = {"total": 0, "passed": 0, "failed": 0, "skipped": 0}

        # 匹配形如 "6 passed (2.5s)" 或 "2 failed, 4 passed"
        summary_match = re.search(
            r"(\d+)\s+passed",
            stdout,
        )
        if summary_match:
            result["passed"] = int(summary_match.group(1))

        failed_match = re.search(r"(\d+)\s+failed", stdout)
        if failed_match:
            result["failed"] = int(failed_match.group(1))

        skipped_match = re.search(r"(\d+)\s+skipped", stdout)
        if skipped_match:
            result["skipped"] = int(skipped_match.group(1))
# type: ignore  My80OmFIVnBZMlhscm9ua3VMazZSbTFrUnc9PTo4MmJmMzg4Yg==

        total_match = re.search(r"Total:\s+(\d+)\s+test", stdout)
        if total_match:
            result["total"] = int(total_match.group(1))
        else:
            result["total"] = result["passed"] + result["failed"] + result["skipped"]

        return result

    @staticmethod
    def _parse_playwright_json_report(report_path: Path | None) -> Dict[str, Any]:
        """Extract a compact, report-safe view from Playwright's JSON reporter."""
        if not report_path or not report_path.exists():
            return {}
        try:
            report = json.loads(report_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            logger.warning("[PlaywrightRunner] JSON 报告解析失败: %s", exc)
            return {}

        tests: list[dict[str, Any]] = []

        def error_text(error: Any) -> str:
            if isinstance(error, dict):
                return str(error.get("message") or error.get("value") or "测试失败")
            return str(error)

        def collect(suite: Any) -> None:
            if not isinstance(suite, dict):
                return
            for spec in suite.get("specs") or []:
                if not isinstance(spec, dict):
                    continue
                for test in spec.get("tests") or []:
                    if not isinstance(test, dict):
                        continue
                    result = (test.get("results") or [{}])[0] or {}
                    steps = []
                    for step in result.get("steps") or []:
                        if not isinstance(step, dict):
                            continue
                        failed = bool(step.get("error"))
                        steps.append({
                            "title": str(step.get("title") or "未命名步骤"),
                            "status": "failed" if failed else "passed",
                            "duration_ms": int(step.get("duration") or 0),
                            "error": error_text(step["error"]) if failed else None,
                        })
                    tests.append({
                        "title": str(spec.get("title") or "未命名测试"),
                        "status": str(result.get("status") or "skipped"),
                        "duration_ms": int(result.get("duration") or 0),
                        "errors": [error_text(item) for item in result.get("errors") or []],
                        "steps": steps,
                    })
            for child in suite.get("suites") or []:
                collect(child)

        for suite in report.get("suites") or []:
            collect(suite)

        if not tests:
            return {}
        result = {"total": len(tests), "passed": 0, "failed": 0, "skipped": 0, "tests": tests}
        for test in tests:
            if test["status"] == "passed":
                result["passed"] += 1
            elif test["status"] == "failed":
                result["failed"] += 1
            else:
                result["skipped"] += 1
        return result

    def _extract_error(self, stdout: str) -> Optional[str]:
        """从 Playwright 输出中提取第一个失败的测试错误信息。"""
        error_line = re.search(
            r"^\s*(Error|TimeoutError|AssertionError|Test timeout)[^\n]{0,300}",
            stdout,
            re.MULTILINE | re.IGNORECASE,
        )
        if error_line:
            return error_line.group(0).strip()

        # Playwright 失败格式示例:
        #   1) [webkit] › file.spec.ts:23:7 › 描述 ─────
        #     Error: expect(...).toBeVisible() 调用超时
        #
        # 尝试匹配失败测试的错误块
        failed_test_pattern = re.search(
            r"^\s*\d+\)\s+.*?─+\s*\n(.*?)\n\s*(?:\d+\)|Error|Test timeout|TimeoutError|AssertionError)",
            stdout,
            re.MULTILINE | re.DOTALL,
        )
        if failed_test_pattern:
            error_block = failed_test_pattern.group(1).strip()
            # 限制长度
            if len(error_block) > 500:
                error_block = error_block[:500] + "..."
            return error_block

        return None
