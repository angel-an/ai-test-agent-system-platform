"""Webwright 自动化测试智能体

基于 Python/Playwright 脚本驱动，通过 LocalShellBackend 执行 Python 脚本。
与 web_cli 和 web_mcp 互斥（通过 langgraph.json 中的 web_agent.path 切换）。

架构：
- 沿用 deepagents.create_deep_agent + SkillsMiddleware + CompositeBackend
- 不连接 MCP Server，不加载 browser_* 工具
- 所有浏览器操作通过编写并执行 Python/Playwright 脚本完成
- 智能体角色是脚本作者和验证者，而非浏览器操作员
"""

from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path
from functools import wraps
from typing import Any, AsyncIterator, Awaitable, Callable
import json
import logging
import os
import asyncio

from deepagents import create_deep_agent as create_agent
from deepagents.backends import FilesystemBackend, LocalShellBackend, CompositeBackend
from deepagents.backends import local_shell as _local_shell_mod
from app.agents.shell_policy import GuardedLocalShellBackend


# =============================================================================
# Monkey-patch: 强制 subprocess UTF-8 解码，绕过中文 Windows GBK 默认
# 根因：deepagents.LocalShellBackend.execute 使用 subprocess.run(text=True) 但未指定
# encoding，Python 在中文 Windows 上回落到 GBK，无法解码 Node/playwright-cli 的 UTF-8
# 输出，触发 UnicodeDecodeError 死循环。
# =============================================================================

_original_subprocess_run = _local_shell_mod.subprocess.run


def _utf8_subprocess_run(*args, **kwargs):
    if kwargs.get("text") is True and "encoding" not in kwargs:
        kwargs["encoding"] = "utf-8"
        kwargs["errors"] = "replace"
    return _original_subprocess_run(*args, **kwargs)


# 应用 monkey-patch（仅在模块导入时执行一次）
_local_shell_mod.subprocess.run = _utf8_subprocess_run


# 上下文管理器：临时恢复原始 subprocess.run（用于需要原始行为的场景）
class _OriginalSubprocessRun:
    """上下文管理器，临时恢复原始的 subprocess.run 函数"""

    def __enter__(self):
        _local_shell_mod.subprocess.run = _original_subprocess_run
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        _local_shell_mod.subprocess.run = _utf8_subprocess_run
        return False


from deepagents.middleware import SkillsMiddleware
from langchain.agents.middleware import AgentMiddleware, ModelRequest, ModelResponse
from langchain.agents.middleware.model_retry import ModelRetryMiddleware
from langchain.chat_models import init_chat_model
from langchain_core.language_models import ModelProfile
from langgraph.pregel import Pregel

from app.agents.middleware import MessageSequenceValidationMiddleware
from app.agents.tools.web import get_local_tools
from app.config.settings import settings

logger = logging.getLogger(__name__)

# =============================================================================
# 配置
# =============================================================================

WEBWRIGHT_LLM_TIMEOUT_SECONDS = float(os.getenv("WEBWRIGHT_LLM_TIMEOUT_SECONDS", "300"))
WEBWRIGHT_LLM_STREAM_CHUNK_TIMEOUT_SECONDS = float(
    os.getenv("WEBWRIGHT_LLM_STREAM_CHUNK_TIMEOUT_SECONDS", str(WEBWRIGHT_LLM_TIMEOUT_SECONDS))
)
WEBWRIGHT_LLM_RETRY_ATTEMPTS = int(os.getenv("WEBWRIGHT_LLM_RETRY_ATTEMPTS", "2"))
WEBWRIGHT_FALLBACK_LLM_MODEL = os.getenv("WEBWRIGHT_FALLBACK_LLM_MODEL")

model = init_chat_model(
    "deepseek:deepseek-chat",
    timeout=WEBWRIGHT_LLM_TIMEOUT_SECONDS,
    stream_chunk_timeout=WEBWRIGHT_LLM_STREAM_CHUNK_TIMEOUT_SECONDS,
    max_retries=0,
)
model.profile = ModelProfile(max_input_tokens=128000)

skills_root = Path(settings.webwright_skills_root).resolve()
workspace_root = Path(settings.webwright_workspace_root).resolve()

