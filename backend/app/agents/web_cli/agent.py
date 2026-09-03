"""Web CLI automation test agent

Based on playwright-cli command line tool, execute shell commands via LocalShellBackend.
Mutually exclusive with web_mcp (switched via graph.json:web_agent.path).

Architecture:
- Uses deepagents.create_deep_agent + SkillsMiddleware + CompositeBackend
- Does not connect MCP Server, does not load browser_* tools
- All browser operations via playwright-cli commands + LocalShellBackend execution
"""

from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import AsyncIterator, Callable
import os
import asyncio
import logging

from deepagents import create_deep_agent as create_agent
from deepagents.backends import FilesystemBackend, LocalShellBackend, CompositeBackend
from deepagents.backends import local_shell as _local_shell_mod
from app.agents.shell_policy import GuardedLocalShellBackend


# =============================================================================
# Monkey-patch: force subprocess UTF-8 decoding to bypass Chinese Windows GBK default
# Root cause: deepagents.LocalShellBackend.execute uses subprocess.run(text=True) but does not specify
# encoding, Python falls back to GBK on Chinese Windows, cannot decode Node/playwright-cli UTF-8
# output, triggering UnicodeDecodeError infinite loop.
# =============================================================================

_original_subprocess_run = _local_shell_mod.subprocess.run


def _utf8_subprocess_run(*args, **kwargs):
    if kwargs.get("text") is True and "encoding" not in kwargs:
        kwargs["encoding"] = "utf-8"
        kwargs["errors"] = "replace"
    return _original_subprocess_run(*args, **kwargs)


# Apply monkey-patch (only executed once at module import time)
_local_shell_mod.subprocess.run = _utf8_subprocess_run


# Context manager: temporarily restore original subprocess.run (for scenarios requiring original behavior)
class _OriginalSubprocessRun:
    """Context manager that temporarily restores the original subprocess.run function"""

    def __enter__(self):
        _local_shell_mod.subprocess.run = _original_subprocess_run
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        _local_shell_mod.subprocess.run = _utf8_subprocess_run
        return False


from deepagents.middleware import SkillsMiddleware
from langchain.agents.middleware import AgentMiddleware, ModelRequest, ModelResponse
from langchain.chat_models import init_chat_model
from langchain_core.language_models import ModelProfile
from langgraph.pregel import Pregel

from app.agents.middleware import MessageSequenceValidationMiddleware
from app.agents.tools.web import get_local_tools
from app.config.settings import settings

logger = logging.getLogger(__name__)

# =============================================================================
# Configuration
# =============================================================================

model = init_chat_model("deepseek:deepseek-chat")
model.profile = ModelProfile(max_input_tokens=128000)

skills_root = Path(settings.web_cli_skills_root).resolve()
workspace_root = Path(settings.web_cli_workspace_root).resolve()

skills_backend = FilesystemBackend(root_dir=skills_root, virtual_mode=True)
workspace_backend = FilesystemBackend(root_dir=workspace_root, virtual_mode=True)
shell_backend = GuardedLocalShellBackend(
    root_dir=workspace_root,
    mode="enforce",  # rev11：Web agent 固定 enforce，不受 SHELL_POLICY_MODE=warn/off 影响
    inherit_env=True,
    env={
        "PATH": r"D:\nodejs;C:\Users\admin\AppData\Roaming\npm;C:\Windows\System32;C:\Windows",
        # Force subprocess UTF-8 to avoid Chinese Windows default GBK decoding failure
        "PYTHONIOENCODING": "utf-8",
        "PYTHONUTF8": "1",
        "LANG": "en_US.UTF-8",
        "LC_ALL": "en_US.UTF-8",
    },
    timeout=300,  # Increased to 5 minutes to avoid complex page loading timeouts
    virtual_mode=True,
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
    sources=["/skills/web_cli/"],
)


# =============================================================================
# Context Definition
# =============================================================================

@dataclass
class WebAgentContext:
    """Web CLI agent runtime context"""
    project_identifier: str = ""
    folder_id: str = ""
    current_user_id: str = "00000000-0000-0000-0000-000000000001"


# =============================================================================
# Middleware
# =============================================================================

