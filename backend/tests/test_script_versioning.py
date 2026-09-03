"""执行治理层 P0-2：脚本版本化（effective/proposed/old）回归测试。

覆盖（评审建议第 5 项）：
1. 版本状态转换：save proposed → publish → effective，旧 effective 降 old；
2. 旧版本拒绝：发布后旧哈希（old 版本）授权拒绝；
3. proposed 未发布拒绝：check_script_registered 仅认 effective；
4. 并发/upsert：同附件重复登记不违反唯一约束（effective 语义保留）；
5. 全量基线回归由安全套件执行。
"""

from datetime import datetime, timezone
from uuid import uuid4

import pytest
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from app.agents.tools.web.artifacts_tools import _publish_version_transition
from app.agents.tools.web.script_provenance import (
    check_script_registered,
    register_script_provenance,
)
from app.models.attachment import Attachment, AttachmentEntityType
from app.models.web_script_registry import WebScriptRegistry
from app.models.web_test import WebTest


@pytest.fixture
async def version_engine():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(WebScriptRegistry.__table__.create)
        await conn.run_sync(Attachment.__table__.create)
        # WebTest 含 JSONB 列（SQLite 不支持），手动建表（JSONB 列用 TEXT）
        await conn.execute(text(
            "CREATE TABLE web_tests ("
            "id CHAR(36) PRIMARY KEY, project_id CHAR(36), folder_id CHAR(36), "
            "test_case_id CHAR(36), function_id CHAR(36), sub_function_id CHAR(36), "
            "identifier VARCHAR(50), name VARCHAR(500), description TEXT, "
            "base_url VARCHAR(500), script_path VARCHAR(500), "
            "script_format VARCHAR(50), script_language VARCHAR(50), "
            "test_config TEXT, target_pages TEXT, test_flows TEXT, "
            "generated_by_agent VARCHAR(50), generation_params TEXT, "
            "total_pages INTEGER, total_flows INTEGER, "
            "created_at DATETIME, updated_at DATETIME)"
        ))
    yield engine
    await engine.dispose()


def _mk_attachment(session, sf_id, obj_name):
    att = Attachment(
        entity_type=AttachmentEntityType.WEB_TEST_SCRIPT,
        entity_id=sf_id,
        project_id=uuid4(),
        file_name="test-script.py",
        file_size=10,
        content_type="text/plain",
        object_name=obj_name,
        created_by="web-agent",
        created_at=datetime.now(timezone.utc),
    )
    session.add(att)
    return att


async def _mk_web_test(session, sf_id, obj_name):
    wt = WebTest(
        project_id=uuid4(),
        sub_function_id=sf_id,
        identifier="WT-TEST",
        name="t",
        script_path=obj_name,
        script_format="playwright",
        script_language="python",
    )
    session.add(wt)
    return wt


