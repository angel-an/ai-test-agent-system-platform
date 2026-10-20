"""执行治理层 rev31/rev32：HTTP 执行面纳入 2a/B3 的回归测试。

覆盖（评审 P1 要求）：
1. WebTestService.run_web_test 授权门：
   - 无 sub_function_id 绑定 → 终局拒绝（fail-closed）；
   - 来源授权失败（未登记/项目不符）→ 终局拒绝；
   - 来源授权通过 → 放行进入执行（PlaywrightRunner 被调用）；
2. PlaywrightRunner（Windows）执行与预检均调用 run_with_job（B3 Job Object）——
   消除 node_modules/.bin 本地 CLI 绕过。
"""

import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from subprocess import CompletedProcess
from types import SimpleNamespace
from unittest import mock

import pytest

from app.services.web_test_service import WebTestService


def _tmp_ws():
    """创建临时工作区（返回 (root, cleanup)）。避开 pytest-asyncio 同步 fixture 注入问题。"""
    root = Path(tempfile.mkdtemp(prefix="exec_gate_"))
    (root / "tests").mkdir(parents=True, exist_ok=True)

    def _cleanup():
        shutil.rmtree(root, ignore_errors=True)

    return root, _cleanup


# ---------------------------------------------------------------------------
# WebTestService.run_web_test 授权门（整层 mock，不触真实 DB/MinIO/workspace）
# ---------------------------------------------------------------------------

def _make_service(monkeypatch, ws_root, auth_result, with_sf=True):
    """构造 WebTestService，mock 掉 DB/MinIO/授权门/runner。"""

    class _Session:
        async def commit(self):
            pass

        async def get(self, model, pk):
            return None  # 子功能统计更新分支：直接跳过

    svc = WebTestService.__new__(WebTestService)
    svc.session = _Session()
    project = SimpleNamespace(id="00000000-0000-0000-0000-0000000000a1", identifier="PR-1")
    web_test = SimpleNamespace(
        id="00000000-0000-0000-0000-0000000000b1",
        project_id="00000000-0000-0000-0000-0000000000a1",
        sub_function_id=("00000000-0000-0000-0000-0000000000c1" if with_sf else None),
        identifier="WT-1",
        name="测试",
        base_url="about:blank",
        script_path="obj/t.py",
        script_format="playwright",
        script_language="python",
    )
    test_run = SimpleNamespace(id="run-1", identifier="WTR-1")
    svc.web_test_repo = SimpleNamespace(get_by_id=mock.AsyncMock(return_value=web_test))
    svc.project_repo = SimpleNamespace(
        get_by_identifier=mock.AsyncMock(return_value=project)
    )
    svc.web_test_run_repo = SimpleNamespace(
        create=mock.AsyncMock(return_value=test_run),
        update=mock.AsyncMock(),
    )
    svc.web_test_result_repo = SimpleNamespace()

    # 预检要求运行期凭据（与脚本自身 test.skip 判据一致）：真实运行本就需要，
    # 这里显式提供，避免测试因"无凭据"而被预检拦下。
    monkeypatch.setenv("WEB_TEST_USERNAME", "tester")
    monkeypatch.setenv("WEB_TEST_PASSWORD", "secret")

    monkeypatch.setattr(
        "app.services.web_test_service.MinIOClient.download_file",
        mock.Mock(return_value=b"print('x')\n"),
    )
    # 双保险：直接对类对象替换（避免字符串路径解析问题）
    from app.config.minio_client import MinIOClient

    monkeypatch.setattr(MinIOClient, "download_file",
                        mock.Mock(return_value=b"print('x')\n"))
    from app.config.settings import settings

    monkeypatch.setattr(settings, "web_cli_workspace_root", str(ws_root))
    monkeypatch.setattr(
        "app.agents.tools.web.script_provenance.authorize_script_execution",
        mock.AsyncMock(return_value=(auth_result, "ok" if auth_result else "三要素不成立")),
    )

    class _FakeRunner:
        def __init__(self, workspace_root):
            pass

        async def run(self, script_path, config=None, timeout=600):
            return SimpleNamespace(
                success=True, stdout="B3_JOB_OK\n", stderr="", returncode=0,
                report_path=None, duration_ms=10,
                result_summary={"total": 1, "passed": 1},
            )

    monkeypatch.setattr("app.services.execution.runner.PlaywrightRunner", _FakeRunner)
    return svc, test_run


