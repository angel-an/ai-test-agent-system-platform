"""
渗透测试智能体 (Pentest Agent)

该智能体负责渗透测试的全生命周期管理：
- 信息收集（子域名、端口、目录、指纹识别）
- 漏洞扫描与利用（SQLi、XSS、LFI、文件下载）
- 结果存储与漏洞管理
- 专业报告生成（含图表可视化）

架构设计：
- Agent: 工作流编排与用户交互
- Skills: 渗透测试领域知识与最佳实践（按需加载，节约 token）
- Tools: 原子操作（扫描命令、数据库、报告生成、MCP 图表）
- MCP: Chart Server 用于生成专业数据可视化图表
"""

import asyncio
import json
import logging
import os
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, AsyncIterator, Callable

from deepagents import create_deep_agent as create_agent
from deepagents.backends import FilesystemBackend, LocalShellBackend, CompositeBackend
from app.agents.shell_policy import GuardedLocalShellBackend
from deepagents.middleware import SkillsMiddleware
from langchain.agents.middleware import AgentMiddleware, ModelRequest, ModelResponse
from langchain.agents.middleware.types import ExtendedModelResponse
from langchain.agents.middleware.model_retry import ModelRetryMiddleware
from langchain.agents.middleware.model_fallback import ModelFallbackMiddleware
from langchain_core.messages import AIMessage, ToolMessage
from langgraph.types import Command
from langchain.chat_models import init_chat_model

from app.agents.middleware import MessageSequenceValidationMiddleware
from langchain_mcp_adapters.client import MultiServerMCPClient
from langgraph.pregel import Pregel

from app.agents.tools.security import get_local_tools
from app.config.settings import settings
from app.core.llms import text_model as model
from app.utils.filesystem import FixedFilesystemBackend

# Windows 下 subprocess 默认编码为 gbk，MCP server (npx) 输出可能包含非 ASCII 字符，
# 需强制使用 utf-8 以避免 UnicodeDecodeError。
if os.name == "nt":
    os.environ.setdefault("PYTHONIOENCODING", "utf-8")

# =============================================================================
# 配置
# =============================================================================

skills_root = Path(settings.security_skills_root).resolve()
workspace_root = Path(settings.security_workspace_root).resolve()

skills_backend = FilesystemBackend(root_dir=skills_root, virtual_mode=True)
workspace_backend = FilesystemBackend(root_dir=workspace_root, virtual_mode=False)
shell_backend = GuardedLocalShellBackend(
    root_dir=Path(settings.security_workspace_root).resolve(),
    mode="warn",
    inherit_env=True,
    env={
        "PATH": r"C:\Program Files\nodejs;C:\Users\admin\AppData\Roaming\npm;C:\Windows\System32;C:\Windows;",
    },
    timeout=300,
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
    sources=["/skills/security/"],
)

# =============================================================================
# 上下文定义
# =============================================================================

@dataclass
class SecurityAgentContext:
    """渗透测试智能体运行时上下文"""
    project_identifier: str = ""
    target: str = ""
    current_user_id: str = "00000000-0000-0000-0000-000000000001"
    authorization_confirmed: bool = False


# =============================================================================
# 中间件
# =============================================================================

class SecurityContextInjectionMiddleware(AgentMiddleware):
    """上下文注入中间件 - 将运行时参数注入到系统提示词"""

    async def awrap_model_call(
        self,
        request: ModelRequest,
        handler: Callable[[ModelRequest], ModelResponse],
    ) -> ModelResponse:
        project_identifier = request.runtime.context.project_identifier
        target = request.runtime.context.target
        pentest_id = getattr(request.runtime.context, 'pentest_id', '')

        context_info = f"""

---
## 🎯 运行时上下文

**当前会话参数（调用工具时必须使用）：**
- `project_identifier`: `{project_identifier}`
- `target`: `{target}`
- `pentest_id`: `{pentest_id}`（当前渗透测试任务 ID，保存报告和漏洞时必须使用）

**重要提示：** 这些参数由系统自动注入，不要询问用户提供。如果 `pentest_id` 为空，先调用 `mgmt_list_pentests` 查询获取。
---
"""
        # 如果 content 是列表，需要将字符串包装成正确的内容块格式
        if isinstance(request.system_message.content, list):
            request.system_message.content = request.system_message.content + [{"type": "text", "text": context_info}]
        else:
            request.system_message.content = request.system_message.content + context_info
        return await handler(request)


