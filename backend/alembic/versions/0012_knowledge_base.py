"""add knowledge base tables (spaces, documents, chunks, retrieval logs)

Revision ID: 0012_knowledge_base
Revises: 0011_user_is_superuser
Create Date: 2026-09-02 00:00:00

创建知识库系统所需的 4 张表：
- knowledge_spaces: 知识空间
- knowledge_documents: 知识文档元数据
- knowledge_chunks: 文档切片与向量
- knowledge_retrieval_logs: 检索日志
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0012_knowledge_base"
down_revision = "0011_user_is_superuser"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 知识空间表
    op.create_table(
        "knowledge_spaces",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("project_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("business_line", sa.String(length=100), nullable=True),
        sa.Column("folder_scope", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("created_by", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["created_by"], ["users.id"]),
    )
    op.create_index("ix_knowledge_spaces_project_id", "knowledge_spaces", ["project_id"])
    op.create_index("ix_knowledge_spaces_business_line", "knowledge_spaces", ["business_line"])

    # 知识文档表
    op.create_table(
        "knowledge_documents",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("space_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("title", sa.String(length=500), nullable=True),
        sa.Column("file_name", sa.String(length=500), nullable=False),
        sa.Column("file_path", sa.String(length=1000), nullable=False),
        sa.Column("file_size", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("content_type", sa.String(length=100), nullable=False),
        sa.Column("status", sa.Enum("pending", "processing", "indexed", "failed", name="documentstatus"), nullable=False, server_default="pending"),
        sa.Column("chunk_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("created_by", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["space_id"], ["knowledge_spaces.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["created_by"], ["users.id"]),
    )
    op.create_index("ix_knowledge_documents_space_id", "knowledge_documents", ["space_id"])
    op.create_index("ix_knowledge_documents_status", "knowledge_documents", ["status"])

    # 知识切片表
    op.create_table(
        "knowledge_chunks",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("document_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("space_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("embedding", sa.Text(), nullable=True),
        sa.Column("chunk_index", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("meta_data", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["document_id"], ["knowledge_documents.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["space_id"], ["knowledge_spaces.id"], ondelete="CASCADE"),
    )
    op.create_index("ix_knowledge_chunks_document_id", "knowledge_chunks", ["document_id"])
    op.create_index("ix_knowledge_chunks_space_id", "knowledge_chunks", ["space_id"])

    # 检索日志表
    op.create_table(
        "knowledge_retrieval_logs",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("space_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("project_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("query", sa.Text(), nullable=False),
        sa.Column("results_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("top_score", sa.Float(), nullable=True),
        sa.Column("degraded", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("degraded_reason", sa.String(length=255), nullable=True),
        sa.Column("response_time_ms", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["space_id"], ["knowledge_spaces.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="SET NULL"),
    )
    op.create_index("ix_knowledge_retrieval_logs_space_id", "knowledge_retrieval_logs", ["space_id"])
    op.create_index("ix_knowledge_retrieval_logs_project_id", "knowledge_retrieval_logs", ["project_id"])


def downgrade() -> None:
    op.drop_index("ix_knowledge_retrieval_logs_project_id", table_name="knowledge_retrieval_logs")
    op.drop_index("ix_knowledge_retrieval_logs_space_id", table_name="knowledge_retrieval_logs")
    op.drop_table("knowledge_retrieval_logs")

    op.drop_index("ix_knowledge_chunks_space_id", table_name="knowledge_chunks")
    op.drop_index("ix_knowledge_chunks_document_id", table_name="knowledge_chunks")
    op.drop_table("knowledge_chunks")

    op.drop_index("ix_knowledge_documents_status", table_name="knowledge_documents")
    op.drop_index("ix_knowledge_documents_space_id", table_name="knowledge_documents")
    op.drop_table("knowledge_documents")

    op.drop_index("ix_knowledge_spaces_business_line", table_name="knowledge_spaces")
    op.drop_index("ix_knowledge_spaces_project_id", table_name="knowledge_spaces")
    op.drop_table("knowledge_spaces")

    op.execute("DROP TYPE IF EXISTS documentstatus")
