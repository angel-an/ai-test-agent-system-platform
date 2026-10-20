# -*- coding: utf-8 -*-
"""P6 业务模板数据化注册：解析优先级 / 唯一性 / 老项目兜底兼容。

覆盖两类：
1) 纯函数优先级（不起 DB）：子功能 override > 项目注册 > 内置表 > 无；
2) 真实 HTTP + 专用 PostgreSQL 测试库（httpx ASGITransport）：
   * 注册 → 查询 → 解析预览选中 project_registration；
   * 同一项目重复注册同一模板键 → 409（不静默覆盖）；
   * 不同项目可注册同名模板键（唯一性是"项目内"）；
   * 老项目兼容：未注册的模板键 → 内置表兜底（enterprise_storage_recharge）；
     未知模板键 → none（**不回退**到别的业务的定位器表）；
   * 子功能 override 存在时优先级最高（覆盖项目注册）；
   * disabled 的注册不参与解析。
"""

from __future__ import annotations

import os
import uuid

import pytest


# ---------------------------------------------------------------------------
# 1) 纯函数：解析优先级
# ---------------------------------------------------------------------------

class TestResolvePriority:
    """解析优先级（与执行链路同一个函数）。"""

    def _fields(self, name: str) -> dict:
        return {"fields": {name: {"kind": "input"}}}

    def test_override_beats_registration_and_builtin(self) -> None:
        from app.services.web_case_compiler import resolve_form_locators

        # 内置表里真实存在的模板键，同时给出注册表与 override
        locators, source = resolve_form_locators(
            "enterprise_storage_recharge",
            override=self._fields("override-field"),
            registered=self._fields("registered-field"),
        )
        assert source == "sub_function_override"
        assert "override-field" in locators["fields"]

    def test_registration_beats_builtin(self) -> None:
        from app.services.web_case_compiler import resolve_form_locators

        locators, source = resolve_form_locators(
            "enterprise_storage_recharge",
            registered=self._fields("registered-field"),
        )
        assert source == "project_registration"
        assert "registered-field" in locators["fields"]

    def test_builtin_is_last_resort(self) -> None:
        from app.services.web_case_compiler import resolve_form_locators

        locators, source = resolve_form_locators("enterprise_storage_recharge")
        assert source == "template_default"
        assert locators["fields"]

    def test_unknown_key_without_registration_is_none(self) -> None:
        """未知模板键：必须 none，**不得**回退到别的业务的定位器表。"""
        from app.services.web_case_compiler import resolve_form_locators

        locators, source = resolve_form_locators("some_new_business_template")
        assert locators is None
        assert source == "none"

    def test_empty_registration_is_ignored(self) -> None:
        from app.services.web_case_compiler import resolve_form_locators

        locators, source = resolve_form_locators(
            "some_new_business_template",
            registered={"fields": {}},
        )
        assert locators is None
        assert source == "none"

    def test_form_plan_for_template_uses_registration(self) -> None:
        """注册表能真正生成表单检查计划（不只是解析出对象）。"""
        from app.services.web_case_compiler import build_form_plan_for_template

        manifest = {
            "cases": [
                {
                    "identifier": "TC-1",
                    "rule_id": "R-1",
                    "field": "商品名称",
                    "assertions": [{"kind": "required", "rule": "是否必填=是"}],
                }
            ]
        }
        registered = {"fields": {"商品名称": {"kind": "input"}}}
        plan, source = build_form_plan_for_template("purchase_limit_management", manifest, None, registered)
        assert source == "project_registration"
        assert plan is not None
        assert plan["check_count"] >= 1, "注册的字段应生成检查"
        assert plan["skipped"] == [], "注册的字段不应被跳过"
        assert "商品名称" in plan["locators"]["fields"]

    def test_unregistered_field_is_skipped_not_faked(self) -> None:
        """未注册的字段：如实进 skipped（带候选提示），不生成假检查。"""
        from app.services.web_case_compiler import build_form_plan_for_template

        manifest = {
            "cases": [
                {
                    "identifier": "TC-1",
                    "rule_id": "R-1",
                    "field": "商品名称",
                    "assertions": [{"kind": "required", "rule": "是否必填=是"}],
                }
            ]
        }
        registered = {"fields": {"限购数量": {"kind": "input"}}}
        plan, source = build_form_plan_for_template("purchase_limit_management", manifest, None, registered)
        assert source == "project_registration"
        assert plan is not None
        assert plan["check_count"] == 0
        assert plan["skipped"] and plan["skipped"][0]["reason"] == "无定位器"
        assert plan["skipped"][0]["declared_fields"] == ["限购数量"]


