"""定时运行模型"""

from sqlalchemy import Boolean, ForeignKey, Integer, String, Text
from sqlalchemy.dialects.postgresql import UUID, JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin, UUIDMixin


class ScheduledRun(Base, UUIDMixin, TimestampMixin):
    """定时运行配置表"""
    __tablename__ = "scheduled_runs"
    __table_args__ = {"comment": "API 测试定时运行配置表"}

    project_id: Mapped[UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("projects.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    identifier: Mapped[str] = mapped_column(
        String(50), unique=True, nullable=False, index=True,
        comment="标识符, 如 SR-1001"
    )
    name: Mapped[str] = mapped_column(String(500), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)

    # 要执行的 API 端点列表 (JSONB array of api_endpoint_ids)
    api_endpoint_ids: Mapped[list] = mapped_column(
        JSONB, nullable=False, default=list,
        comment="要执行的 API 端点 ID 列表"
    )

    # 执行配置
    execution_config: Mapped[dict | None] = mapped_column(
        JSONB, nullable=True, comment="执行配置 {base_url, timeout, etc.}"
    )

    # Cron 表达式
    cron_expression: Mapped[str] = mapped_column(
        String(100), nullable=False, comment="Cron 表达式, 如 0 2 * * *"
    )
    timezone: Mapped[str] = mapped_column(
        String(100), nullable=False, default="Asia/Shanghai"
    )

    # 状态
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    # 最近一次执行信息
    last_execution_id: Mapped[UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("scheduled_run_executions.id", ondelete="SET NULL"),
        nullable=True,
    )
    last_executed_at: Mapped[str | None] = mapped_column(String(50), nullable=True)

    # 关系
    project: Mapped["Project"] = relationship("Project")
    executions: Mapped[list["ScheduledRunExecution"]] = relationship(
        "ScheduledRunExecution",
        back_populates="scheduled_run",
        foreign_keys="ScheduledRunExecution.scheduled_run_id",
        cascade="all, delete-orphan",
        order_by="ScheduledRunExecution.created_at.desc()",
    )


class ScheduledRunExecution(Base, UUIDMixin, TimestampMixin):
    """定时运行执行记录表"""
    __tablename__ = "scheduled_run_executions"
    __table_args__ = {"comment": "定时运行执行记录表"}

    scheduled_run_id: Mapped[UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("scheduled_runs.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    project_id: Mapped[UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("projects.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    identifier: Mapped[str] = mapped_column(String(50), nullable=False)

    # 状态: pending / running / completed / failed
    status: Mapped[str] = mapped_column(
        String(50), default="pending", nullable=False, index=True
    )

    # 执行的 api_test_run_ids (JSONB)
    api_test_run_ids: Mapped[list] = mapped_column(
        JSONB, nullable=False, default=list,
        comment="本次执行产生的 api_test_run id 列表"
    )

    # 统计
    total_tests: Mapped[int] = mapped_column(Integer, default=0)
    passed_tests: Mapped[int] = mapped_column(Integer, default=0)
    failed_tests: Mapped[int] = mapped_column(Integer, default=0)

    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)

    # HTML 报告路径 (MinIO)
    report_path: Mapped[str | None] = mapped_column(String(2048), nullable=True)

    # 关系
    scheduled_run: Mapped["ScheduledRun"] = relationship(
        "ScheduledRun",
        back_populates="executions",
        foreign_keys=[scheduled_run_id],
    )
