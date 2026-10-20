# -*- coding: utf-8 -*-
"""契约驱动生成与执行前环境门禁（P1）测试。

覆盖范围（对应 P1 可交付项）：
1. 契约生成测试计划 / 用例 / Playwright 脚本；
2. `run_id` 全链路传递（预检 context → 产物登记 → run 记录）；
3. 缺少环境配置时返回 blocked；
4. **blocked 状态不发起第三方请求**（不下载脚本、不调用执行器）；
5. 既有调用方在默认配置下行为保持不变（`requires_env` 缺省 = 门禁关闭）；
6. completed / failed / skipped 状态正确回填；
7. 密钥脱敏：明文不进入落库配置。

测试策略：
- 单元层直接测纯函数（`check_env_requirements` / `redact_config` / 状态映射读法）；
- 服务与 API 层用专用 PostgreSQL 库 + httpx ASGITransport；
- `execute_with_preflight` 在需要时被 patch，避免真的起 Playwright 进程；
- MinIO 为远程实例且可用，脚本附件上传走真实路径（不 mock），
  以便验证"脚本确实被持久化"。
"""

from __future__ import annotations

import json
import uuid

import pytest


# ---------------------------------------------------------------------------
# 1. 单元层：环境门禁纯函数
# ---------------------------------------------------------------------------

class TestCheckEnvRequirements:
    """`check_env_requirements` 是门禁的核心判定，必须先固化行为。"""

    def test_disabled_when_requires_env_absent(self):
        """既有调用方不传 requires_env → 门禁关闭，返回空（行为不变）。"""
        from app.services.api_endpoint_script_executor import check_env_requirements

        assert check_env_requirements({}) == []
        assert check_env_requirements({"timeout": 300}) == []
        # 即使缺 base_url，只要未开启 requires_env 就不阻断（兼容旧行为）
        assert check_env_requirements({"base_url": None}) == []

    def test_requires_env_false_also_disabled(self):
        from app.services.api_endpoint_script_executor import check_env_requirements

        assert check_env_requirements({"requires_env": False, "base_url": ""}) == []

    def test_requires_env_true_without_base_url_blocks(self):
        from app.services.api_endpoint_script_executor import check_env_requirements

        assert check_env_requirements({"requires_env": True}) == ["base_url"]

    def test_requires_env_true_with_base_url_passes(self):
        from app.services.api_endpoint_script_executor import check_env_requirements

        assert check_env_requirements(
            {"requires_env": True, "base_url": "https://uat.example.com"}
        ) == []

    def test_missing_reference_keys_are_blocking(self):
        from app.services.api_endpoint_script_executor import check_env_requirements

        missing = check_env_requirements({
            "requires_env": True,
            "base_url": "https://uat.example.com",
            "appid_ref": "",
            "secret_ref": None,
        })
        assert "appid_ref" in missing
        assert "secret_ref" in missing

    def test_present_reference_keys_pass(self):
        from app.services.api_endpoint_script_executor import check_env_requirements

        assert check_env_requirements({
            "requires_env": True,
            "base_url": "https://uat.example.com",
            "appid_ref": "env:MX_APPID",
            "secret_ref": "env:MX_APPSECRET",
        }) == []