# ---------------------------------------------------------------------------
# 2) 真实 HTTP + 专用测试库
# ---------------------------------------------------------------------------

@pytest.fixture()
async def client():
    """仅挂载 web_form_templates 路由的应用 + 专用 PostgreSQL 测试库。"""
    import httpx
    from fastapi import FastAPI
    from sqlalchemy import create_engine, text
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from app.api.deps import get_current_user_id, get_db
    from app.api.v2.web_form_templates import router
    from app.config.settings import settings
    from app.models.base import Base

    # 注册本测试用到的全部模型（create_all 需要）
    from app.models import folder as _folder  # noqa: F401
    from app.models import project as _project  # noqa: F401
    from app.models import user as _user  # noqa: F401
    from app.models import web_form_template as _web_form_template  # noqa: F401
    from app.models import web_function as _web_function  # noqa: F401

    if os.getenv("PGPASSWORD") is None:
        os.environ["PGPASSWORD"] = "postgres"

    base_url = settings.postgres_sync_url
    test_name = "web_form_template_test_db"
    sync_url = base_url.rsplit("/", 1)[0] + "/" + test_name
    async_url = sync_url.replace("postgresql://", "postgresql+asyncpg://")
    admin_url = base_url.rsplit("/", 1)[0] + "/postgres"

    def _admin_exec(sql: str) -> None:
        eng = create_engine(admin_url, isolation_level="AUTOCOMMIT")
        with eng.connect() as conn:
            conn.execute(text(sql))
        eng.dispose()

    _admin_exec(f'DROP DATABASE IF EXISTS "{test_name}" WITH (FORCE)')
    _admin_exec(f'CREATE DATABASE "{test_name}"')

    engine = create_async_engine(async_url, pool_pre_ping=True)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    async with session_factory() as s:
        from app.models.project import Project
        from app.models.user import User

        user = User(
            id=uuid.uuid4(),
            email="p6-template@example.com",
            username="p6-template",
            password_hash="x",
            is_active=True,
        )
        s.add(user)
        await s.flush()
        s.add(Project(identifier="PR-A", name="模板注册项目A", created_by=user.id))
        s.add(Project(identifier="PR-B", name="模板注册项目B", created_by=user.id))
        await s.commit()

    app = FastAPI()
    app.include_router(router, prefix="/api/v2")
    app.dependency_overrides[get_current_user_id] = lambda: "user-1"

    async def _db_dep():
        async with session_factory() as s:
            yield s

    app.dependency_overrides[get_db] = _db_dep

    transport = httpx.ASGITransport(app=app)
    api = httpx.AsyncClient(transport=transport, base_url="http://test")
    try:
        yield api
    finally:
        await api.aclose()
        await engine.dispose()
        _admin_exec(f'DROP DATABASE IF EXISTS "{test_name}" WITH (FORCE)')


def _payload(key: str, *, field_name: str = "商品名称", status: str = "active") -> dict:
    return {
        "template_key": key,
        "name": f"模板 {key}",
        "source_ref": "2026-09-30 自 UAT 页面实测抓取",
        "fields": {
            field_name: {"kind": "input"},
            "限购数量": {"kind": "input", "placeholder_hint": "请输入限购数量"},
        },
        "status": status,
    }


BASE = "/api/v2/projects/PR-A/web-form-templates"


@pytest.mark.asyncio
async def test_register_list_and_preview(client) -> None:
    """注册 → 列表 → 解析预览应选中 project_registration。"""
    resp = await client.post(BASE, json=_payload("purchase_limit_management"))
    assert resp.status_code == 200, resp.text
    body = resp.json()["data"]
    assert body["template_key"] == "purchase_limit_management"
    assert body["field_count"] == 2

    listed = (await client.get(BASE)).json()["data"]
    assert listed["total"] == 1

    preview = (await client.get(f"{BASE}/purchase_limit_management/resolve-preview")).json()["data"]
    assert preview["selected_source"] == "project_registration"
    assert preview["field_count"] == 2
    assert "商品名称" in preview["fields"]
    sources = {c["source"]: c for c in preview["candidates"]}
    assert sources["project_registration"]["selected"] is True
    assert sources["sub_function_override"]["selected"] is False


