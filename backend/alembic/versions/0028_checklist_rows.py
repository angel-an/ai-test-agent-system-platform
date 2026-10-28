"""Checklist 回归表（2026-10-28）

Revision ID: 0028
Revises: 0027
Create Date: 2026-10-28

新增 checklist_rows / checklist_runs 两张表，支撑「上线Checklist」页面的
清单展示、执行结果视图与回写面板。只加表，不动既有数据。

- checklist_rows : 行级锚定（project_id+row_id 唯一）与最近执行结果
- checklist_runs : 一次探针执行的整包结果（verdict/summary/明细）
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision: str = "0028"
down_revision: Union[str, None] = "0027"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "checklist_rows",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "project_id",
            UUID(as_uuid=True),
            sa.ForeignKey("projects.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column("row_id", sa.String(16), nullable=False),
        sa.Column("source_row", sa.Integer()),
        sa.Column("title", sa.String(120), server_default=""),
        sa.Column("main_module", sa.String(60), server_default=""),
        sa.Column("sub_module", sa.String(60), server_default=""),
        sa.Column("feature", sa.Text()),
        sa.Column("expected", sa.Text()),
        sa.Column("priority", sa.String(8), server_default=""),
        sa.Column("probe_kind", sa.String(16), server_default="reachable"),
        sa.Column("data_mode", sa.String(16), server_default="readonly"),
        sa.Column("content_md5", sa.String(32), server_default=""),
        sa.Column("extra", JSONB()),
        sa.Column("latest_status", sa.String(16)),
        sa.Column("latest_error", sa.Text()),
        sa.Column("latest_evidence", sa.Text()),
        sa.Column("latest_consistency", sa.String(60)),
        sa.Column("latest_run_id", UUID(as_uuid=True)),
        sa.Column("latest_run_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True)),
        sa.UniqueConstraint("project_id", "row_id", name="uq_checklist_rows_project_row"),
        comment="Checklist 行表",
    )
    # project_id 索引已由 Column(index=True) 在 create_table 内创建，勿重复建

    op.create_table(
        "checklist_runs",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "project_id",
            UUID(as_uuid=True),
            sa.ForeignKey("projects.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column("run_verdict", sa.String(16), server_default="empty"),
        sa.Column("profile_name", sa.String(60), server_default=""),
        sa.Column("summary", JSONB()),
        sa.Column("skipped_flow", sa.Integer(), server_default="0"),
        sa.Column("report_url", sa.Text()),
        sa.Column("results", JSONB()),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True)),
        comment="Checklist 执行结果表",
    )
    # project_id 索引已由 Column(index=True) 在 create_table 内创建，勿重复建


def downgrade() -> None:
    op.drop_table("checklist_runs")
    op.drop_table("checklist_rows")
