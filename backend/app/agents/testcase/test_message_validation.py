"""Tests for message sequence validation in testcase agent."""

import sys
from pathlib import Path

# Add backend to path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from langchain_core.messages import AIMessage, ToolMessage, HumanMessage

from app.agents.middleware import (
    MessageSequenceValidationMiddleware,
    validate_message_sequence as _validate_message_sequence,
)


def _make_ai_with_tool_calls(tool_call_ids: list[str]) -> AIMessage:
    """Helper to create AIMessage with tool_calls in correct format."""
    tool_calls = [
        {"name": "test_tool", "args": {}, "id": tc_id}
        for tc_id in tool_call_ids
    ]
    return AIMessage(content="", tool_calls=tool_calls)


class TestValidateMessageSequence:
    def test_empty_messages(self):
        assert _validate_message_sequence([]) == []

    def test_no_tool_calls(self):
        messages = [
            HumanMessage(content="Hello"),
            AIMessage(content="Hi there"),
        ]
        result = _validate_message_sequence(messages)
        assert len(result) == 2
        assert result[0].content == "Hello"
        assert result[1].content == "Hi there"

    def test_valid_tool_call_sequence(self):
        """完整的 tool_calls 序列应该保持不变。"""
        messages = [
            HumanMessage(content="Use tool"),
            _make_ai_with_tool_calls(["call_1"]),
            ToolMessage(content="Result", tool_call_id="call_1"),
        ]
        result = _validate_message_sequence(messages)
        assert len(result) == 3
        assert isinstance(result[1], AIMessage)
        assert result[1].tool_calls  # tool_calls 应该保留
        assert isinstance(result[2], ToolMessage)

    def test_orphan_tool_message(self):
        """没有对应 AIMessage 的 ToolMessage 应该被移除。"""
        messages = [
            HumanMessage(content="Hello"),
            ToolMessage(content="Orphan result", tool_call_id="call_orphan"),
            AIMessage(content="Response"),
        ]
        result = _validate_message_sequence(messages)
        assert len(result) == 2
        assert isinstance(result[0], HumanMessage)
        assert isinstance(result[1], AIMessage)

    def test_aimessage_missing_tool_responses(self):
        """AIMessage 声明了 tool_calls 但缺少对应 ToolMessage 时，应清除 tool_calls。"""
        messages = [
            HumanMessage(content="Use tool"),
            _make_ai_with_tool_calls(["call_1"]),
            # 缺少 ToolMessage!
            AIMessage(content="Next response"),
        ]
        result = _validate_message_sequence(messages)
        assert len(result) == 3
        # AIMessage 的 tool_calls 应该被清除
        assert isinstance(result[1], AIMessage)
        assert not result[1].tool_calls

    def test_aimessage_with_partial_tool_responses(self):
        """AIMessage 声明了多个 tool_calls 但只有部分有 ToolMessage 回应时，应清除所有 tool_calls 并移除对应的 ToolMessage。"""
        messages = [
            HumanMessage(content="Use tools"),
            _make_ai_with_tool_calls(["call_1", "call_2"]),
            ToolMessage(content="Result 1", tool_call_id="call_1"),
            # 缺少 call_2 的 ToolMessage!
        ]
        result = _validate_message_sequence(messages)
        # call_1 的 ToolMessage 也应该被移除（因为 AIMessage 被清除了）
        assert len(result) == 2
        # AIMessage 的 tool_calls 应该被清除
        assert isinstance(result[1], AIMessage)
        assert not result[1].tool_calls

    def test_complex_sequence_with_orphans(self):
        """复杂场景：既有有效的 tool_calls，也有孤儿的。"""
        messages = [
            HumanMessage(content="Start"),
            # 有效的 tool_calls 对
            _make_ai_with_tool_calls(["call_valid"]),
            ToolMessage(content="Valid result", tool_call_id="call_valid"),
            # 无效的 tool_calls（缺少 ToolMessage）
            _make_ai_with_tool_calls(["call_invalid"]),
            # 孤儿的 ToolMessage
            ToolMessage(content="Orphan", tool_call_id="call_orphan"),
            AIMessage(content="Final response"),
        ]
        result = _validate_message_sequence(messages)
        assert len(result) == 5
        # 有效的应该保留
        assert isinstance(result[1], AIMessage)
        assert result[1].tool_calls
        assert isinstance(result[2], ToolMessage)
        assert result[2].tool_call_id == "call_valid"
        # 无效的应该被清除
        assert isinstance(result[3], AIMessage)
        assert not result[3].tool_calls
        # 孤儿的 ToolMessage 应该被移除
        assert isinstance(result[4], AIMessage)
        assert result[4].content == "Final response"

    def test_multiple_valid_tool_calls(self):
        """多个有效的 tool_calls 对。"""
        messages = [
            HumanMessage(content="Use tools"),
            _make_ai_with_tool_calls(["call_1", "call_2"]),
            ToolMessage(content="Result 1", tool_call_id="call_1"),
            ToolMessage(content="Result 2", tool_call_id="call_2"),
            AIMessage(content="Done"),
        ]
        result = _validate_message_sequence(messages)
        assert len(result) == 5
        assert result[1].tool_calls
        assert isinstance(result[2], ToolMessage)
        assert isinstance(result[3], ToolMessage)


