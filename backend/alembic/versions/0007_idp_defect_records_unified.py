"""extend idp_defect_records for unified defect registration

Revision ID: 0007_idp_defect_records_unified
Revises: 0006_idp_defect_records
Create Date: 2026-08-27 00:00:00

本迁移扩展 IDP 缺陷记录表，支持 API/Web/安全三类测试的统一缺陷登记。

迁移顺序：
1. 新增字段（可空）
2. 回填 source_run_id = test_run_id
3. 校验回填完成
4. 设非空约束
5. 创建索引和唯一约束
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "0007_idp_defect_records_unified"
down_revision: Union[str, None] = "0006_idp_defect_records"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # === 第 1 步：新增字段（全部可空，无默认值冲突） ===

    # 通用来源字段
    op.add_column(
        "idp_defect_records",
        sa.Column("source_type", sa.String(length=20), nullable=True),
    )
    op.add_column(
        "idp_defect_records",
        sa.Column("source_run_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.add_column(
        "idp_defect_records",
        sa.Column("source_case_id", postgresql.UUID(as_uuid=True), nullable=True),
    )

    # 校验和报告字段
    op.add_column(
        "idp_defect_records",
        sa.Column("verification_status", sa.String(length=20), nullable=True),
    )
    op.add_column(
        "idp_defect_records",
        sa.Column("verification_error", sa.Text(), nullable=True),
    )
    op.add_column(
        "idp_defect_records",
        sa.Column("report_url", sa.String(length=2048), nullable=True),
    )
    op.add_column(
        "idp_defect_records",
        sa.Column("written_back_at", sa.DateTime(timezone=True), nullable=True),
    )

    # === 第 2 步：扩展 create_status 长度 ===
    op.alter_column(
        "idp_defect_records",
        "create_status",
        type_=sa.String(length=30),
        existing_type=sa.String(length=20),
    )

    # === 第 3 步：回填数据 ===
    # source_type 默认为 api（历史记录均为 API 测试产生）
    op.execute("UPDATE idp_defect_records SET source_type = 'api' WHERE source_type IS NULL")
    # source_run_id 从 test_run_id 回填
    op.execute("UPDATE idp_defect_records SET source_run_id = test_run_id WHERE source_run_id IS NULL")

    # === 第 4 步：设非空约束 ===
    op.alter_column("idp_defect_records", "source_type", nullable=False)
    op.alter_column("idp_defect_records", "source_run_id", nullable=False)

    # === 第 5 步：修改 test_run_id 为可空（向后兼容） ===
    op.alter_column("idp_defect_records", "test_run_id", nullable=True)

    # === 第 6 步：创建新索引 ===
    op.create_index(
        "ix_idp_defect_records_source_run_id",
        "idp_defect_records",
        ["source_run_id"],
    )
    op.create_index(
        "ix_idp_defect_records_source_type",
        "idp_defect_records",
        ["source_type"],
    )

    # === 第 7 步：删除旧唯一约束，创建新唯一约束 ===
    # 注意：必须先确保 source_run_id 已正确回填且无重复
    op.drop_constraint("uq_idp_defect_run_project_fingerprint", "idp_defect_records", type_="unique")
    op.create_unique_constraint(
        "uq_idp_defect_source_run_project_fingerprint",
        "idp_defect_records",
        ["source_run_id", "source_project_key", "fingerprint"],
    )


def downgrade() -> None:
    """降级：明确拒绝执行，防止数据丢失。

    此迁移新增了 source_type/source_run_id 等核心字段， downgrade 会：
    1. 丢失 Web/安全测试记录（test_run_id 为 NULL）
    2. 丢失来源类型信息
    3. 破坏统一缺陷登记能力

    如需回退，请人工备份后执行反向操作，不要依赖自动 downgrade。
    """
    raise RuntimeError(
        "降级被拒绝：此迁移包含不可逆的结构变更（source_type/source_run_id 等）。"
        "如需回退，请先备份数据库，然后手动执行反向操作。"
    )
