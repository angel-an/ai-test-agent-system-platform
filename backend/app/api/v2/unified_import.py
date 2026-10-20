"""
统一 API 文档导入路由

将两条导入链路（OpenAPI 原生 / 文本文档）统一为单一入口，
返回一致的 UnifiedImportResult 结构。

来源类型：
- swagger_online: Swagger/OpenAPI 在线 URL
- swagger_offline: Swagger/OpenAPI 离线文件
- apifox_online: Apifox 在线 URL（标准 OpenAPI 格式）
- apifox_offline: Apifox 离线导出文件
- manual_text: 手工文本粘贴（TXT/curl 格式）
- manual_file: 手工文件上传（TXT/DOCX）
"""

from __future__ import annotations

import json
from typing import Any
from uuid import UUID

from fastapi import APIRouter, File, Form, HTTPException, UploadFile, status
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import CurrentUserIdDep, DbSessionDep
from app.models.project import Project
from app.schemas.api_endpoint import (
    EndpointSummary,
    ImportPreview,
    TagFolderSummary,
    UnifiedImportRequest,
    UnifiedImportResult,
)
from app.schemas.common import SuccessResponse
from app.services.api_doc_parser_service import APIDocParserService
from app.services.openapi_parser import OpenAPIParser

router = APIRouter(tags=["API 文档统一导入"])


# =============================================================================
# 辅助函数
# =============================================================================

async def _get_project(project_identifier: str, db: AsyncSession) -> Project:
    """根据标识符获取项目"""
    stmt = select(Project).where(Project.identifier == project_identifier)
    result = await db.execute(stmt)
    project = result.scalar_one_or_none()
    if not project:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"项目 {project_identifier} 不存在",
        )
    return project


def _detect_auth_type(security: list[dict] | None) -> tuple[bool, str | None]:
    """从 security 字段检测认证类型"""
    if not security:
        return False, None

    # OpenAPI 3.0 security: [{"bearerAuth": []}, {"basicAuth": []}]
    # 取第一个认证方案
    for sec in security:
        if not sec:
            continue
        for key in sec.keys():
            key_lower = key.lower()
            if "bearer" in key_lower:
                return True, "bearer"
            if "basic" in key_lower:
                return True, "basic"
            if "api" in key_lower and "key" in key_lower:
                return True, "api_key"
            if "oauth" in key_lower:
                return True, "oauth2"

    return True, "unknown"


def _has_path_params(path: str) -> bool:
    """检查路径是否包含路径参数 {xxx}"""
    return "{" in path and "}" in path


def _has_query_params(parameters: list[dict] | None) -> bool:
    """检查是否有 query 参数"""
    if not parameters:
        return False
    return any(p.get("in") == "query" for p in parameters)


def _extract_path_prefixes(paths: list[str]) -> list[str]:
    """提取路径前缀列表"""
    prefixes: set[str] = set()
    for path in paths:
        parts = path.strip("/").split("/")
        if len(parts) >= 2:
            # 取前两级作为前缀，如 /api/v1
            prefix = "/" + "/".join(parts[:2])
            prefixes.add(prefix)
        elif len(parts) == 1:
            prefixes.add("/" + parts[0])
    return sorted(prefixes)


def _build_preview_from_openapi(
    openapi_spec: dict[str, Any],
    endpoints_data: list[dict[str, Any]],
) -> ImportPreview:
    """从 OpenAPI spec 构建预览摘要"""
    auth_summary: dict[str, int] = {}
    method_summary: dict[str, int] = {}
    paths: list[str] = []
    warnings: list[str] = []

    for ep in endpoints_data:
        method = ep.get("method", "UNKNOWN")
        method_summary[method] = method_summary.get(method, 0) + 1

        path = ep.get("path", "")
        if path:
            paths.append(path)

        # 统计认证
        security = ep.get("security")
        has_auth, auth_type = _detect_auth_type(security)
        if has_auth and auth_type:
            auth_summary[auth_type] = auth_summary.get(auth_type, 0) + 1
        elif not has_auth:
            auth_summary["none"] = auth_summary.get("none", 0) + 1

    # 检查缺失 schema
    for ep in endpoints_data:
        if not ep.get("responses"):
            warnings.append(f"{ep.get('method')} {ep.get('path')}: 缺少响应定义")

    return ImportPreview(
        auth_summary=auth_summary,
        method_summary=method_summary,
        path_prefixes=_extract_path_prefixes(paths),
        warnings=warnings,
    )


