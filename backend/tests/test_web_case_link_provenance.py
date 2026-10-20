# -*- coding: utf-8 -*-
"""执行三要素修复（rev62）回归测试：minimal-loop 产物可执行性。

覆盖：
1. 子功能自动匹配：能选出"储值企业充值"而非"储值免单"（真实 PR-2 名称）；
2. 方案 A 授权：按**正在执行的 WebTest 脚本对象名**解析附件 → 登记通过即放行；
3. 未登记脚本 → 仍拒绝（fail-closed 未放宽）；
4. legacy 解析不被 minimal-loop 自动产物顶掉（正式脚本不受影响）；
5. 不传 script_object_name 时，sub_function_ids 仍为必填（原行为不变）。
"""

from datetime import datetime, timezone
from pathlib import Path
from unittest import mock
from uuid import uuid4

import pytest
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from app.agents.tools.web.script_provenance import (
    MINIMAL_LOOP_AGENT,
    authorize_script_execution,
    register_script_provenance,
    resolve_current_script_attachment,
    sha256_hex,
    sha256_hex_file,
)
from app.models.attachment import Attachment, AttachmentEntityType
from app.models.web_script_registry import WebScriptRegistry
from app.models.web_test import WebTest
from app.services.web_case_link_service import _score_sub_function_match


# ---------------------------------------------------------------------------
# 1. 子功能匹配（纯函数，使用 PR-2 真实名称）
# ---------------------------------------------------------------------------

CASE_TEXT = " ".join([
    "TC-957915",
    "B2B储值订单不能退款，储值订单不在C端显示",
    "小杨生煎-B端储值充值",
    "sit",
    "基于上传需求文档《小杨生煎-B端储值充值.docx》生成；场景来源：B2B储值订单不能退款，储值订单不在C端显示。",
    "已按上传需求准备角色、权限和前置数据。",
    "{}",
])


class TestSubFunctionMatching:
    def test_picks_enterprise_recharge_over_coupon(self):
        """储值企业充值 (WSF-1100) 得分应达阈值，储值免单类应低于阈值。"""
        enterprise = _score_sub_function_match(
            CASE_TEXT, "储值企业充值 CRUD 测试", None, None,
            "营销云-忠诚度-储值企业充值", "营销云-忠诚度-储值企业充值", "营销云-忠诚度",
        )
        coupon = _score_sub_function_match(
            CASE_TEXT, "储值免单活动CRUD测试", None, None,
            "储值免单活动", "储值免单活动", None,
        )
        assert enterprise >= 4, f"储值企业充值 匹配分过低: {enterprise}"
        assert coupon < 4, f"储值免单 匹配分不应达阈值: {coupon}"

    def test_unrelated_names_below_threshold(self):
        assert _score_sub_function_match(
            CASE_TEXT, "账号密码登录", None, None, "登录功能", "登录功能", "用户认证",
        ) < 4


# ---------------------------------------------------------------------------
# 2-5. 授权门（sqlite 文件库，保证 authorize 内部新 session 可见数据）
# ---------------------------------------------------------------------------

@pytest.fixture
async def gate_env(tmp_path):
    db_file = tmp_path / "gate.db"
    engine = create_async_engine(f"sqlite+aiosqlite:///{db_file}")
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
            # N7 编译清单列（迁移 0024）：手写 DDL 必须与模型同步，否则 ORM 插入报
            # "table web_tests has no column named compile_status"
            "compile_status VARCHAR(24), compiled_check_count INTEGER, "
            "compiled_skipped_count INTEGER, compiled_at DATETIME, "
            "is_legacy_asset BOOLEAN DEFAULT 0, "
            "created_at DATETIME, updated_at DATETIME)"
        ))
        # 归属校验需要：WebSubFunction 存在性查询（sqlite 下 Uuid 存 32 位十六进制）
        await conn.execute(text(
            "CREATE TABLE web_sub_functions (id CHAR(32) PRIMARY KEY)"
        ))

    workspace = tmp_path / "ws"
    (workspace / "tests").mkdir(parents=True)
    script_file = workspace / "tests" / "run.spec.ts"
    script_content = "// minimal loop smoke\n"
    script_file.write_bytes(script_content.encode("utf-8"))
    script_hash = sha256_hex_file(script_file)

    def session_factory():
        return AsyncSession(engine)

    yield {
        "engine": engine,
        "workspace": workspace,
        "script_file": script_file,
        "script_hash": script_hash,
        "object_name": "web-tests/PR-1/minimal-loop/case-1/abc/test-script.spec.ts",
        "session_factory": session_factory,
    }
    await engine.dispose()


