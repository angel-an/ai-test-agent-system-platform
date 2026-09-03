"""
知识文档模型

定义知识文档元数据表结构
"""

from sqlalchemy import Enum as SQLEnum, ForeignKey, Integer, String, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin, UUIDMixin
import enum


class DocumentStatus(str, enum.Enum):
    """文档索引状态"""
    PENDING = "pending"       # 待处理
    PROCESSING = "processing" # 处理中
    INDEXED = "indexed"       # 已索引
    FAILED = "failed"         # 索引失败


class KnowledgeDocument(Base, UUIDMixin, TimestampMixin):
    """
    知识文档表

    存储知识文档元数据，实际文件存储在 MinIO
    """
    __tablename__ = "knowledge_documents"
    __table_args__ = {"comment": "知识文档表"}

    space_id: Mapped[UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("knowledge_spaces.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
        comment="所属知识空间 ID"
    )
    title: Mapped[str | None] = mapped_column(
        String(500),
        nullable=True,
        comment="文档标题"
    )
    file_name: Mapped[str] = mapped_column(
        String(500),
        nullable=False,
        comment="原始文件名"
    )
    file_path: Mapped[str] = mapped_column(
        String(1000),
        nullable=False,
        comment="MinIO 对象路径"
    )
    file_size: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
        comment="文件大小（字节）"
    )
    content_type: Mapped[str] = mapped_column(
        String(100),
        nullable=False,
        comment="文件 MIME 类型"
    )
    status: Mapped[DocumentStatus] = mapped_column(
        SQLEnum(DocumentStatus, values_callable=lambda x: [e.value for e in x]),
        default=DocumentStatus.PENDING,
        nullable=False,
        index=True,
        comment="索引状态"
    )
    chunk_count: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
        comment="切片数量"
    )
    error_message: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
        comment="索引失败错误信息"
    )
    created_by: Mapped[UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id"),
        nullable=False,
        comment="创建者 ID"
    )

    # 关系
    space: Mapped["KnowledgeSpace"] = relationship("KnowledgeSpace", back_populates="documents")
    chunks: Mapped[list["KnowledgeChunk"]] = relationship(
        "KnowledgeChunk",
        back_populates="document",
        cascade="all, delete-orphan"
    )

    def __repr__(self) -> str:
        return f"<KnowledgeDocument(id={self.id}, file_name={self.file_name}, status={self.status})>"