def _build_endpoint_summaries(
    endpoints_data: list[dict[str, Any]],
) -> list[EndpointSummary]:
    """构建端点摘要列表"""
    summaries: list[EndpointSummary] = []
    for ep in endpoints_data:
        has_auth, auth_type = _detect_auth_type(ep.get("security"))
        parameters = ep.get("parameters", []) or []

        summaries.append(EndpointSummary(
            endpoint_id=None,  # 创建后填充
            display_name=ep.get("display_name", f"{ep.get('method', '')} {ep.get('path', '')}"),
            method=ep.get("method", ""),
            path=ep.get("path", ""),
            summary=ep.get("summary"),
            tag_group=ep.get("tag_group"),
            has_auth=has_auth,
            auth_type=auth_type,
            has_request_body=ep.get("request_body") is not None,
            has_path_params=_has_path_params(ep.get("path", "")),
            has_query_params=_has_query_params(parameters),
        ))
    return summaries


def _build_unified_result(
    source_type: str,
    schema_title: str,
    schema_version: str,
    parser_result: dict[str, Any],
    openapi_spec: dict[str, Any],
    warnings: list[str] | None = None,
    errors: list[str] | None = None,
) -> UnifiedImportResult:
    """从解析结果构建统一响应"""
    endpoints_data = parser_result.get("endpoints", [])
    tag_folders_data = parser_result.get("tag_folders", [])

    tag_folders = [
        TagFolderSummary(
            folder_id=str(tf.get("folder_id", "")),
            folder_name=tf.get("folder_name", ""),
            endpoint_count=tf.get("endpoint_count", 0),
        )
        for tf in tag_folders_data
    ]

    preview = _build_preview_from_openapi(openapi_spec, endpoints_data)
    if warnings:
        preview.warnings.extend(warnings)

    return UnifiedImportResult(
        source_type=source_type,
        schema_title=schema_title,
        schema_version=schema_version,
        total_endpoints=parser_result.get("total_endpoints", 0),
        total_tags=parser_result.get("total_tags", 0),
        tag_folders=tag_folders,
        endpoints=_build_endpoint_summaries(endpoints_data),
        warnings=warnings or [],
        errors=errors or [],
        preview=preview,
        created=parser_result if "tag_folders" in parser_result else None,
    )


# =============================================================================
# 核心解析函数
# =============================================================================

async def _parse_openapi_source(
    source_type: str,
    openapi_spec: dict[str, Any],
    project_id: UUID,
    parent_folder_id: UUID | None,
    user_id: UUID,
    db: AsyncSession,
    create_structure: bool = True,
) -> UnifiedImportResult:
    """解析 OpenAPI 来源（swagger/apifox 在线或离线）"""
    info = openapi_spec.get("info", {})
    title = info.get("title", "API")
    version = info.get("version", "1.0.0")

    warnings: list[str] = []

    # 验证必需字段
    if "paths" not in openapi_spec:
        return UnifiedImportResult(
            source_type=source_type,
            schema_title=title,
            schema_version=version,
            total_endpoints=0,
            total_tags=0,
            tag_folders=[],
            endpoints=[],
            warnings=[],
            errors=["OpenAPI 文档缺少 'paths' 字段，无法识别接口"],
            preview=ImportPreview(),
            created=None,
        )

    if not openapi_spec.get("paths"):
        warnings.append("OpenAPI 文档的 paths 为空，未找到任何接口")

    parser = OpenAPIParser(db)

    if create_structure:
        try:
            result = await parser.parse_and_create_structure(
                project_id=project_id,
                parent_folder_id=parent_folder_id,
                schema_file_id=None,
                openapi_spec=openapi_spec,
                user_id=user_id,
            )
            await db.commit()
            return _build_unified_result(
                source_type=source_type,
                schema_title=title,
                schema_version=version,
                parser_result=result,
                openapi_spec=openapi_spec,
                warnings=warnings,
            )
        except Exception as exc:
            await db.rollback()
            return UnifiedImportResult(
                source_type=source_type,
                schema_title=title,
                schema_version=version,
                total_endpoints=0,
                total_tags=0,
                tag_folders=[],
                endpoints=[],
                warnings=warnings,
                errors=[f"创建文件夹结构失败: {exc}"],
                preview=ImportPreview(),
                created=None,
            )
    else:
        # 仅预览，不创建结构
        # 复用分组逻辑但不落库
        endpoints_by_tag = parser._group_endpoints_by_tag(openapi_spec.get("paths", {}))
        result = {
            "schema_title": title,
            "schema_version": version,
            "total_endpoints": sum(len(eps) for eps in endpoints_by_tag.values()),
            "total_tags": len(endpoints_by_tag),
            "tag_folders": [
                {"folder_id": "", "folder_name": tag, "endpoint_count": len(eps)}
                for tag, eps in endpoints_by_tag.items()
            ],
            "endpoints": [
                {
                    "display_name": f"{ep['method']} {ep['path']}",
                    "method": ep["method"],
                    "path": ep["path"],
                    "summary": ep.get("summary"),
                    "tag_group": tag,
                    "security": ep.get("security"),
                    "request_body": ep.get("request_body"),
                    "parameters": ep.get("parameters"),
                }
                for tag, eps in endpoints_by_tag.items()
                for ep in eps
            ],
        }
        return _build_unified_result(
            source_type=source_type,
            schema_title=title,
            schema_version=version,
            parser_result=result,
            openapi_spec=openapi_spec,
            warnings=warnings,
        )