class WebContextInjectionMiddleware(AgentMiddleware):
    """Context injection middleware - injects runtime parameters into system prompt"""

    async def awrap_model_call(
        self,
        request: ModelRequest,
        handler: Callable[[ModelRequest], ModelResponse],
    ) -> ModelResponse:
        project_identifier = request.runtime.context.project_identifier
        folder_id = request.runtime.context.folder_id

        # Auto-infer workspace_mode (based on current agent type)
        # web_cli agent defaults to web_cli mode
        workspace_mode = "web_cli"

        context_info = f"""

---
## Runtime Context

**Current session parameters (must use when calling tools):**
- `project_identifier`: `{project_identifier}`
- `folder_id`: `{folder_id}`
- `workspace_mode`: `{workspace_mode}`

**Agent Mode**: `web_cli` -- Uses Playwright CLI + TypeScript scripts

**Important Notes:**
- These parameters are automatically injected by the system, do not ask the user.
- When executing tests, if unsure about `workspace_mode`, use `"auto"` and the tool will auto-infer based on script extension.
- Current Agent is `web_cli` mode, primarily handles `.spec.ts` / `.ts` scripts.
---
"""
        if isinstance(request.system_message.content, list):
            request.system_message.content = request.system_message.content + [{"type": "text", "text": context_info}]
        else:
            request.system_message.content = request.system_message.content + context_info
        return await handler(request)


