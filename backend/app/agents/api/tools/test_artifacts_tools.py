"""
API 测试成果物管理工具

用于保存和查询 API 端点相关的测试成果物：
- 测试计划 (test_plan)
- 测试用例 (test_case)
- 测试脚本 (test_script)
"""

import json
import io
import os
from uuid import UUID, uuid4
from typing import Optional
from datetime import datetime, timezone
from pathlib import Path

from langchain_core.tools import tool
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.attachment import Attachment, AttachmentEntityType
from app.models.api_endpoint import APIEndpoint
from app.config.minio_client import MinIOClient
from app.config.database import async_session_factory
from app.config.settings import settings

def _resolve_workspace_path(file_path: str) -> Path:
    """
    解析文件路径，支持 MCP workspace 中的相对路径

    Args:
        file_path: 文件路径（可以是绝对路径或相对路径）

    Returns:
        解析后的绝对路径
    """
    path = Path(file_path)

    # 获取 API workspace 根目录
    workspace_root = Path(settings.api_workspace_root).resolve()

    # 在 Windows 上，以 / 开头的路径不是真正的绝对路径（没有盘符）
    # 应该被当作相对路径处理，避免解析到 C:\
    if os.name == 'nt':  # Windows
        # 将 / 开头的路径当作相对路径
        if file_path.startswith('/') or file_path.startswith('\\'):
            # 去掉开头的 / 或 \
            file_path = file_path.lstrip('/\\')
            path = Path(file_path)
# noqa  MC80OmFIVnBZMlhscm9ua3VMazZaMkkzYnc9PTpjNzZlZjNkOQ==

    # 如果是绝对路径，直接返回
    if path.is_absolute():
        return path

    # 检查文件是否在当前工作目录存在
    if path.exists():
        return path.resolve()

    # 尝试在 workspace 目录中查找
    workspace_path = workspace_root / path
    if workspace_path.exists():
        return workspace_path

    # 尝试在 MCP 输出目录中查找（MCP 工具可能使用环境变量指定的目录）
    mcp_output_root = os.environ.get('API_WORKSPACE_ROOT')
    if mcp_output_root:
        mcp_path = Path(mcp_output_root) / path
        if mcp_path.exists():
            return mcp_path

    # 如果都找不到，返回 workspace 路径（让调用方处理错误）
    return workspace_root / path

@tool
async def save_test_plan(
    endpoint_id: str,
    plan_path: Optional[str] = None,
    test_plan: Optional[dict] = None,
    plan_content: Optional[str] = None,
    plan_format: str = "markdown",
    project_identifier: str = ""
) -> dict:
    """
    保存 API 端点的测试计划到 MinIO

    支持三种方式提供测试计划内容：
    1. 通过 plan_path 指定由 api_planner 生成的测试计划文件路径
    2. 通过 test_plan 直接提供测试计划字典（JSON 格式）
    3. 通过 plan_content 直接提供测试计划内容（Markdown/字符串）

    Args:
        endpoint_id: API 端点 ID
        plan_path: 测试计划文件路径（由 api_planner 生成），如 "./api-test-plan.md"
        test_plan: 测试计划内容（字典格式），包含：
            - test_scenarios: 测试场景列表
            - coverage: 覆盖率分析
            - priority: 优先级评估
            - estimated_time: 预估测试时间
        plan_content: 测试计划内容（Markdown/字符串格式），可选
        plan_format: 计划格式（markdown, json），默认为 markdown
        project_identifier: 项目标识符

    Returns:
        dict: 包含 attachment_id 和 file_path 的字典
    """
    # 验证 endpoint_id 是否为有效的 UUID
    try:
        endpoint_uuid = UUID(endpoint_id)
    except (ValueError, AttributeError):
        return {"error": f"Invalid endpoint_id format: {endpoint_id}. Must be a valid UUID."}

    # 获取测试计划内容
    plan_bytes = None
    content_type = None
    file_extension = None

    if plan_path:
        # 从 api_planner 生成的文件读取
        try:
            # 使用智能路径解析
            plan_file = _resolve_workspace_path(plan_path)
            if not plan_file.exists():
                return {
                    "error": f"Test plan file not found: {plan_path}",
                    "hint": f"Resolved path: {plan_file}",
                    "tried_paths": [
                        f"Current: {Path(plan_path).resolve()}",
                        f"Workspace: {Path(settings.api_workspace_root).resolve() / plan_path}",
                        f"MCP: {os.environ.get('API_WORKSPACE_ROOT', 'Not set')}"
                    ]
                }
            plan_content = plan_file.read_text(encoding='utf-8')
            plan_bytes = plan_content.encode('utf-8')
