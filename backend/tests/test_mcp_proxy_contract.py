"""执行治理层 P0-1 复审修正：/mcp/call 终局拒绝契约测试。

复审问题：黑名单拒绝仅以普通 error 字符串返回，调用方无法区分"终局拒绝"与
"普通工具错误"，可能触发重试逻辑。修正后 McpCallResponse 增加 final 字段，
且工具返回 guard 的 {final:true} JSON 时同样透传 final=true。
rev31：内部 MCP 工具分支同样透传 final（此前仅外部工具分支有）。
"""

import json

from app.api.v2.mcp_proxy import (
    McpCallRequest,
    McpCallResponse,
    McpServerConfig,
    _result_is_final,
    call_mcp_tool,
)


class TestMcpCallFinalContract:
    def test_response_default_not_final(self):
        r = McpCallResponse(result="ok")
        assert r.final is False
        assert r.error is None

    def test_response_can_carry_final(self):
        r = McpCallResponse(
            result="",
            error="[tool_guard] 工具 'browser_evaluate' 被安全策略禁用（final denial）",
            final=True,
        )
        assert r.final is True
        assert "禁用" in r.error

    def test_result_is_final_detects_guard_json(self):
        assert _result_is_final(
            json.dumps({"success": False, "final": True, "tool": "browser_navigate"})
        ) is True

    def test_result_is_final_ignores_normal_output(self):
        assert _result_is_final(json.dumps({"success": True, "final": False})) is False
        assert _result_is_final("normal tool output") is False
        assert _result_is_final("not json {") is False
        assert _result_is_final("") is False
        assert _result_is_final(None) is False
        assert _result_is_final(123) is False

    def test_result_is_final_ignores_non_guard_json(self):
        # final 字段缺失或非 true 的合法 JSON 不算终局拒绝
        assert _result_is_final(json.dumps({"success": False, "error": "boom"})) is False
        assert _result_is_final(json.dumps({"final": "true"})) is False


class TestInternalMcpBranchFinalPassthrough:
    """rev31：内部 MCP 工具分支必须透传 final（与外部工具分支一致）。"""

    def _server(self) -> McpServerConfig:
        return McpServerConfig(id="internal-test-tools", name="internal", enabled=True)

    async def test_save_web_test_script_missing_sub_function_id_is_final(self):
        """缺 sub_function_id → _save_web_test_script 返回 {final:true} → 外层 final=True。"""
        request = McpCallRequest(
            server=self._server(),
            tool_name="save_web_test_script",
            args={"script_content": "print(1)", "language": "python"},
        )
        resp = await call_mcp_tool(request, db=None)
        assert resp.final is True
        assert "sub_function_id" in resp.result
        assert "script_provenance" in resp.result

    async def test_internal_blacklisted_tool_is_final(self):
        """黑名单工具走 is_tool_denied 终局拒绝（internal server 同样生效）。"""
        request = McpCallRequest(
            server=self._server(),
            tool_name="browser_evaluate",
            args={},
        )
        resp = await call_mcp_tool(request, db=None)
        assert resp.final is True
        assert "禁用" in (resp.error or "")

    async def test_success_result_not_final(self):
        """成功/普通结果不误标 final。"""
        request = McpCallRequest(
            server=self._server(),
            tool_name="unknown_internal_tool",
            args={},
        )
        resp = await call_mcp_tool(request, db=None)
        assert resp.final is False
