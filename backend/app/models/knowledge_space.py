"""
知识空间模型

定义知识空间表结构，支持按项目/业务线/文件夹维度组织知识文档
"""

from sqlalchemy import ForeignKey, String, Text, JSON
from sqlalchemy.dialects.postgresql import UUID, JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin, UUIDMixin


class KnowledgeSpace(Base, UUIDMixin, TimestampMixin):
    """
    知识空间表

    存储知识空间信息，承载 project_identifier / business_line / folder_scope
    """
    __tablename__ = "knowledge_spaces"
    __table_args__ = {"comment": "知识空间表"}

    project_id: Mapped[UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("projects.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
        comment="所属项目 ID"
    )
    name: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
        comment="知识空间名称"
    )
    description: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
        comment="知识空间描述"
    )
    business_line: Mapped[str | None] = mapped_column(
        String(100),
        nullable=True,
        index=True,
        comment="业务线标识"
    )
    folder_scope: Mapped[list | None] = mapped_column(
        JSONB,
        nullable=True,
        default=list,
        comment="关联文件夹 ID 列表（JSONB）"
    )
    created_by: Mapped[UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id"),
        nullable=False,
        comment="创建者 ID"
    )

    # 关系
    project: Mapped["Project"] = relationship("Project", back_populates="knowledge_spaces")
    documents: Mapped[list["KnowledgeDocument"]] = relationship(
        "KnowledgeDocument",
        back_populates="space",
        cascade="all, delete-orphan"
    )
    chunks: Mapped[list["KnowledgeChunk"]] = relationship(
        "KnowledgeChunk",
        back_populates="space",
        cascade="all, delete-orphan"
    )

    def __repr__(self) -> str:
        return f"<KnowledgeSpace(id={self.id}, name={self.name}, project_id={self.project_id})>"
