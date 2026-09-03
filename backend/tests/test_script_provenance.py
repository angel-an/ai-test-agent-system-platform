"""执行治理层 2a：脚本来源授权单元测试（rev22 严格模式）。

覆盖：内容哈希、resolve(strict=True) 工作区包含（绝对路径出界 / `..` 逃逸 /
不存在 / 符号链接尽力）、真实项目绑定、DB 原子 upsert（真实 SQLite）、
三要素绑定（真实项目 + 当前附件 + 内容哈希）、跨项目/附件不符拒绝、
严格授权门（sub_function_ids 必填、附件缺失拒绝、多子功能全部绑定）。
"""

import hashlib
from datetime import datetime
from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from app.agents.tools.web.script_provenance import (
    authorize_script_execution,
    check_script_registered,
    register_script_provenance,
    resolve_current_script_attachment,
    resolve_real_project_identifier,
    resolve_within_workspace,
    sha256_hex,
    sha256_hex_file,
)
from app.models.web_script_registry import WebScriptRegistry

TESTS_DIR = Path(__file__).resolve().parent


# ---------------------------------------------------------------------------
# 哈希
# ---------------------------------------------------------------------------

class TestHashing:
    def test_sha256_hex_known_vector(self):
        assert sha256_hex(b"hello") == hashlib.sha256(b"hello").hexdigest()
        assert sha256_hex(b"") == hashlib.sha256(b"").hexdigest()

    def test_sha256_hex_file_matches_content(self):
        p = Path(__file__)
        assert sha256_hex_file(p) == sha256_hex(p.read_bytes())


# ---------------------------------------------------------------------------
# 工作区路径包含
# ---------------------------------------------------------------------------

class TestResolveWithinWorkspace:
    def test_inside_workspace_allowed(self):
        resolved, reason = resolve_within_workspace(Path(__file__), TESTS_DIR)
        assert resolved is not None
        assert reason == "ok"

    def test_absolute_path_outside_denied(self):
        outside = TESTS_DIR.parent / "app" / "main.py"
        resolved, reason = resolve_within_workspace(outside, TESTS_DIR)
        assert resolved is None
        assert "超出工作区" in reason

    def test_dotdot_escape_denied(self):
        escaped = TESTS_DIR / ".." / "app" / "main.py"
        resolved, reason = resolve_within_workspace(escaped, TESTS_DIR)
        assert resolved is None
        assert "超出工作区" in reason

    def test_nonexistent_file_denied(self):
        missing = TESTS_DIR / "no_such_script.py"
        resolved, reason = resolve_within_workspace(missing, TESTS_DIR)
        assert resolved is None
        assert "resolve strict" in reason or "解析失败" in reason


class TestSymlinkEscape:
    def test_symlink_escape_denied(self):
        link = TESTS_DIR / "_symlink_probe"
        target = TESTS_DIR.parent / "app" / "main.py"
        try:
            link.symlink_to(target)
        except (OSError, NotImplementedError):
            pytest.skip("无法创建符号链接（无权限/沙箱），跳过")
        try:
            resolved, reason = resolve_within_workspace(link, TESTS_DIR)
            assert resolved is None
            assert "超出工作区" in reason
        finally:
            try:
                link.unlink()
            except OSError:
                pass


# ---------------------------------------------------------------------------
# 真实项目绑定（rev19）
# ---------------------------------------------------------------------------

class _ScalarResult:
    def __init__(self, value):
        self._value = value

    def scalar_one_or_none(self):
        return self._value


class TestRealProjectBinding:
    @pytest.mark.asyncio
    async def test_resolves_real_project_identifier(self):
        class _S:
            def __init__(self, v):
                self._v = v
                self.stmt = None

            async def execute(self, stmt):
                self.stmt = str(stmt)
                return _ScalarResult(self._v)

        s = _S("PR-2")
        assert await resolve_real_project_identifier(s, uuid4()) == "PR-2"
        assert "projects" in s.stmt and "identifier" in s.stmt

    @pytest.mark.asyncio
    async def test_missing_project_returns_none(self):
        class _S:
            async def execute(self, stmt):
                return _ScalarResult(None)

        assert await resolve_real_project_identifier(_S(), uuid4()) is None


# ---------------------------------------------------------------------------
# 真实 SQLite 内存库：原子 upsert / 三要素 / 版本语义
# ---------------------------------------------------------------------------

@pytest.fixture
async def registry_engine():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(WebScriptRegistry.__table__.create)
    yield engine
    await engine.dispose()


