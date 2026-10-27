# -*- coding: utf-8 -*-
"""API run 编号唯一性（0026 迁移 + get_next_identifier 口径）。

实测背景（2026-10-27）：``api_test_runs.identifier`` 里有 **33 组重号**
（如 ``ATR-20260824-003`` 被 3 条不同记录共用）。根因是编号按 ``api_test_id``
分桶——同一项目下 N 个 api_test 每天各自从 001 起号，看到编号无法定位到
具体哪一次执行。

本文件锁三件事：
1. 纯函数 ``next_api_run_identifier`` 的编号口径（当天全局、数值序、异构值忽略）；
2. repo 不得再按 ``api_test_id`` 过滤 / 加锁（回退回去就会重新产生重号）；
3. 唯一索引必须是**只覆盖 ATR-% 的部分索引** —— 该列还混着
   ``SR-EP-<endpoint_id>``（同一脚本多次执行复用同一编号，重复是设计意图），
   全局唯一索引在真实库上直接建不起来。
"""

from __future__ import annotations

import inspect
from pathlib import Path

from app.repositories.api_test_repo import (
    APITestRunRepository,
    next_api_run_identifier,
)

BACKEND = Path(__file__).resolve().parents[1]
MIGRATION = BACKEND / "alembic" / "versions" / "0026_api_run_identifier_unique.py"


def test_first_of_day_starts_at_001():
    assert next_api_run_identifier([], "20261027") == "ATR-20261027-001"


def test_increments_by_number_not_by_dict_order():
    """``009`` / ``010`` 之后必须是 ``011`` —— 按字典序会错成 010。"""
    got = next_api_run_identifier(["ATR-20261027-009", "ATR-20261027-010"], "20261027")
    assert got == "ATR-20261027-011"


def test_duplicates_do_not_change_result():
    """存量重号（同一编号出现多次）只算一个，不得把序号推快。"""
    got = next_api_run_identifier(
        ["ATR-20260824-001", "ATR-20260824-001", "ATR-20260824-001"], "20260824"
    )
    assert got == "ATR-20260824-002"


def test_other_dates_are_ignored():
    """只认当天前缀：昨天的 050 不能把今天的编号推到 051。"""
    got = next_api_run_identifier(["ATR-20261026-050"], "20261027")
    assert got == "ATR-20261027-001"


def test_heterogeneous_identifiers_are_ignored():
    """同列混用的 SR-EP-* 等异构值不能参与计算，也不能让函数抛错。"""
    got = next_api_run_identifier(
        ["SR-EP-abc", "ATR-20261027-003", "ATR-20261027-"], "20261027"
    )
    assert got == "ATR-20261027-004"


def test_beyond_three_digits_keeps_growing():
    """序号过 999 自然进位，不允许回绕到 000。"""
    assert next_api_run_identifier(["ATR-20261027-999"], "20261027") == "ATR-20261027-1000"


def test_repo_no_longer_buckets_by_api_test():
    """源码级防回归：编号查询与锁 key 都不许再带 api_test_id。

    带回去 = 恢复"每个 api_test 各自从 001 起号"，重号立刻回来。
    """
    source = inspect.getsource(APITestRunRepository.get_next_identifier)
    code = "\n".join(
        line for line in source.splitlines() if not line.strip().startswith("#")
    )
    assert "api_test_id ==" not in code
    assert "ATR" in code  # 仍是 ATR 编号，没有被误改成别的格式
    # 锁 key 里不能出现 api_test_id 占位
    assert "atr_identifier:{api_test_id}" not in code
    assert 'atr_identifier:{date_str}' in code


def test_migration_builds_partial_unique_index():
    """唯一索引必须带 ATR-% 谓词，否则会被 SR-EP 的重复值挡住。"""
    sql = MIGRATION.read_text(encoding="utf-8")
    assert "uq_api_test_runs_identifier_atr" in sql
    assert "WHERE identifier LIKE 'ATR-%'" in sql


def test_migration_reindexes_before_creating_index():
    """顺序不能反：存量没清理干净就建索引会失败（首次尝试即如此）。"""
    sql = MIGRATION.read_text(encoding="utf-8")
    upgrade = sql.split("def upgrade()", 1)[1]
    assert upgrade.index("_REINDEX_SQL") < upgrade.index("CREATE UNIQUE INDEX")