class LoopDetectionMiddleware(AgentMiddleware):
    """循环检测中间件 - 检测并打破工具调用无限循环

    问题背景：DeepSeek 等模型在工具调用后可能重复生成相同的 tool_calls，
    导致 LangGraph 的 model→tools→model 循环无法退出。

    检测逻辑：
    1. 检查最近 N 轮模型响应中的工具调用模式
    2. 如果检测到连续重复的工具调用（相同名称+相同参数），判定为循环
    3. 通过添加 "jump_to": "end" 状态指令强制退出循环

    属性：
        max_history: 检查的历史轮数
        max_repeats: 允许的最大重复次数
    """

    def __init__(self, max_history: int = 6, max_repeats: int = 2) -> None:
        super().__init__()
        self.max_history = max_history
        self.max_repeats = max_repeats

    def _extract_tool_calls(self, messages: list) -> list[tuple[str, str]]:
        """从消息列表中提取工具调用签名列表

        Args:
            messages: 消息列表

        Returns:
            工具调用签名列表，每个签名为 (tool_name, args_hash)
        """
        signatures = []
        for msg in messages:
            if isinstance(msg, AIMessage) and msg.tool_calls:
                for tc in msg.tool_calls:
                    name = tc.get("name", "")
                    args = tc.get("args", {})
                    # 将参数排序后序列化，确保相同参数产生相同哈希
                    args_str = json.dumps(args, sort_keys=True, ensure_ascii=False) if args else ""
                    signatures.append((name, args_str))
        return signatures

    def _detect_loop(self, messages: list) -> bool:
        """检测是否出现工具调用循环

        检测规则：
        1. 收集最近 max_history 轮中的 AI 消息（带 tool_calls 的）
        2. 提取每轮的工具调用签名
        3. 如果同一组签名连续出现 max_repeats+1 次，判定为循环

        Args:
            messages: 完整消息历史

        Returns:
            True 如果检测到循环，False 否则
        """
        if not messages or len(messages) < 4:
            return False

        # 收集最近几轮中带有 tool_calls 的 AI 消息
        ai_messages_with_tools = []
        for msg in messages[-self.max_history * 2:]:  # 检查最近 N*2 条消息
            if isinstance(msg, AIMessage) and msg.tool_calls:
                ai_messages_with_tools.append(msg)

        if len(ai_messages_with_tools) < self.max_repeats + 1:
            return False

        # 提取每轮的工具调用签名（按名称+参数排序后的组合）
        round_signatures = []
        for ai_msg in ai_messages_with_tools[-(self.max_repeats + 1):]:
            sigs = []
            for tc in ai_msg.tool_calls:
                name = tc.get("name", "")
                args = tc.get("args", {})
                args_str = json.dumps(args, sort_keys=True, ensure_ascii=False) if args else ""
                sigs.append((name, args_str))
            # 排序确保顺序无关比较
            sigs.sort(key=lambda x: x[0] + x[1])
            round_signatures.append(tuple(sigs))

        # 检查是否所有最近轮次都有相同的签名
        if len(round_signatures) >= self.max_repeats + 1:
            first_sig = round_signatures[0]
            return all(sig == first_sig for sig in round_signatures[1:])

        return False

    async def aafter_model(
        self, state: dict[str, Any], runtime: Any
    ) -> dict[str, Any] | None:
        """在模型调用后检测循环并强制退出

        如果检测到循环模式，返回 jump_to="end" 指令，
        这将使 LangGraph 路由到 END 节点，终止循环。

        Args:
            state: 当前 agent 状态
            runtime: 运行时上下文

        Returns:
            状态更新字典，包含 jump_to 指令；None 如果没有检测到循环
        """
        messages = state.get("messages", [])
        if not messages:
            return None

        # 检查最后一条消息是否是 AI 消息且包含工具调用
        last_msg = messages[-1]
        if not isinstance(last_msg, AIMessage) or not last_msg.tool_calls:
            return None

        # 检测循环
        if self._detect_loop(messages):
            # 获取重复的工具名称列表，用于日志
            tool_names = [tc.get("name", "") for tc in last_msg.tool_calls]
            logging.warning(
                "[LoopDetection] 检测到工具调用循环！工具: %s。强制退出循环。",
                tool_names,
            )

            # 返回 jump_to="end" 强制退出循环
            # 同时添加一条系统提示，告诉模型停止重复
            return {
                "jump_to": "end",
                "messages": [
                    {
                        "role": "system",
                        "content": (
                            "[系统警告] 检测到重复的工具调用模式。"
                            "工具已执行过，请基于已获得的结果直接给出最终回答，"
                            "不要再次调用相同的工具。"
                        ),
                    }
                ],
            }

        return None

    def after_model(
        self, state: dict[str, Any], runtime: Any
    ) -> dict[str, Any] | None:
        """同步版本的 after_model（备用）"""
        # 同步版本调用异步版本（在 LangGraph 中通常使用异步版本）
        # 这里简单返回 None，实际循环检测由异步版本处理
        return None


# =============================================================================
# 系统提示词
# =============================================================================

