"""API 文档解析任务模型

记录一次 Excel/文档解析的状态与结果，支持异步查询与"预览→确认导入"两段式流程。
"""

from sqlalchemy import ForeignKey, String, Text
from sqlalchemy.dialects.postgresql import UUID, JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin, UUIDMixin


class APIDocParseTask(Base, UUIDMixin, TimestampMixin):
    """API 文档解析任务表"""
    __tablename__ = "api_doc_parse_tasks"
    __table_args__ = {"comment": "API 文档解析任务表"}

    project_id: Mapped[UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("projects.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
        comment="所属项目 ID"
    )

    attachment_id: Mapped[UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("attachments.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
        comment="原始文档附件 ID"
    )

    # 解析状态: pending / completed / failed
    status: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        default="pending",
        index=True,
        comment="解析状态: pending, completed, failed"
    )

    # 文档类型: excel_mx / excel_generic / txt / docx
    doc_type: Mapped[str] = mapped_column(
        String(30),
        nullable=False,
        default="excel_mx",
        comment="文档类型标识"
    )

    # 解析结果（统一接口模型列表 + 公共规则），仅 status=completed 时有值
    result: Mapped[dict | None] = mapped_column(
        JSONB,
        nullable=True,
        comment="解析结果 {guide, interfaces:[统一模型...]}"
    )

    error: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
        comment="解析失败原因"
    )

    created_by: Mapped[str | None] = mapped_column(
        String(255),
        nullable=True,
        comment="发起人"
    )

    attachment: Mapped["Attachment | None"] = relationship(
        "Attachment",
        foreign_keys=[attachment_id]
    )