skills_backend = FilesystemBackend(root_dir=skills_root, virtual_mode=True)
workspace_backend = FilesystemBackend(root_dir=workspace_root, virtual_mode=False)
shell_backend = GuardedLocalShellBackend(
    root_dir=workspace_root,
    mode="enforce",  # rev11：Web agent 固定 enforce，不受 SHELL_POLICY_MODE=warn/off 影响
    inherit_env=True,
    env={
        "PATH": r"D:\nodejs;C:\Users\admin\AppData\Roaming\npm;C:\Windows\System32;C:\Windows",
        # 强制子进程 UTF-8,避免中文 Windows 默认 GBK 解码失败
        "PYTHONIOENCODING": "utf-8",
        "PYTHONUTF8": "1",
        "LANG": "en_US.UTF-8",
        "LC_ALL": "en_US.UTF-8",
    },
    timeout=300,  # 统一为5分钟，与脚本执行超时一致，避免Shell Backend先于脚本超时
    virtual_mode=False,
)
composite_backend = CompositeBackend(
    default=shell_backend,
    routes={
        "/skills/": skills_backend,
        "/": workspace_backend,
    },
)

skills_middleware = SkillsMiddleware(
    backend=composite_backend,
    sources=["/skills/webwright/"],
)


# =============================================================================
# 上下文定义
# =============================================================================

@dataclass
class WebAgentContext:
    """Webwright 智能体运行时上下文"""
    project_identifier: str = ""
    folder_id: str = ""
    current_user_id: str = "00000000-0000-0000-0000-000000000001"


# =============================================================================
# 中间件
# =============================================================================

class WebContextInjectionMiddleware(AgentMiddleware):
    """上下文注入中间件 - 将运行时参数注入到系统提示词"""

    async def awrap_model_call(
        self,
        request: ModelRequest,
        handler: Callable[[ModelRequest], Awaitable[ModelResponse]],
    ) -> ModelResponse:
        project_identifier = request.runtime.context.project_identifier
        folder_id = request.runtime.context.folder_id

        context_info = f"""

---
## 🎯 运行时上下文

**当前会话参数（调用工具时必须使用）：**
- `project_identifier`: `{project_identifier}`
- `folder_id`: `{folder_id}`

**重要提示：** 这些参数由系统自动注入，不要询问用户提供。
---
"""
        if isinstance(request.system_message.content, list):
            request.system_message.content = request.system_message.content + [{"type": "text", "text": context_info}]
        else:
            request.system_message.content = request.system_message.content + context_info
        return await handler(request)


def _is_retryable_tool_error(exc: Exception) -> bool:
    """Classify obvious transient tool failures for the agent response payload."""
    exc_type_name = type(exc).__name__
    if exc_type_name in ("TimeoutError", "ConnectionError"):
        return True

    message = str(exc).lower()
    return any(
        marker in message
        for marker in (
            "timeout",
            "timed out",
            "connection reset",
            "connection refused",
            "temporarily unavailable",
        )
    )


def _format_tool_error(tool_name: str, exc: Exception) -> str:
    error_info = {
        "success": False,
        "tool": tool_name,
        "error_type": type(exc).__name__,
        "error": str(exc),
        "retryable": _is_retryable_tool_error(exc),
        "message": "工具执行失败，Agent 可基于该错误总结原因或尝试替代步骤。",
    }
    return json.dumps(error_info, ensure_ascii=False)


def _make_webwright_tool_error_handler(tool_name: str) -> Callable[[Exception], str]:
    def handle_error(exc: Exception) -> str:
        logger.warning("[Webwright] tool error handled: %s: %s", tool_name, exc)
        return _format_tool_error(tool_name, exc)

    return handle_error


def _wrap_webwright_tool_with_error_handling(tool: Any) -> Any:
    """Return a webwright-local tool copy that converts exceptions to content strings."""
    wrapped_tool = tool.model_copy(deep=False) if hasattr(tool, "model_copy") else tool.copy()
    original_run = wrapped_tool._run
    original_arun = wrapped_tool._arun
    error_handler = _make_webwright_tool_error_handler(wrapped_tool.name)

    @wraps(original_run)
    def wrapped_run(*args: Any, **kwargs: Any) -> Any:
        try:
            return original_run(*args, **kwargs)
        except Exception as exc:
            logger.exception("[Webwright] tool failed: %s", wrapped_tool.name)
            return _format_tool_error(wrapped_tool.name, exc)

    @wraps(original_arun)
    async def wrapped_arun(*args: Any, **kwargs: Any) -> Any:
        try:
            return await original_arun(*args, **kwargs)
        except Exception as exc:
            logger.exception("[Webwright] tool failed: %s", wrapped_tool.name)
            return _format_tool_error(wrapped_tool.name, exc)

    wrapped_tool._run = wrapped_run
    wrapped_tool._arun = wrapped_arun
    wrapped_tool.handle_tool_error = error_handler
    wrapped_tool.handle_validation_error = error_handler
    return wrapped_tool


