"""N 加 run 级结论字段 verdict（2026-10-27）

Revision ID: 0025
Revises: 0024
Create Date: 2026-10-27

背景（2026-10-26 基线实测）：断言失败的 run 终态是 **REJECTED** 而非 DONE，
任何"按 run_state 统计通过/失败"的汇总都会把失败整批漏掉——TR-833 里 8 条
同规则用例全 FAILED，run_state=REJECTED，数 DONE 得 0。

新增 ``test_runs.verdict``（用例级结论，update_counts 每次收尾推导）：

- ``pass``       failed=0 且存在 PASSED，且无 SKIPPED/BLOCKED/NOT_EXECUTED
- ``fail``       failed > 0
- ``unverified`` failed=0 但存在不可定状态（或 passed=0）——不可当通过用
- ``empty``      未绑定用例（total=0）

与既有迁移口径一致：回填写成**纯 SQL 复算**，不调 Python，
避免"用未来规则改写过去"。降级只删本列。
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0025"
down_revision: Union[str, None] = "0024"

_VERDICT_SQL = """
CASE
    WHEN test_cases_count = 0 THEN 'empty'
    WHEN failed_count > 0 THEN 'fail'
    WHEN passed_count > 0
         AND (skipped_count + blocked_count + not_executed_count) = 0 THEN 'pass'
    ELSE 'unverified'
END
"""


def upgrade() -> None:
    op.add_column(
        "test_runs",
        sa.Column(
            "verdict",
            sa.String(16),
            nullable=True,
            comment="用例级结论 pass/fail/unverified/empty；判红看它，不看 run_state",
        ),
    )
    op.execute(f"UPDATE test_runs SET verdict = {_VERDICT_SQL}")


def downgrade() -> None:
    op.drop_column("test_runs", "verdict")
