"""执行治理层 P0-1：工具安全策略与终局守卫单元测试。

覆盖：
- 黑名单（browser_run_code_unsafe / browser_evaluate / tracing）
- 可配置禁用模式（MCP_DENY_TOOL_PATTERNS）
- 工具列表过滤（filter_tools）
- 导航 origin 白名单（NAVIGATION_ORIGIN_ALLOWLIST）
- 终局守卫包装器（同步/异步、final 拒绝语义、幂等）
"""

import json

import pytest

from app.agents.tool_guard import wrap_tool_with_guard, wrap_tools_with_guard
from app.agents.tool_policy import (
    DENIED_TOOLS,
    check_navigation_origin,
    check_navigation_target,
    extract_navigation_url,
    filter_tools,
    is_tool_denied,
)


# ---------------------------------------------------------------------------
# 黑名单
# ---------------------------------------------------------------------------

class TestDenyList:
    def test_rce_tools_denied(self):
        assert "browser_run_code_unsafe" in DENIED_TOOLS
        assert "browser_evaluate" in DENIED_TOOLS
        assert is_tool_denied("browser_run_code_unsafe")
        assert is_tool_denied("browser_evaluate")

    def test_tracing_tools_denied(self):
        assert is_tool_denied("browser_start_tracing")
        assert is_tool_denied("browser_stop_tracing")

    def test_normal_browser_tools_allowed(self):
        assert not is_tool_denied("browser_navigate")
        assert not is_tool_denied("browser_click")
        assert not is_tool_denied("browser_snapshot")
        assert not is_tool_denied("browser_fill_form")

    def test_server_prefix_forms_denied(self):
        assert is_tool_denied("web_mcp/browser_evaluate")
        assert is_tool_denied("playwright-test/browser_run_code_unsafe")
        assert not is_tool_denied("web_mcp/browser_navigate")

    def test_empty_name_not_denied(self):
        assert not is_tool_denied(None)
        assert not is_tool_denied("")

    def test_extra_deny_patterns_from_env(self, monkeypatch):
        monkeypatch.setenv("MCP_DENY_TOOL_PATTERNS", "run_code,shell_exec")
        assert is_tool_denied("custom_run_code")
        assert is_tool_denied("my_server/shell_exec")
        assert not is_tool_denied("browser_click")

    def test_extra_deny_patterns_unset(self, monkeypatch):
        monkeypatch.delenv("MCP_DENY_TOOL_PATTERNS", raising=False)
        assert not is_tool_denied("custom_run_code")


# ---------------------------------------------------------------------------
# 工具列表过滤
# ---------------------------------------------------------------------------

class _FakeTool:
    def __init__(self, name: str):
        self.name = name


class TestFilterTools:
    def test_filters_denied_and_keeps_rest(self):
        tools = [
            _FakeTool("browser_navigate"),
            _FakeTool("browser_evaluate"),
            _FakeTool("browser_run_code_unsafe"),
            _FakeTool("browser_click"),
        ]
        kept = filter_tools(tools)
        assert [t.name for t in kept] == ["browser_navigate", "browser_click"]

    def test_empty_list(self):
        assert filter_tools([]) == []


# ---------------------------------------------------------------------------
# 导航 origin 白名单
# ---------------------------------------------------------------------------

class TestNavigationOrigin:
    def test_not_enforced_by_default(self, monkeypatch):
        monkeypatch.delenv("NAVIGATION_ORIGIN_ALLOWLIST", raising=False)
        allowed, _ = check_navigation_origin("https://anywhere.example.com/x")
        assert allowed is True

    def test_suffix_match(self, monkeypatch):
        monkeypatch.setenv("NAVIGATION_ORIGIN_ALLOWLIST", ".example.com")
        assert check_navigation_origin("https://a.example.com/x")[0] is True
        assert check_navigation_origin("https://example.com/x")[0] is True
        assert check_navigation_origin("https://evil.com/x")[0] is False

    def test_exact_match(self, monkeypatch):
        monkeypatch.setenv("NAVIGATION_ORIGIN_ALLOWLIST", "uat.mycorp.com")
        assert check_navigation_origin("https://uat.mycorp.com/path")[0] is True
        assert check_navigation_origin("https://prod.mycorp.com/path")[0] is False

    def test_loopback_always_allowed(self, monkeypatch):
        monkeypatch.setenv("NAVIGATION_ORIGIN_ALLOWLIST", ".example.com")
        assert check_navigation_origin("http://localhost:3000/")[0] is True
        assert check_navigation_origin("http://127.0.0.1:8000/")[0] is True

    def test_non_http_skips_check(self, monkeypatch):
        monkeypatch.setenv("NAVIGATION_ORIGIN_ALLOWLIST", ".example.com")
        assert check_navigation_origin("file:///tmp/x.html")[0] is True
        assert check_navigation_origin(None)[0] is True
        assert check_navigation_origin("")[0] is True

    def test_extract_navigation_url(self):
        # 仅导航类工具提取
        assert extract_navigation_url("browser_navigate", {"url": "https://x.com"}) == "https://x.com"
        assert extract_navigation_url("browser_click", {"url": "https://x.com"}) is None
        assert extract_navigation_url("web_open", {"path": "https://x.com"}) == "https://x.com"
        assert extract_navigation_url("browser_navigate", {"url": "not a url"}) is None


