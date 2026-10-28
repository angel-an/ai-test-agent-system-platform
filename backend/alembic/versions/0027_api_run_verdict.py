"""API 链路 run 级结论 verdict（2026-10-28）

Revision ID: 0027
Revises: 0026
Create Date: 2026-10-28

背景：web 链路已有 ``test_runs.verdict``（0025），API 链路的 ``api_test_runs``
一直没有等价字段——断言失败的 run 终态是 failed 而非 completed，但"脚本没跑 /
零结果"的 run 终态也是 failed，单看 status 分不清"用例失败"和"根本没跑出用例"。

新增 ``api_test_runs.verdict``，由用例级计数推导（与 0025 同口径、纯 SQL 复算）：

- ``empty``      total_tests = 0（含中断/环境失败/零结果的 run——**不是用例失败**）
- ``fail``       failed_tests > 0
- ``pass``       failed_tests = 0 且 passed_tests > 0 且 skipped_tests = 0
- ``unverified`` failed_tests = 0 但 passed_tests = 0 或存在 skipped

运行期推导统一走 ``app.repositories.api_test_repo.api_run_verdict``；
迁移回填与它同口径，两边必须同步改。降级只删本列。
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0027"
down_revision: Union[str, None] = "0026"

_VERDICT_SQL = """
CASE
    WHEN total_tests = 0 THEN 'empty'
    WHEN failed_tests > 0 THEN 'fail'
    WHEN passed_tests > 0 AND skipped_tests = 0 THEN 'pass'
    ELSE 'unverified'
END
"""


def upgrade() -> None:
    op.add_column(
        "api_test_runs",
        sa.Column(
            "verdict",
            sa.String(16),
            nullable=True,
            comment="run 结论 pass/fail/unverified/empty；判红看它，不看 status",
        ),
    )
    op.execute(f"UPDATE api_test_runs SET verdict = {_VERDICT_SQL}")


def downgrade() -> None:
    op.drop_column("api_test_runs", "verdict")
