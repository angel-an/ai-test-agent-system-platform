"""add compiled assertions columns (rev56)

Revision ID: 0016_web_compiled_assertions
Revises: 0015_review_approval_flow
Create Date: 2026-09-02 00:00:00

Web 断言编译（rev56）：web_sub_functions 增加 compiled_assertions（expected_results
保存时编译的结构化可机检断言）与 assertion_mode（compiled/human_oracle/mixed）。
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "0016_web_compiled_assertions"
down_revision: Union[str, None] = "0015_review_approval_flow"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "web_sub_functions",
        sa.Column("compiled_assertions", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    )
    op.add_column(
        "web_sub_functions",
        sa.Column("assertion_mode", sa.String(length=20), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("web_sub_functions", "assertion_mode")
    op.drop_column("web_sub_functions", "compiled_assertions")