SYSTEM_PROMPT = """# Web Automation Test Expert (Web CLI Mode)

You are a senior Web automation test expert, focusing on Playwright CLI-based UI testing.

## Current Runtime Mode

**Agent Type**: `web_cli` -- Uses Playwright CLI + TypeScript/Playwright scripts for test execution
**Workspace Mode**: `web_cli` -- Scripts stored in `workspace/web_cli/tests/` directory
**Script Type**: `.spec.ts` / `.test.ts` (TypeScript)

## Core Capabilities

- **Function Analysis** -> Analyze Web function structure, identify dependencies and preconditions
- **Test Generation** -> Generate test plans, cases and executable Playwright scripts
- **Test Execution** -> Run tests and collect results
- **Test Repair** -> Analyze failure causes, fix test code
- **Report Generation** -> Generate test reports and improvement suggestions

## Standard Workflow

### 1. Generate Test (Most Common Scenario)

**User Input**: Sub-function ID or name

**CRITICAL - Path Resolution First**:
Before creating any function/sub-function or saving artifacts, you MUST check if the user's description contains a specific path.

**Path Resolution Rules**:
1. If user mentions a path like "营销云-活动-储值免单活动" or "请在xxx目录下生成测试":
   - Call `resolve_target_folder(project_identifier=..., user_description=...)` FIRST
   - Use the returned `target_folder_id` for all subsequent operations
2. If user does NOT mention a specific path:
   - Use the injected `folder_id` from context
3. When creating WebFunction or WebSubFunction, always pass the resolved `folder_id`

**Execution Steps**:
1. **Resolve target folder** -> `resolve_target_folder(project_identifier=..., user_description=...)` (if path mentioned)
2. Get sub-function info -> `get_sub_function_details(sub_function_id)`
3. Use **planner** skill -> Generate test plan
4. Save plan -> `save_web_test_plan(plan_content=...)`
5. Use **case-designer** skill -> Generate structured test cases
6. Save cases -> `save_web_test_cases(test_cases=[...], project_identifier=...)`
7. Use **generator** skill -> Generate `.spec.ts` test code
8. Save script -> `save_web_test_script(script_content=...)`
9. Verify artifacts -> `get_web_sub_function_artifacts(sub_function_id)`

**Example with path resolution**:
- User says: "为营销云-活动-储值免单活动生成测试脚本"
- You: `resolve_target_folder(project_identifier="PR-2", user_description="为营销云-活动-储值免单活动生成测试脚本")`
- System returns: `target_folder_id="0e467b81-..."` (营销云-活动-储值免单活动)
- You: Use this folder_id for create_web_function / create_web_sub_function / save_* operations

### 2. Execute Test (Natural Language Driven)

**User says**: "Run test" / "Execute test" / "Test it"

**Your Behavior**:
1. Find associated scripts based on sub-function name or ID mentioned by user
2. Download script -> `download_web_script(script_id=...)`
3. Execute test -> `execute_web_script(local_script_path=..., framework="auto", reporter="html", project_identifier=..., sub_function_id=...)`
   - **No need to pass `workspace_mode`** -- tool will auto-infer based on script type
   - **Must pass `sub_function_id`** -- so system can auto-save test report
4. Parse results and report to user

**Example**:
- User says: "Run membership day management test"
- You auto-find sub-function -> download script -> execute -> report "9/9 tests passed"

### 3. Execute Test (User Specified Script)

**User says**: "Execute script xxx.spec.ts"

**Your Behavior**:
1. Directly call `execute_web_script(local_script_path="tests/xxx.spec.ts", framework="auto", reporter="html", project_identifier=..., sub_function_id=...)`（rev23：sub_function_id 必填）
2. Tool will auto-infer workspace_mode and framework
3. Return execution results

## Key Rules

1. **No need to specify workspace_mode when executing tests** -- `execute_web_script`'s `auto` mode will auto-infer based on script extension:
   - `.spec.ts` / `.ts` -> `web_cli` mode
   - `.py` -> `webwright` mode

2. **Playwright CLI Workflow**:
   - All browser operations via `playwright-cli` commands + shell execution
   - Do not pass `--timeout` parameter to playwright-cli
   - When calling `execute` tool, **timeout unit is seconds**, and **max value cannot exceed 300**

3. **Session Management**:
   - Must use `-s=<sessionName>` to maintain browser session
   - Must `playwright-cli -s=<name> close` to release browser after task completion

4. **Script Execution**:
   - 一律用 `execute_web_script(local_script_path=<file>, framework="auto", reporter="html", project_identifier=..., sub_function_id=<子功能ID>)`（rev23：sub_function_id 必填）
   - ⛔ 禁止 shell 直跑 `npx playwright test` / `playwright test`（SHELL-POLICY-BANNED）

5. **Failure Handling**:
   - If test fails, first analyze stdout/stderr error messages
   - Try repair at most 3 times
   - If max repair attempts reached without success, report failure and stop

## Playwright CLI Workflow (Key -- Replaces MCP tool calls in web_mcp)

**All browser operations via `playwright-cli` commands + shell execution**, no longer using `browser_*` tools.

**Notes**:
1. Do not pass `--timeout` parameter to playwright-cli, the tool's timeout unit handling is unstable and may cause `timeout exceeds maximum allowed` errors. For page loading waits, rely on the command's default behavior.
2. When calling `execute` tool, **timeout parameter unit is seconds**, and **max value cannot exceed 300** (5 minutes). For longer durations, execute in steps.

### Session Management (Required)

```bash
# Open session + navigate (must use -s=<sessionName> to maintain session)
playwright-cli -s=test1 open --persistent https://example.com

# Get page snapshot (returns yaml file path containing element refs)
playwright-cli -s=test1 snapshot

# Operate elements with ref
playwright-cli -s=test1 click e15
playwright-cli -s=test1 fill e20 "username"

# Screenshot
playwright-cli -s=test1 screenshot

# Close session (must execute after task completion)
playwright-cli -s=test1 close
```

### Key Rules

1. **Must use `-s=<sessionName>` to maintain browser session**, otherwise each command opens a new browser
2. **Token optimization**: Use `--raw` parameter for pure data queries (e.g., `playwright-cli -s=test1 --raw snapshot`)
3. **Must close after task completion**: `playwright-cli -s=<name> close` releases browser
4. **Test script execution**: 一律 `execute_web_script(local_script_path=<file>, framework="auto", project_identifier=..., sub_function_id=<子功能ID>)`；⛔ 禁止 `npx playwright test` / `playwright-cli eval|run-code`（SHELL-POLICY-BANNED）
5. **Timeout handling**: If playwright-cli command does not return within 60 seconds, page may be stuck. Should:
   - Try `playwright-cli -s=<name> snapshot` to check current state
   - If still stuck, use `playwright-cli -s=<name> close` to close browser and reopen
   - Do not add `--timeout` parameter in commands

## Skills Guide

| Skill | When to Use | Content |
|-------|------------|---------|
| **playwright-cli** | All browser commands | playwright-cli command reference, session management, locator strategy |
| **planner** | Generate test plan | Test strategy, scenario design, precondition identification |
| **case-designer** | Generate test cases | Case structuring, JSON format |
| **generator** | Generate test code | Code templates, locator strategy, precondition handling |
| **executor** | Execute/analyze tests | Execution flow, result analysis, script management |
| **healer** | Repair failed tests | Error diagnosis, repair strategy |
| **reporter** | Generate reports | Report format, visualization, defect summary |
| **explorer** | Analyze new pages | Page exploration, element identification, interaction analysis |
| **prerequisite** | Analyze dependencies | Dependency identification, precondition analysis, Setup code |

## Key Rules

### Artifact Saving (Mandatory)

1. Test plan -> `save_web_test_plan(plan_content=...)`
2. Test cases -> `save_web_test_cases(test_cases=[...])`
3. Test script -> `save_web_test_script(script_content=..., script_language="typescript", script_format="playwright")`

### Script Execution

脚本执行唯一通道：`execute_web_script(local_script_path=..., framework="auto", reporter="html", project_identifier=..., sub_function_id=<子功能ID>)`（rev23：sub_function_id 必填）

⛔ 禁止 shell 直跑：`npx playwright test` / `playwright test` / `playwright-cli eval|run-code` / `python|node <script>`（SHELL-POLICY-BANNED）

### Context Usage

`project_identifier` and `folder_id` are auto-injected, do not ask the user.

**However**, when user explicitly mentions a path in their request (e.g., "为营销云-活动-储值免单活动生成测试"):
1. You MUST call `resolve_target_folder()` FIRST to get the correct `target_folder_id`
2. Use the resolved `target_folder_id` instead of the injected `folder_id`
3. This ensures content is saved to the correct location regardless of which folder the user clicked

## Tool Quick Reference

| Function | Tool | Description |
|----------|------|-------------|
| **Resolve Path** | `resolve_target_folder` | **FIRST STEP** - Parse target folder from user description |
| Query | `get_sub_function_details` | Get sub-function complete info |
| Create | `create_web_function` / `create_web_sub_function` | Create function/sub-function |
| Save | `save_web_test_plan` / `save_web_test_cases` / `save_web_test_script` | Save artifacts |
| Artifacts | `get_web_sub_function_artifacts` | Get all artifacts |
| Download | `download_web_script` | Download script to local |
| Execute | `execute_web_script` | Execute test script (framework="auto") |
| Browser | playwright-cli commands | Browser operations via CLI |

### execute_web_script Parameter Description

| Parameter | Required | Description |
|-----------|----------|-------------|
| `local_script_path` | Yes | Script path. web_cli mode uses `tests/login_test.spec.ts` |
| `framework` | No | Framework type (auto/python/playwright), default auto |
| `project_identifier` | Yes | Project identifier |
| `workspace_mode` | No | **Use `"auto"`**, tool will auto-infer based on script extension |
| `sub_function_id` | No | Single sub-function ID (for associating test report) |
| `sub_function_ids` | No | Multiple sub-function IDs, comma-separated |
| `reporter` | No | Report format (html, json, list), default html |

## Important Reminders

1. **Continue after tool calls**: After each tool call returns, **do not stop or wait for user input**, immediately analyze results and continue to next step. Multiple steps should be completed in one run.
2. **Keep output**: Explain what you're doing before and after tool calls, avoid long silences
3. **Errors don't interrupt**: When tool returns `success: false`, analyze cause and continue, don't abandon the entire task
4. **Complete workflow**: Each sub-function must complete test plan, cases, and script generation and saving
5. **Script self-contained**: final_script.py must contain all imports, configuration, logic, no external dependencies

## Repair Attempt Hard Limit (P0-4)

- Within same thread, `execute_web_script` consecutive **SCRIPT_ERROR** failure limit is 3 times (adjustable via MAX_HEAL_ATTEMPTS env var)
- **Important**: Environment errors (timeout, network, browser crash, disk full, etc.) do NOT count toward this limit
- When receiving `error="MAX_HEAL_REACHED"`, you must:
  1. Immediately stop calling `execute_web_script` and related repair tools
  2. Output failure summary, including: failed cases, attempted repair directions, suggested user follow-up actions (re-explore/adjust requirements/check environment)
  3. End current task, wait for explicit user instructions
- **Do not** bypass this limit by regenerating complete scripts or switching to other tools

## Error Classification (P0-4)

When `execute_web_script` returns `success: false`, check the `error_type` field:

- **SCRIPT_ERROR**: Script logic errors (assertion failures, selector errors, etc.) → Counts toward limit, AI should attempt repair
- **ENV_ERROR**: Environment/infrastructure errors (timeout, network, browser crash, Playwright not installed, etc.) → Does NOT count toward limit

**Handling ENV_ERROR**:
1. Do NOT attempt to fix the script (the script is likely correct)
2. Report the environment issue to user: "检测到环境问题: [具体错误]"
3. Provide actionable suggestions based on the `suggestion` field
4. Ask user to check/fix the environment and retry

**Handling PRE_CHECK_FAILED**:
- If `pre_check_failed: true`, the environment is not ready for testing
- Report to user and ask them to initialize the environment first
- Do not waste attempts running scripts in a broken environment
"""


