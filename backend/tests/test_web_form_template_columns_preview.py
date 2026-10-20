# -*- coding: utf-8 -*-
"""N3（2026-10-18）：预览接口必须能读出**真实形状**的列配置。

为什么单独测这一条：CS 侧的列配置挂在 `custom_config` 顶层（`columns_22` /
`requirement_fields_16`），**不在** `field_locators` 里。而预览接口只拿
`field_locators` 去解析——如果没有人把旧键带进去，预览会稳定显示 0 列，
配置方看到的是"我没配"，实际却是"你读不到"。这正是 N7 那一类"数据在、查不到"的翻版。

真实库实测（2026-10-18）：148 个子功能里只有 1 个（WSF-48576974）带列配置，
`columns_22` 实含 23 项（含「（勾选列）」），键名声明的 22 与实际不符 —— 必须报出来。
"""

from __future__ import annotations

import os
import uuid

import pytest

BASE = "/api/v2/projects/PR-C/web-form-templates"


@pytest.fixture()
async def client():
    import httpx
    from fastapi import FastAPI
    from sqlalchemy import create_engine, text
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from app.api.deps import get_current_user_id, get_db
    from app.api.v2.web_form_templates import router
    from app.config.settings import settings
    from app.models.base import Base

    from app.models import folder as _folder  # noqa: F401
    from app.models import project as _project  # noqa: F401
    from app.models import user as _user  # noqa: F401
    from app.models import web_form_template as _web_form_template  # noqa: F401
    from app.models import web_function as _web_function  # noqa: F401

    if os.getenv("PGPASSWORD") is None:
        os.environ["PGPASSWORD"] = "postgres"

    base_url = settings.postgres_sync_url
    test_name = "web_columns_preview_db"
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
        from app.models.web_function import WebFunction, WebSubFunction

        user = User(id=uuid.uuid4(), email="n3@example.com", username="n3",
                    password_hash="x", is_active=True)
        s.add(user)
        await s.flush()
        project = Project(identifier="PR-C", name="列契约项目", created_by=user.id)
        s.add(project)
        await s.flush()
        fn = WebFunction(project_id=project.id, identifier="WF-1",
                         name="CS 工单", display_name="CS 工单")
        s.add(fn)
        await s.flush()
        s.add(WebSubFunction(
            project_id=project.id, function_id=fn.id, identifier="WSF-CS-LIST",
            name="工单列表", display_name="工单列表",
            custom_config={
                "field_locators": {
                    "fields": {"Member": {"kind": "input", "selector_hint": "input"}},
                    "flow": "modal",
                },
                # 真实形状：旧列键在 **custom_config 顶层**
                "columns_22": ["（勾选列）", "Case No.", "Status", "Owner"],
                "requirement_fields_16": {"工单编号": "Case No.", "工单状态": "Status",
                                          "创建人名称": "Owner", "备注": "Remark"},
                "sortable_columns": "实测 22 列表头全部 sorter=true",
                "locators": {"table_thead": ".ant-table-thead th"},
            },
        ))
        await s.commit()

    app = FastAPI()
    app.include_router(router, prefix="/api/v2")
    app.dependency_overrides[get_current_user_id] = lambda: "user-1"

    async def _db_dep():
        async with session_factory() as s:
            yield s

    app.dependency_overrides[get_db] = _db_dep
    api = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")
    try:
        # 顺带把子功能 id 交给测试用
        async with session_factory() as s:
            from sqlalchemy import select

            from app.models.web_function import WebSubFunction

            row = (await s.execute(
                select(WebSubFunction).where(WebSubFunction.identifier == "WSF-CS-LIST")
            )).scalar_one()
            api.sub_function_id = str(row.id)  # type: ignore[attr-defined]
        yield api
    finally:
        await api.aclose()
        await engine.dispose()
        _admin_exec(f'DROP DATABASE IF EXISTS "{test_name}" WITH (FORCE)')


@pytest.mark.asyncio
async def test_preview_reads_legacy_column_keys(client) -> None:
    resp = await client.get(
        f"{BASE}/cs_module/resolve-preview",
        params={"sub_function_id": client.sub_function_id},
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()["data"]

    cols = data["columns"]
    assert cols["column_count"] == 4, cols          # 旧键被读出来了
    assert cols["source"] == "legacy_cs"
    assert cols["table_scope"] == ".ant-table-thead th"
    # 中文名 → key，英文表头 → header_text：断言里写中文也能对上
    by_header = {i["header_text"]: i for i in cols["items"]}
    assert by_header["Case No."]["key"] == "工单编号"
    # 键名声明的列数（22）与实际（4）不符 → 必须提示，否则按 22 列做断言会整体错位
    assert any("声明 22 列" in n for n in cols["notes"]), cols["notes"]
    # 本期没有列表执行器，必须显式标 False，别被读成"列检查已经会跑"
    assert cols["supported"] is False
    assert data["column_rejects"] == []


@pytest.mark.asyncio
async def test_preview_without_columns_is_empty_not_error(client) -> None:
    """没有列配置时给出 0 列而不是报错（147/148 个子功能处于这一档）。"""
    resp = await client.get(f"{BASE}/cs_module/resolve-preview")
    assert resp.status_code == 200, resp.text
    data = resp.json()["data"]
    assert data["columns"]["column_count"] == 0
    assert data["columns"]["supported"] is False
