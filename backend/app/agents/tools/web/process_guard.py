"""执行治理层 2b-B3：Windows Job Object 资源限制（进程隔离过渡层）。

能力：
- JOB_OBJECT_LIMIT_PROCESS_MEMORY：单进程内存上限（默认 1024MB，env EXEC_JOB_MEMORY_LIMIT_MB）；
- JOB_OBJECT_LIMIT_ACTIVE_PROCESS：活动进程上限（默认 32，env EXEC_JOB_ACTIVE_PROCESS_LIMIT）；
- JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE：作业句柄关闭时杀死整棵进程树（防 chrome/子进程残留）；
- 超时：TerminateJobObject 杀死整棵树后抛 subprocess.TimeoutExpired（沿用调用方超时语义）。

免 pywin32：ctypes 调用 kernel32。非 Windows 或无权限时降级为普通 subprocess.run
（由 env EXEC_JOB_ENABLED=0 可显式关闭；作业创建失败不影响执行，仅记录告警）。
**rev28/rev29 fail-closed**：env EXEC_JOB_FAIL_CLOSED=1 时，作业创建/指派/恢复或
Popen 启动任一失败 → 抛 RuntimeError 终止执行（生产强制模式，不降级无保护运行）；
默认 0 为开发降级（生产部署必须在 .env 明确设为 1，见 .env.example）。
"""

from __future__ import annotations

import ctypes
import logging
import os
import subprocess
from ctypes import wintypes

logger = logging.getLogger(__name__)

# Job Object 限制标志
JOB_OBJECT_LIMIT_PROCESS_MEMORY = 0x00000100
JOB_OBJECT_LIMIT_ACTIVE_PROCESS = 0x00000008
JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x00002000
JobObjectExtendedLimitInformation = 9

CREATE_NEW_PROCESS_GROUP = 0x00000200


class _IO_COUNTERS(ctypes.Structure):
    _fields_ = [
        ("ReadOperationCount", ctypes.c_ulonglong),
        ("WriteOperationCount", ctypes.c_ulonglong),
        ("OtherOperationCount", ctypes.c_ulonglong),
        ("ReadTransferCount", ctypes.c_ulonglong),
        ("WriteTransferCount", ctypes.c_ulonglong),
        ("OtherTransferCount", ctypes.c_ulonglong),
    ]


class _JOBOBJECT_BASIC_LIMIT_INFORMATION(ctypes.Structure):
    _fields_ = [
        ("PerProcessUserTimeLimit", ctypes.c_longlong),
        ("PerJobUserTimeLimit", ctypes.c_longlong),
        ("LimitFlags", ctypes.c_uint32),
        ("MinimumWorkingSetSize", ctypes.c_size_t),
        ("MaximumWorkingSetSize", ctypes.c_size_t),
        ("ActiveProcessLimit", ctypes.c_uint32),
        ("Affinity", ctypes.c_size_t),
        ("PriorityClass", ctypes.c_uint32),
        ("SchedulingClass", ctypes.c_uint32),
    ]


class _JOBOBJECT_EXTENDED_LIMIT_INFORMATION(ctypes.Structure):
    _fields_ = [
        ("BasicLimitInformation", _JOBOBJECT_BASIC_LIMIT_INFORMATION),
        ("IoInfo", _IO_COUNTERS),
        ("ProcessMemoryLimit", ctypes.c_size_t),
        ("JobMemoryLimit", ctypes.c_size_t),
        ("PeakProcessMemoryUsed", ctypes.c_size_t),
        ("PeakJobMemoryUsed", ctypes.c_size_t),
    ]


def _is_windows() -> bool:
    return os.name == "nt"


def _job_enabled() -> bool:
    return os.getenv("EXEC_JOB_ENABLED", "1").strip().lower() not in ("0", "false", "no")


def _memory_limit_mb() -> int:
    try:
        return max(64, int(os.getenv("EXEC_JOB_MEMORY_LIMIT_MB", "1024")))
    except (TypeError, ValueError):
        return 1024


def _active_process_limit() -> int:
    try:
        return max(2, int(os.getenv("EXEC_JOB_ACTIVE_PROCESS_LIMIT", "32")))
    except (TypeError, ValueError):
        return 32


def _fail_closed() -> bool:
    """生产 fail-closed：作业创建/指派失败 → 抛异常终止执行（不降级无保护运行）。
    env: EXEC_JOB_FAIL_CLOSED=1（默认 0：开发环境允许降级为普通 subprocess.run）。"""
    return os.getenv("EXEC_JOB_FAIL_CLOSED", "0").strip().lower() in ("1", "true", "yes")


