"""
API 端点管理路由

提供 OpenAPI 文档解析、端点查询、文件夹结构管理等功能
"""

import json
import io
import httpx
import re
from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from app.api.deps import CurrentUserIdDep, DbSessionDep
from app.models.api_endpoint import APIEndpoint
from app.models.folder import Folder
from app.models.folder_type import FolderType
from app.models.project import Project
from app.models.api_test import APITest, APITestRun
from app.schemas.api_endpoint import (
    APIEndpointResponse,
    APIEndpointCreate,
    APIEndpointUpdate,
    OpenAPIParseResult,
    OpenAPIUploadRequest
)
from app.services.openapi_parser import OpenAPIParser

router = APIRouter()

async def fetch_openapi_from_url(url: str) -> dict[str, Any]:
    """
    从远程 URL 获取 OpenAPI 文档

    Args:
        url: OpenAPI/Swagger 文档的 URL

    Returns:
        解析后的 JSON 字典
    """
    try:
        async with httpx.AsyncClient(timeout=30.0, follow_redirects=True) as client:
            response = await client.get(url)
            response.raise_for_status()
    except httpx.HTTPStatusError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"无法从 URL 获取文档: {e.response.status_code} {e.response.reason_phrase}"
        )
    except httpx.RequestError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"请求远程地址失败: {type(e).__name__}: {e}"
        )

    text = response.text
    content_type = response.headers.get("content-type", "")

    # 不依赖 content-type，直接尝试 JSON 解析；失败再尝试 YAML（若已安装 PyYAML）
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    try:
        import yaml  # 可选依赖：装了就支持 YAML，没装就跳过
        loaded = yaml.safe_load(text)
        if isinstance(loaded, dict):
            return loaded
    except ImportError:
        pass
    except Exception:
        pass

    preview = text[:200].replace("\n", " ").replace("\r", " ")
    hint = ""
    lower = text.lstrip()[:64].lower()
    if "swagger-ui" in url.lower() or lower.startswith("<!doctype") or lower.startswith("<html"):
        hint = (
            " 看起来该地址返回的是 Swagger UI 页面（HTML），请改用 OpenAPI JSON 地址，"
            "例如把 /swagger-ui.html 替换为 /v3/api-docs 或 /v2/api-docs。"
        )
    raise HTTPException(
        status_code=status.HTTP_400_BAD_REQUEST,
        detail=(
            f"远程地址返回的不是 OpenAPI JSON/YAML 文档（content-type={content_type!r}）。"
            f"前 200 字符: {preview!r}。{hint}"
        )
    )

@router.post("/upload-openapi", response_model=OpenAPIParseResult)
async def upload_openapi_schema(
    request: OpenAPIUploadRequest,
    current_user_id: CurrentUserIdDep,
    db: DbSessionDep
):
    """
    上传并解析 OpenAPI Schema 文件

    支持两种方式：
    1. 上传 JSON 文件内容
    2. 提供 OpenAPI 文档的 URL（自动获取）

    自动解析并创建对应的文件夹结构：
    - 按标签分组创建父文件夹（如 "Activities"）
    - 为每个端点创建子文件夹（如 "GET /api/v1/Activities"）
    - 提取完整的接口信息存储到数据库
    """
    # 1. 查询项目
    project_stmt = select(Project).where(
        Project.identifier == request.project_identifier
    )
    project_result = await db.execute(project_stmt)
    project = project_result.scalar_one_or_none()
# pylint: disable  MC80OmFIVnBZMlhscm9ua3VMazZTalpUTUE9PTplYzdhM2U3NA==

    if not project:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"项目 {request.project_identifier} 不存在"
        )

    # 2. 转换 parent_folder_id
    parent_id = request.parent_folder_id if request.parent_folder_id else None

    # 3. 获取 OpenAPI 内容
    openapi_spec = request.file_content

    # 如果提供的是 URL，则从远程获取
    if isinstance(openapi_spec, dict) and "url" in openapi_spec:
        url = openapi_spec["url"]
        try:
            openapi_spec = await fetch_openapi_from_url(url)
        except HTTPException:
            raise

    # 4. 验证是否为有效的 OpenAPI 文档
    if not isinstance(openapi_spec, dict):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="文件内容必须是有效的 JSON 对象"
        )

    # 检查必需字段
    if "paths" not in openapi_spec:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="OpenAPI 文档必须包含 'paths' 字段"
        )

    # 5. 创建解析器并解析
    parser = OpenAPIParser(db)

    try:
        result = await parser.parse_and_create_structure(
            project_id=project.id,
            parent_folder_id=parent_id,
            schema_file_id=None,  # 暂时不上传文件
            openapi_spec=openapi_spec,
            user_id=current_user_id
        )
        await db.commit()

        return OpenAPIParseResult(**result)

    except Exception as e:
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"解析 OpenAPI 文件失败: {str(e)}"
        )