class TestBuildSubprocessEnv:
    """`_build_subprocess_env`：空配置不得用空串覆盖后端进程已继承的环境变量。

    回归场景：只提供 appid_ref/secret_ref、配置值为空时，子进程应继承
    后端运行环境的真实 MX_APPID/MX_APPSECRET，而不是被空串覆盖导致全部用例 skip。
    """

    def test_empty_config_inherits_process_env(self, monkeypatch):
        from app.services.api_endpoint_script_executor import _build_subprocess_env

        monkeypatch.setenv("MX_APPID", "from-process")
        monkeypatch.setenv("MX_APPSECRET", "secret-from-process")

        env = _build_subprocess_env({
            "appid_ref": "env:MX_APPID",
            "secret_ref": "env:MX_APPSECRET",
            "appid": "",
            "appsecret": "",
        })
        assert env["MX_APPID"] == "from-process"
        assert env["MX_APPSECRET"] == "secret-from-process"

    def test_whitespace_config_inherits_process_env(self, monkeypatch):
        """空白字符串（如 "   "）不得被当作有效值注入，应回退到进程环境变量。"""
        from app.services.api_endpoint_script_executor import _build_subprocess_env

        monkeypatch.setenv("MX_APPID", "from-process")
        monkeypatch.setenv("MX_APPSECRET", "secret-from-process")

        env = _build_subprocess_env({
            "appid": "   ",
            "appsecret": "  ",
        })
        assert env["MX_APPID"] == "from-process"
        assert env["MX_APPSECRET"] == "secret-from-process"

    def test_nonempty_config_overrides_process_env(self, monkeypatch):
        """配置提供非空值时，应覆盖进程环境变量。"""
        from app.services.api_endpoint_script_executor import _build_subprocess_env

        monkeypatch.setenv("MX_APPID", "from-process")
        monkeypatch.setenv("MX_APPSECRET", "secret-from-process")

        env = _build_subprocess_env({
            "appid": "cfg-appid",
            "appsecret": "cfg-secret",
        })
        assert env["MX_APPID"] == "cfg-appid"
        assert env["MX_APPSECRET"] == "cfg-secret"

    def test_no_process_env_and_empty_config_leaves_unset(self, monkeypatch):
        """配置为空且进程也无该变量时，不应凭空注入空串键。"""
        from app.services.api_endpoint_script_executor import _build_subprocess_env

        monkeypatch.delenv("MX_APPID", raising=False)
        monkeypatch.delenv("MX_APPSECRET", raising=False)

        env = _build_subprocess_env({"appid": "", "appsecret": ""}, base_env={})
        assert "MX_APPID" not in env
        assert "MX_APPSECRET" not in env

    def test_mobile_values_are_passed_through(self):
        from app.services.api_endpoint_script_executor import _build_subprocess_env

        env = _build_subprocess_env({
            "existing_mobile": "13800000000",
            "not_existing_mobile": "13900000000",
        }, base_env={})
        assert env["EXISTING_MOBILE"] == "13800000000"
        assert env["NOT_EXISTING_MOBILE"] == "13900000000"

    def test_whitespace_mobile_is_not_passed_through(self):
        """空白手机号不得透传（与 APPID 的空白处理语义一致）。"""
        from app.services.api_endpoint_script_executor import _build_subprocess_env

        env = _build_subprocess_env({
            "existing_mobile": "   ",
            "not_existing_mobile": "",
        }, base_env={})
        assert "EXISTING_MOBILE" not in env
        assert "NOT_EXISTING_MOBILE" not in env


class TestRedactConfig:
    """脱敏：密钥明文绝不能进入落库的执行配置。"""

    def test_secret_keys_are_masked(self):
        from app.services.api_endpoint_script_executor import redact_config

        out = redact_config({
            "base_url": "https://uat.example.com",
            "appsecret": "SUPER_SECRET",
            "appid": "ABCDEFG",
            "authorization": "Bearer xyz",
            "api_key": "k-123",
        })
        assert out["base_url"] == "https://uat.example.com"
        assert out["appid"] == "ABCDEFG"
        assert out["appsecret"] == "***"
        assert out["authorization"] == "***"
        assert out["api_key"] == "***"
        assert "SUPER_SECRET" not in json.dumps(out)

    def test_nested_dict_is_redacted(self):
        from app.services.api_endpoint_script_executor import redact_config

        out = redact_config({"auth": {"secret": "S", "appid": "A"}})
        assert out["auth"]["secret"] == "***"
        assert out["auth"]["appid"] == "A"

    def test_requires_env_refs_survive(self):
        """`secret_ref` 命中敏感键规则 → 也会脱敏（按真实行为固化）。

        这不意味着引用本身是明文：脱敏按"键名含 secret"统一处理，避免任何
        `<name>_secret: <value>` 写法把真密钥带进落库；门禁判定用的是**原始**
        config（`check_env_requirements`），不受脱敏影响。
        """
        from app.services.api_endpoint_script_executor import redact_config

        out = redact_config({"secret_ref": "env:MX_APPSECRET"})
        assert out["secret_ref"] == "***"

    def test_non_secret_values_are_preserved(self):
        """非敏感键必须原样保留，否则执行配置会失真。"""
        from app.services.api_endpoint_script_executor import redact_config

        assert redact_config({
            "base_url": "https://uat.example.com",
            "timeout": 300,
            "requires_env": True,
            "existing_mobile": "61234567",
        }) == {
            "base_url": "https://uat.example.com",
            "timeout": 300,
            "requires_env": True,
            "existing_mobile": "61234567",
        }


