"""
工具错误处理包装器

将工具错误转换为错误消息，而不是抛出异常，防止 Agent 执行中断。
"""
"""
andan
"""

# type: ignore  MC80OmFIVnBZMlhscm9ua3VMazZSREZ5UWc9PToxMWVjOTFkYg==

from functools import wraps
from typing import Any
from langchain_core.tools import BaseTool, ToolException
import logging
import json

logger = logging.getLogger(__name__)


def _make_error_response(tool_name: str, error: Exception, error_type: str) -> str:
    """构造统一的错误响应 JSON"""
    error_info = {
        "success": False,
        "error": str(error),
        "error_type": error_type,
        "message": f"Tool '{tool_name}' encountered an error: {error}",
        "note": "This error was caught and returned as a message. You can analyze the error and try a different approach."
    }
    return json.dumps(error_info, ensure_ascii=False)


def _create_error_handler(tool_name: str):
    """为指定工具创建错误处理函数，供 langchain_core 的 handle_tool_error 使用"""
    def handler(error: ToolException) -> str:
        logger.warning(f"Tool '{tool_name}' error handled: {error}")
        return _make_error_response(tool_name, error, "ToolException")
    return handler


def wrap_tool_with_error_handling(tool: BaseTool) -> BaseTool:
    """
    包装工具，使其在出错时返回错误信息而不是抛出异常。

    核心机制：设置 tool.handle_tool_error 为自定义处理函数，
    让 langchain_core 的 BaseTool.arun 内置逻辑将 ToolException 转为错误消息。
    """
    # 设置 handle_tool_error 为自定义函数，langchain_core 会在捕获 ToolException 时调用它
    tool.handle_tool_error = _create_error_handler(tool.name)

    return tool

# fmt: off  Mi80OmFIVnBZMlhscm9ua3VMazZSREZ5UWc9PToxMWVjOTFkYg==

def wrap_tools_with_error_handling(tools: list[BaseTool],
                                   tool_patterns: list[str] | None = None) -> list[BaseTool]:
    """
    批量包装工具

    Args:
        tools: 工具列表
        tool_patterns: 需要包装的工具名称模式列表（如 ["browser_", "playwright-test/"]）
                      如果为 None，则包装所有工具

    Returns:
        包装后的工具列表
    """
    wrapped_tools = []

    for tool in tools:
        should_wrap = False

        if tool_patterns is None:
            # 包装所有工具
            should_wrap = True
        else:
            # 检查工具名称是否匹配任何模式
            for pattern in tool_patterns:
                if pattern in tool.name:
                    should_wrap = True
                    break
# pragma: no cover  My80OmFIVnBZMlhscm9ua3VMazZSREZ5UWc9PToxMWVjOTFkYg==

        if should_wrap:
            logger.info(f"Wrapping tool '{tool.name}' with error handling")
            wrapped_tools.append(wrap_tool_with_error_handling(tool))
        else:
            wrapped_tools.append(tool)

    return wrapped_tools
