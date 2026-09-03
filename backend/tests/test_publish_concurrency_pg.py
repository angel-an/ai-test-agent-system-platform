"""执行治理层 rev42：PostgreSQL 双会话并发发布测试（真实行锁验证）。

SQLite 忽略 FOR UPDATE，发布行锁需真实 PG 事务并发验证：
- 两个独立 AsyncSession 并发调用 _publish_version_transition（同一子功能）；
- FOR UPDATE 行锁 → 只有一个事务成功发布（proposed→effective），
  另一个等待锁后查到无 proposed → error；
- 断言：全库仅 1 个 effective，无双发布。
PG 不可达时跳过（本地开发环境需 PostgreSQL 运行）。
"""

import asyncio
import sys
from uuid import uuid4

import pytest
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

sys.path.insert(0, r"D:\code\Pyproject\ai-test-agent-system-platform\backend")

from app.agents.tools.web.artifacts_tools import _publish_version_transition
from app.config.settings import get_settings


@pytest.fixture
async def pg_engine():
    s = get_settings()
    eng = create_async_engine(s.postgres_url, pool_pre_ping=True)
    try:
        async with eng.connect() as conn:
            await conn.execute(text("SELECT 1"))
    except Exception as e:
        await eng.dispose()
        pytest.skip(f"PostgreSQL 不可达: {e}")
    yield eng
    await eng.dispose()


@pytest.mark.slow
async def test_pg_concurrent_publish_single_effective(pg_engine):
    """双会话并发发布：FOR UPDATE 行锁保证仅一个 effective（无双发布）。"""
    Session = async_sessionmaker(pg_engine, expire_on_commit=False)
    sf = uuid4()
    sf_hex = sf.hex
    # 测试数据挂在真实项目（PR-2）下以满足 FK；entity_id 为唯一测试 sf，清理不影响真实数据
    pid = "9930b156-5147-40c2-9314-5929d49c3e07"

    # 建测试数据：临时 sub_function（FK 链：web_functions → web_sub_functions）+ 附件 + registry
    fn_id = uuid4().hex
    async with Session() as db:
        await db.execute(text(
            "INSERT INTO web_functions (id, project_id, identifier, display_name, name, "
            "total_sub_functions, total_test_cases, total_test_runs, sort_order) VALUES "
            "(:id, :pid, 'PG-FN', 'pg-fn', 'pg-fn', 1, 0, 0, 0)"
        ), {"id": fn_id, "pid": pid})
        await db.execute(text(
            "INSERT INTO web_sub_functions (id, project_id, function_id, identifier, "
            "display_name, name, test_type, priority, total_test_cases, total_test_runs, "
            "sort_order) VALUES "
            "(:id, :pid, :fn, 'PG-SF', 'pg-sf', 'pg-sf', 'functional', 'P1', 0, 0, 0)"
        ), {"id": sf_hex, "pid": pid, "fn": fn_id})
        await db.execute(text(
            "INSERT INTO attachments (id, entity_type, entity_id, project_id, file_name, "
            "file_size, content_type, object_name, created_by) VALUES "
            "(:id, 'WEB_TEST_SCRIPT', :sf, :pid, 'a.py', 10, 'text/plain', :obj, 'web-agent')"
        ), {"id": uuid4().hex, "sf": sf_hex, "pid": pid, "obj": f"obj/a-{sf_hex}.py"})
        await db.execute(text(
            "INSERT INTO attachments (id, entity_type, entity_id, project_id, file_name, "
            "file_size, content_type, object_name, created_by) VALUES "
            "(:id, 'WEB_TEST_SCRIPT', :sf, :pid, 'p.py', 10, 'text/plain', :obj, 'web-agent')"
        ), {"id": uuid4().hex, "sf": sf_hex, "pid": pid, "obj": f"obj/p-{sf_hex}.py"})
        await db.execute(text(
            "INSERT INTO web_script_registry (id, project_identifier, attachment_id, script_hash, "
            "version_status, created_by) VALUES "
            "(:id, 'PR-TEST', (SELECT id FROM attachments WHERE object_name=:o1), :h1, 'effective', 'web-agent')"
        ), {"id": uuid4().hex, "o1": f"obj/a-{sf_hex}.py", "h1": "hashA"})
        await db.execute(text(
            "INSERT INTO web_script_registry (id, project_identifier, attachment_id, script_hash, "
            "version_status, created_by) VALUES "
            "(:id, 'PR-TEST', (SELECT id FROM attachments WHERE object_name=:o2), :h2, 'proposed', 'web-agent')"
        ), {"id": uuid4().hex, "o2": f"obj/p-{sf_hex}.py", "h2": "hashP"})
        await db.execute(text(
            "INSERT INTO web_tests (id, project_id, sub_function_id, identifier, name, "
            "script_path, script_format, script_language, generated_by_agent, total_pages, "
            "total_flows) VALUES "
            "(:id, :pid, :sf, 'WT-PG', 't', :obj, 'playwright', 'python', 'web-agent', 0, 0)"
        ), {"id": uuid4().hex, "pid": pid, "sf": sf_hex, "obj": f"obj/a-{sf_hex}.py"})
        await db.commit()

    try:
        # 双会话并发发布（真事务竞争；FOR UPDATE 行锁生效）
        s1, s2 = Session(), Session()
        r1, r2 = await asyncio.gather(
            _publish_version_transition(s1, sf),
            _publish_version_transition(s2, sf),
        )
        await s1.close()
        await s2.close()

        successes = [r for r in (r1, r2) if isinstance(r, dict) and r.get("success")]
        errors = [r for r in (r1, r2) if isinstance(r, dict) and r.get("error")]
        assert len(successes) == 1, f"应仅 1 次发布成功，实际 {len(successes)}: {successes}"
        assert len(errors) == 1, f"应 1 次无 proposed（锁竞争），实际 {len(errors)}"

        # 全库仅 1 个 effective（该子功能）
        async with Session() as db:
            rows = (await db.execute(text(
                "SELECT r.version_status, r.script_hash FROM web_script_registry r "
                "JOIN attachments a ON a.id = r.attachment_id "
                "WHERE a.entity_id = :sf"
            ), {"sf": sf_hex})).fetchall()
            eff = [r for r in rows if r[0] == "effective"]
            assert len(eff) == 1, f"应仅 1 个 effective，实际 {eff}"
            assert eff[0][1] == "hashP"  # 发布的是 proposed 版本
    finally:
        # 清理测试数据
        async with Session() as db:
            await db.execute(text(
                "DELETE FROM web_script_registry WHERE attachment_id IN "
                "(SELECT id FROM attachments WHERE entity_id = :sf)"
            ), {"sf": sf_hex})
            await db.execute(text(
                "DELETE FROM attachments WHERE entity_id = :sf"
            ), {"sf": sf_hex})
            await db.execute(text(
                "DELETE FROM web_tests WHERE sub_function_id = :sf"
            ), {"sf": sf_hex})
            await db.execute(text(
                "DELETE FROM web_sub_functions WHERE id = :sf"
            ), {"sf": sf_hex})
            await db.execute(text(
                "DELETE FROM web_functions WHERE id = :fn"
            ), {"fn": fn_id})
            await db.commit()
