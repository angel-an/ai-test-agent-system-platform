"""
iOS Agent 工具注册表

工具分类：
1. 设备管理工具：xcrun 连接、设备信息、屏幕截图
2. 测试成果物工具：测试计划、用例、脚本、报告的存储管理（MinIO 操作）
3. 脚本管理工具：从 MinIO 下载脚本到本地测试目录
4. 测试执行工具：测试运行、结果收集、报告解析

注意：Skills 知识库（midscene-ios-*）在 agent.py 的 make_agent() 中通过
SkillsMiddleware 按需加载，不在此处定义。
"""

from typing import List
from langchain_core.tools import BaseTool

# =============================================================================
# 设备管理工具
# =============================================================================

from app.agents.ios.tools.device_tools import (
    check_ios_device,
    list_ios_devices,
    get_ios_app_info,
    take_ios_screenshot,
    analyze_ios_screenshot_quality,
)

# =============================================================================
# 测试成果物管理工具
# =============================================================================

from app.agents.ios.tools.env_tools import (
    check_ios_env,
    init_ios_project,
)

# =============================================================================
# 测试成果物管理工具
# =============================================================================

from app.agents.ios.tools.test_artifacts_tools import (
    save_ios_test_plan,
    save_ios_test_cases,
    save_ios_test_script,
    save_ios_test_report,
    get_ios_artifacts,
    get_ios_artifact_content,
)

# =============================================================================
# 脚本管理工具（从 MinIO 下载到本地测试目录）
# =============================================================================

from app.agents.ios.tools.script_tools import (
    get_ios_script_info,
    download_ios_script,
    delete_ios_script,
)

# =============================================================================
# 测试执行工具
# =============================================================================

from app.agents.ios.tools.test_execution_tools import (
    execute_ios_test,
    collect_ios_report,
    parse_ios_test_report,
    batch_execute_ios_tests,
)

# =============================================================================
# 工具集合
# =============================================================================

def get_local_tools() -> List[BaseTool]:
    """
    获取所有 iOS 测试智能体的本地工具列表。

    Skills 知识库在 agent.py 中通过 SkillsMiddleware 按需加载，
    此处只返回原子操作工具。
    """
    return [
        # 环境管理工具
        check_ios_env,
        init_ios_project,

        # 设备管理工具
        check_ios_device,
        list_ios_devices,
        get_ios_app_info,
        take_ios_screenshot,
        analyze_ios_screenshot_quality,

        # 测试成果物管理工具
        save_ios_test_plan,
        save_ios_test_cases,
        save_ios_test_script,
        save_ios_test_report,
        get_ios_artifacts,
        get_ios_artifact_content,

        # 脚本管理工具
        get_ios_script_info,
        download_ios_script,
        delete_ios_script,

        # 测试执行工具
        execute_ios_test,
        collect_ios_report,
        parse_ios_test_report,
        batch_execute_ios_tests,
    ]

# =============================================================================
# 工具分类导出（供其他模块使用）
# =============================================================================

ENV_TOOLS = [
    check_ios_env,
    init_ios_project,
]

DEVICE_TOOLS = [
    check_ios_device,
    list_ios_devices,
    get_ios_app_info,
    take_ios_screenshot,
    analyze_ios_screenshot_quality,
]

ARTIFACT_TOOLS = [
    save_ios_test_plan,
    save_ios_test_cases,
    save_ios_test_script,
    save_ios_test_report,
    get_ios_artifacts,
    get_ios_artifact_content,
]

SCRIPT_TOOLS = [
    get_ios_script_info,
    download_ios_script,
    delete_ios_script,
]

EXECUTION_TOOLS = [
    execute_ios_test,
    collect_ios_report,
    parse_ios_test_report,
    batch_execute_ios_tests,
]