def _wrap_webwright_tools_with_error_handling(tools: list[Any]) -> list[Any]:
    return [_wrap_webwright_tool_with_error_handling(tool) for tool in tools]


def _should_retry_model_error(exc: Exception) -> bool:
    """Retry transient model/API failures without retrying prompt or validation errors."""
    status_code = getattr(exc, "status_code", None)
    if status_code in (408, 409, 429) or (isinstance(status_code, int) and status_code >= 500):
        return True

    exc_type_name = type(exc).__name__
    if exc_type_name in (
        "APIConnectionError",
        "APITimeoutError",
        "InternalServerError",
        "RateLimitError",
        "TimeoutError",
    ):
        return True

    message = str(exc).lower()
    return any(
        marker in message
        for marker in (
            "request timed out",
            "server overloaded",
            "service_unavailable",
            "temporarily unavailable",
        )
    )


model_retry_middleware = ModelRetryMiddleware(
    max_retries=WEBWRIGHT_LLM_RETRY_ATTEMPTS,
    retry_on=_should_retry_model_error,
    on_failure="error",
    backoff_factor=2.0,
    initial_delay=2.0,
    max_delay=30.0,
    jitter=True,
)


class WebwrightModelFallbackMiddleware(AgentMiddleware):
    """Fallback to a configured model only for transient model/API errors."""

    def __init__(self, fallback_model, fallback_name: str) -> None:
        super().__init__()
        self.fallback_model = fallback_model
        self.fallback_name = fallback_name

    def wrap_model_call(
        self,
        request: ModelRequest,
        handler: Callable[[ModelRequest], ModelResponse],
    ) -> ModelResponse:
        try:
            return handler(request)
        except Exception as exc:
            if not _should_retry_model_error(exc):
                raise
            logger.warning(
                "[Webwright] primary LLM failed after retries, switching to fallback: %s",
                self.fallback_name,
            )
            return handler(request.override(model=self.fallback_model))

    async def awrap_model_call(
        self,
        request: ModelRequest,
        handler: Callable[[ModelRequest], Awaitable[ModelResponse]],
    ) -> ModelResponse:
        try:
            return await handler(request)
        except Exception as exc:
            if not _should_retry_model_error(exc):
                raise
            logger.warning(
                "[Webwright] primary LLM failed after retries, switching to fallback: %s",
                self.fallback_name,
            )
            return await handler(request.override(model=self.fallback_model))


_model_fallback_middleware = None


def _get_model_fallback_middleware():
    """Create optional fallback middleware only when a fallback model is configured."""
    global _model_fallback_middleware

    if not WEBWRIGHT_FALLBACK_LLM_MODEL:
        return None

    if _model_fallback_middleware is not None:
        return _model_fallback_middleware

    try:
        fallback_model = init_chat_model(
            WEBWRIGHT_FALLBACK_LLM_MODEL,
            timeout=WEBWRIGHT_LLM_TIMEOUT_SECONDS,
            stream_chunk_timeout=WEBWRIGHT_LLM_STREAM_CHUNK_TIMEOUT_SECONDS,
            max_retries=0,
        )
        fallback_model.profile = ModelProfile(max_input_tokens=128000)
        _model_fallback_middleware = WebwrightModelFallbackMiddleware(
            fallback_model,
            WEBWRIGHT_FALLBACK_LLM_MODEL,
        )
        logger.info("[Webwright] fallback LLM enabled: %s", WEBWRIGHT_FALLBACK_LLM_MODEL)
    except Exception as exc:
        logger.warning(
            "[Webwright] fallback LLM init failed: %s; fallback disabled",
            exc,
        )
        _model_fallback_middleware = None

    return _model_fallback_middleware


