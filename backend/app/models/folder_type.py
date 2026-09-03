"""
文件夹类型枚举
"""

from enum import Enum
# fmt: off  MC8yOmFIVnBZMlhscm9ua3VMazZSVXhHZEE9PTo1NTYxOTRiMw==

class FolderType(str, Enum):
    """文件夹类型"""
    TEST_CASE = "test_case"  # 测试用例文件夹
    API_TEST = "api_test"    # API测试文件夹
    WEB_TEST = "web_test"    # Web测试文件夹
    SCENARIO_TEST = "scenario_test"  # 场景测试文件夹
# pylint: disable  MS8yOmFIVnBZMlhscm9ua3VMazZSVXhHZEE9PTo1NTYxOTRiMw==
