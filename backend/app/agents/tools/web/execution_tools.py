"""
Web 测试脚本执行工具

提供在 测试目录中执行 Playwright 脚本的功能
"""
"""
andan
"""


import os
import sys
import json
import time
import asyncio
import subprocess
import tempfile
import zipfile
import shutil
from pathlib import Path
from typing import Optional, Dict, Any
from datetime import datetime, timezone
from uuid import UUID

from langchain_core.tools import tool
from langchain_core.runnables import RunnableConfig
from sqlalchemy import select

from app.config import settings
from app.config.database import async_session_factory
from app.models.attachment import Attachment, AttachmentEntityType
from app.models.web_function import WebSubFunction
from app.config.minio_client import MinIOClient


# ============================================================================
# Python 解释器路径解析
# ============================================================================
# 优先使用当前运行后端服务的 Python 解释器（sys.executable），
# 因为它一定存在且可用。回退到系统 PATH 中的 python/python3/py 命令。
# 这解决了虚拟环境中子进程找不到 Python 的问题。

def _get_python_candidates() -> list[str]:
    """获取候选 Python 解释器路径列表，按优先级排序。"""
    candidates = []

    # 0. 最高优先级：环境变量指定的 Python 路径（允许用户强制覆盖）
    env_python = os.environ.get("WEB_TEST_PYTHON_PATH")
    if env_python and Path(env_python).exists():
        candidates.append(env_python)
        print(f"[Web Script Execution] 使用环境变量指定的 Python: {env_python}")

    # 1. 优先使用当前运行的 Python 解释器（最可靠，一定存在）
    if sys.executable and Path(sys.executable).exists():
        candidates.append(sys.executable)
        # 也添加不带 .exe 的版本（Windows 兼容）
        if sys.executable.endswith(".exe"):
            candidates.append(sys.executable[:-4])

    # 2. 系统 PATH 中的命令
    is_windows = sys.platform == "win32"
    if is_windows:
        candidates.extend(["python", "py", "python3"])
    else:
        candidates.extend(["python3", "python"])

    return candidates


def _find_working_python() -> Optional[str]:
    """找到一个可用的 Python 解释器路径。"""
    candidates = _get_python_candidates()
    print(f"[Web Script Execution] 开始预检测 Python 解释器，候选列表: {candidates}")

    for candidate in candidates:
        try:
            # 对于绝对路径（如 sys.executable），直接检查文件是否存在
            candidate_path = Path(candidate)
            if candidate_path.is_absolute() and not candidate_path.exists():
                print(f"[Web Script Execution] 候选 Python 不存在: {candidate}")
                continue

            result = subprocess.run(
                [candidate, "--version"],
                capture_output=True,
                timeout=5,
                creationflags=0x00000200 if sys.platform == "win32" else 0
            )
            if result.returncode == 0:
                version = result.stdout.decode('utf-8', errors='replace').strip() or result.stderr.decode('utf-8', errors='replace').strip()
                print(f"[Web Script Execution] 找到可用 Python: {candidate} -> {version}")
                return candidate
            else:
                stderr = result.stderr.decode('utf-8', errors='replace').strip()
                print(f"[Web Script Execution] 候选 Python 返回非零: {candidate}, stderr: {stderr}")
        except FileNotFoundError:
            print(f"[Web Script Execution] 候选 Python 未找到: {candidate}")
        except Exception as e:
            print(f"[Web Script Execution] 候选 Python 检测失败: {candidate}, 错误: {type(e).__name__}: {e}")
    return None


# 惰性检测：第一次使用时才检测 Python 解释器路径
# 原因：模块导入时 .env 可能尚未加载，延迟检测确保能读到 WEB_TEST_PYTHON_PATH
_PYTHON_CMD: Optional[str] = None
_PYTHON_CMD_INITIALIZED = False


def _get_python_cmd() -> Optional[str]:
    """获取可用的 Python 解释器路径（惰性初始化）。"""
    global _PYTHON_CMD, _PYTHON_CMD_INITIALIZED
    if not _PYTHON_CMD_INITIALIZED:
        _PYTHON_CMD = _find_working_python()
        _PYTHON_CMD_INITIALIZED = True
        if _PYTHON_CMD:
            print(f"[Web Script Execution] 预检测 Python 解释器: {_PYTHON_CMD}")
        else:
            print("[Web Script Execution] ⚠️ 警告: 未找到可用的 Python 解释器，执行 Python 脚本可能失败")
    return _PYTHON_CMD


# ============================================================================
# 修复循环硬性上限(P0-2) — 优化版：按任务窗口隔离，避免历史失败累积
# ============================================================================
# 每个 thread 的连续失败次数,达到上限后 execute_web_script 直接拒绝执行,
# 防止 LLM 在失败任务上无限打转累加 subprocess 阻塞时长。
#
# 关键改进：
# - 使用 (thread_id, sub_function_id) 作为键，不同子功能互不影响
# - 增加时间窗口（30分钟），超过窗口自动重置计数
# - 成功执行后清零对应计数
# - 【P0-4】区分"可修复错误"与"环境错误"，只对脚本逻辑错误计数
#
# 错误分类：
# - SCRIPT_ERROR (可修复): 断言失败、选择器错误、逻辑错误等 → 计入失败计数
# - ENV_ERROR (不可修复): 超时、进程被杀、网络断开、浏览器崩溃等 → 不计入
# - INFRA_ERROR (不可修复): MinIO失败、数据库错误、磁盘满等 → 不计入
_thread_failure_counts: Dict[str, Dict[str, Any]] = {}
_MAX_HEAL_ATTEMPTS = int(os.getenv("MAX_HEAL_ATTEMPTS", "3"))
_FAILURE_WINDOW_SECONDS = int(os.getenv("FAILURE_WINDOW_SECONDS", "1800"))  # 30分钟窗口

# 环境错误关键词（匹配错误信息，不计入失败计数）
_ENV_ERROR_PATTERNS = [
    "timeout", "timed_out", "超时",
    "connection", "refused", "连接",
    "network", "网络",
    "browser", "chrome", "chromium", "浏览器",
    "process", "killed", "进程",
    "memory", "内存",
    "disk", "空间",
    "permission", "权限",
    "not found: npx", "not found: playwright", "command not found",
    "module not found", "cannot find module",
    "no such file", "不存在",
    "econnrefused", "enotfound", "etimedout",
    "spawn", "execa",
]

# 脚本可修复错误关键词（匹配错误信息，计入失败计数）
_SCRIPT_ERROR_PATTERNS = [
    "assertion", "断言",
    "expect", "expected", "to be", "to have",
    "locator", "selector", "element", "选择器",
    "click", "fill", "type", "scroll",
    "visible", "hidden", "enabled", "disabled",
    "timeout.*locator", "waiting.*locator",
    "navigation", "goto", "navigate",
    "page.evaluate",
]


def _classify_error(error_msg: str, stdout: str = "", stderr: str = "", return_code: int = -1) -> str:
    """分类错误类型，决定是否应该计入失败计数。

    Returns:
        "SCRIPT_ERROR" - 脚本逻辑错误，AI可以修复，计入计数
        "ENV_ERROR" - 环境/基础设施错误，AI无法修复，不计入计数
    """
    if not error_msg:
        error_msg = f"{stdout}\n{stderr}"
    error_lower = error_msg.lower()

    # 1. 检查是否为超时（最常见的环境问题）
    if return_code == -1 or "timed_out" in error_lower or "timeout" in error_lower:
        return "ENV_ERROR"

    # 2. 检查环境错误关键词
    for pattern in _ENV_ERROR_PATTERNS:
        if pattern in error_lower:
            return "ENV_ERROR"

    # 3. 检查脚本错误关键词（需要更精确匹配）
    for pattern in _SCRIPT_ERROR_PATTERNS:
        if pattern in error_lower:
            return "SCRIPT_ERROR"

    # 4. 默认：如果返回码是 1 且包含测试失败信息，视为脚本错误
    if return_code == 1:
        # Playwright 测试失败通常返回 1
        if "passed" in error_lower or "failed" in error_lower or "test" in error_lower:
            return "SCRIPT_ERROR"

    # 5. 其他未知错误，保守处理为环境错误（避免误杀）
    return "ENV_ERROR"


async def _pre_check_environment(workspace_mode: str = "auto", framework: str = "playwright") -> Dict[str, Any]:
    """执行前环境预检，提前发现环境问题。

    检查项：
    1. workspace 目录是否存在且可写
    2. tests 目录是否存在
    3. Playwright/Node 是否可用（playwright 框架）
    4. Python 是否可用（python 框架）
    5. 磁盘空间是否充足

    Returns:
        {"ok": True} 或 {"ok": False, "error": "...", "error_type": "ENV_ERROR"}
    """
    import shutil
    import asyncio

    try:
        project_root = _get_workspace_root(workspace_mode)

        # 1. 检查 workspace 目录
        if not project_root.exists():
            return {
                "ok": False,
                "error": f"Workspace 目录不存在: {project_root}",
                "error_type": "ENV_ERROR",
                "suggestion": "请检查 workspace 配置或联系管理员初始化环境"
            }

        # 2. 检查 tests 目录
        tests_dir = project_root / "tests"
        if not tests_dir.exists():
            try:
                tests_dir.mkdir(parents=True, exist_ok=True)
            except Exception as e:
                return {
                    "ok": False,
                    "error": f"无法创建 tests 目录: {e}",
                    "error_type": "ENV_ERROR",
                    "suggestion": "请检查目录权限"
                }

        # 3. 检查磁盘空间（需要至少 100MB）
        try:
            disk_usage = shutil.disk_usage(project_root)
            free_mb = disk_usage.free / (1024 * 1024)
            if free_mb < 100:
                return {
                    "ok": False,
                    "error": f"磁盘空间不足: 剩余 {free_mb:.1f}MB (需要至少 100MB)",
                    "error_type": "ENV_ERROR",
                    "suggestion": "请清理磁盘空间后重试"
                }
        except Exception:
            pass  # 某些系统可能不支持 disk_usage

        # 4. 检查运行时可用性
        is_windows = sys.platform == "win32"

        if framework == "playwright":
            # 检查 npx 和 playwright 是否可用
            check_cmd = "npx playwright --version"
            try:
                # FIX-2026-08-18: Windows SelectorEventLoop 下 asyncio.create_subprocess_exec
                # 不支持 shell=True。统一使用 run_in_executor + subprocess.run 在后台线程
                # 中执行同步子进程，完全避开该限制。
                import concurrent.futures
                loop = asyncio.get_running_loop()

                def _check_playwright():
                    return subprocess.run(
                        check_cmd,
                        capture_output=True,
                        timeout=10,
                        shell=True,
                        cwd=str(project_root),
                        creationflags=0x00000200 if sys.platform == "win32" else 0
                    )

                with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
                    proc = await loop.run_in_executor(pool, _check_playwright)

                if proc.returncode != 0:
                    error_msg = proc.stderr.decode("utf-8", errors="replace")[:200] if proc.stderr else "未知错误"
                    return {
                        "ok": False,
                        "error": f"Playwright 不可用: {error_msg}",
                        "error_type": "ENV_ERROR",
                        "suggestion": "请运行 'npm install -D @playwright/test' 安装依赖"
                    }
            except concurrent.futures.TimeoutError:
                return {
                    "ok": False,
                    "error": "检查 Playwright 环境超时",
                    "error_type": "ENV_ERROR",
                    "suggestion": "环境响应缓慢，请检查系统负载"
                }
            except Exception as e:
                return {
                    "ok": False,
                    "error": f"检查 Playwright 环境失败: {e}",
                    "error_type": "ENV_ERROR",
                    "suggestion": "请检查 Node.js 和 npm 是否正确安装"
                }

        elif framework == "python":
            # 检查 Python 是否可用（使用与 _get_python_cmd 相同的逻辑）
            # P0-4-FIX: 统一使用 _get_python_candidates + _find_working_python 的逻辑
            # 确保预检和实际执行使用一致的 Python 检测方式
            python_cmd = _get_python_cmd()
            if python_cmd:
                try:
                    # FIX-2026-08-18: Windows SelectorEventLoop 下 asyncio.create_subprocess_exec
                    # 不支持 shell=True，且 create_subprocess_shell 可能触发 [Errno 22]。
                    # 预检只是一个简单的 --version 检查，使用 run_in_executor + subprocess.run
                    # 在后台线程中执行同步子进程，避免阻塞事件循环。
                    import concurrent.futures
                    loop = asyncio.get_running_loop()

                    def _check_python():
                        return subprocess.run(
                            [python_cmd, "--version"],
                            capture_output=True,
                            timeout=10,
                            creationflags=0x00000200 if sys.platform == "win32" else 0
                        )

                    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
                        proc = await loop.run_in_executor(pool, _check_python)

                    if proc.returncode == 0:
                        python_version_output = proc.stdout.decode('utf-8', errors='replace').strip() or proc.stderr.decode('utf-8', errors='replace').strip()
                        print(f"[Web Script Execution] Python 环境检查通过: {python_cmd} -> {python_version_output}")
                    else:
                        return {
                            "ok": False,
                            "error": f"Python 预检测解释器 {python_cmd} 返回非零退出码",
                            "error_type": "ENV_ERROR",
                            "suggestion": "请检查 Python 环境是否完整"
                        }
                except Exception as e:
                    return {
                        "ok": False,
                        "error": f"Python 预检测解释器 {python_cmd} 执行失败: {e}",
                        "error_type": "ENV_ERROR",
                        "suggestion": "请检查 Python 环境"
                    }
            else:
                # _get_python_cmd() 返回 None，说明没有可用的 Python
                # 此时已经尝试了：WEB_TEST_PYTHON_PATH、sys.executable、PATH 中的 python/python3/py
                return {
                    "ok": False,
                    "error": f"Python 不可用: 尝试了所有候选路径均失败。sys.executable={sys.executable}",
                    "error_type": "ENV_ERROR",
                    "suggestion": "请设置 WEB_TEST_PYTHON_PATH 环境变量指向可用的 Python 解释器，或将 Python 添加到系统 PATH"
                }

        return {"ok": True}

    except Exception as e:
        return {
            "ok": False,
            "error": f"环境预检失败: {e}",
            "error_type": "ENV_ERROR",
            "suggestion": "请检查环境配置"
        }