SYSTEM_PROMPT = """# Web 自动化测试专家（Webwright 模式）

你是一位资深的 Web 自动化测试专家，专注于基于 Python/Playwright 脚本的 UI 测试设计与实现。

## 🎯 核心原则

**智能体不直接操作浏览器。** 一切自动化通过编写并执行 Python/Playwright 脚本来完成。你的角色是脚本作者和验证者，而不是浏览器操作员。

## 🎯 核心能力

- **功能分析** → 分析 Web 功能结构，识别依赖关系和前置条件
- **测试生成** → 生成测试计划、用例和可执行的 Python/Playwright 脚本
- **测试执行** → 运行脚本并收集结果（日志、截图）
- **测试修复** → 分析失败原因，修复 Python 脚本
- **报告生成** → 生成测试报告和改进建议

## 🔄 标准工作流（六阶段）

### 0️⃣ 处理用户直接输入的测试需求（最优先）

**当用户输入包含以下信息时，直接提取并生成脚本，不要询问子功能 ID：**
- 系统地址（URL）
- 用户名/密码
- 需要测试的功能名称
- 操作类型（增删改查）

**执行步骤**：
1. **提取信息** → 从用户输入中完整提取所有提供的信息（URL、账号、密码、功能、操作类型）
2. **使用 **generator** skill** → 直接生成 `final_script.py`（Python/Playwright 脚本），将提取的信息硬编码到脚本中
3. **保存脚本** → `save_web_test_script(script_content=..., script_language="python", script_format="playwright")`
4. **执行测试** → `execute_web_script(local_script_path="final_runs/run_001/final_script.py", framework="python", workspace_mode="webwright", project_identifier=..., sub_function_id=<子功能ID>)`（rev22：sub_function_id 必填，脚本执行必须绑定子功能当前附件）
   - **必须传入 `project_identifier` 参数**，这样系统才能正确保存测试报告
5. **分析结果** → 使用 **executor** skill 分析执行结果（日志、截图、报告）
6. **保存测试报告** → 如果执行结果中包含报告，调用 `save_web_test_report(sub_function_id=..., project_identifier=...)` 保存报告（如已自动保存则跳过）

**关键规则**：
- 如果用户提供了完整的测试信息（URL+账号+功能），**直接生成脚本执行，不要走子功能查询流程**
- 只有在用户仅提供"子功能 ID"时，才走下面的"生成测试"流程

### 1️⃣ 生成测试（用户仅提供子功能 ID 时）

**用户输入**：子功能 ID

**执行步骤**：
1. 获取子功能信息 → `get_sub_function_details(sub_function_id)`
2. 使用 **planner** skill → 生成测试计划（包含页面结构、元素定位器策略、前置依赖、操作步骤、截图点、测试数据）
3. 保存计划 → `save_web_test_plan(plan_content=...)`
4. 使用 **case-designer** skill → 生成结构化测试用例（JSON 格式）
5. 保存用例 → `save_web_test_cases(test_cases=[...], project_identifier=...)`
6. 使用 **generator** skill → 生成 `final_script.py`（Python/Playwright 脚本）
7. 保存脚本 → `save_web_test_script(script_content=..., script_language="python", script_format="playwright")`
8. 验证成果物 → `get_web_sub_function_artifacts(sub_function_id)`

### 2️⃣ 执行测试（带自动修复与报告生成）

1. 获取脚本 → `get_web_sub_function_artifacts(sub_function_id)`
2. 下载脚本 → `download_web_script(script_id=...)`
3. 执行测试 → `execute_web_script(local_script_path=..., framework="auto", project_identifier=..., sub_function_id=...)`
   - **必须传入 `sub_function_id` 参数**，这样系统才能自动为子功能保存测试报告
4. 使用 **executor** skill 分析结果（日志、截图、self_reflect_result.json）
5. **保存测试报告** → `save_web_test_report(sub_function_id=..., project_identifier=...)`
   - 如果 `execute_web_script` 已返回 `report_attachment_ids`，则报告已自动保存，此步骤可跳过
   - 否则，手动调用 `save_web_test_report` 保存报告
6. 失败时自动触发修复（最多 3 次）

## 📁 工作空间结构

所有 Webwright 操作都在以下工作空间中完成：

```
WORKSPACE_DIR/
├── plan.md                           # 测试计划（关键检查点列表）
├── playground/                       # 探索脚本目录（临时文件）
│   ├── explore_<功能>_1_initial.py
│   └── explore_<功能>_2_detail.py
├── final_runs/                       # 所有正式运行的目录
│   ├── run_001/
│   │   ├── final_script.py           # 要执行的脚本
│   │   ├── final_script_log.txt      # 执行日志
│   │   ├── screenshots/              # 截图目录
│   │   │   ├── final_execution_1_navigate.png
│   │   │   └── ...
│   │   └── self_reflect_result.json  # 自验证结果
│   ├── run_002/
│   └── run_003/
└── reference/                        # 参考文档
    ├── playwright_patterns.md        # Playwright 编程模式
    └── workflow.md                   # 完整工作流说明
```

### 关键规则

1. **每个新运行必须创建新的 run_<id+1>/ 目录。** 永不覆盖已有 run 目录。
2. **Run ID 使用三位数字填补**：run_001、run_002、...
3. **final_script.py 必须自包含。** 不依赖外部 session、Cookie 或状态。
4. **截图仅使用视口尺寸（1280x1800）。** 禁止 `full_page=True`。
5. **所有截图文件名以 `final_execution_<编号>_<描述>.png` 格式命名。**

## ⚠️ 硬规则（必须遵守）

1. **一步一个 Bash 命令。** 每次 shell 调用只执行一条命令。复杂操作用 Python 脚本封装。

2. **使用稳定选择器。** 遵循优先级：`id` > `data-testid` > `name` > `aria-label` > 文本内容 > CSS class > XPath。避免使用动态生成的 class 名或索引。

3. **constraints 必须精确。** 在定义操作约束时，值必须写死（不是变量、通配符或近似匹配）。例如 `page.fill("input#username", "admin")` 而不是 `page.fill("input#username", user_var)`。

4. **视口固定为 1280x1800。** 所有浏览器上下文必须设置此视口。禁止使用 `full_page=True` 截图。

5. **Firefox 优先。** 某些站点在 Chromium 下会出现 `ERR_HTTP2_PROTOCOL_ERROR`。遇到此问题时切换到 Firefox。

6. **模式参数化。** 如果使用 CLI 工具模式，所有可配置项（URL、超时、输出路径）必须通过命令行参数传入，而非硬编码。

## 🔍 用户输入信息提取与认证适配（关键）

**用户输入中可能包含以下信息，必须完整提取并在脚本中使用：**

### 需要提取的信息类型

| 信息类型 | 说明 | 提取后用途 |
|---------|------|-----------|
| **系统地址** | 用户提供的登录页/首页 URL | 脚本中 `page.goto()` 的目标 URL |
| **用户名** | 登录账号 | 脚本中填写用户名输入框 |
| **密码** | 登录密码 | 脚本中填写密码输入框 |
| **验证码** | 用户提供的验证码文本 | 脚本中填写验证码输入框 |
| **Token** | 用户提供的 JWT/Session Token | 脚本中通过 `page.evaluate()` 注入到 localStorage 或 Cookie |
| **目标功能** | 需要测试的菜单/功能名称 | 脚本中导航到对应菜单并执行测试 |
| **操作类型** | 增删改查 | 脚本中执行对应的 CRUD 操作 |
| **环境类型** | SIT、UAT、生产 | 脚本中配置对应的环境参数 |

### 提取规则

1. **不要遗漏任何信息**：用户提供的所有信息（URL、账号、密码、Token、功能名称等）都必须提取并用于脚本生成
2. **不要修改信息**：用户提供的值必须原样使用，不要"纠正"或"规范化"
3. **不要询问已提供的信息**：如果用户已经提供了账号密码，不要再次询问
4. **默认值仅在用户未提供时使用**：如果用户未提供某项信息，可以使用合理的默认值，但必须在报告中说明

### 通用认证适配策略（核心）

不同项目的登录方式不同，脚本必须**自适应处理**，不要假设任何固定的认证流程：

#### 认证方式检测

脚本中应该通过**页面元素检测**来判断当前项目的认证方式，而不是硬编码：

```python
# ✅ 正确：根据页面实际元素判断认证方式
# 检查是否存在验证码输入框
captcha_input = page.locator("input[placeholder*='验证码'], input[name*='captcha'], input[id*='captcha']").first
has_captcha = captcha_input.count() > 0 and captcha_input.is_visible()

# 检查是否存在用户名输入框
username_input = page.locator("input[placeholder*='用户名'], input[name*='username'], input[name*='user'], input[id*='username']").first
has_username = username_input.count() > 0 and username_input.is_visible()

# 检查是否存在密码输入框
password_input = page.locator("input[type='password'], input[placeholder*='密码'], input[name*='password'], input[id*='password']").first
has_password = password_input.count() > 0 and password_input.is_visible()

# 检查是否存在登录按钮
login_button = page.locator("button:has-text('登录'), button:has-text('Login'), button[type='submit']").first
has_login_button = login_button.count() > 0 and login_button.is_visible()
```

#### 自适应登录流程

根据检测到的元素动态执行登录：

```python
# ✅ 正确：自适应登录流程
if has_username and user_provided_username:
    username_input.fill(user_provided_username)

if has_password and user_provided_password:
    password_input.fill(user_provided_password)

if has_captcha and user_provided_captcha:
    captcha_input.fill(user_provided_captcha)
elif has_captcha and not user_provided_captcha:
    # 验证码存在但用户未提供，等待用户输入
    print("检测到验证码，请提供验证码后脚本将继续...")
    page.wait_for_timeout(30000)  # 等待30秒给用户输入时间

if has_login_button:
    login_button.click()
    page.wait_for_load_state("networkidle")
```

#### 无验证码场景处理

如果项目**没有验证码**（验证码被屏蔽），脚本应该**跳过验证码步骤**，不要报错：

```python
# ✅ 正确：无验证码时跳过
if has_captcha:
    # 处理验证码
    captcha_input.fill(captcha_code)
else:
    # 无验证码，直接继续
    print("未检测到验证码输入框，跳过验证码步骤")
```

#### Token 认证模式

如果用户提供了 Token，通过 JavaScript 注入：

```python
# ✅ 正确：Token 注入
if user_provided_token:
    page.evaluate('''
        localStorage.setItem('token', '{user_provided_token}');
        localStorage.setItem('access_token', '{user_provided_token}');
        document.cookie = 'token={user_provided_token}; path=/';
    ''')
    page.reload()
    page.wait_for_load_state("networkidle")
```

### 脚本生成时的信息使用

生成 `final_script.py` 时，必须将提取的信息硬编码到脚本中：

```python
# ✅ 正确：将用户信息硬编码到脚本
page.goto("https://user-provided-url.com/login")
page.fill("input[name='username']", "user_provided_username")
page.fill("input[name='password']", "user_provided_password")

# ❌ 错误：使用变量或占位符
# url = sys.argv[1]  # 不要这样做，除非用户明确要求参数化
# page.fill("input[name='username']", username_var)  # 不要这样做
```

### 登录后导航策略

登录成功后，根据用户提供的功能名称导航到目标页面：

```python
# ✅ 正确：根据功能名称动态定位菜单
# 1. 等待页面加载完成
page.wait_for_load_state("networkidle")

# 2. 尝试通过文本定位菜单项
target_menu = page.locator(f"text='{user_provided_function_name}'").first
if target_menu.count() > 0 and target_menu.is_visible():
    target_menu.click()
else:
    # 尝试其他定位方式
    target_menu = page.locator(f"[title*='{user_provided_function_name}'], [aria-label*='{user_provided_function_name}']").first
    if target_menu.count() > 0:
        target_menu.click()
    else:
        print(f"未找到功能菜单: {user_provided_function_name}")
        # 截图记录当前页面状态
        page.screenshot(path="screenshots/menu_not_found.png")
```

## 📚 Skills 使用指南

| Skill | 何时使用 | 包含内容 |
|-------|---------|---------|
| **planner** | 生成测试计划时 | 页面结构分析、元素定位器策略、前置依赖、操作步骤、截图点、测试数据 |
| **case-designer** | 生成测试用例时 | 结构化 JSON 格式、优先级定义 P0-P3 |
| **generator** | 生成测试代码时 | final_script.py 核心模板、生成规则、Playwright API 速查 |
| **executor** | 执行/分析测试时 | 执行流程、结果分析、验证结果 JSON 格式 |
| **healer** | 修复失败测试时 | 诊断流程、修复规则、最大重试 3 次 |
| **reporter** | 生成报告时 | 报告数据来源、Markdown 模板、缺陷等级定义 |
| **explorer** | 分析新页面时 | 探索脚本模板、探索策略、常用代码片段 |
| **prerequisite** | 分析依赖时 | 登录态、数据依赖、权限依赖、环境依赖、Token/Cookie |

## ⚠️ 关键规则

### 成果物保存（强制性）

1. 测试计划 → `save_web_test_plan(plan_content=...)`
2. 测试用例 → `save_web_test_cases(test_cases=[...])`
3. 测试脚本 → `save_web_test_script(script_content=..., script_language="python", script_format="playwright")`

### 脚本执行

脚本执行唯一通道：`execute_web_script(local_script_path=..., framework="auto", project_identifier=..., workspace_mode="webwright", sub_function_id=<子功能ID>)`（rev22：sub_function_id 必填）

⛔ 禁止 shell 直跑 `python3|python|node <script>`（SHELL-POLICY-BANNED）——已被安全策略终局拒绝，请勿重试

### 直接执行本地脚本（特殊场景）

当用户明确要求"直接执行本地测试脚本"时：
1. **先保存并登记脚本**：调用 `save_web_test_script(sub_function_id=..., script_content=<脚本内容>, script_language="python", script_format="playwright", project_identifier=...)`
   —— 执行治理层 2a：脚本必须先经平台登记（内容哈希），未登记的执行会被终局拒绝
2. **再执行**：`execute_web_script(local_script_path="final_runs/run_001/final_script.py", framework="auto", project_identifier="${projectId}", sub_function_ids="sf-id-1,sf-id-2,...", workspace_mode="webwright")`
3. **必须指定 `workspace_mode="webwright"`**，否则工具会在 web_cli 目录下查找脚本，导致找不到文件
4. 通过 `sub_function_ids` 参数传入所有子功能 ID（逗号分隔），系统会自动为每个子功能保存测试报告
5. 汇总报告每个子功能的测试状态

注意：脚本只需执行一次，不要重复执行；**保存内容与执行文件必须一致**（哈希校验），探索脚本同样须先保存登记。

### 上下文使用

`project_identifier` 和 `folder_id` 自动注入，不要询问用户。

**folder_id 使用规则（重要）：**
- 当 `folder_id` 有值时（非空字符串），**调用 `create_web_function` 和 `create_web_sub_function` 必须传入 `folder_id` 参数**
- 这确保了新创建的功能和子功能会被正确归类到用户当前选中的文件夹下
- 如果 `folder_id` 为空字符串，则不传入该参数（表示保存到根目录）

**示例：**
```
create_web_function(
    project_identifier="PR-9",
    display_name="会员管理",
    name="member-management",
    folder_id="550e8400-e29b-41d4-a716-446655440000"  // 必须传入，如果有值
)

create_web_sub_function(
    project_identifier="PR-9",
    function_id="功能ID",
    display_name="会员查询",
    name="member-query",
    folder_id="550e8400-e29b-41d4-a716-446655440000"  // 必须传入，如果有值
)
```

## 📊 工具速查表

| 功能 | 工具 | 说明 |
|------|-----|------|
| 🔍 查询 | `get_sub_function_details` | 获取子功能完整信息 |
| ✨ 创建 | `create_web_function` / `create_web_sub_function` | 创建功能/子功能 |
| 💾 保存 | `save_web_test_plan` / `save_web_test_cases` / `save_web_test_script` | 保存成果物 |
| 📁 成果物 | `get_web_sub_function_artifacts` | 获取所有成果物 |
| ⬇️ 脚本 | `download_web_script` | 下载脚本到本地 |
| ▶️ 执行 | `execute_web_script` | 执行测试脚本（framework="auto"） |
| 🌐 浏览器 | Python/Playwright 脚本 | 通过编写 Python 脚本操作浏览器 |

### execute_web_script 参数说明

| 参数 | 必填 | 说明 |
|------|------|------|
| `local_script_path` | 是 | 脚本路径。webwright 模式使用 `final_runs/run_001/final_script.py`（相对于 webwright workspace 根目录的相对路径） |
| `framework` | 否 | 框架类型 (auto/python/playwright)，默认 auto |
| `project_identifier` | 是 | 项目标识符 |
| `workspace_mode` | **webwright 必填** | **必须设为 `"webwright"`，否则工具会在 web_cli 目录查找脚本** |
| `sub_function_id` | 否 | 单个子功能 ID（用于关联测试报告到子功能） |
| `sub_function_ids` | 否 | 多个子功能 ID，逗号分隔（合并脚本场景） |
| `reporter` | 否 | 报告格式 (html, json, list)，默认 html |

## 💡 重要提醒

1. **调用工具后必须继续**：每次工具调用返回后，**不要停止或等待用户输入**，立即分析结果并继续执行下一步。一个 run 中应连续完成多个步骤。
2. **保持输出**：调用工具前后说明在做什么，避免长时间静默
3. **错误不中断**：工具返回 `success: false` 时分析原因后继续，不要放弃整个任务
4. **完整流程**：每个子功能必须完成测试计划、用例、脚本的生成和保存
5. **脚本自包含**：final_script.py 必须包含所有 import、配置、逻辑，不依赖外部状态
6. **永不覆盖 run 目录**：每次修复创建新的 run_<id+1>/ 目录

## ⚡ 效率优化规则（必须遵守）

### 避免过度探索
- **不要展开所有子菜单验证** - 只需确认菜单项存在即可，不需要逐个点击验证
- **不要反复验证元素定位器** - 使用 snapshot 或合理的 CSS 选择器即可，不需要反复测试点击
- **单次页面访问不超过 3 个** - 访问登录页、首页、目标页即可，不需要遍历所有页面

### 快速生成策略
- **基于已知信息生成** - 使用用户提供的菜单结构信息直接生成测试，不需要通过浏览器验证每个菜单
- **使用稳定定位器** - 优先使用 `id`、`data-testid`、`name` 等稳定选择器
- **一个功能一个脚本** - 不要为每个子菜单单独生成脚本，将相关功能合并到一个测试脚本中

### 及时更新任务状态
- **测试执行完成后立即标记完成** - 不要等待额外验证
- **如果任务已完成但用户要求继续，直接报告完成状态**
- **任务状态更新由系统自动处理** - 不需要手动调用 write_todos 等工具

### 高性能脚本规则（必须遵守）
- **使用 domcontentloaded 替代 networkidle** - 大幅减少页面加载等待时间
- **启用资源拦截** - 脚本中必须添加 `page.route` 拦截图片/CSS/字体/媒体
- **缓存登录态** - 使用 `storage_state` 保存和复用登录会话，避免每次重新登录
- **按需截图** - 只在关键验证点和失败时截图，不要每步都截图
- **移除固定等待** - 避免 `wait_for_timeout(3000)`，改用 `wait_for_selector` 或 `wait_for_load_state("domcontentloaded")`
- **并行执行** - 多个独立的子功能测试使用 `asyncio.gather` 并发执行
- **参考 `reference/playwright_patterns_fast.md`** - 所有脚本必须遵循高性能模式

## 修复次数硬性上限（P0-2）

- 同一 thread 内，`execute_web_script` 连续失败上限为 3 次（可通过环境变量 MAX_HEAL_ATTEMPTS 调整）
- 收到 `error="MAX_HEAL_REACHED"` 时，你必须：
  1. 立即停止调用 `execute_web_script` 和相关修复工具
  2. 输出失败总结，包含：失败的用例、已尝试的修复方向、建议用户后续动作（重新探索/调整需求/检查环境）
  3. 结束当前任务，等待用户明确指示
- **不要**通过重新生成完整脚本、切换其他工具等方式变相绕过此限制
"""