@router.get("/projects/{project_identifier}/api-endpoints", response_model=list[APIEndpointResponse])
async def list_api_endpoints(
    project_identifier: str,
    current_user_id: CurrentUserIdDep,
    db: DbSessionDep,
    folder_id: UUID | None = None,
    tag_group: str | None = None
):
    """
    查询项目的 API 端点列表

    支持按文件夹或标签分组过滤
    """
    # 查询项目
    project_stmt = select(Project).where(
        Project.identifier == project_identifier
    )
    project_result = await db.execute(project_stmt)
    project = project_result.scalar_one_or_none()

    if not project:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"项目 {project_identifier} 不存在"
        )

    # 构建查询
    endpoint_stmt = select(APIEndpoint).where(
        APIEndpoint.project_id == project.id
    )

    if folder_id:
        # 使用递归 CTE 获取该文件夹及其所有子文件夹的 ID
        folder_cte = select(Folder.id).where(
            Folder.id == folder_id
        ).cte(recursive=True)

        folder_cte = folder_cte.union_all(
            select(Folder.id).where(
                Folder.parent_id == folder_cte.c.id
            )
        )

        endpoint_stmt = endpoint_stmt.where(
            APIEndpoint.folder_id.in_(select(folder_cte.c.id))
        )
# pylint: disable  MS80OmFIVnBZMlhscm9ua3VMazZTalpUTUE9PTplYzdhM2U3NA==

    if tag_group:
        endpoint_stmt = endpoint_stmt.where(APIEndpoint.tag_group == tag_group)

    endpoint_stmt = endpoint_stmt.order_by(
        APIEndpoint.tag_group,
        APIEndpoint.sort_order,
        APIEndpoint.path
    )

    # 执行查询
    endpoint_result = await db.execute(endpoint_stmt)
    endpoints = endpoint_result.scalars().all()

    return endpoints

@router.get("/api-endpoints/{endpoint_id}", response_model=APIEndpointResponse)
async def get_api_endpoint(
    endpoint_id: UUID,
    current_user_id: CurrentUserIdDep,
    db: DbSessionDep
):
    """获取 API 端点的详细信息"""
    endpoint_stmt = select(APIEndpoint).where(APIEndpoint.id == endpoint_id)
    endpoint_result = await db.execute(endpoint_stmt)
    endpoint = endpoint_result.scalar_one_or_none()

    if not endpoint:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"端点 {endpoint_id} 不存在"
        )

    return endpoint
# pragma: no cover  Mi80OmFIVnBZMlhscm9ua3VMazZTalpUTUE9PTplYzdhM2U3NA==

@router.post("/api-endpoints", response_model=APIEndpointResponse)
async def create_api_endpoint(
    create_data: dict,
    current_user_id: CurrentUserIdDep,
    db: DbSessionDep
):
    """手工创建 API 端点"""
    # 获取项目
    project_identifier = create_data.get("project_identifier")
    project_stmt = select(Project).where(
        Project.identifier == project_identifier
    )
    project_result = await db.execute(project_stmt)
    project = project_result.scalar_one_or_none()

    if not project:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"项目 {project_identifier} 不存在"
        )

    # 创建端点
    endpoint = APIEndpoint(
        project_id=project.id,
        folder_id=create_data.get("folder_id"),
        display_name=create_data.get("display_name"),
        path=create_data.get("path"),
        method=create_data.get("method"),
        summary=create_data.get("summary"),
        description=create_data.get("description"),
        tag_group=create_data.get("tag_group"),
        parameters=create_data.get("parameters"),
        request_body=create_data.get("request_body"),
        responses=create_data.get("responses"),
        sort_order=0,
        total_test_cases=0,
        total_test_runs=0,
        last_run_status=None,
        api_test_ids=[],
    )

    db.add(endpoint)
    await db.commit()
    await db.refresh(endpoint)

    return endpoint

