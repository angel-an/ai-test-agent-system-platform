"""执行治理层 P0-2：自愈评审队列回归测试。

覆盖：
1. enqueue 入队（pending）+ 幂等（同子功能重复入队不新建）；
2. bump 重试计数 → 达上限 blocked；
3. 异常时不阻断（入队函数内部 try/except）。
"""

from datetime import datetime, timezone
from uuid import uuid4

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from app.agents.tools.web.script_review import (
    MAX_REVIEW_RETRIES,
    bump_review_retry,
    enqueue_script_review,
)
from app.models.web_script_review import WebScriptReview


@pytest.fixture
async def review_engine():
    # StaticPool 单连接共享：并发测试（双 worker）需要多 session 看到同一库
    from sqlalchemy.pool import StaticPool

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


class TestScriptReviewQueue:
    async def test_enqueue_pending(self, review_engine):
        session_factory = _factory(review_engine)
        sf = uuid4()
        task = await enqueue_script_review(session_factory, sf, None, "保存广告投放失败")
        assert task is not None
        assert task.status == "pending"
        assert task.retry_count == 0
        assert "保存" in (task.error_summary or "")

    async def test_enqueue_idempotent(self, review_engine):
        session_factory = _factory(review_engine)
        sf = uuid4()
        t1 = await enqueue_script_review(session_factory, sf, None, "err1")
        t2 = await enqueue_script_review(session_factory, sf, None, "err2 更新摘要")
        async with AsyncSession(review_engine) as session:
            rows = (await session.execute(
                select(WebScriptReview).where(WebScriptReview.sub_function_id == sf)
            )).scalars().all()
            assert len(rows) == 1  # 幂等：不新建
            assert "err2" in (rows[0].error_summary or "")  # 摘要更新

    async def test_bump_retry_to_blocked(self, review_engine):
        session_factory = _factory(review_engine)
        sf = uuid4()
        await enqueue_script_review(session_factory, sf, None, "err")
        task = None
        for _ in range(MAX_REVIEW_RETRIES):
            task = await bump_review_retry(session_factory, sf, "再次失败")
        assert task is not None
        assert task.retry_count == MAX_REVIEW_RETRIES
        assert task.status == "blocked"  # 达上限 → blocked（人工介入）

    async def test_enqueue_no_block_on_error(self, review_engine):
        """异常（如 session 不可用）不阻断：返回 None 而非抛错（无 RuntimeWarning）。"""

        # 同步 factory 返回 None：async with None → AttributeError 被捕获；
        # 避免异步 coroutine 未 await 的 RuntimeWarning
        def _bad_factory():
            return None

        result = await enqueue_script_review(_bad_factory, uuid4(), None, "x")
        assert result is None  # 内部捕获，不抛