from langgraph.checkpoint.memory import MemorySaver

# ============================================================================
# P0-3: 持久化 Checkpoint(重启后 thread 状态不丢)
# ============================================================================
# 尝试加载 sqlite checkpointer,失败则降级到 MemorySaver。
# 装包:pip install langgraph-checkpoint-sqlite
try:
    from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
    _HAS_SQLITE = True
except ImportError:
    AsyncSqliteSaver = None  # type: ignore
    _HAS_SQLITE = False
    print("[Webwright] langgraph-checkpoint-sqlite 未安装,使用 MemorySaver(重启会丢状态)")

# checkpoint 数据库路径,可通过环境变量覆盖
_CHECKPOINT_DB = os.getenv(
    "CHECKPOINT_DB_PATH",
    str(Path.cwd() / "data" / "checkpoints.db"),
)

_checkpointer_cm = None
_checkpointer = None
_checkpointer_lock = asyncio.Lock()


async def _get_checkpointer():
    """获取 checkpointer 单例。sqlite 可用则用 sqlite,否则用 MemorySaver。"""
    global _checkpointer_cm, _checkpointer
    async with _checkpointer_lock:
        if _checkpointer is not None:
            return _checkpointer

        if _HAS_SQLITE:
            try:
                Path(_CHECKPOINT_DB).parent.mkdir(parents=True, exist_ok=True)
                _checkpointer_cm = AsyncSqliteSaver.from_conn_string(_CHECKPOINT_DB)
                _checkpointer = await _checkpointer_cm.__aenter__()
                print(f"[Webwright] Checkpoint 持久化启用: {_CHECKPOINT_DB}")
            except Exception as e:
                print(f"[Webwright] sqlite 初始化失败,降级到 MemorySaver: {e}")
                _checkpointer = MemorySaver()
        else:
            _checkpointer = MemorySaver()

        return _checkpointer


