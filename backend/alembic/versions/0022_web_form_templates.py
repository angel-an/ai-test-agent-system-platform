"""web_form_templates：项目级业务模板注册表（P6 数据化注册）。

Revision ID: 0022
Revises: 0021
Create Date: 2026-09-30

背景：应用启动时会跑 ``Base.metadata.create_all``（表可能已由它建好），
但**已存在的表不会被补列**。本迁移用幂等方式兜住两种情况：

1. 表不存在 → 按模型建表（含 ``extra_config``）；
2. 表已存在但缺 ``extra_config`` → 补列。

``extra_config`` 的必要性来自实测：只登记 ``fields`` 时，执行器在打开表单处
拿不到 ``form_button``（TR-163 真实运行崩溃），因此注册必须能承载**完整**定位器表。

降级：仅删除本迁移可能补的列/表（回滚到 0021 状态）。
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "0022"
down_revision: Union[str, None] = "0021"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

TABLE = "web_form_templates"


def _table_exists(conn) -> bool:
    return bool(
        conn.execute(
            sa.text("SELECT to_regclass('public.web_form_templates') IS NOT NULL")
        ).scalar()
    )


def _column_exists(conn, column: str) -> bool:
    return bool(
        conn.execute(
            sa.text(
                "SELECT EXISTS (SELECT 1 FROM information_schema.columns "
                "WHERE table_name = :t AND column_name = :c)"
            ),
            {"t": TABLE, "c": column},
        ).scalar()
    )


def upgrade() -> None:
    conn = op.get_bind()

    if not _table_exists(conn):
        op.create_table(
            TABLE,
            sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
            sa.Column("project_id", postgresql.UUID(as_uuid=True), nullable=False),
            sa.Column("sub_function_id", postgresql.UUID(as_uuid=True), nullable=True),
            sa.Column("template_key", sa.String(length=120), nullable=False),
            sa.Column("name", sa.String(length=255), nullable=False),
            sa.Column("fields", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
            sa.Column("extra_config", postgresql.JSONB(), nullable=True),
            sa.Column("source_ref", sa.String(length=255), nullable=True),
            sa.Column("description", sa.Text(), nullable=True),
            sa.Column("status", sa.String(length=20), nullable=False, server_default="active"),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
            sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
            sa.ForeignKeyConstraint(["sub_function_id"], ["web_sub_functions.id"], ondelete="SET NULL"),
            sa.UniqueConstraint("project_id", "template_key", name="uq_web_form_template_project_key"),
            comment="项目级业务模板注册（表单定位器数据化）",
        )
        op.create_index("ix_web_form_templates_project_id", TABLE, ["project_id"])
        op.create_index("ix_web_form_templates_sub_function_id", TABLE, ["sub_function_id"])
        op.create_index("ix_web_form_templates_template_key", TABLE, ["template_key"])
        op.create_index("ix_web_form_templates_status", TABLE, ["status"])
        return

    if not _column_exists(conn, "extra_config"):
        op.add_column(
            TABLE,
            sa.Column(
                "extra_config",
                postgresql.JSONB(),
                nullable=True,
                comment="定位器表的其余键（form_button/modal/…），与 fields 合并成完整定位器表",
            ),
        )


def downgrade() -> None:
    conn = op.get_bind()
    if not _table_exists(conn):
        return
    if _column_exists(conn, "extra_config"):
        op.drop_column(TABLE, "extra_config")
