"""API document parsing routes."""

from typing import Any
from uuid import UUID

from fastapi import APIRouter, File, Form, HTTPException, UploadFile, status
from pydantic import BaseModel, Field
from sqlalchemy import select

from app.api.deps import CurrentUserIdDep, DbSessionDep
from app.models.project import Project
from app.schemas.common import SuccessResponse
from app.services.api_doc_parser_service import APIDocParserService
from app.services.openapi_parser import OpenAPIParser


router = APIRouter(
    prefix="/projects/{project_identifier}/api-docs",
    tags=["API 文档解析"],
)


class APIDocParseTextRequest(BaseModel):
    """Request for parsing API documentation text."""

    content: str = Field(..., min_length=1, description="API 文档文本内容")
    title: str | None = Field(default=None, description="文档标题")
    parent_folder_id: UUID | None = Field(default=None, description="父文件夹 ID")
    create_structure: bool = Field(default=True, description="是否创建 APIEndpoint 结构")


async def _get_project(project_identifier: str, db: DbSessionDep) -> Project:
    stmt = select(Project).where(Project.identifier == project_identifier)
    result = await db.execute(stmt)
    project = result.scalar_one_or_none()
    if not project:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"项目 {project_identifier} 不存在",
        )
    return project


async def _parse_and_optionally_create_structure(
    *,
    project_identifier: str,
    content: str,
    title: str | None,
    parent_folder_id: UUID | None,
    create_structure: bool,
    current_user_id: UUID,
    db: DbSessionDep,
) -> dict[str, Any]:
    project = await _get_project(project_identifier, db)
    service = APIDocParserService()
    draft = service.parse_text_to_draft(content, title=title)
    openapi_spec = service.draft_to_openapi(draft)

    if not draft["endpoints"]:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="未从文档中识别到 API 接口，请确认包含类似 'POST /api/xxx' 的接口行",
        )

    data: dict[str, Any] = {
        "draft": draft,
        "openapi": openapi_spec,
        "created": None,
    }

    if create_structure:
        try:
            parser = OpenAPIParser(db)
            data["created"] = await parser.parse_and_create_structure(
                project_id=project.id,
                parent_folder_id=parent_folder_id,
                schema_file_id=None,
                openapi_spec=openapi_spec,
                user_id=current_user_id,
            )
            await db.commit()
        except Exception as exc:
            await db.rollback()
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"创建 APIEndpoint 结构失败: {exc}",
            ) from exc

    return data


@router.post(
    "/parse-text",
    response_model=SuccessResponse[dict[str, Any]],
    summary="解析 TXT/Word 提取出的 API 文档文本",
)
async def parse_api_doc_text(
    project_identifier: str,
    request: APIDocParseTextRequest,
    current_user_id: CurrentUserIdDep,
    db: DbSessionDep,
) -> SuccessResponse[dict[str, Any]]:
    data = await _parse_and_optionally_create_structure(
        project_identifier=project_identifier,
        content=request.content,
        title=request.title,
        parent_folder_id=request.parent_folder_id,
        create_structure=request.create_structure,
        current_user_id=current_user_id,
        db=db,
    )
    return SuccessResponse(success=True, data=data)


@router.post(
    "/parse-file",
    response_model=SuccessResponse[dict[str, Any]],
    summary="上传并解析 TXT/DOCX API 文档",
)
async def parse_api_doc_file(
    project_identifier: str,
    current_user_id: CurrentUserIdDep,
    db: DbSessionDep,
    file: UploadFile = File(..., description="API 文档文件，支持 txt/docx"),
    title: str | None = Form(default=None, description="文档标题"),
    parent_folder_id: UUID | None = Form(default=None, description="父文件夹 ID"),
    create_structure: bool = Form(default=True, description="是否创建 APIEndpoint 结构"),
) -> SuccessResponse[dict[str, Any]]:
    content_bytes = await file.read()
    if len(content_bytes) > 15 * 1024 * 1024:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="文件大小不能超过 15MB",
        )

    service = APIDocParserService()
    try:
        content = service.extract_text_from_file(file.filename, file.content_type, content_bytes)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

    data = await _parse_and_optionally_create_structure(
        project_identifier=project_identifier,
        content=content,
        title=title or file.filename,
        parent_folder_id=parent_folder_id,
        create_structure=create_structure,
        current_user_id=current_user_id,
        db=db,
    )
    data["file"] = {
        "filename": file.filename,
        "content_type": file.content_type,
        "size": len(content_bytes),
    }
    return SuccessResponse(success=True, data=data)
