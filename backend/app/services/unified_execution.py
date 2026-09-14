# -*- coding: utf-8 -*-
"""P1: 统一执行入口 — 所有执行路径（HTTP API、Agent Tool、编排）必须经过此入口。

目标：确保任何执行前都完成预检，禁止绕过。
"""

from __future__ import annotations

import functools
import logging
from pathlib import Path
from typing import Any, Callable, Coroutine, Literal

from app.test_platform.runtime_context import RunContext
from app.test_platform.execution_gates import GateConfig
from app.services.execution_preflight import ExecutionPreflight, PreflightBlockedError

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# 统一执行入口
# ---------------------------------------------------------------------------

async def execute_with_preflight(
    context: RunContext,
    track: Literal["api", "web", "scenario"],
    executor: Callable[[], Coroutine[Any, Any, dict[str, Any]]],
    gate_config: GateConfig | None = None,
) -> dict[str, Any]:
    """统一执行入口 — 先预检，通过后执行。

    所有执行路径（HTTP API、Agent Tool、编排）必须调用此函数，
    禁止在任何地方直接执行而不经过预检。

    Args:
        context: 统一运行上下文（必须包含 project_id、run_id）
        track: 执行轨道类型
        executor: 实际执行函数（预检通过后调用）
        gate_config: 可选的门控配置（默认从环境变量读取）

    Returns:
        执行结果字典，预检失败时返回阻断信息

    Example:
        >>> result = await execute_with_preflight(
        ...     context=run_context,
        ...     track="web",
        ...     executor=lambda: run_web_test(context, base_url),
        ... )
    """
    # 1. 预检（硬阻断，不降级）
    _gate = gate_config or GateConfig.from_env()
    if context.is_live_mode():
        if track == "api":
            _gate = GateConfig(
                live_api=True,
                live_web=_gate.live_web,
                run_risky_negative=_gate.run_risky_negative,
                auth_negative=_gate.auth_negative,
                fail_closed=_gate.fail_closed,
            )
        elif track == "web":
            _gate = GateConfig(
                live_api=_gate.live_api,
                live_web=True,
                run_risky_negative=_gate.run_risky_negative,
                auth_negative=_gate.auth_negative,
                fail_closed=_gate.fail_closed,
            )

    preflight = ExecutionPreflight(gate_config=_gate)
    try:
        preflight_result = preflight.require_passed(context, track=track)
        logger.info(
            "[execute_with_preflight] 预检通过: run_id=%s, track=%s, mode=%s",
            context.run_id, track, context.mode,
        )
    except PreflightBlockedError as e:
        logger.warning(
            "[execute_with_preflight] 预检阻断: run_id=%s, track=%s, reason=%s",
            context.run_id, track, e.result.reason_code,
        )
        return {
            "success": False,
            "status": "blocked",
            "run_id": context.run_id,
            "track": track,
            "mode": context.mode,
            "preflight": e.result.to_dict(),
            "message": f"执行被预检阻断: {e.result.message}",
        }

    # 2. 真正执行
    try:
        result = await executor()
        result["_preflight"] = preflight_result.to_dict()
        return result
    except Exception as e:
        logger.exception(
            "[execute_with_preflight] 执行异常: run_id=%s, track=%s",
            context.run_id, track,
        )
        return {
            "success": False,
            "status": "failed",
            "run_id": context.run_id,
            "track": track,
            "mode": context.mode,
            "error": str(e),
            "_preflight": preflight_result.to_dict(),
        }


def require_preflight(track: Literal["api", "web", "scenario"]):
    """装饰器：为 Agent Tool 函数添加预检门禁。

    使用方式：
        @require_preflight("web")
        async def execute_web_script(..., run_id: str = None, ...):
            # 预检通过后才会执行此函数体
            ...

    Args:
        track: 执行轨道类型

    Note:
        被装饰函数必须接收 run_id 和 project_identifier 参数，
        用于构建 RunContext。如果缺少这些参数，预检将失败。
    """
    def decorator(func: Callable) -> Callable:
        @functools.wraps(func)
        async def wrapper(*args, **kwargs):
            # 从 kwargs 中提取构建 RunContext 所需的参数
            project_id = kwargs.get("project_identifier", "")
            run_id = kwargs.get("run_id", "")
            artifact_id = kwargs.get("artifact_id", "") or kwargs.get("script_id", "")
            artifact_version = kwargs.get("artifact_version", "")
            mode = kwargs.get("mode", "safe")

            # 如果没有 run_id，直接阻断（禁止自动推断）
            if not run_id:
                logger.warning(
                    "[require_preflight] 阻断: 缺少 run_id, tool=%s, project=%s",
                    func.__name__, project_id,
                )
                return {
                    "success": False,
                    "status": "blocked",
                    "reason_code": "MISSING_RUN_ID",
                    "message": "执行被阻断: 缺少 run_id。所有执行必须显式指定 run_id，禁止自动推断。",
                }

            # 构建 RunContext
            try:
                context = RunContext(
                    project_id=project_id or "unknown",
                    run_id=run_id,
                    artifact_id=artifact_id or None,
                    artifact_version=artifact_version or None,
                    mode=mode if mode in ("safe", "live") else "safe",
                )
            except ValueError as e:
                return {
                    "success": False,
                    "status": "blocked",
                    "reason_code": "INVALID_CONTEXT",
                    "message": f"运行上下文无效: {e}",
                }

            # 调用统一执行入口
            return await execute_with_preflight(
                context=context,
                track=track,
                executor=lambda: func(*args, **kwargs),
            )

        return wrapper
    return decorator


__all__ = [
    "execute_with_preflight",
    "require_preflight",
]