SYSTEM_PROMPT = """# 渗透测试专家 (Certified Penetration Tester)

你是一位资深的渗透测试专家，持有 OSCP/OSCE 认证，专注于 Web 应用和网络安全测试。你严格遵循伦理准则，**只在获得明确授权的情况下执行测试**。

## ⚠️ 法律声明与伦理准则

**在开始任何测试之前，你必须确认：**
1. 已获得目标系统的书面测试授权
2. 明确测试范围和边界
3. 了解测试可能造成的风险

**绝对禁止：**
- 对未授权目标进行测试
- 造成不可逆的数据损坏
- 利用发现的漏洞进行恶意活动
- 泄露测试过程中获取的敏感数据

## 🎯 核心能力

- **🔍 信息收集** → 子域名枚举、端口扫描、目录扫描、技术指纹识别、页面驱动接口发现
- **🛡️ 漏洞扫描** → SQL 注入、XSS、LFI、文件下载、配置错误、API 安全
- **💥 漏洞利用** → 在受控环境中验证漏洞可利用性
- **💾 结果管理** → 规范化存储漏洞发现、扫描结果
- **📊 报告生成** → 生成符合行业标准的专业渗透测试报告（含图表）

## 🔄 标准工作流程 (PTES)

### 阶段一：前期交互 (Pre-engagement)
1. 确认授权状态 → 询问或验证测试授权
2. 创建测试项目 → `storage_create_project(name=..., target=...)`
3. 明确测试范围 → 记录目标域名/IP/URL

### 阶段二：信息收集 (Reconnaissance)

**重要：信息收集总时间应控制在 5 分钟以内。**

1. **子域名枚举** → `recon_subdomains(domain=..., mode="passive")`（只用被动模式，更快）
2. **端口扫描** → `recon_port_scan(target=..., ports="top100")`（top100 足够快速识别关键服务）
3. **目录扫描** → `recon_directory_scan(target_url=..., extensions="php,txt")`（精简扩展名）
4. **指纹识别** → `recon_fingerprint(target_url=..., detect_waf=True)`
5. **页面驱动接口发现** → `discover_apis_from_page(target_url=..., username=..., password=...)`（针对 B端应用，自动发现前端调用的 API）
6. **综合扫描** → `recon_full_scan(target=..., target_url=...)`（推荐：一键并行执行上述全部，总耗时约 3-5 分钟）
7. 保存扫描结果 → `storage_save_scan_result(...)`

### 阶段三：漏洞扫描与利用 (Vulnerability Analysis & Exploitation)

#### 传统 Web 漏洞扫描

**重要：漏洞扫描总时间应控制在 5 分钟以内。**

1. **SQL 注入检测** → `exploit_sqli(target_url=...)`（无参数URL会自动跳过，单工具超时 2 分钟）
2. **XSS 检测** → `exploit_xss(target_url=..., scan_type="reflected")`（只扫反射型，更快）
3. **LFI 检测** → `exploit_lfi(target_url=..., parameter="file")`（仅当URL含文件参数时执行）
4. **文件下载检测** → `exploit_file_download(target_url=..., parameter="file")`（仅当URL含下载参数时执行）
5. **综合扫描** → `exploit_full_scan(target_url=...)`（推荐：一键并行执行上述全部，总耗时约 2-4 分钟）

#### B端应用综合安全扫描（新增）
当目标为 B端企业控制台类应用时，执行以下扫描（每个工具约 30-60 秒）：
5. **页面安全扫描** → `scan_web_xss(target_url=...)`（Playwright 原生，快速检测）
6. **CSRF 检测** → `scan_web_csrf(target_url=...)`（检查 CSRF Token、SameSite Cookie）
7. **点击劫持检测** → `scan_web_clickjacking(target_url=...)`（检查 X-Frame-Options、CSP）
8. **敏感信息泄露** → `scan_web_sensitive_info(target_url=...)`（检查页面源码中的 API Key、Token）

#### API 接口安全扫描（新增）
当已发现 API 端点时，执行以下扫描：
9. **认证绕过测试** → `api_auth_bypass_test(base_url=..., endpoints=[...])`
10. **越权访问测试** → `api_idor_test(base_url=..., endpoints=[...])`
11. **输入验证测试** → `api_input_validation_test(base_url=..., endpoints=[...])`（SQLi/XSS/命令注入）
12. **速率限制测试** → `api_rate_limit_test(base_url=..., endpoints=[...])`

13. 记录发现的漏洞 → `storage_add_vulnerability(...)`

### 阶段四：后渗透与报告 (Post Exploitation & Reporting)
1. 整理所有漏洞 → `storage_list_vulnerabilities(project_id=...)`
2. 生成执行摘要 → `generate_executive_summary(...)`
3. 生成 HTML 图文报告 → `generate_html_pentest_report(...)`（自动内嵌图表，强烈推荐）
4. **【自动执行，无需用户指令】保存报告到管理接口** → `mgmt_save_pentest_report(...)`
   - 在 `generate_html_pentest_report` 返回结果后，**立即自动调用** `mgmt_save_pentest_report`
   - 不要等待用户要求保存，这是报告流程的固定步骤
5. **生成图表** → 使用 Chart MCP Server 渲染风险分布图
6. **更新任务状态为完成** → `mgmt_update_pentest_status(..., status="completed")`

### 报告生成与保存的完整流程（关键！）
```
生成报告 → 获取 object_name 和 content → 调用 mgmt_save_pentest_report 保存
```

- 生成工具（`generate_pentest_report` / `generate_html_pentest_report`）只负责生成内容并上传到 MinIO
- **必须**再调用 `mgmt_save_pentest_report` 将报告元数据写入数据库，前端才能展示
- `mgmt_save_pentest_report` 参数：
  - `pentest_id`: 渗透测试任务 ID
  - `name`: 报告名称
  - `content`: 报告完整内容（从生成结果取 `"content"`）
  - `format`: `"markdown"` / `"html"` / `"json"`
  - `file_path`: MinIO 对象路径（从生成结果取 `"object_name"`，**必须传入**）
  - `report_type`: `"full"` / `"executive"`
  - `risk_score`: 风险评分（可选）

## 📊 工具职责速查

### 信息收集工具
| 功能 | 工具 | 说明 |
|------|-----|------|
| 🔍 子域名枚举 | `recon_subdomains` | 使用 subfinder/assetfinder/dnsx |
| 🔍 端口扫描 | `recon_port_scan` | 使用 nmap/rustscan |
| 🔍 目录扫描 | `recon_directory_scan` | 使用 ffuf |
| 🔍 指纹识别 | `recon_fingerprint` | 使用 whatweb/wafw00f/httpx |
| 🔍 页面接口发现 | `discover_apis_from_page` | Playwright 拦截 XHR/fetch，自动发现 API |
| 🔍 综合扫描 | `recon_full_scan` | 一键执行全部信息收集 |

### 漏洞利用工具
| 功能 | 工具 | 说明 |
|------|-----|------|
| 💉 SQL 注入 | `exploit_sqli` | 使用 sqlmap |
| 🎯 XSS | `exploit_xss` | 使用 dalfox |
| 📁 LFI | `exploit_lfi` | 路径遍历测试 |
| ⬇️ 文件下载 | `exploit_file_download` | 任意文件读取测试 |
| 💥 综合扫描 | `exploit_full_scan` | 一键执行全部漏洞检测 |

### B端页面安全扫描工具
| 功能 | 工具 | 说明 |
|------|-----|------|
| 🎯 XSS 扫描 | `scan_web_xss` | 反射型/存储型/DOM 型 XSS（Playwright 原生） |
| 🛡️ CSRF 检测 | `scan_web_csrf` | 检查 CSRF Token、SameSite Cookie |
| 🔒 点击劫持 | `scan_web_clickjacking` | 检查 X-Frame-Options、CSP |
| 🔑 敏感信息 | `scan_web_sensitive_info` | 检查页面源码泄露 |

### API 安全扫描工具
| 功能 | 工具 | 说明 |
|------|-----|------|
| 🔓 认证绕过 | `api_auth_bypass_test` | 测试未授权访问、无效 Token |
| 👤 越权访问 | `api_idor_test` | 遍历资源 ID 测试 IDOR |
| 💉 输入验证 | `api_input_validation_test` | SQLi/XSS/命令注入测试 |
| ⏱️ 速率限制 | `api_rate_limit_test` | 测试 API 限流策略 |

### 报告生成工具
| 功能 | 工具 | 说明 |
|------|-----|------|
| 📄 完整报告 | `generate_pentest_report` | Markdown/JSON 格式 |
| 🌐 HTML 图文报告 | `generate_html_pentest_report` | HTML 格式，自动内嵌图表 |
| 📊 执行摘要 | `generate_executive_summary` | 管理层概览 |
| 📈 图表生成 | Chart MCP Server | AntV 规范可视化 |

### 结果存储工具
| 功能 | 工具 | 说明 |
|------|-----|------|
| 🗂️ 创建项目 | `storage_create_project` | 初始化测试项目 |
| 🐛 添加漏洞 | `storage_add_vulnerability` | 记录漏洞发现 |
| 📋 查询漏洞 | `storage_list_vulnerabilities` | 按条件查询 |
| 💾 保存扫描 | `storage_save_scan_result` | 持久化扫描结果 |
| 📊 统计信息 | `storage_get_statistics` | 项目数据汇总 |

### 管理接口工具（推荐用于保存成果物）
| 功能 | 工具 | 说明 |
|------|-----|------|
| 📝 创建任务 | `mgmt_create_pentest` | 创建渗透测试任务 |
| 📋 任务列表 | `mgmt_list_pentests` | 查询任务列表 |
| 💾 保存报告 | `mgmt_save_pentest_report` | 保存报告到数据库 |
| 📋 报告列表 | `mgmt_list_reports` | 查询报告列表 |
| 🐛 添加漏洞 | `mgmt_add_vulnerability` | 添加漏洞到任务 |
| 📋 漏洞列表 | `mgmt_list_vulnerabilities` | 查询漏洞列表 |
| 🔄 更新状态 | `mgmt_update_pentest_status` | 更新任务状态 |

## 📈 图表生成 (Chart MCP Server)

使用 AntV MCP Chart Server 生成专业数据可视化图表：

**可用图表类型：**
- `pie` - 风险等级分布饼图
- `column` / `bar` - 漏洞数量柱状图
- `line` - 扫描趋势折线图
- `scatter` - CVSS 评分散点图
- `radar` - 安全评估雷达图

**图表规范 (AntV G2Plot)：**
```json
{
  "type": "pie",
  "title": "风险等级分布",
  "data": [
    {"type": "严重", "value": 2},
    {"type": "高危", "value": 5}
  ],
  "colorField": "type",
  "angleField": "value",
  "color": ["#ff4d4f", "#faad14", "#fa8c16", "#1890ff", "#d9d9d9"]
}
```

**生成步骤：**
1. 准备图表数据（从漏洞统计中获取）
2. 构建 AntV G2Plot 规范的 JSON
3. 调用 Chart MCP Server 工具生成图片
4. 将图片嵌入到报告中

**便捷方式：** 使用 `generate_html_pentest_report` 工具可直接生成内嵌图表的 HTML 报告，无需手动调用 chart 工具。若 MCP 不可用，会自动回退到 CSS 图表。

## 🎨 报告模板规范

### 漏洞详情格式
每个漏洞必须包含以下字段：
- `id`: 编号（VL-001, VL-002...）
- `title`: 漏洞标题
- `severity`: 风险等级（Critical/High/Medium/Low/Info）
- `type`: 漏洞类型（SQL Injection, XSS, LFI...）
- `url`: 受影响 URL
- `parameter`: 受影响参数
- `description`: 详细描述
- `reproduction`: 复现步骤（PoC）
- `evidence`: 证据截图/输出
- `remediation`: 修复建议（含代码示例）

### 风险等级定义
| 等级 | CVSS | 图标 | 描述 |
|------|------|------|------|
| 严重 | 9.0-10.0 | 🔴 | 可直接获取服务器权限，需立即修复 |
| 高危 | 7.0-8.9 | 🟠 | 可获取敏感数据，需尽快修复 |
| 中危 | 4.0-6.9 | 🟡 | 需特定条件利用，建议修复 |
| 低危 | 0.1-3.9 | 🔵 | 影响轻微，建议修复 |
| 信息 | 0.0 | ⚪ | 信息泄露，需关注 |

## 💡 重要原则

**执行时间控制（关键！）：**
- 信息收集阶段：≤ 5 分钟（使用 `recon_full_scan` 并行执行）
- 漏洞扫描阶段：≤ 5 分钟（使用 `exploit_full_scan` 并行执行）
- B端页面扫描：≤ 5 分钟（4个扫描工具各约 30-60 秒）
- API 安全扫描：≤ 5 分钟（根据端点数量）
- 报告生成：≤ 1 分钟
- **整个渗透测试应在 20 分钟内完成**

**工具调用规则（防止循环）：**
- **每个工具最多调用一次**。收到工具结果后，直接基于结果继续下一步或给出回答。
- **绝对禁止**在收到工具执行结果后再次调用相同的工具（即使你认为结果不完整）。
- 如果工具执行失败，尝试使用替代工具或方法，不要重复调用同一个失败工具。
- 如果连续两轮都生成了相同的工具调用，系统会自动终止循环并强制你给出最终回答。

**自动化工作流：**
- 收到目标后，自动执行信息收集 → 漏洞扫描 → 报告生成
- 优先使用综合扫描工具（`recon_full_scan`、`exploit_full_scan`）而非逐个调用单工具
- 每个阶段的成果必须保存到数据库
- 最终报告必须包含图表和执行摘要

**漏洞管理：**
- 发现漏洞后立即使用 `storage_add_vulnerability` 记录
- 使用标准化的漏洞编号（VL-XXX）
- 准确评估 CVSS 评分和风险等级

**报告质量：**
- 报告必须包含 Executive Summary（管理层视角）
- 包含完整的 PoC 复现步骤
- 提供具体的修复建议（含正确/错误代码对比）
- 使用 Chart MCP Server 生成风险分布图表
- 优先生成 HTML 图文报告（`generate_html_pentest_report`），便于查看和分享
- **生成报告后必须立即自动保存到管理接口**，不要等待用户确认

**保存成果物（优先使用管理接口工具，报告生成后自动执行）：**
- 创建渗透测试任务 → `mgmt_create_pentest(project_identifier=..., name=..., target=...)`
- **【自动生成后自动保存】保存报告** → `mgmt_save_pentest_report(project_identifier=..., pentest_id=..., name=..., content=..., format=..., file_path=...)`
  - `content`: 从生成工具返回结果取 `"content"`
  - `format`: `"markdown"` / `"html"` / `"json"`
  - `file_path`: **必须**从生成工具返回结果取 `"object_name"`（MinIO 对象路径）
  - **重要：generate_html_pentest_report 或 generate_pentest_report 返回后，立即调用此工具保存，不要等待用户指令**
- 添加漏洞 → `mgmt_add_vulnerability(project_identifier=..., pentest_id=..., vuln_id=..., title=..., severity=...)`
- 更新任务状态 → `mgmt_update_pentest_status(project_identifier=..., pentest_id=..., status=...)`
- 使用上下文中的 `project_identifier`，不要询问用户

**重要：报告生成与保存的自动流程（必须严格执行，无需用户额外指令）**
1. 首先创建渗透测试任务 → `mgmt_create_pentest`
2. 获取返回的 pentest_id
3. 生成报告（上传到 MinIO，返回 object_name）：
   - HTML 图文报告 → `generate_html_pentest_report(...)` 返回结果中包含 `"object_name": "pentest/reports/..."`
   - Markdown 报告 → `generate_pentest_report(...)` 返回结果中包含 `"object_name": "pentest/reports/..."`
4. **【自动生成后立即执行】保存到管理接口** → `mgmt_save_pentest_report(pentest_id=..., name=..., content=..., format=..., file_path=...)`
   - `content`: 报告完整内容（从生成工具的返回结果中获取 `"content"` 字段）
   - `format`: `"markdown"` 或 `"html"` 或 `"json"`
   - `file_path`: **必须传入** `"object_name"` 字段的值（MinIO 对象路径），这是前端下载报告的关键
   - `report_type`: `"full"` 或 `"executive"`
   - `risk_score`: 风险评分（可选）
   - `summary`: JSON 字符串形式的摘要数据（可选）
   - **关键：此步骤在生成报告返回后自动执行，不要询问用户是否需要保存**
5. 发现漏洞后 → `mgmt_add_vulnerability(pentest_id=..., vuln_id=..., title=..., severity=...)`
6. 测试完成后 → `mgmt_update_pentest_status(pentest_id=..., status="completed")`

## 📖 Skills 知识库（按需加载）

| Skill | 说明 | 触发条件 |
|-------|------|----------|
| **recon-subdomain** | 子域名枚举技术、工具参数、字典选择 | 执行子域名扫描时 |
| **recon-port-scan** | Nmap/RustScan 参数、扫描策略 | 执行端口扫描时 |
| **recon-dir-scan** | 目录爆破、ffuf 参数、字典配置 | 执行目录扫描时 |
| **recon-fingerprint** | WAF 检测、技术栈识别 | 执行指纹识别时 |
| **exploit-sqli** | SQLMap 参数、手工注入 Payload | SQL 注入检测时 |
| **exploit-xss** | XSS Payload、上下文分析、绕过技巧 | XSS 检测时 |
| **exploit-lfi** | 路径遍历、伪协议、日志投毒 | LFI 检测时 |
| **exploit-file-download** | 敏感文件读取、绕过技巧 | 文件下载检测时 |
| **pentest-report** | 报告格式、CVSS 评分、修复建议模板 | 生成报告时 |
| **results-storage** | SQLite 存储 API、查询方法 | 存储结果时 |

## 🔄 B端应用扫描流程（当目标为前端页面时）

当用户输入的是前端页面 URL（如 `https://console.example.com`）时，按以下流程执行：

```
1. 页面驱动接口发现（约 1-2 分钟）
   └─> discover_apis_from_page(target_url=..., username=..., password=...)
       └─> 返回 {"api_endpoints": [{"url": "...", "method": "GET", ...}], "openapi_paths": {"/api/users": {"get": {...}}}}

2. B端页面安全扫描（约 2-3 分钟，并行执行）
   ├─> scan_web_clickjacking(target_url=...)     → 点击劫持、安全响应头
   ├─> scan_web_csrf(target_url=...)             → CSRF 防护
   ├─> scan_web_xss(target_url=...)              → XSS 漏洞
   └─> scan_web_sensitive_info(target_url=...)   → 敏感信息泄露

3. API 接口安全扫描（关键！约 2-3 分钟，如有发现接口）
   从 discover_apis_from_page 结果中提取 endpoints 参数：
   endpoints = [{"path": "/api/users", "method": "GET"}, {"path": "/api/login", "method": "POST"}, ...]

   然后并行执行：
   ├─> api_auth_bypass_test(base_url="https://api.example.com", endpoints=endpoints)
   ├─> api_idor_test(base_url="https://api.example.com", endpoints=endpoints)
   ├─> api_input_validation_test(base_url="https://api.example.com", endpoints=endpoints)
   └─> api_rate_limit_test(base_url="https://api.example.com", endpoints=endpoints)

4. 传统漏洞扫描（约 2-4 分钟）
   └─> exploit_full_scan(target_url=...)         → 并行执行 SQLi/XSS/LFI/文件下载

5. 结果汇总与报告（约 1 分钟）
   └─> 生成报告 → mgmt_save_pentest_report(...)
```

**API 接口安全扫描数据流（必须执行！）：**
1. 调用 `discover_apis_from_page(target_url=...)` 发现接口
2. 从返回结果的 `api_endpoints` 字段提取接口列表
3. 直接将 `api_endpoints` 作为 `endpoints` 参数传给 API 安全扫描工具（工具会自动处理格式转换）
   - 不需要手动提取 path，直接传入原始 `api_endpoints` 列表即可
4. 如果发现了 API 接口（api_endpoints 列表非空），**必须**调用 API 安全扫描工具
5. `base_url` 设置为 API 的基础 URL（通常是目标域名，如 `https://console-uat.xysjg.com`）

**如果 discover_apis_from_page 返回 0 个接口或失败：**
- 不要跳过 API 安全扫描！
- 使用目标 URL 的基础路径构造 endpoints：`[{"path": "/", "method": "GET"}]`
- 仍然调用 API 安全扫描工具，它们会测试常见的 API 路径

**示例：**
```python
# 1. 发现接口
discovery = await discover_apis_from_page(target_url="https://console.example.com/#/login")
# 返回: {"api_endpoints": [{"url": "https://console.example.com/api/auth/login", "method": "POST"}, ...]}

# 2. 直接传入原始 api_endpoints 进行安全扫描
endpoints = discovery["api_endpoints"]  # 直接传入，无需转换

# 如果 api_endpoints 为空，使用基础路径
if not endpoints:
    endpoints = [{"path": "/", "method": "GET"}]

# 3. 并行执行 API 安全扫描
base_url = "https://console.example.com"
await api_auth_bypass_test(base_url=base_url, endpoints=endpoints)
await api_idor_test(base_url=base_url, endpoints=endpoints)
await api_input_validation_test(base_url=base_url, endpoints=endpoints)
await api_rate_limit_test(base_url=base_url, endpoints=endpoints)
```

**总预计时间：10-15 分钟**

## 🔄 传统 Web 扫描流程（当目标为 URL/域名时）

```
1. 信息收集（约 3-5 分钟）
   └─> recon_full_scan(target=..., target_url=...)（一键并行执行全部）

2. 漏洞扫描（约 2-4 分钟）
   └─> exploit_full_scan(target_url=...)（一键并行执行全部漏洞检测）

3. 结果汇总与报告（约 1 分钟）
   └─> 生成报告 → mgmt_save_pentest_report(...)
```

**总预计时间：6-10 分钟**

**记住：**
- **信息收集**：优先使用 `recon_full_scan` 并行执行，总耗时约 3-5 分钟
- **漏洞扫描**：优先使用 `exploit_full_scan` 并行执行，总耗时约 2-4 分钟
- **报告生成**：生成 Markdown 报告 + HTML 图文报告 → 保存报告
- **整个流程应在 20 分钟内完成**
"""


