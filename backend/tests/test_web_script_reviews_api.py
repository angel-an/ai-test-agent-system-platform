"""P0-2 生产接入测试（rev45/46 + rev47 鉴权）。

- POST /web-script-reviews/run-pending：按需触发修复循环（limit 上限 20）；
- GET /web-script-reviews/pending：待评审任务列表（真实 DB）；
- register_script_repair_job：WEB_SCRIPT_REPAIR_INTERVAL_MINUTES>0 才注册，
  max_instances=1 防重叠；
- rev47 鉴权：端点要求超管；get_current_superuser 401/403/200。
"""

from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest
from fastapi import HTTPException
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.pool import StaticPool

from app.models.web_script_review import WebScriptReview


@pytest.fixture
async def api_engine():
    engine = create_async_engine(
        "sqlite+aiosqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False}
    )
    async with engine.begin() as conn:
        await conn.run_sync(WebScriptReview.__table__.create)
    yield engine
    await engine.dispose()


def _factory(engine):
    from sqlalchemy.ext.asyncio import async_sessionmaker

    return async_sessionmaker(engine, expire_on_commit=False)


def _superuser(**kw):
    """模拟已认证超管用户（端点直接调用时传入 current_user）。"""
    base = dict(username="admin", id=uuid4(), is_superuser=True, is_active=True)
    base.update(kw)
    return SimpleNamespace(**base)


class TestSuperuserDependency:
    def _req(self, path="/api/v2/web-script-reviews/pending"):
        from fastapi import Request

        return SimpleNamespace(url=SimpleNamespace(path=path))

    async def test_superuser_allowed(self, monkeypatch, caplog):
        """超管 → 返回用户 + allow 审计。"""
        import logging

        import app.api.deps as deps_mod

        u = _superuser()
        async def _active(token, db):
            return u

        monkeypatch.setattr(deps_mod, "get_current_active_user", _active)
        with caplog.at_level(logging.INFO, logger="app.api.deps"):
            got = await deps_mod.get_current_superuser(self._req(), token="t", db=None)
        assert got is u
        assert any("[audit]" in r.message and "decision=allow" in r.message
                   for r in caplog.records)

    async def test_non_superuser_forbidden(self, monkeypatch, caplog):
        """已认证但非超管 → 403 + deny 审计（P2：拒绝也留痕）。"""
        import logging

        import app.api.deps as deps_mod

        u = _superuser(is_superuser=False)
        async def _active(token, db):
            return u

        monkeypatch.setattr(deps_mod, "get_current_active_user", _active)
        with caplog.at_level(logging.WARNING, logger="app.api.deps"):
            with pytest.raises(HTTPException) as exc:
                await deps_mod.get_current_superuser(self._req(), token="t", db=None)
        assert exc.value.status_code == 403
        assert any("[audit]" in r.message and "decision=deny" in r.message
                   and "not_superuser" in r.message
                   for r in caplog.records)

    async def test_invalid_token_unauthorized(self, monkeypatch, caplog):
        """无效令牌 → 401（上游抛出）+ deny 审计。"""
        import logging

        import app.api.deps as deps_mod
        from fastapi import status as _status

        async def _active(token, db):
            raise HTTPException(
                status_code=_status.HTTP_401_UNAUTHORIZED,
                detail="未认证或认证已过期",
            )

        monkeypatch.setattr(deps_mod, "get_current_active_user", _active)
        with caplog.at_level(logging.WARNING, logger="app.api.deps"):
            with pytest.raises(HTTPException) as exc:
                await deps_mod.get_current_superuser(self._req(), token="t", db=None)
        assert exc.value.status_code == 401
        assert any("[audit]" in r.message and "decision=deny" in r.message
                   and "unauthorized" in r.message
                   for r in caplog.records)


