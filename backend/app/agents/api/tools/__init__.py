"""
API Agent 工具模块

本目录包含所有 API 测试智能体的工具定义，按功能分类组织。
"""

from .openapi_tools import (
    list_api_endpoints,
    get_endpoint_details,
    get_multiple_endpoints_details,
    get_folder_structure,
)
# pylint: disable  MC80OmFIVnBZMlhscm9ua3VMazZabmROWlE9PTo3MTUzNjhmNw==

from .test_artifacts_tools import (
    save_test_plan,
    save_test_cases,
    save_test_script,
    get_endpoint_artifacts,
    get_artifact_content,
)

from .test_execution_tools import (
    run_tests,
    run_test_suite,
    parse_test_results,
)
# noqa  MS80OmFIVnBZMlhscm9ua3VMazZabmROWlE9PTo3MTUzNjhmNw==

from .script_tools import (
    get_api_script_info,
    download_api_script,
    delete_api_script,
)

from .script_execution_tools import (
    execute_api_script,
    get_test_execution_status,
)
# pylint: disable  Mi80OmFIVnBZMlhscm9ua3VMazZabmROWlE9PTo3MTUzNjhmNw==

from .batch_tools import (
    batch_generate_tests,
    batch_run_tests,
)
# pylint: disable  My80OmFIVnBZMlhscm9ua3VMazZabmROWlE9PTo3MTUzNjhmNw==

from .scenario_tools import (
    create_test_scenario,
    update_test_scenario,
    add_scenario_step,
    update_scenario_step,
    add_data_mapping,
    add_step_extractor,
    add_step_assertion,
    get_scenario_details,
    list_test_scenarios,
    execute_scenario,
)

from .coverage_review_tools import (
    review_api_coverage,
)

from typing import List

from langchain_core.tools import BaseTool

# ---------------------------------------------------------------------------
# 按业务域分类的工具列表
#
# 说明：本段原先只存在于遗留副本 app/agents/tools/api/__init__.py 中。
# 该副本已确认为纯水印差异的重复实现并删除，聚合导出统一收敛到本文件。
# 供 app/agents/tools/__init__.py 这类聚合入口复用。
# ---------------------------------------------------------------------------

OPENAPI_TOOLS = [
    list_api_endpoints,
    get_endpoint_details,
    get_multiple_endpoints_details,
    get_folder_structure,
]

ARTIFACT_TOOLS = [
    save_test_plan,
    save_test_cases,
    save_test_script,
    get_endpoint_artifacts,
    get_artifact_content,
]

SCRIPT_TOOLS = [
    get_api_script_info,
    download_api_script,
    delete_api_script,
]

EXECUTION_TOOLS = [
    execute_api_script,
    get_test_execution_status,
]

RUNNER_TOOLS = [
    run_tests,
    run_test_suite,
    parse_test_results,
]

BATCH_TOOLS = [
    batch_generate_tests,
    batch_run_tests,
]

SCENARIO_TOOLS = [
    create_test_scenario,
    update_test_scenario,
    add_scenario_step,
    update_scenario_step,
    add_data_mapping,
    add_step_extractor,
    add_step_assertion,
    get_scenario_details,
    list_test_scenarios,
    execute_scenario,
]

COVERAGE_TOOLS = [
    review_api_coverage,
]

ALL_API_TOOLS = (
    OPENAPI_TOOLS
    + ARTIFACT_TOOLS
    + SCRIPT_TOOLS
    + EXECUTION_TOOLS
    + RUNNER_TOOLS
    + BATCH_TOOLS
    + SCENARIO_TOOLS
    + COVERAGE_TOOLS
)


def get_local_tools() -> List[BaseTool]:
    """获取所有 API 本地工具列表（MCP 工具在 agent.py 中异步加载）。"""
    return list(ALL_API_TOOLS)

__all__ = [
    # OpenAPI 文档管理工具
    "list_api_endpoints",
    "get_endpoint_details",
    "get_multiple_endpoints_details",
    "get_folder_structure",

    # 测试成果物管理工具
    "save_test_plan",
    "save_test_cases",
    "save_test_script",
    "get_endpoint_artifacts",
    "get_artifact_content",

    # 测试执行工具
    "run_tests",
    "run_test_suite",
    "parse_test_results",

    # 脚本管理工具
    "get_api_script_info",
    "download_api_script",
    "delete_api_script",

    # 脚本执行工具
    "execute_api_script",
    "get_test_execution_status",

    # 批量操作工具
    "batch_generate_tests",
    "batch_run_tests",

    # 场景测试工具
    "create_test_scenario",
    "update_test_scenario",
    "add_scenario_step",
    "update_scenario_step",
    "add_data_mapping",
    "add_step_extractor",
    "add_step_assertion",
    "get_scenario_details",
    "list_test_scenarios",
    "execute_scenario",

    # 覆盖度评审工具
    "review_api_coverage",

    # 分类列表与聚合（承接原 app/agents/tools/api/__init__.py 的导出）
    "OPENAPI_TOOLS",
    "ARTIFACT_TOOLS",
    "SCRIPT_TOOLS",
    "EXECUTION_TOOLS",
    "RUNNER_TOOLS",
    "BATCH_TOOLS",
    "SCENARIO_TOOLS",
    "COVERAGE_TOOLS",
    "ALL_API_TOOLS",
    "get_local_tools",
]
