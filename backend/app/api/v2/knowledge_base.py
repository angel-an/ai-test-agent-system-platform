"""
知识库管理 API

提供知识空间、文档的 CRUD 操作接口，以及检索接口
"""

import logging
from typing import Optional
from uuid import UUID

from fastapi import APIRouter, File, Form, Query, UploadFile, status, Depends

from app.api.deps import (
    CurrentUserIdDep,
    DbSessionDep,
    PaginationDep,
    KnowledgeBaseServiceDep,
)
from app.schemas.knowledge_base import (
    KnowledgeSpaceCreate,
    KnowledgeSpaceUpdate,
    KnowledgeSpaceInfo,
    KnowledgeDocumentInfo,
    KnowledgeUploadResponse,
    KnowledgeRetrievalRequest,
    KnowledgeRetrievalResponse,
)
from app.schemas.common import SuccessResponse, MessageResponse
from app.schemas.pagination import PaginatedResponse
from app.services.knowledge_base_service import KnowledgeBaseService
from app.services.knowledge_retriever import KnowledgeRetriever
from app.services.knowledge_indexer import KnowledgeIndexer
from app.repositories.project_repo import ProjectRepository
from app.utils.exceptions import NotFoundException, ForbiddenException, BadRequestException

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/projects/{project_identifier}/knowledge-base")


# ============================================================================
# 项目隔离校验辅助函数
# ============================================================================

async def _get_project_id(
    session,
    project_identifier: str,
) -> UUID:
    """根据标识符获取项目 ID，不存在则抛异常"""
    repo = ProjectRepository(session)
    project = await repo.get_by_identifier(project_identifier)
    if not project:
        raise NotFoundException(resource_type="项目", resource_id=project_identifier)
    return project.id


async def _verify_space_belongs_to_project(
    service: KnowledgeBaseService,
    space_id: UUID,
    project_identifier: str,
    session,  # 复用当前请求 session，避免嵌套 session 冲突
) -> None:
    """
    校验知识空间是否属于指定项目。

    不满足时抛出 ForbiddenException，防止跨项目访问。
    """
    space = await service.get_space(space_id)
    # 复用当前 session 查项目，避免 async_session_factory 嵌套冲突
    project_id = await _get_project_id(session, project_identifier)

    if space.project_id != project_id:
        raise ForbiddenException(
            message=f"知识空间 {space_id} 不属于项目 {project_identifier}"
        )


# ============================================================================
# 知识空间接口
# ============================================================================

@router.get(
    "/spaces",
    response_model=PaginatedResponse[KnowledgeSpaceInfo],
    summary="获取知识空间列表",
    description="获取项目下的所有知识空间列表，支持搜索和分页",
)
async def get_knowledge_spaces(
    project_identifier: str,
    service: KnowledgeBaseServiceDep,
    pagination: PaginationDep,
    search: Optional[str] = Query(None, description="搜索关键词"),
    business_line: Optional[str] = Query(None, description="业务线过滤"),
):
    """获取知识空间列表"""
    spaces, total = await service.list_spaces(
        project_identifier=project_identifier,
        search=search,
        business_line=business_line,
        offset=pagination.offset,
        limit=pagination.limit,
    )

    from app.schemas.pagination import PaginationInfo
    info = PaginationInfo.create(
        page=pagination.p,
        page_size=pagination.page_size,
        total=total,
        base_url=f"/api/v2/projects/{project_identifier}/knowledge-base/spaces",
    )

    return PaginatedResponse(
        success=True,
        info=info,
        data=spaces,
    )


@router.post(
    "/spaces",
    response_model=SuccessResponse[KnowledgeSpaceInfo],
    status_code=status.HTTP_201_CREATED,
    summary="创建知识空间",
    description="在项目中创建新的知识空间",
)
async def create_knowledge_space(
    project_identifier: str,
    data: KnowledgeSpaceCreate,
    service: KnowledgeBaseServiceDep,
    current_user_id: CurrentUserIdDep,
):
    """创建知识空间"""
    space = await service.create_space(
        project_identifier=project_identifier,
        data=data,
        created_by=current_user_id,
    )
    return SuccessResponse(success=True, data=space)


