"""N7 编译清单持久化：把 compiled_cases 抽成可查询列（2026-10-17）

Revision ID: 0024
Revises: 0023
Create Date: 2026-10-17

背景：编译产物一直存在 ``web_tests.generation_params.compiled_cases``（JSONB），
但"选执行范围"时无法用 SQL 直接筛可生成检查数，且**缺失**与**零**在 JSON 里不可分。

本迁移新增 5 列并**按同一套规则回填**（口径见
``app/services/web_test_compile_manifest.py``）：

- ``compile_status``：compiled / zero_checks / check_plan_missing / not_compiled
- ``compiled_check_count``：可执行检查数；**NULL=未知，0=确实零检查**
- ``compiled_skipped_count``：被跳过的断言数（NULL=未知）
- ``compiled_at``：编译时刻；回填值取 ``updated_at``（**近似值**，历史真值不可考）
- ``is_legacy_asset``：``web_agent`` / ``web-agent`` 生成的历史资产

回填刻意写成"纯 SQL 复算"而不是调 Python：迁移时的 ORM 模型可能已演进到未来版本，
用当时的业务代码回填历史数据会得到"用未来规则改写过去"的结果，事后无法判断
某条资产的列值到底来自当时编译还是后来重算。

降级：只删除本迁移补的列与索引。
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0024"
down_revision: Union[str, None] = "0023"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_NEW_COLUMNS: list[tuple[str, sa.Column]] = [
    ("compile_status", sa.Column("compile_status", sa.String(length=24), nullable=True)),
    ("compiled_check_count", sa.Column("compiled_check_count", sa.Integer(), nullable=True)),
    ("compiled_skipped_count", sa.Column("compiled_skipped_count", sa.Integer(), nullable=True)),
    (
        "compiled_at",
        sa.Column("compiled_at", sa.DateTime(timezone=True), nullable=True),
    ),
    (
        "is_legacy_asset",
        sa.Column(
            "is_legacy_asset",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
    ),
]

_INDEX_NAME = "ix_web_tests_compile_status"

#: 回填口径：与 derive_compile_manifest 逐条对齐，但用 SQL 表达
_BACKFILL_SQL = """
UPDATE web_tests SET
    -- 检查数：只有「有 compiled_cases 且该键是数字」才取数，否则 NULL（未知）
    compiled_check_count = CASE
        WHEN jsonb_typeof(generation_params -> 'compiled_cases' -> 'form_executable_checks') = 'number'
        THEN (generation_params -> 'compiled_cases' ->> 'form_executable_checks')::int
    END,
    compiled_skipped_count = CASE
        WHEN jsonb_typeof(generation_params -> 'compiled_cases' -> 'form_skipped_no_locator') = 'number'
        THEN (generation_params -> 'compiled_cases' ->> 'form_skipped_no_locator')::int
    END,
    compile_status = CASE
        -- 缺键，或键存在但为空对象 → 没有编译产物
        WHEN generation_params -> 'compiled_cases' IS NULL
             OR generation_params -> 'compiled_cases' = '{}'::jsonb
        THEN 'not_compiled'
        -- 有产物但没有检查计划字段 → 检查数未知（未配置定位器）
        -- ⚠️ 必须 coalesce：键缺失时 jsonb_typeof 返回 NULL，而 `NULL <> 'number'`
        -- 的结果是 NULL（非真），CASE 会一路落到 ELSE，把"未知"误判成 zero_checks。
        -- 这正是 2026-10-17 首轮回填踩到的坑（34 条被写成 zero_checks 且计数为 NULL）。
        WHEN coalesce(
                 jsonb_typeof(generation_params -> 'compiled_cases' -> 'form_executable_checks'),
                 'missing'
             ) <> 'number'
        THEN 'check_plan_missing'
        WHEN (generation_params -> 'compiled_cases' ->> 'form_executable_checks')::int > 0
        THEN 'compiled'
        ELSE 'zero_checks'
    END,
    is_legacy_asset = (lower(coalesce(generated_by_agent, '')) IN ('web_agent', 'web-agent')),
    -- 近似值：历史资产的真实编译时刻未记录，用记录最后更新时间代替并在文档中标注。
    -- ⚠️ 必须 coalesce：本库有 532 条资产 updated_at 为空（历史写入路径未触发 onupdate），
    -- 只取 updated_at 会让 451 条"确有编译产物"的资产 compiled_at 仍为 NULL。
    compiled_at = CASE
        WHEN generation_params -> 'compiled_cases' IS NOT NULL
             AND generation_params -> 'compiled_cases' <> '{}'::jsonb
        THEN coalesce(updated_at, created_at)
    END
"""


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


def _index_exists(conn, name: str) -> bool:
    return bool(
        conn.execute(
            sa.text("SELECT EXISTS (SELECT 1 FROM pg_indexes WHERE indexname = :n)"),
            {"n": name},
        ).scalar()
    )


def upgrade() -> None:
    conn = op.get_bind()
    for name, column in _NEW_COLUMNS:
        if not _column_exists(conn, "web_tests", name):
            op.add_column("web_tests", column)

    # 回填在补列之后单独执行：已有数据的列值全部由 SQL 复算得出，
    # 便于事后用同一条 SQL 与列值做逐行对账。
    conn.execute(sa.text(_BACKFILL_SQL))

    if not _index_exists(conn, _INDEX_NAME):
        op.create_index(_INDEX_NAME, "web_tests", ["compile_status"])


def downgrade() -> None:
    conn = op.get_bind()
    if _index_exists(conn, _INDEX_NAME):
        op.drop_index(_INDEX_NAME, table_name="web_tests")
    for name, _column in reversed(_NEW_COLUMNS):
        if _column_exists(conn, "web_tests", name):
            op.drop_column("web_tests", name)