class TestVersionStatusTransition:
    async def test_register_effective_and_proposed(self, version_engine):
        async with AsyncSession(version_engine) as session:
            sf = uuid4()
            a1 = _mk_attachment(session, sf, "obj/a.py")
            await session.flush()
            await register_script_provenance(session, "PR-1", a1.id, "hashA", "python", "playwright",
                                             version_status="effective")
            a2 = _mk_attachment(session, sf, "obj/a-20260831.py")
            await session.flush()
            await register_script_provenance(session, "PR-1", a2.id, "hashB", "python", "playwright",
                                             version_status="proposed")
            await session.commit()
            rows = (await session.execute(select(WebScriptRegistry))).scalars().all()
            statuses = {r.script_hash: r.version_status for r in rows}
            assert statuses == {"hashA": "effective", "hashB": "proposed"}

    async def test_publish_transition(self, version_engine):
        async with AsyncSession(version_engine) as session:
            sf = uuid4()
            a1 = _mk_attachment(session, sf, "obj/a.py")
            await session.flush()
            await register_script_provenance(session, "PR-1", a1.id, "hashA", "python", "playwright")
            a2 = _mk_attachment(session, sf, "obj/a-20260831.py")
            await session.flush()
            await register_script_provenance(session, "PR-1", a2.id, "hashB", "python", "playwright",
                                             version_status="proposed")
            wt = await _mk_web_test(session, sf, "obj/a.py")
            await session.commit()

            result = await _publish_version_transition(session, sf)
            assert result.get("success") is True
            assert result.get("object_name") == "obj/a-20260831.py"
            assert "hashA" in result.get("old_versions_downgraded", [])

            rows = (await session.execute(select(WebScriptRegistry))).scalars().all()
            statuses = {r.script_hash: r.version_status for r in rows}
            assert statuses == {"hashA": "old", "hashB": "effective"}
            # WebTest.script_path 已更新
            await session.refresh(wt)
            assert wt.script_path == "obj/a-20260831.py"

    async def test_authorization_only_effective(self, version_engine):
        async with AsyncSession(version_engine) as session:
            sf = uuid4()
            a1 = _mk_attachment(session, sf, "obj/a.py")
            await session.flush()
            await register_script_provenance(session, "PR-1", a1.id, "hashA", "python", "playwright")
            a2 = _mk_attachment(session, sf, "obj/a-20260831.py")
            await session.flush()
            await register_script_provenance(session, "PR-1", a2.id, "hashB", "python", "playwright",
                                             version_status="proposed")
            await session.commit()

            # effective 通过
            ok, _ = await check_script_registered(session, "PR-1", "hashA")
            assert ok is True
            # proposed 拒绝（未发布）
            ok, reason = await check_script_registered(session, "PR-1", "hashB")
            assert ok is False
            assert "effective" in reason

    async def test_old_version_rejected_after_publish(self, version_engine):
        async with AsyncSession(version_engine) as session:
            sf = uuid4()
            a1 = _mk_attachment(session, sf, "obj/a.py")
            await session.flush()
            await register_script_provenance(session, "PR-1", a1.id, "hashA", "python", "playwright")
            a2 = _mk_attachment(session, sf, "obj/a-20260831.py")
            await session.flush()
            await register_script_provenance(session, "PR-1", a2.id, "hashB", "python", "playwright",
                                             version_status="proposed")
            await _mk_web_test(session, sf, "obj/a.py")
            await session.commit()

            await _publish_version_transition(session, sf)
            # 发布后：旧版本 hashA（old）拒绝，新版本 hashB（effective）通过
            ok_old, _ = await check_script_registered(session, "PR-1", "hashA")
            assert ok_old is False
            ok_new, _ = await check_script_registered(session, "PR-1", "hashB")
            assert ok_new is True

    async def test_concurrent_proposed_publish_takes_latest(self, version_engine):
        """rev40：并发场景——同一子功能多个 proposed，publish 只取最新（created_at desc），
        其余保持 proposed（待后续发布），旧 effective 降 old。"""
        from datetime import timedelta

        async with AsyncSession(version_engine) as session:
            sf = uuid4()
            a1 = _mk_attachment(session, sf, "obj/a.py")
            await session.flush()
            await register_script_provenance(session, "PR-1", a1.id, "hashA", "python", "playwright")
            # 两个 proposed（第二个时间 +1s，确保 created_at 排序确定）
            for i, obj in enumerate(["obj/p1.py", "obj/p2.py"]):
                a = _mk_attachment(session, sf, obj)
                await session.flush()
                await register_script_provenance(
                    session, "PR-1", a.id, f"hashP{i}", "python", "playwright",
                    version_status="proposed")
                if i == 1:
                    # 第二个 proposed 的 registry 行时间 +1s（同秒并发场景的确定性修正）
                    await session.execute(text(
                        "UPDATE web_script_registry SET created_at = "
                        "datetime(created_at, '+1 second') WHERE script_hash='hashP1'"
                    ))
            await _mk_web_test(session, sf, "obj/a.py")
            await session.commit()

            result = await _publish_version_transition(session, sf)
            # 最新 proposed（hashP1）发布为 effective，旧 eff（hashA）降 old
            assert result.get("success") is True
            assert result.get("object_name") == "obj/p2.py"
            rows = (await session.execute(select(WebScriptRegistry))).scalars().all()
            statuses = {r.script_hash: r.version_status for r in rows}
            assert statuses == {"hashA": "old", "hashP0": "proposed", "hashP1": "effective"}

    async def test_concurrent_publish_single_success(self, version_engine):
        """rev42：并发发布——第一次发布成功后，第二次无 proposed（不产生双 effective）。"""
        async with AsyncSession(version_engine) as session:
            sf = uuid4()
            a1 = _mk_attachment(session, sf, "obj/a.py")
            await session.flush()
            await register_script_provenance(session, "PR-1", a1.id, "hashA", "python", "playwright")
            a2 = _mk_attachment(session, sf, "obj/p1.py")
            await session.flush()
            await register_script_provenance(session, "PR-1", a2.id, "hashP", "python", "playwright",
                                             version_status="proposed")
            await _mk_web_test(session, sf, "obj/a.py")
            await session.commit()

            r1 = await _publish_version_transition(session, sf)
            assert r1.get("success") is True
            # 第二次发布：proposed 已被消费 → 无 proposed
            r2 = await _publish_version_transition(session, sf)
            assert r2.get("error") is not None
            # 全库仅 1 个 effective（hashP），hashA 为 old
            rows = (await session.execute(select(WebScriptRegistry))).scalars().all()
            eff = [r for r in rows if r.version_status == "effective"]
            assert len(eff) == 1 and eff[0].script_hash == "hashP"

    async def test_publish_locks_verified_attachment(self, version_engine):
        """rev44：verified_attachment_id 锁定已验证版本发布（并发下不发布未验证 B）。"""
        async with AsyncSession(version_engine) as session:
            sf = uuid4()
            a1 = _mk_attachment(session, sf, "obj/a.py")
            await session.flush()
            await register_script_provenance(session, "PR-1", a1.id, "hashA", "python", "playwright")
            a_p1 = _mk_attachment(session, sf, "obj/p1.py")
            await session.flush()
            await register_script_provenance(session, "PR-1", a_p1.id, "hashP1", "python", "playwright",
                                             version_status="proposed")
            a_p2 = _mk_attachment(session, sf, "obj/p2.py")
            await session.flush()
            await register_script_provenance(session, "PR-1", a_p2.id, "hashP2", "python", "playwright",
                                             version_status="proposed")
            await _mk_web_test(session, sf, "obj/a.py")
            a_p1_id = a_p1.id  # commit 前缓存（expire 后访问触发 lazy load）
            await session.commit()

            # 锁定已验证附件 p1 发布（即使 p2 更新）：发布的是 p1
            result = await _publish_version_transition(session, sf, verified_attachment_id=a_p1_id)
            assert result.get("success") is True
            assert result.get("object_name") == "obj/p1.py"
            rows = (await session.execute(select(WebScriptRegistry))).scalars().all()
            statuses = {r.script_hash: r.version_status for r in rows}
            assert statuses == {"hashA": "old", "hashP1": "effective", "hashP2": "proposed"}

    async def test_upsert_keeps_unique_per_attachment(self, version_engine):
        """并发/重复登记：同附件 upsert 不违反唯一约束，状态保留。"""
        async with AsyncSession(version_engine) as session:
            sf = uuid4()
            a1 = _mk_attachment(session, sf, "obj/a.py")
            await session.flush()
            a1_uuid = a1.id  # 缓存 UUID 对象（commit 后属性 expire，不再访问 a1.id）
            a1_id = str(a1_uuid)
            await register_script_provenance(session, "PR-1", a1_uuid, "hashA", "python", "playwright",
                                             version_status="proposed")
            await session.commit()
            # 再次登记同附件（upsert）→ 仍 1 行，状态保持 proposed
            await register_script_provenance(session, "PR-1", a1_uuid, "hashA2", "python", "playwright",
                                             version_status="proposed")
            await session.commit()
            rows = (await session.execute(text(
                "SELECT count(*) FROM web_script_registry WHERE attachment_id=:a"
            ), {"a": a1_uuid.hex})).scalar()
            assert rows == 1
            status = (await session.execute(text(
                "SELECT version_status FROM web_script_registry WHERE attachment_id=:a"
            ), {"a": a1_uuid.hex})).scalar()
            assert status == "proposed"
