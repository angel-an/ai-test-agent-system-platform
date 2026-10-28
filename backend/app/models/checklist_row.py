"""
Checklist 回归模型

存储「上线Checklist」结构化行与探针执行结果，支撑前端「上线Checklist」页面的
清单展示、执行结果视图与回写面板。
"""

from datetime import datetime
from uuid import uuid4

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin, UUIDMixin


class ChecklistRow(Base, UUIDMixin, TimestampMixin):
    """Checklist 行（与在线表格行锚定，row_id 项目内唯一）"""

    __tablename__ = "checklist_rows"
    __table_args__ = (
        UniqueConstraint("project_id", "row_id", name="uq_checklist_rows_project_row"),
        {"comment": "Checklist 行表"},
    )

    project_id: Mapped[UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("projects.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
        comment="所属项目 ID",
    )
    row_id: Mapped[str] = mapped_column(String(16), nullable=False, comment="行锚 CK-<md8>")
    source_row: Mapped[int | None] = mapped_column(Integer, comment="在线表格行号快照")
    title: Mapped[str] = mapped_column(String(120), default="", comment="标题 [主/子] 功能点")
    main_module: Mapped[str] = mapped_column(String(60), default="", comment="主模块")
    sub_module: Mapped[str] = mapped_column(String(60), default="", comment="子模块")
    feature: Mapped[str] = mapped_column(Text, default="", comment="功能点")
    expected: Mapped[str] = mapped_column(Text, default="", comment="预期结果")
    priority: Mapped[str] = mapped_column(String(8), default="", comment="优先级 P0/P1/P2")
    probe_kind: Mapped[str] = mapped_column(String(16), default="reachable", comment="探针类型")
    data_mode: Mapped[str] = mapped_column(String(16), default="readonly", comment="数据供给模式")
    content_md5: Mapped[str] = mapped_column(String(32), default="", comment="内容指纹（变更检测）")
    extra: Mapped[dict | None] = mapped_column(JSONB, comment="人工结果列等附加字段")

    # 最近一次执行结果（由 run-results 写入）
    latest_status: Mapped[str | None] = mapped_column(
        String(16), comment="最近行级结果 pass/fail/unverified"
    )
    latest_error: Mapped[str | None] = mapped_column(Text, comment="最近失败原因（人话）")
    latest_evidence: Mapped[str | None] = mapped_column(Text, comment="最近证据路径/链接")
    latest_consistency: Mapped[str | None] = mapped_column(String(60), comment="一致性断言说明")
    latest_run_id: Mapped[UUID | None] = mapped_column(
        UUID(as_uuid=True), comment="最近所属 ChecklistRun ID"
    )
    latest_run_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), comment="最近执行时间")


class ChecklistRun(Base, UUIDMixin, TimestampMixin):
    """Checklist 一次探针执行的整包结果"""

    __tablename__ = "checklist_runs"
    __table_args__ = {"comment": "Checklist 执行结果表"}

    project_id: Mapped[UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("projects.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
        comment="所属项目 ID",
    )
    run_verdict: Mapped[str] = mapped_column(String(16), default="empty", comment="run 级结论")
    profile_name: Mapped[str] = mapped_column(String(60), default="", comment="项目画像名")
    summary: Mapped[dict | None] = mapped_column(JSONB, comment="pass/fail/unverified 计数")
    skipped_flow: Mapped[int] = mapped_column(Integer, default=0, comment="暂缓的 flow 行数")
    report_url: Mapped[str | None] = mapped_column(Text, comment="在线报告链接")
    results: Mapped[dict | None] = mapped_column(JSONB, comment="按行明细整包")