class TestDefaultSuperuserGate:
    """rev48（P1 修复）：默认用户超管提升仅限显式开发模式。"""

    @pytest.fixture
    async def user_engine(self):
        from sqlalchemy import text as _text
        from sqlalchemy.ext.asyncio import create_async_engine
        from sqlalchemy.pool import StaticPool

        engine = create_async_engine(
            "sqlite+aiosqlite://", poolclass=StaticPool,
            connect_args={"check_same_thread": False},
        )
        async with engine.begin() as conn:
            # 裸 DDL：避免 SQLite NUMERIC 亲和性把全数字 UUID hex 转成 int
            # （默认开发用户 id 的 hex 全为数字，`User.__table__.create` 会触发该问题）
            await conn.execute(_text(
                "CREATE TABLE users ("
                "id CHAR(32) PRIMARY KEY, email VARCHAR(255) NOT NULL, "
                "username VARCHAR(100) NOT NULL, password_hash VARCHAR(255) NOT NULL, "
                "is_active BOOLEAN NOT NULL, is_superuser BOOLEAN NOT NULL, "
                "created_at DATETIME DEFAULT CURRENT_TIMESTAMP NOT NULL, updated_at DATETIME)"
            ))
        yield engine
        await engine.dispose()

    async def _run_bootstrap(self, user_engine, monkeypatch, dev_flag):
        import app.main as main_mod
        from app.config.settings import settings

        monkeypatch.setattr(main_mod, "async_session_factory", _factory(user_engine))
        monkeypatch.setattr(settings, "enable_dev_default_superuser", dev_flag)
        await main_mod.ensure_default_user()

    async def _get_user(self, user_engine):
        from sqlalchemy import select

        from app.models.user import User

        async with AsyncSession(user_engine) as s:
            return (await s.execute(select(User))).scalars().first()

    async def test_prod_no_user_skips_creation(self, user_engine, monkeypatch):
        """生产（flag=0）+ 无默认用户 → 不创建超管。"""
        await self._run_bootstrap(user_engine, monkeypatch, dev_flag=False)
        assert await self._get_user(user_engine) is None

    async def test_prod_existing_user_not_promoted(self, user_engine, monkeypatch):
        """生产（flag=0）+ 存量非超管用户 → 保持非超管（安全基线）。"""
        from sqlalchemy import select

        from app.config.settings import settings
        from app.models.user import User

        async with AsyncSession(user_engine) as s:
            s.add(User(id=UUID(settings.default_user_id), email=settings.default_user_email,
                       username=settings.default_user_name, password_hash="x",
                       is_active=True, is_superuser=False))
            await s.commit()
        await self._run_bootstrap(user_engine, monkeypatch, dev_flag=False)
        u = await self._get_user(user_engine)
        assert u is not None and u.is_superuser is False

    async def test_dev_existing_user_promoted(self, user_engine, monkeypatch):
        """开发（flag=1）+ 存量非超管用户 → 幂等提升。"""
        from app.config.settings import settings
        from app.models.user import User

        async with AsyncSession(user_engine) as s:
            s.add(User(id=UUID(settings.default_user_id), email=settings.default_user_email,
                       username=settings.default_user_name, password_hash="x",
                       is_active=True, is_superuser=False))
            await s.commit()
        await self._run_bootstrap(user_engine, monkeypatch, dev_flag=True)
        u = await self._get_user(user_engine)
        assert u is not None and u.is_superuser is True

    async def test_dev_no_user_creates_superuser(self, user_engine, monkeypatch):
        """开发（flag=1）+ 无默认用户 → 创建超管。"""
        await self._run_bootstrap(user_engine, monkeypatch, dev_flag=True)
        u = await self._get_user(user_engine)
        assert u is not None and u.is_superuser is True


