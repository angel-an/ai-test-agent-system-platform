"""工具终局守卫包装器 —— 执行治理层 P0-1

与 tool_error_handler.py 的本质区别：
- tool_error_handler：把异常转成"可换思路重试"的错误消息（适合业务错误）；
- tool_guard：命中安全策略直接返回 final 拒绝，无重试路径（借鉴 dsh guard 的"拒绝即终局"）。

用法（与现有 wrap_tools_with_error_handling 同款模式，原地包装、幂等）：
    tools = wrap_tools_with_guard(tools)

包装后：
- 黑名单工具（browser_evaluate / browser_run_code_unsafe 等）→ final 拒绝；
- 导航类工具（browser_navigate 等）→ 先做 scheme 门禁（默认仅 http/https/about，
  file:/data:/javascript: 等一律拒绝）+ http(s) origin 白名单校验，出圈 final 拒绝；
- 其余调用原样透传。
"""

from __future__ import annotations

import json
import logging
from functools import wraps
from typing import Any, Callable

from app.agents.tool_policy import (
    check_navigation_target,
    extract_navigation_url,
    is_tool_denied,
)

logger = logging.getLogger(__name__)


def _final_denial(tool_name: str, reason: str) -> str:
    """构造终局拒绝响应：final=true，不含任何"可换思路再试"的暗示。"""
    return json.dumps(
        {
            "success": False,
            "final": True,
            "guard": "tool_guard",
            "tool": tool_name,
            "reason": reason,
            "message": "该工具调用被安全策略终局拒绝，请勿重试或更换参数重试。",
        },
        ensure_ascii=False,
    )


def _guard_args(tool_name: str, args: tuple, kwargs: dict) -> str | None:
    """返回拒绝原因；None 表示放行。"""
    if is_tool_denied(tool_name):
        return f"工具 '{tool_name}' 被安全策略禁用（tool_policy.DENIED_TOOLS）"
    # 导航类工具：校验 scheme + origin（兼容位置参数首参为 URL 的调用形态）
    merged = dict(kwargs)
    if args and isinstance(args[0], str):
        merged.setdefault("url", args[0])
    nav_url = extract_navigation_url(tool_name, merged)
    if nav_url is not None:
        allowed, reason = check_navigation_target(nav_url)
        if not allowed:
            return f"导航目标被安全策略拒绝: {reason}"
    return None


def _is_guarded(tool: Any) -> bool:
    """判断工具是否已被守卫包装（幂等性，避免重复包装层层嵌套）。"""
    return bool(getattr(getattr(tool, "_run", None), "_is_guard", False))


def wrap_tool_with_guard(tool: Any) -> Any:
    """包装单个工具：命中策略时返回 final 拒绝，不进入原始执行。

    原地包装（与 web_mcp 现有 handle_tool_error 包装一致，避免拷贝破坏
    MCP 会话绑定）；通过函数标记 _is_guard 保证幂等。
    """
    name = getattr(tool, "name", None) or "?"
    if _is_guarded(tool):
        return tool

    original_run: Callable = tool._run
    original_arun: Callable = tool._arun

    @wraps(original_run)
    def guarded_run(*args: Any, **kwargs: Any) -> Any:
        denial = _guard_args(name, args, kwargs)
        if denial is not None:
            logger.warning("[ToolGuard] %s -> FINAL DENY: %s", name, denial)
            return _final_denial(name, denial)
        return original_run(*args, **kwargs)

    @wraps(original_arun)
    async def guarded_arun(*args: Any, **kwargs: Any) -> Any:
        denial = _guard_args(name, args, kwargs)
        if denial is not None:
            logger.warning("[ToolGuard] %s -> FINAL DENY: %s", name, denial)
            return _final_denial(name, denial)
        return await original_arun(*args, **kwargs)

    # 标记已包装（挂在函数上，避免给 pydantic 模型塞未知字段）
    guarded_run._is_guard = True  # type: ignore[attr-defined]
    guarded_arun._is_guard = True  # type: ignore[attr-defined]

    tool._run = guarded_run
    tool._arun = guarded_arun
    return tool


def wrap_tools_with_guard(tools: list[Any]) -> list[Any]:
    """批量包装工具。"""
    return [wrap_tool_with_guard(t) for t in tools]