class TestPlaywrightReportStatusMapping:
    """状态回填：依据 Playwright JSON 报告，而不是进程退出码。"""

    def _report_with(self, statuses: list[str]) -> dict:
        """构造真实形状的 Playwright JSON 报告。

        真实报告中每个 spec 有一个 test，每个 test 有一个 result；
        `_parse_playwright_json` 按 results 计数，因此 total 必须等于状态数。
        """
        return {
            "suites": [{
                "title": "mobile-duplicate.spec.ts",
                "specs": [
                    {
                        "title": f"case-{i}",
                        "tests": [{
                            "projectName": "chromium",
                            "results": [{"status": status, "duration": 1}],
                        }],
                    }
                    for i, status in enumerate(statuses)
                ],
            }]
        }

    def test_all_passed_maps_to_completed_counts(self):
        from app.services.api_endpoint_script_executor import _parse_playwright_json

        total, passed, failed, skipped, rows = _parse_playwright_json(
            self._report_with(["passed", "passed"])
        )
        assert (total, passed, failed, skipped) == (2, 2, 0, 0)
        assert len(rows) == 2
        assert all(r["status"] == "passed" for r in rows)

    def test_failed_is_counted(self):
        from app.services.api_endpoint_script_executor import _parse_playwright_json

        total, passed, failed, skipped, _ = _parse_playwright_json(
            self._report_with(["passed", "failed"])
        )
        assert (total, passed, failed, skipped) == (2, 1, 1, 0)

    def test_skipped_is_counted(self):
        from app.services.api_endpoint_script_executor import _parse_playwright_json

        total, passed, failed, skipped, _ = _parse_playwright_json(
            self._report_with(["skipped"])
        )
        assert (total, passed, failed, skipped) == (1, 0, 0, 1)

    def test_empty_report_yields_no_results(self):
        from app.services.api_endpoint_script_executor import _parse_playwright_json

        total, passed, failed, skipped, rows = _parse_playwright_json({"suites": []})
        assert (total, passed, failed, skipped, rows) == (0, 0, 0, 0, [])


# ---------------------------------------------------------------------------
# 2. 服务 / API 层
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def anyio_backend():
    return "asyncio"


@pytest.fixture()
async def ctx():
    """专用 PostgreSQL 测试库 + 契约端点 + httpx 客户端。

    与 `test_api_bundle_import.py` 采用同样的策略：模型含 JSONB/UUID，
    且门禁语义依赖真实事务，因此必须用真实 PostgreSQL。
    """
    import os

    import httpx
    from fastapi import FastAPI
    from sqlalchemy import create_engine, text
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    os.environ.setdefault("PGPASSWORD", "postgres")

    from app.api.deps import get_current_user_id, get_db
    from app.api.v2.api_endpoints import router as endpoints_router
    from app.config.settings import settings
    from app.models.base import Base

    # 注册全部模型
    from app.models import api_asset as _api_asset  # noqa: F401
    from app.models import api_endpoint as _api_endpoint  # noqa: F401
    from app.models import api_test as _api_test  # noqa: F401
    from app.models import attachment as _attachment  # noqa: F401
    from app.models import folder as _folder  # noqa: F401
    from app.models import project as _project  # noqa: F401
    from app.models import test_run_artifact as _artifact  # noqa: F401
    from app.models import user as _user  # noqa: F401

    base_url = settings.postgres_sync_url
    test_name = "contract_p1_test_db"
    sync_url = base_url.rsplit("/", 1)[0] + "/" + test_name
    async_url = sync_url.replace("postgresql://", "postgresql+asyncpg://")
    admin_url = base_url.rsplit("/", 1)[0] + "/postgres"

    def _admin_exec(sql: str) -> None:
        engine = create_engine(admin_url, isolation_level="AUTOCOMMIT")
        with engine.connect() as conn:
            conn.execute(text(sql))
        engine.dispose()

    _admin_exec(f'DROP DATABASE IF EXISTS "{test_name}" WITH (FORCE)')
    _admin_exec(f'CREATE DATABASE "{test_name}"')

    engine = create_async_engine(async_url, pool_pre_ping=True)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    from app.models.project import Project
    from app.models.user import User

    async with session_factory() as session:
        user = User(
            id=uuid.uuid4(),
            email="p1-test@example.com",
            username="p1-test",
            password_hash="x",
            is_active=True,
        )
        session.add(user)
        await session.flush()
        session.add(Project(
            identifier="PR-P1",
            name="P1 契约驱动测试项目",
            created_by=user.id,
        ))
        await session.commit()

    app = FastAPI()
    app.include_router(endpoints_router, prefix="/api/v2")
    app.dependency_overrides[get_current_user_id] = lambda: "user-1"

    async def _db_dep():
        async with session_factory() as session:
            yield session

    app.dependency_overrides[get_db] = _db_dep

    transport = httpx.ASGITransport(app=app)
    client = httpx.AsyncClient(transport=transport, base_url="http://test")

    try:
        yield {
            "client": client,
            "session_factory": session_factory,
            "engine": engine,
        }
    finally:
        await client.aclose()
        await engine.dispose()
        _admin_exec(f'DROP DATABASE IF EXISTS "{test_name}" WITH (FORCE)')


