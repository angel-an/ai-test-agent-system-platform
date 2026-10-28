"""Widen checklist_rows.latest_consistency to Text（2026-10-28）

Revision ID: 0029
Revises: 0028
Create Date: 2026-10-28

0028 把 latest_consistency 定义为 varchar(60)，原设计只放短断言（如
"C端与B端文案一致"）。miniprogram-probe-run 会把验证方式说明写进该列
（如"组件内点击 IDE 不支持，经数据断言+直达落地页验证"），实测超长发
StringDataRightTruncationError，run-results 整包回传 500。放宽为 Text。

模型侧 checklist_row.py 同步改（String(60) → Text）。
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0029"
down_revision: Union[str, None] = "0028"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.alter_column(
        "checklist_rows",
        "latest_consistency",
        type_=sa.Text(),
        existing_nullable=True,
    )


def downgrade() -> None:
    op.alter_column(
        "checklist_rows",
        "latest_consistency",
        type_=sa.String(60),
        existing_nullable=True,
    )
