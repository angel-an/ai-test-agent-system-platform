# -*- coding: utf-8 -*-
"""API 链路僵死 run 的收尾判据（``recover_stuck_api_runs``）。

实测背景（2026-10-27）：``recover_stuck_executions()`` 一直只覆盖 web 链路的
``TestRunScriptJob`` / ``TestRun``，``api_test_runs`` **没有任何收尾路径**。
库里留下 7 条 pending/running，最老 64 天，界面上看着"还在跑"。

三个必须锁住的点（每一条都是实际踩到或差点踩到的）：
1. 判据用 ``COALESCE(updated_at, created_at)`` —— 实测 3 条僵死 run 的
   ``updated_at`` 是 NULL，直接比较得到 NULL、一条都收不掉；
2. 中断措辞必须能与"用例失败"区分 —— 否则统计会把进程崩溃算成被测系统缺陷；
3. 只收 pending/running，绝不碰已完成的历史记录。
"""

from __future__ import annotations

import inspect

from app.main import recover_stuck_api_runs
from app.repositories.api_test_repo import API_RUN_INTERRUPTED_MESSAGE


def _code_lines() -> str:
    source = inspect.getsource(recover_stuck_api_runs)
    return "\n".join(
        line for line in source.splitlines() if not line.strip().startswith("#")
    )


def test_interrupted_message_is_distinguishable_from_test_failure():
    """措辞里必须明确"非用例失败"，否则没法把它从缺陷统计里摘出来。"""
    assert "非用例失败" in API_RUN_INTERRUPTED_MESSAGE
    assert "中断" in API_RUN_INTERRUPTED_MESSAGE


def test_uses_coalesce_for_last_activity():
    """``updated_at`` 为 NULL 的僵死 run 只有 COALESCE 能收掉。"""
    code = _code_lines()
    assert "coalesce" in code.lower()


def test_only_reaps_pending_and_running():
    """已完成 / 已失败的记录不能被改写。"""
    code = _code_lines()
    assert '"pending"' in code and '"running"' in code
    assert "completed" not in code


def test_time_window_guards_against_other_workers():
    """启动时只收"5 分钟前就没动静"的，避免多 worker 下误杀正在跑的 run。"""
    code = _code_lines()
    assert "interval '5 minutes'" in code


def test_does_not_touch_counts():
    """中断不是用例失败，计数一个都不许改（否则 verdict 会从 empty 变 fail）。"""
    code = _code_lines()
    assert "total_tests" not in code
    assert "failed_tests" not in code
    assert "passed_tests" not in code


def test_reaper_is_wired_into_startup():
    """收尾函数必须真的挂在 lifespan 里，否则永远不会执行。"""
    from app import main as main_module

    lifespan_source = inspect.getsource(main_module.lifespan)
    assert "recover_stuck_api_runs()" in lifespan_source