#: 可被契约生成消费的端点配置（mx_md5 是生成器的硬性前置条件）
ENDPOINT_CUSTOM_CONFIG = {
    "api_name": "api_checkMobileDuplicate",
    "method_name": "checkMobileDuplicate",
    "auth": {
        "type": "mx_md5",
        "appid_ref": "env:MX_APPID",
        "secret_ref": "env:MX_APPSECRET",
        "sign_fields": ["appid", "appsercret", "ts", "msg"],
    },
    "business_rules": [],
    "source_location": {"sheet": "COP api_checkMobileDuplicat", "row": 2},
    "pending_confirmations": [],
}

VALID_ENV_CONFIG = {
    "base_url": "https://uat.example.com",
    "requires_env": True,
    "appid_ref": "env:MX_APPID",
    "secret_ref": "env:MX_APPSECRET",
}


async def _create_endpoint(ctx, *, path: str | None = None, custom_config=None) -> dict:
    """在测试库中建一个契约端点，返回 {id, project_id}。"""
    from sqlalchemy import select

    from app.models.api_endpoint import APIEndpoint
    from app.models.project import Project

    session_factory = ctx["session_factory"]
    async with session_factory() as session:
        project = (await session.execute(
            select(Project).where(Project.identifier == "PR-P1")
        )).scalar_one()
        endpoint = APIEndpoint(
            project_id=project.id,
            display_name="POST /api/json/api_checkMobileDuplicate",
            path=path or "/api/json/api_checkMobileDuplicate",
            method="POST",
            normalized_path=(path or "/api/json/api_checkMobileDuplicate").lower(),
            summary="Check Mobile Duplicate",
            request_body={"fields": [
                {"name": "MobilePhone", "required": True, "type": "Text", "example": "61234567"},
                {"name": "CountryCode", "required": False, "type": "Text", "example": "852"},
            ]},
            responses={"fields": [], "error_codes": []},
            custom_config=custom_config if custom_config is not None else ENDPOINT_CUSTOM_CONFIG,
        )
        session.add(endpoint)
        await session.commit()
        return {"id": str(endpoint.id), "project_id": str(project.id)}


