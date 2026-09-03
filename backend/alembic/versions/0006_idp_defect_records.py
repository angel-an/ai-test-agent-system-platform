"""add idp_defect_records table

Revision ID: 0006_idp_defect_records
Revises: 0005_android_ios_apps
Create Date: 2026-08-26 00:00:00

本迁移创建 IDP 缺陷记录表，用于存储 API 测试失败时创建的 IDP 缺陷记录。
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "0006_idp_defect_records"
down_revision: Union[str, None] = "0005_android_ios_apps"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 创建 idp_defect_records 表
    op.create_table(
        "idp_defect_records",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "test_run_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("api_test_runs.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "test_case_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("test_cases.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("source_project_key", sa.String(length=50), nullable=False),
        sa.Column("idp_project_id", sa.Integer(), nullable=False),
        sa.Column("fingerprint", sa.String(length=500), nullable=False),
        sa.Column("idp_issue_id", sa.Integer(), nullable=True),
        sa.Column("idp_issue_key", sa.String(length=50), nullable=True),
        sa.Column("idp_issue_url", sa.String(length=2048), nullable=True),
        sa.Column("create_status", sa.String(length=20), nullable=False, server_default="pending"),
        sa.Column("reqid", sa.String(length=255), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("defect_title", sa.String(length=500), nullable=True),
        sa.Column("defect_priority", sa.String(length=50), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        comment="IDP 缺陷记录表",
    )

    # 创建唯一约束：同一测试运行 + 同一项目 + 同一指纹只能有一条记录
    op.create_unique_constraint(
        "uq_idp_defect_run_project_fingerprint",
        "idp_defect_records",
        ["test_run_id", "source_project_key", "fingerprint"],
    )
    op.create_index(
        "ix_idp_defect_records_test_run_id",
        "idp_defect_records",
        ["test_run_id"],
    )
    op.create_index(
        "ix_idp_defect_records_source_project_key",
        "idp_defect_records",
        ["source_project_key"],
    )
    op.create_index(
        "ix_idp_defect_records_create_status",
        "idp_defect_records",
        ["create_status"],
    )


def downgrade() -> None:
    # 删除唯一约束
    op.drop_constraint(
        "uq_idp_defect_run_project_fingerprint",
        "idp_defect_records",
        type_="unique",
    )
    # 删除索引
    op.drop_index("ix_idp_defect_records_create_status", table_name="idp_defect_records")
    op.drop_index("ix_idp_defect_records_source_project_key", table_name="idp_defect_records")
    op.drop_index("ix_idp_defect_records_test_run_id", table_name="idp_defect_records")
    op.drop_index("ix_idp_defect_records_fingerprint", table_name="idp_defect_records")

    # 删除表
    op.drop_table("idp_defect_records")
