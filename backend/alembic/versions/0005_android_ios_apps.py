"""add android and ios app tables

Revision ID: 0005_android_ios_apps
Revises: 0004_security_tests
Create Date: 2026-06-22 00:00:00

本迁移新增 Android 和 iOS 测试管理所需的四张核心表：

- android_apps: Android App 功能表
- android_sub_functions: Android 子功能表
- ios_apps: iOS App 功能表
- ios_sub_functions: iOS 子功能表
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# fmt: off

revision: str = "0005_android_ios_apps"
down_revision: Union[str, None] = "0004_security_tests"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ========== Android App 表 ==========
    op.create_table(
        "android_apps",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "project_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("projects.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "folder_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("folders.id", ondelete="CASCADE"),
            nullable=True,
        ),
        sa.Column("identifier", sa.String(length=50), nullable=False),
        sa.Column("display_name", sa.String(length=500), nullable=False),
        sa.Column("name", sa.String(length=500), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("app_package", sa.String(length=500), nullable=True),
        sa.Column("main_activity", sa.String(length=500), nullable=True),
        sa.Column("version", sa.String(length=100), nullable=True),
        sa.Column("device_udid", sa.String(length=200), nullable=True),
        sa.Column("business_module", sa.String(length=200), nullable=True),
        sa.Column("navigation", postgresql.JSONB(), nullable=True),
        sa.Column("pages", postgresql.JSONB(), nullable=True, server_default="[]"),
        sa.Column("tags", postgresql.JSONB(), nullable=True, server_default="[]"),
        sa.Column("custom_config", postgresql.JSONB(), nullable=True),
        sa.Column("total_sub_functions", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("total_test_cases", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("total_test_runs", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("last_run_status", sa.String(length=50), nullable=True),
        sa.Column("sort_order", sa.Integer(), nullable=False, server_default="0"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("identifier"),
        sa.Index("ix_android_apps_project_id", "project_id"),
        sa.Index("ix_android_apps_folder_id", "folder_id"),
        sa.Index("ix_android_apps_identifier", "identifier"),
    )

    # ========== Android SubFunction 表 ==========
    op.create_table(
        "android_sub_functions",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "project_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("projects.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "app_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("android_apps.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "folder_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("folders.id", ondelete="CASCADE"),
            nullable=True,
        ),
        sa.Column("identifier", sa.String(length=50), nullable=False),
        sa.Column("display_name", sa.String(length=500), nullable=False),
        sa.Column("name", sa.String(length=500), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("test_type", sa.String(length=50), nullable=False, server_default="functional"),
        sa.Column("target_pages", postgresql.JSONB(), nullable=True, server_default="[]"),
        sa.Column("test_scenario", sa.Text(), nullable=True),
        sa.Column("test_data", postgresql.JSONB(), nullable=True),
        sa.Column("expected_results", postgresql.JSONB(), nullable=True, server_default="[]"),
        sa.Column("priority", sa.String(length=20), nullable=False, server_default="medium"),
        sa.Column("tags", postgresql.JSONB(), nullable=True, server_default="[]"),
        sa.Column("custom_config", postgresql.JSONB(), nullable=True),
        sa.Column("total_test_cases", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("total_test_runs", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("last_run_status", sa.String(length=50), nullable=True),
        sa.Column("sort_order", sa.Integer(), nullable=False, server_default="0"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("identifier"),
        sa.Index("ix_android_sub_functions_project_id", "project_id"),
        sa.Index("ix_android_sub_functions_app_id", "app_id"),
        sa.Index("ix_android_sub_functions_folder_id", "folder_id"),
        sa.Index("ix_android_sub_functions_identifier", "identifier"),
    )

    # ========== iOS App 表 ==========
    op.create_table(
        "ios_apps",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "project_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("projects.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "folder_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("folders.id", ondelete="CASCADE"),
            nullable=True,
        ),
        sa.Column("identifier", sa.String(length=50), nullable=False),
        sa.Column("display_name", sa.String(length=500), nullable=False),
        sa.Column("name", sa.String(length=500), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("bundle_id", sa.String(length=500), nullable=True),
        sa.Column("main_activity", sa.String(length=500), nullable=True),
        sa.Column("version", sa.String(length=100), nullable=True),
        sa.Column("device_udid", sa.String(length=200), nullable=True),
        sa.Column("device_type", sa.String(length=50), nullable=True, server_default="physical"),
        sa.Column("wda_url", sa.String(length=500), nullable=True),
        sa.Column("business_module", sa.String(length=200), nullable=True),
        sa.Column("navigation", postgresql.JSONB(), nullable=True),
        sa.Column("pages", postgresql.JSONB(), nullable=True, server_default="[]"),
        sa.Column("tags", postgresql.JSONB(), nullable=True, server_default="[]"),
        sa.Column("custom_config", postgresql.JSONB(), nullable=True),
        sa.Column("total_sub_functions", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("total_test_cases", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("total_test_runs", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("last_run_status", sa.String(length=50), nullable=True),
        sa.Column("sort_order", sa.Integer(), nullable=False, server_default="0"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("identifier"),
        sa.Index("ix_ios_apps_project_id", "project_id"),
        sa.Index("ix_ios_apps_folder_id", "folder_id"),
        sa.Index("ix_ios_apps_identifier", "identifier"),
    )

    # ========== iOS SubFunction 表 ==========
    op.create_table(
        "ios_sub_functions",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "project_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("projects.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "app_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("ios_apps.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "folder_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("folders.id", ondelete="CASCADE"),
            nullable=True,
        ),
        sa.Column("identifier", sa.String(length=50), nullable=False),
        sa.Column("display_name", sa.String(length=500), nullable=False),
        sa.Column("name", sa.String(length=500), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("test_type", sa.String(length=50), nullable=False, server_default="functional"),
        sa.Column("target_pages", postgresql.JSONB(), nullable=True, server_default="[]"),
        sa.Column("test_scenario", sa.Text(), nullable=True),
        sa.Column("test_data", postgresql.JSONB(), nullable=True),
        sa.Column("expected_results", postgresql.JSONB(), nullable=True, server_default="[]"),
        sa.Column("priority", sa.String(length=20), nullable=False, server_default="medium"),
        sa.Column("tags", postgresql.JSONB(), nullable=True, server_default="[]"),
        sa.Column("custom_config", postgresql.JSONB(), nullable=True),
        sa.Column("total_test_cases", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("total_test_runs", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("last_run_status", sa.String(length=50), nullable=True),
        sa.Column("sort_order", sa.Integer(), nullable=False, server_default="0"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("identifier"),
        sa.Index("ix_ios_sub_functions_project_id", "project_id"),
        sa.Index("ix_ios_sub_functions_app_id", "app_id"),
        sa.Index("ix_ios_sub_functions_folder_id", "folder_id"),
        sa.Index("ix_ios_sub_functions_identifier", "identifier"),
    )


def downgrade() -> None:
    op.drop_table("ios_sub_functions")
    op.drop_table("ios_apps")
    op.drop_table("android_sub_functions")
    op.drop_table("android_apps")