async def _add_sub_function(session, sf_id):
    """登记一个 Web 子功能（仅 id，供归属校验查询）。"""
    await session.execute(
        text("INSERT INTO web_sub_functions (id) VALUES (:id)"),
        {"id": sf_id.hex},
    )


async def _add_attachment(session, sf_id, object_name, entity_id=None):
    att = Attachment(
        entity_type=AttachmentEntityType.WEB_TEST_SCRIPT,
        entity_id=entity_id or sf_id,
        project_id=uuid4(),
        file_name=Path(object_name).name,
        file_size=32,
        content_type="text/plain",
        object_name=object_name,
        created_by="test",
        created_at=datetime.now(timezone.utc),
    )
    session.add(att)
    await session.flush()
    return att


async def _add_web_test(session, sf_id, object_name, agent):
    wt = WebTest(
        project_id=uuid4(),
        sub_function_id=sf_id,
        identifier=f"WT-{uuid4().hex[:6]}",
        name="t",
        script_path=object_name,
        script_format="playwright",
        script_language="typescript",
        generated_by_agent=agent,
        created_at=datetime.now(timezone.utc),
    )
    session.add(wt)
    await session.flush()
    return wt


class TestAuthorizeByExecutingWebTest:
    async def test_registered_minimal_loop_asset_is_authorized(self, gate_env):
        """方案 A：按正在执行的 WebTest 对象名解析 → 登记后放行。"""
        sf_id = uuid4()
        async with AsyncSession(gate_env["engine"]) as session:
            await _add_sub_function(session, sf_id)
            att = await _add_attachment(session, sf_id, gate_env["object_name"])
            await register_script_provenance(
                session, "PR-1", att.id, gate_env["script_hash"],
                "typescript", "playwright", version_status="effective",
            )
            await session.commit()

        ok, reason = await authorize_script_execution(
            "PR-1",
            gate_env["script_file"],
            gate_env["workspace"],
            gate_env["session_factory"],
            [sf_id],
            script_object_name=gate_env["object_name"],
        )
        assert ok is True, reason
        assert reason == "ok"

    async def test_unregistered_script_is_still_rejected(self, gate_env):
        """fail-closed 未放宽：附件存在但未登记 → 拒绝。"""
        sf_id = uuid4()
        async with AsyncSession(gate_env["engine"]) as session:
            await _add_attachment(session, sf_id, gate_env["object_name"])
            await session.commit()

        ok, reason = await authorize_script_execution(
            "PR-1",
            gate_env["script_file"],
            gate_env["workspace"],
            gate_env["session_factory"],
            [sf_id],
            script_object_name=gate_env["object_name"],
        )
        assert ok is False
        assert "登记" in reason

    async def test_missing_attachment_is_rejected(self, gate_env):
        """对象名解析不到附件 → 拒绝（不降级为 legacy 解析）。"""
        sf_id = uuid4()
        ok, reason = await authorize_script_execution(
            "PR-1",
            gate_env["script_file"],
            gate_env["workspace"],
            gate_env["session_factory"],
            [sf_id],
            script_object_name="web-tests/PR-1/minimal-loop/nope/test-script.spec.ts",
        )
        assert ok is False
        assert "附件不存在" in reason

    async def test_sub_function_ids_still_required_without_object_name(self, gate_env):
        """不传 script_object_name 时行为不变：sub_function_ids 必填。"""
        ok, reason = await authorize_script_execution(
            "PR-1",
            gate_env["script_file"],
            gate_env["workspace"],
            gate_env["session_factory"],
            None,
        )
        assert ok is False
        assert "sub_function_id" in reason

    async def test_borrowing_other_sub_function_script_is_rejected(self, gate_env):
        """附件归属其它子功能时，用本子功能声明执行 → 拒绝（防跨子功能借用）。"""
        owner_sf = uuid4()
        caller_sf = uuid4()
        async with AsyncSession(gate_env["engine"]) as session:
            await _add_sub_function(session, owner_sf)
            await _add_sub_function(session, caller_sf)
            att = await _add_attachment(
                session, owner_sf, gate_env["object_name"], entity_id=owner_sf
            )
            await register_script_provenance(
                session, "PR-1", att.id, gate_env["script_hash"],
                "typescript", "playwright", version_status="effective",
            )
            await session.commit()

        ok, reason = await authorize_script_execution(
            "PR-1",
            gate_env["script_file"],
            gate_env["workspace"],
            gate_env["session_factory"],
            [caller_sf],
            script_object_name=gate_env["object_name"],
        )
        assert ok is False
        assert "不一致" in reason


