"""TR 编号互斥锁的单元契约（不依赖数据库）。

背景（rev97）：``generate_identifier`` 原本是纯 max+1，两个并发创建请求会读到
同一个最大值，插入时撞 ``ix_test_runs_identifier`` 变成 HTTP 500。真实库实测
并发 6 时 18 次创建失败 15 次。

这里锁住三条不依赖真实库的性质：
- PostgreSQL 下**先取事务级咨询锁**，且用 try 版本（不阻塞挂死）；
- 非 PostgreSQL 不调 advisory lock（SQLite 无此函数，且本来写串行）；
- 拿不到锁时**快速失败**（409 语义），不能变成无响应。
"""

import asyncio
import time

import pytest

from app.repositories import test_run_repo as mod
from app.repositories.test_run_repo import _IDENTIFIER_LOCK_KEY  # noqa: F401
from app.utils.exceptions import ConflictException


class _Dialect:
    def __init__(self, name: str):
        self.name = name


class _Bind:
    def __init__(self, name: str):
        self.dialect = _Dialect(name)


class _Result:
    def __init__(self, value):
        self._value = value

    def scalar(self):
        return self._value


class FakeSession:
    """只实现 _acquire_identifier_lock 用到的两个方法。"""

    def __init__(self, dialect: str = "postgresql", lock_results: list | None = None):
        self._bind = _Bind(dialect)
        self._results = list(lock_results or [])
        self.calls: list[tuple[str, object]] = []

    def get_bind(self):
        return self._bind

    async def execute(self, statement, params=None):
        self.calls.append((str(statement), params))
        return _Result(self._results.pop(0) if self._results else False)


def test_postgresql_takes_transaction_lock_first():
    session = FakeSession(lock_results=[True])
    asyncio.run(mod.TestRunRepository(session)._acquire_identifier_lock())

    assert len(session.calls) == 1
    sql, params = session.calls[0]
    assert "pg_try_advisory_xact_lock" in sql
    assert params == {"key": _IDENTIFIER_LOCK_KEY}


def test_lock_is_try_version_not_blocking():
    """必须是 try 版本：阻塞版在持锁事务卡住时会让创建接口无限等待。"""
    session = FakeSession(lock_results=[True])
    asyncio.run(mod.TestRunRepository(session)._acquire_identifier_lock())
    assert "pg_advisory_xact_lock" not in session.calls[0][0].replace(
        "pg_try_advisory_xact_lock", ""
    )


def test_non_postgresql_skips_advisory_lock():
    session = FakeSession(dialect="sqlite", lock_results=[True])
    asyncio.run(mod.TestRunRepository(session)._acquire_identifier_lock())
    assert session.calls == []


def test_retries_until_lock_acquired(monkeypatch):
    monkeypatch.setattr(mod, "_IDENTIFIER_LOCK_POLL", 0.001)
    session = FakeSession(lock_results=[False, False, True])
    asyncio.run(mod.TestRunRepository(session)._acquire_identifier_lock())
    assert len(session.calls) == 3


def test_timeout_raises_conflict_instead_of_hanging(monkeypatch):
    monkeypatch.setattr(mod, "_IDENTIFIER_LOCK_TIMEOUT", 0.15)
    monkeypatch.setattr(mod, "_IDENTIFIER_LOCK_POLL", 0.01)
    session = FakeSession(lock_results=[])

    started = time.monotonic()
    with pytest.raises(ConflictException) as ei:
        asyncio.run(mod.TestRunRepository(session)._acquire_identifier_lock())
    elapsed = time.monotonic() - started

    assert "互斥锁超时" in str(ei.value)
    assert 0.1 <= elapsed < 1.0, f"应在设定上限附近快速失败，实际 {elapsed:.2f}s"


def test_missing_bind_does_not_break_creation(monkeypatch):
    """拿不到方言时不得阻断创建流程（只会少一层保护，不是错误）。"""

    class _Broken:
        def get_bind(self):
            raise RuntimeError("no bind")

        async def execute(self, *a, **kw):  # pragma: no cover - 不应被调用
            raise AssertionError("不应查询数据库")

    asyncio.run(mod.TestRunRepository(_Broken())._acquire_identifier_lock())