class TestRealDatabaseSemantics:
    @pytest.mark.asyncio
    async def test_upsert_inserts_row_with_fields(self, registry_engine):
        async with AsyncSession(registry_engine) as session:
            aid = uuid4()
            await register_script_provenance(
                session, "PR-1", aid, "hashA", "python", "playwright"
            )
            await session.commit()
            rows = (await session.execute(select(WebScriptRegistry))).scalars().all()
            assert len(rows) == 1
            row = rows[0]
            assert row.project_identifier == "PR-1"
            assert row.attachment_id == aid
            assert row.script_hash == "hashA"
            assert row.script_language == "python"

    @pytest.mark.asyncio
    async def test_duplicate_save_upsert_no_constraint_violation(self, registry_engine):
        async with AsyncSession(registry_engine) as session:
            aid = uuid4()
            await register_script_provenance(
                session, "PR-1", aid, "hashA", "python", "playwright"
            )
            await register_script_provenance(
                session, "PR-1", aid, "hashA", "python", "playwright"
            )
            await session.commit()
            rows = (await session.execute(select(WebScriptRegistry))).scalars().all()
            assert len(rows) == 1
            assert rows[0].script_hash == "hashA"

    @pytest.mark.asyncio
    async def test_update_expires_old_hash(self, registry_engine):
        async with AsyncSession(registry_engine) as session:
            aid = uuid4()
            await register_script_provenance(
                session, "PR-1", aid, "hashA", "python", "playwright"
            )
            await register_script_provenance(
                session, "PR-1", aid, "hashB", "python", "playwright"
            )
            await session.commit()
            ok_old, _ = await check_script_registered(session, "PR-1", "hashA")
            assert ok_old is False
            ok_new, _ = await check_script_registered(session, "PR-1", "hashB")
            assert ok_new is True
            rows = (await session.execute(select(WebScriptRegistry))).scalars().all()
            assert len(rows) == 1

    @pytest.mark.asyncio
    async def test_cross_project_rejected_real_db(self, registry_engine):
        async with AsyncSession(registry_engine) as session:
            await register_script_provenance(
                session, "PR-1", uuid4(), "hashA", "python", "playwright"
            )
            await session.commit()
            ok, reason = await check_script_registered(session, "PR-9", "hashA")
            assert ok is False
            assert "未经平台登记或项目" in reason

    @pytest.mark.asyncio
    async def test_attachment_binding_real_db(self, registry_engine):
        async with AsyncSession(registry_engine) as session:
            att1, att2 = uuid4(), uuid4()
            await register_script_provenance(
                session, "PR-1", att1, "hashA", "python", "playwright"
            )
            await session.commit()
            ok, _ = await check_script_registered(session, "PR-1", "hashA", att1)
            assert ok is True
            ok2, reason = await check_script_registered(session, "PR-1", "hashA", att2)
            assert ok2 is False
            assert "附件归属不符" in reason

    @pytest.mark.asyncio
    async def test_current_version_attachment_binding_real_db(self, registry_engine):
        async with AsyncSession(registry_engine) as session:
            att1 = uuid4()
            await register_script_provenance(
                session, "PR-1", att1, "hashA", "python", "playwright"
            )
            await register_script_provenance(
                session, "PR-1", att1, "hashB", "python", "playwright"
            )
            await session.commit()
            ok_old, _ = await check_script_registered(session, "PR-1", "hashA", att1)
            assert ok_old is False
            ok_new, _ = await check_script_registered(session, "PR-1", "hashB", att1)
            assert ok_new is True


# ---------------------------------------------------------------------------
# 授权门（rev22 严格：sub_function_ids 必填、附件缺失拒绝、多子功能全部绑定）
# ---------------------------------------------------------------------------

class _FilteringResult:
    def __init__(self, rows):
        self._rows = rows

    def scalars(self):
        return self

    def all(self):
        return self._rows

    def scalar_one_or_none(self):
        return self._rows[0] if self._rows else None


class _FilteringSession:
    """模拟 (project, hash[, attachment]) WHERE 过滤：编译 stmt 取绑定参数。"""

    def __init__(self, registered_project, registered_hash, registered_attachment=None):
        self._project = registered_project
        self._hash = registered_hash
        self._attachment = registered_attachment

    async def execute(self, stmt):
        params = stmt.compile().params
        pvals = [str(v) for v in params.values()]
        if self._project in pvals and self._hash in pvals:
            if self._attachment is not None and str(self._attachment) not in pvals:
                return _FilteringResult([])
            return _FilteringResult([object()])
        return _FilteringResult([])