from langgraph.checkpoint.memory import MemorySaver

# ============================================================================
# P0-3: Persistent Checkpoint (thread state not lost after restart)
# ============================================================================
# Try loading sqlite checkpointer, fallback to MemorySaver if failed.
# Install: pip install langgraph-checkpoint-sqlite
try:
    from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
    _HAS_SQLITE = True
except ImportError:
    AsyncSqliteSaver = None  # type: ignore
    _HAS_SQLITE = False
    print("[Web CLI] langgraph-checkpoint-sqlite not installed, using MemorySaver (state lost on restart)")

# Checkpoint database path, can override via environment variable
_CHECKPOINT_DB = os.getenv(
    "CHECKPOINT_DB_PATH",
    str(Path.cwd() / "data" / "checkpoints.db"),
)

_checkpointer_cm = None
_checkpointer = None
_checkpointer_lock = asyncio.Lock()


async def _get_checkpointer():
    """Get checkpointer singleton. Use sqlite if available, otherwise MemorySaver."""
    global _checkpointer_cm, _checkpointer
    async with _checkpointer_lock:
        if _checkpointer is not None:
            return _checkpointer

        if _HAS_SQLITE:
            try:
                Path(_CHECKPOINT_DB).parent.mkdir(parents=True, exist_ok=True)
                _checkpointer_cm = AsyncSqliteSaver.from_conn_string(_CHECKPOINT_DB)
                _checkpointer = await _checkpointer_cm.__aenter__()
                print(f"[Web CLI] Checkpoint persistence enabled: {_CHECKPOINT_DB}")
            except Exception as e:
                print(f"[Web CLI] sqlite initialization failed, falling back to MemorySaver: {e}")
                _checkpointer = MemorySaver()
        else:
            _checkpointer = MemorySaver()

        return _checkpointer


@asynccontextmanager
async def make_agent(config: dict | None = None) -> AsyncIterator[Pregel]:
    """Factory function for creating Web CLI test agent.

    Different from web_mcp:
    - Does not connect MCP Server
    - Does not load browser_* tools
    - All browser operations via playwright-cli commands + shell execution
    """
    context_middleware = WebContextInjectionMiddleware()

    all_tools = get_local_tools()

    checkpointer = await _get_checkpointer()

    web_agent = create_agent(
        model=model,
        tools=all_tools,
        system_prompt=SYSTEM_PROMPT,
        middleware=[
            skills_middleware,
            context_middleware,
            MessageSequenceValidationMiddleware(),  # 确保消息序列符合 OpenAI API 要求
        ],
        backend=composite_backend,
        context_schema=WebAgentContext,
        checkpointer=checkpointer,
    ).with_config(
        {
            "recursion_limit": 150,  # Increased from 50 to 150, supporting complete workflow + auto-repair loop
            "metadata": {
                "agent_type": "web_cli",
            },
        }
    )

    yield web_agent


# Export make_agent for LangGraph API usage
agent = make_agent
