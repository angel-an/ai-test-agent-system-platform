"""执行治理层 2b-B3：Windows Job Object 资源限制（进程隔离过渡层）。

能力：
- JOB_OBJECT_LIMIT_PROCESS_MEMORY：单进程内存上限（默认 1024MB，env EXEC_JOB_MEMORY_LIMIT_MB）；
- JOB_OBJECT_LIMIT_ACTIVE_PROCESS：活动进程上限（默认 32，env EXEC_JOB_ACTIVE_PROCESS_LIMIT）；
- JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE：作业句柄关闭时杀死整棵进程树（防 chrome/子进程残留）；
- 超时：只终止本次命令的进程树（``taskkill /F /T /PID``）后抛 subprocess.TimeoutExpired
  （沿用调用方超时语义）。**不再用 TerminateJobObject 收尾**——它杀作业内全部进程，
  会连坐共享/嵌套同一作业的宿主后端。终止方式通过异常的 ``pg_terminate_mode`` /
  ``pg_terminate_rc`` / ``pg_terminate_detail`` 上报（tree / kill_only / failed / none）。

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
from pathlib import Path
from typing import IO

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


#: 执行期输出回读上限（超限保留**尾部**——Playwright 统计行与失败栈都在末尾）。
#: rev95：超时被杀时日志文件是唯一现场来源，回读须有限防止内存爆。
EXEC_LOG_READ_LIMIT = 2 * 1024 * 1024


def _open_log_sink(path) -> "IO[bytes] | None":
    """打开执行期日志文件（父目录自动创建）。失败返回 None —— 观测能力缺失
    不得阻断执行本身（回退管道模式）。"""
    if not path:
        return None
    try:
        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        return open(p, "wb")  # noqa: SIM115 —— 句柄由调用方在 finally 中关闭
    except OSError as e:  # pragma: no cover - 磁盘异常
        logger.warning("[ProcessGuard] 执行日志不可写，回退管道模式: %s", e)
        return None


def _close_quiet(*handles) -> None:
    """幂等关闭文件句柄（stdout/stderr 可能共用同一对象）。"""
    for fh in handles:
        if fh is None:
            continue
        try:
            fh.close()
        except (OSError, ValueError):  # pragma: no cover
            pass


def _read_log_tail(path, limit: int = EXEC_LOG_READ_LIMIT) -> bytes:
    """回读执行期日志（须在写句柄关闭后调用，避免 Windows 文件共享歧义）。"""
    if not path:
        return b""
    try:
        p = Path(path)
        size = p.stat().st_size
        with open(p, "rb") as fh:
            if size > limit:
                fh.seek(size - limit)
                head = f"...[执行日志超限，仅保留末尾 {limit} 字节]...\n".encode()
                return head + fh.read()
            return fh.read()
    except OSError:
        return b""


def _win_creationflags() -> dict:
    """creationflags 仅 Windows 支持（POSIX 传入会 ValueError）。"""
    return {"creationflags": CREATE_NEW_PROCESS_GROUP} if _is_windows() else {}


#: taskkill 在"目标进程已不存在"时返回 128（Windows 约定），属**达成目标**而非失败
_TASKKILL_ALREADY_GONE = 128


def _decode_console(raw: bytes | None) -> str:
    """解码 taskkill 的控制台输出。

    中文 Windows 输出为 cp936，而 ``mbcs`` 只在 Windows 存在 —— 任一编码失败
    都不能影响主流程，因此逐个尝试后兜底 replace。
    """
    if not raw:
        return ""
    for enc in ("mbcs", "cp936", "utf-8"):
        try:
            return raw.decode(enc, errors="strict").strip()[:200]
        except (LookupError, UnicodeDecodeError):
            continue
    return raw.decode("utf-8", errors="replace").strip()[:200]


def _terminate_process_tree(pid: int) -> tuple[bool, int | None, str]:
    """终止 ``pid`` 的整棵进程树，且**只终止这一棵**。

    返回 ``(是否已确认终止, taskkill 返回码, 可读说明)``。

    rev96：旧实现只要 ``subprocess.run`` 不抛异常就认为成功，返回码从未检查 ——
    taskkill 因权限/参数失败时仍被记成"已终止"，真实原因被吞掉，事后无法判断
    现场是否干净。这里显式区分三种结果：

    - ``rc == 0``：进程树已终止；
    - ``rc == 128``：目标进程已不存在（超时前自退），同样达成目标；
    - 其它 rc / 调用异常：**失败**，由调用方决定降级方式（不再回退作业终止）。

    注意：失败时**不**回退 ``TerminateJobObject`` —— 它杀的是作业内全部进程，
    宿主后端与子进程共享/嵌套同一作业时会连坐（2026-10-17 实测三次后端中断）。
    """
    if not _is_windows():
        return False, None, "非 Windows 平台不使用 taskkill"
    try:
        completed = subprocess.run(
            ["taskkill", "/F", "/T", "/PID", str(pid)],
            capture_output=True, timeout=30,
        )
    except FileNotFoundError as exc:
        return False, None, f"taskkill 不可用: {exc}"
    except subprocess.TimeoutExpired:
        return False, None, "taskkill 自身超时（30s）"
    except Exception as exc:  # noqa: BLE001 - 任何异常都必须如实上报，不能当成功
        return False, None, f"taskkill 调用异常: {type(exc).__name__}: {exc}"
    rc = completed.returncode
    detail = _decode_console(completed.stdout) or _decode_console(completed.stderr)
    if rc == 0:
        return True, rc, f"taskkill 已终止进程树{('：' + detail) if detail else ''}"
    if rc == _TASKKILL_ALREADY_GONE:
        return True, rc, f"进程树已不存在（taskkill rc=128）{('：' + detail) if detail else ''}"
    return False, rc, f"taskkill 失败 rc={rc}{('：' + detail) if detail else ''}"


def _handle_plain_timeout(exc: subprocess.TimeoutExpired, use_log: bool, out_path, err_path):
    """无 Job Object 路径的超时处理：PIPE 模式沿用 subprocess.run 已附带的输出，
    文件模式改从日志回读（rev95：超时现场不丢）。"""
    if use_log:
        exc.stdout = _read_log_tail(out_path)
        exc.stderr = _read_log_tail(err_path)
    return exc


def _run_plain(
    cmd, cwd, env, timeout, shell, use_log: bool, out_f, err_f, out_path, err_path
) -> subprocess.CompletedProcess:
    """未启用 Job Object 时的执行（POSIX / EXEC_JOB_ENABLED=0 / 作业创建失败降级）。"""
    out_arg = out_f if use_log else subprocess.PIPE
    err_arg = err_f if use_log else subprocess.PIPE
    try:
        return subprocess.run(
            cmd, cwd=cwd, env=env, stdout=out_arg, stderr=err_arg,
            timeout=timeout, shell=shell, **_win_creationflags(),
        )
    except subprocess.TimeoutExpired as exc:
        _close_quiet(out_f, err_f)  # 先落定写句柄，再回读（Windows 文件共享）
        raise _handle_plain_timeout(exc, use_log, out_path, err_path)
    finally:
        _close_quiet(out_f, err_f)


def run_with_job(
    cmd,
    cwd: str | None = None,
    env: dict | None = None,
    timeout: int = 120,
    shell: bool = True,
    *,
    stdout_path: str | None = None,
    stderr_path: str | None = None,
) -> subprocess.CompletedProcess:
    """在 Job Object 限制下执行命令（Windows）；非 Windows / 关闭 / 失败时降级为
    subprocess.run。超时先 TerminateJobObject 杀整棵树，再抛 TimeoutExpired。
    rev29：Popen 启动失败同样走 fail-closed/降级分支（proc 已预置 None，
    不会再以 UnboundLocalError 覆盖原始异常）。

    rev95（执行期可观测性 / 超时快照）：
    传入 stdout_path / stderr_path 时，子进程输出**实时落盘**（不再走 PIPE），
    进程被杀/超时后日志已存在于磁盘 —— 回读内容同时附到 TimeoutExpired 与
    CompletedProcess（返回值语义不变，超限保留尾部）。只传一个路径时，
    另一个输出合并进同一文件，避免 PIPE 缓冲在超时后丢失。
    **不传路径 = 完全保持原 PIPE 行为（默认零影响）。**
    """
    use_log = bool(stdout_path or stderr_path)
    out_path = stdout_path or stderr_path
    err_path = stderr_path or stdout_path
    out_f = _open_log_sink(out_path) if use_log else None
    err_f = _open_log_sink(err_path) if use_log else None
    if use_log:
        if out_f is None and err_f is None:
            logger.warning("[ProcessGuard] 执行日志均不可写，回退管道模式")
            use_log = False
        else:
            out_f = out_f or err_f   # 单文件可写时两路合并
            err_f = err_f or out_f
    out_arg = out_f if use_log else subprocess.PIPE
    err_arg = err_f if use_log else subprocess.PIPE

    if not _is_windows() or not _job_enabled():
        try:
            return _run_plain(cmd, cwd, env, timeout, shell, use_log, out_f, err_f,
                              out_path, err_path)
        finally:
            _close_quiet(out_f, err_f)
    job = None
    try:
        job = JobObject(
            memory_limit_mb=_memory_limit_mb(),
            active_process_limit=_active_process_limit(),
        )
    except Exception as e:
        # rev28：生产 fail-closed——作业创建失败直接终止执行，不降级无保护运行
        if _fail_closed():
            _close_quiet(out_f, err_f)
            raise RuntimeError(f"[ProcessGuard] fail-closed：Job Object 创建失败，拒绝无保护执行: {e}") from e
        logger.warning("[ProcessGuard] Job Object 创建失败，降级为普通执行: %s", e)
        job = None
    if job is None:
        try:
            return _run_plain(cmd, cwd, env, timeout, shell, use_log, out_f, err_f,
                              out_path, err_path)
        finally:
            _close_quiet(out_f, err_f)
    proc = None  # rev29：Popen 抛异常前必须初始化，否则 except 访问 proc 触发 UnboundLocalError
    try:
        # rev28：CREATE_SUSPENDED 启动 → assign → 恢复主线程，
        # 保证进程及其全部子进程从创建起即位于作业内（子进程无法逃逸竞态）
        proc = subprocess.Popen(
            cmd, cwd=cwd, env=env, stdout=out_arg, stderr=err_arg,
            shell=shell, creationflags=CREATE_NEW_PROCESS_GROUP | CREATE_SUSPENDED,
        )
        if not job.assign(proc._handle):
            raise RuntimeError("AssignProcessToJobObject 失败，进程不在作业保护内")
        if not resume_suspended_main_thread(proc.pid):
            raise RuntimeError("恢复挂起主线程失败")
        stdout, stderr = proc.communicate(timeout=timeout)
        if use_log:
            # rev95：回读前先落定句柄，避免 Windows 文件共享歧义
            _close_quiet(out_f, err_f)
            stdout = _read_log_tail(out_path)
            stderr = _read_log_tail(err_path)
        return subprocess.CompletedProcess(proc.args, proc.returncode, stdout, stderr)
    except subprocess.TimeoutExpired as exc:
        # rev96（2026-10-17）：旧实现两个硬伤 ——
        # ① taskkill 从不看返回码，失败也记成"已终止"，真实原因被吞掉；
        # ② 失败时回退 job.terminate()（TerminateJobObject），而它杀的是**作业内全部进程**，
        #    宿主后端一旦与子进程共享/嵌套同一作业就会连坐（本日三次后端中断均为该形态）。
        # 现在：只终止本次命令的进程树；失败**不回退作业终止**，降级为仅杀主进程并如实上报。
        # 取舍说明：宁可留下可能残留的子进程（可观测、可清理），也不冒杀死宿主的风险。
        tree_ok, tree_rc, tree_detail = (False, None, "未启动子进程")
        if proc is not None:
            tree_ok, tree_rc, tree_detail = _terminate_process_tree(proc.pid)
        if tree_ok:
            terminate_mode = "tree"
        elif proc is not None:
            try:
                proc.kill()  # 仅主进程；子进程可能残留，但不波及宿主
                terminate_mode = "kill_only"
                tree_detail = f"{tree_detail}；已降级为仅杀主进程（子进程可能残留）"
            except OSError as kill_exc:  # pragma: no cover - 极端情况
                terminate_mode = "failed"
                tree_detail = f"{tree_detail}；主进程强杀失败: {kill_exc}"
        else:
            terminate_mode = "none"
        if proc is not None:
            try:
                proc.wait(timeout=10)  # 等整树退出，确保日志缓冲落盘
            except Exception:  # pragma: no cover - 极端情况下进程僵死
                pass
        # 终止方式与返回码附到异常上：上层据此判断"现场可信度"，
        # 不再需要从日志措辞反推是否真的杀干净了。
        exc.pg_terminate_mode = terminate_mode
        exc.pg_terminate_rc = tree_rc
        exc.pg_terminate_detail = tree_detail
        if use_log:
            _close_quiet(out_f, err_f)
            exc.stdout = _read_log_tail(out_path)
            exc.stderr = _read_log_tail(err_path)
            logger.warning(
                "[ProcessGuard] 命令超时（%ss），终止方式=%s（%s）；"
                "现场已落盘 %s（%d 字节）",
                timeout, terminate_mode, tree_detail, out_path, len(exc.stdout or b""),
            )
        else:
            logger.warning(
                "[ProcessGuard] 命令超时（%ss），终止方式=%s（%s）",
                timeout, terminate_mode, tree_detail,
            )
        raise exc
    except Exception as e:
        # rev28：生产 fail-closed——作业保护设置失败拒绝执行；开发降级普通 run
        if proc is not None and proc.poll() is None:
            try:
                proc.kill()
            except OSError:
                pass
        if _fail_closed():
            _close_quiet(out_f, err_f)
            raise RuntimeError(f"[ProcessGuard] fail-closed：作业保护未建立，拒绝无保护执行: {e}") from e
        logger.warning("[ProcessGuard] 作业保护设置失败，降级为普通执行: %s", e)
        try:
            return _run_plain(cmd, cwd, env, timeout, shell, use_log, out_f, err_f,
                              out_path, err_path)
        finally:
            _close_quiet(out_f, err_f)
    finally:
        job.close()
        _close_quiet(out_f, err_f)


__all__ = ["JobObject", "resume_suspended_main_thread", "run_with_job"]