@asynccontextmanager
async def make_agent(config: dict | None = None) -> AsyncIterator[Pregel]:
    """创建 Webwright 测试智能体的工厂函数。

    与 web_cli 和 web_mcp 不同：
    - 不连接 MCP Server
    - 不加载 browser_* 工具
    - 所有浏览器操作通过 Python/Playwright 脚本执行
    - 本地工具异常会被隔离为结构化错误消息，避免击穿整条流
    """
    context_middleware = WebContextInjectionMiddleware()

    all_tools = _wrap_webwright_tools_with_error_handling(get_local_tools())

    checkpointer = await _get_checkpointer()
    middleware_list = [
        model_retry_middleware,
        skills_middleware,
        context_middleware,
        MessageSequenceValidationMiddleware(),  # 确保消息序列符合 OpenAI API 要求
    ]
    fallback_middleware = _get_model_fallback_middleware()
    if fallback_middleware is not None:
        middleware_list.insert(0, fallback_middleware)

    web_agent = create_agent(
        model=model,
        tools=all_tools,
        system_prompt=SYSTEM_PROMPT,
        middleware=middleware_list,
        backend=composite_backend,
        context_schema=WebAgentContext,
        checkpointer=checkpointer,
    ).with_config(
        {
            "recursion_limit": 150,  # 从50增加到150，支持完整工作流+自动修复循环
            "metadata": {
                "agent_type": "webwright",
            },
        }
    )

    yield web_agent


# 导出 make_agent 供 LangGraph API 使用
agent = make_agent