# ---------------------------------------------------------------------------
# 6. fail-fast：不创建"注定失败"的运行（M3/M4 验收）
# ---------------------------------------------------------------------------

def _stub_service(monkeypatch, *, base_url, sub_function):
    """构造只走前置判定的服务桩（不触 DB）。"""
    from types import SimpleNamespace

    from app.services.web_case_link_service import WebCaseLinkService

    svc = WebCaseLinkService.__new__(WebCaseLinkService)
    svc.session = SimpleNamespace(
        commit=mock.AsyncMock(),
        flush=mock.AsyncMock(),
        refresh=mock.AsyncMock(),
        execute=mock.AsyncMock(),
        get=mock.AsyncMock(),
        add=mock.Mock(),
    )
    project_id = uuid4()
    monkeypatch.setattr(
        svc, "_get_project",
        mock.AsyncMock(return_value=SimpleNamespace(id=project_id, identifier="PR-X")),
    )
    monkeypatch.setattr(
        svc, "_select_test_case",
        mock.AsyncMock(return_value=SimpleNamespace(
            id=uuid4(), project_id=project_id, custom_fields={}, description="",
            automation_status=None, identifier="TC-X",
            # 用例必须带可正向识别的业务信息：模板门要求"脚本模板 ↔ 业务"正向匹配，
            # 无匹配时（正确行为）抛 [unsupported_web_template]。
            name="B端企业储值充值-储值账户列表冒烟", module="小杨生煎-B端储值充值",
            case_kind="sit", preconditions="",
        )),
    )
    monkeypatch.setattr(
        svc, "_get_existing_web_test", mock.AsyncMock(return_value=None)
    )
    monkeypatch.setattr(
        svc, "_load_batch_cases", mock.AsyncMock(return_value=[])
    )
    monkeypatch.setattr(
        svc, "_resolve_path_config",
        mock.AsyncMock(return_value={
            "base_url": base_url,
            "navigation_path": ["登录", "进入菜单"],
            "matched": False,
            "template_id": None,
            "base_url_source": "request",
            "login_profile": "WEB_TEST_DEFAULT",
            "score": 0,
        }),
    )
    monkeypatch.setattr(svc, "_resolve_sub_function", mock.AsyncMock(return_value=sub_function))
    create_asset = mock.AsyncMock()
    monkeypatch.setattr(svc, "_create_web_test_asset", create_asset)
    return svc, create_asset


class TestFailFastDoesNotCreateRun:
    async def test_no_sub_function_blocks_run_creation(self, monkeypatch):
        """解析不到子功能 → 拒绝建运行，且不创建任何 Web 资产。"""
        svc, create_asset = _stub_service(
            monkeypatch, base_url="https://console-uat.example.com/login", sub_function=None
        )
        with pytest.raises(ValueError, match="未找到可绑定的 Web 子功能"):
            await svc.create_minimal_loop("PR-X", [uuid4()], create_test_run=True)
        create_asset.assert_not_awaited()

    async def test_placeholder_base_url_blocks_run_creation(self, monkeypatch):
        """base_url 仍是占位地址 → 拒绝建运行。"""
        from types import SimpleNamespace

        svc, create_asset = _stub_service(
            monkeypatch,
            base_url="https://example.invalid/console/login",
            sub_function=SimpleNamespace(id=uuid4(), function_id=uuid4()),
        )
        with pytest.raises(ValueError, match="占位地址"):
            await svc.create_minimal_loop("PR-X", [uuid4()], create_test_run=True)
        create_asset.assert_not_awaited()

    async def test_asset_still_created_when_run_not_requested(self, monkeypatch):
        """只生成资产（不建运行）时，缺绑定不应阻断。"""
        from types import SimpleNamespace

        svc, create_asset = _stub_service(
            monkeypatch,
            base_url="https://example.invalid/console/login",
            sub_function=SimpleNamespace(id=uuid4(), function_id=uuid4()),
        )
        create_asset.return_value = SimpleNamespace(
            id=uuid4(), identifier="WT-X", name="冒烟", script_path="obj/x.spec.ts"
        )
        monkeypatch.setattr(svc, "_ensure_test_case_tags", mock.AsyncMock())
        monkeypatch.setattr(svc, "_merge_web_path_description", lambda d, n: d or "")
        await svc.create_minimal_loop("PR-X", [uuid4()], create_test_run=False)
        create_asset.assert_awaited()