class JobObject:
    """Windows Job Object 包装：KILL_ON_JOB_CLOSE + 内存上限 + 活动进程上限。"""

    def __init__(
        self,
        memory_limit_mb: int | None = None,
        active_process_limit: int | None = None,
    ) -> None:
        if not _is_windows():
            raise OSError("Job Object 仅支持 Windows")
        self._handle = kernel32.CreateJobObjectW(None, None)
        if not self._handle:
            raise ctypes.WinError(ctypes.get_last_error())

        limits = _JOBOBJECT_EXTENDED_LIMIT_INFORMATION()
        flags = JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        if memory_limit_mb:
            flags |= JOB_OBJECT_LIMIT_PROCESS_MEMORY
            # ProcessMemoryLimit 位于扩展结构体（非 BasicLimitInformation）
            limits.ProcessMemoryLimit = memory_limit_mb * 1024 * 1024
        if active_process_limit:
            flags |= JOB_OBJECT_LIMIT_ACTIVE_PROCESS
            limits.BasicLimitInformation.ActiveProcessLimit = active_process_limit
        limits.BasicLimitInformation.LimitFlags = flags

        ok = kernel32.SetInformationJobObject(
            self._handle,
            JobObjectExtendedLimitInformation,
            ctypes.byref(limits),
            ctypes.sizeof(limits),
        )
        if not ok:
            self.close()
            raise ctypes.WinError(ctypes.get_last_error())

    def assign(self, process_handle: int) -> bool:
        return bool(kernel32.AssignProcessToJobObject(self._handle, process_handle))

    def terminate(self, exit_code: int = 1) -> None:
        kernel32.TerminateJobObject(self._handle, exit_code)

    def close(self) -> None:
        if self._handle:
            kernel32.CloseHandle(self._handle)  # KILL_ON_JOB_CLOSE 生效
            self._handle = None


if _is_windows():
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateJobObjectW.restype = wintypes.HANDLE
    kernel32.CreateJobObjectW.argtypes = [wintypes.LPVOID, wintypes.LPCWSTR]
    kernel32.SetInformationJobObject.restype = wintypes.BOOL
    kernel32.SetInformationJobObject.argtypes = [
        wintypes.HANDLE,
        ctypes.c_int,
        wintypes.LPVOID,
        ctypes.c_uint32,
    ]
    kernel32.AssignProcessToJobObject.restype = wintypes.BOOL
    kernel32.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
    kernel32.TerminateJobObject.restype = wintypes.BOOL
    kernel32.TerminateJobObject.argtypes = [wintypes.HANDLE, ctypes.c_uint32]
    kernel32.CloseHandle.restype = wintypes.BOOL
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    # 进程存在性探测（测试与诊断用；os.kill(pid,0) 对已终止 PID 行为不稳定）
    kernel32.OpenProcess.restype = wintypes.HANDLE
    kernel32.OpenProcess.argtypes = [ctypes.c_uint32, wintypes.BOOL, ctypes.c_uint32]
    # 恢复挂起主线程（CREATE_SUSPENDED + assign 后再恢复，保证整树入作业）
    kernel32.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
    kernel32.CreateToolhelp32Snapshot.argtypes = [ctypes.c_uint32, ctypes.c_uint32]
    kernel32.Thread32First.restype = wintypes.BOOL
    kernel32.Thread32Next.restype = wintypes.BOOL
    kernel32.OpenThread.restype = wintypes.HANDLE
    kernel32.OpenThread.argtypes = [ctypes.c_uint32, wintypes.BOOL, ctypes.c_uint32]
    kernel32.ResumeThread.restype = ctypes.c_uint32
    kernel32.ResumeThread.argtypes = [wintypes.HANDLE]
else:
    kernel32 = None  # type: ignore[assignment]


class _THREADENTRY32(ctypes.Structure):
    _fields_ = [
        ("dwSize", ctypes.c_uint32),
        ("cntUsage", ctypes.c_uint32),
        ("th32ThreadID", ctypes.c_uint32),
        ("th32OwnerProcessID", ctypes.c_uint32),
        ("tpBasePri", ctypes.c_long),
        ("tpDeltaPri", ctypes.c_long),
        ("dwFlags", ctypes.c_uint32),
    ]


TH32CS_SNAPTHREAD = 0x00000004
THREAD_SUSPEND_RESUME = 0x0002
THREAD_QUERY_INFORMATION = 0x0040
CREATE_SUSPENDED = 0x00000004

if _is_windows():
    # rev29 P2：Thread32First/Thread32Next 依赖 _THREADENTRY32* 签名，须在类定义后声明
    # （此前仅设 restype，缺参数签名，64 位下指针截断风险；现全部 Win32 API 均配 argtypes）
    kernel32.Thread32First.argtypes = [wintypes.HANDLE, ctypes.POINTER(_THREADENTRY32)]
    kernel32.Thread32Next.argtypes = [wintypes.HANDLE, ctypes.POINTER(_THREADENTRY32)]


