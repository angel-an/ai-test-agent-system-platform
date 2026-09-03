"""
IDP 缺陷记录模型

定义 IDP 缺陷创建记录的表结构
"""

from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text, Index
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin, UUIDMixin


class IDPDefectRecord(Base, UUIDMixin, TimestampMixin):
    """
    IDP 缺陷记录表

    存储 API/Web/安全测试失败时创建的 IDP 缺陷记录。
    支持通用来源标识，不限于 API 测试。
    """
    __tablename__ = "idp_defect_records"
    __table_args__ = (
        Index("ix_idp_defect_records_fingerprint", "fingerprint"),
        Index("ix_idp_defect_records_source_run_id", "source_run_id"),
        Index("ix_idp_defect_records_source_project_key", "source_project_key"),
        Index("ix_idp_defect_records_create_status", "create_status"),
        Index("ix_idp_defect_records_source_type", "source_type"),
        {"comment": "IDP 缺陷记录表"},
    )

    # 来源类型（通用）
    source_type: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        default="api",
        comment="来源类型: api, web, security",
    )

    # 来源运行 ID（通用，替代原有的 test_run_id 外键）
    # 注意：不再做外键约束，因为不同测试类型的运行表不同
    source_run_id: Mapped[UUID] = mapped_column(
        UUID(as_uuid=True),
        nullable=False,
        comment="来源测试运行 ID",
    )
    source_case_id: Mapped[UUID | None] = mapped_column(
        UUID(as_uuid=True),
        nullable=True,
        index=True,
        comment="来源测试用例 ID",
    )

    # 向后兼容：保留 test_run_id / test_case_id 字段（API 测试专用）
    test_run_id: Mapped[UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("api_test_runs.id", ondelete="CASCADE"),
        nullable=True,
        comment="API 测试运行 ID（向后兼容）",
    )
    test_case_id: Mapped[UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("test_cases.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
        comment="测试用例 ID（向后兼容）",
    )

    # 项目映射
    source_project_key: Mapped[str] = mapped_column(
        String(50),
        nullable=False,
        comment="本地项目标识符，如 PR-2",
    )
    idp_project_id: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        comment="IDP 项目 ID",
    )

    # 缺陷指纹（用于去重）
    fingerprint: Mapped[str] = mapped_column(
        String(500),
        nullable=False,
        comment="缺陷指纹",
    )

    # IDP 缺陷信息
    idp_issue_id: Mapped[int | None] = mapped_column(
        Integer,
        nullable=True,
        comment="IDP Issue ID",
    )
    idp_issue_key: Mapped[str | None] = mapped_column(
        String(50),
        nullable=True,
        comment="IDP Issue Key，如 BUG-123",
    )
    idp_issue_url: Mapped[str | None] = mapped_column(
        String(2048),
        nullable=True,
        comment="IDP Issue URL",
    )

    # 创建状态（完整生命周期）
    create_status: Mapped[str] = mapped_column(
        String(30),
        nullable=False,
        default="pending",
        comment="创建状态: not_required, insufficient_evidence, pending, created, verified, written_back, sync_failed, duplicate, skipped",
    )

    # 校验状态
    verification_status: Mapped[str | None] = mapped_column(
        String(20),
        nullable=True,
        comment="校验状态: passed, failed",
    )
    verification_error: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
        comment="校验失败原因",
    )

    # 请求追踪
    reqid: Mapped[str | None] = mapped_column(
        String(255),
        nullable=True,
        comment="请求追踪 ID",
    )

    # 报告链接
    report_url: Mapped[str | None] = mapped_column(
        String(2048),
        nullable=True,
        comment="测试报告地址",
    )
    written_back_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        comment="报告回写时间",
    )

    # 错误信息
    error_message: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
        comment="错误信息（创建失败时记录）",
    )

    # 缺陷内容摘要（用于报告回写）
    defect_title: Mapped[str | None] = mapped_column(
        String(500),
        nullable=True,
        comment="缺陷标题",
    )
    defect_priority: Mapped[str | None] = mapped_column(
        String(50),
        nullable=True,
        comment="缺陷优先级",
    )

    def __repr__(self) -> str:
        return f"<IDPDefectRecord(id={self.id}, type={self.source_type}, status={self.create_status}, issue_key={self.idp_issue_key})>"