class TestGenerateArtifactsApi:
    """POST /api-endpoints/{id}/contract-artifacts"""

    BASE = "/api/v2/api-endpoints"

    async def test_generates_analysis_plan_and_script(self, ctx):
        endpoint = await _create_endpoint(ctx)
        resp = await ctx["client"].post(
            f"{self.BASE}/{endpoint['id']}/contract-artifacts",
            json={"env_config": VALID_ENV_CONFIG},
        )
        assert resp.status_code == 200, resp.text
        data = resp.json()["data"]

        assert set(data) >= {"analysis", "plan", "script", "script_attachment_id", "endpoint_id"}
        assert data["analysis"], "需求分析不应为空"
        assert data["plan"], "测试计划不应为空"
        assert "require('@playwright/test')" in data["script"]
        assert "https://uat.example.com" in data["script"]
        assert "MX_APPID" in data["script"] or VALID_ENV_CONFIG["appid_ref"] in json.dumps(data)
        assert data["endpoint_id"] == endpoint["id"]

    async def test_script_attachment_is_persisted(self, ctx):
        """脚本必须落成附件，供执行器下载与追溯。"""
        from sqlalchemy import select

        from app.models.attachment import Attachment

        endpoint = await _create_endpoint(ctx)
        resp = await ctx["client"].post(
            f"{self.BASE}/{endpoint['id']}/contract-artifacts",
            json={"env_config": VALID_ENV_CONFIG},
        )
        assert resp.status_code == 200, resp.text
        attachment_id = resp.json()["data"]["script_attachment_id"]

        async with ctx["session_factory"]() as session:
            att = (await session.execute(
                select(Attachment).where(Attachment.id == uuid.UUID(attachment_id))
            )).scalar_one_or_none()
            assert att is not None, "脚本附件应已落库"
            assert att.object_name.endswith(".spec.ts")
            assert att.entity_id == uuid.UUID(endpoint["id"])

    async def test_missing_base_url_returns_400(self, ctx):
        endpoint = await _create_endpoint(ctx)
        resp = await ctx["client"].post(
            f"{self.BASE}/{endpoint['id']}/contract-artifacts",
            json={"env_config": {"requires_env": True}},
        )
        assert resp.status_code == 400, resp.text
        assert "base_url" in resp.text

    async def test_unknown_endpoint_returns_404(self, ctx):
        resp = await ctx["client"].post(
            f"{self.BASE}/{uuid.uuid4()}/contract-artifacts",
            json={"env_config": VALID_ENV_CONFIG},
        )
        assert resp.status_code == 404, resp.text

    async def test_non_mx_md5_gateway_endpoint_generates_connectivity_script(self, ctx):
        """非 mx_md5 且带 method_name 的网关端点 → 生成连通性脚本（而非报错）。

        设计意图（api_test_service.generate_contract_artifacts）：
        网关通用路径端点（如 POST /cop/json，仅有 Collection 来源）没有 Excel 契约
        与 mx_md5 鉴权配置，硬性报错会让这类端点完全无法自动化，因此降级生成
        基础连通性脚本。
        """
        endpoint = await _create_endpoint(
            ctx,
            path="/api/other/generic",
            custom_config={**ENDPOINT_CUSTOM_CONFIG, "auth": {"type": "apiKey"}},
        )
        resp = await ctx["client"].post(
            f"{self.BASE}/{endpoint['id']}/contract-artifacts",
            json={"env_config": VALID_ENV_CONFIG},
        )
        assert resp.status_code == 200, resp.text
        script = resp.json()["data"]["script"]
        # 连通性脚本特征：Method header 路由 + 只断言"网关可达"
        assert "Method" in script
        assert "checkMobileDuplicate" in script
        assert resp.json()["data"]["script_attachment_id"]

    async def test_non_mx_md5_without_method_name_is_rejected(self, ctx):
        """非 mx_md5 且**无** method_name → 无法降级，必须明确报错而不是产出错误脚本。"""
        endpoint = await _create_endpoint(
            ctx,
            path="/api/other/no-method",
            custom_config={
                **ENDPOINT_CUSTOM_CONFIG,
                "auth": {"type": "apiKey"},
                "method_name": None,
            },
        )
        resp = await ctx["client"].post(
            f"{self.BASE}/{endpoint['id']}/contract-artifacts",
            json={"env_config": VALID_ENV_CONFIG},
        )
        assert resp.status_code == 400, resp.text
        assert "mx_md5" in resp.text