@router.get("/projects/{project_identifier}/folder-structure")
async def get_api_folder_structure(
    project_identifier: str,
    current_user_id: CurrentUserIdDep,
    db: DbSessionDep
):
    """
    获取项目的 API 文件夹结构

    返回树形结构的文件夹列表，包含端点统计信息
    """
    # 查询项目
    project_stmt = select(Project).where(
        Project.identifier == project_identifier
    )
    project_result = await db.execute(project_stmt)
    project = project_result.scalar_one_or_none()

    if not project:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"项目 {project_identifier} 不存在"
        )

    # 查询根文件夹
    folder_stmt = select(Folder).where(
        Folder.project_id == project.id,
        Folder.folder_type == FolderType.API_TEST,
        Folder.parent_id.is_(None)
    ).order_by(Folder.name)

    folder_result = await db.execute(folder_stmt)
    root_folders = folder_result.scalars().all()

    # 递归构建文件夹树
    async def build_folder_tree(folder: Folder) -> dict[str, Any]:
        # 查询该文件夹下的端点数量
        endpoint_count_stmt = select(APIEndpoint).where(
            APIEndpoint.folder_id == folder.id
        )
        endpoint_count_result = await db.execute(endpoint_count_stmt)
        endpoint_count = len(endpoint_count_result.scalars().all())

        return {
            "id": str(folder.id),
            "name": folder.name,
            "description": folder.description,
            "folder_type": folder.folder_type.value,
            "endpoint_count": endpoint_count,
            "parent_id": str(folder.parent_id) if folder.parent_id else None,
            "children": [await build_folder_tree(child) for child in folder.children]
        }

    folder_tree = []
    for folder in root_folders:
        folder_tree.append(await build_folder_tree(folder))

    return {
        "project_identifier": project_identifier,
        "folder_type": "api_test",
        "folder_tree": folder_tree
    }

@router.patch("/api-endpoints/{endpoint_id}", response_model=APIEndpointResponse)
async def update_api_endpoint(
    endpoint_id: UUID,
    update_data: APIEndpointUpdate,
    current_user_id: CurrentUserIdDep,
    db: DbSessionDep
):
    """更新 API 端点信息"""
    endpoint_stmt = select(APIEndpoint).where(APIEndpoint.id == endpoint_id)
    endpoint_result = await db.execute(endpoint_stmt)
    endpoint = endpoint_result.scalar_one_or_none()

    if not endpoint:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"端点 {endpoint_id} 不存在"
        )

    # 更新字段
    update_dict = update_data.model_dump(exclude_unset=True)
    for field, value in update_dict.items():
        setattr(endpoint, field, value)

    await db.commit()
    await db.refresh(endpoint)

    return endpoint

@router.delete("/api-endpoints/{endpoint_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_api_endpoint(
    endpoint_id: UUID,
    current_user_id: CurrentUserIdDep,
    db: DbSessionDep
):
    """删除 API 端点"""
    endpoint_stmt = select(APIEndpoint).where(APIEndpoint.id == endpoint_id)
    endpoint_result = await db.execute(endpoint_stmt)
    endpoint = endpoint_result.scalar_one_or_none()

    if not endpoint:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"端点 {endpoint_id} 不存在"
        )

    await db.delete(endpoint)
    await db.commit()

    return None
# noqa  My80OmFIVnBZMlhscm9ua3VMazZTalpUTUE9PTplYzdhM2U3NA==