class TestRunPendingEndpoint:
    async def test_run_pending_returns_results(self, monkeypatch):
        """POST run-pending：真实 run_pending_repairs 被调用，limit 透传。"""
        from app.api.v2.web_script_reviews import RunPendingRequest, run_pending

        calls = {}

        async def _fake_run(session_factory, limit=5):
            calls["limit"] = limit
            return [{"sub_function_id": "sf-1", "error_summary": "e",
                     "status": "passed", "detail": "验证通过"}]

        monkeypatch.setattr(
            "app.agents.tools.web.script_review.run_pending_repairs", _fake_run
        )
        resp = await run_pending(RunPendingRequest(limit=3), current_user=_superuser())
        assert resp["ran"] == 1
        assert resp["results"][0]["status"] == "passed"
        assert calls["limit"] == 3

    async def test_run_pending_limit_capped_at_20(self, monkeypatch):
        """limit 上限 20：防止单次触发消耗过多 LLM API 额度。"""
        from app.api.v2.web_script_reviews import RunPendingRequest, run_pending

        calls = {}

        async def _fake_run(session_factory, limit=5):
            calls["limit"] = limit
            return []

        monkeypatch.setattr(
            "app.agents.tools.web.script_review.run_pending_repairs", _fake_run
        )
        await run_pending(RunPendingRequest(limit=99), current_user=_superuser())
        assert calls["limit"] == 20
        await run_pending(RunPendingRequest(limit=0), current_user=_superuser())
        assert calls["limit"] == 1

    async def test_run_pending_no_pending_returns_empty(self, monkeypatch):
        """无待评审任务 → ran=0，不抛错。"""
        from app.api.v2.web_script_reviews import RunPendingRequest, run_pending

        async def _fake_run(session_factory, limit=5):
            return []

        monkeypatch.setattr(
            "app.agents.tools.web.script_review.run_pending_repairs", _fake_run
        )
        resp = await run_pending(RunPendingRequest(), current_user=_superuser())
        assert resp == {"ran": 0, "results": []}

    async def test_run_pending_audit_logged(self, monkeypatch, caplog):
        """rev47 审计：触发操作记录 actor/action/ran。"""
        import logging

        from app.api.v2.web_script_reviews import RunPendingRequest, run_pending

        async def _fake_run(session_factory, limit=5):
            return [{"sub_function_id": "sf-1", "error_summary": "e",
                     "status": "passed", "detail": "ok"}]

        monkeypatch.setattr(
            "app.agents.tools.web.script_review.run_pending_repairs", _fake_run
        )
        with caplog.at_level(logging.INFO, logger="app.api.v2.web_script_reviews"):
            await run_pending(RunPendingRequest(limit=2), current_user=_superuser())
        assert any(
            "[audit]" in r.message and "run_pending" in r.message and "ran=1" in r.message
            for r in caplog.records
        )


class TestApproveRejectEndpoint:
    """rev55：审批通过/拒绝 API（超管）。"""

    async def test_approve_calls_service(self, monkeypatch, caplog):
        """POST approve：调用 approve_script_review（真实服务函数），返回发布结果。"""
        import logging

        from app.api.v2.web_script_reviews import ApproveReviewRequest, approve_review

        from uuid import UUID as _UUID

        sf = _UUID("2971e0ae-c1c2-42da-8941-26ebe351b177")
        called = {}

        async def _fake_approve(session_factory, sub_function_id, actor=""):
            called["sf"] = sub_function_id
            called["actor"] = actor
            return {"status": "passed", "detail": "审批通过并发布", "version_no": 1}

        monkeypatch.setattr(
            "app.agents.tools.web.script_review.approve_script_review", _fake_approve
        )
        with caplog.at_level(logging.INFO, logger="app.api.v2.web_script_reviews"):
            resp = await approve_review(
                ApproveReviewRequest(sub_function_id=str(sf)),
                current_user=_superuser(username="admin"),
            )
        assert resp["status"] == "passed"
        assert called["sf"] == sf
        assert any("[audit]" in r.message and "approve" in r.message
                   for r in caplog.records)

    async def test_reject_calls_service(self, monkeypatch):
        """POST reject：调用 reject_script_review，proposed 保留审计不发布。"""
        from app.api.v2.web_script_reviews import ApproveReviewRequest, reject_review

        from uuid import UUID as _UUID

        sf = _UUID("2971e0ae-c1c2-42da-8941-26ebe351b177")
        called = {}

        async def _fake_reject(session_factory, sub_function_id, reason=None, actor=""):
            called["sf"] = sub_function_id
            called["reason"] = reason
            return {"status": "rejected", "detail": "审批拒绝", "version_no": 1}

        monkeypatch.setattr(
            "app.agents.tools.web.script_review.reject_script_review", _fake_reject
        )
        resp = await reject_review(
            ApproveReviewRequest(sub_function_id=str(sf), reason="断言不充分"),
            current_user=_superuser(),
        )
        assert resp["status"] == "rejected"
        assert called["sf"] == sf
        assert called["reason"] == "断言不充分"