def _get_failure_key(thread_id: str, sub_function_id: Optional[str]) -> str:
    """生成失败计数键，按 (thread_id, sub_function_id) 隔离"""
    sf = sub_function_id or "default"
    return f"{thread_id}:{sf}"


def _get_fail_count(key: str) -> int:
    """获取失败计数，自动检查时间窗口"""
    entry = _thread_failure_counts.get(key)
    if entry is None:
        return 0
    # 检查是否超过时间窗口
    last_time = entry.get("last_time", 0)
    if time.time() - last_time > _FAILURE_WINDOW_SECONDS:
        # 超过窗口，重置计数
        _thread_failure_counts.pop(key, None)
        return 0
    return entry.get("count", 0)


def _increment_fail_count(key: str, error_type: str = "SCRIPT_ERROR") -> None:
    """增加失败计数

    Args:
        error_type: "SCRIPT_ERROR" 或 "ENV_ERROR"，只有 SCRIPT_ERROR 才计数
    """
    if error_type == "ENV_ERROR":
        # 环境错误不计入，但记录日志
        print(f"[Web Script Execution] 环境错误，不计入失败计数: {key}")
        return

    entry = _thread_failure_counts.get(key)
    if entry is None:
        _thread_failure_counts[key] = {"count": 1, "last_time": time.time()}
    else:
        entry["count"] = entry.get("count", 0) + 1
        entry["last_time"] = time.time()
    print(f"[Web Script Execution] 脚本错误，失败计数+1: {key}, count={_thread_failure_counts[key]['count']}")


def _clear_fail_count(key: str) -> None:
    """清除失败计数"""
    _thread_failure_counts.pop(key, None)


async def _update_sub_function_stats(
    sub_function_id: str,
    execution_result: Dict[str, Any],
) -> bool:
    """更新子功能的测试运行次数和最后状态。

    使用独立的 session 进行更新，带重试机制确保数据一致性。
    修复要点：
    - 使用 SELECT FOR UPDATE 防止并发更新冲突
    - 每次重试使用独立 session，确保资源释放
    - 捕获 UUID 解析错误，防止非法 ID 导致崩溃
    - 区分可重试错误（DB 冲突）和不可重试错误（非法参数）

    Args:
        sub_function_id: 子功能 ID
        execution_result: 执行结果字典

    Returns:
        是否成功更新
    """
    from sqlalchemy.exc import SQLAlchemyError, OperationalError

    # P0-5: 前置校验 sub_function_id 格式，避免无效 UUID 导致不必要的重试
    try:
        sf_uuid = UUID(sub_function_id)
    except (ValueError, TypeError) as e:
        print(f"[Web Script Execution] 子功能 ID 格式非法 '{sub_function_id}': {e}")
        return False

    max_retries = 3
    for attempt in range(1, max_retries + 1):
        db = None
        try:
            async with async_session_factory() as db:
                # 使用 with_for_update 防止并发更新冲突（行级锁）
                sub_function_result = await db.execute(
                    select(WebSubFunction)
                    .where(WebSubFunction.id == sf_uuid)
                    .with_for_update()
                )
                sub_function = sub_function_result.scalar_one_or_none()

                if not sub_function:
                    print(f"[Web Script Execution] 子功能 {sub_function_id} 不存在，无法更新统计")
                    return False

                old_count = sub_function.total_test_runs or 0
                new_status = "passed" if execution_result.get("success") else "failed"

                sub_function.total_test_runs = old_count + 1
                sub_function.last_run_status = new_status

                await db.commit()
                print(
                    f"[Web Script Execution] 已更新子功能 {sub_function_id} 的统计: "
                    f"total_test_runs={old_count}->{sub_function.total_test_runs}, "
                    f"last_run_status={sub_function.last_run_status}"
                )
                return True

        except OperationalError as e:
            # 数据库级错误（如死锁、连接断开），可重试
            print(f"[Web Script Execution] 更新子功能 {sub_function_id} 遇到数据库错误 (尝试 {attempt}/{max_retries}): {e}")
            if attempt < max_retries:
                wait_time = 0.2 * attempt  # 递增延迟：0.2s, 0.4s, 0.6s
                print(f"[Web Script Execution] 等待 {wait_time}s 后重试...")
                await asyncio.sleep(wait_time)
            else:
                print(f"[Web Script Execution] 更新子功能 {sub_function_id} 统计最终失败，已重试 {max_retries} 次")
                return False
        except SQLAlchemyError as e:
            # 其他 SQLAlchemy 错误，通常不可重试
            print(f"[Web Script Execution] 更新子功能 {sub_function_id} 统计失败（SQLAlchemy 错误，不重试）: {e}")
            return False
        except Exception as e:
            print(f"[Web Script Execution] 更新子功能 {sub_function_id} 统计时发生未知错误: {e}")
            return False
        finally:
            # 确保 session 在异常时被正确关闭（async_session_factory 的 async with 已处理，
            # 但显式标记有助于调试）
            if db is not None:
                try:
                    await db.close()
                except Exception:
                    pass

    return False


# ============================================================================
# 测试目录配置
# ============================================================================

def _get_workspace_root(workspace_mode: str = "auto") -> Path:
    """获取当前使用的 workspace 根目录

    Args:
        workspace_mode: 指定 workspace 模式
            - "auto": 按优先级检测 web_cli -> web_mcp -> webwright
            - "web_cli": 强制使用 web_cli workspace
            - "web_mcp": 强制使用 web_mcp workspace
            - "webwright": 强制使用 webwright workspace

    Returns:
        对应模式的 workspace 根目录路径
    """
    mode_map = {
        "web_cli": settings.web_cli_workspace_root,
        "web_mcp": settings.web_mcp_workspace_root,
        "webwright": settings.webwright_workspace_root,
    }

    # 如果指定了具体模式，直接返回对应路径
    if workspace_mode in mode_map:
        return Path(mode_map[workspace_mode])

    # auto 模式：按优先级检测存在的目录
    for root_attr in ['web_cli_workspace_root', 'web_mcp_workspace_root', 'webwright_workspace_root']:
        root_path = Path(getattr(settings, root_attr, ''))
        if root_path.exists():
            return root_path

    # 默认返回 web_cli（保持向后兼容）
    return Path(settings.web_cli_workspace_root)


def get_workspace_tests_dir(workspace_mode: str = "auto") -> Path:
    """
    获取 WORKSPACE 测试目录路径

    Args:
        workspace_mode: 指定 workspace 模式

    Returns:
        WORKSPACE 测试目录的绝对路径
    """
    return _get_workspace_root(workspace_mode) / "tests"


def get_project_root(workspace_mode: str = "auto") -> Path:
    """
    获取项目根目录（用于在 测试目录中找到 package.json）

    Args:
        workspace_mode: 指定 workspace 模式

    Returns:
        项目根目录的绝对路径
    """
    return _get_workspace_root(workspace_mode)