@pytest.mark.asyncio
async def test_duplicate_registration_conflicts(client) -> None:
    """同一项目重复注册同一模板键 → 409（不得静默覆盖）。"""
    payload = _payload("dup_key")
    first = await client.post(BASE, json=payload)
    assert first.status_code == 200, first.text

    second = await client.post(BASE, json=_payload("dup_key", field_name="改了字段"))
    assert second.status_code == 409
    assert "已注册" in second.json()["detail"]

    # 未变更：仍是第一次的字段
    preview = (await client.get(f"{BASE}/dup_key/resolve-preview")).json()["data"]
    assert preview["fields"] == ["商品名称", "限购数量"]


@pytest.mark.asyncio
async def test_update_existing_registration(client) -> None:
    """PUT 可更新（模板键不可改）。"""
    await client.post(BASE, json=_payload("upd_key"))
    updated = await client.put(f"{BASE}/upd_key", json=_payload("upd_key", field_name="新字段名"))
    assert updated.status_code == 200, updated.text
    preview = (await client.get(f"{BASE}/upd_key/resolve-preview")).json()["data"]
    assert preview["fields"][0] == "新字段名"


@pytest.mark.asyncio
async def test_uniqueness_is_per_project(client) -> None:
    """唯一性是"项目内"：另一个项目可注册同名模板键。"""
    assert (await client.post(BASE, json=_payload("shared_key"))).status_code == 200
    other = await client.post("/api/v2/projects/PR-B/web-form-templates", json=_payload("shared_key"))
    assert other.status_code == 200, other.text


@pytest.mark.asyncio
async def test_legacy_builtin_fallback_still_works(client) -> None:
    """老项目兼容：未注册时，内置表模板仍走 template_default。"""
    preview = (
        await client.get(f"{BASE}/enterprise_storage_recharge/resolve-preview")
    ).json()["data"]
    assert preview["selected_source"] == "template_default"
    assert preview["field_count"] > 0
    assert preview["registration"] is None


@pytest.mark.asyncio
async def test_unknown_key_is_none_not_other_business_table(client) -> None:
    """未知模板键：none，并说明原因（不回退到别的业务的定位器表）。"""
    preview = (await client.get(f"{BASE}/never_registered_key/resolve-preview")).json()["data"]
    assert preview["selected_source"] == "none"
    assert preview["field_count"] == 0
    assert "pending_locator" in preview["reason"]


@pytest.mark.asyncio
async def test_disabled_registration_does_not_participate(client) -> None:
    """disabled 的注册不参与解析（配置留着但不生效，必须可解释）。"""
    await client.post(BASE, json=_payload("disabled_key", status="disabled"))
    preview = (await client.get(f"{BASE}/disabled_key/resolve-preview")).json()["data"]
    assert preview["selected_source"] == "none"
    candidates = {c["source"]: c for c in preview["candidates"]}
    assert candidates["project_registration"]["available"] is False


@pytest.mark.asyncio
async def test_field_validation_rejects_empty_spec(client) -> None:
    """字段校验：规格必须是非空对象（拒绝"注册了却生成不出检查"）。"""
    payload = _payload("bad_key")
    payload["fields"] = {"商品名称": {}}
    resp = await client.post(BASE, json=payload)
    assert resp.status_code == 422
    assert "非空对象" in resp.json()["detail"]

    payload2 = _payload("bad_key2")
    payload2["fields"] = {"   ": {"kind": "input"}}
    resp2 = await client.post(BASE, json=payload2)
    assert resp2.status_code == 422
    assert "空的字段名" in resp2.json()["detail"]


@pytest.mark.asyncio
async def test_delete_falls_back_to_builtin(client) -> None:
    """删除注册 → 回到内置表兜底（老项目行为不受影响）。"""
    await client.post(BASE, json=_payload("enterprise_storage_recharge"))
    preview_before = (
        await client.get(f"{BASE}/enterprise_storage_recharge/resolve-preview")
    ).json()["data"]
    assert preview_before["selected_source"] == "project_registration"

    deleted = await client.delete(f"{BASE}/enterprise_storage_recharge")
    assert deleted.status_code == 200

    preview_after = (
        await client.get(f"{BASE}/enterprise_storage_recharge/resolve-preview")
    ).json()["data"]
    assert preview_after["selected_source"] == "template_default"


# ---------------------------------------------------------------------------
# 3) 真实运行暴露的缺陷回归（TR-163，2026-09-30）
# ---------------------------------------------------------------------------

