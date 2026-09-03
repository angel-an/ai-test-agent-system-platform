"""执行治理层 P0-2 第 2 阶段：真实 Agent 自愈编排测试。

覆盖（验收重点）：
1. LLM 修复生成器（build_llm_fix_generator）：读原脚本 + 失败摘要 → 生成修复，
   剥离代码围栏；mock LLM 验证；
2. verify_proposed_script：proposed 内容执行验证（self_reflect 判定）；
3. Agent 超时/失败 → 计入重试（run_repair_cycle 异常路径已有，补 verify 失败）；
4. 版本不可越权：fix 只返回内容，save 固定 proposed（run_repair_cycle 内部）。
"""

from datetime import datetime, timezone
from types import SimpleNamespace
from uuid import uuid4

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.pool import StaticPool

from app.agents.tools.web.script_repair_agent import (
    build_llm_fix_generator,
    verify_proposed_script,
)
from app.models.attachment import AttachmentEntityType
from app.models.web_script_review import WebScriptReview
from app.models.web_script_registry import WebScriptRegistry


@pytest.fixture
async def binding_engine():
    """真实 SQLite 绑定测试：attachments + registry 表（verify 用真实 session 查询）。"""
    from sqlalchemy import text as _text

    engine = create_async_engine(
        "sqlite+aiosqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False}
    )
    async with engine.begin() as conn:
        await conn.run_sync(WebScriptRegistry.__table__.create)
        await conn.execute(_text(
            "CREATE TABLE attachments ("
            "id CHAR(32) PRIMARY KEY, entity_type VARCHAR(50), entity_id CHAR(32), "
            "project_id CHAR(32), file_name VARCHAR(200), file_size INTEGER, "
            "content_type VARCHAR(100), object_name VARCHAR(500), description TEXT, "
            "created_by VARCHAR(64), step_index INTEGER, created_at DATETIME, updated_at DATETIME)"
        ))
    yield engine
    await engine.dispose()


class TestVerifyRealDbBinding:
    async def _seed(self, engine, sf, att_type="WEB_TEST_SCRIPT", reg_status=None):
        """建附件 + 可选 registry 行；返回附件 UUID。
        att_type 用枚举**名称**（与真实 PG 存储一致，ORM 加载回枚举成员）。"""
        from sqlalchemy.ext.asyncio import async_sessionmaker

        Session = async_sessionmaker(engine, expire_on_commit=False)
        att_id = uuid4()
        async with Session() as db:
            await db.execute(
                text_(
                    "INSERT INTO attachments (id, entity_type, entity_id, project_id, file_name, "
                    "file_size, content_type, object_name, created_by) VALUES "
                    "(:id, :t, :sf, :pid, 'p.py', 10, 'text/plain', :obj, 'web-agent')"
                ),
                {"id": att_id.hex, "t": att_type, "sf": sf.hex, "pid": uuid4().hex,
                 "obj": f"obj/p-{sf.hex}.py"},
            )
            if reg_status:
                await db.execute(
                    text_(
                        "INSERT INTO web_script_registry (id, project_identifier, attachment_id, "
                        "script_hash, version_status, script_language, script_format, created_by) "
                        "VALUES (:id, 'PR-T', :a, :h, :s, '', '', 'web-agent')"
                    ),
                    {"id": uuid4().hex, "a": att_id.hex, "h": "hashP", "s": reg_status},
                )
            await db.commit()
        return att_id

    async def test_verify_proposed_registry_passes(self, binding_engine, monkeypatch):
        """proposed registry 存在 → 验证通过（脚本 rc=0）。"""
        from app.config.minio_client import MinIOClient

        sf = uuid4()
        att_id = await self._seed(binding_engine, sf, reg_status="proposed")
        Session = _factory(binding_engine)
        monkeypatch.setattr(MinIOClient, "download_file", lambda obj: b"print('ok')\n")
        ok, detail = await verify_proposed_script(Session, sf, str(att_id), timeout=30)
        assert ok is True, detail

    async def test_verify_no_registry_rejected(self, binding_engine, monkeypatch):
        """无 registry 记录 → 拒绝（"无对应 proposed registry"）。"""
        from app.config.minio_client import MinIOClient

        sf = uuid4()
        att_id = await self._seed(binding_engine, sf, reg_status=None)
        Session = _factory(binding_engine)
        monkeypatch.setattr(MinIOClient, "download_file", lambda obj: b"print('ok')\n")
        ok, detail = await verify_proposed_script(Session, sf, str(att_id), timeout=30)
        assert ok is False
        assert "proposed" in detail or "registry" in detail

    async def test_verify_effective_registry_rejected(self, binding_engine, monkeypatch):
        """仅 effective registry（非 proposed）→ 拒绝。"""
        from app.config.minio_client import MinIOClient

        sf = uuid4()
        att_id = await self._seed(binding_engine, sf, reg_status="effective")
        Session = _factory(binding_engine)
        monkeypatch.setattr(MinIOClient, "download_file", lambda obj: b"print('ok')\n")
        ok, detail = await verify_proposed_script(Session, sf, str(att_id), timeout=30)
        assert ok is False
        assert "proposed" in detail or "registry" in detail

    async def test_verify_wrong_type_rejected(self, binding_engine, monkeypatch):
        """附件类型非脚本（如 web_test_report）→ 拒绝。"""
        from app.config.minio_client import MinIOClient

        sf = uuid4()
        att_id = await self._seed(binding_engine, sf, att_type="WEB_TEST_REPORT",
                                  reg_status="proposed")
        Session = _factory(binding_engine)
        monkeypatch.setattr(MinIOClient, "download_file", lambda obj: b"print('ok')\n")
        ok, detail = await verify_proposed_script(Session, sf, str(att_id), timeout=30)
        assert ok is False
        assert "非脚本" in detail


