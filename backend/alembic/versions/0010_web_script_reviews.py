"""add web_script_reviews (execution governance P0-2 self-healing review queue)

Revision ID: 0010_web_script_reviews
Revises: 0009_web_script_versioning
Create Date: 2026-08-31 00:00:00

自愈评审队列：执行失败（脚本自评 failed 等）入队，供评审修复循环处理；
status: pending / repairing / passed / blocked。
"""

from alembic import op
import sqlalchemy as sa

revision = "0010_web_script_reviews"
down_revision = "0009_web_script_versioning"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "web_script_reviews",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("sub_function_id", sa.Uuid(), nullable=False, index=True),
        sa.Column(
            "attachment_id",
            sa.Uuid(),
            sa.ForeignKey("attachments.id", ondelete="SET NULL"),
            nullable=True,
            index=True,
        ),
        sa.Column("status", sa.String(20), nullable=False, server_default="pending", index=True),
        sa.Column("error_summary", sa.Text(), nullable=True),
        sa.Column("retry_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        comment="Web 脚本自愈评审队列（执行治理层 P0-2）",
    )


def downgrade() -> None:
    op.drop_table("web_script_reviews")