@router.get("/api-endpoints/{endpoint_id}/test-scripts")
async def get_endpoint_test_scripts(
    endpoint_id: UUID,
    current_user_id: CurrentUserIdDep,
    db: DbSessionDep
):
    """
    获取 API 端点关联的测试脚本列表
    """
    # 查询端点
    endpoint_stmt = select(APIEndpoint).where(APIEndpoint.id == endpoint_id)
    endpoint_result = await db.execute(endpoint_stmt)
    endpoint = endpoint_result.scalar_one_or_none()

    if not endpoint:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"端点 {endpoint_id} 不存在"
        )

    # 获取关联的测试脚本
    api_test_ids = endpoint.api_test_ids or []
    if not api_test_ids:
        return {
            "endpoint_id": str(endpoint_id),
            "test_scripts": []
        }

    # 查询测试脚本详情
    test_scripts_stmt = select(APITest).where(
        APITest.id.in_(api_test_ids)
    )
    test_scripts_result = await db.execute(test_scripts_stmt)
    test_scripts = test_scripts_result.scalars().all()

    return {
        "endpoint_id": str(endpoint_id),
        "test_scripts": [
            {
                "id": str(script.id),
                "name": script.name,
                "identifier": script.identifier,
                "script_format": script.script_format,
                "script_language": script.script_language,
                "total_endpoints": script.total_endpoints,
                "total_scenarios": script.total_scenarios,
                "created_at": script.created_at.isoformat() if script.created_at else None,
                "updated_at": script.updated_at.isoformat() if script.updated_at else None,
            }
            for script in test_scripts
        ]
    }

@router.get("/api-endpoints/{endpoint_id}/test-runs")
async def get_endpoint_test_runs(
    endpoint_id: UUID,
    current_user_id: CurrentUserIdDep,
    db: DbSessionDep,
    limit: int = 10
):
    """
    获取 API 端点的测试执行报告

    返回最近的测试运行记录
    """
    # 查询端点
    endpoint_stmt = select(APIEndpoint).where(APIEndpoint.id == endpoint_id)
    endpoint_result = await db.execute(endpoint_stmt)
    endpoint = endpoint_result.scalar_one_or_none()

    if not endpoint:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"端点 {endpoint_id} 不存在"
        )

    # 获取关联的测试脚本
    api_test_ids = endpoint.api_test_ids or []
    if not api_test_ids:
        return {
            "endpoint_id": str(endpoint_id),
            "test_runs": [],
            "total_runs": 0,
            "last_run_status": endpoint.last_run_status
        }

    # 查询测试运行记录（按时间倒序）
    test_runs_stmt = select(APITestRun).where(
        APITestRun.api_test_id.in_(api_test_ids)
    ).order_by(APITestRun.created_at.desc()).limit(limit)

    test_runs_result = await db.execute(test_runs_stmt)
    test_runs = test_runs_result.scalars().all()

    # 统计总运行次数
    count_stmt = select(APITestRun).where(
        APITestRun.api_test_id.in_(api_test_ids)
    )
    count_result = await db.execute(count_stmt)
    total_runs = len(count_result.scalars().all())

    return {
        "endpoint_id": str(endpoint_id),
        "test_runs": [
            {
                "id": str(run.id),
                "status": run.status,
                "total_scenarios": run.total_tests,
                "passed_scenarios": run.passed_tests,
                "failed_scenarios": run.failed_tests,
                "skipped_scenarios": run.skipped_tests,
                "duration": (run.duration_ms / 1000) if run.duration_ms else None,
                "created_at": run.created_at.isoformat() if run.created_at else None,
            }
            for run in test_runs
        ],
        "total_runs": total_runs,
        "last_run_status": endpoint.last_run_status
    }

