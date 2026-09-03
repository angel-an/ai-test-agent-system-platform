"""
API 测试执行工具

提供测试执行、结果收集等功能
"""

import json
import os
import subprocess
from pathlib import Path
from typing import Optional
from datetime import datetime
# fmt: off  MC80OmFIVnBZMlhscm9ua3VMazZTSGhEYmc9PTo5YjY5ZDgwOA==

from langchain_core.tools import tool

from app.config import settings


def _node_command(command: str, workspace_root: Path) -> str:
    """Resolve Node CLI commands without relying on Windows PATH lookup."""
    suffix = ".cmd" if os.name == "nt" else ""
    local_command = workspace_root / "node_modules" / ".bin" / f"{command}{suffix}"
    if local_command.exists():
        return str(local_command)
    return f"{command}{suffix}"

@tool
async def run_tests(
    test_path: str,
    framework: str = "playwright",
    reporter: str = "list"
) -> str:
    """
    运行 API 测试并收集结果

    Args:
        test_path: 测试文件路径或目录
        framework: 测试框架 (playwright, jest, pytest)
        reporter: 报告格式 (list, json, html)

    Returns:
        JSON 格式的测试执行结果

    Example:
        >>> result = await run_tests(
        ...     test_path="./tests/api",
        ...     framework="playwright",
        ...     reporter="json"
        ... )
    """
    try:
        workspace_root = Path(settings.api_workspace_root).resolve()
        # 确定测试命令
        if framework == "playwright":
            cmd = [_node_command("playwright", workspace_root), "test", test_path, f"--reporter={reporter}"]
        elif framework == "jest":
            cmd = [_node_command("npm", workspace_root), "test", "--", test_path, f"--reporter={reporter}"]
        elif framework == "pytest":
            cmd = ["pytest", test_path, f"--reporter={reporter}"]
        else:
            return json.dumps({
                "success": False,
                "error": f"不支持的测试框架: {framework}"
            }, ensure_ascii=False, indent=2)

        # 执行测试
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            encoding='utf-8',
            errors='replace',
            timeout=300,  # 5分钟超时
            cwd=workspace_root,
            shell=False,
        )

        return json.dumps({
            "success": result.returncode == 0,
            "exit_code": result.returncode,
            "stdout": result.stdout,
            "stderr": result.stderr,
            "framework": framework,
            "test_path": test_path,
            "timestamp": datetime.now().isoformat()
        }, ensure_ascii=False, indent=2)

    except subprocess.TimeoutExpired:
        return json.dumps({
            "success": False,
            "error": "测试执行超时（5分钟）"
        }, ensure_ascii=False, indent=2)
    except Exception as e:
        return json.dumps({
            "success": False,
            "error": f"测试执行失败: {str(e)}"
        }, ensure_ascii=False, indent=2)

@tool
async def run_test_suite(
    project_identifier: str,
    endpoint_ids: list[str],
    framework: str = "playwright"
) -> str:
    """
    批量运行多个端点的测试

    Args:
        project_identifier: 项目标识符
        endpoint_ids: 端点 ID 列表
        framework: 测试框架

    Returns:
        JSON 格式的批量测试执行结果
    """
    try:
        results = []
        success_count = 0
        failed_count = 0

        for endpoint_id in endpoint_ids:
            # 构建测试路径
            test_path = f"./api-tests/{project_identifier}/endpoints/{endpoint_id}"
# pragma: no cover  MS80OmFIVnBZMlhscm9ua3VMazZTSGhEYmc9PTo5YjY5ZDgwOA==

            # 运行测试
            result = await run_tests(
                test_path=test_path,
                framework=framework,
                reporter="json"
            )

            result_data = json.loads(result)
            results.append({
                "endpoint_id": endpoint_id,
                "result": result_data
            })

            if result_data.get("success"):
                success_count += 1
            else:
                failed_count += 1

        return json.dumps({
            "success": True,
            "summary": {
                "total": len(endpoint_ids),
                "success": success_count,
                "failed": failed_count
            },
            "results": results
        }, ensure_ascii=False, indent=2)

    except Exception as e:
        return json.dumps({
            "success": False,
            "error": f"批量测试执行失败: {str(e)}"
        }, ensure_ascii=False, indent=2)
# fmt: off  Mi80OmFIVnBZMlhscm9ua3VMazZTSGhEYmc9PTo5YjY5ZDgwOA==

@tool
async def parse_test_results(
    result_output: str
) -> str:
    """
    解析测试输出并提取关键信息

    Args:
        result_output: 测试运行的原始输出

    Returns:
        JSON 格式的解析结果
    """
    try:
        # 尝试解析 JSON 输出
        if result_output.strip().startswith("{"):
            data = json.loads(result_output)
            return json.dumps({
                "success": True,
                "parsed": True,
                "data": data
            }, ensure_ascii=False, indent=2)

        # 解析文本输出
        lines = result_output.split("\n")
        passed = []
        failed = []
        skipped = []

        for line in lines:
            if "✓" in line or "PASS" in line or "passed" in line:
                passed.append(line.strip())
            elif "✗" in line or "FAIL" in line or "failed" in line:
                failed.append(line.strip())
            elif "○" in line or "skipped" in line:
                skipped.append(line.strip())

        return json.dumps({
            "success": True,
            "parsed": True,
            "summary": {
                "passed": len(passed),
                "failed": len(failed),
                "skipped": len(skipped)
            },
            "details": {
                "passed": passed[:10],  # 最多返回10个
                "failed": failed[:10],
                "skipped": skipped[:10]
            }
        }, ensure_ascii=False, indent=2)
# pragma: no cover  My80OmFIVnBZMlhscm9ua3VMazZTSGhEYmc9PTo5YjY5ZDgwOA==

    except Exception as e:
        return json.dumps({
            "success": False,
            "error": f"解析测试结果失败: {str(e)}"
        }, ensure_ascii=False, indent=2)
