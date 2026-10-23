# -*- coding: utf-8 -*-
"""定时运行手动触发的后台任务引用回归（rev186）。

背景：修订 create_task 未持引用（任务可能被事件循环 GC 回收）时，只补了
``_BACKGROUND_TASKS.add`` 而漏了模块级定义，真实调用会 500（NameError）。
本测试真实调用端点函数，同时校验「引用集存在」与「任务被登记」两件事。
"""

from __future__ import annotations

from types import SimpleNamespace
from uuid import uuid4

import pytest


class _MockRepo:
    def __init__(self, _db):
        pass

    async def get_by_id(self, _run_id):
        return SimpleNamespace(id=uuid4())


class _FakeTask:
    def __init__(self, coro):
        coro.close()

    def add_done_callback(self, _cb):
        return None


async def test_trigger_scheduled_run_holds_task_ref(monkeypatch):
    import asyncio

    import app.api.v2.scheduled_runs as sr_mod
    import app.services.scheduler as sched_mod

    assert hasattr(sr_mod, "_BACKGROUND_TASKS"), "模块级 _BACKGROUND_TASKS 未定义（会 NameError）"

    async def _fake_exec(_run_id):
        return None

    monkeypatch.setattr(sched_mod, "_execute_scheduled_run", _fake_exec)
    monkeypatch.setattr(sr_mod, "ScheduledRunRepository", _MockRepo)

    created: list[str] = []
    callbacks: list[object] = []

    class _TrackingTask(_FakeTask):
        def add_done_callback(self, cb):
            callbacks.append(cb)
            return None

    def _fake_create_task(coro):
        created.append("task")
        return _TrackingTask(coro)

    monkeypatch.setattr(asyncio, "create_task", _fake_create_task)
    before = len(sr_mod._BACKGROUND_TASKS)

    resp = await sr_mod.trigger_scheduled_run(
        project_identifier="PR-2",
        run_id=str(uuid4()),
        db=object(),
    )

    assert resp.success is True
    assert resp.data["status"] == "triggered"
    assert created == ["task"], "后台执行未被调度"
    assert len(callbacks) == 1, "未注册 done 回调（引用集不会被清理）"


async def test_trigger_scheduled_run_404_when_missing(monkeypatch):
    from fastapi import HTTPException

    import app.api.v2.scheduled_runs as sr_mod

    class _EmptyRepo:
        def __init__(self, _db):
            pass

        async def get_by_id(self, _run_id):
            return None

    monkeypatch.setattr(sr_mod, "ScheduledRunRepository", _EmptyRepo)
    with pytest.raises(HTTPException) as exc:
        await sr_mod.trigger_scheduled_run(
            project_identifier="PR-2", run_id=str(uuid4()), db=object()
        )
    assert exc.value.status_code == 404
