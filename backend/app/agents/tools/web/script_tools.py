"""
Web 测试脚本管理工具

提供从数据库查询脚本、从 MinIO 下载脚本到 MCP 测试目录的功能
"""
"""
andan
"""


import os
import sys
import json
from pathlib import Path
from typing import Optional
from datetime import datetime
from uuid import UUID

from langchain_core.tools import tool
from sqlalchemy import select

from app.config import settings
from app.config.database import async_session_factory
from app.models.attachment import Attachment
from app.config.minio_client import MinIOClient


# ============================================================================
# workspace 测试目录配置
# ============================================================================

# workspace 测试服务器根目录
# 优先使用 web_cli 目录，如果不存在则使用 web_mcp 或 webwright 目录
def _get_workspace_root() -> Path:
    """获取当前使用的 workspace 根目录

    按优先级检测：web_cli -> web_mcp -> webwright
    用于支持不同 web 测试模式（web_cli, web_mcp, webwright）
    """
    # 按优先级检测各个 workspace 目录
    for root_attr in ['web_cli_workspace_root', 'web_mcp_workspace_root', 'webwright_workspace_root']:
        root_path = Path(getattr(settings, root_attr, ''))
        if root_path.exists():
            return root_path
    # 默认返回 web_cli（保持向后兼容）
    return Path(settings.web_cli_workspace_root)


WORKSPACE_TESTS_ROOT = _get_workspace_root() / "tests"


def ensure_workspace_tests_dir() -> Path:
    """
    确保测试目录存在并返回路径

    Returns:
        测试目录的绝对路径
    """
    workspace_root = _get_workspace_root()
    tests_dir = workspace_root / "tests"
    tests_dir.mkdir(parents=True, exist_ok=True)
    return tests_dir
# fmt: off  MC80OmFIVnBZMlhscm9ua3VMazZNR1pYVmc9PTo0YmI3NzliMw==

# type: ignore  MS80OmFIVnBZMlhscm9ua3VMazZNR1pYVmc9PTo0YmI3NzliMw==

@tool
async def get_web_script_info(script_id: str) -> str:
    """
    根据 Web 测试脚本 ID 查询脚本的详细信息

    Args:
        script_id: Web 测试脚本附件的 ID

    Returns:
        JSON 格式的脚本信息，包含：
        - success: 是否成功
        - script: 脚本详细信息
        - local_path: 如果已下载到本地，显示本地路径

    Example:
        >>> info = await get_web_script_info("550e8400-e29b-41d4-a716-446655440000")
    """
    try:
        async with async_session_factory() as db:
            result = await db.execute(
                select(Attachment).where(Attachment.id == UUID(script_id))
            )
            attachment = result.scalar_one_or_none()

            if not attachment:
                return json.dumps({
                    "success": False,
                    "error": f"未找到脚本 ID: {script_id}"
                }, ensure_ascii=False, indent=2)

            # 检查是否已下载到本地
            workspace_tests_dir = ensure_workspace_tests_dir()
            local_script_path = workspace_tests_dir / attachment.file_name
            is_downloaded = local_script_path.exists()

            script_info = {
                "success": True,
                "script": {
                    "id": str(attachment.id),
                    "file_name": attachment.file_name,
                    "description": attachment.description,
                    "file_size": attachment.file_size,
                    "content_type": attachment.content_type,
                    "object_name": attachment.object_name,
                    "entity_id": str(attachment.entity_id),
                    "created_at": attachment.created_at.isoformat() if attachment.created_at else None,
                    "updated_at": attachment.updated_at.isoformat() if attachment.updated_at else None,
                }
            }

            if is_downloaded:
                script_info["script"]["local_path"] = str(local_script_path)
                script_info["message"] = "脚本已下载到本地"

            return json.dumps(script_info, ensure_ascii=False, indent=2)

    except Exception as e:
        return json.dumps({
            "success": False,
            "error": f"查询脚本信息时发生错误: {str(e)}"
        }, ensure_ascii=False, indent=2)


@tool
async def download_web_script(
    script_id: str,
    filename: Optional[str] = None,
    project_identifier: str = ""
) -> str:
    """
    从 MinIO 下载 Web 测试脚本到 MCP 测试目录

    此工具会：
    1. 从数据库查询脚本信息并校验项目归属
    2. 从 MinIO 下载脚本内容
    3. 保存到 MCP 测试目录（backend/mcp/web/tests/）
    4. 使用时间戳重命名避免冲突
    5. 返回本地文件路径

    Args:
        script_id: Web 测试脚本附件的 ID
        filename: 可选，指定下载后的文件名（不含扩展名，会自动添加 .spec.ts）
        project_identifier: 项目标识符，用于校验脚本归属（必填）

    Returns:
        JSON 格式的下载结果，包含：
        - success: 是否成功
        - script_id: 脚本 ID
        - original_filename: 原始文件名
        - local_filename: 本地文件名（带时间戳）
        - local_path: 本地完整路径
        - file_size: 文件大小
        - download_time: 下载时间

    Example:
        >>> result = await download_web_script(
        ...     script_id="550e8400-e29b-41d4-a716-446655440000",
        ...     filename="login_test",
        ...     project_identifier="PR-3"
        ... )
    """
    try:
        # 1. 从数据库查询脚本信息并校验项目归属
        async with async_session_factory() as db:
            result = await db.execute(
                select(Attachment).where(Attachment.id == UUID(script_id))
            )
            attachment = result.scalar_one_or_none()

            if not attachment:
                return json.dumps({
                    "success": False,
                    "error": f"未找到脚本 ID: {script_id}"
                }, ensure_ascii=False, indent=2)

            # 校验项目归属：脚本必须属于传入的 project_identifier
            if project_identifier:
                from app.models.project import Project
                project_result = await db.execute(
                    select(Project).where(Project.identifier == project_identifier)
                )
                project = project_result.scalar_one_or_none()

                if not project:
                    return json.dumps({
                        "success": False,
                        "error": f"项目不存在: {project_identifier}"
                    }, ensure_ascii=False, indent=2)

                if attachment.project_id != project.id:
                    return json.dumps({
                        "success": False,
                        "error": f"权限拒绝：脚本 {script_id} 不属于项目 {project_identifier}"
                    }, ensure_ascii=False, indent=2)

            # 保存脚本信息
            script_id_str = str(attachment.id)
            original_filename = attachment.file_name
            file_size = attachment.file_size
            content_type = attachment.content_type
            object_name = attachment.object_name

        # 2. 从 MinIO 下载脚本内容
        script_bytes = MinIOClient.download_file(object_name)
        script_content = script_bytes.decode('utf-8')
