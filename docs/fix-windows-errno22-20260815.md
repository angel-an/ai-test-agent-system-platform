# Windows [Errno 22] Invalid argument 修复记录

## 日期
2026-08-15

## 问题描述
在 Windows 上运行 LangGraph 服务时，智能体执行 Web 测试脚本（`execute_web_script` 工具）时，子进程调用会触发 `[Errno 22] Invalid argument` 错误，导致线程死锁、任务一直显示"运行中"。

## 根本原因
Windows 上使用了 `asyncio.WindowsSelectorEventLoopPolicy`（修复 psycopg/Redis 兼容性）后，`asyncio.create_subprocess_shell` 在长时间运行后会与 SelectorEventLoop 产生兼容性冲突，触发 `OSError: [Errno 22] Invalid argument`。

## 修复内容

### 文件: `backend/app/agents/tools/web/execution_tools.py`

#### 修复 1: `_run_subprocess_async` 函数 (第 943 行)
**变更前:**
```python
if is_windows:
    cmd_str = cmd if isinstance(cmd, str) else " ".join(cmd)
    process = await asyncio.create_subprocess_shell(cmd_str, **kwargs)
```

**变更后:**
```python
if is_windows:
    if isinstance(cmd, list):
        cmd = " ".join(f'"{arg}"' if " " in arg else arg for arg in cmd)
    kwargs["shell"] = True
    process = await asyncio.create_subprocess_exec(cmd, **kwargs)
```

**说明:**
- 使用 `create_subprocess_exec` 替代 `create_subprocess_shell`
- 通过 `shell=True` 参数让操作系统通过 COMSPEC 执行命令
- 对包含空格的参数自动添加引号包裹

#### 修复 2: `_kill_process_tree` 函数 (第 1016 行)
**变更前:**
```python
proc = await asyncio.create_subprocess_shell(
    f"taskkill /F /T /PID {process.pid}",
    stdout=asyncio.subprocess.DEVNULL,
    stderr=asyncio.subprocess.DEVNULL,
)
```

**变更后:**
```python
kill_cmd = f"taskkill /F /T /PID {process.pid}"
proc = await asyncio.create_subprocess_exec(
    kill_cmd,
    shell=True,
    stdout=asyncio.subprocess.DEVNULL,
    stderr=asyncio.subprocess.DEVNULL,
)
```

**说明:**
- 同样使用 `create_subprocess_exec + shell=True` 替代 `create_subprocess_shell`

#### 修复 3: `_pre_check_environment` 函数 (第 179 行)
**变更前:**
```python
proc = await asyncio.create_subprocess_shell(
    check_cmd,
    stdout=asyncio.subprocess.PIPE,
    stderr=asyncio.subprocess.PIPE,
    cwd=str(project_root)
)
```

**变更后:**
```python
is_windows = sys.platform == "win32"
if is_windows:
    proc = await asyncio.create_subprocess_exec(
        check_cmd,
        shell=True,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        cwd=str(project_root)
    )
else:
    proc = await asyncio.create_subprocess_shell(
        check_cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        cwd=str(project_root)
    )
```

**说明:**
- Windows 下使用 `create_subprocess_exec + shell=True`
- Linux/macOS 保持原有 `create_subprocess_shell` 不变

## 验证
- [x] `python -m py_compile` 语法检查通过
- [ ] 重启服务后实际运行测试验证

## 重启服务步骤
```bash
# 1. 结束现有 Python 进程
taskkill //F //IM python.exe

# 2. 重新启动服务
python start_server_postgres.py
```

## 相关文件（无需修改）
- `backend/app/services/execution/runner.py` - 已经是 `create_subprocess_exec`，无需修改
- `start_server_postgres.py` - Windows 事件循环策略设置保持不变

## 参考
- Python Issue: https://bugs.python.org/issue41605
- asyncio.create_subprocess_shell 在 Windows SelectorEventLoop 下存在已知问题