class TestListPendingEndpoint:
    async def test_list_pending_shows_queued_tasks(self, api_engine, monkeypatch):
        """GET pending：真实 DB 查询 pending 任务。"""
        import app.api.v2.web_script_reviews as api_mod
        from app.agents.tools.web.script_review import enqueue_script_review

        session_factory = _factory(api_engine)
        sf = uuid4()
        await enqueue_script_review(session_factory, sf, None, "保存失败: 超时")
        # 端点直接用模块级 async_session_factory → 指向测试 DB
        monkeypatch.setattr(api_mod, "async_session_factory", session_factory)

        resp = await api_mod.list_pending(current_user=_superuser())
        assert resp["count"] == 1
        item = resp["items"][0]
        assert item["sub_function_id"] == str(sf)
        assert item["status"] == "pending"
        assert "超时" in item["error_summary"]

    async def test_list_pending_empty(self, api_engine, monkeypatch):
        """无待评审任务 → count=0。"""
        import app.api.v2.web_script_reviews as api_mod

        monkeypatch.setattr(api_mod, "async_session_factory", _factory(api_engine))
        resp = await api_mod.list_pending(current_user=_superuser())
        assert resp["count"] == 0


class TestRepairSchedulerRegistration:
    def test_register_disabled_when_interval_zero(self, monkeypatch):
        """默认（0/未设置）→ 不注册定时任务。"""
        import app.services.scheduler as sched_mod

        monkeypatch.delenv("WEB_SCRIPT_REPAIR_INTERVAL_MINUTES", raising=False)

        class _StubScheduler:
            def add_job(self, *a, **k):
                raise AssertionError("interval=0 时不应注册任务")

        monkeypatch.setattr(sched_mod, "scheduler", _StubScheduler())
        sched_mod.register_script_repair_job()  # 不应抛错

    def test_register_enabled_with_interval(self, monkeypatch):
        """>0 → 注册 interval 任务（id/max_instances/minutes 断言）。"""
        import app.services.scheduler as sched_mod

        monkeypatch.setenv("WEB_SCRIPT_REPAIR_INTERVAL_MINUTES", "5")
        calls = {}

        class _StubScheduler:
            def add_job(self, fn, trigger, **kw):
                calls["trigger"] = trigger
                calls.update(kw)

        monkeypatch.setattr(sched_mod, "scheduler", _StubScheduler())
        sched_mod.register_script_repair_job()
        assert calls["trigger"] == "interval"
        assert calls["id"] == "web_script_repair_cycle"
        assert calls["minutes"] == 5
        assert calls["max_instances"] == 1
        assert calls["coalesce"] is True

    def test_register_skips_second_registration(self, monkeypatch):
        """replace_existing=True：重复注册不会堆积任务。"""
        import app.services.scheduler as sched_mod

        monkeypatch.setenv("WEB_SCRIPT_REPAIR_INTERVAL_MINUTES", "5")
        calls = {"n": 0}

        class _StubScheduler:
            def add_job(self, fn, trigger, **kw):
                calls["n"] += 1

        monkeypatch.setattr(sched_mod, "scheduler", _StubScheduler())
        sched_mod.register_script_repair_job()
        sched_mod.register_script_repair_job()
        assert calls["n"] == 2  # 每次调用均显式 replace_existing，由调度器去重
