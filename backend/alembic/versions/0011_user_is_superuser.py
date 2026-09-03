"""add users.is_superuser (management API authorization, rev47)

Revision ID: 0011_user_is_superuser
Revises: 0010_web_script_reviews
Create Date: 2026-09-01 00:00:00

管理 API（如 Web 脚本评审修复循环触发）需超管权限；存量用户默认非超管
（server_default false），生产安全默认。
"""

from alembic import op
import sqlalchemy as sa

revision = "0011_user_is_superuser"
down_revision = "0010_web_script_reviews"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column("is_superuser", sa.Boolean(), nullable=False, server_default=sa.false()),
    )


def downgrade() -> None:
    op.drop_column("users", "is_superuser")
