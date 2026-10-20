"""API document parsing routes."""

from typing import Any
import json
from uuid import UUID

from fastapi import APIRouter, File, Form, HTTPException, UploadFile, status
from pydantic import BaseModel, Field
from sqlalchemy import select

from app.api.deps import CurrentUserIdDep, DbSessionDep
from app.models.api_doc_parse_task import APIDocParseTask
from app.models.project import Project
from app.schemas.common import SuccessResponse
from app.services.api_doc_parser_service import APIDocParserService
from app.services.excel_import_service import ExcelImportService
from app.services.openapi_parser import OpenAPIParser
from app.services.postman_collection_parser import PostmanCollectionParser
from app.services.postman_environment_parser import PostmanEnvironmentParser


router = APIRouter(
    prefix="/projects/{project_identifier}/api-docs",
    tags=["API 文档解析"],
)


@router.post("/parse-bundle")
async def parse_document_bundle(
    project_identifier: str,
    db: DbSessionDep,
    user_id: CurrentUserIdDep,
    excel: UploadFile = File(...),
    collection: UploadFile = File(...),
    environment: UploadFile = File(...),
):
    """Parse the three source documents and return a reviewable bundle preview.

    This endpoint deliberately performs no import; confirmation remains a separate step.
    """
    await _get_project(project_identifier, db)
    excel_bytes = await excel.read()
    collection_data = json.loads((await collection.read()).decode("utf-8-sig"))
    environment_data = json.loads((await environment.read()).decode("utf-8-sig"))
    parsed_collection = PostmanCollectionParser().parse(json.dumps(collection_data))
    parsed_environment = PostmanEnvironmentParser().parse(json.dumps(environment_data))
    return SuccessResponse(data={
        "project_identifier": project_identifier,
        "sources": {"excel": excel.filename, "collection": collection.filename, "environment": environment.filename},
        "collection_count": len(parsed_collection.requests),
        "environment_count": len(parsed_environment.variables),
        "excel_size": len(excel_bytes),
        "status": "parsed",
    })


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


# ---------- Excel API 文档解析（美心模板） ----------

class ExcelParseConfirmRequest(BaseModel):
    """确认导入单个接口的请求体（可携带前端编辑后的统一模型）。"""

    edited_spec: dict[str, Any] | None = Field(default=None, description="前端编辑后的统一接口模型")
    parent_folder_id: UUID | None = Field(default=None, description="父文件夹 ID")


async def _get_parse_task(task_id: UUID, project_id: UUID, db: DbSessionDep) -> APIDocParseTask:
    stmt = select(APIDocParseTask).where(APIDocParseTask.id == task_id)
    result = await db.execute(stmt)
    task = result.scalar_one_or_none()
    if not task or task.project_id != project_id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"解析任务 {task_id} 不存在",
        )
    return task


def _task_summary(task: APIDocParseTask) -> dict[str, Any]:
    interfaces = (task.result or {}).get("interfaces", [])
    return {
        "task_id": str(task.id),
        "status": task.status,
        "doc_type": task.doc_type,
        "error": task.error,
        "attachment_id": str(task.attachment_id) if task.attachment_id else None,
        "interface_count": len(interfaces),
        "interfaces": [
            {
                "index": i,
                "name": s.get("name"),
                "method_name": s.get("method_name"),
                "http_method": s.get("http_method"),
                "path": s.get("path"),
                "pending_count": len(s.get("pending_confirmations", [])),
            }
            for i, s in enumerate(interfaces)
        ],
        "created_at": task.created_at.isoformat() if task.created_at else None,
    }


@router.post(
    "/parse-excel",
    response_model=SuccessResponse[dict[str, Any]],
    summary="上传并解析 Excel API 文档（美心模板）",
)
async def parse_excel_api_doc(
    project_identifier: str,
    current_user_id: CurrentUserIdDep,
    db: DbSessionDep,
    file: UploadFile = File(..., description="Excel API 文档，支持 .xlsx"),
    parent_folder_id: UUID | None = Form(default=None, description="父文件夹 ID（确认导入时生效）"),
) -> SuccessResponse[dict[str, Any]]:
    if not (file.filename or "").lower().endswith(".xlsx"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="仅支持 .xlsx 文件",
        )
    content_bytes = await file.read()
    if len(content_bytes) > 15 * 1024 * 1024:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="文件大小不能超过 15MB",
        )

    project = await _get_project(project_identifier, db)
    service = ExcelImportService(db)
    try:
        attachment = await service.save_source_attachment(
            project_id=project.id,
            filename=file.filename or "api_doc.xlsx",
            content=content_bytes,
            content_type=file.content_type or "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            created_by=str(current_user_id),
        )
        task = await service.parse_and_create_task(
            project_id=project.id,
            attachment=attachment,
            content=content_bytes,
            created_by=str(current_user_id),
        )
        await db.commit()
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"解析 Excel 失败: {exc}",
        ) from exc

    return SuccessResponse(success=True, data=_task_summary(task))


@router.get(
    "/parse-excel/{task_id}",
    response_model=SuccessResponse[dict[str, Any]],
    summary="查询 Excel 解析状态与接口列表",
)
async def get_excel_parse_status(
    project_identifier: str,
    task_id: UUID,
    db: DbSessionDep,
) -> SuccessResponse[dict[str, Any]]:
    project = await _get_project(project_identifier, db)
    task = await _get_parse_task(task_id, project.id, db)
    return SuccessResponse(success=True, data=_task_summary(task))


@router.get(
    "/parse-excel/{task_id}/interfaces/{index}",
    response_model=SuccessResponse[dict[str, Any]],
    summary="获取单个接口的统一模型详情（含来源与待确认项）",
)
async def get_excel_interface_detail(
    project_identifier: str,
    task_id: UUID,
    index: int,
    db: DbSessionDep,
) -> SuccessResponse[dict[str, Any]]:
    project = await _get_project(project_identifier, db)
    task = await _get_parse_task(task_id, project.id, db)
    interfaces = (task.result or {}).get("interfaces", [])
    if index < 0 or index >= len(interfaces):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"接口索引 {index} 越界（共 {len(interfaces)} 个）",
        )
    return SuccessResponse(success=True, data={"index": index, "interface": interfaces[index]})


@router.post(
    "/parse-excel/{task_id}/interfaces/{index}/confirm",
    response_model=SuccessResponse[dict[str, Any]],
    summary="确认导入单个接口为 APIEndpoint",
)
async def confirm_excel_interface(
    project_identifier: str,
    task_id: UUID,
    index: int,
    request: ExcelParseConfirmRequest,
    current_user_id: CurrentUserIdDep,
    db: DbSessionDep,
) -> SuccessResponse[dict[str, Any]]:
    project = await _get_project(project_identifier, db)
    task = await _get_parse_task(task_id, project.id, db)
    if task.status != "completed":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"解析任务状态为 {task.status}，不能导入",
        )
    service = ExcelImportService(db)
    try:
        endpoint = await service.confirm_import(
            project_id=project.id,
            task=task,
            interface_index=index,
            edited_spec=request.edited_spec,
            parent_folder_id=request.parent_folder_id,
        )
        await db.commit()
    except IndexError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except Exception as exc:  # noqa: BLE001
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"导入接口失败: {exc}",
        ) from exc

    return SuccessResponse(success=True, data={
        "endpoint_id": str(endpoint.id),
        "display_name": endpoint.display_name,
        "method": endpoint.method,
        "path": endpoint.path,
        "folder_id": str(endpoint.folder_id),
    })