class TestRunWebTestAuthorizationGate:
    WT_ID = "00000000-0000-0000-0000-0000000000b1"

    async def test_no_sub_function_binding_is_final_denial(self, monkeypatch):
        """无 sub_function_id → 预检终局拒绝（guard=preflight, code=sub_function_unbound）。"""
        ws, cleanup = _tmp_ws()
        try:
            svc, _ = _make_service(monkeypatch, ws, auth_result=True, with_sf=False)
            r = await svc.run_web_test(project_identifier="PR-1", web_test_id=self.WT_ID)
            assert r.get("status") == "failed"
            assert r.get("guard") == "preflight"
            assert (r.get("preflight") or {}).get("code") == "sub_function_unbound"
            assert r.get("final") is True
            assert "sub_function_id" in (r.get("error_message") or "")
            assert "[sub_function_unbound]" in (r.get("error_message") or "")
        finally:
            cleanup()

    async def test_authorization_failure_is_final_denial(self, monkeypatch):
        """来源授权失败（未登记/项目不符）→ 终局拒绝。"""
        ws, cleanup = _tmp_ws()
        try:
            svc, _ = _make_service(monkeypatch, ws, auth_result=False, with_sf=True)
            r = await svc.run_web_test(project_identifier="PR-1", web_test_id=self.WT_ID)
            assert r.get("status") == "failed"
            assert r.get("guard") == "script_provenance"
            assert r.get("final") is True
            assert "授权" in (r.get("error_message") or "")
        finally:
            cleanup()

    async def test_authorization_pass_reaches_execution(self, monkeypatch):
        """来源授权通过 → 放行进入 PlaywrightRunner（非 script_provenance 拒绝）。"""
        ws, cleanup = _tmp_ws()
        try:
            svc, test_run = _make_service(monkeypatch, ws, auth_result=True, with_sf=True)
            r = await svc.run_web_test(project_identifier="PR-1", web_test_id=self.WT_ID)
            assert r.get("guard") != "script_provenance"
            assert r.get("status") == "completed"
            assert "B3_JOB_OK" in (r.get("stdout") or "")
        finally:
            cleanup()


# ---------------------------------------------------------------------------
# PlaywrightRunner：Windows 分支执行与预检均经 run_with_job（B3 Job Object）
# ---------------------------------------------------------------------------

