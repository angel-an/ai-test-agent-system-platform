"""消息序列验证中间件 - 修复 OpenAI API tool_calls 序列错误。

此模块提供共享的消息序列验证功能，可被所有 Agent 使用。
问题背景：summarization/truncation 可能截断 tool_call 对，导致 OpenAI API 400 错误。
"""

import logging
from typing import Any

from langchain.agents.middleware import AgentMiddleware
from langchain_core.messages import AIMessage
from langgraph.types import Command

logger = logging.getLogger(__name__)


def _clear_tool_calls(msg):
    """彻底清除 AIMessage 中的所有 tool_calls 痕迹。"""
    msg_copy = msg.model_copy()
    msg_copy.tool_calls = []

    # 清除 additional_kwargs 中的 tool_calls
    if msg_copy.additional_kwargs:
        additional = dict(msg_copy.additional_kwargs)
        additional.pop("tool_calls", None)
        msg_copy.additional_kwargs = additional

    # 清除 response_metadata 中的 tool_calls
    if hasattr(msg_copy, "response_metadata") and msg_copy.response_metadata:
        metadata = dict(msg_copy.response_metadata)
        metadata.pop("tool_calls", None)
        msg_copy.response_metadata = metadata

    return msg_copy


def validate_message_sequence(messages: list) -> list:
    """验证并修复消息序列，确保符合 OpenAI API 要求。

    OpenAI API 要求：assistant message 带有 tool_calls 时，
    必须紧跟对应的 tool messages（每个 tool_call_id 对应一个）。

    这个函数检查并修复因 summarization/truncation 导致的消息序列断裂。

    修复策略：
    1. 如果 AIMessage 的 tool_calls 缺少对应的 ToolMessage，
       将该 AIMessage 替换为普通消息（清除 tool_calls），
       并移除后续所有对应的 ToolMessage（即使它们存在）。
    2. 如果 ToolMessage 缺少对应的 AIMessage（孤儿 ToolMessage），
       移除该 ToolMessage。
    """
    if not messages:
        return messages

    from langchain_core.messages import AIMessage, ToolMessage

    fixed_messages = []
    i = 0
    # 跟踪哪些 tool_call_ids 对应的 AIMessage 被保留了
    kept_aimessage_tool_ids = set()

    while i < len(messages):
        msg = messages[i]

        # 处理带 tool_calls 的 AIMessage
        if isinstance(msg, AIMessage) and msg.tool_calls:
            # 收集所有有效的 tool_call_ids（必须有 id）
            expected_tool_ids = {
                tc.get("id") for tc in msg.tool_calls if tc.get("id")
            }

            # 如果没有有效的 tool_call_ids，直接清除 tool_calls
            if not expected_tool_ids:
                fixed_messages.append(_clear_tool_calls(msg))
                i += 1
                continue

            # 检查后续消息是否是对应的 ToolMessage
            found_tool_ids = set()
            j = i + 1
            while j < len(messages) and isinstance(messages[j], ToolMessage):
                tool_msg = messages[j]
                if tool_msg.tool_call_id in expected_tool_ids:
                    found_tool_ids.add(tool_msg.tool_call_id)
                j += 1

            # 如果缺少对应的 ToolMessage，移除该 AIMessage 的 tool_calls
            if found_tool_ids != expected_tool_ids:
                fixed_messages.append(_clear_tool_calls(msg))
                i += 1
                continue
            else:
                # 保留这个 AIMessage，记录它的 tool_call_ids
                kept_aimessage_tool_ids.update(expected_tool_ids)

        fixed_messages.append(msg)
        i += 1

    # 第二遍：移除所有孤儿 ToolMessage（没有对应保留的 AIMessage）
    final_messages = []
    for msg in fixed_messages:
        if isinstance(msg, ToolMessage):
            if msg.tool_call_id not in kept_aimessage_tool_ids:
                # 跳过这个孤儿 ToolMessage
                continue
        final_messages.append(msg)

    return final_messages


class MessageSequenceValidationMiddleware(AgentMiddleware):
    """消息序列验证中间件 - 确保 tool_calls 消息序列符合 OpenAI API 要求。

    使用 abefore_model 钩子，在 summarization 截断消息之后、模型调用之前执行，
    修复因 summarization 截断导致的消息序列断裂。

    问题背景：
    - summarization 中间件在 awrap_model_call 中截断消息（request.override）
    - 用户自定义中间件在 summarization 之后注册，其 awrap_model_call 在 summarization 之前执行
    - 因此必须使用 abefore_model（作为 graph node 在 model 之前执行），
      确保在 summarization 之后、模型调用之前验证消息序列
    """

    async def abefore_model(self, state, runtime) -> dict[str, Any] | Command[Any] | None:
        """在模型调用前验证并修复消息序列。

        使用 RemoveMessage 清除所有旧消息，然后添加修复后的消息，
        确保 add_messages reducer 正确替换消息列表而非追加。
        """
        messages = state.get("messages", [])
        if not messages:
            return None

        # 验证并修复消息序列
        fixed_messages = validate_message_sequence(messages)

        # 检查是否有变化：消息数量变化 或 某条消息的 tool_calls 被清除
        has_changes = len(fixed_messages) != len(messages)
        if not has_changes:
            # 检查是否有 AIMessage 的 tool_calls 被清除
            for orig, fixed in zip(messages, fixed_messages):
                if (
                    isinstance(orig, AIMessage)
                    and isinstance(fixed, AIMessage)
                    and orig.tool_calls
                    and not fixed.tool_calls
                ):
                    has_changes = True
                    break

        if has_changes:
            removed_count = len(messages) - len(fixed_messages)
            if removed_count > 0:
                logger.warning(
                    "Message sequence fixed before model call: %d -> %d messages "
                    "(%d orphan messages removed). "
                    "This indicates summarization/truncation broke tool call pairs.",
                    len(messages),
                    len(fixed_messages),
                    removed_count,
                )
            else:
                logger.warning(
                    "Message sequence fixed before model call: cleared tool_calls from "
                    "AIMessage(s) with missing tool responses. "
                    "This indicates summarization/truncation broke tool call pairs."
                )
            # 使用 RemoveMessage 清除所有旧消息，然后添加修复后的消息
            # 这样可以确保 add_messages reducer 正确替换消息列表
            from langgraph.graph.message import RemoveMessage

            return {
                "messages": [
                    RemoveMessage(id="all"),  # 清除所有旧消息
                    *fixed_messages,  # 添加修复后的消息
                ]
            }

        return None