# ---------------------------------------------------------------------------
# 终局守卫包装器
# ---------------------------------------------------------------------------

class _RunTool:
    """模拟 langchain 工具的 _run/_arun 双通道。"""

    def __init__(self, name: str = "browser_navigate"):
        self.name = name
        self._run = self._sync
        self._arun = self._async

    def _sync(self, url: str | None = None, **kwargs):
        return f"ok:{url}"

    async def _async(self, url: str | None = None, **kwargs):
        return f"ok:{url}"


class TestToolGuard:
    def test_normal_call_passes_through(self, monkeypatch):
        # rev54：本地 .env 可能配置了白名单（app/__init__ load_dotenv 注入 os.environ），
        # 本测试验证"未配置白名单（放行模式）下正常调用透传"→ 显式清除
        monkeypatch.delenv("NAVIGATION_ORIGIN_ALLOWLIST", raising=False)
        t = wrap_tool_with_guard(_RunTool())
        assert t._run(url="https://example.com") == "ok:https://example.com"

    def test_denied_tool_returns_final_denial(self):
        t = wrap_tool_with_guard(_RunTool(name="browser_evaluate"))
        out = t._run(url="https://example.com")
        data = json.loads(out)
        assert data["success"] is False
        assert data["final"] is True
        assert data["guard"] == "tool_guard"
        assert "禁用" in data["reason"]

    def test_origin_blocked_returns_final_denial(self, monkeypatch):
        monkeypatch.setenv("NAVIGATION_ORIGIN_ALLOWLIST", ".example.com")
        t = wrap_tool_with_guard(_RunTool())
        out = t._run(url="https://evil.com/x")
        data = json.loads(out)
        assert data["final"] is True
        assert "origin" in data["reason"]

    def test_origin_allowed_with_allowlist(self, monkeypatch):
        monkeypatch.setenv("NAVIGATION_ORIGIN_ALLOWLIST", ".example.com")
        t = wrap_tool_with_guard(_RunTool())
        assert t._run(url="https://a.example.com/x") == "ok:https://a.example.com/x"

    def test_positional_url_arg_checked(self, monkeypatch):
        monkeypatch.setenv("NAVIGATION_ORIGIN_ALLOWLIST", ".example.com")
        t = wrap_tool_with_guard(_RunTool())
        out = t._run("https://evil.com/x")
        data = json.loads(out)
        assert data["final"] is True

    async def test_async_path(self, monkeypatch):
        monkeypatch.setenv("NAVIGATION_ORIGIN_ALLOWLIST", ".example.com")
        t = wrap_tool_with_guard(_RunTool())
        assert await t._arun(url="https://a.example.com/x") == "ok:https://a.example.com/x"
        out = await t._arun(url="https://evil.com/x")
        assert json.loads(out)["final"] is True

    def test_wrapping_is_idempotent(self):
        t = wrap_tool_with_guard(_RunTool())
        t2 = wrap_tool_with_guard(t)
        assert t2 is t

    def test_batch_wrap(self, monkeypatch):
        monkeypatch.delenv("NAVIGATION_ORIGIN_ALLOWLIST", raising=False)
        tools = wrap_tools_with_guard([_RunTool(), _RunTool(name="browser_evaluate")])
        assert tools[0]._run(url="https://example.com") == "ok:https://example.com"
        assert json.loads(tools[1]._run(url="https://example.com"))["final"] is True


# ---------------------------------------------------------------------------
# P0-1 复审修正：导航 scheme 门禁（非 HTTP(S) 绕过）
# ---------------------------------------------------------------------------

