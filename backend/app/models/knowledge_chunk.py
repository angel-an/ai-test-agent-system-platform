"""
知识切片模型

定义切片与向量索引元数据表结构

注意：本表依赖 pgvector 扩展，需要在 PostgreSQL 中先安装：
    CREATE EXTENSION IF NOT EXISTS vector;

如果 pgvector 不可用，embedding 字段可改为 text 类型存储向量字符串，
检索时回退到关键词匹配。
"""

from sqlalchemy import ForeignKey, Integer, Text
from sqlalchemy.dialects.postgresql import UUID, JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, UUIDMixin


class KnowledgeChunk(Base, UUIDMixin):
    """
    知识切片表

    存储文档切片内容和向量索引
    """
    __tablename__ = "knowledge_chunks"
    __table_args__ = {"comment": "知识切片表"}

    document_id: Mapped[UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("knowledge_documents.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
        comment="所属文档 ID"
    )
    space_id: Mapped[UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("knowledge_spaces.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
        comment="所属知识空间 ID"
    )
    content: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        comment="切片文本内容"
    )
    # 使用 text 类型存储 embedding 向量（逗号分隔的浮点数）
    # 这样不依赖 pgvector 扩展，同时保留向量数据
    # 后续如需使用 pgvector，可迁移为 vector 类型
    embedding: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
        comment="Embedding 向量（逗号分隔的浮点数）"
    )
    chunk_index: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
        comment="切片序号"
    )
    meta_data: Mapped[dict | None] = mapped_column(
        JSONB,
        nullable=True,
        default=dict,
        comment="切片元数据（如页码、章节等）"
    )

    # 关系
    document: Mapped["KnowledgeDocument"] = relationship("KnowledgeDocument", back_populates="chunks")
    space: Mapped["KnowledgeSpace"] = relationship("KnowledgeSpace", back_populates="chunks")

    def __repr__(self) -> str:
        return f"<KnowledgeChunk(id={self.id}, document_id={self.document_id}, index={self.chunk_index})>"