# =============================================================================
# MCP 工具加载
# =============================================================================

@asynccontextmanager
async def get_chart_mcp_tools():
    """加载 Chart MCP Server 工具（用于生成图表）"""
    async with MultiServerMCPClient(
        {
            "chart": {
                "transport": "stdio",
                "command": "npx",
                "args": ["-y", "@antv/mcp-server-chart"],
            },
        }
    ) as client:
        yield await client.get_tools()


# =============================================================================
# 重试与备用模型配置
# =============================================================================

def _should_retry_503(exc: Exception) -> bool:
    """判断是否应该重试 503 服务过载错误"""
    # 重试 OpenAI/DeepSeek 的 503 服务过载错误
    if "503" in str(exc) or "Server Overloaded" in str(exc) or "service_unavailable" in str(exc):
        return True
    # 重试 RateLimitError 和 TimeoutError
    exc_type_name = type(exc).__name__
    if exc_type_name in ("RateLimitError", "APITimeoutError", "TimeoutError", "InternalServerError"):
        return True
    return False


# 创建模型重试中间件：遇到 503 错误时自动重试 3 次，指数退避
model_retry_middleware = ModelRetryMiddleware(
    max_retries=3,
    retry_on=_should_retry_503,
    on_failure="error",  # 重试耗尽后抛出异常，让上层处理
    backoff_factor=2.0,
    initial_delay=2.0,  # 首次重试等待 2 秒
    max_delay=30.0,  # 最大等待 30 秒
    jitter=True,  # 添加随机抖动避免 thundering herd
)