# fmt: off  MS80OmFIVnBZMlhscm9ua3VMazZaMkkzYnc9PTpjNzZlZjNkOQ==

            # 根据文件扩展名确定格式
            if plan_file.suffix in ['.md', '.markdown']:
                plan_format = "markdown"
                content_type = "text/markdown"
                file_extension = "md"
            elif plan_file.suffix == '.json':
                plan_format = "json"
                content_type = "application/json"
                file_extension = "json"
            else:
                # 默认使用 markdown
                content_type = "text/markdown"
                file_extension = "md"
        except Exception as e:
            return {"error": f"Failed to read test plan file: {str(e)}"}
    elif test_plan:
        # 从字典生成 JSON
        plan_json = json.dumps(test_plan, ensure_ascii=False, indent=2)
        plan_bytes = plan_json.encode('utf-8')
        content_type = "application/json"
        file_extension = "json"
        plan_format = "json"
    elif plan_content:
        # 直接使用提供的内容
        plan_bytes = plan_content.encode('utf-8')
        if plan_format == "json":
            content_type = "application/json"
            file_extension = "json"
        else:
            content_type = "text/markdown"
            file_extension = "md"
    else:
        return {"error": "Either plan_path, test_plan, or plan_content must be provided"}

    async with async_session_factory() as session:
        # 查询 endpoint
        endpoint_stmt = select(APIEndpoint).where(
            APIEndpoint.id == endpoint_uuid
        )
        endpoint_result = await session.execute(endpoint_stmt)
        endpoint = endpoint_result.scalar_one_or_none()

        if not endpoint:
            return {"error": f"Endpoint {endpoint_id} not found"}

        # 生成 MinIO 对象名称
        object_name = f"api-tests/{project_identifier}/endpoints/{endpoint_id}/test-plan.{file_extension}"

        # 上传到 MinIO
        MinIOClient.upload_bytes(
            object_name=object_name,
            data=plan_bytes,
            content_type=content_type
        )

        # 检查是否已存在相同的附件
        existing_stmt = select(Attachment).where(
            Attachment.object_name == object_name
        )
        existing_result = await session.execute(existing_stmt)
        existing_attachment = existing_result.scalar_one_or_none()

        # 生成文件名和描述
        file_name = f"test-plan-{endpoint.display_name}.{file_extension}"
        format_desc = "Markdown" if plan_format == "markdown" else "JSON"
        description = f"API 端点 {endpoint.display_name} 的测试计划 ({format_desc})"

        if existing_attachment:
            # 更新现有附件
            existing_attachment.file_size = len(plan_bytes)
            existing_attachment.content_type = content_type
            existing_attachment.file_name = file_name
            existing_attachment.description = description
            existing_attachment.updated_at = datetime.now()
            attachment = existing_attachment
        else:
            # 创建新附件记录
            attachment = Attachment(
                entity_type=AttachmentEntityType.API_TEST_PLAN,
                entity_id=endpoint_uuid,
                project_id=endpoint.project_id,
                file_name=file_name,
                file_size=len(plan_bytes),
                content_type=content_type,
                object_name=object_name,
                description=description,
                created_by="api-agent"
            )
            session.add(attachment)

        await session.commit()
        await session.refresh(attachment)

        return {
            "success": True,
            "attachment_id": str(attachment.id),
            "file_path": object_name,
            "format": plan_format,
            "file_extension": file_extension,
            "message": f"测试计划已保存 ({format_desc})"
        }

@tool
async def save_test_cases(
    endpoint_id: str,
    test_cases: list[dict],
    project_identifier: str
) -> dict:
    """
    保存 API 端点的测试用例到 MinIO

    Args:
        endpoint_id: API 端点 ID
        test_cases: 测试用例列表，每个用例包含：
            - name: 用例名称
            - description: 用例描述
            - steps: 测试步骤
            - expected_result: 预期结果
            - priority: 优先级
        project_identifier: 项目标识符

    Returns:
        dict: 包含 attachment_id 和 file_path 的字典
    """
    # 验证 endpoint_id 是否为有效的 UUID
    try:
        endpoint_uuid = UUID(endpoint_id)
    except (ValueError, AttributeError):
        return {"error": f"Invalid endpoint_id format: {endpoint_id}. Must be a valid UUID."}

    async with async_session_factory() as session:
        # 查询 endpoint
        endpoint_stmt = select(APIEndpoint).where(
            APIEndpoint.id == endpoint_uuid
        )
        endpoint_result = await session.execute(endpoint_stmt)
        endpoint = endpoint_result.scalar_one_or_none()

        if not endpoint:
            return {"error": f"Endpoint {endpoint_id} not found"}

        # 序列化测试用例
        cases_json = json.dumps(test_cases, ensure_ascii=False, indent=2)
        cases_bytes = cases_json.encode('utf-8')

        # 生成 MinIO 对象名称
        object_name = f"api-tests/{project_identifier}/endpoints/{endpoint_id}/test-cases.json"

        # 上传到 MinIO
        MinIOClient.upload_bytes(
            object_name=object_name,
            data=cases_bytes,
            content_type="application/json"
        )
