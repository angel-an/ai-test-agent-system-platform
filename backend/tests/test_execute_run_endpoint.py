# -*- coding: utf-8 -*-
"""execute_test_run 真实业务分支回归（2026-09-29 复核要求）。

背景：守卫 `_deprecated_cases_in_run` 曾引用未导入的 `TestRunTestCase`，
专项回归只验证到 import 层，真实调用 500（NameError）。本测试用 mock service/db
走通端点函数的真实分支（守卫查询 → 状态检查 → 后台执行），防止同类漏测。
"""

from __future__ import annotations

from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.schemas.enums import TestRunState


class _EmptyResult:
    def scalars(self):
        return self

    def all(self):
        return []


class _MockDB:
    """只满足守卫查询：db.execute(...) → 空结果（运行无废弃用例）。"""

    async def execute(self, _stmt):
        return _EmptyResult()


class _MockService:
    async def get_by_identifier(self, _project, _run):
        return SimpleNamespace(id=uuid4(), run_state=TestRunState.NEW_RUN)


class _MockEngine:
    async def execute_run(self, *_a, **_k):
        return {}


class _FakeTask:
    """最小 Task 替身。

    端点现在必须持有任务引用（rev186：不持引用会被 GC 回收），并注册
    ``add_done_callback`` 清理引用集，因此替身需实现该方法，否则 mock 与
    真实 asyncio.Task 形态不符。
    """

    def __init__(self, coro):
        coro.close()  # 不真正启动执行

    def add_done_callback(self, _cb):
        return None


async def test_execute_run_guard_branch_no_name_error(monkeypatch):
    """守卫分支 + 导入正确性：真实调用 execute_test_run 不再 500。"""
    import asyncio

    import app.api.v2.test_runs as tr_mod

    monkeypatch.setattr(tr_mod, "_get_execution_engine", lambda: _MockEngine())
    created: list[bool] = []

    def _fake_create_task(coro):
        created.append(True)
        return _FakeTask(coro)

    monkeypatch.setattr(asyncio, "create_task", _fake_create_task)
    resp = await tr_mod.execute_test_run(
        project_identifier="PR-9",
        test_run_identifier="TR-X",
        service=_MockService(),
        db=_MockDB(),
    )
    assert resp.success is True
    assert resp.data["status"] == "in_progress"
    assert created == [True]  # 后台执行确实被调度（守卫未拦截）


async def test_execute_run_rejects_deprecated_cases(monkeypatch):
    """守卫拦截分支：运行含废弃用例 → 400，且不会调度执行。"""
    import asyncio

    import app.api.v2.test_runs as tr_mod
    from fastapi import HTTPException

    class _Rows:
        def scalars(self):
            return self

        def all(self):
            return [SimpleNamespace(
                identifier="TC-DEP1", custom_fields={"deprecated": True},
            )]

    class _DBWithDeprecated:
        async def execute(self, _stmt):
            return _Rows()

    monkeypatch.setattr(tr_mod, "_get_execution_engine", lambda: _MockEngine())
    scheduled: list[bool] = []
    monkeypatch.setattr(
        asyncio, "create_task", lambda coro: (scheduled.append(True), _FakeTask(coro))[1]
    )
    with pytest.raises(HTTPException) as exc:
        await tr_mod.execute_test_run(
            project_identifier="PR-9",
            test_run_identifier="TR-X",
            service=_MockService(),
            db=_DBWithDeprecated(),
        )
    assert exc.value.status_code == 400
    assert "TC-DEP1" in exc.value.detail
    assert scheduled == []  # 未调度执行
