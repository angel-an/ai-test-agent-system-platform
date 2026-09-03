"""add review approval flow columns (rev55)

Revision ID: 0015_review_approval_flow
Revises: 0014_idp_test_run_nullable
Create Date: 2026-09-02 00:00:00

自愈评审队列审批流（rev55）：修复后脚本**不直接覆盖生效**——验证通过后先落
pending_approval，审批通过才发布 effective，审批拒绝保留 proposed 为审计。
本迁移为 web_script_reviews 增加：
- proposed_attachment_id：本次修复生成并验证通过的 proposed 附件（审批发布锚点）；
- version_no：修复版本号（审计第几版修复被审批/拒绝）。
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0015_review_approval_flow"
down_revision: Union[str, None] = "0014_idp_test_run_nullable"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "web_script_reviews",
        sa.Column(
            "proposed_attachment_id",
            sa.Uuid(),
            sa.ForeignKey("attachments.id", ondelete="SET NULL"),
            nullable=True,
            index=True,
        ),
    )
    op.add_column(
        "web_script_reviews",
        sa.Column("version_no", sa.Integer(), nullable=False, server_default="0"),
    )


def downgrade() -> None:
    op.drop_column("web_script_reviews", "version_no")
    op.drop_column("web_script_reviews", "proposed_attachment_id")
