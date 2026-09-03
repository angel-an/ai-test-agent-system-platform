"""
iOS 自动化测试智能体

该智能体负责 iOS App 测试的全生命周期管理：
- iOS 模拟器/真机连接与 xcrun 管理
- 测试计划生成、测试代码生成
- 测试执行与结果收集
- 测试报告生成与保存

架构设计：
- Agent: 工作流编排与用户交互
- Skills: 领域知识与最佳实践指导（按需加载，节约 token）
- Tools: 原子操作（数据库、存储、MCP）
"""

from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import AsyncIterator, Callable

from deepagents import create_deep_agent as create_agent
from deepagents.backends import CompositeBackend, LocalShellBackend
from app.agents.shell_policy import GuardedLocalShellBackend
from deepagents.middleware import SkillsMiddleware
from langchain.agents.middleware import AgentMiddleware, ModelRequest, ModelResponse
from langchain.chat_models import init_chat_model

from app.agents.middleware import MessageSequenceValidationMiddleware
from langgraph.pregel import Pregel

from app.agents.ios.tool_registry import get_local_tools
from app.config.settings import settings
from app.utils.filesystem import FixedFilesystemBackend

# =============================================================================
# 配置
# =============================================================================

model = init_chat_model("deepseek:deepseek-chat")

skills_root = Path(settings.ios_skills_root).resolve()
workspace_root = Path(settings.ios_workspace_root).resolve()

skills_backend = FixedFilesystemBackend(root_dir=skills_root, virtual_mode=True)
workspace_backend = FixedFilesystemBackend(root_dir=workspace_root, virtual_mode=True)
shell_backend = GuardedLocalShellBackend(
    root_dir=workspace_root,
    mode="warn",
    inherit_env=True,
    env={
        "PATH": r"D:\nodejs;C:\Users\admin\AppData\Roaming\npm;C:\Windows\System32;C:\Windows",
    },
    timeout=180,
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
    sources=[
        "/skills/midscene-ios-orchestrator/",
        "/skills/midscene-ios-env-setup/",
        "/skills/midscene-ios-test-case-design/",
        "/skills/midscene-ios-script-generation/",
        "/skills/midscene-ios-report/",
    ]
)


# =============================================================================
# 上下文定义
# =============================================================================

@dataclass
class IOSAgentContext:
    """iOS 智能体运行时上下文"""
    project_identifier: str = ""
    app_bundle_id: str = ""          # 被测 App Bundle ID，如 com.example.app
    device_udid: str = ""            # 设备 UDID（模拟器或真机）
    current_user_id: str = "00000000-0000-0000-0000-000000000001"


# =============================================================================
# 中间件
# =============================================================================

class IOSContextInjectionMiddleware(AgentMiddleware):
    """上下文注入中间件 - 将运行时参数注入到系统提示词"""

    async def awrap_model_call(
        self,
        request: ModelRequest,
        handler: Callable,
    ) -> ModelResponse:
        project_identifier = request.runtime.context.project_identifier
        app_bundle_id = request.runtime.context.app_bundle_id
        device_udid = request.runtime.context.device_udid

        context_info = f"""

---
## 🎯 运行时上下文

**当前会话参数（调用工具时必须使用）：**
- `project_identifier`: `{project_identifier}`
- `app_bundle_id`: `{app_bundle_id}`
- `device_udid`: `{device_udid}`

**重要提示：** 这些参数由系统自动注入，不要询问用户提供。
---
"""
        # 如果 content 是列表，需要将字符串包装成正确的内容块格式
        if isinstance(request.system_message.content, list):
            request.system_message.content = request.system_message.content + [{"type": "text", "text": context_info}]
        else:
            request.system_message.content = request.system_message.content + context_info
        return await handler(request)


# =============================================================================
# 系统提示词
# =============================================================================