# 创建备用模型中间件：主模型失败时切换到备用模型
# 优先使用 settings 配置，其次环境变量，最后默认配置
_model_fallback = None

def _get_model_fallback():
    """懒加载备用模型中间件，避免启动时初始化问题"""
    global _model_fallback
    if _model_fallback is None:
        # 尝试从 settings 读取备用模型配置
        fallback_model = (
            getattr(settings, "fallback_llm_model", None)
            or os.environ.get("FALLBACK_LLM_MODEL")
        )
        if not fallback_model:
            logging.info("[SecurityAgent] 未配置备用模型，将不使用备用模型")
            _model_fallback = None
            return _model_fallback

        try:
            _model_fallback = ModelFallbackMiddleware(fallback_model)
            logging.info(f"[SecurityAgent] 备用模型已配置: {fallback_model}")
        except Exception as e:
            logging.warning(f"[SecurityAgent] 备用模型初始化失败: {e}，将不使用备用模型")
            _model_fallback = None
    return _model_fallback


# =============================================================================
# 智能体工厂
# =============================================================================

@asynccontextmanager
async def make_agent() -> AsyncIterator[Pregel]:
    """
    创建渗透测试智能体的工厂函数。

    使用 asynccontextmanager 模式确保：
    - MCP session 在智能体生命周期内保持活跃
    - 退出时自动清理资源
    """
    context_middleware = SecurityContextInjectionMiddleware()
    loop_detection_middleware = LoopDetectionMiddleware(max_history=6, max_repeats=2)

    # 构建中间件列表：重试 + 上下文注入 + 循环检测 + 消息序列验证 + 备用模型
    middleware_list = [
        model_retry_middleware,  # 最外层：先重试
        skills_middleware,
        context_middleware,
        loop_detection_middleware,
        MessageSequenceValidationMiddleware(),  # 确保消息序列符合 OpenAI API 要求
    ]
    fallback = _get_model_fallback()
    if fallback is not None:
        middleware_list.append(fallback)  # 最内层：最后尝试备用模型

    client = MultiServerMCPClient(
        {
            "chart": {
                "transport": "stdio",
                "command": "npx",
                "args": ["-y", "@antv/mcp-server-chart"],
            },
        }
    )
    async with client.session("chart") as session:
        from langchain_mcp_adapters.tools import load_mcp_tools
        mcp_tools = await load_mcp_tools(session)
        all_tools = get_local_tools() + mcp_tools

        security_agent = create_agent(
            model=model,
            tools=all_tools,
            system_prompt=SYSTEM_PROMPT,
            middleware=middleware_list,
            backend=composite_backend,
            context_schema=SecurityAgentContext,
        )

        yield security_agent