@router.get("/api-endpoints/{endpoint_id}/artifacts")
async def get_endpoint_artifacts_api(
    endpoint_id: UUID,
    current_user_id: CurrentUserIdDep,
    db: DbSessionDep,
    artifact_type: str | None = None
):
    """
    获取 API 端点的测试成果物列表
    """
    try:
        # 查询端点
        endpoint_stmt = select(APIEndpoint).where(APIEndpoint.id == endpoint_id)
        endpoint_result = await db.execute(endpoint_stmt)
        endpoint = endpoint_result.scalar_one_or_none()

        if not endpoint:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"端点 {endpoint_id} 不存在"
            )

        # 导入 Attachment 模型
        from app.models.attachment import Attachment, AttachmentEntityType

        # 只查询 API 测试相关的成果物类型
        api_test_artifact_types = [
            AttachmentEntityType.API_TEST_PLAN,
            AttachmentEntityType.API_TEST_CASE,
            AttachmentEntityType.API_TEST_SCRIPT,
            AttachmentEntityType.API_TEST_RESULT,
            AttachmentEntityType.API_TEST_REPORT,
        ]

        # 构建查询 - 只查询 API 测试成果物
        stmt = select(Attachment).where(
            Attachment.entity_id == endpoint_id,
            # Existing PostgreSQL rows store SQLAlchemy enum member names.
            # Use those names explicitly so newly added artifact types are
            # filtered consistently with the historical rows.
            Attachment.entity_type.in_([item.name for item in api_test_artifact_types])
        )

        # 按类型过滤（可选）
        if artifact_type:
            try:
                entity_type = AttachmentEntityType[artifact_type]
                stmt = stmt.where(Attachment.entity_type == entity_type)
            except KeyError:
                pass

        # 执行查询
        result = await db.execute(stmt)
        attachments = result.scalars().all()

        print(f"[API Endpoints] Found {len(attachments)} artifacts for endpoint {endpoint_id}")

        # 格式化返回
        artifacts = []
        for attachment in attachments:
            artifact_data = {
                "id": str(attachment.id),
                "type": attachment.entity_type.value.upper(),
                "file_name": attachment.file_name,
                "description": attachment.description,
                "file_size": attachment.file_size,
                "content_type": attachment.content_type,
                "object_name": attachment.object_name,
                "created_at": attachment.created_at.isoformat() if attachment.created_at else None,
            }
            print(f"[API Endpoints] Artifact: {artifact_data['type']} - {artifact_data['file_name']}")
            artifacts.append(artifact_data)

        print(f"[API Endpoints] Returning {len(artifacts)} artifacts")

        return {
            "success": True,
            "endpoint_id": str(endpoint_id),
            "artifacts": artifacts,
            "total": len(artifacts)
        }

    except HTTPException:
        raise
    except Exception as e:
        import traceback
        import logging
        logger = logging.getLogger(__name__)
        logger.error(f"Error fetching artifacts for endpoint {endpoint_id}: {str(e)}")
        logger.error(f"Traceback: {traceback.format_exc()}")

        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"获取成果物失败: {str(e)}"
        )

@router.get("/attachments/{attachment_id}/content")
async def get_attachment_content_api(
    attachment_id: UUID,
    current_user_id: CurrentUserIdDep,
    db: DbSessionDep
):
    """
    获取附件内容（文本文件）
    """
    from app.models.attachment import Attachment
    from app.config.minio_client import MinIOClient

    # 查询附件
    stmt = select(Attachment).where(Attachment.id == attachment_id)
    result = await db.execute(stmt)
    attachment = result.scalar_one_or_none()

    if not attachment:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"附件 {attachment_id} 不存在"
        )

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
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"下载文件失败: {str(e)}"
        )

@router.get("/attachments/{attachment_id}/download")
async def download_attachment_api(
    attachment_id: UUID,
    current_user_id: CurrentUserIdDep,
    db: DbSessionDep
):
    """
    下载附件文件
    """
    from fastapi.responses import StreamingResponse
    from app.models.attachment import Attachment
    from app.config.minio_client import MinIOClient
    from app.utils.http_headers import build_content_disposition
    import io

    # 查询附件
    stmt = select(Attachment).where(Attachment.id == attachment_id)
    result = await db.execute(stmt)
    attachment = result.scalar_one_or_none()

    if not attachment:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"附件 {attachment_id} 不存在"
        )

    # 从 MinIO 下载文件
    try:
        content_bytes = MinIOClient.download_file(attachment.object_name)

        return StreamingResponse(
            io.BytesIO(content_bytes),
            media_type=attachment.content_type or "application/octet-stream",
            headers={
                "Content-Disposition": build_content_disposition(attachment.file_name),
                "Content-Length": str(len(content_bytes)),
            }
        )
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"下载文件失败: {str(e)}"
        )

