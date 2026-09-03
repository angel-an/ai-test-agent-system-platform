"""
API Agent 工具注册表

工具分类：
1. OpenAPI 工具：OpenAPI 文档解析与端点管理（数据库操作）
2. 成果物工具：测试计划、用例、脚本的存储管理（MinIO 操作）
3. 执行工具：测试运行和结果解析（本地执行）
4. 批量工具：批量操作的准备和协调

注意：MCP 工具（api_planner, api_generator, api_healer, chart）
在 agent.py 的 make_agent() 中异步加载，不在此处定义。
"""

# fmt: off  MC80OmFIVnBZMlhscm9ua3VMazZkMjExUXc9PTowNjFiMDY3Zg==

from typing import List
from langchain_core.tools import BaseTool

# =============================================================================
# OpenAPI 文档管理工具
# =============================================================================

from app.agents.api.tools.openapi_tools import (
    # parse_openapi_and_create_structure,
    list_api_endpoints,
    get_endpoint_details,
    get_multiple_endpoints_details,
    get_folder_structure
)

# =============================================================================
# 测试成果物管理工具
# =============================================================================

from app.agents.api.tools.test_artifacts_tools import (
    save_test_plan,
    save_test_cases,
    save_test_script,
    get_endpoint_artifacts,
    get_artifact_content
)

# =============================================================================
# 需求分析报告工具
# =============================================================================

from app.agents.api.tools.requirement_tools import (
    save_requirement_analysis,
)

# =============================================================================
# 测试执行工具
# =============================================================================

from app.agents.api.tools.test_execution_tools import (
    run_tests,
    run_test_suite,
    parse_test_results
)

# =============================================================================
# 脚本管理工具（从 MinIO 下载到 MCP 测试目录）
# =============================================================================

from app.agents.api.tools.script_tools import (
    get_api_script_info,
    download_api_script,
    delete_api_script
)

# =============================================================================
# 脚本执行工具（在 MCP 测试目录中执行脚本）
# =============================================================================

from app.agents.api.tools.script_execution_tools import (
    execute_api_script,
    get_test_execution_status
)
# pragma: no cover  MS80OmFIVnBZMlhscm9ua3VMazZkMjExUXc9PTowNjFiMDY3Zg==

# =============================================================================
# 批量操作工具
# =============================================================================
# pragma: no cover  Mi80OmFIVnBZMlhscm9ua3VMazZkMjExUXc9PTowNjFiMDY3Zg==

from app.agents.api.tools.batch_tools import (
    batch_generate_tests,
    batch_run_tests
)

# =============================================================================
# 图表生成工具
# =============================================================================

from app.agents.api.tools.chart_tools import (
    generate_test_result_chart,
    generate_endpoint_comparison_chart,
    generate_test_trend_chart,
)

# =============================================================================
# 场景测试工具
# =============================================================================

from app.agents.api.tools.scenario_tools import (
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

# =============================================================================
# 工具集合
# =============================================================================

def get_local_tools() -> List[BaseTool]:
    """
    获取所有本地工具列表。

    MCP 工具在 agent.py 中异步加载，此处只返回本地工具。
    """
    return [
        # OpenAPI 文档管理
        # parse_openapi_and_create_structure,
        list_api_endpoints,
        get_endpoint_details,
        get_multiple_endpoints_details,
        get_folder_structure,

        # 测试成果物管理
        save_test_plan,
        save_test_cases,
        save_test_script,
        get_endpoint_artifacts,
        get_artifact_content,

        # 需求分析报告
        save_requirement_analysis,

        # 测试执行
        run_tests,
        run_test_suite,
        parse_test_results,

        # 脚本管理
        get_api_script_info,
        download_api_script,
        delete_api_script,

        # 脚本执行
        execute_api_script,
        get_test_execution_status,

        # 批量操作
        batch_generate_tests,
        batch_run_tests,

        # 场景测试
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

        # 图表生成
        generate_test_result_chart,
        generate_endpoint_comparison_chart,
        generate_test_trend_chart,
    ]

# =============================================================================
# 工具分类导出（供其他模块使用）
# =============================================================================

OPENAPI_TOOLS = [
    # parse_openapi_and_create_structure,
    list_api_endpoints,
    get_endpoint_details,
    get_folder_structure,
]

ARTIFACT_TOOLS = [
    save_test_plan,
    save_test_cases,
    save_test_script,
    get_endpoint_artifacts,
    get_artifact_content,
]

REQUIREMENT_TOOLS = [
    save_requirement_analysis,
]

EXECUTION_TOOLS = [
    run_tests,
    run_test_suite,
    parse_test_results,
]

SCRIPT_TOOLS = [
    get_api_script_info,
    download_api_script,
    delete_api_script,
]
# fmt: off  My80OmFIVnBZMlhscm9ua3VMazZkMjExUXc9PTowNjFiMDY3Zg==

SCRIPT_EXECUTION_TOOLS = [
    execute_api_script,
    get_test_execution_status,
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

CHART_TOOLS = [
    generate_test_result_chart,
    generate_endpoint_comparison_chart,
    generate_test_trend_chart,
]