async def _parse_text_source(
    source_type: str,
    text_content: str,
    title: str | None,
    project_id: UUID,
    parent_folder_id: UUID | None,
    user_id: UUID,
    db: AsyncSession,
    create_structure: bool = True,
) -> UnifiedImportResult:
    """解析文本来源（manual_text / manual_file）"""
    service = APIDocParserService()

    try:
        draft = service.parse_text_to_draft(text_content, title=title)
    except Exception as exc:
        return UnifiedImportResult(
            source_type=source_type,
            schema_title=title or "API文档",
            schema_version="1.0.0",
            total_endpoints=0,
            total_tags=0,
            tag_folders=[],
            endpoints=[],
            warnings=[],
            errors=[f"文本解析失败: {exc}"],
            preview=ImportPreview(),
            created=None,
        )

    if not draft.get("endpoints"):
        return UnifiedImportResult(
            source_type=source_type,
            schema_title=draft.get("title", "API文档"),
            schema_version="1.0.0",
            total_endpoints=0,
            total_tags=0,
            tag_folders=[],
            endpoints=[],
            warnings=[],
            errors=["未从文档中识别到 API 接口，请确认包含类似 'POST /api/xxx' 的接口行"],
            preview=ImportPreview(),
            created=None,
        )

    warnings: list[str] = []
    if not draft.get("base_url"):
        warnings.append("未识别到 base_url/域名，后续测试需要手动配置")

    # 转换为 OpenAPI spec
    openapi_spec = service.draft_to_openapi(draft)

    # 复用 OpenAPI 解析逻辑
    return await _parse_openapi_source(
        source_type=source_type,
        openapi_spec=openapi_spec,
        project_id=project_id,
        parent_folder_id=parent_folder_id,
        user_id=user_id,
        db=db,
        create_structure=create_structure,
    )


# =============================================================================
# API 路由
# =============================================================================

@router.post(
    "/unified-import",
    response_model=SuccessResponse[UnifiedImportResult],
    summary="统一 API 文档导入入口",
)
async def unified_import(
    request: UnifiedImportRequest,
    current_user_id: CurrentUserIdDep,
    db: DbSessionDep,
) -> SuccessResponse[UnifiedImportResult]:
    """
    统一的 API 文档导入入口

    支持六种来源类型：
    - swagger_online: Swagger/OpenAPI 在线 URL
    - swagger_offline: Swagger/OpenAPI 离线文件（JSON/YAML）
    - apifox_online: Apifox 在线 URL（需为标准 OpenAPI 格式）
    - apifox_offline: Apifox 离线导出文件
    - manual_text: 手工文本粘贴（支持 TXT/curl 格式）
    - manual_file: 手工文件上传（TXT/DOCX）
    """
    from app.api.v2.api_endpoints import fetch_openapi_from_url

    project = await _get_project(request.project_identifier, db)
    parent_id = request.parent_folder_id if request.parent_folder_id else None

    errors: list[str] = []

    # ------------------------------------------------------------------
    # 根据来源类型分发到对应解析器
    # ------------------------------------------------------------------

    if request.source_type in ("swagger_online", "apifox_online"):
        # 在线 URL 拉取
        if not request.url:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"{request.source_type} 类型需要提供 url 参数",
            )

        try:
            openapi_spec = await fetch_openapi_from_url(request.url, request.headers)
        except HTTPException:
            raise
        except Exception as exc:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"URL 拉取失败: {exc}",
            ) from exc

        result = await _parse_openapi_source(
            source_type=request.source_type,
            openapi_spec=openapi_spec,
            project_id=project.id,
            parent_folder_id=parent_id,
            user_id=current_user_id,
            db=db,
            create_structure=request.create_structure,
        )

    elif request.source_type in ("swagger_offline", "apifox_offline"):
        # 离线文件内容
        if not request.file_content:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"{request.source_type} 类型需要提供 file_content 参数",
            )

        openapi_spec = request.file_content

        # 验证基本结构
        if not isinstance(openapi_spec, dict):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="file_content 必须是有效的 JSON 对象",
            )

        if "paths" not in openapi_spec:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="OpenAPI 文档必须包含 'paths' 字段",
            )

        result = await _parse_openapi_source(
            source_type=request.source_type,
            openapi_spec=openapi_spec,
            project_id=project.id,
            parent_folder_id=parent_id,
            user_id=current_user_id,
            db=db,
            create_structure=request.create_structure,
        )

    elif request.source_type == "manual_text":
        # 手工文本粘贴
        if not request.text_content:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="manual_text 类型需要提供 text_content 参数",
            )

        result = await _parse_text_source(
            source_type=request.source_type,
            text_content=request.text_content,
            title=None,
            project_id=project.id,
            parent_folder_id=parent_id,
            user_id=current_user_id,
            db=db,
            create_structure=request.create_structure,
        )

    elif request.source_type == "manual_file":
        # 手工文件上传（通过 /unified-import-file 处理）
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="manual_file 请使用 POST /unified-import-file 接口",
        )

    else:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"不支持的 source_type: {request.source_type}",
        )

    return SuccessResponse(success=True, data=result)