def _apply_self_reflect_status(execution_result: dict, workspace_mode: str) -> None:
    """rev34/rev35（P0 归因）：webwright python 模式读取脚本自评并覆盖执行结果。

    脚本自评（self_reflect_result.json）为 failed（业务步骤未通过但进程退出码 0）
    时，将 execution_result["success"] 置 False——直接调用链（execute_web_script）
    与 HTTP 链（PlaywrightRunner）都据此避免误报通过。

    Args:
        execution_result: _execute_script_internal 返回的执行结果 dict（原地修改）
        workspace_mode: webwright / web_cli / web_mcp
    """
    if workspace_mode != "webwright":
        return
    report_path = execution_result.get("report_path")
    if not report_path:
        return
    sr_path = Path(str(report_path)) / "self_reflect_result.json"
    if not sr_path.exists():
        return
    try:
        sr = json.loads(sr_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        print(f"[Web Script Execution] 读取 self_reflect_result.json 失败: {e}")
        return
    status = (sr or {}).get("execution_status")
    if status == "failed":
        execution_result["success"] = False
        execution_result["self_reflect_status"] = "failed"
        execution_result["error_message"] = (
            "脚本自评 failed（业务步骤未全部通过，进程退出码为 0），"
            "详见报告 self_reflect_result.json"
        )
        print("[Web Script Execution] rev34/35：脚本自评 failed，执行判定为失败（不再归因为通过）")
    else:
        execution_result["self_reflect_status"] = status or "unknown"
        print(f"[Web Script Execution] rev34/35：脚本自评 execution_status={status}")


@tool
async def execute_web_script(
    local_script_path: str,
    framework: str = "auto",
    reporter: str = "html",
    project_identifier: str = "PR-1",
    sub_function_id: Optional[str] = None,
    sub_function_ids: Optional[str] = None,
    workspace_mode: str = "auto",
    config: Optional[RunnableConfig] = None,
) -> str:
    """
    执行 Web 测试脚本（支持 Playwright .spec.ts 和 Python/Playwright .py 脚本）

    此工具会根据脚本文件扩展名自动检测测试框架：
    - .py → python（Webwright 模式，直接执行 Python 脚本）
    - .spec.ts / .test.ts → playwright（Playwright CLI 模式）

    也可以显式指定 framework 参数覆盖自动检测。

    执行流程：
    1. 验证脚本文件存在于 workspace 目录
    2. 自动检测或应用指定的测试框架
    3. 执行测试（Playwright CLI 或 Python 直接运行）
    4. 生成测试报告（HTML/JSON）
    5. 将测试报告保存到 MinIO
    6. 在数据库中创建测试报告附件记录
    7. 更新子功能的测试运行次数
    8. 清理临时报告文件

    Args:
        local_script_path: 本地脚本文件的完整路径（相对或绝对路径）。
            Webwright 模式使用 /webwright/final_runs/run_001/final_script.py
            web_cli/web_mcp 模式使用 tests/login_test.spec.ts 或相对路径
        framework: 测试框架 (auto, playwright, python)。默认 auto 根据文件扩展名自动检测
        reporter: 报告格式 (html, json, list)
        project_identifier: 项目标识符，用于保存测试报告
        sub_function_id: 子功能 ID（可选，用于更新测试统计）
        sub_function_ids: 多个子功能 ID，逗号分隔（可选，用于合并脚本为多个子功能保存报告）
        workspace_mode: 指定 workspace 模式 (auto, web_cli, web_mcp, webwright)。
            当使用 webwright 模式时，必须设置为 "webwright"
            当使用 web_cli 模式时，必须设置为 "web_cli"
            当使用 web_mcp 模式时，必须设置为 "web_mcp"
            如果不确定，使用 "auto" 会根据脚本路径自动推断

    Returns:
        JSON 格式的执行结果，包含：
        - success: 是否成功
        - script_path: 执行的脚本路径
        - detected_framework: 自动检测到的框架（当 framework="auto" 时）
        - execution_result: 执行结果（stdout, stderr, duration, return_code）
        - report_attachment_id: 测试报告附件 ID（如果生成了报告）
        - error: 错误信息（如果有）

    Example:
        >>> # Webwright 模式（Python 脚本）- 必须指定 workspace_mode="webwright"
        >>> result = await execute_web_script(
        ...     local_script_path="/webwright/final_runs/run_001/final_script.py",
        ...     framework="auto",
        ...     project_identifier="PR-3",
        ...     workspace_mode="webwright"
        ... )
        >>> # web_cli 模式（TypeScript 脚本）- 必须指定 workspace_mode="web_cli"
        >>> result = await execute_web_script(
        ...     local_script_path="tests/login_test.spec.ts",
        ...     framework="auto",
        ...     reporter="html",
        ...     project_identifier="PR-3",
        ...     sub_function_id="5ea81a5f-c97b-4a36-a680-13637f1b9eed",
        ...     workspace_mode="web_cli"
        ... )
        >>> # Playwright CLI 模式（TypeScript 脚本）- 旧示例，不推荐使用
        >>> result = await execute_web_script(
        ...     local_script_path="tests/login_test.spec.ts",
        ...     framework="auto",
        ...     reporter="html",
        ...     project_identifier="PR-3",
        ...     sub_function_id="5ea81a5f-c97b-4a36-a680-13637f1b9eed"
        ... )
    """
    try:
        # === P0-2: 修复循环硬性上限检查（优化版：按任务窗口隔离）===
        thread_id = "default"
        if config:
            thread_id = (config.get("configurable") or {}).get("thread_id", "default")

        # 使用 (thread_id, sub_function_id) 作为键，不同子功能互不影响
        failure_key = _get_failure_key(thread_id, sub_function_id)
        _fail_count = _get_fail_count(failure_key)

        if _fail_count >= _MAX_HEAL_ATTEMPTS:
            # 达到上限前也应把本次失败计入子功能统计,否则前端看不到最新失败态。
            # 之前提前 return 跳过了 line ~526 的统计更新,导致 total_test_runs 停在旧值、last_run_status 不为 failed。
            sf_ids_to_update: list[str] = []
            if sub_function_ids:
                sf_ids_to_update = [s.strip() for s in sub_function_ids.split(",") if s.strip()]
            elif sub_function_id:
                sf_ids_to_update = [sub_function_id]
            for sf_id in sf_ids_to_update:
                try:
                    async with async_session_factory() as db:
                        sub_function_result = await db.execute(
                            select(WebSubFunction).where(WebSubFunction.id == UUID(sf_id))
                        )
                        sub_function = sub_function_result.scalar_one_or_none()
                        if sub_function:
                            sub_function.total_test_runs = (sub_function.total_test_runs or 0) + 1
                            # 只在从未成功过时才置 failed;
                            # 已经 passed 的子功能不应被这次"AI 修脚本连败被踩刹车"污染,
                            # 因为 MAX_HEAL_REACHED 意味着这次根本没跑脚本。
                            if sub_function.last_run_status != "passed":
                                sub_function.last_run_status = "failed"
                            await db.commit()
                            print(
                                f"[Web Script Execution] MAX_HEAL_REACHED 已累计一次失败到子功能 {sf_id}: "
                                f"total_test_runs={sub_function.total_test_runs}, last_run_status={sub_function.last_run_status}"
                            )
                except Exception as stat_e:
                    print(f"[Web Script Execution] MAX_HEAL_REACHED 更新子功能 {sf_id} 统计失败: {stat_e}")
            return json.dumps({
                "success": False,
                "error": "MAX_HEAL_REACHED",
                "error_type": "SCRIPT_ERROR",
                "message": (
                    f"当前子功能已连续脚本失败 {_fail_count} 次,达到上限 {_MAX_HEAL_ATTEMPTS}。"
                    f"系统检测到这些失败均为脚本逻辑错误（如断言失败、选择器错误等），"
                    f"继续自动修复的成功率较低。请停止继续修复,输出失败报告并结束任务。"
                    f"如需重新尝试,请让用户明确指示或检查测试环境后重试。"
                ),
                "attempts": _fail_count,
                "max_attempts": _MAX_HEAL_ATTEMPTS,
                "thread_id": thread_id,
                "sub_function_id": sub_function_id,
                "sub_function_ids": sub_function_ids,
                "recommendation": "请生成失败分析报告，总结失败原因和修复建议，然后结束任务等待用户指示",
            }, ensure_ascii=False, indent=2)
        # === 守卫结束 ===

        # P0-4: 执行前环境预检，提前发现环境问题
        # 先根据脚本路径推断 framework，用于预检
        _pre_check_framework = framework
        if _pre_check_framework == "auto":
            _path_lower = str(local_script_path).lower()
            if _path_lower.endswith(".py"):
                _pre_check_framework = "python"
            elif _path_lower.endswith(".ts") or _path_lower.endswith(".js"):
                _pre_check_framework = "playwright"

        pre_check = await _pre_check_environment(workspace_mode, _pre_check_framework)
        if not pre_check["ok"]:
            # 环境预检失败，更新子功能状态为 failed，然后返回错误
            # 否则前端会显示"未执行"而不是"失败"
            _pre_check_result = {"success": False, "error": pre_check["error"]}
            if sub_function_ids:
                sf_id_list = [s.strip() for s in sub_function_ids.split(",") if s.strip()]
                for sf_id in sf_id_list:
                    try:
                        await _update_sub_function_stats(sf_id, _pre_check_result)
                    except Exception as stat_e:
                        print(f"[Web Script Execution] 预检失败更新子功能 {sf_id} 统计失败: {stat_e}")
            elif sub_function_id:
                try:
                    await _update_sub_function_stats(sub_function_id, _pre_check_result)
                except Exception as stat_e:
                    print(f"[Web Script Execution] 预检失败更新子功能 {sub_function_id} 统计失败: {stat_e}")

            return json.dumps({
                "success": False,
                "error": f"ENV_CHECK_FAILED: {pre_check['error']}",
                "error_type": "ENV_ERROR",
                "message": pre_check["error"],
                "suggestion": pre_check.get("suggestion", "请检查环境配置"),
                "pre_check_failed": True,
            }, ensure_ascii=False, indent=2)
        # === 环境预检结束 ===

        # 1. 解析脚本路径
        # Windows 路径规范化：统一使用反斜杠，处理正斜杠输入
        normalized_path_str = os.path.normpath(str(local_script_path))
        script_path = Path(normalized_path_str)
        project_root = _get_workspace_root(workspace_mode)

        # 尝试从脚本路径本身推断 workspace_mode（如果传入的是绝对路径）
        if workspace_mode == "auto" and script_path.is_absolute():
            # 检查路径是否包含 webwright 目录
            if "webwright" in str(script_path).lower():
                workspace_mode = "webwright"
                project_root = _get_workspace_root("webwright")
            elif "web_cli" in str(script_path).lower():
                workspace_mode = "web_cli"
                project_root = _get_workspace_root("web_cli")
            elif "web_mcp" in str(script_path).lower():
                workspace_mode = "web_mcp"
                project_root = _get_workspace_root("web_mcp")

        # 新增：当传入相对路径时，从脚本扩展名推断 workspace_mode
        elif workspace_mode == "auto" and not script_path.is_absolute():
            extension = script_path.suffix.lower()
            name = script_path.name.lower()
            if extension == ".py":
                # Python 脚本通常用于 webwright 模式
                workspace_mode = "webwright"
                project_root = _get_workspace_root("webwright")
                print(f"[Web Script Execution] 从扩展名推断 workspace_mode: webwright (Python 脚本)")
            elif extension in (".ts", ".js") or ".spec." in name or ".test." in name:
                # TypeScript/JavaScript 脚本通常用于 web_cli 模式
                workspace_mode = "web_cli"
                project_root = _get_workspace_root("web_cli")
                print(f"[Web Script Execution] 从扩展名推断 workspace_mode: web_cli (TypeScript 脚本)")
            else:
                # 默认使用 web_cli（当前项目主要使用模式）
                workspace_mode = "web_cli"
                project_root = _get_workspace_root("web_cli")
                print(f"[Web Script Execution] 从扩展名推断 workspace_mode: web_cli (默认)")

        # 路径解析：统一处理绝对路径和相对路径
        # 策略：
        # 1. 如果是绝对路径，直接使用（AI传回的 download_web_script 结果）
        # 2. 如果是相对路径，先尝试从 project_root 解析（webwright 模式脚本在 final_runs/ 下）
        # 3. 再尝试 workspace_tests_dir（web_cli 模式的 tests/ 目录）
        original_script_path = script_path

        # FIX: 确保 project_root 是绝对路径，避免 Windows 上相对路径拼接问题
        # 例如：project_root = backend\workspace\webwright (相对路径)
        # 拼接后可能产生不正确的路径，导致 exists() 检查失败
        abs_project_root = project_root.resolve()

        if script_path.is_absolute():
            # 绝对路径：直接使用（通常是 AI 从 download_web_script 获取的 local_path）
            # 但需要验证文件存在，如果不存在可能是路径格式问题
            if not script_path.exists():
                # 尝试规范化后的路径（处理正斜杠/反斜杠不一致）
                alt_path = Path(str(script_path).replace('/', os.sep).replace('\\', os.sep))
                if alt_path.exists():
                    script_path = alt_path
                    print(f"[Web Script Execution] 路径规范化后存在: {script_path}")
                else:
                    # FIX: 处理 Windows 上 \path 被解析为当前盘符根目录，但与 project_root 不同盘的情况
                    # 例如：script_path = \\webwright\\final_runs\\run_032\\final_script.py (D:盘根目录)
                    # project_root = backend\\workspace\\webwright (可能被解析为相对路径)
                    # 尝试将 script_path 的盘符与 project_root 对齐
                    try:
                        # 如果 project_root 不是绝对路径，先转为绝对路径
                        abs_project_root = project_root.resolve()
                        # 如果 script_path 是根目录路径（如 \\webwright\\...），尝试在 project_root 所在盘符下查找
                        if str(script_path).startswith('\\') or (sys.platform == 'win32' and len(script_path.parts) > 0):
                            # 尝试将路径拼接为 project_root 所在盘符下的绝对路径
                            candidate = abs_project_root / script_path.name
                            if candidate.exists():
                                script_path = candidate
                                print(f"[Web Script Execution] 从 project_root 找到脚本: {script_path}")
                            else:
                                # 尝试在 project_root 下按相对路径查找
                                candidate2 = abs_project_root / 'final_runs' / script_path.name
                                if candidate2.exists():
                                    script_path = candidate2
                                    print(f"[Web Script Execution] 从 final_runs 找到脚本: {script_path}")
                                else:
                                    # 遍历 project_root 查找同名文件
                                    for f in abs_project_root.rglob("*.py"):
                                        if f.name == script_path.name:
                                            script_path = f
                                            print(f"[Web Script Execution] 遍历找到脚本: {script_path}")
                                            break
                    except Exception as path_e:
                        print(f"[Web Script Execution] 路径修复尝试失败: {path_e}")
            # 计算相对于项目根目录的路径，供 Playwright CLI 使用
            try:
                abs_script_path = script_path.resolve()
                relative_path = abs_script_path.relative_to(abs_project_root)
            except ValueError:
                # 如果不在项目根目录下，使用文件名（Playwright 会在当前目录查找）
                relative_path = Path(script_path.name)
        else:
            # 相对路径：根据 workspace_mode 选择正确的解析策略
            # webwright 模式：脚本通常在 final_runs/run_XXX/ 下，直接从 project_root 解析
            # web_cli/web_mcp 模式：脚本在 tests/ 目录下
            if workspace_mode == "webwright":
                # webwright 模式：优先从 project_root 直接解析（如 final_runs/run_001/final_script.py）
                # FIX: 使用 abs_project_root 确保是绝对路径，避免 Windows 相对路径拼接问题
                script_path_from_root = abs_project_root / script_path
                if script_path_from_root.exists():
                    script_path = script_path_from_root
                    relative_path = script_path.relative_to(abs_project_root)
                    print(f"[Web Script Execution] webwright 模式：从 abs_project_root 解析路径: {script_path}")
                else:
                    # 回退：尝试 tests 目录
                    workspace_tests_dir = get_workspace_tests_dir(workspace_mode)
                    script_path = workspace_tests_dir / script_path
                    try:
                        relative_path = script_path.relative_to(abs_project_root)
                    except ValueError:
                        relative_path = script_path
            else:
                # web_cli/web_mcp 模式：尝试从 workspace_tests_dir 解析
                workspace_tests_dir = get_workspace_tests_dir(workspace_mode)
                # 防重复路径拼接: 如果 local_script_path 已经以 tests/ 开头,
                # 直接以 abs_project_root 为基解析,避免 tests/tests/ 重复
                if script_path.parts and script_path.parts[0] == "tests":
                    script_path = abs_project_root / script_path
                else:
                    script_path = workspace_tests_dir / script_path
                try:
                    relative_path = script_path.relative_to(abs_project_root)
                except ValueError:
                    relative_path = script_path

        # 调试日志：记录路径解析结果
        print(f"[Web Script Execution] 原始路径: {local_script_path}")
        print(f"[Web Script Execution] 规范化路径: {normalized_path_str}")
        print(f"[Web Script Execution] 解析后路径: {script_path}")
        print(f"[Web Script Execution] 路径类型: {'绝对路径' if script_path.is_absolute() else '相对路径'}")
        print(f"[Web Script Execution] 路径存在: {script_path.exists()}")
        print(f"[Web Script Execution] 项目根目录: {project_root}")
        print(f"[Web Script Execution] 绝对项目根目录: {abs_project_root}")

        # 2. 验证脚本文件存在（带重试机制）
        if not script_path.exists():
            # 尝试备用路径查找
            found_path = None
            # 备用1：在绝对项目根目录下查找同名文件
            candidate = abs_project_root / script_path.name
            if candidate.exists():
                found_path = candidate
                print(f"[Web Script Execution] 在 abs_project_root 找到备用路径: {found_path}")
            # 备用2：在 tests 目录下查找同名文件
            if not found_path:
                candidate = get_workspace_tests_dir(workspace_mode) / script_path.name
                if candidate.exists():
                    found_path = candidate
                    print(f"[Web Script Execution] 在 tests 目录找到备用路径: {found_path}")
            # 备用3：遍历 tests 目录查找同名文件
            if not found_path:
                tests_dir = get_workspace_tests_dir(workspace_mode)
                if tests_dir.exists():
                    for f in tests_dir.rglob("*"):
                        if f.is_file() and f.name == script_path.name:
                            found_path = f
                            print(f"[Web Script Execution] 在 tests 子目录找到备用路径: {found_path}")
                            break

            if found_path:
                script_path = found_path
                try:
                    relative_path = script_path.relative_to(abs_project_root)
                except ValueError:
                    relative_path = script_path
            else:
                # 脚本文件不存在：更新子功能状态为 failed，然后返回错误
                _not_found_result = {"success": False, "error": f"脚本文件不存在: {script_path}"}
                if sub_function_ids:
                    sf_id_list = [s.strip() for s in sub_function_ids.split(",") if s.strip()]
                    for sf_id in sf_id_list:
                        try:
                            await _update_sub_function_stats(sf_id, _not_found_result)
                        except Exception as stat_e:
                            print(f"[Web Script Execution] 脚本不存在更新子功能 {sf_id} 统计失败: {stat_e}")
                elif sub_function_id:
                    try:
                        await _update_sub_function_stats(sub_function_id, _not_found_result)
                    except Exception as stat_e:
                        print(f"[Web Script Execution] 脚本不存在更新子功能 {sub_function_id} 统计失败: {stat_e}")

                return json.dumps({
                    "success": False,
                    "error": f"脚本文件不存在: {script_path}",
                    "hint": f"原始路径: {local_script_path}，已尝试: 1) 直接路径 2) 项目根目录 3) tests 目录",
                    "project_root": str(project_root),
                    "workspace_tests_dir": str(get_workspace_tests_dir(workspace_mode))
                }, ensure_ascii=False, indent=2)

        # 2.5 自动检测框架（根据文件扩展名）
        detected_framework = framework
        if framework == "auto" or not framework:
            extension = script_path.suffix.lower()
            # 处理复合扩展名如 .spec.ts
            name = script_path.name.lower()
            if extension == ".py":
                detected_framework = "python"
            elif extension in (".ts", ".js") or ".spec." in name or ".test." in name:
                detected_framework = "playwright"
            else:
                detected_framework = "python"  # 默认使用 python
            print(f"[Web Script Execution] 自动检测到框架: {detected_framework} (文件: {name})")
        else:
            print(f"[Web Script Execution] 使用指定框架: {detected_framework}")

        script_filename = script_path.name
# pylint: disable  MS80OmFIVnBZMlhscm9ua3VMazZNVTVzYmc9PTpkMTVmOGZjYg==

        print(f"[Web Script Execution] 准备执行脚本: {script_path}")
        print(f"[Web Script Execution] 参数: project_identifier={project_identifier}, sub_function_id={sub_function_id}, sub_function_ids={sub_function_ids}")

        # 3. 确定项目根目录（包含 package.json 和 node_modules）
        # 项目根目录应该是 backend/mcp/web/


        # 4. 确定相对路径（相对于项目根目录）
        # 直接使用前面已计算好的 relative_path（第182-191行已处理跨盘符/非子路径的情况）
        print(f"[Web Script Execution] 项目根目录: {project_root}")
        print(f"[Web Script Execution] 相对脚本路径: {relative_path}")

        # 5. 执行脚本
        # =====================================================================
        # 执行治理层 2a（严格来源授权）：
        #  a) 路径安全：resolve(strict=True) + 工作区包含（符号链接/.. 逃逸防护）
        #  b) 来源授权：脚本内容哈希必须在本项目 web_script_registry 中已登记
        #     （save_web_test_script 保存时登记）；未登记/项目不符 → 终局拒绝
        # =====================================================================
        from app.agents.tools.web.script_provenance import authorize_script_execution
        # rev22 严格模式：解析**全部**子功能（sub_function_ids 逗号分隔 / 单个
        # sub_function_id）；空或非法 → 授权门拒绝（三要素绑定必须建立，无降级回退）
        _auth_sf_ids: list[UUID] = []
        _sf_raw = sub_function_ids if sub_function_ids else (sub_function_id or "")
        for _part in _sf_raw.split(","):
            _part = _part.strip()
            if not _part:
                # rev23：空白/非法 ID 一律终局拒绝，不静默忽略
                return json.dumps({
                    "success": False,
                    "final": True,
                    "guard": "script_provenance",
                    "reason": "子功能 ID 为空：严格模式要求全部子功能 ID 合法且参与三要素绑定",
                    "message": "子功能 ID 为空，终局拒绝。",
                }, ensure_ascii=False)
            try:
                _auth_sf_ids.append(UUID(_part))
            except (ValueError, AttributeError):
                return json.dumps({
                    "success": False,
                    "final": True,
                    "guard": "script_provenance",
                    "reason": f"非法子功能 ID: {_part}",
                    "message": "存在非法子功能 ID，终局拒绝。",
                }, ensure_ascii=False)
        ok, reason = await authorize_script_execution(
            project_identifier,
            script_path,
            abs_project_root,
            async_session_factory,
            sub_function_ids=_auth_sf_ids,
        )
        if not ok:
            print(f"[Web Script Execution] 来源授权拒绝: {reason}")
            # P0-2（rev40）：授权失败同样入队评审（脚本未登记/项目不符等需人工核查）
            try:
                from app.agents.tools.web.script_review import (
                    enqueue_reviews_for_subfunctions,
                )

                await enqueue_reviews_for_subfunctions(
                    async_session_factory, _auth_sf_ids, f"来源授权拒绝: {reason}"
                )
            except Exception as _rq_e:
                print(f"[Web Script Execution] P0-2：授权失败入队评审任务失败（不阻断）: {_rq_e}")
            return json.dumps({
                "success": False,
                "final": True,
                "guard": "script_provenance",
                "reason": reason,
                "message": "脚本未经平台登记或项目归属不符（请先 save_web_test_script 保存并登记后再执行），终局拒绝。",
            }, ensure_ascii=False)
        print("[Web Script Execution] 来源授权通过")

        execution_result = await _execute_script_internal(
            script_path=str(relative_path),
            script_filename=script_filename,
            framework=detected_framework,
            reporter=reporter,
            # rev33：传绝对项目根（settings 的 workspace root 为相对路径时，
            # 子进程 cwd 解析会双重拼接 backend\workspace\webwright\backend\...）
            project_root=str(abs_project_root)
        )

        print(f"[Web Script Execution] 执行结果: success={execution_result.get('success')}, return_code={execution_result.get('return_code')}, timed_out={execution_result.get('timed_out', False)}")
        print(f"[Web Script Execution] 执行结果详情: {json.dumps(execution_result, ensure_ascii=False, indent=2)[:500]}")

        # rev34/rev35（P0 归因修正）：webwright python 模式读取脚本自评
        # （self_reflect_result.json）——脚本内部自评 failed（如某业务步骤失败但
        # 进程退出码为 0）时，不得归因为通过，否则子功能统计被错误更新为 passed。
        _apply_self_reflect_status(execution_result, workspace_mode)

        # P0-2（自愈评审队列）：执行失败（自评 failed / 进程失败）→ 为**全部**子功能
        # 入队评审任务（rev40：不再只入队第一个；绑定当前附件版本）
        if not execution_result.get("success", True):
            try:
                from app.agents.tools.web.script_review import (
                    enqueue_reviews_for_subfunctions,
                )

                _err = (
                    execution_result.get("error_message")
                    or execution_result.get("stderr")
                    or execution_result.get("stdout")
                    or "执行失败（详见报告）"
                )
                await enqueue_reviews_for_subfunctions(
                    async_session_factory, _auth_sf_ids, str(_err)[:800]
                )
                print(f"[Web Script Execution] P0-2：已为 {len(_auth_sf_ids)} 个子功能入队评审任务")
            except Exception as _rq_e:
                print(f"[Web Script Execution] P0-2：入队评审任务失败（不阻断）: {_rq_e}")

        # 6. 保存测试报告到 MinIO（如果生成了 HTML 报告）
        report_attachment_ids = []
        if reporter == "html" and execution_result.get("report_path"):
            # 处理多个子功能（合并脚本场景）
            if sub_function_ids:
                sf_id_list = [sf_id.strip() for sf_id in sub_function_ids.split(",") if sf_id.strip()]
                for sf_id in sf_id_list:
                    try:
                        async with async_session_factory() as db:
                            sub_function_result = await db.execute(
                                select(WebSubFunction).where(WebSubFunction.id == UUID(sf_id))
                            )
                            sub_function = sub_function_result.scalar_one_or_none()

                            if not sub_function:
                                print(f"[Web Script Execution] 子功能不存在: {sf_id}")
                                continue

                            # 校验项目归属
                            from app.models.project import Project
                            project_result = await db.execute(
                                select(Project).where(Project.identifier == project_identifier)
                            )
                            project = project_result.scalar_one_or_none()

                            if not project or sub_function.project_id != project.id:
                                print(f"[Web Script Execution] 子功能 {sf_id} 不属于项目 {project_identifier}")
                                continue

                        attachment_id = await _save_test_report(
                            sub_function_id=sf_id,
                            project_identifier=project_identifier,
                            sub_function=sub_function,
                            report_path=execution_result["report_path"],
                            execution_result=execution_result,
                            project_root=str(abs_project_root)
                        )
                        if attachment_id:
                            report_attachment_ids.append(attachment_id)
                    except Exception as e:
                        print(f"[Web Script Execution] 处理子功能 {sf_id} 失败: {e}")

            # 处理单个子功能（原有逻辑）
            elif sub_function_id:
                try:
                    async with async_session_factory() as db:
                        sub_function_result = await db.execute(
                            select(WebSubFunction).where(WebSubFunction.id == UUID(sub_function_id))
                        )
                        sub_function = sub_function_result.scalar_one_or_none()

                        if not sub_function:
                            print(f"[Web Script Execution] 子功能不存在: {sub_function_id}")
                        else:
                            # 校验项目归属：子功能必须属于传入的 project_identifier
                            from app.models.project import Project
                            project_result = await db.execute(
                                select(Project).where(Project.identifier == project_identifier)
                            )
                            project = project_result.scalar_one_or_none()

                            if not project:
                                print(f"[Web Script Execution] 项目不存在: {project_identifier}")
                            elif sub_function.project_id != project.id:
                                print(f"[Web Script Execution] 权限拒绝：子功能 {sub_function_id} 不属于项目 {project_identifier}")
                            else:
                                # 保存测试报告
                                try:
                                    attachment_id = await _save_test_report(
                                        sub_function_id=sub_function_id,
                                        project_identifier=project_identifier,
                                        sub_function=sub_function,
                                        report_path=execution_result["report_path"],
                                        execution_result=execution_result,
                                        project_root=str(abs_project_root)
                                    )
                                    if attachment_id:
                                        report_attachment_ids.append(attachment_id)
                                except Exception as report_e:
                                    print(f"[Web Script Execution] 保存测试报告失败: {report_e}")
                                    import traceback
                                    traceback.print_exc()

                                # 保存执行结果（WEB_TEST_RESULT 类型附件）
                                try:
                                    result_attachment_id = await _save_test_result(
                                        sub_function_id=sub_function_id,
                                        project_identifier=project_identifier,
                                        sub_function=sub_function,
                                        execution_result=execution_result,
                                    )
                                    if result_attachment_id:
                                        print(f"[Web Script Execution] 执行结果已保存: {result_attachment_id}")
                                except Exception as e:
                                    print(f"[Web Script Execution] 保存执行结果失败: {e}")
                except Exception as e:
                    print(f"[Web Script Execution] 处理单个子功能 {sub_function_id} 失败: {e}")
                    import traceback
                    traceback.print_exc()

        # 7. 更新子功能的测试运行次数和最后状态（统一更新，避免重复计数）
        print(f"[Web Script Execution] 开始更新统计: sub_function_ids={sub_function_ids}, sub_function_id={sub_function_id}")
        _stats_updated = False
        if sub_function_ids:
            # 处理多个子功能
            sf_id_list = [sf_id.strip() for sf_id in sub_function_ids.split(",") if sf_id.strip()]
            print(f"[Web Script Execution] 处理多个子功能: {sf_id_list}")
            for sf_id in sf_id_list:
                _stats_updated = await _update_sub_function_stats(sf_id, execution_result) or _stats_updated
        elif sub_function_id:
            # 处理单个子功能
            print(f"[Web Script Execution] 处理单个子功能: {sub_function_id}")
            _stats_updated = await _update_sub_function_stats(sub_function_id, execution_result)
        else:
            print(f"[Web Script Execution] 未提供 sub_function_id 或 sub_function_ids，跳过统计更新")

        # 8. 返回结果
        # rev34：外层 success 必须反映执行实际结果（脚本自评 failed / 进程失败时
        # 不得返回 success=True，否则调用方误判通过）
        _exec_ok = bool(
            isinstance(execution_result, dict) and execution_result.get("success", True)
        )
        result = {
            "success": _exec_ok,
            "script_path": str(script_path),
            "script_filename": script_filename,
            "detected_framework": detected_framework,
            "execution_result": execution_result
        }

        if report_attachment_ids:
            result["report_attachment_ids"] = report_attachment_ids
            result["message"] = f"脚本执行完成，已为 {len(report_attachment_ids)} 个子功能保存测试报告"

        if sub_function_id:
            result["sub_function_id"] = sub_function_id
        if sub_function_ids:
            result["sub_function_ids"] = sub_function_ids

        # === P0-4: 根据执行结果更新失败计数（带错误分类）===
        if execution_result.get("success"):
            _clear_fail_count(failure_key)  # 成功即清零
        else:
            # 分类错误：区分脚本错误（可修复）vs 环境错误（不可修复）
            error_msg = execution_result.get("error", "")
            stdout = execution_result.get("stdout", "")
            stderr = execution_result.get("stderr", "")
            return_code = execution_result.get("return_code", -1)
            error_type = _classify_error(error_msg, stdout, stderr, return_code)
            _increment_fail_count(failure_key, error_type)
            # 将错误分类结果也返回给AI，帮助其决策
            result["error_type"] = error_type
            result["error_classification"] = (
                "脚本逻辑错误，建议修复脚本后重试" if error_type == "SCRIPT_ERROR"
                else "环境/基础设施错误，建议检查环境后重试"
            )
        # === 计数更新结束 ===

        return json.dumps(result, ensure_ascii=False, indent=2)

    except Exception as e:
        import traceback
        traceback.print_exc()
        return json.dumps({
            "success": False,
            "error": f"执行脚本时发生错误: {str(e)}"
        }, ensure_ascii=False, indent=2)


async def _run_subprocess_async(
    cmd,
    cwd: str,
    env: dict,
    timeout: int,
    is_windows: bool,
) -> Dict[str, Any]:
    """异步执行 subprocess,超时时清理进程树。

    与旧的 subprocess.run 相比:
    - 不阻塞 asyncio 事件循环(SSE 心跳/其他请求正常响应)
    - 超时时通过 taskkill /T (Windows) 或 killpg (POSIX) 清理子孙进程,
      避免 playwright 起的 chrome 残留
    - FIX-2026-08-18: Windows SelectorEventLoop 下 asyncio.create_subprocess_exec
      不支持 shell=True。统一使用 run_in_executor + subprocess.run 在后台线程
      中执行同步子进程，完全避开该限制。
    """
    import concurrent.futures
    loop = asyncio.get_running_loop()

    # 准备命令：列表转字符串（Windows 下 subprocess.run 需要 shell=True 时）
    if isinstance(cmd, list):
        cmd_list = cmd
        # 为日志记录生成字符串版本
        cmd_str = " ".join(f'"{arg}"' if " " in arg else arg for arg in cmd)
    else:
        cmd_list = [cmd]
        cmd_str = cmd

    def _run_in_thread():
        """在线程池中执行同步 subprocess（Windows 下经 Job Object 资源限制，2b-B3）"""
        if is_windows:
            from app.agents.tools.web.process_guard import run_with_job
            return run_with_job(
                cmd_str if isinstance(cmd, str) else cmd_str,
                cwd=cwd,
                env=env,
                timeout=timeout,
                shell=True,
            )
        else:
            import os
            return subprocess.run(
                cmd_list,
                cwd=cwd,
                env=env,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                timeout=timeout,
                preexec_fn=os.setsid,
            )

    try:
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
            proc = await asyncio.wait_for(
                loop.run_in_executor(pool, _run_in_thread),
                timeout=timeout + 5  # 给线程切换留一点余量
            )
        return {
            "return_code": proc.returncode,
            "stdout": proc.stdout.decode("utf-8", errors="replace") if proc.stdout else "",
            "stderr": proc.stderr.decode("utf-8", errors="replace") if proc.stderr else "",
            "timed_out": False,
        }
    except asyncio.TimeoutError:
        # 超时：尝试杀掉进程树
        # 注意：由于 subprocess.run 在线程中执行，超时时它可能还在运行。
        # 我们只能通过超时异常返回，无法精确杀掉那个线程中的进程。
        # 但 subprocess.run 的 timeout 参数会在底层杀掉进程。
        return {
            "return_code": -1,
            "stdout": "",
            "stderr": f"[TIMEOUT] 执行超过 {timeout}s",
            "timed_out": True,
        }
    except subprocess.TimeoutExpired:
        return {
            "return_code": -1,
            "stdout": "",
            "stderr": f"[TIMEOUT] 执行超过 {timeout}s",
            "timed_out": True,
        }


async def _kill_process_tree(process, is_windows: bool):
    """终止子进程及其所有后代(playwright/chrome)。"""
    if process.returncode is not None:
        return
    try:
        if is_windows:
            # FIX-2026-08-18: Windows SelectorEventLoop 下 asyncio.create_subprocess_exec
            # 不支持 shell=True。统一使用 run_in_executor + subprocess.run 在后台线程
            # 中执行同步子进程，完全避开该限制。
            import concurrent.futures
            loop = asyncio.get_running_loop()
            kill_cmd = f"taskkill /F /T /PID {process.pid}"

            def _run_kill():
                return subprocess.run(
                    kill_cmd,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    shell=True,
                    creationflags=0x00000200,  # CREATE_NEW_PROCESS_GROUP
                )

            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
                await asyncio.wait_for(
                    loop.run_in_executor(pool, _run_kill),
                    timeout=5
                )
        else:
            import signal
            os.killpg(os.getpgid(process.pid), signal.SIGTERM)
            try:
                await asyncio.wait_for(process.wait(), timeout=3)
            except asyncio.TimeoutError:
                os.killpg(os.getpgid(process.pid), signal.SIGKILL)
    except (ProcessLookupError, OSError):
        pass


async def _execute_script_internal(
    script_path: str,
    script_filename: str,
    framework: str,
    reporter: str,
    project_root: str
) -> Dict[str, Any]:
    """
    内部执行脚本函数（高性能版）

    优化点：
    - 减少超时时间（300秒 → 180秒），避免长时间阻塞
    - 支持并发执行标记（future: 多脚本并行）
    - 优化报告目录清理策略

    Args:
        script_path: 脚本文件相对路径（相对于 project_root）
        script_filename: 脚本文件名
        framework: 测试框架
        reporter: 报告格式
        project_root: 项目根目录

    Returns:
        执行结果字典
    """
    try:
        start_time = datetime.now()

        # 确定测试命令
        is_windows = sys.platform == "win32"

        # 为每次执行生成唯一的报告目录，避免批量执行时报告被覆盖
        import time
        report_dir_name = f"playwright-report-{int(time.time() * 1000)}-{os.urandom(4).hex()}"
        report_dir = Path(project_root) / report_dir_name

        # P0-3: JSON reporter 输出文件路径（供后续 _save_test_report 解析结构化结果）
        # 说明:
        # - HTML 依然是主报告(前端展示、MinIO 归档),保留原有目录检测逻辑不变
        # - JSON 通过 PLAYWRIGHT_JSON_OUTPUT_NAME 环境变量写入独立文件,避免污染 stdout 日志
        # - 解析失败时,回落到原有 stdout 正则解析(_save_test_report 内),向后兼容
        json_report_path = Path(project_root) / f"{report_dir_name}-result.json"

        if framework == "playwright":
            # Playwright CLI 需要相对于项目根目录的脚本路径
            # script_path 参数已经是相对路径（如 "tests/xxx.spec.ts" 或 "xxx.spec.ts"）
            # 使用 script_path 而不是 script_filename，确保子目录中的脚本也能正确执行
            # FIX: 使用 as_posix() 将路径转换为正斜杠格式，避免 Windows 反斜杠问题
            playwright_test_target = Path(script_path).as_posix()
            if reporter == "html":
                # HTML 报告需要指定输出目录，使用唯一目录避免冲突
                # 注意：--screenshot=only-on-failure 需要 Playwright v1.40+
                # 为兼容旧版本，不在 CLI 传递 screenshot 参数，而是在 playwright.config.ts 中配置
                # P0-3: 使用 html,json 双 reporter,HTML 主报告 + JSON 结构化结果
                # FIX: Windows 和 Linux 统一使用列表格式命令，避免 shell 转义问题
                cmd = ["npx", "playwright", "test", playwright_test_target, "--reporter=html,json", f"--output={report_dir_name}"]
            else:
                cmd = ["npx", "playwright", "test", playwright_test_target, f"--reporter={reporter}"]
        elif framework == "python":
            # Webwright 模式：直接执行 Python 脚本
            # script_path 可能是绝对路径或相对路径
            script_file = Path(script_path)
            if script_file.is_absolute():
                script_abs_path = str(script_file)
            else:
                script_abs_path = str(Path(project_root) / script_path)

            # 确定 Python 命令（优先使用预检测的解释器，与预检逻辑保持一致）
            python_cmd = _PYTHON_CMD
            if not python_cmd:
                # 回退：先尝试 sys.executable，再尝试系统 PATH 中的命令
                python_candidates = []
                if sys.executable:
                    python_candidates.append(sys.executable)
                if is_windows:
                    python_candidates.extend(["python", "py", "python3"])
                else:
                    python_candidates.extend(["python3", "python"])

                print(f"[Web Script Execution] 预检测未找到 Python，尝试回退列表: {python_candidates}")

                for candidate in python_candidates:
                    try:
                        test_proc = subprocess.run(
                            [candidate, "--version"],
                            capture_output=True,
                            timeout=5,
                            creationflags=0x00000200 if is_windows else 0
                        )
                        if test_proc.returncode == 0:
                            python_cmd = candidate
                            print(f"[Web Script Execution] 使用 Python 命令: {candidate}")
                            break
                    except Exception as e:
                        print(f"[Web Script Execution] 候选 Python 不可用: {candidate}, 错误: {e}")
                        continue

            if not python_cmd:
                return {
                    "success": False,
                    "error": f"无法找到可用的 Python 解释器。尝试了预检测路径、sys.executable({sys.executable})和系统 PATH。请确保 Python 已安装。",
                    "stdout": "",
                    "stderr": "",
                    "report_path": None,
                    "json_stats": None,
                }

            print(f"[Web Script Execution] 使用 Python 解释器: {python_cmd}")

            # FIX: 统一使用列表格式命令，避免 shell 转义问题
            cmd = [python_cmd, script_abs_path]
        else:
            return {
                "success": False,
                "error": f"不支持的测试框架: {framework}，Web 测试支持 playwright 和 python"
            }

        print(f"[Web Script Execution] 执行命令: {' '.join(cmd)}")
        print(f"[Web Script Execution] 工作目录: {project_root}")
        print(f"[Web Script Execution] 报告目录: {report_dir}")
# pylint: disable  Mi80OmFIVnBZMlhscm9ua3VMazZNVTVzYmc9PTpkMTVmOGZjYg==

        # 准备环境变量（设置 CI=1 禁用 Playwright HTML reporter 自动打开浏览器）
        env = os.environ.copy()
        if framework == "playwright" and reporter == "html":
            env['CI'] = '1'
            # P0-3: 让 json reporter 写入独立文件而非 stdout,避免污染日志
            env['PLAYWRIGHT_JSON_OUTPUT_NAME'] = str(json_report_path)

        # 执行测试（根据脚本类型设置不同超时：Python脚本10分钟，Playwright CLI 5分钟）
        script_timeout = 600 if framework == "python" else 300  # Python/Playwright脚本需要更长时间
        run_result = await _run_subprocess_async(
            cmd=cmd,
            cwd=project_root,
            env=env,
            timeout=script_timeout,
            is_windows=is_windows,
        )

        # 检测报告路径（无论超时或正常完成，都尝试捕获已生成的报告）
        report_path = await _detect_report_path(
            framework=framework,
            reporter=reporter,
            report_dir=report_dir,
            script_path=script_path,
            project_root=project_root
        )

        # P0-3: 尝试解析 JSON reporter 输出（Playwright html,json 双 reporter 场景）
        # 解析失败不影响主流程,后续 _save_test_report 会回落到 stdout 正则解析
        json_stats = None
        if framework == "playwright" and reporter == "html" and json_report_path.exists():
            try:
                json_stats = _parse_playwright_json_report(json_report_path)
                if json_stats:
                    print(f"[Web Script Execution] JSON reporter 解析成功: "
                          f"passed={json_stats.get('passed')}, "
                          f"failed={json_stats.get('failed')}, "
                          f"skipped={json_stats.get('skipped')}")
            except Exception as e:
                print(f"[Web Script Execution] JSON reporter 解析失败（将回落到 stdout 解析）: {e}")

        if run_result["timed_out"]:
            return {
                "success": False,
                "error": f"脚本执行超时（超过 {script_timeout}s）",
                "stdout": run_result["stdout"],
                "stderr": run_result["stderr"],
                "report_path": report_path,  # 包含已生成的报告路径
                "json_stats": json_stats,
            }

        end_time = datetime.now()
        duration = (end_time - start_time).total_seconds()

        # 解析输出（保持与旧代码相同的变量名，后续逻辑无需修改）
        stdout = run_result["stdout"]
        stderr = run_result["stderr"]
        return_code = run_result["return_code"]

        print(f"[Web Script Execution] 执行完成，返回码: {return_code}")
        print(f"[Web Script Execution] 执行时间: {duration:.2f}s")

        # =========================================================================
        # Python 脚本结果后处理：生成兼容的 json_stats
        # =========================================================================
        # Playwright 脚本有 JSON reporter 自动生成统计，但 Python 脚本没有
        # 需要根据执行结果生成兼容的统计信息，供后续报告生成使用
        if framework == "python" and not json_stats:
            if return_code == 0:
                # 执行成功：生成一个通过的测试项统计
                json_stats = {
                    "total": 1,
                    "passed": 1,
                    "failed": 0,
                    "skipped": 0,
                    "flaky": 0,
                    "duration_ms": int(duration * 1000),
                    "test_cases": [{
                        "title": "Python 脚本执行",
                        "status": "passed",
                        "duration_ms": int(duration * 1000),
                        "error": None
                    }]
                }
            else:
                # 执行失败：生成一个失败的测试项统计
                # 尝试从 stderr/stdout 提取错误信息
                error_msg = None
                if stderr:
                    error_msg = stderr.strip()[:500]
                elif stdout:
                    # 尝试从 stdout 提取 Traceback 或错误行
                    import re
                    traceback_match = re.search(
                        r"Traceback \(most recent call last\):.*?(?=\n\n|\Z)",
                        stdout, re.DOTALL
                    )
                    if traceback_match:
                        error_msg = traceback_match.group(0)[:500]
                    else:
                        error_line = re.search(
                            r"^[A-Z][a-zA-Z0-9_]*(?:Error|Exception):[^\n]{0,300}",
                            stdout, re.MULTILINE
                        )
                        if error_line:
                            error_msg = error_line.group(0).strip()
                        else:
                            error_msg = stdout.strip()[:500] if stdout.strip() else "执行失败（无详细错误信息）"
                else:
                    error_msg = f"执行失败（返回码: {return_code}）"

                json_stats = {
                    "total": 1,
                    "passed": 0,
                    "failed": 1,
                    "skipped": 0,
                    "flaky": 0,
                    "duration_ms": int(duration * 1000),
                    "test_cases": [{
                        "title": "Python 脚本执行",
                        "status": "failed",
                        "duration_ms": int(duration * 1000),
                        "error": error_msg
                    }]
                }
            print(f"[Web Script Execution] Python 脚本结果统计已生成: "
                  f"passed={json_stats['passed']}, failed={json_stats['failed']}")

        return {
            "success": return_code == 0,
            "return_code": return_code,
            "duration": duration,
            "stdout": stdout,
            "stderr": stderr,
            "report_path": report_path,
            "json_stats": json_stats,
            "start_time": start_time.isoformat(),
            "end_time": end_time.isoformat()
        }

    except subprocess.TimeoutExpired:
        # 保留作为最后兜底(理论上不会触发,超时已在 _run_subprocess_async 处理)
        # 即使在此兜底场景也尝试检测报告
        try:
            report_path = await _detect_report_path(
                framework=framework,
                reporter=reporter,
                report_dir=report_dir,
                script_path=script_path,
                project_root=project_root
            )
            return {
                "success": False,
                "error": "脚本执行超时",
                "report_path": report_path,
            }
        except Exception:
            return {
                "success": False,
                "error": "脚本执行超时"
            }
    except Exception as e:
        return {
            "success": False,
            "error": f"执行脚本时发生错误: {str(e)}"
        }


def _parse_playwright_json_report(json_path: Path) -> Optional[Dict[str, Any]]:
    """解析 Playwright --reporter=json 生成的结果文件。

    Playwright JSON reporter 的 schema 在版本间有小幅变化:
    - 1.35 之前: stats 使用 { expected, unexpected, flaky, skipped }
    - 1.40+:     stats 保留 { expected, unexpected, flaky, skipped }，语义未变
    - suites 结构:
        [{ title, specs: [{ title, tests: [{ results: [{ status, duration }] }] }], suites: [...嵌套] }]

    本函数做以下处理:
    - 字段用 .get() 兜底,兼容 schema 微调
    - 递归展开 suites,收集所有 test 的 title/status/duration
    - 失败时抛出异常,由调用方决定是否回落到 stdout 解析

    Args:
        json_path: Playwright JSON 报告文件路径

    Returns:
        统一结构:
        {
            "total": int,
            "passed": int,
            "failed": int,
            "skipped": int,
            "flaky": int,
            "duration_ms": int,
            "test_cases": [
                {"title": str, "status": str, "duration_ms": int, "error": str|None}
            ]
        }
        或 None (文件不存在或格式完全无法识别)
    """
    if not json_path.exists():
        return None

    try:
        with open(json_path, "r", encoding="utf-8", errors="replace") as f:
            data = json.load(f)
    except (json.JSONDecodeError, OSError) as e:
        print(f"[Web Script Execution] JSON 报告读取失败: {e}")
        return None

    stats = data.get("stats", {}) if isinstance(data, dict) else {}
    passed = int(stats.get("expected", 0) or 0)
    # unexpected 是 Playwright 对 "failed" 的正式命名;部分社区 fork 用 failed
    failed = int(stats.get("unexpected", stats.get("failed", 0)) or 0)
    skipped = int(stats.get("skipped", 0) or 0)
    flaky = int(stats.get("flaky", 0) or 0)
    duration_ms = int(stats.get("duration", 0) or 0)

    # 递归展开 suites 结构,提取每个 test case
    test_cases = []

    def _walk_suites(suites):
        if not isinstance(suites, list):
            return
        for suite in suites:
            if not isinstance(suite, dict):
                continue
            # 当前层的 specs
            for spec in suite.get("specs", []) or []:
                if not isinstance(spec, dict):
                    continue
                spec_title = spec.get("title", "unknown")
                for test in spec.get("tests", []) or []:
                    if not isinstance(test, dict):
                        continue
                    # 每个 test 可能有多次 results (retries),取最后一次
                    results = test.get("results", []) or []
                    if not results:
                        continue
                    last = results[-1] if isinstance(results[-1], dict) else {}
                    status = last.get("status", "unknown")
                    dur = int(last.get("duration", 0) or 0)
                    error_msg = None
                    err = last.get("error")
                    if isinstance(err, dict):
                        error_msg = err.get("message") or err.get("stack") or None
                    test_cases.append({
                        "title": spec_title,
                        "status": status,
                        "duration_ms": dur,
                        "error": error_msg,
                    })
            # 递归嵌套 suites
            _walk_suites(suite.get("suites", []) or [])

    _walk_suites(data.get("suites", []) if isinstance(data, dict) else [])

    total = passed + failed + skipped
    return {
        "total": total,
        "passed": passed,
        "failed": failed,
        "skipped": skipped,
        "flaky": flaky,
        "duration_ms": duration_ms,
        "test_cases": test_cases,
    }


async def _detect_report_path(
    framework: str,
    reporter: str,
    report_dir: Path,
    script_path: str,
    project_root: str
) -> Optional[str]:
    """检测并返回报告目录路径，支持 Playwright HTML 和 Python/Webwright 模式。

    统一提取报告检测逻辑，确保超时或异常场景下也能捕获已生成的报告。

    Args:
        framework: 测试框架 (playwright / python)
        reporter: 报告格式 (html / list / etc.)
        report_dir: Playwright HTML 报告目录（唯一目录）
        script_path: 脚本文件路径
        project_root: 项目根目录

    Returns:
        报告目录路径（字符串）或 None
    """
    report_path = None

    if framework == "playwright" and reporter == "html":
        # 策略1: 检查唯一命名的报告目录
        index_html = report_dir / "index.html"
        if index_html.exists():
            report_path = str(report_dir)
            print(f"[Web Script Execution] HTML 报告已生成(唯一目录): {report_path}")
        else:
            # 策略2: 检查默认的 playwright-report 目录
            fallback_dir = Path(project_root) / "playwright-report"
            fallback_index = fallback_dir / "index.html"
            if fallback_index.exists():
                report_path = str(fallback_dir)
                print(f"[Web Script Execution] HTML 报告已生成(默认目录): {report_path}")
            else:
                # 策略3: 扫描 project_root 下所有 playwright-report-* 目录
                # 处理 Playwright 可能生成到非预期目录的情况
                report_dirs = sorted(
                    [d for d in Path(project_root).glob("playwright-report-*") if d.is_dir()],
                    key=lambda d: d.stat().st_mtime,
                    reverse=True
                )
                for candidate_dir in report_dirs:
                    candidate_index = candidate_dir / "index.html"
                    if candidate_index.exists():
                        report_path = str(candidate_dir)
                        print(f"[Web Script Execution] HTML 报告已生成(扫描目录): {report_path}")
                        break

    elif framework == "python":
        # Webwright 模式：检查 Python 脚本执行产物
        # script_path 可能是相对路径（如 "final_runs/run_001/final_script.py"）
        # 需要先解析为绝对路径来确定脚本目录
        script_path_obj = Path(script_path)
        if script_path_obj.is_absolute():
            script_dir = script_path_obj.parent
        else:
            # 相对路径：与 project_root 拼接
            script_dir = Path(project_root) / script_path_obj.parent

        log_file = script_dir / "final_script_log.txt"
        screenshots_dir = script_dir / "screenshots"
        test_report_html = script_dir / "test_report.html"
        md_report_files = list(script_dir.glob("test_report_run_*.md"))

        if test_report_html.exists():
            report_path = str(script_dir)
            print(f"[Web Script Execution] 测试报告已生成: {test_report_html}")
        elif md_report_files:
            report_path = str(script_dir)
            print(f"[Web Script Execution] Markdown 测试报告已生成: {md_report_files[0]}")
        elif log_file.exists() or screenshots_dir.exists():
            # 有日志或截图也作为报告路径，供 _save_test_report 打包
            report_path = str(script_dir)
            print(f"[Web Script Execution] 使用脚本目录作为报告路径: {report_path}")
        else:
            # 没有任何报告文件，但脚本目录存在，仍返回目录路径
            # 这样 _save_test_report 可以基于 stdout/stderr 生成基本报告
            if script_dir.exists():
                report_path = str(script_dir)
                print(f"[Web Script Execution] Python 脚本无报告产物，使用脚本目录: {report_path}")
            else:
                # 脚本目录不存在，使用 project_root 作为回退
                report_path = str(project_root)
                print(f"[Web Script Execution] Python 脚本目录不存在，使用 project_root: {report_path}")

    return report_path


async def _save_test_report(
    sub_function_id: str,
    project_identifier: str,
    sub_function: WebSubFunction,
    report_path: str,
    execution_result: Dict[str, Any],
    project_root: str
) -> Optional[str]:
    """
    保存测试报告到 MinIO 并创建附件记录

    Args:
        sub_function_id: 子功能 ID
        project_identifier: 项目标识符
        sub_function: 子功能对象
        report_path: 报告目录路径
        execution_result: 执行结果
        project_root: 项目根目录

    Returns:
        附件 ID，如果保存失败则返回 None
    """
    try:
        # ======================================================================
        # 检测报告类型：Webwright Markdown vs Playwright HTML
        # ======================================================================
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        report_dir = Path(report_path)

        # Webwright Python 脚本可能已经生成了最终 HTML 报告。
        # 优先保存脚本原生报告，避免被通用生成器降级成“无结构化测试数据”的空报告。
        native_html_report = report_dir / "test_report.html"
        if native_html_report.exists() and native_html_report.is_file():
            print(f"[Web Report] 检测到 Webwright HTML 报告: {native_html_report}")
            html_bytes = native_html_report.read_bytes()
            html_object_name = f"web-tests/{project_identifier}/sub-functions/{sub_function_id}/test-report-html-{timestamp}.html"

            sr_summary = {}
            sr_path = report_dir / "self_reflect_result.json"
            if sr_path.exists():
                try:
                    sr_data = json.loads(sr_path.read_text(encoding="utf-8"))
                    sr_summary = sr_data.get("summary", {}) or {}
                except Exception as e:
                    print(f"[Web Report] 读取 self_reflect_result.json 摘要失败: {e}")

            failed_count = int(sr_summary.get("FAIL", 0)) if isinstance(sr_summary, dict) else 0
            success = execution_result.get("success", False)
            result_tag = "通过" if success and failed_count == 0 else "失败"
            safe_name = sub_function.display_name.replace(" ", "-").replace("/", "-").replace("\\", "-") if sub_function.display_name else "未命名功能"
            html_filename = f"{safe_name}-测试报告-{result_tag}-{timestamp}.html"

            try:
                MinIOClient.upload_bytes(
                    object_name=html_object_name,
                    data=html_bytes,
                    content_type="text/html"
                )
                print(f"[Web Report] Webwright HTML 报告已上传到 MinIO: {html_object_name}")
            except Exception as e:
                print(f"[Web Report] MinIO HTML 上传失败: {e}，将使用本地备份")

            try:
                from app.agents.tools.web.artifacts_tools import _backup_to_local
                _backup_to_local(html_object_name, html_bytes)
            except Exception:
                pass

            async with async_session_factory() as session:
                description = f"Web 测试报告 - {sub_function.display_name}\n"
                duration = execution_result.get("duration", 0)
                description += f"执行时间: {duration:.2f}秒\n"
                if isinstance(sr_summary, dict) and sr_summary:
                    passed_count = int(sr_summary.get("PASS", 0))
                    warn_count = int(sr_summary.get("WARN", 0))
                    description += f"通过: {passed_count} | 失败: {failed_count} | 警告: {warn_count}"

                existing_stmt = select(Attachment).where(Attachment.object_name == html_object_name)
                existing_result = await session.execute(existing_stmt)
                existing_attachment = existing_result.scalar_one_or_none()

                if existing_attachment:
                    existing_attachment.file_size = len(html_bytes)
                    existing_attachment.file_name = html_filename
                    existing_attachment.description = description
                    existing_attachment.updated_at = datetime.now(timezone.utc)
                    session.add(existing_attachment)
                    await session.commit()
                    await session.refresh(existing_attachment)
                    attachment = existing_attachment
                    print(f"[Web Report] 更新已存在的 Webwright HTML 附件记录: {attachment.id}")
                else:
                    attachment = Attachment(
                        entity_type=AttachmentEntityType.WEB_TEST_REPORT,
                        entity_id=UUID(sub_function_id),
                        project_id=sub_function.project_id,
                        file_name=html_filename,
                        file_size=len(html_bytes),
                        content_type="text/html",
                        object_name=html_object_name,
                        description=description,
                        created_by="web-agent"
                    )
                    session.add(attachment)
                    await session.commit()
                    await session.refresh(attachment)
                    print(f"[Web Report] Webwright HTML 报告附件已创建: {attachment.id}")

                return str(attachment.id)

        # 检查是否是 Webwright 模式（有 Markdown 报告）
        md_report_files = list(report_dir.glob("test_report_run_*.md"))
        is_webwright_mode = len(md_report_files) > 0

        if is_webwright_mode:
            # ======================================================================
            # Webwright 模式：将 Markdown 报告转换为 HTML 并保存
            # ======================================================================
            print(f"[Web Report] 检测到 Webwright Markdown 报告: {md_report_files[0]}")

            from app.utils.markdown_report_converter import MarkdownReportConverter

            converter = MarkdownReportConverter(project_identifier=project_identifier)
            md_content = md_report_files[0].read_text(encoding='utf-8')
            data = converter.parse_markdown_report(md_content)

            # 找到当前子功能对应的模块数据
            module_data = None
            for module in data.get("modules", []):
                if module["name"] == sub_function.display_name:
                    module_data = module
                    break

            # 生成 HTML 报告
            html_content = converter.generate_html_report(
                data,
                module_name=sub_function.display_name,
                module_data=module_data
            )
            html_bytes = html_content.encode('utf-8')

            # 保存 HTML 报告到 MinIO
            html_object_name = f"web-tests/{project_identifier}/sub-functions/{sub_function_id}/test-report-html-{timestamp}.html"
            MinIOClient.upload_bytes(
                object_name=html_object_name,
                data=html_bytes,
                content_type="text/html"
            )

            # 创建附件记录
            async with async_session_factory() as session:
                safe_name = sub_function.display_name.replace(" ", "-").replace("/", "-").replace("\\", "-") if sub_function.display_name else "未命名功能"
                html_filename = f"{safe_name}-测试报告-run_{data.get('run_id', 'unknown')}-{timestamp}.html"

                passed = module_data["passed"] if module_data else data["stats"]["passed"]
                skipped = module_data["skipped"] if module_data else data["stats"]["skipped"]
                failed = module_data["failed"] if module_data else data["stats"]["failed"]
                description = f"Web 测试报告 - {sub_function.display_name}\n"
                description += f"通过: {passed} | 跳过: {skipped} | 失败: {failed}"

                existing_stmt = select(Attachment).where(Attachment.object_name == html_object_name)
                existing_result = await session.execute(existing_stmt)
                existing_attachment = existing_result.scalar_one_or_none()

                if existing_attachment:
                    existing_attachment.file_size = len(html_bytes)
                    existing_attachment.file_name = html_filename
                    existing_attachment.description = description
                    existing_attachment.updated_at = datetime.now(timezone.utc)
                    session.add(existing_attachment)
                    await session.commit()
                    await session.refresh(existing_attachment)
                    attachment = existing_attachment
                    print(f"[Web Report] 更新已存在的 HTML 附件记录: {attachment.id}")
                else:
                    attachment = Attachment(
                        entity_type=AttachmentEntityType.WEB_TEST_REPORT,
                        entity_id=UUID(sub_function_id),
                        project_id=sub_function.project_id,
                        file_name=html_filename,
                        file_size=len(html_bytes),
                        content_type="text/html",
                        object_name=html_object_name,
                        description=description,
                        created_by="web-agent"
                    )
                    session.add(attachment)
                    await session.commit()
                    await session.refresh(attachment)
                    print(f"[Web Report] HTML 报告附件已创建: {attachment.id}")

                try:
                    from app.agents.tools.web.artifacts_tools import _backup_to_local
                    _backup_to_local(html_object_name, html_bytes)
                except Exception:
                    pass

                return str(attachment.id)

        # ======================================================================
        # 检查是否是 Python 脚本模式（无 Markdown 报告，但有 json_stats）
        # ======================================================================
        json_stats = execution_result.get("json_stats")
        is_python_mode = isinstance(json_stats, dict) and json_stats.get("test_cases")

        # 尝试从 self_reflect_result.json 获取更详细的测试结果（webwright 模式）
        sr_results = None
        if report_path:
            sr_path = Path(report_path) / "self_reflect_result.json"
            if not sr_path.exists():
                sr_path = Path(report_path).parent / "self_reflect_result.json"
            if sr_path.exists():
                try:
                    sr_data = json.loads(sr_path.read_text(encoding="utf-8"))
                    sr_results = sr_data.get("results", [])
                    sr_summary = sr_data.get("summary", {})
                    print(f"[Web Report] 从 self_reflect_result.json 读取到 {len(sr_results)} 个检查点")
                except Exception as e:
                    print(f"[Web Report] 读取 self_reflect_result.json 失败: {e}")

        if is_python_mode:
            # ======================================================================
            # Python 脚本模式：直接使用 json_stats 生成 HTML 报告（跳过 ZIP 打包）
            # ======================================================================
            print(f"[Web Report] 检测到 Python 脚本执行结果，直接生成 HTML 报告")

            try:
                from app.utils.web_test_report_generator import generate_web_test_report_html

                # 收集截图路径
                screenshots = []
                r_dir = Path(report_path)
                if r_dir.exists():
                    # 来源1: screenshots/ 子目录
                    screenshots_dir = r_dir / "screenshots"
                    if screenshots_dir.exists():
                        for png_file in screenshots_dir.glob("*.png"):
                            screenshots.append(str(png_file.relative_to(r_dir)))

                    # 来源2: 报告目录根目录下的 png 文件
                    for png_file in r_dir.glob("*.png"):
                        screenshots.append(str(png_file.name))

                print(f"[Web Report] 收集到 {len(screenshots)} 张截图")

                # 读取日志
                logs = None
                log_file = r_dir / "final_script_log.txt" if r_dir.exists() else None
                if log_file and log_file.exists():
                    try:
                        logs = log_file.read_text(encoding="utf-8")
                    except Exception:
                        pass

                # 确定结果标签
                success = execution_result.get("success", False)
                failed_count = int(json_stats.get("failed", 0))
                result_tag = "通过" if success and failed_count == 0 else "失败"

                # 如果有 self_reflect_result.json 的结果，转换为 test_scenarios
                test_scenarios = None
                if sr_results:
                    test_scenarios = []
                    for r in sr_results:
                        status = r.get("status", "PASS")
                        if status == "PASS":
                            mapped_status = "pass"
                        elif status == "FAIL":
                            mapped_status = "fail"
                        elif status == "WARN":
                            mapped_status = "partial"
                        else:
                            mapped_status = "pass"
                        test_scenarios.append({
                            "name": r.get("name", "未命名检查点"),
                            "status": mapped_status,
                            "detail": f"检查点: {r.get('cp', '')} | {r.get('detail', '')}".strip(" |"),
                        })

                # 生成 HTML 报告（Python 模式 - 带 self_reflect 结果）
                html_content = generate_web_test_report_html(
                    sub_function_name=sub_function.display_name or "未命名功能",
                    sub_function_id=sub_function_id,
                    execution_result=execution_result,
                    project_identifier=project_identifier,
                    test_scenarios=test_scenarios,
                    screenshots=screenshots if screenshots else None,
                    logs=logs,
                    report_dir=str(r_dir) if r_dir.exists() else None,
                )
                html_bytes = html_content.encode("utf-8")

                # 生成 HTML 文件名
                safe_name = sub_function.display_name.replace(" ", "-").replace("/", "-").replace("\\", "-") if sub_function.display_name else "未命名功能"
                html_filename = f"{safe_name}-测试报告-{result_tag}-{timestamp}.html"

                # 保存 HTML 报告到 MinIO
                html_object_name = f"web-tests/{project_identifier}/sub-functions/{sub_function_id}/test-report-html-{timestamp}.html"
                try:
                    MinIOClient.upload_bytes(
                        object_name=html_object_name,
                        data=html_bytes,
                        content_type="text/html"
                    )
                    print(f"[Web Report] HTML 报告已上传到 MinIO: {html_object_name}")
                except Exception as e:
                    print(f"[Web Report] MinIO HTML 上传失败: {e}，将使用本地备份")

                # 备份 HTML 报告
                try:
                    from app.agents.tools.web.artifacts_tools import _backup_to_local
                    _backup_to_local(html_object_name, html_bytes)
                except Exception:
                    pass

                # 创建附件记录
                async with async_session_factory() as session:
                    description = f"Web 测试报告 - {sub_function.display_name}\n"
                    duration = execution_result.get("duration", 0)
                    description += f"执行时间: {duration:.2f}秒\n"
                    if isinstance(sr_summary, dict) and sr_summary:
                        passed_count = int(sr_summary.get("PASS", 0))
                        failed_count = int(sr_summary.get("FAIL", 0))
                        other_count = int(sr_summary.get("WARN", 0))
                        other_label = "警告"
                    else:
                        passed_count = int(json_stats.get("passed", 0))
                        failed_count = int(json_stats.get("failed", 0))
                        other_count = int(json_stats.get("skipped", 0))
                        other_label = "跳过"
                    if passed_count > 0 or failed_count > 0 or other_count > 0:
                        description += f"通过: {passed_count} | 失败: {failed_count} | {other_label}: {other_count}"

                    existing_stmt = select(Attachment).where(Attachment.object_name == html_object_name)
                    existing_result = await session.execute(existing_stmt)
                    existing_attachment = existing_result.scalar_one_or_none()

                    if existing_attachment:
                        existing_attachment.file_size = len(html_bytes)
                        existing_attachment.file_name = html_filename
                        existing_attachment.description = description
                        existing_attachment.updated_at = datetime.now(timezone.utc)
                        session.add(existing_attachment)
                        await session.commit()
                        await session.refresh(existing_attachment)
                        attachment = existing_attachment
                        print(f"[Web Report] 更新已存在的 HTML 附件记录: {attachment.id}")
                    else:
                        attachment = Attachment(
                            entity_type=AttachmentEntityType.WEB_TEST_REPORT,
                            entity_id=UUID(sub_function_id),
                            project_id=sub_function.project_id,
                            file_name=html_filename,
                            file_size=len(html_bytes),
                            content_type="text/html",
                            object_name=html_object_name,
                            description=description,
                            created_by="web-agent"
                        )
                        session.add(attachment)
                        await session.commit()
                        await session.refresh(attachment)
                        print(f"[Web Report] HTML 报告附件已创建: {attachment.id}")

                    return str(attachment.id)

            except Exception as html_e:
                print(f"[Web Report] Python 模式 HTML 报告生成失败: {html_e}")
                import traceback
                traceback.print_exc()
                # 失败时回退到 ZIP 打包逻辑（下方继续执行）

        # ======================================================================
        # Playwright CLI 模式：打包 ZIP + 生成 HTML（原有逻辑）
        # ======================================================================
        # 1. 将报告目录打包成 ZIP
        # 解析测试结果，用于生成更友好的文件名
        # P0-3: 优先使用 JSON reporter 的结构化统计,解析失败时回落到 stdout 正则解析
        stdout = execution_result.get("stdout", "")
        json_stats = execution_result.get("json_stats")
        import re
        passed_count = 0
        failed_count = 0

        if isinstance(json_stats, dict) and json_stats.get("total", 0) > 0:
            # 优先使用 JSON reporter 的结构化统计(更精确、跨版本稳定)
            passed_count = int(json_stats.get("passed", 0))
            failed_count = int(json_stats.get("failed", 0))
            print(f"[Web Report] 使用 JSON reporter 统计: passed={passed_count}, failed={failed_count}")
        else:
            # 回落: 用原有的 stdout 正则解析(向后兼容,Webwright 模式也走这里)
            for line in stdout.splitlines():
                line = line.strip()
                # 匹配 Playwright 总结行: "N passed (Xs)" 或 "N failed"
                m = re.search(r'^(\d+)\s+passed(?:\s*\([^)]*\))?$', line, re.IGNORECASE)
                if m:
                    passed_count = int(m.group(1))
                m = re.search(r'^(\d+)\s+failed', line, re.IGNORECASE)
                if m:
                    failed_count = int(m.group(1))
                # 匹配行首的测试状态标记
                if re.match(r'^[✓✔√]', line):
                    passed_count += 1
                elif re.match(r'^[✘✗×]', line):
                    failed_count += 1
                # 匹配明确的 "[通过]" 或 "[失败]" 格式
                elif re.match(r'^[\[【]\s*通过\s*[\]】]', line) or re.match(r'^通过[：:]', line):
                    passed_count += 1
                elif re.match(r'^[\[【]\s*失败\s*[\]】]', line) or re.match(r'^失败[：:]', line):
                    failed_count += 1
        success = execution_result.get("success", False)
        json_stats = execution_result.get("json_stats")
        if isinstance(json_stats, dict) and json_stats.get("total", 0) > 0:
            # 优先使用 JSON stats 判断（更精确）
            json_failed = int(json_stats.get("failed", 0))
            result_tag = "通过" if json_failed == 0 else "失败"
            print(f"[Web Report] 使用 JSON stats 判断结果: failed={json_failed}, result_tag={result_tag}")
        else:
            result_tag = "通过" if success and failed_count == 0 else "失败" if failed_count > 0 else "执行" if success else "失败"
            print(f"[Web Report] 使用 execution_result 判断结果: success={success}, failed_count={failed_count}, result_tag={result_tag}")

        # 生成更友好的文件名: 功能名-测试结果-时间戳.zip
        safe_name = sub_function.display_name.replace(" ", "-").replace("/", "-").replace("\\", "-") if sub_function.display_name else "未命名功能"
        zip_filename = f"{safe_name}-测试报告-{result_tag}-{timestamp}.zip"
        zip_path = Path(project_root) / zip_filename

        print(f"[Web Report] 打包测试报告: {report_path} -> {zip_path}")

        with zipfile.ZipFile(zip_path, 'w', zipfile.ZIP_DEFLATED) as zipf:
            report_dir = Path(report_path)
            for file_path in report_dir.rglob('*'):
                if file_path.is_file():
                    arcname = file_path.relative_to(report_dir)
                    zipf.write(file_path, arcname)

        # 2. 读取 ZIP 文件内容
        with open(zip_path, 'rb') as f:
            zip_bytes = f.read()

        # 3. 上传到 MinIO（失败时记录日志，附件仍使用原始 object_name）
        object_name = f"web-tests/{project_identifier}/sub-functions/{sub_function_id}/test-report-{timestamp}.zip"
        try:
            MinIOClient.upload_bytes(
                object_name=object_name,
                data=zip_bytes,
                content_type="application/zip"
            )
            print(f"[Web Report] ZIP 报告已上传到 MinIO: {object_name}")
        except Exception as e:
            print(f"[Web Report] MinIO ZIP 上传失败: {e}，将使用本地备份")

        # 3.5 备份到本地文件系统（无论 MinIO 是否成功都执行备份）
        try:
            from app.agents.tools.web.artifacts_tools import _backup_to_local
            backup_path = _backup_to_local(object_name, zip_bytes)
            print(f"[Backup] 测试报告已备份到本地: {backup_path}")
        except Exception as e:
            print(f"[Backup Warning] 本地备份失败: {e}")

        # 4. 创建附件记录
        async with async_session_factory() as session:
            # 生成报告描述
            duration = execution_result.get("duration", 0)
            stdout = execution_result.get("stdout", "")
# pragma: no cover  My80OmFIVnBZMlhscm9ua3VMazZNVTVzYmc9PTpkMTVmOGZjYg==

            # P0-3: 描述行沿用上方已计算的 passed_count/failed_count(JSON 优先,stdout 兜底)
            # 保留旧的宽松计数作为最终兜底,避免 description 完全为空
            if passed_count == 0 and failed_count == 0:
                passed_count = stdout.count("✓") + stdout.count("passed")
                failed_count = stdout.count("✘") + stdout.count("failed")

            description = f"Web 测试报告 - {sub_function.display_name}\n"
            description += f"执行时间: {duration:.2f}秒\n"
            if passed_count > 0 or failed_count > 0:
                description += f"通过: {passed_count} | 失败: {failed_count}"

            # 创建附件（幂等：先查后插，避免重复唯一约束冲突）
            existing_stmt = select(Attachment).where(Attachment.object_name == object_name)
            existing_result = await session.execute(existing_stmt)
            existing_attachment = existing_result.scalar_one_or_none()

            if existing_attachment:
                existing_attachment.file_size = len(zip_bytes)
                existing_attachment.file_name = zip_filename
                existing_attachment.description = description
                existing_attachment.updated_at = datetime.now(timezone.utc)
                session.add(existing_attachment)
                await session.commit()
                await session.refresh(existing_attachment)
                attachment = existing_attachment
                print(f"[Web Report] 更新已存在的附件记录: {attachment.id}")
            else:
                attachment = Attachment(
                    entity_type=AttachmentEntityType.WEB_TEST_REPORT,
                    entity_id=UUID(sub_function_id),
                    project_id=sub_function.project_id,
                    file_name=zip_filename,
                    file_size=len(zip_bytes),
                    content_type="application/zip",
                    object_name=object_name,
                    description=description,
                    created_by="web-agent"
                )
                session.add(attachment)
                await session.commit()
                await session.refresh(attachment)
                print(f"[Web Report] 附件记录已创建: {attachment.id}")

            # 5. 清理临时 ZIP 文件
            try:
                zip_path.unlink()
                print(f"[Web Report] 临时 ZIP 文件已清理: {zip_path}")
            except Exception as e:
                print(f"[Web Report] 清理临时 ZIP 文件失败: {e}")

            # 5. 生成并保存美观的 HTML 报告（新的增强格式）
            # 注意：在清理报告目录之前先生成 HTML 报告
            html_attachment_id = None
            try:
                from app.utils.web_test_report_generator import generate_web_test_report_html

                # 收集截图路径（在清理前读取）
                # Playwright --screenshot=only-on-failure 会将截图放入 test-results/ 目录
                # 同时兼容旧的 screenshots/ 目录（脚本中显式调用 page.screenshot()）
                screenshots = []
                r_dir = Path(report_path)
                if r_dir.exists():
                    # 来源1: Playwright 自动截图目录 (test-results/)
                    test_results_dir = r_dir / "test-results"
                    if test_results_dir.exists():
                        for png_file in test_results_dir.rglob("*.png"):
                            screenshots.append(str(png_file.relative_to(r_dir)))

                    # 来源2: 显式 screenshots/ 子目录
                    screenshots_dir = r_dir / "screenshots"
                    if screenshots_dir.exists():
                        for png_file in screenshots_dir.glob("*.png"):
                            screenshots.append(str(png_file.relative_to(r_dir)))

                    # 来源3: 报告目录根目录下的 png 文件（兼容旧脚本）
                    for png_file in r_dir.glob("*.png"):
                        screenshots.append(str(png_file.name))

                print(f"[Web Report] 收集到 {len(screenshots)} 张截图")

                # 读取日志（在清理前读取）
                logs = None
                log_file = r_dir / "final_script_log.txt" if r_dir.exists() else None
                if log_file and log_file.exists():
                    try:
                        logs = log_file.read_text(encoding="utf-8")
                    except Exception:
                        pass

                # 生成 HTML 报告
                html_content = generate_web_test_report_html(
                    sub_function_name=sub_function.display_name or "未命名功能",
                    sub_function_id=sub_function_id,
                    execution_result=execution_result,
                    project_identifier=project_identifier,
                    screenshots=screenshots if screenshots else None,
                    logs=logs,
                )
                html_bytes = html_content.encode("utf-8")

                # 生成 HTML 文件名（符合前端解析规则: 功能名-测试报告-结果-时间戳.html）
                safe_name = sub_function.display_name.replace(" ", "-").replace("/", "-").replace("\\", "-") if sub_function.display_name else "未命名功能"
                html_filename = f"{safe_name}-测试报告-{result_tag}-{timestamp}.html"

                # 保存 HTML 报告到 MinIO（失败时记录日志，仍使用原始 object_name）
                html_object_name = f"web-tests/{project_identifier}/sub-functions/{sub_function_id}/test-report-html-{timestamp}.html"
                try:
                    MinIOClient.upload_bytes(
                        object_name=html_object_name,
                        data=html_bytes,
                        content_type="text/html"
                    )
                    print(f"[Web Report] HTML 报告已上传到 MinIO: {html_object_name}")
                except Exception as e:
                    print(f"[Web Report] MinIO HTML 上传失败: {e}，将使用本地备份")

                # 备份 HTML 报告（无论 MinIO 是否成功）
                try:
                    from app.agents.tools.web.artifacts_tools import _backup_to_local
                    _backup_to_local(html_object_name, html_bytes)
                except Exception:
                    pass

                # 创建 HTML 报告附件记录（幂等：先查后插）
                html_existing_stmt = select(Attachment).where(Attachment.object_name == html_object_name)
                html_existing_result = await session.execute(html_existing_stmt)
                html_existing = html_existing_result.scalar_one_or_none()

                if html_existing:
                    html_existing.file_size = len(html_bytes)
                    html_existing.file_name = html_filename
                    html_existing.description = f"[HTML] {description}"
                    html_existing.updated_at = datetime.now(timezone.utc)
                    session.add(html_existing)
                    await session.commit()
                    await session.refresh(html_existing)
                    html_attachment = html_existing
                    print(f"[Web Report] 更新已存在的 HTML 附件记录: {html_attachment.id}")
                else:
                    html_attachment = Attachment(
                        entity_type=AttachmentEntityType.WEB_TEST_REPORT,
                        entity_id=UUID(sub_function_id),
                        project_id=sub_function.project_id,
                        file_name=html_filename,
                        file_size=len(html_bytes),
                        content_type="text/html",
                        object_name=html_object_name,
                        description=f"[HTML] {description}",
                        created_by="web-agent"
                    )
                    session.add(html_attachment)
                    await session.commit()
                    await session.refresh(html_attachment)
                    print(f"[Web Report] HTML 报告附件已创建: {html_attachment.id}")
                html_attachment_id = str(html_attachment.id)

                # 备份 HTML 报告
                try:
                    from app.agents.tools.web.artifacts_tools import _backup_to_local
                    _backup_to_local(html_object_name, html_bytes)
                except Exception:
                    pass

            except Exception as html_e:
                print(f"[Web Report] HTML 报告生成失败: {html_e}")
                import traceback
                traceback.print_exc()

            # 6. 清理报告目录（仅在所有保存操作完成后清理）
            # 注意：清理放在 session 上下文外，确保数据库事务已提交
            # 即使 HTML 报告生成失败，ZIP 报告已成功保存，此时清理是安全的
            try:
                shutil.rmtree(report_path)
                print(f"[Web Report] 报告目录已清理: {report_path}")
            except Exception as e:
                print(f"[Web Report] 清理报告目录失败: {e}")

            # P0-3: 清理 JSON reporter 文件(与 HTML 报告目录同级,同一次执行的产物)
            # 命名约定: {report_dir_name}-result.json
            try:
                report_dir_path = Path(report_path)
                json_file = report_dir_path.parent / f"{report_dir_path.name}-result.json"
                if json_file.exists():
                    json_file.unlink()
                    print(f"[Web Report] JSON 报告文件已清理: {json_file}")
            except Exception as e:
                print(f"[Web Report] 清理 JSON 报告文件失败: {e}")

            # 返回 HTML 报告附件 ID（优先）或 ZIP 附件 ID
            return html_attachment_id or str(attachment.id)

    except Exception as e:
        print(f"[Web Report] 保存测试报告失败: {e}")
        import traceback
        traceback.print_exc()
        # 保存失败时，不清理报告目录，保留现场供排查
        print(f"[Web Report] 报告目录保留供排查: {report_path}")
        return None


async def _save_test_result(
    sub_function_id: str,
    project_identifier: str,
    sub_function: WebSubFunction,
    execution_result: Dict[str, Any],
) -> Optional[str]:
    """
    保存测试执行结果到 MinIO 并创建 WEB_TEST_RESULT 附件记录

    Args:
        sub_function_id: 子功能 ID
        project_identifier: 项目标识符
        sub_function: 子功能对象
        execution_result: 执行结果字典

    Returns:
        附件 ID，如果保存失败则返回 None
    """
    try:
        import json
        from datetime import datetime, timezone

        # 构建执行结果摘要
        stdout = execution_result.get("stdout", "")
        stderr = execution_result.get("stderr", "")
        return_code = execution_result.get("return_code", -1)
        duration = execution_result.get("duration", 0)
        success = execution_result.get("success", False)
        json_stats = execution_result.get("json_stats", {})

        # 解析通过/失败数
        passed_count = 0
        failed_count = 0
        skipped_count = 0

        if isinstance(json_stats, dict) and json_stats.get("total", 0) > 0:
            passed_count = int(json_stats.get("passed", 0))
            failed_count = int(json_stats.get("failed", 0))
            skipped_count = int(json_stats.get("skipped", 0))
        else:
            # 从 stdout 解析
            import re
            for line in stdout.splitlines():
                line = line.strip()
                m = re.search(r'^(\d+)\s+passed', line, re.IGNORECASE)
                if m:
                    passed_count = int(m.group(1))
                m = re.search(r'^(\d+)\s+failed', line, re.IGNORECASE)
                if m:
                    failed_count = int(m.group(1))
                m = re.search(r'^(\d+)\s+skipped', line, re.IGNORECASE)
                if m:
                    skipped_count = int(m.group(1))

        # 构建结果内容（JSON 格式）
        result_data = {
            "success": success,
            "return_code": return_code,
            "duration": duration,
            "passed": passed_count,
            "failed": failed_count,
            "skipped": skipped_count,
            "total": passed_count + failed_count + skipped_count,
            "stdout_preview": stdout[:5000] if stdout else "",
            "stderr_preview": stderr[:2000] if stderr else "",
            "executed_at": datetime.now().isoformat(),
        }

        result_json = json.dumps(result_data, ensure_ascii=False, indent=2)
        result_bytes = result_json.encode("utf-8")

        # 生成文件名和对象名
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        safe_name = sub_function.display_name.replace(" ", "-").replace("/", "-").replace("\\", "-") if sub_function.display_name else "未命名功能"
        result_filename = f"{safe_name}-执行结果-{timestamp}.json"
        object_name = f"web-tests/{project_identifier}/sub-functions/{sub_function_id}/test-result-{timestamp}.json"

        # 上传到 MinIO
        MinIOClient.upload_bytes(
            object_name=object_name,
            data=result_bytes,
            content_type="application/json"
        )

        # 创建附件记录
        async with async_session_factory() as session:
            # 幂等：先查后插
            existing_stmt = select(Attachment).where(Attachment.object_name == object_name)
            existing_result = await session.execute(existing_stmt)
            existing_attachment = existing_result.scalar_one_or_none()

            description = f"Web 测试执行结果 - {sub_function.display_name}\n"
            description += f"状态: {'通过' if success else '失败'}\n"
            if passed_count > 0 or failed_count > 0:
                description += f"通过: {passed_count} | 失败: {failed_count} | 跳过: {skipped_count}"

            if existing_attachment:
                existing_attachment.file_size = len(result_bytes)
                existing_attachment.file_name = result_filename
                existing_attachment.description = description
                existing_attachment.updated_at = datetime.now(timezone.utc)
                session.add(existing_attachment)
                await session.commit()
                await session.refresh(existing_attachment)
                attachment = existing_attachment
                print(f"[Web Result] 更新已存在的执行结果附件: {attachment.id}")
            else:
                attachment = Attachment(
                    entity_type=AttachmentEntityType.WEB_TEST_RESULT,
                    entity_id=UUID(sub_function_id),
                    project_id=sub_function.project_id,
                    file_name=result_filename,
                    file_size=len(result_bytes),
                    content_type="application/json",
                    object_name=object_name,
                    description=description,
                    created_by="web-agent"
                )
                session.add(attachment)
                await session.commit()
                await session.refresh(attachment)
                print(f"[Web Result] 执行结果附件已创建: {attachment.id}")

            # 备份到本地
            try:
                from app.agents.tools.web.artifacts_tools import _backup_to_local
                _backup_to_local(object_name, result_bytes)
            except Exception:
                pass

            return str(attachment.id)

    except Exception as e:
        print(f"[Web Result] 保存执行结果失败: {e}")
        import traceback
        traceback.print_exc()
        return None


@tool
async def get_test_execution_status(
    execution_id: str
) -> str:
    """
    获取测试执行状态（占位符，未来可扩展为异步执行查询）

    Args:
        execution_id: 执行 ID

    Returns:
        JSON 格式的执行状态
    """
    return json.dumps({
        "success": True,
        "execution_id": execution_id,
        "status": "completed",
        "message": "当前版本仅支持同步执行，不支持异步状态查询"
    }, ensure_ascii=False, indent=2)
