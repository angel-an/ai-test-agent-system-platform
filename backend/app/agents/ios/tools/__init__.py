"""
iOS Agent 工具模块

本目录包含所有 iOS 测试智能体的工具定义，按功能分类组织：
- device_tools: 设备管理（xcrun、截图、设备信息）
- test_artifacts_tools: 成果物管理（计划、用例、脚本、报告的保存与查询）
- script_tools: 脚本管理（下载、删除）
- test_execution_tools: 测试执行（运行、报告收集、解析）
"""

from .device_tools import (
    check_ios_device,
    list_ios_devices,
    get_ios_app_info,
    take_ios_screenshot,
    analyze_ios_screenshot_quality,
)

from .test_artifacts_tools import (
    save_ios_test_plan,
    save_ios_test_cases,
    save_ios_test_script,
    save_ios_test_report,
    get_ios_artifacts,
    get_ios_artifact_content,
)

from .script_tools import (
    get_ios_script_info,
    download_ios_script,
    delete_ios_script,
)

from .test_execution_tools import (
    execute_ios_test,
    collect_ios_report,
    parse_ios_test_report,
    batch_execute_ios_tests,
)

__all__ = [
    # 设备管理工具
    "check_ios_device",
    "list_ios_devices",
    "get_ios_app_info",
    "take_ios_screenshot",
    "analyze_ios_screenshot_quality",

    # 测试成果物管理工具
    "save_ios_test_plan",
    "save_ios_test_cases",
    "save_ios_test_script",
    "save_ios_test_report",
    "get_ios_artifacts",
    "get_ios_artifact_content",

    # 脚本管理工具
    "get_ios_script_info",
    "download_ios_script",
    "delete_ios_script",

    # 测试执行工具
    "execute_ios_test",
    "collect_ios_report",
    "parse_ios_test_report",
    "batch_execute_ios_tests",
]
