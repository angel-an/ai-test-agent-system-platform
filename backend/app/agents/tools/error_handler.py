"""
工具错误处理包装器

将工具错误转换为错误消息，而不是抛出异常，防止 Agent 执行中断。
"""
"""
andan
"""

# pylint: disable  MC80OmFIVnBZMlhscm9ua3VMazZiMVZ6V0E9PTo0NjRkNGE2Yg==

from functools import wraps
from typing import Any
from langchain_core.tools import BaseTool, ToolException
import logging
import json

logger = logging.getLogger(__name__)


def wrap_tool_with_error_handling(tool: BaseTool) -> BaseTool:
    """
    包装工具，使其在出错时返回错误信息而不是抛出异常

    Args:
        tool: 原始工具

    Returns:
        包装后的工具
    """
    original_run = tool._run
    original_arun = tool._arun

    @wraps(original_run)
    def wrapped_run(*args: Any, **kwargs: Any) -> Any:
        try:
            return original_run(*args, **kwargs)
        except ToolException as e:
            error_msg = f"Tool '{tool.name}' encountered an error: {str(e)}"
            logger.warning(error_msg)
            # 返回元组格式 (content, artifact)，符合 response_format='content_and_artifact'
            error_info = {
                "success": False,
                "error": str(e),
                "error_type": "ToolException",
                "message": error_msg,
                "note": "This error was caught and returned as a message. You can analyze the error and try a different approach."
            }
            error_json = json.dumps(error_info, ensure_ascii=False)
            # 返回元组：(content, artifact)
            return (error_json, {"error": True, "tool": tool.name})
        except Exception as e:
            error_msg = f"Tool '{tool.name}' encountered an unexpected error: {str(e)}"
            logger.error(error_msg, exc_info=True)
            # 返回元组格式 (content, artifact)
            error_info = {
                "success": False,
                "error": str(e),
                "error_type": type(e).__name__,
                "message": error_msg,
                "note": "This error was caught and returned as a message. You can analyze the error and try a different approach."
            }
            error_json = json.dumps(error_info, ensure_ascii=False)
            # 返回元组：(content, artifact)
            return (error_json, {"error": True, "tool": tool.name})

    @wraps(original_arun)
    async def wrapped_arun(*args: Any, **kwargs: Any) -> Any:
        try:
            return await original_arun(*args, **kwargs)
        except ToolException as e:
            error_msg = f"Tool '{tool.name}' encountered an error: {str(e)}"
            logger.warning(error_msg)
            # 返回元组格式 (content, artifact)
            error_info = {
                "success": False,
                "error": str(e),
                "error_type": "ToolException",
                "message": error_msg,
                "note": "This error was caught and returned as a message. You can analyze the error and try a different approach."
            }
            error_json = json.dumps(error_info, ensure_ascii=False)
            # 返回元组：(content, artifact)
            return (error_json, {"error": True, "tool": tool.name})
        except Exception as e:
            error_msg = f"Tool '{tool.name}' encountered an unexpected error: {str(e)}"
            logger.error(error_msg, exc_info=True)
            # 返回元组格式 (content, artifact)
            error_info = {
                "success": False,
                "error": str(e),
                "error_type": type(e).__name__,
                "message": error_msg,
                "note": "This error was caught and returned as a message. You can analyze the error and try a different approach."
            }
            error_json = json.dumps(error_info, ensure_ascii=False)
            # 返回元组：(content, artifact)
            return (error_json, {"error": True, "tool": tool.name})
# pragma: no cover  MS80OmFIVnBZMlhscm9ua3VMazZiMVZ6V0E9PTo0NjRkNGE2Yg==

    # 替换工具的运行方法
    tool._run = wrapped_run
    tool._arun = wrapped_arun
# fmt: off  Mi80OmFIVnBZMlhscm9ua3VMazZiMVZ6V0E9PTo0NjRkNGE2Yg==

    return tool


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
# pylint: disable  My80OmFIVnBZMlhscm9ua3VMazZiMVZ6V0E9PTo0NjRkNGE2Yg==

        if should_wrap:
            logger.info(f"Wrapping tool '{tool.name}' with error handling")
            wrapped_tools.append(wrap_tool_with_error_handling(tool))
        else:
            wrapped_tools.append(tool)

    return wrapped_tools