def text_(s):
    from sqlalchemy import text as _t

    return _t(s)


class _FakeMessage:
    def __init__(self, content):
        self.content = content


class _FakeLLM:
    def __init__(self, reply):
        self._reply = reply

    async def ainvoke(self, prompt):
        assert "失败摘要" in prompt  # 修复可追踪：失败摘要进入提示词
        return _FakeMessage(self._reply)


@pytest.fixture
async def review_engine():
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


class TestLlmFixGenerator:
    async def test_fix_generator_reads_context_and_strips_fence(self, monkeypatch):
        """LLM 修复：读原脚本（mock MinIO）+ 失败摘要 → 修复内容；剥离 ``` 围栏。"""
        # mock 读取原脚本：_read_script_content 内部用 async_session_factory + MinIO
        # —— 直接 mock build_llm_fix_generator 依赖的 LLM 与脚本读取
        fake_llm = _FakeLLM("```python\nprint('fixed_script')\n```")
        fix = build_llm_fix_generator(model=fake_llm)
        # mock _read_script_content 避免 DB/MinIO
        import app.agents.tools.web.script_repair_agent as mod

        async def _fake_read(session_factory, sf_id):
            return "print('original')"

        monkeypatch.setattr(mod, "_read_script_content", _fake_read)
        from app.config.database import async_session_factory

        content = await fix(uuid4(), "超时失败: 等待选择器超时")
        assert "print('fixed_script')" in content  # 围栏已剥离
        assert "```" not in content

    async def test_fix_generator_error_summary_traceable(self, monkeypatch):
        """修复可追踪：失败摘要进入 LLM 提示词（_FakeLLM 断言）。"""
        fake_llm = _FakeLLM("print('fixed')")
        fix = build_llm_fix_generator(model=fake_llm)
        import app.agents.tools.web.script_repair_agent as mod

        async def _fake_read(session_factory, sf_id):
            return "print('original')"

        monkeypatch.setattr(mod, "_read_script_content", _fake_read)
        from app.config.database import async_session_factory

        content = await fix(uuid4(), "保存广告投放失败")
        assert "fixed" in content