def test_registered_table_keeps_full_locator_table() -> None:
    """回归（TR-163）：注册必须还原**完整**定位器表，否则执行器缺 form_button 会崩溃。

    实测：只登记 fields 时，生成的执行器脚本报
    `TypeError: FORM_PLAN.locators.form_button is not iterable`，整条运行硬失败且看不出原因。
    这里直接校验"合并后的表"包含表单打开配置。
    """
    from app.services.web_case_link_service import WebCaseLinkService

    class _Row:
        fields = {"会员手机号": {"kind": "textarea"}}
        extra_config = {"form_button": ["企业充值/退款"], "modal": ".ant-modal"}
        template_key = "enterprise_storage_recharge"
        source_ref = "test"
        id = "00000000-0000-0000-0000-000000000000"

    table = WebCaseLinkService._merge_registered_locators(_Row())
    assert table["fields"] == {"会员手机号": {"kind": "textarea"}}
    assert table["form_button"] == ["企业充值/退款"]
    assert table["modal"] == ".ant-modal"


def test_registered_extra_config_cannot_override_fields() -> None:
    """extra_config 里的 fields 不得覆盖显式字段表（避免两处真相）。"""
    from app.services.web_case_link_service import WebCaseLinkService

    class _Row:
        fields = {"A": {"kind": "input"}}
        extra_config = {"fields": {"B": {"kind": "input"}}, "modal": ".ant-modal"}
        template_key = "k"
        source_ref = None
        id = "00000000-0000-0000-0000-000000000000"

    table = WebCaseLinkService._merge_registered_locators(_Row())
    assert list(table["fields"].keys()) == ["A"]


def test_form_executor_is_tolerant_of_missing_form_button() -> None:
    """回归（TR-163）：form_button 缺失时执行器不得 TypeError，而应给出可读结论。"""
    from app.services.web_case_compiler import render_form_executor

    plan = {
        "locators": {"name": "t", "flow": "modal", "modal": ".ant-modal",
                     "fields": {"A": {"kind": "input"}}},
        "checks": [{"type": "required_error", "field": "A", "rule": "必填", "case_id": "TC-1"}],
        "skipped": [], "check_count": 1, "skipped_count": 0, "assertion_check_count": 1,
        "executable": True,
    }
    script = render_form_executor(plan)
    assert "FORM_PLAN.locators.form_button || []" in script, "必须对缺失的 form_button 容错"
    assert "for (const name of FORM_PLAN.locators.form_button)" not in script, "不得再有裸迭代"
    assert "定位器未配置表单入口" in script, "缺失配置要给出可读原因，而不是笼统的定位器失效"


@pytest.mark.asyncio
async def test_extra_config_round_trip(client) -> None:
    """extra_config 能存能取（完整定位器表的其余键）。"""
    payload = _payload("with_extra")
    payload["extra_config"] = {"form_button": ["新 建"], "modal": ".ant-modal", "search_button": "搜索"}
    assert (await client.post(BASE, json=payload)).status_code == 200

    listed = (await client.get(f"{BASE}?template_key=with_extra")).json()["data"]["items"][0]
    assert listed["extra_config"]["form_button"] == ["新 建"]
    assert listed["field_count"] == 2


@pytest.mark.asyncio
async def test_resolve_preview_surfaces_extra_config_locators(client) -> None:
    """回归：预览必须摊开 extra_config 派生的定位器键，与执行链路完全一致。

    此前 ``resolve-preview`` 只把 ``{"fields": ...}`` 当注册表传给
    ``resolve_form_locators``，丢掉 ``extra_config``（``form_button`` / ``modal`` 等）。
    于是形成最坏的一种分叉：**预览显示"一切正常"，实际执行必崩** ——
    执行器在打开表单处报 ``FORM_PLAN.locators.form_button is not iterable``（TR-163）。

    既有测试只断言 ``field_count`` / ``fields`` / ``selected_source``，
    对这类丢键**完全无感**（6 处预览测试全绿，缺陷却在）。本测试专盯非字段定位器键。
    """
    payload = _payload("preview_extra")
    payload["extra_config"] = {"form_button": ["新 建"], "modal": ".ant-modal"}
    assert (await client.post(BASE, json=payload)).status_code == 200

    preview = (await client.get(f"{BASE}/preview_extra/resolve-preview")).json()["data"]
    assert preview["selected_source"] == "project_registration"
    assert preview["field_count"] == 2

    # 关键断言：执行时会用到的**非字段**定位器键，预览必须如实暴露，
    # 否则注册漏带 extra_config 造成的执行期硬失败在预览里不可见。
    assert "form_button" in preview["locator_keys"]
    assert preview["locator_keys"].count("form_button") == 1
    assert "modal" in preview["locator_keys"]
    assert "fields" in preview["locator_keys"]

    # 来源元信息不是定位器配置，不得混进 locator_keys
    for meta in ("template_key", "registration_id", "source_ref"):
        assert meta not in preview["locator_keys"], f"{meta} 是元信息，不应算定位器键"


