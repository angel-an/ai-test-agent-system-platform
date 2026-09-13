"""测试用例追踪字段 — 需求/功能点/验收标准关联。

Revision ID: 0020
Revises: 0019
Create Date: 2026-09-14

阶段二：为 test_cases 增加 requirement_id / feature_id / acceptance_id /
source_location 四个可空字段，用于「需求 → 功能点 → 测试点 → 测试用例 → 执行结果」
的稳定追踪。

兼容性说明：
- 全部 nullable，历史用例不受影响，无需一次性迁移；
- 新生成用例由生成链路保证写入 feature_id。
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = "0020"
down_revision: Union[str, None] = "0019"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """新增追踪字段。"""
    op.add_column(
        "test_cases",
        sa.Column("requirement_id", sa.String(length=64), nullable=True,
                  comment="需求编号"),
    )
    op.add_column(
        "test_cases",
        sa.Column("feature_id", sa.String(length=32), nullable=True,
                  comment="功能点编号，如 FP-001"),
    )
    op.add_column(
        "test_cases",
        sa.Column("acceptance_id", sa.String(length=64), nullable=True,
                  comment="验收标准编号，如 FP-001-AC-01"),
    )
    op.add_column(
        "test_cases",
        sa.Column("source_location", sa.String(length=500), nullable=True,
                  comment="需求来源位置"),
    )
    op.create_index("ix_test_cases_requirement_id", "test_cases", ["requirement_id"])
    op.create_index("ix_test_cases_feature_id", "test_cases", ["feature_id"])


def downgrade() -> None:
    """移除追踪字段。"""
    op.drop_index("ix_test_cases_feature_id", table_name="test_cases")
    op.drop_index("ix_test_cases_requirement_id", table_name="test_cases")
    op.drop_column("test_cases", "source_location")
    op.drop_column("test_cases", "acceptance_id")
    op.drop_column("test_cases", "feature_id")
    op.drop_column("test_cases", "requirement_id")
