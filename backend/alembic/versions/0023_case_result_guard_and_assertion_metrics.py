"""N2 写入来源/轮次 + R5 断言指标模型（2026-10-17）

Revision ID: 0023
Revises: 0022
Create Date: 2026-10-17

两个改动都只能靠补列落地（``Base.metadata.create_all`` 不会给已存在的表补列）：

1. **N2 双写收口**：``test_run_test_cases`` 增加
   ``latest_job_id / latest_execution_order / latest_retry_count / latest_writer``，
   用于判定"本次写入是不是该用例在该 run 内的最新一轮"以及"同一轮里谁更权威"。
   历史行按 ``-1 / 0 / NULL`` 处理，视作无轮次信息，不阻断写入。

2. **R5 指标模型**：``test_runs`` 增加两类 × 5 指标（``case_attributed_*`` /
   ``config_only_*``）与 ``unattributed_checks``。两类**分别计数**，因为共享一个
   ``passed_count`` 无法同时给出"用例通过率"与"配置检查通过率"。

降级：只删除本迁移补的列。
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0023"
down_revision: Union[str, None] = "0022"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_RUN_CASE_COLUMNS: list[tuple[str, sa.Column]] = [
    ("latest_job_id", sa.Column("latest_job_id", sa.UUID(as_uuid=True), nullable=True)),
    (
        "latest_execution_order",
        sa.Column("latest_execution_order", sa.Integer(), nullable=False, server_default="-1"),
    ),
    (
        "latest_retry_count",
        sa.Column("latest_retry_count", sa.Integer(), nullable=False, server_default="0"),
    ),
    ("latest_writer", sa.Column("latest_writer", sa.String(length=20), nullable=True)),
]

_METRIC_KEYS = ("total", "pass", "fail", "unverified", "blocked")
_RUN_METRIC_COLUMNS: list[tuple[str, sa.Column]] = [
    (
        f"{category}_{key}",
        sa.Column(f"{category}_{key}", sa.Integer(), nullable=False, server_default="0"),
    )
    for category in ("case_attributed", "config_only")
    for key in _METRIC_KEYS
] + [
    (
        "unattributed_checks",
        sa.Column("unattributed_checks", sa.Integer(), nullable=False, server_default="0"),
    )
]


def _column_exists(conn, table: str, column: str) -> bool:
    return bool(
        conn.execute(
            sa.text(
                "SELECT EXISTS (SELECT 1 FROM information_schema.columns "
                "WHERE table_name = :t AND column_name = :c)"
            ),
            {"t": table, "c": column},
        ).scalar()
    )


def upgrade() -> None:
    conn = op.get_bind()
    for name, column in _RUN_CASE_COLUMNS:
        if not _column_exists(conn, "test_run_test_cases", name):
            op.add_column("test_run_test_cases", column)
    for name, column in _RUN_METRIC_COLUMNS:
        if not _column_exists(conn, "test_runs", name):
            op.add_column("test_runs", column)


def downgrade() -> None:
    conn = op.get_bind()
    for name, _column in reversed(_RUN_METRIC_COLUMNS):
        if _column_exists(conn, "test_runs", name):
            op.drop_column("test_runs", name)
    for name, _column in reversed(_RUN_CASE_COLUMNS):
        if _column_exists(conn, "test_run_test_cases", name):
            op.drop_column("test_run_test_cases", name)