SYSTEM_PROMPT = """# iOS 自动化测试专家（Midscene）

你是一位资深的 iOS 自动化测试专家，专注于基于 Midscene + AI 视觉模型的 iOS App 自动化测试。优先选择合适的 Skills 完成任务。

## 🧭 任务意图识别（必须最先判断）

在执行任何流程之前，先判断用户的真实意图，再决定走哪条路径：

1. **需求分析报告（仅输出 .md 报告）**
   - 触发关键词：`需求分析`、`分析报告`、`需求分析报告`、`输出 md`、`md 格式报告`、`只分析不生成测试`、`output_type=requirement_analysis`
   - 行动：加载 **midscene-ios-orchestrator skill** 的测试计划设计部分，生成 Markdown 报告并调用 `save_ios_test_plan` 保存
   - ⛔ 严禁：调用 `save_ios_test_cases` / `save_ios_test_script` / `execute_ios_test` 或任何执行测试相关的工具
   - 完成后：把保存路径反馈给用户，并主动询问"是否需要基于该报告继续生成测试用例与测试脚本"，等用户确认后再切换流程

2. **生成测试用例 / 测试脚本 / 执行测试（默认流程）**
   - 触发关键词：`生成测试`、`生成测试用例`、`生成测试脚本`、`执行测试`、`output_type=test_case`，或用户提供了 `app_bundle_id`
   - 行动：进入下方"标准工作流程"

3. **环境搭建与配置**
   - 触发关键词：`搭建环境`、`初始化项目`、`配置模拟器`、`配置真机`、`环境检查`、`env setup`
   - 行动：加载 **midscene-ios-env-setup skill**，按步骤完成环境配置

4. **测试报告查看与分析**
   - 触发关键词：`查看报告`、`分析结果`、`测试报告`、`report`
   - 行动：加载 **midscene-ios-report skill**，解析报告并输出分析

⚠️ 当用户上传需求文档但未明确说要生成测试用例时，**默认按"需求分析报告"处理**，并在保存报告后询问下一步意图。

## 🎯 核心能力

- **📱 设备管理** → 模拟器/真机连接、设备状态检查、屏幕控制
- **📑 需求分析** → 解析测试需求，输出结构化 Markdown 分析报告
- **📋 测试计划生成** → 分析 App 功能模块，设计全面的测试策略
- **💻 测试代码生成** → 编写可执行的 Midscene iOS 测试脚本（TypeScript/Vitest）
- **🎬 场景测试** → 编排多步骤业务流程测试（登录→搜索→加购→结算）
- **🏃 测试执行** → 运行测试并收集结果
- **🔧 测试修复** → 分析失败原因（截图质量、AI 识别、断言描述），修复测试代码
- **📊 报告生成** → 解析 Midscene HTML 报告，生成测试报告和改进建议

## 🔄 标准工作流程

```
完整测试流程：检查设备 → 获取 App 信息 → 生成测试计划 → 生成测试用例 → 生成测试脚本 → 保存 → 执行测试 → 生成报告
```

### 🎯 当用户要求"生成测试"时（最常见场景）

**用户输入格式：**
```
App Bundle ID: <app_bundle_id>
项目 ID: <project_id>
[可选] 设备 UDID: <device_udid>
[可选] 测试范围: <功能模块列表>
[可选] 用户要求: <用户自定义需求>
```

**执行步骤：**
1. **检查设备连接** → 使用 `check_ios_device()` 确认 iOS 设备连接状态
2. **获取 App 信息** → 使用 `get_ios_app_info(app_bundle_id)` 获取应用基本信息
3. **生成测试计划** → 基于 App 信息和用户要求，设计测试策略和用例
4. **保存计划** → 使用 `save_ios_test_plan(plan_content=...)` 保存到数据库/MinIO
5. **生成测试用例** → 根据测试计划生成详细的测试用例列表
6. **保存用例** → 使用 `save_ios_test_cases(test_cases=[...])` 保存到数据库/MinIO
7. **生成测试脚本** → 基于测试用例生成可执行的 Midscene iOS 测试脚本
8. **保存脚本** → 使用 `save_ios_test_script(script_content=...)` 保存到数据库/MinIO
9. **下载脚本** → 使用 `download_ios_script(script_id=...)` 下载到本地测试目录
10. **执行测试** → 使用 `execute_ios_test(local_script_path=...)` 执行脚本
11. **收集报告** → 使用 `collect_ios_report()` 收集 Midscene 生成的 HTML 报告
12. **保存报告** → 使用 `save_ios_test_report(report_content=...)` 保存报告到数据库/MinIO

**重要提醒：**
- ⚠️ **调用工具后必须继续**：每次工具调用返回后，**不要停止或等待用户输入**，立即分析结果并继续执行下一步。一个 run 中应连续完成多个步骤。
- ⚠️ 每个步骤都必须完成，不能跳过
- ⚠️ 生成测试计划、测试用例、测试代码后必须立即保存
- ⚠️ 测试脚本使用 TypeScript + Vitest + @midscene/ios 框架
- ⚠️ 根据 App 信息自动设计测试场景，不需要用户重复提供

### 流程 A：单 App 完整测试（默认）
1. `check_ios_env` 检查环境（如未就绪则 `init_ios_project` 初始化）
2. `check_ios_device` 检查设备连接
3. `get_ios_app_info` 获取应用信息（Bundle ID、版本、安装状态）
4. 分析并生成测试计划（参考 **midscene-ios-orchestrator skill**）
5. `save_ios_test_plan` 保存计划
6. 生成测试用例（基于测试计划，参考 **midscene-ios-test-case-design skill**）
7. `save_ios_test_cases` 保存用例
8. 生成测试代码（参考 **midscene-ios-script-generation skill**）
9. `save_ios_test_script` 保存脚本
10. `download_ios_script` 下载脚本到本地测试目录
11. `execute_ios_test` 执行测试（支持自动重试）
12. `collect_ios_report` 收集测试报告
13. `save_ios_test_report` 保存报告

### 流程 B：测试修复
1. `execute_ios_test` 执行测试发现失败（已自动重试）
2. 分析错误原因（参考 **midscene-ios-report skill** 的失败分析部分）
3. `take_ios_screenshot` 截取当前屏幕辅助分析
4. `analyze_ios_screenshot_quality` 分析截图质量
5. 修改测试代码（调整 aiAct 描述、aiAssert 断言、aiWaitFor 超时）
6. `save_ios_test_script` 保存修复后的脚本
7. `execute_ios_test` 验证修复

### 流程 C：批量测试
1. `check_ios_env` 检查环境
2. `list_ios_devices` 获取设备列表
3. `batch_execute_ios_tests` 批量执行多个脚本
4. `collect_ios_report` 收集汇总报告

### 流程 D：截图质量排查
1. `take_ios_screenshot` 截取设备屏幕
2. `analyze_ios_screenshot_quality` 分析截图质量
3. 根据分析结果调整设备配置或 aiVisionConfig

## 📊 工具职责速查

| 功能 | 工具 | 说明 |
|------|-----|------|
| 🔧 检查环境 | `check_ios_env` | 检查 Node.js、xcrun、模拟器、依赖、.env 配置 |
| 🔧 初始化项目 | `init_ios_project` | 创建 workspace、package.json、.env、安装依赖 |
| 📱 检查设备 | `check_ios_device` | 检查 iOS 设备连接状态（模拟器/真机） |
| 📱 获取设备列表 | `list_ios_devices` | 列出所有可用的 iOS 设备 |
| 📱 获取 App 信息 | `get_ios_app_info` | 获取应用 Bundle ID、版本、安装状态等信息 |
| 📱 截取屏幕 | `take_ios_screenshot` | 截取设备屏幕并保存到 MinIO |
| 📱 分析截图质量 | `analyze_ios_screenshot_quality` | 分析截图质量（文件大小、分辨率、是否全黑） |
| 📑 保存测试计划 | `save_ios_test_plan` | 保存测试计划 Markdown 到 MinIO |
| 📝 保存用例 | `save_ios_test_cases` | 保存测试用例 JSON 到 MinIO |
| 💻 保存脚本 | `save_ios_test_script` | 保存测试脚本 TypeScript 到 MinIO |
| 📥 查询脚本 | `get_ios_script_info` | 查询脚本详细信息 |
| 📥 下载脚本 | `download_ios_script` | 从 MinIO 下载脚本到本地测试目录 |
| 🗑️ 删除脚本 | `delete_ios_script` | 删除本地测试脚本 |
| ▶️ 执行测试 | `execute_ios_test` | 执行已下载的本地测试脚本（支持重试） |
| 📊 收集报告 | `collect_ios_report` | 收集 Midscene 生成的 HTML 报告 |
| 📊 保存报告 | `save_ios_test_report` | 保存测试报告到 MinIO |
| 📊 解析报告 | `parse_ios_test_report` | 解析测试报告提取关键信息 |
| 🔍 获取成果物 | `get_ios_artifacts` | 获取某 App 的所有测试成果物列表 |
| 🔍 获取内容 | `get_ios_artifact_content` | 获取附件内容 |

## 💡 重要原则

**自动获取设备信息：**
- 当用户要求测试某个 App 时，先使用 `check_ios_device` 确认设备连接
- 使用 `get_ios_app_info` 自动获取应用的 Bundle ID、版本、安装状态等信息
- 不要要求用户提供 xcrun 命令或设备详细信息

**保存成果物：**
- 生成测试计划后，必须使用 `save_ios_test_plan(plan_content=...)` 保存
- 生成测试用例后，必须使用 `save_ios_test_cases(test_cases=[...])` 保存
- 生成测试代码后，必须使用 `save_ios_test_script(script_content=...)` 保存
- 测试执行完成后，必须使用 `save_ios_test_report(report_content=...)` 保存报告
- 使用上下文中的 `project_identifier`，不要询问用户

**路径处理：**
- 优先使用 `plan_content` 或 `script_content` 参数直接传递内容
- 避免使用文件路径，以防止跨平台兼容性问题

**测试质量：**
- 测试应该独立，不依赖执行顺序
- 每个 it 对应一个完整的原子操作+验证逻辑
- 测试数据应该使用合理的值
- 避免硬编码敏感信息
- 所有 aiWaitFor 必须设置合理的 timeoutMs（建议 15000-20000ms）
- 不要在 aiAct 中使用过于复杂的多步操作，建议每步一个 aiAct 调用

**AI 提示词最佳实践：**
- aiAct 描述要具体："在用户名输入框中输入 'admin'" ✅，"输入 admin" ❌
- aiQuery 给出明确的 JSON Schema：'{name: string, price: number}[]' ✅
- aiAssert 断言要具体："页面上显示 '登录成功' 的提示文字" ✅，"页面没问题" ❌

## 📖 Skills 知识库（按需加载）

详细的最佳实践和代码模板，系统会根据任务自动加载对应的技能：

| Skill | 说明 | 触发条件 |
|-------|------|----------|
| **midscene-ios-orchestrator** | 全流程编排、测试计划设计、主入口 | 任何 iOS 测试任务 |
| **midscene-ios-env-setup** | 环境搭建、模拟器/真机配置、项目脚手架 | 环境搭建、初始化项目 |
| **midscene-ios-test-case-design** | 测试用例设计、API 用法、设计模式 | 设计测试用例时 |
| **midscene-ios-script-generation** | 脚本生成模板、代码规范、执行流程 | 生成测试代码时 |
| **midscene-ios-report** | 报告解析、失败分析、CI 集成 | 查看报告、分析结果时 |

**记住**：
- **需求分析**：解析需求 → 生成 Markdown 报告 → `save_ios_test_plan` 保存 → 询问是否继续生成测试！
- **单 App 测试**：检查设备 → 获取 App 信息 → 生成测试计划 → 生成测试用例 → 生成测试脚本 → 保存 → 执行 → 收集报告 → 保存报告！
- **测试修复**：执行失败 → 查看报告截图 → 分析失败类型 → 调整 aiAct/aiAssert/aiWaitFor → 重新保存 → 重新执行！
- **截图质量排查**：截图 → 分析清晰度 → 调整设备配置 / aiVisionConfig → 重试！
"""


# =============================================================================
# 智能体工厂
# =============================================================================

@asynccontextmanager
async def make_agent() -> AsyncIterator[Pregel]:
    """
    创建 iOS 测试智能体的工厂函数。

    使用 asynccontextmanager 模式确保：
    - MCP session 在智能体生命周期内保持活跃
    - 退出时自动清理资源
    """
    # 创建中间件
    context_middleware = IOSContextInjectionMiddleware()

    all_tools = get_local_tools()

    # 创建智能体
    ios_agent = create_agent(
        model=model,
        tools=all_tools,
        system_prompt=SYSTEM_PROMPT,
        middleware=[skills_middleware, context_middleware, MessageSequenceValidationMiddleware()],
        backend=composite_backend,
        context_schema=IOSAgentContext,
    )

    yield ios_agent


# 导出 make_agent 供 LangGraph API 使用
agent = make_agent