@router.get(
    "/spaces/{space_id}",
    response_model=SuccessResponse[KnowledgeSpaceInfo],
    summary="获取知识空间详情",
    description="获取指定知识空间的详细信息",
)
async def get_knowledge_space(
    project_identifier: str,
    space_id: UUID,
    service: KnowledgeBaseServiceDep,
    db: DbSessionDep,
):
    """获取知识空间详情——带项目隔离校验"""
    await _verify_space_belongs_to_project(service, space_id, project_identifier, db)
    space = await service.get_space(space_id)
    return SuccessResponse(success=True, data=space)


@router.put(
    "/spaces/{space_id}",
    response_model=SuccessResponse[KnowledgeSpaceInfo],
    summary="更新知识空间",
    description="更新知识空间信息",
)
async def update_knowledge_space(
    project_identifier: str,
    space_id: UUID,
    data: KnowledgeSpaceUpdate,
    service: KnowledgeBaseServiceDep,
    db: DbSessionDep,
):
    """更新知识空间——带项目隔离校验"""
    await _verify_space_belongs_to_project(service, space_id, project_identifier, db)
    space = await service.update_space(space_id, data)
    return SuccessResponse(success=True, data=space)


@router.delete(
    "/spaces/{space_id}",
    response_model=MessageResponse,
    summary="删除知识空间",
    description="删除知识空间及其下的所有文档和切片",
)
async def delete_knowledge_space(
    project_identifier: str,
    space_id: UUID,
    service: KnowledgeBaseServiceDep,
    db: DbSessionDep,
):
    """删除知识空间——带项目隔离校验"""
    await _verify_space_belongs_to_project(service, space_id, project_identifier, db)
    await service.delete_space(space_id)
    return MessageResponse(success=True, message="知识空间删除成功")


# ============================================================================
# 文档接口
# ============================================================================

@router.post(
    "/spaces/{space_id}/documents",
    response_model=SuccessResponse[KnowledgeUploadResponse],
    status_code=status.HTTP_201_CREATED,
    summary="上传文档",
    description="上传文档到指定知识空间",
)
async def upload_document(
    project_identifier: str,
    space_id: UUID,
    service: KnowledgeBaseServiceDep,
    current_user_id: CurrentUserIdDep,
    db: DbSessionDep,
    file: UploadFile = File(..., description="要上传的文档文件"),
    title: Optional[str] = Form(None, description="文档标题"),
):
    """上传文档到知识空间——带项目隔离校验"""
    await _verify_space_belongs_to_project(service, space_id, project_identifier, db)

    # 验证文件类型
    valid_types = [
        "application/pdf",
        "application/msword",
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        "text/plain",
        "text/markdown",
    ]

    content_type = file.content_type or "application/octet-stream"
    if content_type not in valid_types:
        # 根据扩展名判断
        filename = file.filename or ""
        if not any(filename.lower().endswith(ext) for ext in ['.pdf', '.doc', '.docx', '.txt', '.md']):
            raise BadRequestException(
                message=f"[P1-FIXED] 不支持的文件类型: {content_type}",
            )

    # 读取文件内容
    file_data = await file.read()
    if len(file_data) > 50 * 1024 * 1024:  # 50MB 限制
        raise BadRequestException(
            message="文件大小不能超过 50MB",
        )

    result = await service.upload_document(
        space_id=space_id,
        file_name=file.filename or "unnamed",
        file_data=file_data,
        content_type=content_type,
        created_by=current_user_id,
        title=title,
    )

    return SuccessResponse(success=True, data=result)


