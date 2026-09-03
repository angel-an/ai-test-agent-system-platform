"""
检索日志模型

定义检索日志、命中、降级记录表结构
"""

from sqlalchemy import Boolean, Float, ForeignKey, Integer, String, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, UUIDMixin
from datetime import datetime
from sqlalchemy import DateTime, func


class KnowledgeRetrievalLog(Base, UUIDMixin):
    """
    检索日志表

    记录知识库检索请求、命中结果和降级情况
    """
    __tablename__ = "knowledge_retrieval_logs"
    __table_args__ = {"comment": "检索日志表"}

    space_id: Mapped[UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("knowledge_spaces.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
        comment="知识空间 ID"
    )
    project_id: Mapped[UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("projects.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
        comment="项目 ID"
    )
    query: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        comment="检索查询文本"
    )
    results_count: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
        comment="返回结果数量"
    )
    top_score: Mapped[float | None] = mapped_column(
        Float,
        nullable=True,
        comment="最高相似度分数"
    )
    degraded: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
        comment="是否触发降级"
    )
    degraded_reason: Mapped[str | None] = mapped_column(
        String(255),
        nullable=True,
        comment="降级原因"
    )
    response_time_ms: Mapped[int | None] = mapped_column(
        Integer,
        nullable=True,
        comment="响应时间（毫秒）"
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
        comment="创建时间"
    )

    def __repr__(self) -> str:
        return f"<KnowledgeRetrievalLog(id={self.id}, query={self.query[:50]}..., degraded={self.degraded})>"