@router.get("/attachments/{attachment_id}/report-viewer")
async def get_report_viewer_url(
    attachment_id: UUID,
    current_user_id: CurrentUserIdDep,
    db: DbSessionDep
):
    """
    获取测试报告查看器 URL

    对于 HTML 格式的测试报告，直接返回 HTML 内容 URL。
    对于 ZIP 格式的测试报告，解压并返回 index.html 的访问路径。
    """
    from app.models.attachment import Attachment, AttachmentEntityType
    from app.config.minio_client import MinIOClient
    import zipfile
    import io
    import tempfile
    from pathlib import Path

    # 查询附件
    stmt = select(Attachment).where(Attachment.id == attachment_id)
    result = await db.execute(stmt)
    attachment = result.scalar_one_or_none()

    if not attachment:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"附件 {attachment_id} 不存在"
        )

    # 只处理测试报告类型（支持 API 和 Web 测试报告）
    if attachment.entity_type not in [AttachmentEntityType.API_TEST_REPORT, AttachmentEntityType.WEB_TEST_REPORT]:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="只支持查看测试报告"
        )

    # 如果是 HTML 文件，直接返回内容 URL
    if attachment.content_type == "text/html" or attachment.file_name.endswith(".html"):
        return {
            "success": True,
            "attachment_id": str(attachment_id),
            "report_type": "html",
            "index_url": f"/api/v2/attachments/{attachment_id}/report-html"
        }

    # 对于 ZIP 格式的测试报告，解压并返回 index.html 的访问路径
    try:
        zip_bytes = MinIOClient.download_file(attachment.object_name)

        # 创建临时目录
        temp_dir = Path(tempfile.gettempdir()) / "test-reports" / str(attachment_id)
        temp_dir.mkdir(parents=True, exist_ok=True)

        # 解压 ZIP 文件
        with zipfile.ZipFile(io.BytesIO(zip_bytes), 'r') as zip_ref:
            zip_ref.extractall(temp_dir)

        # 查找 index.html
        index_html = temp_dir / "index.html"
        if not index_html.exists():
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="报告中未找到 index.html"
            )

        # 返回临时目录路径和附件 ID
        return {
            "success": True,
            "attachment_id": str(attachment_id),
            "report_type": "zip",
            "report_path": str(temp_dir),
            "index_url": f"/api/v2/attachments/{attachment_id}/report-files/index.html"
        }
    except zipfile.BadZipFile:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="无效的 ZIP 文件"
        )
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"处理报告失败: {str(e)}"
        )


def _render_original_content(content: str, file_name: str) -> str:
    """rev36：将非 HTML/无法解析的报告内容以原文形式包装为可读 HTML。

    Markdown 解析失败或解析结果为空（格式不匹配）时保留原文展示，
    不能渲染成空白通用页。
    """
    escaped = content.replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')
    formatted = escaped.replace('\n', '<br>\n')
    return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>{file_name} - 测试报告（原文）</title>