class TestLegacyPathCompatibility:
    """复核意见第 5 条：不传 script_object_name 时旧行为必须不变。"""

    async def test_legacy_authorization_unchanged_without_object_name(self, gate_env):
        """不传 script_object_name → 仍按"子功能当前附件"解析，且正式脚本授权通过。"""
        sf_id = uuid4()
        agent_obj = f"web-tests/PR-1/sub-functions/{sf_id}/test-script.py"
        async with AsyncSession(gate_env["engine"]) as session:
            att = await _add_attachment(session, sf_id, agent_obj)
            await _add_web_test(session, sf_id, agent_obj, "web-agent")
            # 更晚创建的 minimal-loop 产物（旧逻辑下会把"当前脚本"顶掉）
            await _add_web_test(
                session, sf_id, gate_env["object_name"], MINIMAL_LOOP_AGENT
            )
            await register_script_provenance(
                session, "PR-1", att.id, gate_env["script_hash"],
                "typescript", "playwright", version_status="effective",
            )
            await session.commit()

        ok, reason = await authorize_script_execution(
            "PR-1",
            gate_env["script_file"],
            gate_env["workspace"],
            gate_env["session_factory"],
            [sf_id],
            # 注意：故意不传 script_object_name
        )
        assert ok is True, reason
        assert reason == "ok"


class TestProvenanceRegistrationIdempotency:
    """复核意见第 4 条：重复登记幂等、哈希基于最终脚本内容。"""

    async def test_reregistration_keeps_single_row_and_updates_hash(self, gate_env):
        from types import SimpleNamespace

        from app.services.web_case_link_service import WebCaseLinkService

        sf_id = uuid4()
        web_test = SimpleNamespace(project_id=uuid4(), id=uuid4())
        async with AsyncSession(gate_env["engine"]) as session:
            await _add_attachment(session, sf_id, gate_env["object_name"])
            await session.commit()

            svc = WebCaseLinkService(session)
            await svc._register_script_provenance(
                "PR-1", web_test, "v1\n",
                object_name=gate_env["object_name"], sub_function_id=sf_id,
            )
            await session.commit()
            rows_v1 = (
                await session.execute(select(WebScriptRegistry))
            ).scalars().all()
            # 立即取值：ORM 实例属性在后续 commit 后会被过期重载
            count_v1, hash_v1 = len(rows_v1), rows_v1[0].script_hash

            await svc._register_script_provenance(
                "PR-1", web_test, "v2 changed\n",
                object_name=gate_env["object_name"], sub_function_id=sf_id,
            )
            await session.commit()
            rows_v2 = (
                await session.execute(select(WebScriptRegistry))
            ).scalars().all()
            count_v2, hash_v2 = len(rows_v2), rows_v2[0].script_hash

        assert count_v1 == 1, "同一附件应只有一行登记（初次）"
        assert count_v2 == 1, "重复登记不得新增行（幂等）"
        assert hash_v1 == sha256_hex("v1\n".encode("utf-8"))
        assert hash_v2 == sha256_hex("v2 changed\n".encode("utf-8")), \
            "哈希必须更新为最终脚本内容"

class TestLegacyResolutionNotHijacked:
    async def test_agent_script_wins_over_minimal_loop_asset(self, gate_env):
        """同一子功能下：正式脚本（Agent 产物）不被 minimal-loop 冒烟顶掉。"""
        sf_id = uuid4()
        agent_obj = "web-tests/PR-1/sub-functions/%s/test-script.py" % sf_id
        async with AsyncSession(gate_env["engine"]) as session:
            agent_att = await _add_attachment(session, sf_id, agent_obj)
            # 先创建正式 WebTest，再创建（更晚的）minimal-loop WebTest
            await _add_web_test(session, sf_id, agent_obj, "web-agent")
            await _add_web_test(
                session, sf_id, gate_env["object_name"], MINIMAL_LOOP_AGENT
            )
            expected = agent_att.id  # commit 前取值（commit 后属性会过期）
            await session.commit()

        async with AsyncSession(gate_env["engine"]) as session:
            resolved = await resolve_current_script_attachment(session, sf_id)
        assert resolved == expected, "legacy 解析被 minimal-loop 产物顶掉了"

    async def test_minimal_loop_used_when_no_agent_script(self, gate_env):
        """只有自动产物时，legacy 解析回退取它（不返回 None）。"""
        sf_id = uuid4()
        async with AsyncSession(gate_env["engine"]) as session:
            att = await _add_attachment(session, sf_id, gate_env["object_name"])
            await _add_web_test(
                session, sf_id, gate_env["object_name"], MINIMAL_LOOP_AGENT
            )
            expected = att.id  # commit 前取值（commit 后属性会过期）
            await session.commit()

        async with AsyncSession(gate_env["engine"]) as session:
            resolved = await resolve_current_script_attachment(session, sf_id)
        assert resolved == expected