class TestRepairCycle:
    async def test_repair_cycle_verify_ok_goes_pending_approval(self, review_engine, monkeypatch):
        """rev55（审批流）：验证通过 → **pending_approval**（不自动发布）；
        proposed 附件与版本号记录；approve 后才发布 effective → passed。"""
        from app.agents.tools.web import artifacts_tools
        from app.agents.tools.web.script_review import (
            approve_script_review,
            run_repair_cycle,
        )

        session_factory = _factory(review_engine)
        sf = uuid4()
        await enqueue_script_review(session_factory, sf, None, "保存失败")

        async def _fake_save(**kwargs):
            return {"attachment_id": "00000000-0000-0000-0000-000000000001",
                    "version_status": "proposed"}

        publish_calls = []

        async def _fake_publish(**kwargs):
            publish_calls.append(kwargs)
            return {"success": True, "object_name": "obj/new.py"}

        monkeypatch.setattr(artifacts_tools.save_web_test_script, "coroutine", _fake_save)
        monkeypatch.setattr(artifacts_tools.publish_script_version, "coroutine", _fake_publish)

        async def _fix(sf_id, err):
            return "print('fixed')"

        async def _verify(sf_id, att_id):
            return True, ""

        result = await run_repair_cycle(session_factory, sf, _fix, _verify)
        # rev55：验证通过不再自动发布 → pending_approval，publish 未被调用
        assert result["status"] == "pending_approval", result
        assert publish_calls == []
        async with AsyncSession(review_engine) as session:
            task = (await session.execute(
                select(WebScriptReview).where(WebScriptReview.sub_function_id == sf)
            )).scalars().first()
            assert task.status == "pending_approval"
            assert task.version_no == 1
            assert task.proposed_attachment_id is not None

        # 审批通过 → 发布 effective + passed
        appr = await approve_script_review(session_factory, sf)
        assert appr["status"] == "passed", appr
        assert len(publish_calls) == 1
        assert publish_calls[0]["verified_attachment_id"] == \
            "00000000-0000-0000-0000-000000000001"
        async with AsyncSession(review_engine) as session:
            task = (await session.execute(
                select(WebScriptReview).where(WebScriptReview.sub_function_id == sf)
            )).scalars().first()
            assert task.status == "passed"

    async def test_approve_without_pending_approval_no_task(self, review_engine):
        """rev55：无待审批任务 → approve 返回 no_task。"""
        from app.agents.tools.web.script_review import approve_script_review

        result = await approve_script_review(_factory(review_engine), uuid4())
        assert result["status"] == "no_task"

    async def test_reject_keeps_proposed_and_marks_rejected(self, review_engine, monkeypatch):
        """rev55：审批拒绝 → rejected（proposed 保留审计，publish 不被调用）。"""
        from app.agents.tools.web import artifacts_tools
        from app.agents.tools.web.script_review import (
            reject_script_review,
            run_repair_cycle,
        )

        session_factory = _factory(review_engine)
        sf = uuid4()
        await enqueue_script_review(session_factory, sf, None, "err")

        async def _fake_save(**kwargs):
            return {"attachment_id": "00000000-0000-0000-0000-000000000002",
                    "version_status": "proposed"}

        publish_calls = []

        async def _fake_publish(**kwargs):
            publish_calls.append(kwargs)
            return {"success": True, "object_name": "obj/x.py"}

        monkeypatch.setattr(artifacts_tools.save_web_test_script, "coroutine", _fake_save)
        monkeypatch.setattr(artifacts_tools.publish_script_version, "coroutine", _fake_publish)

        async def _fix(sf_id, err):
            return "print('fixed2')"

        async def _verify(sf_id, att_id):
            return True, ""

        result = await run_repair_cycle(session_factory, sf, _fix, _verify)
        assert result["status"] == "pending_approval"

        rej = await reject_script_review(session_factory, sf, reason="断言不充分")
        assert rej["status"] == "rejected", rej
        assert publish_calls == []  # 拒绝不发布
        async with AsyncSession(review_engine) as session:
            task = (await session.execute(
                select(WebScriptReview).where(WebScriptReview.sub_function_id == sf)
            )).scalars().first()
            assert task.status == "rejected"
            assert task.version_no == 1

    async def test_repair_cycle_failure_bumps_to_blocked(self, review_engine, monkeypatch):
        """rev40：修复循环——验证失败 → 重试计数 → 达上限 blocked。"""
        from app.agents.tools.web import artifacts_tools

        session_factory = _factory(review_engine)
        sf = uuid4()
        await enqueue_script_review(session_factory, sf, None, "err")

        async def _fake_save(**kwargs):
            return {"attachment_id": "00000000-0000-0000-0000-000000000001",
                    "version_status": "proposed"}

        monkeypatch.setattr(artifacts_tools.save_web_test_script, "coroutine", _fake_save)

        async def _fix(sf_id, err):
            return "print('try')"

        async def _verify(sf_id, att_id):
            return False, "再次失败"

        from app.agents.tools.web.script_review import (
            MAX_REVIEW_RETRIES,
            run_repair_cycle,
        )

        last = None
        for _ in range(MAX_REVIEW_RETRIES):
            last = await run_repair_cycle(session_factory, sf, _fix, _verify)
        assert last["status"] == "blocked"
        async with AsyncSession(review_engine) as session:
            task = (await session.execute(
                select(WebScriptReview).where(WebScriptReview.sub_function_id == sf)
            )).scalars().first()
            assert task.status == "blocked"
            assert task.retry_count == MAX_REVIEW_RETRIES

    async def test_repair_cycle_no_task(self, review_engine):
        from app.agents.tools.web.script_review import run_repair_cycle

        session_factory = _factory(review_engine)

        async def _fix(sf_id, err):
            return "x"

        async def _verify(sf_id, att_id):
            return True, ""

        result = await run_repair_cycle(session_factory, uuid4(), _fix, _verify)
        assert result["status"] == "no_task"

    async def test_approve_publish_failure_not_marked_passed(self, review_engine, monkeypatch):
        """rev55（审批流）：approve 时发布失败 → 任务不标 passed（留 pending_approval）。"""
        from app.agents.tools.web import artifacts_tools
        from app.agents.tools.web.script_review import (
            approve_script_review,
            run_repair_cycle,
        )

        session_factory = _factory(review_engine)
        sf = uuid4()
        await enqueue_script_review(session_factory, sf, None, "err")

        async def _fake_save(**kwargs):
            return {"attachment_id": "00000000-0000-0000-0000-000000000001"}

        async def _fake_publish_fail(**kwargs):
            return {"success": False, "error": "发布失败"}

        monkeypatch.setattr(artifacts_tools.save_web_test_script, "coroutine", _fake_save)
        monkeypatch.setattr(artifacts_tools.publish_script_version, "coroutine", _fake_publish_fail)

        async def _fix(sf_id, err):
            return "x"

        async def _verify(sf_id, att_id):
            return True, ""

        result = await run_repair_cycle(session_factory, sf, _fix, _verify)
        assert result["status"] == "pending_approval"  # 验证通过不自动发布

        # 审批发布失败 → error，任务不得 passed
        appr = await approve_script_review(session_factory, sf)
        assert appr["status"] == "error", appr
        async with AsyncSession(review_engine) as session:
            task = (await session.execute(
                select(WebScriptReview).where(WebScriptReview.sub_function_id == sf)
            )).scalars().first()
            assert task.status == "pending_approval"  # 绝不 passed

    async def test_repair_exception_counts_as_retry(self, review_engine, monkeypatch):
        """rev41：fix 生成异常 → 计入重试（不悬挂任务，不静默 error）。"""
        from app.agents.tools.web import artifacts_tools

        session_factory = _factory(review_engine)
        sf = uuid4()
        await enqueue_script_review(session_factory, sf, None, "err")

        async def _fix_boom(sf_id, err):
            raise RuntimeError("fix 生成器崩溃")

        async def _verify(sf_id, att_id):
            return True, ""

        from app.agents.tools.web.script_review import run_repair_cycle

        result = await run_repair_cycle(session_factory, sf, _fix_boom, _verify)
        assert result["status"] != "error"  # 异常已转化为重试
        assert result["status"] in ("pending", "blocked")  # rev42 状态机
        assert "重试" in result["detail"]
        async with AsyncSession(review_engine) as session:
            task = (await session.execute(
                select(WebScriptReview).where(WebScriptReview.sub_function_id == sf)
            )).scalars().first()
            assert task.retry_count == 1
            assert task.status in ("pending", "blocked")