@router.get(
    "/spaces/{space_id}/documents",
    response_model=PaginatedResponse[KnowledgeDocumentInfo],
    summary="获取文档列表",
    description="获取知识空间下的文档列表",
)
async def get_documents(
    project_identifier: str,
    space_id: UUID,
    service: KnowledgeBaseServiceDep,
    pagination: PaginationDep,
    db: DbSessionDep,
):
    """获取文档列表——带项目隔离校验"""
    await _verify_space_belongs_to_project(service, space_id, project_identifier, db)
    documents, total = await service.list_documents(
        space_id=space_id,
        offset=pagination.offset,
        limit=pagination.limit,
    )

    from app.schemas.pagination import PaginationInfo
    info = PaginationInfo.create(
        page=pagination.p,
        page_size=pagination.page_size,
        total=total,
        base_url=f"/api/v2/projects/{project_identifier}/knowledge-base/spaces/{space_id}/documents",
    )

    return PaginatedResponse(
        success=True,
        info=info,
        data=documents,
    )


@router.get(
    "/spaces/{space_id}/documents/{doc_id}",
    response_model=SuccessResponse[KnowledgeDocumentInfo],
    summary="获取文档详情",
    description="获取指定文档的详细信息",
)
async def get_document(
    project_identifier: str,
    space_id: UUID,
    doc_id: UUID,
    service: KnowledgeBaseServiceDep,
    db: DbSessionDep,
):
    """获取文档详情——带项目隔离校验"""
    await _verify_space_belongs_to_project(service, space_id, project_identifier, db)
    document = await service.get_document(space_id, doc_id)
    return SuccessResponse(success=True, data=document)


@router.delete(
    "/spaces/{space_id}/documents/{doc_id}",
    response_model=MessageResponse,
    summary="删除文档",
    description="删除指定文档",
)
async def delete_document(
    project_identifier: str,
    space_id: UUID,
    doc_id: UUID,
    service: KnowledgeBaseServiceDep,
    db: DbSessionDep,
):
    """删除文档——带项目隔离校验"""
    await _verify_space_belongs_to_project(service, space_id, project_identifier, db)
    await service.delete_document(space_id, doc_id)
    return MessageResponse(success=True, message="文档删除成功")


# ============================================================================
# 索引管理
# ============================================================================

@router.post(
    "/spaces/{space_id}/rebuild",
    response_model=SuccessResponse[dict],
    summary="重建索引",
    description="重建知识空间下所有文档的索引",
)
async def rebuild_index(
    project_identifier: str,
    space_id: UUID,
    db: DbSessionDep,
    service: KnowledgeBaseServiceDep,
):
    """重建索引——带项目隔离校验"""
    await _verify_space_belongs_to_project(service, space_id, project_identifier, db)
    indexer = KnowledgeIndexer(db)
    result = await indexer.reindex_space(space_id)
    return SuccessResponse(success=True, data=result)


@router.post(
    "/spaces/{space_id}/documents/{doc_id}/index",
    response_model=MessageResponse,
    summary="索引文档",
    description="对指定文档执行索引",
)
async def index_document(
    project_identifier: str,
    space_id: UUID,
    doc_id: UUID,
    db: DbSessionDep,
    service: KnowledgeBaseServiceDep,
):
    """索引单个文档——带项目隔离校验"""
    await _verify_space_belongs_to_project(service, space_id, project_identifier, db)
    indexer = KnowledgeIndexer(db)
    success = await indexer.index_document(doc_id)

    if success:
        return MessageResponse(success=True, message="文档索引成功")
    else:
        return MessageResponse(success=False, message="文档索引失败")


# ============================================================================
# 检索接口
# ============================================================================

@router.post(
    "/retrieve",
    response_model=KnowledgeRetrievalResponse,
    summary="知识检索",
    description="在知识库中执行检索查询",
)
async def retrieve_knowledge(
    project_identifier: str,
    request: KnowledgeRetrievalRequest,
    db: DbSessionDep,
):
    """知识检索"""
    # 获取项目 ID
    project_id = await _get_project_id(db, project_identifier)

    retriever = KnowledgeRetriever(db)
    response = await retriever.retrieve(request, project_id=project_id)
    return response
