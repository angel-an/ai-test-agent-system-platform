"""backfill idp_defect_records unified columns (rev51-fix)

Revision ID: 0013_idp_defect_records_backfill
Revises: 0012_knowledge_base
Create Date: 2026-09-02 00:00:00

背景（真实 E2E 暴露）：本地 PG 的 idp_defect_records 表停留在 0006 旧结构
（alembic_version 已 stamp 到 0012，但 0007 的加列从未真正执行——表缺
source_type/source_run_id/source_case_id/verification_status/verification_error/
report_url/written_back_at），导致失败登记 INSERT 报 UndefinedColumnError。

本迁移**幂等补列**（IF NOT EXISTS），与模型 IDPDefectRecord 对齐：
- source_type/source_run_id 非空（source_run_id 从 test_run_id 回填）；
- 其余列可空；补索引。
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "0013_idp_defect_records_backfill"
down_revision: Union[str, None] = "0012_knowledge_base"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # === 幂等补列（0007 应加而未加的列） ===
    op.execute("ALTER TABLE idp_defect_records ADD COLUMN IF NOT EXISTS source_type VARCHAR(20)")
    op.execute("ALTER TABLE idp_defect_records ADD COLUMN IF NOT EXISTS source_run_id UUID")
    op.execute("ALTER TABLE idp_defect_records ADD COLUMN IF NOT EXISTS source_case_id UUID")
    op.execute("ALTER TABLE idp_defect_records ADD COLUMN IF NOT EXISTS verification_status VARCHAR(20)")
    op.execute("ALTER TABLE idp_defect_records ADD COLUMN IF NOT EXISTS verification_error TEXT")
    op.execute("ALTER TABLE idp_defect_records ADD COLUMN IF NOT EXISTS report_url VARCHAR(2048)")
    op.execute("ALTER TABLE idp_defect_records ADD COLUMN IF NOT EXISTS written_back_at TIMESTAMP WITH TIME ZONE")

    # === 回填 + 非空约束 ===
    op.execute("UPDATE idp_defect_records SET source_type = 'api' WHERE source_type IS NULL")
    op.execute("UPDATE idp_defect_records SET source_run_id = test_run_id WHERE source_run_id IS NULL")
    op.execute("ALTER TABLE idp_defect_records ALTER COLUMN source_type SET NOT NULL")
    # source_run_id 非空：无 test_run_id 的历史行用零 UUID 兜底（防御）
    op.execute(
        "UPDATE idp_defect_records SET source_run_id = '00000000-0000-0000-0000-000000000000' "
        "WHERE source_run_id IS NULL"
    )
    op.execute("ALTER TABLE idp_defect_records ALTER COLUMN source_run_id SET NOT NULL")

    # === 索引（幂等） ===
    op.execute("CREATE INDEX IF NOT EXISTS ix_idp_defect_records_source_run_id "
               "ON idp_defect_records (source_run_id)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_idp_defect_records_source_type "
               "ON idp_defect_records (source_type)")


def downgrade() -> None:
    raise RuntimeError(
        "降级被拒绝：0013 修复表结构与模型一致，回退会重新引入 UndefinedColumnError。"
    )
