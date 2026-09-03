"""add web_script_registry for script provenance (execution governance 2a)

Revision ID: 0008_web_script_registry
Revises: 0007_idp_defect_records_unified
Create Date: 2026-08-27 00:00:00

脚本来源注册表：save_web_test_script 同一事务内登记
(真实项目标识, attachment_id, script_hash)；execute_web_script 执行前按
真实项目 + 内容哈希校验。rev19：attachment_id 外键关联 attachments，
UniqueConstraint(attachment_id, script_hash) 防重复登记。
"""

from alembic import op
import sqlalchemy as sa

revision = "0008_web_script_registry"
down_revision = "0007_idp_defect_records_unified"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "web_script_registry",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("project_identifier", sa.String(100), nullable=False),
        sa.Column(
            "attachment_id",
            sa.Uuid(),
            sa.ForeignKey("attachments.id", ondelete="CASCADE"),
            nullable=False,
            unique=True,  # rev20：每附件一个当前哈希（upsert 语义）
        ),
        sa.Column("script_hash", sa.String(64), nullable=False),
        sa.Column("script_language", sa.String(32), nullable=True),
        sa.Column("script_format", sa.String(32), nullable=True),
        sa.Column("created_by", sa.String(64), nullable=True),
        sa.Index("ix_web_script_registry_project_identifier", "project_identifier"),
        sa.Index("ix_web_script_registry_script_hash", "script_hash"),
        comment="Web 测试脚本来源注册表（执行治理层 2a）",
    )


def downgrade() -> None:
    op.drop_table("web_script_registry")
