"""新增 test_run_artifacts 表 — 运行产物追踪与执行治理。

Revision ID: 0017
Revises: 0016
Create Date: 2026-09-07
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "0017"
down_revision: Union[str, None] = "0016_web_compiled_assertions"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """创建 test_run_artifacts 表。"""
    op.create_table(
        "test_run_artifacts",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("project_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("test_case_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("scenario_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("run_id", sa.String(length=100), nullable=False),
        sa.Column("artifact_type", sa.String(length=50), nullable=False),
        sa.Column("artifact_path", sa.Text(), nullable=False),
        sa.Column("artifact_version", sa.String(length=50), nullable=True),
        sa.Column("expected_sha256", sa.String(length=64), nullable=True),
        sa.Column("actual_sha256", sa.String(length=64), nullable=True),
        sa.Column("status", sa.String(length=20), nullable=False, server_default="draft"),
        sa.Column("mode", sa.String(length=10), nullable=False, server_default="safe"),
        sa.Column("executed_at", sa.DateTime(), nullable=True),
        sa.Column("completed_at", sa.DateTime(), nullable=True),
        sa.Column("duration_ms", sa.Integer(), nullable=True),
        sa.Column("blocked_reason", sa.Text(), nullable=True),
        sa.Column("blocked_reason_code", sa.String(length=50), nullable=True),
        sa.Column("metadata", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["test_case_id"], ["test_cases.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["scenario_id"], ["test_scenarios.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )

    # 创建索引
    op.create_index("ix_test_run_artifacts_project_id", "test_run_artifacts", ["project_id"])
    op.create_index("ix_test_run_artifacts_run_id", "test_run_artifacts", ["run_id"])
    op.create_index("ix_test_run_artifacts_status", "test_run_artifacts", ["status"])
    op.create_index("ix_test_run_artifacts_artifact_type", "test_run_artifacts", ["artifact_type"])
    op.create_index("ix_test_run_artifacts_test_case_id", "test_run_artifacts", ["test_case_id"])
    op.create_index("ix_test_run_artifacts_scenario_id", "test_run_artifacts", ["scenario_id"])

    # 复合索引：项目 + run_id（用于快速查找同一项目的运行）
    op.create_index(
        "ix_test_run_artifacts_project_run",
        "test_run_artifacts",
        ["project_id", "run_id"],
    )


def downgrade() -> None:
    """删除 test_run_artifacts 表。"""
    op.drop_index("ix_test_run_artifacts_project_run", table_name="test_run_artifacts")
    op.drop_index("ix_test_run_artifacts_scenario_id", table_name="test_run_artifacts")
    op.drop_index("ix_test_run_artifacts_test_case_id", table_name="test_run_artifacts")
    op.drop_index("ix_test_run_artifacts_artifact_type", table_name="test_run_artifacts")
    op.drop_index("ix_test_run_artifacts_status", table_name="test_run_artifacts")
    op.drop_index("ix_test_run_artifacts_run_id", table_name="test_run_artifacts")
    op.drop_index("ix_test_run_artifacts_project_id", table_name="test_run_artifacts")
    op.drop_table("test_run_artifacts")
