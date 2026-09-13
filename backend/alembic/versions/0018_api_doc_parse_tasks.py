"""新增 api_doc_parse_tasks 表 — Excel/文档解析任务与两段式导入。

Revision ID: 0018
Revises: 0017
Create Date: 2026-09-11
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "0018"
down_revision: Union[str, None] = "0017"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """创建 api_doc_parse_tasks 表。"""
    op.create_table(
        "api_doc_parse_tasks",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("project_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("attachment_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("status", sa.String(length=20), nullable=False, server_default="pending"),
        sa.Column("doc_type", sa.String(length=30), nullable=False, server_default="excel_mx"),
        sa.Column("result", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("created_by", sa.String(length=255), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["attachment_id"], ["attachments.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )

    op.create_index("ix_api_doc_parse_tasks_project_id", "api_doc_parse_tasks", ["project_id"])
    op.create_index("ix_api_doc_parse_tasks_attachment_id", "api_doc_parse_tasks", ["attachment_id"])
    op.create_index("ix_api_doc_parse_tasks_status", "api_doc_parse_tasks", ["status"])


def downgrade() -> None:
    """删除 api_doc_parse_tasks 表。"""
    op.drop_index("ix_api_doc_parse_tasks_status", table_name="api_doc_parse_tasks")
    op.drop_index("ix_api_doc_parse_tasks_attachment_id", table_name="api_doc_parse_tasks")
    op.drop_index("ix_api_doc_parse_tasks_project_id", table_name="api_doc_parse_tasks")
    op.drop_table("api_doc_parse_tasks")