class TestNavigationSchemeGate:
    """导航工具默认拒绝非 http/https/about scheme，堵住 file:/data:/javascript: 绕过。"""

    def test_file_scheme_denied_without_allowlist(self, monkeypatch):
        monkeypatch.delenv("NAVIGATION_ORIGIN_ALLOWLIST", raising=False)
        allowed, reason = check_navigation_target("file:///C:/sensitive.txt")
        assert allowed is False
        assert "scheme" in reason

    def test_data_scheme_denied(self):
        allowed, _ = check_navigation_target("data:text/html,<h1>x</h1>")
        assert allowed is False

    def test_javascript_scheme_denied(self):
        allowed, _ = check_navigation_target("javascript:alert(1)")
        assert allowed is False

    def test_chrome_scheme_denied(self):
        allowed, _ = check_navigation_target("chrome://settings")
        assert allowed is False

    def test_about_blank_allowed(self):
        allowed, _ = check_navigation_target("about:blank")
        assert allowed is True
        # 带 fragment 的 about:blank 也放行
        assert check_navigation_target("about:blank#x")[0] is True

    def test_about_narrowed_to_blank(self):
        # P1 复审修正：about: 仅放行 about:blank，其余（about:config 等）拒绝
        assert check_navigation_target("about:config")[0] is False
        assert check_navigation_target("about:memory")[0] is False

    def test_about_exact_match_not_prefix(self):
        # rev5（P1 复审修正）：禁止前缀匹配——about:blankevil / ?query / 子路径 必须拒绝
        assert check_navigation_target("about:blankevil")[0] is False
        assert check_navigation_target("about:blank?url=https://evil.com")[0] is False
        assert check_navigation_target("about:blank/evil")[0] is False
        # fragment 仍允许
        assert check_navigation_target("about:blank#x")[0] is True
        # 大小写不敏感
        assert check_navigation_target("ABOUT:BLANK")[0] is True

    def test_protocol_relative_url_extracted(self):
        # P1 复审修正：//host/path 协议相对 URL 必须进入导航校验
        assert extract_navigation_url("browser_navigate", {"url": "//evil.com/path"}) == "//evil.com/path"

    def test_protocol_relative_denied_when_allowlist_enforced(self, monkeypatch):
        monkeypatch.setenv("NAVIGATION_ORIGIN_ALLOWLIST", ".example.com")
        assert check_navigation_target("//evil.com/path")[0] is False
        assert check_navigation_target("//a.example.com/x")[0] is True
        assert check_navigation_target("//localhost:3000/x")[0] is True

    def test_protocol_relative_allowed_when_not_enforced(self, monkeypatch):
        monkeypatch.delenv("NAVIGATION_ORIGIN_ALLOWLIST", raising=False)
        assert check_navigation_target("//evil.com/path")[0] is True

    def test_scheme_override_via_env(self, monkeypatch):
        monkeypatch.setenv("NAVIGATION_ALLOW_SCHEMES", "data")
        monkeypatch.delenv("NAVIGATION_ORIGIN_ALLOWLIST", raising=False)
        assert check_navigation_target("data:text/html,<h1>x</h1>")[0] is True
        # 显式放行 data 不影响 file: 仍被拒
        assert check_navigation_target("file:///C:/x")[0] is False

    def test_http_with_allowlist_still_enforced(self, monkeypatch):
        monkeypatch.setenv("NAVIGATION_ORIGIN_ALLOWLIST", ".example.com")
        assert check_navigation_target("https://a.example.com/x")[0] is True
        assert check_navigation_target("https://evil.com/x")[0] is False

    def test_extract_navigation_url_now_catches_non_http(self):
        # 复审修正：extract 不再只认 http(s)，非 http(s) 也要进 scheme 门禁
        assert extract_navigation_url(
            "browser_navigate", {"url": "file:///C:/sensitive.txt"}
        ) == "file:///C:/sensitive.txt"
        assert extract_navigation_url("browser_navigate", {"url": "data:text/html,x"}) == "data:text/html,x"
        assert extract_navigation_url("browser_click", {"url": "file:///C:/x"}) is None

    def test_guard_denies_file_url_even_without_allowlist(self, monkeypatch):
        monkeypatch.delenv("NAVIGATION_ORIGIN_ALLOWLIST", raising=False)
        t = wrap_tool_with_guard(_RunTool())
        out = t._run(url="file:///C:/sensitive.txt")
        data = json.loads(out)
        assert data["final"] is True
        assert "scheme" in data["reason"]

    def test_guard_allows_about_blank(self):
        t = wrap_tool_with_guard(_RunTool())
        assert t._run(url="about:blank") == "ok:about:blank"

    async def test_guard_async_denies_file_url(self, monkeypatch):
        monkeypatch.delenv("NAVIGATION_ORIGIN_ALLOWLIST", raising=False)
        t = wrap_tool_with_guard(_RunTool())
        out = await t._arun(url="file:///C:/sensitive.txt")
        assert json.loads(out)["final"] is True
