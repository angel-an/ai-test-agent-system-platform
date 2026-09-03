"""add version_status to web_script_registry (execution governance P0-2)

Revision ID: 0009_web_script_versioning
Revises: 0008_web_script_registry
Create Date: 2026-08-31 00:00:00

脚本版本化（P0-2）：
- web_script_registry 增加 version_status 列：effective（当前生效，执行授权基准）/
  proposed（新提交待评审发布）/ old（历史版本）；
- 存量行默认 effective——现有脚本不失效（2a 授权语义不变）；
- attachment_id 继续唯一（每版本一个附件锚点，方案 A：每个版本对应独立 attachment）；
- 执行授权仅认 effective（authorize_script_execution 增加过滤，见代码）。
"""

from alembic import op
import sqlalchemy as sa

revision = "0009_web_script_versioning"
down_revision = "0008_web_script_registry"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "web_script_registry",
        sa.Column(
            "version_status",
            sa.String(20),
            nullable=False,
            server_default="effective",
            comment="脚本版本状态：effective(当前生效)/proposed(待发布)/old(历史)",
        ),
    )
    op.create_index(
        "ix_web_script_registry_version_status",
        "web_script_registry",
        ["version_status"],
    )
    # 存量数据：默认即 effective，无需显式 UPDATE


def downgrade() -> None:
    op.drop_index("ix_web_script_registry_version_status", table_name="web_script_registry")
    op.drop_column("web_script_registry", "version_status")