class TestAuthorizeScriptExecution:
    @pytest.mark.asyncio
    async def test_allows_registered_in_workspace(self, monkeypatch):
        from app.agents.tools.web import script_provenance as sp

        att = uuid4()

        async def _resolve(session, sf):
            return att

        monkeypatch.setattr(sp, "resolve_within_workspace", lambda p, r: (p, "ok"))
        monkeypatch.setattr(sp, "sha256_hex_file", lambda p: "hashA")
        monkeypatch.setattr(sp, "resolve_current_script_attachment", _resolve)

        class _FakeCM:
            async def __aenter__(self):
                return _FilteringSession("PR-1", "hashA", att)

            async def __aexit__(self, *a):
                return False

        ok, reason = await sp.authorize_script_execution(
            "PR-1", Path("x.py"), Path("."), lambda: _FakeCM(), sub_function_ids=[uuid4()]
        )
        assert ok is True
        assert reason == "ok"

    @pytest.mark.asyncio
    async def test_no_subfunction_ids_denied(self, monkeypatch):
        # rev22：sub_function_ids 必填，空 → 终局拒绝（无降级回退）
        from app.agents.tools.web import script_provenance as sp

        monkeypatch.setattr(sp, "resolve_within_workspace", lambda p, r: (p, "ok"))
        ok, reason = await sp.authorize_script_execution(
            "PR-1", Path("x.py"), Path("."), lambda: None, sub_function_ids=[]
        )
        assert ok is False
        assert "严格模式要求" in reason

    @pytest.mark.asyncio
    async def test_unresolved_attachment_denied(self, monkeypatch):
        # rev22：附件解析失败/缺失 → 终局拒绝，不降级为"项目+哈希"
        from app.agents.tools.web import script_provenance as sp

        async def _resolve_none(session, sf):
            return None

        monkeypatch.setattr(sp, "resolve_within_workspace", lambda p, r: (p, "ok"))
        monkeypatch.setattr(sp, "sha256_hex_file", lambda p: "hashA")
        monkeypatch.setattr(sp, "resolve_current_script_attachment", _resolve_none)

        class _FakeCM:
            async def __aenter__(self):
                return object()

            async def __aexit__(self, *a):
                return False

        ok, reason = await sp.authorize_script_execution(
            "PR-1", Path("x.py"), Path("."), lambda: _FakeCM(), sub_function_ids=[uuid4()]
        )
        assert ok is False
        assert "无当前脚本附件" in reason

    @pytest.mark.asyncio
    async def test_path_escape_denied(self, monkeypatch):
        from app.agents.tools.web import script_provenance as sp

        monkeypatch.setattr(sp, "resolve_within_workspace", lambda p, r: (None, "路径超出工作区"))
        ok, reason = await sp.authorize_script_execution(
            "PR-1", Path("x.py"), Path("."), lambda: None, sub_function_ids=[uuid4()]
        )
        assert ok is False
        assert "超出工作区" in reason

    @pytest.mark.asyncio
    async def test_unregistered_denied(self, monkeypatch):
        from app.agents.tools.web import script_provenance as sp

        att = uuid4()

        async def _resolve(session, sf):
            return att

        monkeypatch.setattr(sp, "resolve_within_workspace", lambda p, r: (p, "ok"))
        monkeypatch.setattr(sp, "sha256_hex_file", lambda p: "hashother")
        monkeypatch.setattr(sp, "resolve_current_script_attachment", _resolve)

        class _FakeCM:
            async def __aenter__(self):
                return _FilteringSession("PR-1", "hashA", att)

            async def __aexit__(self, *a):
                return False

        ok, reason = await sp.authorize_script_execution(
            "PR-1", Path("x.py"), Path("."), lambda: _FakeCM(), sub_function_ids=[uuid4()]
        )
        assert ok is False
        assert "授权失败" in reason

    @pytest.mark.asyncio
    async def test_attachment_mismatch_denied(self, monkeypatch):
        from app.agents.tools.web import script_provenance as sp

        att = uuid4()

        async def _resolve(session, sf):
            return att

        monkeypatch.setattr(sp, "resolve_within_workspace", lambda p, r: (p, "ok"))
        monkeypatch.setattr(sp, "sha256_hex_file", lambda p: "hashA")
        monkeypatch.setattr(sp, "resolve_current_script_attachment", _resolve)

        class _FakeCM:
            async def __aenter__(self):
                return _FilteringSession("PR-1", "hashA", uuid4())  # 附件不符

            async def __aexit__(self, *a):
                return False

        ok, reason = await sp.authorize_script_execution(
            "PR-1", Path("x.py"), Path("."), lambda: _FakeCM(), sub_function_ids=[uuid4()]
        )
        assert ok is False
        assert "授权失败" in reason

    @pytest.mark.asyncio
    async def test_multi_subfunction_all_must_bind(self, monkeypatch):
        """rev22：多子功能执行——每个子功能的当前附件都必须绑定同一脚本哈希。"""
        from app.agents.tools.web import script_provenance as sp

        sf1, sf2 = uuid4(), uuid4()
        att_common = uuid4()
        att_other = uuid4()

        async def _resolve(session, sf):
            return att_common if sf == sf1 else att_other

        monkeypatch.setattr(sp, "resolve_within_workspace", lambda p, r: (p, "ok"))
        monkeypatch.setattr(sp, "sha256_hex_file", lambda p: "hashA")
        monkeypatch.setattr(sp, "resolve_current_script_attachment", _resolve)

        class _FakeCM:
            async def __aenter__(self):
                return _FilteringSession("PR-1", "hashA", att_common)

            async def __aexit__(self, *a):
                return False

        # sf2 附件不符 → 拒绝
        ok, reason = await sp.authorize_script_execution(
            "PR-1", Path("x.py"), Path("."), lambda: _FakeCM(),
            sub_function_ids=[sf1, sf2],
        )
        assert ok is False
        assert "授权失败" in reason

        # 两个子功能附件一致 → 放行
        async def _resolve_same(session, sf):
            return att_common

        monkeypatch.setattr(sp, "resolve_current_script_attachment", _resolve_same)
        ok2, _ = await sp.authorize_script_execution(
            "PR-1", Path("x.py"), Path("."), lambda: _FakeCM(),
            sub_function_ids=[sf1, sf2],
        )
        assert ok2 is True