@router.post(
    "/unified-import-file",
    response_model=SuccessResponse[UnifiedImportResult],
    summary="统一文件上传导入",
)
async def unified_import_file(
    project_identifier: str = Form(..., description="项目标识符"),
    source_type: str = Form(..., description="来源类型"),
    parent_folder_id: str | None = Form(default=None, description="父文件夹 ID"),
    create_structure: bool = Form(default=True, description="是否创建文件夹结构"),
    current_user_id: CurrentUserIdDep = None,
    db: DbSessionDep = None,
    file: UploadFile = File(..., description="上传的文件"),
) -> SuccessResponse[UnifiedImportResult]:
    """
    统一文件上传导入入口

    支持文件类型：
    - .json / .yaml / .yml: OpenAPI/Swagger 格式
    - .txt: 纯文本 API 文档
    - .docx: Word 文档
    """
    project = await _get_project(project_identifier, db)
    parent_id = UUID(parent_folder_id) if parent_folder_id else None

    # 读取文件内容
    content_bytes = await file.read()
    if len(content_bytes) > 15 * 1024 * 1024:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="文件大小不能超过 15MB",
        )

    filename = file.filename or ""
    suffix = filename.lower().split(".")[-1] if "." in filename else ""

    # 根据文件类型分发
    if suffix in ("yaml", "yml", "json"):
        # OpenAPI 格式文件
        try:
            text = content_bytes.decode("utf-8")
        except UnicodeDecodeError:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="文件编码错误，请使用 UTF-8 编码",
            )

        if suffix == "json":
            try:
                openapi_spec = json.loads(text)
            except json.JSONDecodeError as exc:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=f"JSON 解析失败: {exc}",
                ) from exc
        else:
            # YAML
            try:
                import yaml
                openapi_spec = yaml.safe_load(text)
            except ImportError:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="YAML 解析需要安装 PyYAML: pip install pyyaml",
                )
            except Exception as exc:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=f"YAML 解析失败: {exc}",
                ) from exc

        if not isinstance(openapi_spec, dict):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="解析后的内容不是有效的对象",
            )

        result = await _parse_openapi_source(
            source_type=source_type,
            openapi_spec=openapi_spec,
            project_id=project.id,
            parent_folder_id=parent_id,
            user_id=current_user_id,
            db=db,
            create_structure=create_structure,
        )

    elif suffix in ("txt", "docx"):
        # 文本文档
        service = APIDocParserService()
        try:
            text = service.extract_text_from_file(filename, file.content_type, content_bytes)
        except ValueError as exc:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=str(exc),
            ) from exc

        result = await _parse_text_source(
            source_type=source_type,
            text_content=text,
            title=filename,
            project_id=project.id,
            parent_folder_id=parent_id,
            user_id=current_user_id,
            db=db,
            create_structure=create_structure,
        )

    else:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"不支持的文件类型: .{suffix}，请上传 .json/.yaml/.yml/.txt/.docx 文件",
        )

    return SuccessResponse(success=True, data=result)