class TestVerifyProposed:
    async def test_verify_passes_simple_script(self, monkeypatch):
        """verify_proposed_script：简单脚本执行 rc=0 → 通过。"""
        from app.config.minio_client import MinIOClient

        sf = uuid4()
        # mock 附件查询（session_factory 同步工厂：async_sessionmaker 约定）+ MinIO 下载
        att = SimpleNamespace(id=uuid4(), entity_id=sf,
                              entity_type=AttachmentEntityType.WEB_TEST_SCRIPT,
                              object_name="obj/proposed.py")

        def _fake_factory():
            class _S:
                async def __aenter__(self):
                    return self

                async def __aexit__(self, *a):
                    return False

                async def execute(self, stmt):
                    return SimpleNamespace(scalars=lambda: SimpleNamespace(first=lambda: att))

            return _S()

        monkeypatch.setattr(MinIOClient, "download_file", lambda obj: b"print('ok')\n")
        ok, detail = await verify_proposed_script(_fake_factory, sf, str(uuid4()), timeout=30)
        assert ok is True, detail
        assert "验证通过" in detail

    async def test_verify_rejects_nonzero_rc_even_if_self_reflect_passed(self, monkeypatch):
        """rev44（评审问题 2）：退出码非零无条件拒绝——即使自评 passed 也不放行。"""
        from app.config.minio_client import MinIOClient

        sf = uuid4()
        att = SimpleNamespace(id=uuid4(), entity_id=sf,
                              entity_type=AttachmentEntityType.WEB_TEST_SCRIPT,
                              object_name="obj/proposed.py")

        def _fake_factory():
            class _S:
                async def __aenter__(self):
                    return self

                async def __aexit__(self, *a):
                    return False

                async def execute(self, stmt):
                    return SimpleNamespace(scalars=lambda: SimpleNamespace(first=lambda: att))

            return _S()

        # 脚本 exit(5) 但写入 self_reflect passed —— 必须拒绝
        script_body = (
            "import json\n"
            "from pathlib import Path\n"
            "Path('self_reflect_result.json').write_text("
            "json.dumps({'execution_status': 'passed'}), encoding='utf-8')\n"
            "import sys; sys.exit(5)\n"
        )
        monkeypatch.setattr(MinIOClient, "download_file", lambda obj: script_body.encode())
        ok, detail = await verify_proposed_script(_fake_factory, sf, str(uuid4()), timeout=30)
        assert ok is False
        assert "returncode" in detail or "非零" in detail

    async def test_verify_checks_attachment_binding(self, monkeypatch):
        """rev44（评审问题 4）：附件不属于指定子功能 → 拒绝（不依赖调用方）。"""
        from app.config.minio_client import MinIOClient

        # 附件 entity_id 与传入 sub_function_id 不同
        other_att = SimpleNamespace(id=uuid4(), entity_id=uuid4(),
                                    entity_type=AttachmentEntityType.WEB_TEST_SCRIPT,
                                    object_name="obj/x.py")
        sf = uuid4()

        def _fake_factory():
            class _S:
                async def __aenter__(self):
                    return self

                async def __aexit__(self, *a):
                    return False

                async def execute(self, stmt):
                    return SimpleNamespace(scalars=lambda: SimpleNamespace(first=lambda: other_att))

            return _S()

        monkeypatch.setattr(MinIOClient, "download_file", lambda obj: b"print(1)\n")
        ok, detail = await verify_proposed_script(_fake_factory, sf, str(uuid4()), timeout=30)
        assert ok is False
        assert "不属于该子功能" in detail

    async def test_verify_failure_timeout_returns_false(self, monkeypatch):
        """verify 失败（脚本 exit 非 0）→ (False, 详情)——可计入重试。"""
        from app.config.minio_client import MinIOClient

        sf = uuid4()
        att = SimpleNamespace(id=uuid4(), entity_id=sf,
                              entity_type=AttachmentEntityType.WEB_TEST_SCRIPT,
                              object_name="obj/proposed.py")

        def _fake_factory():
            class _S:
                async def __aenter__(self):
                    return self

                async def __aexit__(self, *a):
                    return False

                async def execute(self, stmt):
                    return SimpleNamespace(scalars=lambda: SimpleNamespace(first=lambda: att))

            return _S()

        monkeypatch.setattr(MinIOClient, "download_file", lambda obj: b"import sys; sys.exit(3)\n")
        ok, detail = await verify_proposed_script(
            _fake_factory, sf, str(uuid4()), timeout=30
        )
        assert ok is False
        assert "验证失败" in detail or "returncode" in detail