class TestGenerateAndRunApi:
    """POST /projects/{identifier}/api-endpoints/{id}/generate-run"""

    BASE = "/api/v2/projects/PR-P1/api-endpoints"

    async def test_blocked_when_env_missing_does_not_execute(self, ctx, monkeypatch):
        """缺环境配置 → blocked，且**不得**触发任何第三方请求。

        这是本组最重要的断言：把 `execute_with_preflight` 换成探针，
        一旦被调用即失败，从机制上证明"阻断时没有执行"。
        """
        called = {"preflight": 0}

        async def _probe_preflight(*args, **kwargs):
            called["preflight"] += 1
            raise AssertionError("缺环境配置时不应进入执行链路")

        monkeypatch.setattr(
            "app.services.unified_execution.execute_with_preflight",
            _probe_preflight,
        )

        endpoint = await _create_endpoint(ctx)
        resp = await ctx["client"].post(
            f"{self.BASE}/{endpoint['id']}/generate-run",
            json={"env_config": {"requires_env": True}},
        )
        assert resp.status_code == 200, resp.text
        data = resp.json()["data"]
        assert data["status"] == "blocked"
        assert "base_url" in data["message"]
        assert data["run_id"]
        assert called["preflight"] == 0, "blocked 分支不得进入执行链路"

    async def test_blocked_run_is_recorded_with_redacted_config(self, ctx):
        from sqlalchemy import select

        from app.models.api_test import APITestRun

        endpoint = await _create_endpoint(ctx)
        resp = await ctx["client"].post(
            f"{self.BASE}/{endpoint['id']}/generate-run",
            json={"env_config": {"requires_env": True, "appsecret": "LEAK_ME_123"}},
        )
        assert resp.status_code == 200, resp.text
        run_id = uuid.UUID(resp.json()["data"]["run_id"])

        async with ctx["session_factory"]() as session:
            run = (await session.execute(
                select(APITestRun).where(APITestRun.id == run_id)
            )).scalar_one_or_none()
            assert run is not None, "blocked 也必须留下 run 记录（可追溯）"
            assert run.status == "blocked"
            dumped = json.dumps(run.execution_config or {})
            assert "LEAK_ME_123" not in dumped, "明文密钥不得进入执行配置"

    async def test_unknown_project_returns_404(self, ctx):
        endpoint = await _create_endpoint(ctx)
        resp = await ctx["client"].post(
            f"/api/v2/projects/NO-SUCH-PROJECT/api-endpoints/{endpoint['id']}/generate-run",
            json={"env_config": VALID_ENV_CONFIG},
        )
        assert resp.status_code == 404, resp.text

    async def test_endpoint_of_other_project_returns_404(self, ctx):
        """跨项目取端点必须 404，不能泄露其它项目端点是否可生成。"""
        from sqlalchemy import select

        from app.models.project import Project
        from app.models.user import User

        async with ctx["session_factory"]() as session:
            user = (await session.execute(select(User).limit(1))).scalar_one()
            session.add(Project(
                identifier="PR-P1-OTHER",
                name="别的项目",
                created_by=user.id,
            ))
            await session.commit()

        endpoint = await _create_endpoint(ctx)
        resp = await ctx["client"].post(
            f"/api/v2/projects/PR-P1-OTHER/api-endpoints/{endpoint['id']}/generate-run",
            json={"env_config": VALID_ENV_CONFIG},
        )
        assert resp.status_code == 404, resp.text

    async def test_preflight_blocked_path_marks_artifact_blocked(self, ctx, monkeypatch):
        """预检阻断（环境已配但被闸门拦住）→ 返回 blocked 且产物标记 blocked。"""
        async def _blocked_preflight(**kwargs):
            return {
                "status": "blocked",
                "message": "预检未通过：示例原因",
                "preflight": {"reason_code": "GATE_DENIED"},
            }

        monkeypatch.setattr(
            "app.services.unified_execution.execute_with_preflight",
            _blocked_preflight,
        )

        endpoint = await _create_endpoint(ctx)
        resp = await ctx["client"].post(
            f"{self.BASE}/{endpoint['id']}/generate-run",
            json={"env_config": VALID_ENV_CONFIG},
        )
        assert resp.status_code == 200, resp.text
        data = resp.json()["data"]
        assert data["status"] == "blocked"
        assert data["preflight"]["reason_code"] == "GATE_DENIED"
        assert data["run_id"]

    async def test_run_id_propagates_to_artifact_registration(self, ctx, monkeypatch):
        """run_id 必须全链路一致：context → 产物登记 → 返回值。"""
        captured: dict = {}

        async def _capture_preflight(*, context, track, executor, gate_config=None):
            captured["context_run_id"] = context.run_id
            captured["artifact_id"] = context.artifact_id
            captured["track"] = track
            return {"run_id": context.run_id, "status": "running"}

        monkeypatch.setattr(
            "app.services.unified_execution.execute_with_preflight",
            _capture_preflight,
        )

        endpoint = await _create_endpoint(ctx)
        resp = await ctx["client"].post(
            f"{self.BASE}/{endpoint['id']}/generate-run",
            json={"env_config": VALID_ENV_CONFIG},
        )
        assert resp.status_code == 200, resp.text
        data = resp.json()["data"]

        assert captured["track"] == "api"
        assert captured["context_run_id"] == data["run_id"]
        assert captured["context_run_id"].startswith(f"ep-{endpoint['id']}-")
        assert captured["artifact_id"], "context 必须带产物 ID"

    async def test_executor_is_reached_when_preflight_passes(self, ctx, monkeypatch):
        """预检通过时才真正调用执行器（确认门禁不是永久拦截）。"""
        called = {"executor": 0}

        async def _passing_preflight(*, context, track, executor, gate_config=None):
            result = await executor()
            called["executor"] += 1
            return result

        async def _fake_execute_endpoint_script(**kwargs):
            return str(uuid.uuid4())

        monkeypatch.setattr(
            "app.services.unified_execution.execute_with_preflight",
            _passing_preflight,
        )
        monkeypatch.setattr(
            "app.services.api_test_service.execute_endpoint_script",
            _fake_execute_endpoint_script,
        )

        endpoint = await _create_endpoint(ctx)
        resp = await ctx["client"].post(
            f"{self.BASE}/{endpoint['id']}/generate-run",
            json={"env_config": VALID_ENV_CONFIG},
        )
        assert resp.status_code == 200, resp.text
        assert called["executor"] == 1, "预检通过后必须调用执行器"
        assert resp.json()["data"]["status"] == "running"