def resume_suspended_main_thread(pid: int) -> bool:
    """恢复 CREATE_SUSPENDED 启动的进程主线程（assign 完成后调用，
    保证该进程及其后续子进程全部位于作业内——消除先跑后指派竞态）。"""
    if not _is_windows():
        return False
    snap = kernel32.CreateToolhelp32Snapshot(TH32CS_SNAPTHREAD, 0)
    if snap == wintypes.HANDLE(-1).value or not snap:
        return False
    try:
        te = _THREADENTRY32()
        te.dwSize = ctypes.sizeof(te)
        ok = kernel32.Thread32First(snap, ctypes.byref(te))
        while ok:
            if te.th32OwnerProcessID == pid:
                h = kernel32.OpenThread(
                    THREAD_SUSPEND_RESUME | THREAD_QUERY_INFORMATION, False, te.th32ThreadID
                )
                if h:
                    kernel32.ResumeThread(h)
                    kernel32.CloseHandle(h)
                    return True
            ok = kernel32.Thread32Next(snap, ctypes.byref(te))
        return False
    finally:
        kernel32.CloseHandle(snap)


def run_with_job(
    cmd,
    cwd: str | None = None,
    env: dict | None = None,
    timeout: int = 120,
    shell: bool = True,
) -> subprocess.CompletedProcess:
    """在 Job Object 限制下执行命令（Windows）；非 Windows / 关闭 / 失败时降级为
    subprocess.run。超时先 TerminateJobObject 杀整棵树，再抛 TimeoutExpired。
    rev29：Popen 启动失败同样走 fail-closed/降级分支（proc 已预置 None，
    不会再以 UnboundLocalError 覆盖原始异常）。"""
    if not _is_windows() or not _job_enabled():
        return subprocess.run(
            cmd, cwd=cwd, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            timeout=timeout, shell=shell, creationflags=CREATE_NEW_PROCESS_GROUP,
        )
    job = None
    try:
        job = JobObject(
            memory_limit_mb=_memory_limit_mb(),
            active_process_limit=_active_process_limit(),
        )
    except Exception as e:
        # rev28：生产 fail-closed——作业创建失败直接终止执行，不降级无保护运行
        if _fail_closed():
            raise RuntimeError(f"[ProcessGuard] fail-closed：Job Object 创建失败，拒绝无保护执行: {e}") from e
        logger.warning("[ProcessGuard] Job Object 创建失败，降级为普通执行: %s", e)
        job = None
    if job is None:
        return subprocess.run(
            cmd, cwd=cwd, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            timeout=timeout, shell=shell, creationflags=CREATE_NEW_PROCESS_GROUP,
        )
    proc = None  # rev29：Popen 抛异常前必须初始化，否则 except 访问 proc 触发 UnboundLocalError
    try:
        # rev28：CREATE_SUSPENDED 启动 → assign → 恢复主线程，
        # 保证进程及其全部子进程从创建起即位于作业内（子进程无法逃逸竞态）
        proc = subprocess.Popen(
            cmd, cwd=cwd, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            shell=shell, creationflags=CREATE_NEW_PROCESS_GROUP | CREATE_SUSPENDED,
        )
        if not job.assign(proc._handle):
            raise RuntimeError("AssignProcessToJobObject 失败，进程不在作业保护内")
        if not resume_suspended_main_thread(proc.pid):
            raise RuntimeError("恢复挂起主线程失败")
        stdout, stderr = proc.communicate(timeout=timeout)
        return subprocess.CompletedProcess(proc.args, proc.returncode, stdout, stderr)
    except subprocess.TimeoutExpired:
        job.terminate()  # 杀整棵进程树
        logger.warning("[ProcessGuard] 命令超时（%ss），已终止 Job Object 进程树", timeout)
        raise
    except Exception as e:
        # rev28：生产 fail-closed——作业保护设置失败拒绝执行；开发降级普通 run
        if proc is not None and proc.poll() is None:
            try:
                proc.kill()
            except OSError:
                pass
        if _fail_closed():
            raise RuntimeError(f"[ProcessGuard] fail-closed：作业保护未建立，拒绝无保护执行: {e}") from e
        logger.warning("[ProcessGuard] 作业保护设置失败，降级为普通执行: %s", e)
        return subprocess.run(
            cmd, cwd=cwd, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            timeout=timeout, shell=shell, creationflags=CREATE_NEW_PROCESS_GROUP,
        )
    finally:
        job.close()


__all__ = ["JobObject", "resume_suspended_main_thread", "run_with_job"]
