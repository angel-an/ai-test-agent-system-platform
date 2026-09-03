"""
iOS 测试成果物管理工具

用于保存和查询 iOS 测试相关的成果物：
- 测试计划 (ios_test_plan)
- 测试用例 (ios_test_case)
- 测试脚本 (ios_test_script)
- 测试报告 (ios_test_report)
"""

import json
import os
from uuid import UUID, uuid4
from typing import Optional
from datetime import datetime, timezone
from pathlib import Path

from langchain_core.tools import tool
from sqlalchemy import select

from app.models.attachment import Attachment, AttachmentEntityType
from app.config.minio_client import MinIOClient
from app.config.database import async_session_factory
from app.config.settings import settings


def _resolve_workspace_path(file_path: str) -> Path:
    """
    解析文件路径，支持 workspace 中的相对路径

    Args:
        file_path: 文件路径（可以是绝对路径或相对路径）

    Returns:
        解析后的绝对路径
    """
    path = Path(file_path)

    # 获取 iOS workspace 根目录
    workspace_root = Path(settings.ios_workspace_root).resolve()

    # 在 Windows 上，以 / 开头的路径不是真正的绝对路径（没有盘符）
    # 应该被当作相对路径处理，避免解析到 C:\
    if os.name == 'nt':  # Windows
        # 将 / 开头的路径当作相对路径
        if file_path.startswith('/') or file_path.startswith('\\'):
            # 去掉开头的 / 或 \
            file_path = file_path.lstrip('/\\')
            path = Path(file_path)

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

    # 如果都找不到，返回 workspace 路径（让调用方处理错误）
    return workspace_root / path