# pylint: disable  Mi80OmFIVnBZMlhscm9ua3VMazZNR1pYVmc9PTo0YmI3NzliMw==

        if not script_content:
            return json.dumps({
                "success": False,
                "error": f"无法从存储服务器下载脚本: {original_filename}"
            }, ensure_ascii=False, indent=2)

        # 3. 确保 测试目录存在
        workspace_tests_dir = ensure_workspace_tests_dir()

        # 4. 生成本地文件名（带时间戳）
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        file_extension = Path(original_filename).suffix

        # 确保文件名符合测试文件模式（支持 .py, .spec.ts, .test.ts）
        if file_extension in ('.py', '.spec.ts', '.test.ts'):
            # 保持原始扩展名
            base_name = Path(original_filename).stem
            # 移除可能的 .spec 或 .test 后缀以获得更干净的名称
            base_name = base_name.replace('.spec', '').replace('.test', '')
            local_filename = f"{filename or base_name}_{timestamp}{file_extension}"
        elif file_extension in ('.ts', '.js'):
            # 对于普通 .ts/.js 文件，添加 .spec 前缀使其成为 Playwright 测试文件
            base_name = Path(original_filename).stem
            local_filename = f"{filename or base_name}_{timestamp}.spec{file_extension}"
        elif not file_extension:
            # 无扩展名，默认添加 .py（Webwright 模式）
            base_name = filename or original_filename
            local_filename = f"{base_name}_{timestamp}.py"
        else:
            base_name = Path(original_filename).stem
            local_filename = f"{filename or base_name}_{timestamp}{file_extension}"

        # 5. 确定保存目录
        # 检查是否是 webwright 模式的 Python 脚本（保存到 final_runs/ 或 workspace 根目录）
        workspace_root = _get_workspace_root()
        if file_extension == '.py':
            # Webwright 模式：保存到 workspace 根目录（脚本路径通常包含 final_runs/run_XXX/）
            save_dir = workspace_root
        else:
            # web_cli/web_mcp 模式：保存到 tests/ 目录
            save_dir = workspace_tests_dir
        local_path = save_dir / local_filename

        with open(local_path, 'w', encoding='utf-8') as f:
            f.write(script_content)

        print(f"[Script Download] 脚本已下载到: {local_path}")

        # 6. 返回结果
        return json.dumps({
            "success": True,
            "script_id": script_id_str,
            "original_filename": original_filename,
            "local_filename": local_filename,
            "local_path": str(local_path),
            "file_size": file_size,
            "content_type": content_type,
            "download_time": datetime.now().isoformat(),
            "message": "脚本已下载到测试目录"
        }, ensure_ascii=False, indent=2)

    except Exception as e:
        import traceback
        traceback.print_exc()
        return json.dumps({
            "success": False,
            "error": f"下载脚本时发生错误: {str(e)}"
        }, ensure_ascii=False, indent=2)


@tool
async def delete_web_script(
    local_path: str
) -> str:
    """
    删除本地测试脚本文件

    从 Web 测试 workspace 删除已下载的脚本文件（支持 web_cli/web_mcp 的 tests/ 目录和 webwright 的 workspace 目录）

    Args:
        local_path: 本地脚本文件的完整路径

    Returns:
        JSON 格式的删除结果

    Example:
        >>> result = await delete_web_script(
        ...     local_path="backend/workspace/web/tests/login_test_20260211.spec.ts"
        ... )
    """
    try:
        script_path = Path(local_path)

        if not script_path.exists():
            return json.dumps({
                "success": False,
                "error": f"文件不存在: {local_path}"
            }, ensure_ascii=False, indent=2)

        # 确保路径在允许的 workspace 目录内（支持 web_cli/web_mcp 的 tests/ 和 webwright 的 workspace）
        workspace_tests_dir = ensure_workspace_tests_dir()
        workspace_root = _get_workspace_root()
        resolved_path = str(script_path.resolve())
        is_in_tests_dir = resolved_path.startswith(str(workspace_tests_dir.resolve()))
        is_in_workspace = resolved_path.startswith(str(workspace_root.resolve()))
        if not (is_in_tests_dir or is_in_workspace):
            return json.dumps({
                "success": False,
                "error": "只能删除 Web workspace 目录下的文件"
            }, ensure_ascii=False, indent=2)

        # 删除文件
        script_path.unlink()
# pragma: no cover  My80OmFIVnBZMlhscm9ua3VMazZNR1pYVmc9PTo0YmI3NzliMw==

        print(f"[Script Management] 脚本已删除: {local_path}")

        return json.dumps({
            "success": True,
            "local_path": local_path,
            "message": "脚本已删除"
        }, ensure_ascii=False, indent=2)

    except Exception as e:
        return json.dumps({
            "success": False,
            "error": f"删除脚本时发生错误: {str(e)}"
        }, ensure_ascii=False, indent=2)