# noqa  Mi80OmFIVnBZMlhscm9ua3VMazZaMkkzYnc9PTpjNzZlZjNkOQ==

        # 检查是否已存在相同的附件
        existing_stmt = select(Attachment).where(
            Attachment.object_name == object_name
        )
        existing_result = await session.execute(existing_stmt)
        existing_attachment = existing_result.scalar_one_or_none()

        if existing_attachment:
            # 更新现有附件
            existing_attachment.file_size = len(cases_bytes)
            existing_attachment.description = f"API 端点 {endpoint.display_name} 的测试用例（共 {len(test_cases)} 个）"
            existing_attachment.updated_at = datetime.now()
            attachment = existing_attachment
        else:
            # 创建新附件记录
            attachment = Attachment(
                entity_type=AttachmentEntityType.API_TEST_CASE,
                entity_id=endpoint_uuid,
                project_id=endpoint.project_id,
                file_name=f"test-cases-{endpoint.display_name}.json",
                file_size=len(cases_bytes),
                content_type="application/json",
                object_name=object_name,
                description=f"API 端点 {endpoint.display_name} 的测试用例（共 {len(test_cases)} 个）",
                created_by="api-agent"
            )
            session.add(attachment)

        # 更新端点的测试用例统计
        # object_name 固定 → JSON 是全量覆盖,计数必须与之同步:赋值而非累加,避免重复生成造成脱离实际的膨胀。
        endpoint.total_test_cases = len(test_cases)
        endpoint.updated_at = datetime.now(timezone.utc)

        await session.commit()
        await session.refresh(attachment)

        return {
            "success": True,
            "attachment_id": str(attachment.id),
            "file_path": object_name,
            "test_cases_count": len(test_cases),
            "message": f"已保存 {len(test_cases)} 个测试用例"
        }

@tool
async def save_test_script(
    endpoint_id: str,
    script_path: Optional[str] = None,
    script_content: Optional[str] = None,
    script_language: str = "typescript",
    script_format: str = "playwright",
    project_identifier: str = ""
) -> dict:
    """
    保存 API 端点的测试脚本到 MinIO

    支持两种方式提供脚本内容：
    1. 通过 script_path 指定由 api_generator 生成的脚本文件路径
    2. 通过 script_content 直接提供脚本内容

    Args:
        endpoint_id: API 端点 ID
        script_path: 脚本文件路径（由 api_generator 生成）
        script_content: 脚本内容（代码），可选
        script_language: 脚本语言（如: typescript, python）
        script_format: 脚本格式（如: playwright, pytest）
        project_identifier: 项目标识符

    Returns:
        dict: 包含 attachment_id 和 file_path 的字典
    """
    # 验证 endpoint_id 是否为有效的 UUID
    try:
        endpoint_uuid = UUID(endpoint_id)
    except (ValueError, AttributeError):
        return {"error": f"Invalid endpoint_id format: {endpoint_id}. Must be a valid UUID."}

    # 获取脚本内容
    if script_path:
        # 从 api_generator 生成的文件读取
        try:
            # 使用智能路径解析
            script_file = _resolve_workspace_path(script_path)
            if not script_file.exists():
                return {
                    "error": f"Script file not found: {script_path}",
                    "hint": f"Resolved path: {script_file}",
                    "tried_paths": [
                        f"Current: {Path(script_path).resolve()}",
                        f"Workspace: {Path(settings.api_workspace_root).resolve() / script_path}",
                        f"MCP: {os.environ.get('API_WORKSPACE_ROOT', 'Not set')}"
                    ]
                }
            script_content = script_file.read_text(encoding='utf-8')
        except Exception as e:
            return {"error": f"Failed to read script file: {str(e)}"}
    elif not script_content:
        return {"error": "Either script_path or script_content must be provided"}

    async with async_session_factory() as session:
        # 查询 endpoint
        endpoint_stmt = select(APIEndpoint).where(
            APIEndpoint.id == endpoint_uuid
        )
        endpoint_result = await session.execute(endpoint_stmt)
        endpoint = endpoint_result.scalar_one_or_none()

        if not endpoint:
            return {"error": f"Endpoint {endpoint_id} not found"}

        # 确定文件扩展名
        extension = {
            "typescript": "ts",
            "javascript": "js",
            "python": "py",
            "java": "java",
        }.get(script_language, "txt")

        # 生成 MinIO 对象名称
        object_name = f"api-tests/{project_identifier}/endpoints/{endpoint_id}/test-script.{extension}"

        # 上传到 MinIO
        script_bytes = script_content.encode('utf-8')
        MinIOClient.upload_bytes(
            object_name=object_name,
            data=script_bytes,
            content_type="text/plain"
        )
