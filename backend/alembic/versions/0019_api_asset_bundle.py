"""新增 api_document_bundles 等 7 张表 — 三文件联合导入统一资产包。

Revision ID: 0019
Revises: 0018
Create Date: 2026-09-12
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "0019"
down_revision: Union[str, None] = "0018"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """创建统一资产包 7 张表。"""
    # 1. 资产包主表
    op.create_table(
        "api_document_bundles",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("project_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("system_type", sa.String(length=50), nullable=True),
        sa.Column("status", sa.String(length=30), nullable=False, server_default="draft"),
        sa.Column("current_version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("parse_result", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("created_by", sa.String(length=255), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("project_id", "name", name="uq_bundle_project_name"),
    )
    op.create_index("ix_api_document_bundles_project_id", "api_document_bundles", ["project_id"])
    op.create_index("ix_api_document_bundles_status", "api_document_bundles", ["status"])

    # 2. 来源文件表
    op.create_table(
        "api_document_sources",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("bundle_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("source_type", sa.String(length=30), nullable=False),
        sa.Column("file_name", sa.String(length=500), nullable=False),
        sa.Column("file_hash", sa.String(length=64), nullable=False),
        sa.Column("file_size", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("attachment_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("parse_status", sa.String(length=20), nullable=False, server_default="pending"),
        sa.Column("parse_result", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("parse_error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["attachment_id"], ["attachments.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["bundle_id"], ["api_document_bundles.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("bundle_id", "source_type", name="uq_source_bundle_type"),
    )
    op.create_index("ix_api_document_sources_bundle_id", "api_document_sources", ["bundle_id"])
    op.create_index("ix_api_document_sources_file_hash", "api_document_sources", ["file_hash"])

    # 3. 统一接口契约表
    op.create_table(
        "api_interface_contracts",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("bundle_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("endpoint_key", sa.String(length=255), nullable=False),
        sa.Column("method", sa.String(length=10), nullable=False),
        sa.Column("path", sa.String(length=2048), nullable=False),
        sa.Column("normalized_path", sa.String(length=2048), nullable=False),
        sa.Column("name", sa.String(length=500), nullable=True),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("status", sa.String(length=30), nullable=False, server_default="pending"),
        sa.Column("request", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("response", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("auth", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("assertions", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("dependencies", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("pending_confirmations", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("source_refs", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("imported_endpoint_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("imported_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["bundle_id"], ["api_document_bundles.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["imported_endpoint_id"], ["api_endpoints.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("bundle_id", "method", "normalized_path", name="uq_contract_bundle_method_path"),
    )
    op.create_index("ix_api_interface_contracts_bundle_id", "api_interface_contracts", ["bundle_id"])
    op.create_index("ix_api_interface_contracts_endpoint_key", "api_interface_contracts", ["endpoint_key"])
    op.create_index("ix_api_interface_contracts_status", "api_interface_contracts", ["status"])
    op.create_index("ix_api_interface_contracts_imported_endpoint_id", "api_interface_contracts", ["imported_endpoint_id"])

    # 4. 请求模板表
    op.create_table(
        "api_request_templates",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("contract_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("name", sa.String(length=500), nullable=False),
        sa.Column("headers", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("query", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("body", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("pre_request_script", sa.Text(), nullable=True),
        sa.Column("test_script", sa.Text(), nullable=True),
        sa.Column("source_location", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["contract_id"], ["api_interface_contracts.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_api_request_templates_contract_id", "api_request_templates", ["contract_id"])

    # 5. 环境变量表
    op.create_table(
        "api_variables",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("bundle_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("value_ref", sa.String(length=500), nullable=True),
        sa.Column("is_secret", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("environment", sa.String(length=50), nullable=True),
        sa.Column("source_type", sa.String(length=30), nullable=False, server_default="postman_environment"),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["bundle_id"], ["api_document_bundles.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("bundle_id", "name", name="uq_variable_bundle_name"),
    )
    op.create_index("ix_api_variables_bundle_id", "api_variables", ["bundle_id"])

    # 6. 断言表
    op.create_table(
        "api_assertions",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("contract_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("source_type", sa.String(length=30), nullable=False),
        sa.Column("assert_type", sa.String(length=50), nullable=False),
        sa.Column("expression", sa.Text(), nullable=False),
        sa.Column("expected", sa.Text(), nullable=True),
        sa.Column("source_location", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["contract_id"], ["api_interface_contracts.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_api_assertions_contract_id", "api_assertions", ["contract_id"])

    # 7. 依赖表
    op.create_table(
        "api_dependencies",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("bundle_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("from_contract_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("to_contract_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("variable", sa.String(length=255), nullable=False),
        sa.Column("extract_expr", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["bundle_id"], ["api_document_bundles.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["from_contract_id"], ["api_interface_contracts.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["to_contract_id"], ["api_interface_contracts.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_api_dependencies_bundle_id", "api_dependencies", ["bundle_id"])
    op.create_index("ix_api_dependencies_from_contract_id", "api_dependencies", ["from_contract_id"])

    # 8. 冲突表
    op.create_table(
        "api_conflicts",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("bundle_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("contract_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("conflict_type", sa.String(length=50), nullable=False),
        sa.Column("severity", sa.String(length=10), nullable=False, server_default="medium"),
        sa.Column("status", sa.String(length=20), nullable=False, server_default="open"),
        sa.Column("message", sa.Text(), nullable=False),
        sa.Column("details", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("source_refs", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["bundle_id"], ["api_document_bundles.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["contract_id"], ["api_interface_contracts.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_api_conflicts_bundle_id", "api_conflicts", ["bundle_id"])
    op.create_index("ix_api_conflicts_contract_id", "api_conflicts", ["contract_id"])
    op.create_index("ix_api_conflicts_severity", "api_conflicts", ["severity"])
    op.create_index("ix_api_conflicts_status", "api_conflicts", ["status"])


def downgrade() -> None:
    """删除统一资产包 7 张表（先删子表）。"""
    op.drop_index("ix_api_conflicts_status", table_name="api_conflicts")
    op.drop_index("ix_api_conflicts_severity", table_name="api_conflicts")
    op.drop_index("ix_api_conflicts_contract_id", table_name="api_conflicts")
    op.drop_index("ix_api_conflicts_bundle_id", table_name="api_conflicts")
    op.drop_table("api_conflicts")

    op.drop_index("ix_api_dependencies_from_contract_id", table_name="api_dependencies")
    op.drop_index("ix_api_dependencies_bundle_id", table_name="api_dependencies")
    op.drop_table("api_dependencies")

    op.drop_index("ix_api_assertions_contract_id", table_name="api_assertions")
    op.drop_table("api_assertions")

    op.drop_index("ix_api_variables_bundle_id", table_name="api_variables")
    op.drop_table("api_variables")

    op.drop_index("ix_api_request_templates_contract_id", table_name="api_request_templates")
    op.drop_table("api_request_templates")

    op.drop_index("ix_api_interface_contracts_imported_endpoint_id", table_name="api_interface_contracts")
    op.drop_index("ix_api_interface_contracts_status", table_name="api_interface_contracts")
    op.drop_index("ix_api_interface_contracts_endpoint_key", table_name="api_interface_contracts")
    op.drop_index("ix_api_interface_contracts_bundle_id", table_name="api_interface_contracts")
    op.drop_table("api_interface_contracts")

    op.drop_index("ix_api_document_sources_file_hash", table_name="api_document_sources")
    op.drop_index("ix_api_document_sources_bundle_id", table_name="api_document_sources")
    op.drop_table("api_document_sources")

    op.drop_index("ix_api_document_bundles_status", table_name="api_document_bundles")
    op.drop_index("ix_api_document_bundles_project_id", table_name="api_document_bundles")
    op.drop_table("api_document_bundles")
