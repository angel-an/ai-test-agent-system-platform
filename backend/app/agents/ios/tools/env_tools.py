"""
iOS 测试环境管理工具

提供 xcrun 设备检查、环境预检、依赖安装、项目初始化等辅助功能
"""

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Optional

from langchain_core.tools import tool

from app.config import settings


# ============================================================================
# 环境检查工具（从 Android 迁移并适配 iOS）
# ============================================================================

@tool
async def check_ios_env() -> str:
    """
    检查 iOS 测试环境是否就绪

    检查项：
    - Node.js 版本
    - xcrun / Xcode 是否可用
    - 已连接设备/模拟器列表
    - 项目依赖是否安装（@midscene/ios）
    - .env 配置是否完整

    Returns:
        JSON 格式的环境检查结果

    Example:
        >>> result = await check_ios_env()
    """
    checks = {}

    # 1. 检查 Node.js
    try:
        result = subprocess.run(
            ["node", "-v"],
            capture_output=True,
            text=True,
            timeout=10,
            shell=sys.platform == "win32"
        )
        checks["node"] = {
            "available": result.returncode == 0,
            "version": result.stdout.strip() if result.returncode == 0 else None,
            "error": result.stderr.strip() if result.returncode != 0 else None
        }
    except Exception as e:
        checks["node"] = {"available": False, "error": str(e)}

    # 2. 检查 xcrun / Xcode
    try:
        result = subprocess.run(
            ["xcrun", "--version"],
            capture_output=True,
            text=True,
            timeout=10,
            shell=sys.platform == "win32"
        )
        checks["xcrun"] = {
            "available": result.returncode == 0,
            "version": result.stdout.strip().split("\n")[0] if result.returncode == 0 else None,
            "error": result.stderr.strip() if result.returncode != 0 else None
        }
    except Exception as e:
        checks["xcrun"] = {"available": False, "error": str(e)}

    # 3. 检查 simctl（模拟器控制）
    try:
        result = subprocess.run(
            ["xcrun", "simctl", "list", "devices", "available"],
            capture_output=True,
            text=True,
            timeout=15,
            shell=sys.platform == "win32"
        )
        simulators = []
        if result.returncode == 0:
            # 解析可用模拟器列表
            for line in result.stdout.split("\n"):
                line = line.strip()
                if "(" in line and ")" in line and "Shutdown" not in line:
                    # 提取设备名称和 UDID
                    name = line.split("(")[0].strip()
                    udid_match = line.split("(")[1].split(")")[0] if len(line.split("(")) > 1 else ""
                    if udid_match and len(udid_match) > 8:
                        simulators.append({"name": name, "udid": udid_match})
        checks["simulators"] = {
            "count": len(simulators),
            "devices": simulators[:10],  # 最多显示10个
            "ready": len(simulators) > 0
        }
    except Exception as e:
        checks["simulators"] = {"count": 0, "devices": [], "ready": False, "error": str(e)}

    # 4. 检查项目依赖
    project_root = Path(settings.ios_workspace_root).resolve()
    package_json = project_root / "package.json"
    node_modules = project_root / "node_modules" / "@midscene" / "ios"
    checks["project"] = {
        "workspace_root": str(project_root),
        "package_json_exists": package_json.exists(),
        "midscene_ios_installed": node_modules.exists()
    }

    # 5. 检查 .env 配置
    env_file = project_root / ".env"
    env_vars = {}
    if env_file.exists():
        try:
            with open(env_file, 'r', encoding='utf-8') as f:
                for line in f:
                    line = line.strip()
                    if line and '=' in line and not line.startswith('#'):
                        key, _, value = line.partition('=')
                        if key.startswith('MIDSCENE'):
                            env_vars[key.strip()] = '已配置' if value.strip() else '空值'
        except Exception as e:
            checks["env"] = {"available": False, "error": str(e)}
    checks["env"] = {
        "available": env_file.exists(),
        "midscene_vars": env_vars
    }

    overall_ready = (
        checks["node"]["available"]
        and checks["xcrun"]["available"]
        and checks["simulators"]["ready"]
    )

    return json.dumps({
        "success": True,
        "ready": overall_ready,
        "checks": checks,
        "message": "环境检查完成" if overall_ready else "环境未就绪，请根据 checks 修复"
    }, ensure_ascii=False, indent=2)


