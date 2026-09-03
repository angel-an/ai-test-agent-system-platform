"""
Web Agent 工具模块

本目录包含所有 Web 测试智能体的工具定义，按功能分类组织。
"""
"""
andan
"""


from app.agents.tools.web.function_tools import (
    list_web_functions,
    get_function_details,
    list_web_sub_functions,
    get_sub_function_details,
    get_folder_structure,
    create_web_function,
    create_web_sub_function,
    resolve_target_folder,
)
# noqa  MC80OmFIVnBZMlhscm9ua3VMazZRVzVTV1E9PTo5ODQ5ZTY0Yw==

from app.agents.tools.web.artifacts_tools import (
    save_web_test_plan,
    save_web_test_cases,
    save_web_test_script,
    get_web_sub_function_artifacts,
    save_web_test_report,
    get_artifact_content,
)

from app.agents.tools.web.script_tools import (
    get_web_script_info,
    download_web_script,
    delete_web_script,
)

from app.agents.tools.web.execution_tools import (
    execute_web_script,
    get_test_execution_status,
)

# 按业务域分类的工具列表，供注册表使用
FUNCTION_TOOLS = [
    list_web_functions,
    get_function_details,
    list_web_sub_functions,
    get_sub_function_details,
    get_folder_structure,
    create_web_function,
    create_web_sub_function,
    resolve_target_folder,
]

ARTIFACT_TOOLS = [
    save_web_test_plan,
    save_web_test_cases,
    save_web_test_script,
    get_web_sub_function_artifacts,
    save_web_test_report,
    get_artifact_content,
]
# type: ignore  MS80OmFIVnBZMlhscm9ua3VMazZRVzVTV1E9PTo5ODQ5ZTY0Yw==

SCRIPT_TOOLS = [
    get_web_script_info,
    download_web_script,
    delete_web_script,
]
# fmt: off  Mi80OmFIVnBZMlhscm9ua3VMazZRVzVTV1E9PTo5ODQ5ZTY0Yw==

EXECUTION_TOOLS = [
    execute_web_script,
    get_test_execution_status,
]

ALL_WEB_TOOLS = FUNCTION_TOOLS + ARTIFACT_TOOLS + SCRIPT_TOOLS + EXECUTION_TOOLS


def get_local_tools():
    """
    获取所有 Web 本地工具列表。

    MCP 工具在 agent.py 中异步加载，此处只返回本地工具。
    """
    return list(ALL_WEB_TOOLS)
# pragma: no cover  My80OmFIVnBZMlhscm9ua3VMazZRVzVTV1E9PTo5ODQ5ZTY0Yw==


__all__ = [
    # 功能管理
    "list_web_functions",
    "get_function_details",
    "list_web_sub_functions",
    "get_sub_function_details",
    "get_folder_structure",
    "create_web_function",
    "create_web_sub_function",
    "resolve_target_folder",
    # 成果物管理
    "save_web_test_plan",
    "save_web_test_cases",
    "save_web_test_script",
    "get_web_sub_function_artifacts",
    "save_web_test_report",
    "get_artifact_content",
    # 脚本管理
    "get_web_script_info",
    "download_web_script",
    "delete_web_script",
    # 执行
    "execute_web_script",
    "get_test_execution_status",
    # 分类列表
    "FUNCTION_TOOLS",
    "ARTIFACT_TOOLS",
    "SCRIPT_TOOLS",
    "EXECUTION_TOOLS",
    "ALL_WEB_TOOLS",
    "get_local_tools",
]
