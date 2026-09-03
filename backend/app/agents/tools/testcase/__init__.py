"""
测试用例生成智能体工具模块

本目录包含所有测试用例生成智能体的工具定义，按功能分类组织：
- 测试用例管理: 创建、更新、批量操作
- 文档解析: 从 URL 下载并解析文档内容
- Excel 导出: 将测试用例导出为 Excel 文件
"""
"""
andan
"""


from typing import List
from langchain_core.tools import BaseTool
# type: ignore  MC80OmFIVnBZMlhscm9ua3VMazZaMGxPT0E9PTo4OWFmNzQ4OA==

from app.agents.tools.testcase.testcase_tools import (
    create_test_case_tool,
    update_test_case_tool,
    batch_create_test_cases_tool,
    stage_testcases,
    list_staged_testcases,
    export_all_testcases,
    clear_staged_testcases,
    stage_uat_scenarios,
    list_staged_uat_scenarios,
    export_all_uat_scenarios,
    clear_staged_uat_scenarios,
)

from app.agents.tools.testcase.document_tools import (
    parse_document_from_url,
    get_rag_tools,
)

from app.agents.tools.testcase.kb_tools import (
    query_knowledge_base_tool,
    get_knowledge_spaces_tool,
)

from app.agents.tools.testcase.excel_tools import (
    export_test_cases_to_excel,
)

# 按业务域分类的工具列表
TESTCASE_TOOLS = [
    create_test_case_tool,
    update_test_case_tool,
    batch_create_test_cases_tool,
]

STAGING_TOOLS = [
    stage_testcases,
    list_staged_testcases,
    export_all_testcases,
    clear_staged_testcases,
    stage_uat_scenarios,
    list_staged_uat_scenarios,
    export_all_uat_scenarios,
    clear_staged_uat_scenarios,
]

DOCUMENT_TOOLS = [
    parse_document_from_url,
]

EXCEL_TOOLS = [
    export_test_cases_to_excel,
]

KB_TOOLS = [
    query_knowledge_base_tool,
    get_knowledge_spaces_tool,
]

ALL_LOCAL_TOOLS = TESTCASE_TOOLS + STAGING_TOOLS + DOCUMENT_TOOLS + KB_TOOLS


async def get_local_tools() -> List[BaseTool]:
    """
    获取所有本地工具列表。

    RAG MCP 工具在 agent.py 的 make_agent() 中异步加载，此处只返回本地工具。
    """
    return list(ALL_LOCAL_TOOLS)


async def get_all_tools() -> List[BaseTool]:
    """
    获取所有工具（包括 RAG MCP 工具）。

    Returns:
        所有工具的列表
    """
    local_tools = await get_local_tools()

    # 加载 RAG MCP 工具
    try:
        rag_tools = await get_rag_tools()
        return local_tools + rag_tools
    except Exception as e:
        print(f"Warning: Failed to load RAG MCP tools: {e}")
        return local_tools


__all__ = [
    # 测试用例管理
    "create_test_case_tool",
    "update_test_case_tool",
    "batch_create_test_cases_tool",
    # 暂存与导出
    "stage_testcases",
    "list_staged_testcases",
    "export_all_testcases",
    "clear_staged_testcases",
    "stage_uat_scenarios",
    "list_staged_uat_scenarios",
    "export_all_uat_scenarios",
    "clear_staged_uat_scenarios",
    # 文档解析
    "parse_document_from_url",
    # Excel 导出
    "export_test_cases_to_excel",
    # 知识库工具
    "query_knowledge_base_tool",
    "get_knowledge_spaces_tool",
    # 分类列表
    "TESTCASE_TOOLS",
    "STAGING_TOOLS",
    "DOCUMENT_TOOLS",
    "EXCEL_TOOLS",
    "KB_TOOLS",
    "ALL_LOCAL_TOOLS",
    "get_local_tools",
    "get_all_tools",
]