# ============================================================================
# 项目初始化工具（从 Android 迁移并适配 iOS）
# ============================================================================

@tool
async def init_ios_project(
    project_identifier: str = "",
    midscene_model_api_key: Optional[str] = None
) -> str:
    """
    初始化 iOS 测试项目工作区

    此工具会：
    1. 创建 workspace/ios 目录
    2. 初始化 package.json（含 @midscene/ios, tsx, typescript, dotenv）
    3. 创建 .env 配置文件（配置 Midscene AI 模型参数）
    4. 创建 tests/ios 目录
    5. 执行 npm install 安装依赖

    Args:
        project_identifier: 项目标识符（用于生成 package.json 名称）
        midscene_model_api_key: Midscene AI 模型 API Key（可选）

    Returns:
        JSON 格式的初始化结果

    Example:
        >>> result = await init_ios_project(
        ...     project_identifier="proj_001",
        ...     midscene_model_api_key="sk-your-key"
        ... )
    """
    try:
        project_root = Path(settings.ios_workspace_root).resolve()
        project_root.mkdir(parents=True, exist_ok=True)

        # 1. 创建 package.json（如不存在）
        package_json = project_root / "package.json"
        if not package_json.exists():
            package_content = {
                "name": f"ios-tests-{project_identifier or 'default'}",
                "version": "1.0.0",
                "private": True,
                "scripts": {
                    "test": "tsx",
                    "test:ios": "tsx tests/ios/**/*.ts"
                },
                "devDependencies": {
                    "tsx": "^4.0.0",
                    "typescript": "^5.0.0",
                    "@types/node": "^20.0.0"
                },
                "dependencies": {
                    "@midscene/ios": "latest",
                    "dotenv": "^16.0.0"
                }
            }
            with open(package_json, 'w', encoding='utf-8') as f:
                json.dump(package_content, f, ensure_ascii=False, indent=2)

        # 2. 创建 .env
        env_file = project_root / ".env"
        if not env_file.exists():
            env_lines = [
                "# Midscene.js iOS 模型配置",
                "MIDSCENE_MODEL_NAME=qwen3.7-plus",
                "MIDSCENE_MODEL_BASE_URL=https://dashscope.aliyuncs.com/compatible-mode/v1",
                f"MIDSCENE_MODEL_API_KEY={midscene_model_api_key or 'sk-your-api-key'}",
                "MIDSCENE_MODEL_FAMILY=qwen3",
                "",
            ]
            with open(env_file, 'w', encoding='utf-8') as f:
                f.write("\n".join(env_lines))

        # 3. 创建 tests/ios 目录
        tests_dir = project_root / "tests" / "ios"
        tests_dir.mkdir(parents=True, exist_ok=True)

        # 4. 安装依赖
        install_result = subprocess.run(
            ["npm", "install"],
            cwd=str(project_root),
            capture_output=True,
            text=True,
            timeout=300,
            shell=sys.platform == "win32"
        )

        return json.dumps({
            "success": install_result.returncode == 0,
            "workspace_root": str(project_root),
            "package_json_created": not package_json.exists() or True,
            "env_file_created": True,
            "npm_install_returncode": install_result.returncode,
            "npm_install_stdout": install_result.stdout[-2000:] if install_result.stdout else "",
            "npm_install_stderr": install_result.stderr[-2000:] if install_result.stderr else "",
            "message": "iOS 测试项目初始化完成" if install_result.returncode == 0 else "npm install 失败，请手动安装"
        }, ensure_ascii=False, indent=2)

    except Exception as e:
        import traceback
        traceback.print_exc()
        return json.dumps({
            "success": False,
            "error": f"初始化项目时发生错误: {str(e)}"
        }, ensure_ascii=False, indent=2)
