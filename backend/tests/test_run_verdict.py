# -*- coding: utf-8 -*-
"""run 级结论 verdict 的口径测试（rev：0025 迁移 + update_counts）。

背景（2026-10-26 基线实测）：断言失败的 run 终态是 REJECTED 而非 DONE，
按 run_state 统计会把失败整批漏掉。verdict 是"判红"的唯一依据，
口径与 ``compute_run_verdict`` 和 0025 迁移的回填 SQL **必须一致**——
本文件同时用表驱动用例锁住 Python 实现与 SQL 文本。
"""

from __future__ import annotations

import re
from pathlib import Path

from app.repositories.test_run_repo import (
    RUN_VERDICT_EMPTY,
    RUN_VERDICT_FAIL,
    RUN_VERDICT_PASS,
    RUN_VERDICT_UNVERIFIED,
    compute_run_verdict,
)

BACKEND = Path(__file__).resolve().parents[1]


def test_fail_wins_over_everything():
    """一条失败就该红，其余状态不管多少。"""
    assert compute_run_verdict(total=8, passed=7, failed=1, skipped=0, blocked=0, not_executed=0) == RUN_VERDICT_FAIL
    assert compute_run_verdict(total=10, passed=0, failed=4, skipped=5, blocked=1, not_executed=0) == RUN_VERDICT_FAIL


def test_all_passed_is_pass():
    assert compute_run_verdict(total=13, passed=13, failed=0, skipped=0, blocked=0, not_executed=0) == RUN_VERDICT_PASS


def test_inconclusive_is_never_pass():
    """SKIPPED/BLOCKED/NOT_EXECUTED 都算不可定：不能把"部分没跑"当成通过。"""
    assert compute_run_verdict(total=5, passed=3, failed=0, skipped=2, blocked=0, not_executed=0) == RUN_VERDICT_UNVERIFIED
    assert compute_run_verdict(total=20, passed=8, failed=0, skipped=0, blocked=0, not_executed=12) == RUN_VERDICT_UNVERIFIED


def test_nothing_passed_and_nothing_failed_is_unverified():
    """全部 NOT_EXECUTED：既不红也不绿，是"没结论"。"""
    assert compute_run_verdict(total=147, passed=0, failed=0, skipped=0, blocked=0, not_executed=147) == RUN_VERDICT_UNVERIFIED


def test_empty_run():
    assert compute_run_verdict(total=0, passed=0, failed=0, skipped=0, blocked=0, not_executed=0) == RUN_VERDICT_EMPTY


def test_migration_backfill_sql_matches_python_order():
    """迁移回填 SQL 与 Python 口径必须同序：empty → fail → pass → unverified。

    漂移的后果：历史 run 与新 run 的 verdict 用两套规则，基线 diff 会出假差异。
    """
    sql = (BACKEND / "alembic" / "versions" / "0025_test_run_verdict.py").read_text(encoding="utf-8")
    assert "test_cases_count = 0 THEN 'empty'" in sql
    assert "failed_count > 0 THEN 'fail'" in sql
    assert "(skipped_count + blocked_count + not_executed_count) = 0 THEN 'pass'" in sql
    assert "ELSE 'unverified'" in sql
    # CASE 分支顺序（SQL 语义依赖先后）
    assert sql.index("'empty'") < sql.index("'fail'") < sql.index("'pass'") < sql.index("ELSE 'unverified'")