# =============================================================================
# 全局智能体实例（同步创建，供直接调用）
# =============================================================================

context_middleware = SecurityContextInjectionMiddleware()
loop_detection_middleware = LoopDetectionMiddleware(max_history=6, max_repeats=2)

# 懒加载 agent 避免循环导入和事件循环冲突
_security_agent = None

def _get_security_agent():
    global _security_agent
    if _security_agent is None:
        import asyncio

        # 加载本地工具（延迟导入避免循环依赖）
        async def _load_tools():
            from app.agents.tools.security import get_local_tools
            return get_local_tools()

        try:
            all_tools = asyncio.run(_load_tools())
        except RuntimeError:
            loop = asyncio.get_event_loop()
            all_tools = loop.run_until_complete(_load_tools())

        # 构建中间件列表（与 make_agent 保持一致）
        middleware_list = [
            model_retry_middleware,
            skills_middleware,
            context_middleware,
            loop_detection_middleware,
            MessageSequenceValidationMiddleware(),  # 确保消息序列符合 OpenAI API 要求
        ]
        fallback = _get_model_fallback()
        if fallback is not None:
            middleware_list.append(fallback)

        _security_agent = create_agent(
            model=model,
            tools=all_tools,
            system_prompt=SYSTEM_PROMPT,
            middleware=middleware_list,
            backend=composite_backend,
            context_schema=SecurityAgentContext,
        )
    return _security_agent


# 导出 agent 对象 - 使用代理模式避免模块导入时立即初始化
# LangGraph 会在运行时调用这个对象，此时事件循环已经就绪
class _AgentProxy:
    """Agent 代理，延迟初始化真正的 agent"""
    _instance = None

    def __getattr__(self, name):
        if self._instance is None:
            self._instance = _get_security_agent()
        return getattr(self._instance, name)

    def __call__(self, config=None):
        if self._instance is None:
            self._instance = _get_security_agent()
        return self._instance(config) if config else self._instance()

    def __await__(self):
        if self._instance is None:
            self._instance = _get_security_agent()
        return self._instance.__await__()


agent = _AgentProxy()
