# -*- coding: utf-8 -*-
"""API 链路 run 级结论 verdict（``api_test_runs.verdict``，0027 迁移）。

背景：web 链路 0025 已有等价字段；API 链路单看 status 分不清
"用例失败"和"根本没跑出用例"（零结果/中断/环境失败终态同为 failed）。

锁住四个点：
1. ``api_run_verdict`` 与 0027 迁移回填 SQL 同口径（表驱动全组合比对）；
2. ``api_test_executor`` 的计数落库点与两条失败路径都写 verdict；
3. ``api_endpoint_script_executor`` / ``executors.py`` 的收尾点写 verdict；
4. 对外输出（run 列表 / 详情 / 端点 runs）带 verdict。
"""

from __future__ import annotations

import inspect

from app.repositories.api_test_repo import api_run_verdict
from app.repositories.test_run_repo import RUN_VERDICT_EMPTY, RUN_VERDICT_FAIL, RUN_VERDICT_PASS, RUN_VERDICT_UNVERIFIED


# ---------------------------------------------------------------------------
# 1. 口径一致性：api_run_verdict ≡ 0027 回填 SQL
# ---------------------------------------------------------------------------

def _migration_verdict_sql() -> str:
    """直接从迁移文件文本里抠出 CASE 表达式，防止"改了迁移忘了改注释"式的口径漂移。

    用文本抽取而不是 importlib 加载：迁移顶部 ``from alembic import op``
    依赖 alembic 运行环境（测试进程里 import 直接炸）。
    """
    import re

    src = _source(["alembic", "versions", "0027_api_run_verdict.py"])
    match = re.search(r'_VERDICT_SQL = """(.*?)"""', src, re.DOTALL)
    assert match, "迁移必须保留 _VERDICT_SQL 常量"
    return match.group(1)


def _sql_case_mirror(sql: str, total: int, passed: int, failed: int, skipped: int) -> str:
    """把 0027 的 CASE 表达式在 Python 里逐分支镜像执行。

    只认迁移里实际写出的比较（total_tests=0 / failed_tests>0 /
    passed_tests>0 AND skipped_tests=0 / ELSE），新增分支时这里必须同步。
    """
    assert "WHEN total_tests = 0 THEN 'empty'" in sql
    assert "WHEN failed_tests > 0 THEN 'fail'" in sql
    assert "WHEN passed_tests > 0 AND skipped_tests = 0 THEN 'pass'" in sql
    assert "ELSE 'unverified'" in sql
    if total == 0:
        return "empty"
    if failed > 0:
        return "fail"
    if passed > 0 and skipped == 0:
        return "pass"
    return "unverified"


def test_api_run_verdict_matches_migration_sql_for_all_count_combos():
    """0~3 的四桶计数全组合下，运行期推导与迁移回填必须一一相同。"""
    sql = _migration_verdict_sql()
    for total in range(4):
        for passed in range(4):
            for failed in range(4):
                for skipped in range(4):
                    expect = _sql_case_mirror(sql, total, passed, failed, skipped)
                    got = api_run_verdict(
                        total=total, passed=passed, failed=failed, skipped=skipped
                    )
                    assert got == expect, (
                        f"口径漂移: total={total} passed={passed} "
                        f"failed={failed} skipped={skipped}: {got} != {expect}"
                    )


def test_zero_result_is_empty_not_fail():
    """零结果（没跑出用例）必须归 empty——中断/环境失败不是用例失败。"""
    assert api_run_verdict(total=0, passed=0, failed=0, skipped=0) == RUN_VERDICT_EMPTY


def test_any_failure_is_fail():
    assert api_run_verdict(total=3, passed=2, failed=1, skipped=0) == RUN_VERDICT_FAIL


def test_all_pass_is_pass():
    assert api_run_verdict(total=3, passed=3, failed=0, skipped=0) == RUN_VERDICT_PASS


def test_skipped_only_is_unverified():
    assert api_run_verdict(total=3, passed=0, failed=0, skipped=3) == RUN_VERDICT_UNVERIFIED


# ---------------------------------------------------------------------------
# 2. api_test_executor 写点
# ---------------------------------------------------------------------------

def _source(path_parts: list[str]) -> str:
    from pathlib import Path

    return (Path(__file__).resolve().parents[1].joinpath(*path_parts)).read_text(
        encoding="utf-8"
    )


def test_executor_counts_update_writes_verdict():
    """计数落库点必须与 verdict 同事务写入，否则统计和结论可能脱节。"""
    src = _source(["app", "services", "api_test_executor.py"])
    assert "verdict=api_run_verdict(" in src


def test_executor_failure_paths_write_verdict():
    """两条 status=\"failed\" 收尾路径都要带 verdict（零结果 → empty）。"""
    src = _source(["app", "services", "api_test_executor.py"])
    failed_write_points = src.count('status="failed"')
    assert failed_write_points == 2, f"失败收尾写点数量变了: {failed_write_points}"
    # 每条失败路径都用行上计数推导，而不是写死
    assert "total=row.total_tests" in src
    assert "total=failed_row.total_tests" in src


# ---------------------------------------------------------------------------
# 3. 其他执行器写点
# ---------------------------------------------------------------------------

def test_endpoint_executor_finalizes_with_verdict():
    src = _source(["app", "services", "api_endpoint_script_executor.py"])
    assert "verdict=api_run_verdict(" in src
    # blocked 创建也要落 empty（阻断 ≠ 用例失败）
    assert 'status="blocked"' in src


def test_playwright_executor_writes_verdict_on_both_paths():
    src = _source(["app", "services", "execution", "executors.py"])
    assert "verdict=api_run_verdict(" in src          # completed：计数推导
    assert '"verdict": api_run_verdict(' in src       # failed：行上计数推导


# ---------------------------------------------------------------------------
# 4. 对外输出
# ---------------------------------------------------------------------------

def test_api_serialization_includes_verdict():
    run_service_src = _source(["app", "services", "api_test_service.py"])
    assert run_service_src.count('"verdict": item.verdict') == 1      # run 列表
    assert run_service_src.count('"verdict": test_run.verdict') == 1  # run 详情


def test_endpoint_runs_api_includes_verdict():
    src = _source(["app", "api", "v2", "api_endpoints.py"])
    assert '"verdict": run.verdict' in src