# fmt: off  My80OmFIVnBZMlhscm9ua3VMazZaMkkzYnc9PTpjNzZlZjNkOQ==

        # 检查是否已存在相同的附件
        existing_stmt = select(Attachment).where(
            Attachment.object_name == object_name
        )
        existing_result = await session.execute(existing_stmt)
        existing_attachment = existing_result.scalar_one_or_none()

        if existing_attachment:
            # 更新现有附件
            existing_attachment.file_size = len(script_bytes)
            existing_attachment.description = f"API 端点 {endpoint.display_name} 的测试脚本 ({script_format} - {script_language})"
            existing_attachment.updated_at = datetime.now()
            attachment = existing_attachment
        else:
            # 创建新附件记录
            attachment = Attachment(
                entity_type=AttachmentEntityType.API_TEST_SCRIPT,
                entity_id=endpoint_uuid,
                project_id=endpoint.project_id,
                file_name=f"test-script.{extension}",
                file_size=len(script_bytes),
                content_type="text/plain",
                object_name=object_name,
                description=f"API 端点 {endpoint.display_name} 的测试脚本 ({script_format} - {script_language})",
                created_by="api-agent"
            )
            session.add(attachment)

        await session.commit()
        await session.refresh(attachment)

        return {
            "success": True,
            "attachment_id": str(attachment.id),
            "file_path": object_name,
            "language": script_language,
            "format": script_format,
            "message": "测试脚本已保存"
        }

@tool
async def get_endpoint_artifacts(
    endpoint_id: str,
    artifact_type: Optional[str] = None
) -> dict:
    """
    获取 API 端点的测试成果物列表

    Args:
        endpoint_id: API 端点 ID
        artifact_type: 成果物类型过滤（可选）:
            - API_TEST_PLAN: 测试计划
            - API_TEST_CASE: 测试用例
            - API_TEST_SCRIPT: 测试脚本

    Returns:
        dict: 成果物列表，包含类型、文件名、描述、创建时间等信息
    """
    # 验证 endpoint_id 是否为有效的 UUID
    try:
        endpoint_uuid = UUID(endpoint_id)
    except (ValueError, AttributeError) as e:
        return {"error": f"Invalid endpoint_id format: {endpoint_id}. Must be a valid UUID."}

    async with async_session_factory() as session:
        # 构建查询
        stmt = select(Attachment).where(
            Attachment.entity_id == endpoint_uuid
        )

        # 按类型过滤
        if artifact_type:
            try:
                entity_type = AttachmentEntityType[artifact_type]
                stmt = stmt.where(Attachment.entity_type == entity_type)
            except KeyError:
                return {"error": f"Invalid artifact_type: {artifact_type}"}

        # 执行查询
        result = await session.execute(stmt)
        attachments = result.scalars().all()

        # 格式化返回
        artifacts = []
        for attachment in attachments:
            artifacts.append({
                "id": str(attachment.id),
                "type": attachment.entity_type.value,
                "file_name": attachment.file_name,
                "description": attachment.description,
                "file_size": attachment.file_size,
                "content_type": attachment.content_type,
                "object_name": attachment.object_name,
                "created_at": attachment.created_at.isoformat() if attachment.created_at else None,
            })

        return {
            "success": True,
            "endpoint_id": endpoint_id,
            "artifacts": artifacts,
            "total": len(artifacts)
        }

@tool
async def get_artifact_content(
    attachment_id: str
) -> dict:
    """
    获取附件内容

    Args:
        attachment_id: 附件 ID

    Returns:
        dict: 包含文件内容和元数据的字典
    """
    async with async_session_factory() as session:
        # 查询附件
        stmt = select(Attachment).where(
            Attachment.id == UUID(attachment_id)
        )
        result = await session.execute(stmt)
        attachment = result.scalar_one_or_none()

        if not attachment:
            return {"error": f"Attachment {attachment_id} not found"}

        # 从 MinIO 下载文件
        try:
            content_bytes = MinIOClient.download_file(attachment.object_name)
            content = content_bytes.decode('utf-8')

            return {
                "success": True,
                "attachment_id": str(attachment.id),
                "type": attachment.entity_type.value,
                "file_name": attachment.file_name,
                "content": content,
                "content_type": attachment.content_type,
                "created_at": attachment.created_at.isoformat() if attachment.created_at else None,
            }
        except Exception as e:
            return {"error": f"Failed to download file: {str(e)}"}
