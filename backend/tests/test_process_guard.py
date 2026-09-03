"""执行治理层 2b-B3：Windows Job Object 资源限制测试。

真实 Windows API 测试（本机为 Windows）：创建/指派/终止、正常执行、
超时杀进程树、**杀整棵进程树（子进程 PID 断言）**、env 关闭降级、
**fail-closed（创建/指派/Popen 失败拒绝执行）**、开发降级（含 Popen 失败）；
POSIX 降级路径。
"""

import os
import subprocess
import sys
import time

import pytest

from app.agents.tools.web.process_guard import (
    JobObject,
    resume_suspended_main_thread,
    run_with_job,
)

WINDOWS = os.name == "nt"
PY = f'"{sys.executable}"'
CREATE_NEW_PROCESS_GROUP = 0x00000200
CREATE_SUSPENDED = 0x00000004


def _process_exists(pid: int) -> bool:
    """进程存在性检查：Windows 用 OpenProcess（os.kill(pid,0) 对已终止 PID 行为不稳定）。"""
    if WINDOWS:
        import ctypes

        from app.agents.tools.web import process_guard as pg

        PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
        handle = pg.kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
        if handle:
            pg.kernel32.CloseHandle(handle)
            return True
        return False
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True


@pytest.mark.skipif(not WINDOWS, reason="Job Object 仅支持 Windows")
class TestJobObject:
    def test_create_assign_terminate(self):
        job = JobObject(memory_limit_mb=256, active_process_limit=8)
        proc = subprocess.Popen(
            [sys.executable, "-c", "import time; time.sleep(30)"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        try:
            assert job.assign(proc._handle) is True
            job.terminate()
            proc.wait(timeout=5)
            assert proc.returncode is not None  # 已被作业终止
        finally:
            job.close()
            if proc.poll() is None:
                proc.kill()

    def test_terminate_kills_process_tree(self):
        """rev28：CREATE_SUSPENDED 启动 → assign → 恢复主线程后，
        父进程启动子进程 → 记录子 PID → TerminateJobObject 后子进程不存在。

        rev37（时序稳定化）：TerminateJobObject 是异步的——进程对象销毁可能延迟，
        死亡判定改为轮询（最多 5s）；父进程 stdout 读取加超时保护（避免阻塞挂死）。
        """
        import queue
        import threading

        job = JobObject()
        parent = subprocess.Popen(
            [
                sys.executable, "-c",
                "import subprocess, sys, time;"
                " p = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)']);"
                " print('CHILD:' + str(p.pid), flush=True);"
                " time.sleep(60)",
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            creationflags=CREATE_NEW_PROCESS_GROUP | CREATE_SUSPENDED,
        )
        try:
            assert job.assign(parent._handle) is True
            assert resume_suspended_main_thread(parent.pid) is True
            # 超时保护：父进程 spawn 子进程后打印 CHILD:，最多等 10s
            line_q: queue.Queue = queue.Queue()

            def _readline():
                try:
                    line_q.put(parent.stdout.readline())
                except Exception:
                    line_q.put(b"")

            t = threading.Thread(target=_readline, daemon=True)
            t.start()
            try:
                raw = line_q.get(timeout=10)
            except queue.Empty:
                pytest.fail("读取子进程 PID 超时（父进程未打印 CHILD:）")
            line = raw.decode().strip()
            child_pid = int(line.split(":", 1)[1])
            assert _process_exists(child_pid) is True  # 子进程在作业内存活
            job.terminate()
            parent.wait(timeout=5)
            # TerminateJobObject 异步生效：轮询死亡（最多 5s）
            deadline = time.time() + 5
            while time.time() < deadline and _process_exists(child_pid):
                time.sleep(0.1)
            assert _process_exists(child_pid) is False
        finally:
            job.close()
            if parent.poll() is None:
                parent.kill()

    def test_run_with_job_normal_execution(self):
        result = run_with_job(f"{PY} -c \"print(42)\"", timeout=30, shell=True)
        assert result.returncode == 0
        assert b"42" in result.stdout

    def test_run_with_job_timeout_terminates_tree(self):
        with pytest.raises(subprocess.TimeoutExpired):
            run_with_job(
                f"{PY} -c \"import time; time.sleep(60)\"",
                timeout=2,
                shell=True,
            )
        # 超时路径调用 TerminateJobObject（其杀树语义由 test_terminate_kills_process_tree 验证）

    def test_env_disable_falls_back(self, monkeypatch):
        monkeypatch.setenv("EXEC_JOB_ENABLED", "0")
        result = run_with_job(f"{PY} -c \"print(1)\"", timeout=30, shell=True)
        assert result.returncode == 0

    def test_fail_closed_raises_on_job_creation_failure(self, monkeypatch):
        """rev28：fail-closed=1 且作业创建失败 → RuntimeError（不降级无保护运行）。"""
        monkeypatch.setenv("EXEC_JOB_FAIL_CLOSED", "1")

        def _boom(*a, **k):
            raise OSError("模拟作业创建失败")

        monkeypatch.setattr("app.agents.tools.web.process_guard.JobObject", _boom)
        with pytest.raises(RuntimeError, match="fail-closed"):
            run_with_job(f"{PY} -c \"print(1)\"", timeout=10, shell=True)

    def test_fail_closed_raises_on_assign_failure(self, monkeypatch):
        """rev28：fail-closed=1 且指派失败 → RuntimeError（进程不在作业保护内）。"""
        monkeypatch.setenv("EXEC_JOB_FAIL_CLOSED", "1")

        class _FakeJob:
            def __init__(self, **kw):
                pass

            def assign(self, handle):
                return False

            def close(self):
                pass

        monkeypatch.setattr("app.agents.tools.web.process_guard.JobObject", _FakeJob)
        with pytest.raises(RuntimeError, match="AssignProcessToJobObject"):
            run_with_job(f"{PY} -c \"print(1)\"", timeout=10, shell=True)

    def test_dev_degradation_allowed_by_default(self, monkeypatch):
        """rev28：默认（fail-closed=0）作业创建失败 → 降级普通执行，不抛异常。"""
        monkeypatch.delenv("EXEC_JOB_FAIL_CLOSED", raising=False)

        def _boom(*a, **k):
            raise OSError("模拟作业创建失败")

        monkeypatch.setattr("app.agents.tools.web.process_guard.JobObject", _boom)
        result = run_with_job(f"{PY} -c \"print(7)\"", timeout=30, shell=True)
        assert result.returncode == 0

    def test_popen_failure_fail_closed_raises(self, monkeypatch):
        """rev29 P1：Popen 启动失败（proc 尚未赋值）且 fail-closed=1 →
        RuntimeError（不得以 UnboundLocalError 覆盖原始异常）。"""
        monkeypatch.setenv("EXEC_JOB_FAIL_CLOSED", "1")

        def _boom(*a, **k):
            raise OSError("模拟 Popen 启动失败")

        monkeypatch.setattr(subprocess, "Popen", _boom)
        with pytest.raises(RuntimeError, match="fail-closed"):
            run_with_job(f"{PY} -c \"print(1)\"", timeout=10, shell=True)

    def test_popen_failure_dev_degradation(self, monkeypatch):
        """rev29 P1：Popen 启动失败且 fail-closed=0 → 降级普通执行成功（不抛
        UnboundLocalError，不泄漏进程）。"""
        monkeypatch.delenv("EXEC_JOB_FAIL_CLOSED", raising=False)
        real_popen = subprocess.Popen
        calls = {"n": 0}

        def _flaky(*a, **k):
            calls["n"] += 1
            if calls["n"] == 1:
                raise OSError("模拟 Popen 启动失败")
            return real_popen(*a, **k)

        monkeypatch.setattr(subprocess, "Popen", _flaky)
        result = run_with_job(f"{PY} -c \"print(9)\"", timeout=30, shell=True)
        assert result.returncode == 0
        assert calls["n"] == 2  # 第一次 Popen 失败 → 降级路径再次 Popen 成功

    def test_fail_closed_raises_on_resume_failure(self, monkeypatch):
        """rev30 补强：恢复挂起主线程失败（resume 返回 False）且 fail-closed=1 →
        RuntimeError（进程不在作业保护内，拒绝无保护执行）。"""
        monkeypatch.setenv("EXEC_JOB_FAIL_CLOSED", "1")
        monkeypatch.setattr(
            "app.agents.tools.web.process_guard.resume_suspended_main_thread",
            lambda pid: False,
        )
        with pytest.raises(RuntimeError, match="恢复挂起主线程失败"):
            run_with_job(f"{PY} -c \"print(1)\"", timeout=10, shell=True)

    def test_dev_degradation_on_resume_failure(self, monkeypatch):
        """rev30 补强：恢复挂起主线程失败且 fail-closed=0 → 降级普通执行成功。"""
        monkeypatch.delenv("EXEC_JOB_FAIL_CLOSED", raising=False)
        monkeypatch.setattr(
            "app.agents.tools.web.process_guard.resume_suspended_main_thread",
            lambda pid: False,
        )
        result = run_with_job(f"{PY} -c \"print(5)\"", timeout=30, shell=True)
        assert result.returncode == 0


@pytest.mark.skipif(WINDOWS, reason="POSIX 降级路径")
def test_posix_fallback():
    result = run_with_job("echo hi", timeout=30)
    assert result.returncode == 0