class TestEnqueueAutoBindAttachment:
    """rev51（评审问题 2）：失败评审强制绑定当前 effective 附件（attachment 不为 NULL）。"""

    @pytest.fixture
    async def bind_engine(self):
        from sqlalchemy import text as _text
        from sqlalchemy.pool import StaticPool

        engine = create_async_engine(
            "sqlite+aiosqlite://", poolclass=StaticPool,
            connect_args={"check_same_thread": False},
        )
        async with engine.begin() as conn:
            await conn.run_sync(WebScriptReview.__table__.create)
            await conn.execute(_text(
                "CREATE TABLE web_tests ("
                "id CHAR(32) PRIMARY KEY, project_id CHAR(32), sub_function_id CHAR(32), "
                "function_id CHAR(32), identifier VARCHAR(50), name VARCHAR(200), "
                "script_path VARCHAR(500), created_at DATETIME)"
            ))
            await conn.execute(_text(
                "CREATE TABLE attachments ("
                "id CHAR(32) PRIMARY KEY, entity_type VARCHAR(50), entity_id CHAR(32), "
                "project_id CHAR(32), file_name VARCHAR(200), file_size INTEGER, "
                "content_type VARCHAR(100), object_name VARCHAR(500), created_by VARCHAR(64), "
                "created_at DATETIME)"
            ))
        yield engine
        await engine.dispose()

    async def test_enqueue_none_binds_current_attachment(self, bind_engine):
        """attachment_id 为空 → 自动解析 WebTest.script_path 对应附件并绑定。"""
        from sqlalchemy import text as _text

        session_factory = _factory(bind_engine)
        sf = uuid4()
        att_id = uuid4()
        obj = "obj/current.py"
        async with AsyncSession(bind_engine) as s:
            await s.execute(_text(
                "INSERT INTO web_tests (id, sub_function_id, script_path, created_at) "
                "VALUES (:id, :sf, :obj, CURRENT_TIMESTAMP)"
            ), {"id": uuid4().hex, "sf": sf.hex, "obj": obj})
            await s.execute(_text(
                "INSERT INTO attachments (id, entity_id, object_name, created_at) "
                "VALUES (:id, :sf, :obj, CURRENT_TIMESTAMP)"
            ), {"id": att_id.hex, "sf": sf.hex, "obj": obj})
            await s.commit()

        task = await enqueue_script_review(session_factory, sf, None, "保存失败")
        assert task is not None
        assert task.attachment_id is not None
        assert task.attachment_id == att_id

    async def test_enqueue_none_no_attachment_keeps_null(self, bind_engine):
        """无 WebTest/附件 → 解析失败不阻断，attachment 保持 NULL（记录原因）。"""
        session_factory = _factory(bind_engine)
        sf = uuid4()
        task = await enqueue_script_review(session_factory, sf, None, "err")
        assert task is not None
        assert task.status == "pending"
        assert task.attachment_id is None