@tool
async def save_ios_test_plan(
    app_bundle_id: str,
    plan_content: str,
    plan_format: str = "markdown",
    project_identifier: str = ""
) -> dict:
    """
    保存 iOS 测试计划到 MinIO

    Args:
        app_bundle_id: 被测应用 Bundle ID，如 "com.example.app"
        plan_content: 测试计划内容（Markdown/字符串格式）
        plan_format: 计划格式（markdown, json），默认为 markdown
        project_identifier: 项目标识符

    Returns:
        dict: 包含 attachment_id 和 file_path 的字典
    """
    # 生成唯一标识
    entity_id = uuid4()

    # 确定内容类型和扩展名
    if plan_format == "json":
        content_type = "application/json"
        file_extension = "json"
    else:
        content_type = "text/markdown"
        file_extension = "md"

    plan_bytes = plan_content.encode('utf-8')

    # 生成 MinIO 对象名称
    object_name = f"ios-tests/{project_identifier}/apps/{app_bundle_id}/test-plan.{file_extension}"

    # 上传到 MinIO
    MinIOClient.upload_bytes(
        object_name=object_name,
        data=plan_bytes,
        content_type=content_type
    )

    async with async_session_factory() as session:
        # 检查是否已存在相同的附件
        existing_stmt = select(Attachment).where(
            Attachment.object_name == object_name
        )
        existing_result = await session.execute(existing_stmt)
        existing_attachment = existing_result.scalar_one_or_none()

        # 生成文件名和描述
        file_name = f"test-plan-{app_bundle_id}.{file_extension}"
        format_desc = "Markdown" if plan_format == "markdown" else "JSON"
        description = f"iOS 应用 {app_bundle_id} 的测试计划 ({format_desc})"

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
                entity_type=AttachmentEntityType.IOS_TEST_PLAN,
                entity_id=entity_id,
                project_id=UUID("00000000-0000-0000-0000-000000000001"),  # 默认项目ID
                file_name=file_name,
                file_size=len(plan_bytes),
                content_type=content_type,
                object_name=object_name,
                description=description,
                created_by="ios-agent"
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
async def save_ios_test_cases(
    app_bundle_id: str,
    test_cases: list[dict],
    project_identifier: str = ""
) -> dict:
    """
    保存 iOS 测试用例到 MinIO

    Args:
        app_bundle_id: 被测应用 Bundle ID，如 "com.example.app"
        test_cases: 测试用例列表，每个用例包含：
            - id: 用例ID
            - name: 用例名称
            - description: 用例描述
            - steps: 测试步骤（aiAct/aiQuery/aiAssert 描述）
            - expected_result: 预期结果
            - priority: 优先级（P0/P1/P2）
        project_identifier: 项目标识符

    Returns:
        dict: 包含 attachment_id 和 file_path 的字典
    """
    entity_id = uuid4()

    # 序列化测试用例
    cases_json = json.dumps(test_cases, ensure_ascii=False, indent=2)
    cases_bytes = cases_json.encode('utf-8')

    # 生成 MinIO 对象名称
    object_name = f"ios-tests/{project_identifier}/apps/{app_bundle_id}/test-cases.json"

    # 上传到 MinIO
    MinIOClient.upload_bytes(
        object_name=object_name,
        data=cases_bytes,
        content_type="application/json"
    )

    async with async_session_factory() as session:
        # 检查是否已存在相同的附件
        existing_stmt = select(Attachment).where(
            Attachment.object_name == object_name
        )
        existing_result = await session.execute(existing_stmt)
        existing_attachment = existing_result.scalar_one_or_none()

        description = f"iOS 应用 {app_bundle_id} 的测试用例（共 {len(test_cases)} 个）"

        if existing_attachment:
            # 更新现有附件
            existing_attachment.file_size = len(cases_bytes)
            existing_attachment.description = description
            existing_attachment.updated_at = datetime.now()
            attachment = existing_attachment
        else:
            # 创建新附件记录
            attachment = Attachment(
                entity_type=AttachmentEntityType.IOS_TEST_CASE,
                entity_id=entity_id,
                project_id=UUID("00000000-0000-0000-0000-000000000001"),
                file_name=f"test-cases-{app_bundle_id}.json",
                file_size=len(cases_bytes),
                content_type="application/json",
                object_name=object_name,
                description=description,
                created_by="ios-agent"
            )
            session.add(attachment)

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
async def save_ios_test_script(
    app_bundle_id: str,
    script_content: str,
    script_name: str = "test",
    script_language: str = "typescript",
    project_identifier: str = "",
    sub_function_id: str = "",
) -> dict:
    """
    保存 iOS 测试脚本到 MinIO

    Args:
        app_bundle_id: 被测应用 Bundle ID，如 "com.example.app"
        script_content: 脚本内容（TypeScript 代码）
        script_name: 脚本名称（用于生成文件名）
        script_language: 脚本语言（默认为 typescript）
        project_identifier: 项目标识符
        sub_function_id: 可选，子功能 ID，用于关联脚本到子功能

    Returns:
        dict: 包含 attachment_id 和 file_path 的字典
    """
    # 如果提供了 sub_function_id，使用它作为 entity_id
    if sub_function_id:
        try:
            entity_id = UUID(sub_function_id)
        except ValueError:
            entity_id = uuid4()
    else:
        entity_id = uuid4()

    # 确定文件扩展名
    extension = {
        "typescript": "ts",
        "javascript": "js",
        "python": "py",
    }.get(script_language, "ts")

    # 生成 MinIO 对象名称
    object_name = f"ios-tests/{project_identifier}/apps/{app_bundle_id}/test-scripts/{script_name}.{extension}"

    # 上传到 MinIO
    script_bytes = script_content.encode('utf-8')
    MinIOClient.upload_bytes(
        object_name=object_name,
        data=script_bytes,
        content_type="text/plain"
    )

    async with async_session_factory() as session:
        # 检查是否已存在相同的附件
        existing_stmt = select(Attachment).where(
            Attachment.object_name == object_name
        )
        existing_result = await session.execute(existing_stmt)
        existing_attachment = existing_result.scalar_one_or_none()

        description = f"iOS 应用 {app_bundle_id} 的测试脚本 ({script_name}.{extension})"

        if existing_attachment:
            # 更新现有附件
            existing_attachment.file_size = len(script_bytes)
            existing_attachment.description = description
            existing_attachment.updated_at = datetime.now()
            attachment = existing_attachment
        else:
            # 创建新附件记录
            attachment = Attachment(
                entity_type=AttachmentEntityType.IOS_TEST_SCRIPT,
                entity_id=entity_id,
                project_id=UUID("00000000-0000-0000-0000-000000000001"),
                file_name=f"{script_name}.{extension}",
                file_size=len(script_bytes),
                content_type="text/plain",
                object_name=object_name,
                description=description,
                created_by="ios-agent"
            )
            session.add(attachment)

        await session.commit()
        await session.refresh(attachment)

        return {
            "success": True,
            "attachment_id": str(attachment.id),
            "file_path": object_name,
            "language": script_language,
            "message": f"测试脚本已保存: {script_name}.{extension}"
        }