class TestRepairWithRealAgent:
    async def test_repair_cycle_with_llm_fix_and_verify(self, review_engine, monkeypatch):
        """真实 Agent 编排集成：LLM fix + verify 通过 → **pending_approval**（rev55
        审批流：不自动发布）；approve 后才发布 → passed。
        版本不可越权：run_repair_cycle 内部 save 固定 version_mode='proposed'。"""
        from app.agents.tools.web import artifacts_tools
        from app.agents.tools.web.script_review import (
            approve_script_review,
            enqueue_script_review,
            run_repair_cycle,
        )

        session_factory = _factory(review_engine)
        sf = uuid4()
        await enqueue_script_review(session_factory, sf, None, "保存失败")

        save_kwargs = {}
        publish_calls = []

        async def _fake_save(**kwargs):
            save_kwargs.update(kwargs)
            return {"attachment_id": "00000000-0000-0000-0000-000000000001",
                    "version_status": "proposed"}

        async def _fake_publish(**kwargs):
            publish_calls.append(kwargs)
            return {"success": True, "object_name": "obj/new.py"}

        # rev58-fix：整对象替换（SimpleNamespace.coroutine），避免全量顺序中
        # 其他文件对 .coroutine 子属性的残留干扰（NoneType.send 异常溯源）
        monkeypatch.setattr(
            artifacts_tools, "save_web_test_script",
            SimpleNamespace(coroutine=_fake_save),
        )
        monkeypatch.setattr(
            artifacts_tools, "publish_script_version",
            SimpleNamespace(coroutine=_fake_publish),
        )

        # 真实 LLM fix（mock LLM）+ 简单 verify（真实脚本执行）
        from app.agents.tools.web.script_repair_agent import build_llm_fix_generator

        # rev58-fix：mock _read_script_content，避免真实 fix 依赖全局
        # async_session_factory（PG/web_tests 查询）被全量其他测试污染
        import app.agents.tools.web.script_repair_agent as repair_mod

        async def _fake_read(session_factory, sf_id):
            return "print('original')"

        monkeypatch.setattr(repair_mod, "_read_script_content", _fake_read)

        fix = build_llm_fix_generator(model=_FakeLLM("print('fixed_script')"))

        async def _verify(sf_id, att_id):
            return True, "ok"

        result = await run_repair_cycle(session_factory, sf, fix, _verify)
        assert result["status"] == "pending_approval", \
            f"rev55 应 pending_approval，实际 {result}"  # 全量顺序污染排查：输出真实 detail
        assert publish_calls == []
        # 版本不可越权：save 必须 version_mode='proposed'（非 effective）
        assert save_kwargs.get("version_mode") == "proposed"
        async with AsyncSession(review_engine) as session:
            task = (await session.execute(
                select(WebScriptReview).where(WebScriptReview.sub_function_id == sf)
            )).scalars().first()
            assert task.status == "pending_approval"
            assert task.proposed_attachment_id is not None

        # 审批通过 → 发布 + passed
        appr = await approve_script_review(session_factory, sf)
        assert appr["status"] == "passed", appr
        assert len(publish_calls) == 1
        async with AsyncSession(review_engine) as session:
            task = (await session.execute(
                select(WebScriptReview).where(WebScriptReview.sub_function_id == sf)
            )).scalars().first()
            assert task.status == "passed"
