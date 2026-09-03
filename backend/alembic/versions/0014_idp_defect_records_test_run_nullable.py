"""make idp_defect_records.test_run_id nullable (rev51-fix)

Revision ID: 0014_idp_test_run_nullable
Revises: 0013_idp_defect_records_backfill
Create Date: 2026-09-02 00:00:00

模型 IDPDefectRecord.test_run_id 为可空（Mapped[UUID | None]），统一登记以
source_run_id 为主键来源（Web/安全记录 test_run_id 为 NULL）。本地 PG 表仍
NOT NULL（0007 的 alter 未应用），导致 Web 失败登记 NotNullViolation →
except 链触发 MissingGreenlet，掩盖真实 run 结果。本迁移对齐模型。
"""

from typing import Sequence, Union

from alembic import op

revision: str = "0014_idp_test_run_nullable"
down_revision: Union[str, None] = "0013_idp_defect_records_backfill"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("ALTER TABLE idp_defect_records ALTER COLUMN test_run_id DROP NOT NULL")


def downgrade() -> None:
    raise RuntimeError("降级被拒绝：恢复 NOT NULL 会重新导致 Web/安全登记失败。")