@pytest.mark.asyncio
async def test_resolve_preview_locator_keys_match_execution_path(client) -> None:
    """预览摊开的定位器键，必须与执行链路合并出来的表**逐键一致**。

    这是"两处实现分叉"的通用防线：不再逐项断言某个键，而是要求两端集合相等。
    """
    from app.services.web_case_compiler import (
        NON_LOCATOR_CONFIG_KEYS,
        merge_registered_locators,
        resolve_form_locators,
    )

    payload = _payload("parity_key")
    payload["extra_config"] = {"form_button": ["新 建"], "modal": ".ant-modal"}
    assert (await client.post(BASE, json=payload)).status_code == 200

    preview = (await client.get(f"{BASE}/parity_key/resolve-preview")).json()["data"]

    class _Row:
        fields = {**payload["fields"]}
        extra_config = {**payload["extra_config"]}
        template_key = "parity_key"
        source_ref = payload["source_ref"]
        id = "00000000-0000-0000-0000-000000000000"
        status = "active"

    table, source = resolve_form_locators("parity_key", None, merge_registered_locators(_Row()))
    execution_keys = sorted(k for k in table if k not in NON_LOCATOR_CONFIG_KEYS)

    assert source == "project_registration"
    assert preview["locator_keys"] == execution_keys, (
        "预览暴露的定位器键必须与执行链路一致；不一致意味着用户看到的表不是执行时会用的表"
    )


@pytest.mark.asyncio
async def test_resolve_preview_exposes_negative_scenarios_and_rejects(client) -> None:
    """规则级负路径场景（P1-3 阶段 B）必须在预览里可见，且与执行器**同一个**解析函数。

    否则会出现与 ``locator_keys`` 完全相同形状的分叉：预览说"配置正常"，
    执行时却因场景被拒一条都没跑（只在日志里留几行 ``[negative-skip]``）。
    """
    from app.services.web_case_compiler import parse_negative_scenarios

    good = {
        "scenario_id": "NEG-DUP-GOODS-EDIT",
        "rule_ref": "同一商品仅能属于一条活动",
        "target_entry": "edit",
        "setup": [{"action": "click", "labels": ["编辑"]},
                  {"action": "select_row", "row_index": 1}],
        "submit_label": ["提 交"],
        "expect_reject": {
            "result_code_not": "0", "message_contains": ["已新建"],
            "api_path_hint": "/v1/purchaseLimit/modify",
            "allow": {"host_suffix": "imarketing-uat.xysjg.com",
                      "path_prefix": "/gw/api/v1/purchaseLimit/modify", "methods": ["PUT"]},
        },
    }
    bad = {"scenario_id": "NEG-BAD", "expect_reject": {"result_code_not": "0"}}

    payload = _payload("neg_preview")
    payload["extra_config"] = {"form_button": ["新 建"], "modal": ".ant-modal",
                               "negative_scenarios": [good, bad]}
    assert (await client.post(BASE, json=payload)).status_code == 200

    preview = (await client.get(f"{BASE}/neg_preview/resolve-preview")).json()["data"]
    assert preview["negative_scenario_count"] == 1
    shown = preview["negative_scenarios"][0]
    assert shown["scenario_id"] == "NEG-DUP-GOODS-EDIT"
    assert shown["setup_steps"] == ["click", "select_row"]     # 顺序即执行顺序
    assert shown["allow"]["path_prefix"] == "/gw/api/v1/purchaseLimit/modify"
    assert shown["allow"]["host_suffix"] == "imarketing-uat.xysjg.com"

    # 被拒场景必须带原因（不能只给个数字，也不能静默消失）
    assert [r["scenario_id"] for r in preview["negative_scenario_rejects"]] == ["NEG-BAD"]
    assert preview["negative_scenario_rejects"][0]["reason"] == "缺少 expect_reject.api_path_hint"

    # 预览与执行器逐字节同一份解析结果（parity 防线）
    parsed, rejects = parse_negative_scenarios(payload["extra_config"]["negative_scenarios"])
    assert preview["negative_scenario_count"] == len(parsed)
    assert preview["negative_scenario_rejects"] == rejects

    # 场景是**配置**，不是"定位器"——不得混进 locator_keys
    assert "negative_scenarios" not in preview["locator_keys"]
