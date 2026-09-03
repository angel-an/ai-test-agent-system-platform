import logging

from langchain_mcp_adapters.client import MultiServerMCPClient

from app.agents.tool_policy import is_tool_denied
from app.config.settings import settings

logger = logging.getLogger(__name__)

# 只保留 browser_* 工具，过滤掉 planner_*、generator_*、test_* 等会阻塞或依赖 setup 的工具
# 危险工具（browser_evaluate / browser_run_code_unsafe 等）由 tool_policy 黑名单统一拦截
ALLOWED_TOOL_PREFIX = "browser_"


async def get_playwright_tools(extra_servers: dict | None = None) -> list:
    """
    启动 Playwright MCP Server 并获取浏览器操作工具。

    使用 run-mcp-server 提供完整的 browser_* 工具集，
    只保留 browser_* 前缀的工具，过滤掉 planner_*、generator_*、test_* 等
    会阻塞进程或依赖未初始化 session 的工具；
    同时按 tool_policy 黑名单排除危险工具（"看不到"原则）。

    注意：langchain_mcp_adapters 每次工具调用会创建新 session，
    所以工具对象可以安全缓存和复用。
    """
    servers = {
        "web_mcp": {
            "transport": "stdio",
            "command": r"cmd",
            "args": ["/c", f"cd {settings.web_mcp_root} & ",
                     "npx", "playwright", "run-mcp-server"],
        },
        **(extra_servers or {}),
    }

    client = MultiServerMCPClient(servers)
    tools = await client.get_tools()
    # 只保留 browser_* 工具，并按共享安全策略排除危险工具
    safe_tools = [
        t for t in tools
        if t.name.startswith(ALLOWED_TOOL_PREFIX) and not is_tool_denied(t.name)
    ]
    filtered = [t.name for t in tools if t not in safe_tools]
    if filtered:
        logger.warning(f"Filtered out tools: {filtered}")
    return safe_tools
