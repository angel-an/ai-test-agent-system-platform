"""add security test tables

Revision ID: 0004_security_tests
Revises: 0003_script_job_logs
Create Date: 2026-06-18 00:00:00

本迁移新增安全测试（渗透测试）管理所需的三张核心表：

- security_tests: 安全测试任务表
- security_vulnerabilities: 漏洞发现表
- security_reports: 渗透测试报告表
"""
"""
andan
"""


from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# fmt: off  MC80OmFIVnBZMlhscm9ua3VMazZXRXg2Tmc9PTo0YzY3MzE4MQ==

revision: str = "0004_security_tests"
down_revision: Union[str, None] = "0003_script_job_logs"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 1) 创建 security_tests 表
    op.create_table(
        "security_tests",
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
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("target", sa.String(length=500), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("status", sa.String(length=50), nullable=False, server_default="pending"),
        sa.Column("scan_config", postgresql.JSONB(), nullable=True),
        sa.Column("total_vulnerabilities", sa.Integer(), nullable=True, server_default="0"),
        sa.Column("critical_count", sa.Integer(), nullable=True, server_default="0"),
        sa.Column("high_count", sa.Integer(), nullable=True, server_default="0"),
        sa.Column("medium_count", sa.Integer(), nullable=True, server_default="0"),
        sa.Column("low_count", sa.Integer(), nullable=True, server_default="0"),
        sa.Column("info_count", sa.Integer(), nullable=True, server_default="0"),
        sa.Column("risk_score", sa.Float(), nullable=True),
        sa.Column("thread_id", sa.String(length=100), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("identifier"),
    )
    op.create_index("ix_security_tests_project_id", "security_tests", ["project_id"])
    op.create_index("ix_security_tests_folder_id", "security_tests", ["folder_id"])
    op.create_index("ix_security_tests_identifier", "security_tests", ["identifier"])

    # 2) 创建 security_vulnerabilities 表
    op.create_table(
        "security_vulnerabilities",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "security_test_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("security_tests.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("vuln_id", sa.String(length=50), nullable=False),
        sa.Column("title", sa.String(length=500), nullable=False),
        sa.Column("severity", sa.String(length=50), nullable=False),
        sa.Column("vuln_type", sa.String(length=100), nullable=True),
        sa.Column("affected_url", sa.String(length=500), nullable=True),
        sa.Column("parameter", sa.String(length=200), nullable=True),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("reproduction", sa.Text(), nullable=True),
        sa.Column("evidence", sa.Text(), nullable=True),
        sa.Column("remediation", sa.Text(), nullable=True),
        sa.Column("cvss_score", sa.Float(), nullable=True),
        sa.Column("status", sa.String(length=50), nullable=False, server_default="open"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_security_vulnerabilities_security_test_id",
        "security_vulnerabilities",
        ["security_test_id"],
    )

    # 3) 创建 security_reports 表
    op.create_table(
        "security_reports",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "security_test_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("security_tests.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("report_type", sa.String(length=50), nullable=False, server_default="full"),
        sa.Column("format", sa.String(length=50), nullable=False, server_default="markdown"),
        sa.Column("content", sa.Text(), nullable=True),
        sa.Column("file_path", sa.String(length=500), nullable=True),
        sa.Column("risk_score", sa.Float(), nullable=True),
        sa.Column("summary", postgresql.JSONB(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_security_reports_security_test_id",
        "security_reports",
        ["security_test_id"],
    )


def downgrade() -> None:
    # 3) 删除 security_reports
    op.drop_index("ix_security_reports_security_test_id", table_name="security_reports")
    op.drop_table("security_reports")

    # 2) 删除 security_vulnerabilities
    op.drop_index(
        "ix_security_vulnerabilities_security_test_id",
        table_name="security_vulnerabilities",
    )
    op.drop_table("security_vulnerabilities")

    # 1) 删除 security_tests
    op.drop_index("ix_security_tests_identifier", table_name="security_tests")
    op.drop_index("ix_security_tests_folder_id", table_name="security_tests")
    op.drop_index("ix_security_tests_project_id", table_name="security_tests")
    op.drop_table("security_tests")