# ---------------------------------------------------------------------------
# 当前附件解析：真实数据（rev24：以 WebTest.script_path 为准，而非附件 created_at）
# ---------------------------------------------------------------------------

@pytest.fixture
async def webtest_engine():
    """真实 SQLite：web_tests + attachments 表。"""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.execute(text(
            "CREATE TABLE web_tests (id VARCHAR(36) PRIMARY KEY, script_path VARCHAR(500), "
            "sub_function_id VARCHAR(36), created_at DATETIME)"
        ))
        await conn.execute(text(
            "CREATE TABLE attachments (id VARCHAR(36) PRIMARY KEY, object_name VARCHAR(500), "
            "entity_type VARCHAR(50), entity_id VARCHAR(36), created_at DATETIME)"
        ))
    yield engine
    await engine.dispose()


class TestResolveCurrentAttachmentRealData:
    """rev24：真实数据验证 resolve_current_script_attachment 以 WebTest.script_path 为准，
    而非附件 created_at（"先 .ts 后 .py 再更新旧 .ts" 目标场景）。"""

    @pytest.mark.asyncio
    async def test_follows_webtest_script_path_not_attachment_created_at(self, webtest_engine):
        sf = uuid4()
        att_ts, att_py = uuid4(), uuid4()
        obj_ts = f"web-tests/PR-1/ts-{uuid4().hex}.spec.ts"
        obj_py = f"web-tests/PR-1/py-{uuid4().hex}.py"
        base = datetime(2026, 1, 1)
        async with AsyncSession(webtest_engine) as s:
            # 附件：.ts 先创建；.py 后创建（创建时间 .py 更新）
            # 注意：PGUUID 绑定为 32 位无连字符 hex（.hex），夹具按此格式存储
            await s.execute(
                text("INSERT INTO attachments (id, object_name, created_at) VALUES (:i,:o,:c)"),
                {"i": att_ts.hex, "o": obj_ts, "c": base},
            )
            await s.execute(
                text("INSERT INTO attachments (id, object_name, created_at) VALUES (:i,:o,:c)"),
                {"i": att_py.hex, "o": obj_py, "c": base.replace(month=2, day=1)},
            )
            # WebTest：先指向 .py；后更新指向旧 .ts（"更新旧脚本"场景）
            await s.execute(
                text("INSERT INTO web_tests (id, script_path, sub_function_id, created_at) VALUES (:i,:o,:s,:c)"),
                {"i": uuid4().hex, "o": obj_py, "s": sf.hex, "c": base},
            )
            await s.execute(
                text("INSERT INTO web_tests (id, script_path, sub_function_id, created_at) VALUES (:i,:o,:s,:c)"),
                {"i": uuid4().hex, "o": obj_ts, "s": sf.hex, "c": base.replace(month=3, day=1)},
            )
            resolved = await resolve_current_script_attachment(s, sf)
            # WebTest 最新指向 .ts → 应解析 .ts 附件，而非创建时间更新的 .py
            assert resolved == att_ts

    @pytest.mark.asyncio
    async def test_no_webtest_returns_none(self, webtest_engine):
        async with AsyncSession(webtest_engine) as s:
            assert await resolve_current_script_attachment(s, uuid4()) is None

    @pytest.mark.asyncio
    async def test_webtest_script_path_without_attachment_returns_none(self, webtest_engine):
        sf = uuid4()
        async with AsyncSession(webtest_engine) as s:
            await s.execute(
                text("INSERT INTO web_tests (id, script_path, sub_function_id, created_at) VALUES (:i,:o,:s,:c)"),
                {"i": str(uuid4()), "o": "web-tests/PR-1/missing.py", "s": str(sf), "c": datetime(2026, 1, 1)},
            )
            assert await resolve_current_script_attachment(s, sf) is None
