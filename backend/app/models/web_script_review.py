"""脚本自愈评审队列（执行治理层 P0-2 + rev55 审批流）。

执行失败（脚本自评 failed / 授权拒绝 / 运行异常）时入队，供评审修复循环处理：
- pending：待评审；
- repairing：修复中（agent 生成新 proposed 版本）；
- pending_approval：修复验证通过、待**审批**（proposed 已保存但**不自动发布**
  ——rev55：修复后脚本不直接覆盖生效，先落待审批，人工审批通过才发布）；
- passed：审批通过并发布 effective（旧版本降 old）；
- rejected：审批拒绝（proposed 保留为审计，不发布）；
- blocked：连续失败达上限，人工介入。
"""

from __future__ import annotations

from uuid import UUID, uuid4

from sqlalchemy import ForeignKey, String, Text, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin


class WebScriptReview(Base, TimestampMixin):
    """Web 脚本自愈评审任务。"""

    __tablename__ = "web_script_reviews"
    __table_args__ = {"comment": "Web 脚本自愈评审队列（执行治理层 P0-2）"}

    id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), primary_key=True, default=uuid4, comment="主键 ID"
    )
    sub_function_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), nullable=False, index=True, comment="子功能 ID"
    )
    attachment_id: Mapped[UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("attachments.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
        comment="关联脚本附件（当前失败版本）",
    )
    status: Mapped[str] = mapped_column(
        String(20), default="pending", nullable=False, index=True,
        comment="评审状态：pending/repairing/passed/blocked",
    )
    error_summary: Mapped[str | None] = mapped_column(
        Text, nullable=True, comment="失败摘要（stdout/stderr/自评摘要）"
    )
    retry_count: Mapped[int] = mapped_column(
        default=0, nullable=False, comment="已修复重试次数"
    )
    # rev55（审批流）：本次修复生成的 proposed 附件（验证通过、待审批发布锚点）
    proposed_attachment_id: Mapped[UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("attachments.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
        comment="修复生成并验证通过的 proposed 附件（审批通过后发布为 effective）",
    )
    # rev55：修复版本号（同一子功能累计修复版本，供审计"第几版修复被审批/拒绝"）
    version_no: Mapped[int] = mapped_column(
        default=0, nullable=False, comment="修复版本号（0=初始失败，1 起为修复版本）"
    )
