"""
Android Agent 工具模块

本目录包含所有 Android 测试智能体的工具定义，按功能分类组织：
- device_tools: 设备管理（adb、截图、设备信息）
- test_artifacts_tools: 成果物管理（计划、用例、脚本、报告的保存与查询）
- script_tools: 脚本管理（下载、删除）
- test_execution_tools: 测试执行（运行、报告收集、解析）
"""

from .device_tools import (
    check_android_device,
    list_connected_devices,
    get_app_info,
    take_android_screenshot,
    analyze_screenshot_quality,
)

from .test_artifacts_tools import (
    save_android_test_plan,
    save_android_test_cases,
    save_android_test_script,
    save_android_test_report,
    get_android_artifacts,
    get_android_artifact_content,
)

from .script_tools import (
    get_android_script_info,
    download_android_script,
    delete_android_script,
)

from .test_execution_tools import (
    execute_android_test,
    collect_android_report,
    parse_android_test_report,
    batch_execute_android_tests,
)

__all__ = [
    # 设备管理工具
    "check_android_device",
    "list_connected_devices",
    "get_app_info",
    "take_android_screenshot",
    "analyze_screenshot_quality",

    # 测试成果物管理工具
    "save_android_test_plan",
    "save_android_test_cases",
    "save_android_test_script",
    "save_android_test_report",
    "get_android_artifacts",
    "get_android_artifact_content",

    # 脚本管理工具
    "get_android_script_info",
    "download_android_script",
    "delete_android_script",

    # 测试执行工具
    "execute_android_test",
    "collect_android_report",
    "parse_android_test_report",
    "batch_execute_android_tests",
]