@pytest.mark.skipif(os.name != "nt", reason="Job Object 仅 Windows")
class TestPlaywrightRunnerWindowsJobObject:
    async def test_precheck_and_execution_use_run_with_job(self, monkeypatch):
        """Windows 下：CLI 预检（--version）与脚本执行均调用 run_with_job。"""
        ws, cleanup = _tmp_ws()
        try:
            script = ws / "tests" / "t.py"
            script.write_text("print('B3_JOB_OK')\n", encoding="utf-8")

            calls = []

            async def _fake_to_thread(fn, *args, **kwargs):
                return fn(*args, **kwargs)

            monkeypatch.setattr("asyncio.to_thread", _fake_to_thread)

            def _fake_run_with_job(cmd, cwd, env, timeout, shell, **kwargs):
                calls.append(cmd)
                return CompletedProcess(cmd, 0, b"B3_JOB_OK\n", b"")

            monkeypatch.setattr(
                "app.agents.tools.web.process_guard.run_with_job", _fake_run_with_job
            )

            from app.services.execution.runner import PlaywrightRunner

            runner = PlaywrightRunner(ws)
            result = await runner.run(script, config={"reporter": "list"}, timeout=30)
            assert result.success is True
            assert "B3_JOB_OK" in result.stdout
            # 至少两次：预检 --version + 实际执行
            assert len(calls) >= 2, f"run_with_job 调用次数不足: {calls}"
            # 预检必须带 --version
            assert any("--version" in [str(c) for c in call] for call in calls)
            # 执行必须带测试脚本
            assert any(any("t.py" in str(c) for c in call) for call in calls)
        finally:
            cleanup()

    async def test_precheck_timeout_has_headroom_for_cold_start(self, monkeypatch):
        """预检超时必须对 CLI 冷启动留出余量（rev115，实测 TR-445/446）。

        为什么要有这条：Windows 上 `playwright.cmd --version` 是 cmd→node 两层壳，
        冷启动实测基线 6.5~7.3s；若预检超时贴着 10s，加上 Job Object 开销与机器
        并发负载就会**随机**判失败 —— 现象是整轮 stdout_len=0 报
        「Playwright CLI 检查超时」，会被误读成用例失败。预检只用于确认
        "CLI 存在且可执行"，超时必须显著大于冷启动基线。
        """
        ws, cleanup = _tmp_ws()
        try:
            script = ws / "tests" / "t_timeout.py"
            script.write_text("print('B3_JOB_OK')\n", encoding="utf-8")

            seen = {}

            async def _fake_to_thread(fn, *args, **kwargs):
                return fn(*args, **kwargs)

            monkeypatch.setattr("asyncio.to_thread", _fake_to_thread)

            def _fake_run_with_job(cmd, cwd, env, timeout, shell, **kwargs):
                if any("--version" in str(c) for c in cmd):
                    seen["precheck_timeout"] = timeout
                return CompletedProcess(cmd, 0, b"B3_JOB_OK\n", b"")

            monkeypatch.setattr(
                "app.agents.tools.web.process_guard.run_with_job", _fake_run_with_job
            )

            from app.services.execution.runner import PlaywrightRunner

            runner = PlaywrightRunner(ws)
            result = await runner.run(script, config={"reporter": "list"}, timeout=30)
            assert result.success is True
            assert "precheck_timeout" in seen, "预检未走 run_with_job"
            # 冷启动基线约 7s ⇒ 至少留 3 倍余量
            assert seen["precheck_timeout"] >= 30, (
                f"预检超时过紧（{seen['precheck_timeout']}s），CLI 冷启动会随机超时"
            )
        finally:
            cleanup()

    async def test_precheck_timeout_message_says_it_is_not_the_case(self, monkeypatch):
        """预检超时的文案必须点明「非用例问题」，否则会被当成用例失败排查。"""
        from app.services.execution import runner as runner_mod

        src = (runner_mod.__file__)
        text = open(src, encoding="utf-8").read()
        assert "Playwright CLI 检查超时（预检" in text
        assert "非用例问题" in text

    async def test_run_with_job_returns_stdout(self, monkeypatch):
        """run_with_job 输出回传（stdout/returncode）。"""
        ws, cleanup = _tmp_ws()
        try:
            script = ws / "tests" / "t2.py"
            script.write_text("print('OK2')\n", encoding="utf-8")

            async def _fake_to_thread(fn, *args, **kwargs):
                return fn(*args, **kwargs)

            monkeypatch.setattr("asyncio.to_thread", _fake_to_thread)

            def _fake_run_with_job(cmd, cwd, env, timeout, shell, **kwargs):
                return CompletedProcess(cmd, 0, b"OK2\n", b"")

            monkeypatch.setattr(
                "app.agents.tools.web.process_guard.run_with_job", _fake_run_with_job
            )

            from app.services.execution.runner import PlaywrightRunner

            runner = PlaywrightRunner(ws)
            result = await runner.run(script, config={"reporter": "list"}, timeout=30)
            assert result.returncode == 0
            assert "OK2" in result.stdout
        finally:
            cleanup()

    async def test_exec_log_path_passed_to_run_with_job(self, monkeypatch):
        """rev95：执行期输出必须落到脚本同目录日志文件（超时现场的唯一来源）。"""
        ws, cleanup = _tmp_ws()
        try:
            script = ws / "tests" / "t3.py"
            script.write_text("print('OK3')\n", encoding="utf-8")

            async def _fake_to_thread(fn, *args, **kwargs):
                return fn(*args, **kwargs)

            monkeypatch.setattr("asyncio.to_thread", _fake_to_thread)
            seen = {}

            def _fake_run_with_job(cmd, cwd, env, timeout, shell, **kwargs):
                if "--version" not in [str(c) for c in cmd]:
                    seen.update(kwargs)
                return CompletedProcess(cmd, 0, b"OK3\n", b"")

            monkeypatch.setattr(
                "app.agents.tools.web.process_guard.run_with_job", _fake_run_with_job
            )

            from app.services.execution.runner import PlaywrightRunner

            runner = PlaywrightRunner(ws)
            await runner.run(script, config={"reporter": "list"}, timeout=30)
            assert seen.get("stdout_path") == str(ws / "tests" / "t3.exec.log")
            assert seen.get("stderr_path") == str(ws / "tests" / "t3.exec.err.log")
        finally:
            cleanup()

    async def test_timeout_surfaces_exec_log_tail(self, monkeypatch):
        """rev95：超时不得「零输出」——error_message 带现场路径与输出尾部。

        复现 10-12 实测场景：602s 超时后 stdout 为空、无法定位挂死点。
        """
        ws, cleanup = _tmp_ws()
        try:
            script = ws / "tests" / "t4.py"
            script.write_text("print('X')\n", encoding="utf-8")
            marker = "STEP3 提交（挂死在此）"

            async def _fake_to_thread(fn, *args, **kwargs):
                return fn(*args, **kwargs)

            monkeypatch.setattr("asyncio.to_thread", _fake_to_thread)

            def _fake_run_with_job(cmd, cwd, env, timeout, shell, **kwargs):
                if "--version" in [str(c) for c in cmd]:
                    return CompletedProcess(cmd, 0, b"", b"")
                # 子进程实时落盘（这是超时后唯一残留的现场）
                with open(kwargs["stdout_path"], "wb") as fh:
                    fh.write(f"STEP1 打开表单\nSTEP2 点击保存\n{marker}\n".encode())
                raise subprocess.TimeoutExpired(cmd, timeout, output=b"", stderr=b"")

            monkeypatch.setattr(
                "app.agents.tools.web.process_guard.run_with_job", _fake_run_with_job
            )

            from app.services.execution.runner import PlaywrightRunner

            runner = PlaywrightRunner(ws)
            result = await runner.run(script, config={"reporter": "list"}, timeout=5)
            assert result.success is False
            assert result.error_message and "测试执行超时" in result.error_message
            assert str(ws / "tests" / "t4.exec.log") in result.error_message
            assert marker in result.error_message
            assert marker in result.stdout  # 现场同时回填 stdout（DB 可读）
        finally:
            cleanup()
