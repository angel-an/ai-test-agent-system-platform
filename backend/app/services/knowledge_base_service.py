"""
知识库服务

处理知识空间、文档的管理业务逻辑
"""

import logging
from typing import Optional
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func

from app.models.knowledge_space import KnowledgeSpace
from app.models.knowledge_document import KnowledgeDocument, DocumentStatus
from app.models.knowledge_chunk import KnowledgeChunk
from app.repositories.project_repo import ProjectRepository
from app.schemas.knowledge_base import (
    KnowledgeSpaceCreate, KnowledgeSpaceUpdate, KnowledgeSpaceInfo,
    KnowledgeDocumentInfo, KnowledgeUploadResponse,
)
from app.utils.exceptions import NotFoundException, BadRequestException
from app.config.minio_client import MinIOClient, MinIOError

logger = logging.getLogger(__name__)


class KnowledgeBaseService:
    """
    知识库服务类

    处理知识空间、文档的 CRUD 和业务逻辑
    """

    def __init__(self, session: AsyncSession):
        self.session = session
        self.project_repo = ProjectRepository(session)

    async def _get_project_id_by_identifier(self, project_identifier: str) -> UUID:
        """根据项目标识符获取项目 ID"""
        project = await self.project_repo.get_by_identifier(project_identifier)
        if not project:
            raise NotFoundException(resource_type="项目", resource_id=project_identifier)
        return project.id

    # ========================================================================
    # 知识空间管理
    # ========================================================================

    async def create_space(
        self,
        project_identifier: str,
        data: KnowledgeSpaceCreate,
        created_by: UUID,
    ) -> KnowledgeSpaceInfo:
        """创建知识空间"""
        project_id = await self._get_project_id_by_identifier(project_identifier)

        space = KnowledgeSpace(
            project_id=project_id,
            name=data.name,
            description=data.description,
            business_line=data.business_line,
            folder_scope=data.folder_scope or [],
            created_by=created_by,
        )
        self.session.add(space)
        await self.session.flush()
        await self.session.refresh(space)

        return KnowledgeSpaceInfo(
            id=space.id,
            project_id=space.project_id,
            name=space.name,
            description=space.description,
            business_line=space.business_line,
            folder_scope=space.folder_scope,
            created_by=space.created_by,
            created_at=space.created_at,
            updated_at=space.updated_at,
            document_count=0,
        )

    async def update_space(
        self,
        space_id: UUID,
        data: KnowledgeSpaceUpdate,
    ) -> KnowledgeSpaceInfo:
        """更新知识空间"""
        result = await self.session.execute(
            select(KnowledgeSpace).where(KnowledgeSpace.id == space_id)
        )
        space = result.scalar_one_or_none()
        if not space:
            raise NotFoundException(resource_type="知识空间", resource_id=str(space_id))

        update_data = data.model_dump(exclude_unset=True)
        for key, value in update_data.items():
            if value is not None:
                setattr(space, key, value)

        await self.session.flush()
        await self.session.refresh(space)

        # 统计文档数量
        doc_count = await self._count_documents_by_space(space_id)

        return KnowledgeSpaceInfo(
            id=space.id,
            project_id=space.project_id,
            name=space.name,
            description=space.description,
            business_line=space.business_line,
            folder_scope=space.folder_scope,
            created_by=space.created_by,
            created_at=space.created_at,
            updated_at=space.updated_at,
            document_count=doc_count,
        )

    async def delete_space(self, space_id: UUID) -> None:
        """删除知识空间（级联删除文档和切片）"""
        result = await self.session.execute(
            select(KnowledgeSpace).where(KnowledgeSpace.id == space_id)
        )
        space = result.scalar_one_or_none()
        if not space:
            raise NotFoundException(resource_type="知识空间", resource_id=str(space_id))

        # 删除关联的 MinIO 文件
        docs_result = await self.session.execute(
            select(KnowledgeDocument).where(KnowledgeDocument.space_id == space_id)
        )
        documents = docs_result.scalars().all()
        for doc in documents:
            try:
                MinIOClient.delete_file(doc.file_path)
            except Exception as e:
                logger.warning(f"删除 MinIO 文件失败 {doc.file_path}: {e}")

        await self.session.delete(space)
        await self.session.flush()

    async def get_space(self, space_id: UUID) -> KnowledgeSpaceInfo:
        """获取知识空间详情"""
        result = await self.session.execute(
            select(KnowledgeSpace).where(KnowledgeSpace.id == space_id)
        )
        space = result.scalar_one_or_none()
        if not space:
            raise NotFoundException(resource_type="知识空间", resource_id=str(space_id))

        doc_count = await self._count_documents_by_space(space_id)

        return KnowledgeSpaceInfo(
            id=space.id,
            project_id=space.project_id,
            name=space.name,
            description=space.description,
            business_line=space.business_line,
            folder_scope=space.folder_scope,
            created_by=space.created_by,
            created_at=space.created_at,
            updated_at=space.updated_at,
            document_count=doc_count,
        )

    async def list_spaces(
        self,
        project_identifier: str,
        search: Optional[str] = None,
        business_line: Optional[str] = None,
        offset: int = 0,
        limit: int = 30,
    ) -> tuple[list[KnowledgeSpaceInfo], int]:
        """列出项目的知识空间"""
        project_id = await self._get_project_id_by_identifier(project_identifier)

        query = select(KnowledgeSpace).where(KnowledgeSpace.project_id == project_id)

        if search:
            query = query.where(
                (KnowledgeSpace.name.ilike(f"%{search}%")) |
                (KnowledgeSpace.description.ilike(f"%{search}%"))
            )

        if business_line:
            query = query.where(KnowledgeSpace.business_line == business_line)

        # 总数
        count_query = select(func.count()).select_from(KnowledgeSpace).where(
            KnowledgeSpace.project_id == project_id
        )
        if search:
            count_query = count_query.where(
                (KnowledgeSpace.name.ilike(f"%{search}%")) |
                (KnowledgeSpace.description.ilike(f"%{search}%"))
            )
        if business_line:
            count_query = count_query.where(KnowledgeSpace.business_line == business_line)

        total_result = await self.session.execute(count_query)
        total = total_result.scalar_one()

        # 分页查询
        query = query.order_by(KnowledgeSpace.created_at.desc()).offset(offset).limit(limit)
        result = await self.session.execute(query)
        spaces = result.scalars().all()

        # 转换为 Info 模型
        infos = []
        for space in spaces:
            doc_count = await self._count_documents_by_space(space.id)
            infos.append(KnowledgeSpaceInfo(
                id=space.id,
                project_id=space.project_id,
                name=space.name,
                description=space.description,
                business_line=space.business_line,
                folder_scope=space.folder_scope,
                created_by=space.created_by,
                created_at=space.created_at,
                updated_at=space.updated_at,
                document_count=doc_count,
            ))

        return infos, total

    async def _count_documents_by_space(self, space_id: UUID) -> int:
        """统计空间下的文档数量"""
        result = await self.session.execute(
            select(func.count()).select_from(KnowledgeDocument).where(
                KnowledgeDocument.space_id == space_id
            )
        )
        return result.scalar_one()

    # ========================================================================
    # 文档管理
    # ========================================================================

    async def upload_document(
        self,
        space_id: UUID,
        file_name: str,
        file_data: bytes,
        content_type: str,
        created_by: UUID,
        title: Optional[str] = None,
    ) -> KnowledgeUploadResponse:
        """上传文档到 MinIO 并创建记录"""
        # 验证空间存在
        result = await self.session.execute(
            select(KnowledgeSpace).where(KnowledgeSpace.id == space_id)
        )
        space = result.scalar_one_or_none()
        if not space:
            raise NotFoundException(resource_type="知识空间", resource_id=str(space_id))

        # 上传到 MinIO
        import io
        object_name = f"knowledge/{space_id}/{file_name}"
        try:
            MinIOClient.upload_file(
                object_name=object_name,
                data=io.BytesIO(file_data),
                length=len(file_data),
                content_type=content_type,
            )
        except MinIOError as e:
            logger.error(f"MinIO 上传失败: {e}")
            raise BadRequestException(f"文件上传失败: {e}") from e

        # 创建文档记录
        document = KnowledgeDocument(
            space_id=space_id,
            title=title or file_name,
            file_name=file_name,
            file_path=object_name,
            file_size=len(file_data),
            content_type=content_type,
            status=DocumentStatus.PENDING,
            chunk_count=0,
            created_by=created_by,
        )
        self.session.add(document)
        await self.session.flush()
        await self.session.refresh(document)

        return KnowledgeUploadResponse(
            document_id=str(document.id),
            file_name=file_name,
            file_size=len(file_data),
            status="pending",
            message="上传成功，等待索引",
        )

    async def delete_document(self, space_id: UUID, document_id: UUID) -> None:
        """删除文档"""
        result = await self.session.execute(
            select(KnowledgeDocument).where(
                KnowledgeDocument.id == document_id,
                KnowledgeDocument.space_id == space_id,
            )
        )
        document = result.scalar_one_or_none()
        if not document:
            raise NotFoundException(resource_type="文档", resource_id=str(document_id))

        # 删除 MinIO 文件
        try:
            MinIOClient.delete_file(document.file_path)
        except Exception as e:
            logger.warning(f"删除 MinIO 文件失败 {document.file_path}: {e}")

        await self.session.delete(document)
        await self.session.flush()

    async def list_documents(
        self,
        space_id: UUID,
        offset: int = 0,
        limit: int = 30,
    ) -> tuple[list[KnowledgeDocumentInfo], int]:
        """列出空间下的文档"""
        # 验证空间存在
        result = await self.session.execute(
            select(KnowledgeSpace).where(KnowledgeSpace.id == space_id)
        )
        space = result.scalar_one_or_none()
        if not space:
            raise NotFoundException(resource_type="知识空间", resource_id=str(space_id))

        # 查询文档
        query = select(KnowledgeDocument).where(
            KnowledgeDocument.space_id == space_id
        ).order_by(KnowledgeDocument.created_at.desc())

        count_result = await self.session.execute(
            select(func.count()).select_from(KnowledgeDocument).where(
                KnowledgeDocument.space_id == space_id
            )
        )
        total = count_result.scalar_one()

        result = await self.session.execute(query.offset(offset).limit(limit))
        documents = result.scalars().all()

        infos = []
        for doc in documents:
            infos.append(KnowledgeDocumentInfo(
                id=doc.id,
                space_id=doc.space_id,
                title=doc.title,
                file_name=doc.file_name,
                file_size=doc.file_size,
                content_type=doc.content_type,
                status=doc.status.value,
                chunk_count=doc.chunk_count,
                error_message=doc.error_message,
                created_by=doc.created_by,
                created_at=doc.created_at,
                updated_at=doc.updated_at,
            ))

        return infos, total

    async def get_document(self, space_id: UUID, document_id: UUID) -> KnowledgeDocumentInfo:
        """获取文档详情"""
        result = await self.session.execute(
            select(KnowledgeDocument).where(
                KnowledgeDocument.id == document_id,
                KnowledgeDocument.space_id == space_id,
            )
        )
        document = result.scalar_one_or_none()
        if not document:
            raise NotFoundException(resource_type="文档", resource_id=str(document_id))

        return KnowledgeDocumentInfo(
            id=document.id,
            space_id=document.space_id,
            title=document.title,
            file_name=document.file_name,
            file_size=document.file_size,
            content_type=document.content_type,
            status=document.status.value,
            chunk_count=document.chunk_count,
            error_message=document.error_message,
            created_by=document.created_by,
            created_at=document.created_at,
            updated_at=document.updated_at,
        )

    async def update_document_status(
        self,
        document_id: UUID,
        status: DocumentStatus,
        chunk_count: int = 0,
        error_message: Optional[str] = None,
    ) -> None:
        """更新文档索引状态（供索引服务调用）"""
        result = await self.session.execute(
            select(KnowledgeDocument).where(KnowledgeDocument.id == document_id)
        )
        document = result.scalar_one_or_none()
        if not document:
            return

        document.status = status
        document.chunk_count = chunk_count
        if error_message:
            document.error_message = error_message

        await self.session.flush()