<style>
body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, 'Helvetica Neue', Arial, sans-serif; background: #f5f7fa; color: #333; line-height: 1.6; padding: 24px; }}
.container {{ max-width: 1200px; margin: 0 auto; background: #fff; border-radius: 12px; padding: 32px; box-shadow: 0 2px 12px rgba(0,0,0,0.06); }}
h1 {{ font-size: 24px; margin-bottom: 16px; color: #1a1a2e; }}
.badge {{ display: inline-block; padding: 6px 16px; border-radius: 16px; font-size: 12px; font-weight: 600; background: #faad14; color: #fff; margin-bottom: 16px; }}
.content {{ font-family: 'SF Mono', Monaco, 'Cascadia Code', monospace; font-size: 13px; line-height: 1.8; color: #555; background: #f8f9fa; padding: 20px; border-radius: 8px; border: 1px solid #e8e8e8; overflow-x: auto; }}
.footer {{ margin-top: 24px; text-align: center; color: #aaa; font-size: 13px; }}
</style>
</head>
<body>
<div class="container">
  <h1>📄 {file_name}</h1>
  <span class="badge">原始内容（非 HTML / 无法解析的报告格式）</span>
  <div class="content">{formatted}</div>
  <div class="footer">AI Test Agent System Platform</div>
</div>
</body>
</html>"""


@router.get("/attachments/{attachment_id}/report-html")
async def get_report_html(
    attachment_id: UUID,
    current_user_id: CurrentUserIdDep,
    db: DbSessionDep
):
    """
    直接获取 HTML 测试报告内容

    用于 content_type 为 text/html 的测试报告附件，直接返回 HTML 内容。
    对于大文件（>5MB），返回 MinIO 预签名 URL 让浏览器直接下载，避免服务器超时。
    """
    from fastapi.responses import HTMLResponse, RedirectResponse
    from app.models.attachment import Attachment, AttachmentEntityType
    from app.config.minio_client import MinIOClient

    # 查询附件
    stmt = select(Attachment).where(Attachment.id == attachment_id)
    result = await db.execute(stmt)
    attachment = result.scalar_one_or_none()

    if not attachment:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"附件 {attachment_id} 不存在"
        )

    # 只处理测试报告类型
    if attachment.entity_type not in [AttachmentEntityType.API_TEST_REPORT, AttachmentEntityType.WEB_TEST_REPORT]:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="只支持查看测试报告"
        )

    # rev36：ZIP 报告附件 → 重定向到 report-files 的 index.html（入口页）
    if attachment.content_type and "zip" in attachment.content_type.lower():
        print(f"[Report HTML] 附件 {attachment_id} 为 ZIP 报告，重定向到 report-files/index.html")
        return RedirectResponse(
            url=f"/api/v2/attachments/{attachment_id}/report-files/index.html"
        )

    # 大文件阈值：5MB
    LARGE_FILE_THRESHOLD = 5 * 1024 * 1024

    # 对于大文件，使用 MinIO 预签名 URL 直接重定向，避免服务器超时
    if attachment.file_size and attachment.file_size > LARGE_FILE_THRESHOLD:
        try:
            presigned_url = MinIOClient.get_presigned_url(attachment.object_name)
            return RedirectResponse(url=presigned_url)
        except Exception as e:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"获取报告下载链接失败: {str(e)}"
            )

    # 小文件：从 MinIO 下载并返回 HTML 内容
    try:
        html_bytes = MinIOClient.download_file(attachment.object_name)
        html_content = html_bytes.decode('utf-8')

        # 兜底：如果内容不是有效的 HTML（可能是 Markdown），实时转换
        content_stripped = html_content.strip()
        is_valid_html = (
            content_stripped.startswith('<!DOCTYPE') or
            content_stripped.startswith('<!doctype') or
            content_stripped.startswith('<html') or
            content_stripped.startswith('<HTML')
        )

        if not is_valid_html:
            print(f"[Report HTML] 附件 {attachment_id} 内容不是 HTML，尝试作为 Markdown 转换")
            # 尝试使用 MarkdownReportConverter 转换
            try:
                from app.utils.markdown_report_converter import MarkdownReportConverter
                converter = MarkdownReportConverter()
                data = converter.parse_markdown_report(html_content)
                # rev36：解析结果为空（格式不匹配，无模块/无统计）→ 保留原文展示，
                # 不能渲染成空白通用页
                _has_module = bool(data.get("modules"))
                _has_stats = bool((data.get("stats") or {}).get("total"))
                if not _has_module and not _has_stats:
                    print(f"[Report HTML] Markdown 解析结果为空（格式不匹配），保留原文展示")
                    html_content = _render_original_content(html_content, attachment.file_name)
                    return HTMLResponse(content=html_content)
                # 使用附件文件名或报告中的模块名
                module_name = attachment.file_name.replace('.html', '').replace('.md', '')
                # 尝试从文件名提取功能名（格式: 功能名-测试报告-...）
                name_match = re.match(r'^(.+?)-测试报告', module_name)
                if name_match:
                    module_name = name_match.group(1)
                # 查找匹配的模块数据
                module_data = None
                for mod in data.get("modules", []):
                    if mod["name"] == module_name or module_name in mod["name"] or mod["name"] in module_name:
                        module_data = mod
                        break
                # 如果没找到匹配模块，使用第一个模块或整体数据
                if not module_data and data.get("modules"):
                    module_data = data["modules"][0]
                html_content = converter.generate_html_report(
                    data,
                    module_name=module_name,
                    module_data=module_data
                )
                print(f"[Report HTML] Markdown 转换成功，已生成 HTML 报告")
            except Exception as convert_error:
                # 转换失败，返回简单的 HTML 包装（避免显示纯文本）
                print(f"[Report HTML] Markdown 转换失败: {convert_error}")
                html_content = _render_original_content(html_content, attachment.file_name)

        return HTMLResponse(content=html_content)
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"读取报告失败: {str(e)}"
        )

@router.get("/attachments/{attachment_id}/report-files/{file_path:path}")
async def get_report_file(
    attachment_id: UUID,
    file_path: str,
    current_user_id: CurrentUserIdDep,
    db: DbSessionDep
):
    """
    获取测试报告中的文件

    从解压后的临时目录中读取文件并返回
    """
    from fastapi.responses import FileResponse, HTMLResponse
    from pathlib import Path
    import tempfile
    import mimetypes
    import zipfile as _zipfile

    # 构建文件路径
    temp_dir = Path(tempfile.gettempdir()) / "test-reports" / str(attachment_id)
    target_file = temp_dir / file_path

    # rev36：临时目录无该文件时，若附件为 ZIP 报告，从 MinIO 下载并解压
    if not target_file.exists():
        from app.models.attachment import Attachment, AttachmentEntityType
        from app.config.minio_client import MinIOClient

        att_stmt = select(Attachment).where(Attachment.id == attachment_id)
        attachment = (await db.execute(att_stmt)).scalar_one_or_none()
        if attachment and attachment.object_name and attachment.content_type \
                and "zip" in attachment.content_type.lower():
            try:
                zdata = MinIOClient.download_file(attachment.object_name)
                temp_dir.mkdir(parents=True, exist_ok=True)
                with _zipfile.ZipFile(io.BytesIO(zdata)) as zf:
                    # 防 zip 路径穿越：仅解压到 temp_dir 内
                    for member in zf.namelist():
                        dest = (temp_dir / member).resolve()
                        if not str(dest).startswith(str(temp_dir.resolve())):
                            continue
                        if member.endswith("/"):
                            dest.mkdir(parents=True, exist_ok=True)
                        else:
                            dest.parent.mkdir(parents=True, exist_ok=True)
                            dest.write_bytes(zf.read(member))
                print(f"[Report Files] 附件 {attachment_id} ZIP 已解压到临时目录")
            except Exception as e:
                print(f"[Report Files] ZIP 解压失败: {e}")
        else:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"附件 {attachment_id} 无可用报告文件（非 ZIP 或对象不存在）",
            )

    # 安全检查：确保文件在临时目录内
    try:
        target_file = target_file.resolve()
        temp_dir = temp_dir.resolve()
        if not str(target_file).startswith(str(temp_dir)):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="访问被拒绝"
            )
    except Exception:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="访问被拒绝"
        )

    # 检查文件是否存在
    if not target_file.exists() or not target_file.is_file():
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"文件不存在: {file_path}"
        )

    # 确定 MIME 类型
    mime_type, _ = mimetypes.guess_type(str(target_file))
    if mime_type is None:
        mime_type = "application/octet-stream"

    # 对于 HTML 文件，读取内容并使用 HTMLResponse 返回，避免浏览器下载
    if mime_type == "text/html":
        with open(target_file, 'r', encoding='utf-8') as f:
            html_content = f.read()
        return HTMLResponse(content=html_content)

    # 对于其他文件，使用 FileResponse 但不设置 filename，让浏览器根据 MIME 类型处理
    return FileResponse(
        path=str(target_file),
        media_type=mime_type
    )

@router.put("/attachments/{attachment_id}/content")
async def update_attachment_content_api(
    attachment_id: UUID,
    content_data: dict,
    current_user_id: CurrentUserIdDep,
    db: DbSessionDep
):
    """
    更新附件内容
    """
    from app.models.attachment import Attachment
    from app.config.minio_client import MinIOClient

    # 查询附件
    stmt = select(Attachment).where(Attachment.id == attachment_id)
    result = await db.execute(stmt)
    attachment = result.scalar_one_or_none()

    if not attachment:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"附件 {attachment_id} 不存在"
        )

    try:
        content = content_data.get("content", "")
        content_bytes = content.encode('utf-8')

        # 上传到 MinIO（覆盖原有文件）
        MinIOClient.upload_bytes(
            object_name=attachment.object_name,
            data=content_bytes,
            content_type=attachment.content_type
        )

        # 更新文件大小
        attachment.file_size = len(content_bytes)
        await db.commit()

        return {
            "success": True,
            "message": "附件内容已更新",
            "attachment_id": str(attachment.id),
            "file_size": len(content_bytes)
        }
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"更新附件失败: {str(e)}"
        )