class TestMessageSequenceValidationMiddleware:
    """测试 MessageSequenceValidationMiddleware 的 abefore_model 方法。"""

    async def _run_abefore_model(self, middleware, state):
        """Helper to run abefore_model asynchronously."""
        return await middleware.abefore_model(state, None)

    def test_abefore_model_with_valid_sequence(self):
        """有效的消息序列应该返回 None（不修改）。"""
        import asyncio

        middleware = MessageSequenceValidationMiddleware()
        state = {
            "messages": [
                HumanMessage(content="Use tool"),
                _make_ai_with_tool_calls(["call_1"]),
                ToolMessage(content="Result", tool_call_id="call_1"),
            ]
        }
        result = asyncio.run(self._run_abefore_model(middleware, state))
        assert result is None

    def test_abefore_model_with_orphan_tool_message(self):
        """孤儿 ToolMessage 应该被移除。"""
        import asyncio

        middleware = MessageSequenceValidationMiddleware()
        state = {
            "messages": [
                HumanMessage(content="Hello"),
                ToolMessage(content="Orphan", tool_call_id="call_orphan"),
                AIMessage(content="Response"),
            ]
        }
        result = asyncio.run(self._run_abefore_model(middleware, state))
        assert result is not None
        assert "messages" in result
        # 应该包含 RemoveMessage + 修复后的消息
        from langgraph.graph.message import RemoveMessage

        assert any(isinstance(m, RemoveMessage) for m in result["messages"])
        # 过滤掉 RemoveMessage，检查剩余消息
        non_remove = [m for m in result["messages"] if not isinstance(m, RemoveMessage)]
        assert len(non_remove) == 2
        assert isinstance(non_remove[0], HumanMessage)
        assert isinstance(non_remove[1], AIMessage)

    def test_abefore_model_with_missing_tool_response(self):
        """缺少 ToolMessage 时应该清除 AIMessage 的 tool_calls。"""
        import asyncio

        middleware = MessageSequenceValidationMiddleware()
        ai_msg = _make_ai_with_tool_calls(["call_1"])
        state = {
            "messages": [
                HumanMessage(content="Use tool"),
                ai_msg,
                AIMessage(content="Next"),
            ]
        }
        result = asyncio.run(self._run_abefore_model(middleware, state))
        assert result is not None
        assert "messages" in result
        from langgraph.graph.message import RemoveMessage

        assert any(isinstance(m, RemoveMessage) for m in result["messages"])
        non_remove = [m for m in result["messages"] if not isinstance(m, RemoveMessage)]
        assert len(non_remove) == 3
        # AIMessage 的 tool_calls 应该被清除
        assert not non_remove[1].tool_calls

    def test_abefore_model_empty_messages(self):
        """空消息列表应该返回 None。"""
        import asyncio

        middleware = MessageSequenceValidationMiddleware()
        state = {"messages": []}
        result = asyncio.run(self._run_abefore_model(middleware, state))
        assert result is None


if __name__ == "__main__":
    import pytest
    pytest.main([__file__, "-v"])