class TestLegacyCallerCompatibility:
    """既有调用方在默认配置下行为必须保持不变（P1 的关键回归要求）。"""

    BASE = "/api/v2/projects/PR-P1/api-endpoints"

    async def test_scheduler_style_config_bypasses_gate(self, ctx, monkeypatch):
        """调度器传的是 execution_config or {}，不含 requires_env。

        此时既不能 blocked，也不能因为门禁新增字段而改变结果：
        必须直接走到执行器（行为与改动前一致）。
        """
        called = {"executor": 0}

        async def _passing_preflight(*, context, track, executor, gate_config=None):
            return await executor()

        async def _fake_execute_endpoint_script(**kwargs):
            called["executor"] += 1
            assert "requires_env" not in kwargs["execution_config"]
            return str(uuid.uuid4())

        monkeypatch.setattr(
            "app.services.unified_execution.execute_with_preflight",
            _passing_preflight,
        )
        monkeypatch.setattr(
            "app.services.api_test_service.execute_endpoint_script",
            _fake_execute_endpoint_script,
        )

        endpoint = await _create_endpoint(ctx)
        resp = await ctx["client"].post(
            f"{self.BASE}/{endpoint['id']}/generate-run",
            # 旧调用方本来就提供 base_url（否则脚本无法生成）；
            # 这里缺的只有门禁新增的 requires_env 开关。
            json={"env_config": {"base_url": "https://uat.example.com", "timeout": 300}},
        )
        assert resp.status_code == 200, resp.text
        assert called["executor"] == 1, "缺省配置下既有行为不得被门禁改变"

    async def test_gate_defaults_off_in_executor(self):
        """执行器自身的判定：不传 requires_env 时门禁不生效。"""
        from app.services.api_endpoint_script_executor import check_env_requirements

        legacy_configs = [
            {},
            {"timeout": 300},
            {"base_url": ""},
            {"base_url": None, "timeout": 60},
        ]
        for config in legacy_configs:
            assert check_env_requirements(config) == [], f"旧配置被误拦: {config}"
