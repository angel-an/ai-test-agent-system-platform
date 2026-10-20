"""Tests for message sequence validation middleware."""

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
    """测试 MessageSequenceValidationMiddleware 的 awrap_model_call 方法。"""

    async def _run_awrap_model_call(self, middleware, request):
        """Helper to run awrap_model_call asynchronously."""
        async def mock_handler(req):
            return req

        return await middleware.awrap_model_call(request, mock_handler)

    def _make_request(self, messages):
        """Helper to create a ModelRequest-like object."""
        from dataclasses import dataclass
        from typing import Any

        @dataclass
        class MockRequest:
            messages: list
            system_message: Any = None
            runtime: Any = None

            def override(self, **kwargs):
                return MockRequest(
                    messages=kwargs.get("messages", self.messages),
                    system_message=kwargs.get("system_message", self.system_message),
                    runtime=kwargs.get("runtime", self.runtime),
                )

        return MockRequest(messages=messages)

    def test_awrap_model_call_with_valid_sequence(self):
        """有效的消息序列应该不修改。"""
        import asyncio

        middleware = MessageSequenceValidationMiddleware()
        request = self._make_request([
            HumanMessage(content="Use tool"),
            _make_ai_with_tool_calls(["call_1"]),
            ToolMessage(content="Result", tool_call_id="call_1"),
        ])
        result = asyncio.run(self._run_awrap_model_call(middleware, request))
        # 消息应该保持不变
        assert len(result.messages) == 3
        assert result.messages[1].tool_calls

    def test_awrap_model_call_with_orphan_tool_message(self):
        """孤儿 ToolMessage 应该被移除。"""
        import asyncio

        middleware = MessageSequenceValidationMiddleware()
        request = self._make_request([
            HumanMessage(content="Hello"),
            ToolMessage(content="Orphan", tool_call_id="call_orphan"),
            AIMessage(content="Response"),
        ])
        result = asyncio.run(self._run_awrap_model_call(middleware, request))
        assert len(result.messages) == 2
        assert isinstance(result.messages[0], HumanMessage)
        assert isinstance(result.messages[1], AIMessage)

    def test_awrap_model_call_with_missing_tool_response(self):
        """缺少 ToolMessage 时应该清除 AIMessage 的 tool_calls。"""
        import asyncio

        middleware = MessageSequenceValidationMiddleware()
        request = self._make_request([
            HumanMessage(content="Use tool"),
            _make_ai_with_tool_calls(["call_1"]),
            AIMessage(content="Next"),
        ])
        result = asyncio.run(self._run_awrap_model_call(middleware, request))
        assert len(result.messages) == 3
        # AIMessage 的 tool_calls 应该被清除
        assert not result.messages[1].tool_calls

    def test_awrap_model_call_empty_messages(self):
        """空消息列表应该不修改。"""
        import asyncio

        middleware = MessageSequenceValidationMiddleware()
        request = self._make_request([])
        result = asyncio.run(self._run_awrap_model_call(middleware, request))
        assert len(result.messages) == 0


if __name__ == "__main__":
    import pytest
    pytest.main([__file__, "-v"])