@tool
async def save_ios_test_report(
    app_bundle_id: str,
    report_content: str,
    report_format: str = "markdown",
    project_identifier: str = ""
) -> dict:
    """
    保存 iOS 测试报告到 MinIO

    Args:
        app_bundle_id: 被测应用 Bundle ID，如 "com.example.app"
        report_content: 报告内容（Markdown/HTML 格式）
        report_format: 报告格式（markdown, html），默认为 markdown
        project_identifier: 项目标识符

    Returns:
        dict: 包含 attachment_id 和 file_path 的字典
    """
    entity_id = uuid4()

    # 确定内容类型和扩展名
    if report_format == "html":
        content_type = "text/html"
        file_extension = "html"
    else:
        content_type = "text/markdown"
        file_extension = "md"

    report_bytes = report_content.encode('utf-8')

    # 生成 MinIO 对象名称
    object_name = f"ios-tests/{project_identifier}/apps/{app_bundle_id}/test-report.{file_extension}"

    # 上传到 MinIO
    MinIOClient.upload_bytes(
        object_name=object_name,
        data=report_bytes,
        content_type=content_type
    )

    async with async_session_factory() as session:
        # 检查是否已存在相同的附件
        existing_stmt = select(Attachment).where(
            Attachment.object_name == object_name
        )
        existing_result = await session.execute(existing_stmt)
        existing_attachment = existing_result.scalar_one_or_none()

        file_name = f"test-report-{app_bundle_id}.{file_extension}"
        format_desc = "HTML" if report_format == "html" else "Markdown"
        description = f"iOS 应用 {app_bundle_id} 的测试报告 ({format_desc})"

        if existing_attachment:
            # 更新现有附件
            existing_attachment.file_size = len(report_bytes)
            existing_attachment.content_type = content_type
            existing_attachment.file_name = file_name
            existing_attachment.description = description
            existing_attachment.updated_at = datetime.now()
            attachment = existing_attachment
        else:
            # 创建新附件记录
            attachment = Attachment(
                entity_type=AttachmentEntityType.IOS_TEST_REPORT,
                entity_id=entity_id,
                project_id=UUID("00000000-0000-0000-0000-000000000001"),
                file_name=file_name,
                file_size=len(report_bytes),
                content_type=content_type,
                object_name=object_name,
                description=description,
                created_by="ios-agent"
            )
            session.add(attachment)

        await session.commit()
        await session.refresh(attachment)

        return {
            "success": True,
            "attachment_id": str(attachment.id),
            "file_path": object_name,
            "format": report_format,
            "message": f"测试报告已保存 ({format_desc})"
        }


@tool
async def get_ios_artifacts(
    app_bundle_id: str,
    artifact_type: Optional[str] = None,
    project_identifier: str = "",
    sub_function_id: str = "",
) -> dict:
    """
    获取 iOS 应用的测试成果物列表

    Args:
        app_bundle_id: 被测应用 Bundle ID
        artifact_type: 成果物类型过滤（可选）:
            - IOS_TEST_PLAN: 测试计划
            - IOS_TEST_CASE: 测试用例
            - IOS_TEST_SCRIPT: 测试脚本
            - IOS_TEST_REPORT: 测试报告
            - IOS_DEVICE_INFO: 设备信息
            - IOS_SCREENSHOT: 截图
        project_identifier: 项目标识符
        sub_function_id: 可选，子功能 ID，用于查询子功能关联的成果物

    Returns:
        dict: 成果物列表，包含类型、文件名、描述、创建时间等信息
    """
    async with async_session_factory() as session:
        # 构建查询
        if sub_function_id:
            # 通过 entity_id 查询子功能关联的成果物
            try:
                sub_func_uuid = UUID(sub_function_id)
                stmt = select(Attachment).where(
                    Attachment.entity_id == sub_func_uuid
                )
            except ValueError:
                return {"error": f"Invalid sub_function_id: {sub_function_id}"}
        else:
            # 通过 object_name 前缀匹配
            prefix = f"ios-tests/{project_identifier}/apps/{app_bundle_id}/"
            stmt = select(Attachment).where(
                Attachment.object_name.like(f"{prefix}%")
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
            "app_bundle_id": app_bundle_id,
            "project_identifier": project_identifier,
            "artifacts": artifacts,
            "total": len(artifacts)
        }


@tool
async def get_ios_artifact_content(
    attachment_id: str
) -> dict:
    """
    获取 iOS 测试成果物内容

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