class TestConcurrency:
    async def test_concurrent_claim_only_one_succeeds(self, review_engine):
        """rev42：双 worker 并发领取——原子 UPDATE 只允许一个成功。"""
        import asyncio

        from app.agents.tools.web.script_review import claim_review

        session_factory = _factory(review_engine)
        sf = uuid4()
        await enqueue_script_review(session_factory, sf, None, "err")

        r1, r2 = await asyncio.gather(
            claim_review(session_factory, sf),
            claim_review(session_factory, sf),
        )
        succeeded = [r for r in (r1, r2) if r is not None]
        assert len(succeeded) == 1  # 只有一个 worker 领取成功
        # 状态为 repairing（已被领取）
        async with AsyncSession(review_engine) as session:
            task = (await session.execute(
                select(WebScriptReview).where(WebScriptReview.sub_function_id == sf)
            )).scalars().first()
            assert task.status == "repairing"

    async def test_concurrent_claim_no_double_processing(self, review_engine):
        """rev42：领取后再领取 → 无任务（不可重复消费）。"""
        from app.agents.tools.web.script_review import claim_review

        session_factory = _factory(review_engine)
        sf = uuid4()
        await enqueue_script_review(session_factory, sf, None, "err")
        t1 = await claim_review(session_factory, sf)
        t2 = await claim_review(session_factory, sf)
        assert t1 is not None
        assert t2 is None  # 已领取（repairing），不可再次领取

    async def test_run_pending_repairs_schedules_all(self, review_engine, monkeypatch):
        """rev44：调度入口 run_pending_repairs——扫描全部 pending 并逐个修复。"""
        from app.agents.tools.web import artifacts_tools
        from app.agents.tools.web.script_review import enqueue_script_review, run_pending_repairs

        session_factory = _factory(review_engine)
        sf1, sf2 = uuid4(), uuid4()
        await enqueue_script_review(session_factory, sf1, None, "err1")
        await enqueue_script_review(session_factory, sf2, None, "err2")

        async def _fake_save(**kwargs):
            return {"attachment_id": "00000000-0000-0000-0000-000000000001"}

        async def _fake_publish(**kwargs):
            return {"success": True, "object_name": "obj/new.py"}

        monkeypatch.setattr(artifacts_tools.save_web_test_script, "coroutine", _fake_save)
        monkeypatch.setattr(artifacts_tools.publish_script_version, "coroutine", _fake_publish)

        async def _fix(sf_id, err):
            return "print('fixed')"

        async def _verify(sf_id, att_id):
            return True, ""

        results = await run_pending_repairs(
            session_factory, fix_generator=_fix, execute_verifier=_verify, limit=10
        )
        assert len(results) == 2  # 两个 pending 都处理
        for r in results:
            # rev55（审批流）：验证通过 → pending_approval（不自动发布）
            assert r["status"] == "pending_approval"
