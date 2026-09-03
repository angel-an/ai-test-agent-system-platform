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
]
