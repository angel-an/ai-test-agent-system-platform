# -*- coding: utf-8 -*-
"""长期能力测试：Collection 脚本断言（P0-2 收尾）与启动迁移预检（P0-4 部署接入）。

背景与边界
----------
1. **Collection 脚本断言**：真实美心 Collection 的 47 个请求**全部没有 test script**
   （实测），因此"断言数为 0"是数据事实而非解析缺陷。本文件用**合成脚本**验证
   提取能力本身，并明确记录真实样本的边界。
2. **启动迁移预检**：统一导入的幂等键依赖 api_endpoints 唯一约束；预检必须出现在
   部署路径上。默认只告警（不阻断既有部署），可用 MIGRATION_PREFLIGHT_STRICT 强制。
"""

from __future__ import annotations

import json

import pytest


# ---------------------------------------------------------------------------
# 1. Collection test script 断言提取
# ---------------------------------------------------------------------------

class TestCollectionAssertionExtraction:
    """`_extract_assertions` 的模式覆盖（合成脚本，不依赖真实样本）。"""

    def _extract(self, script: str) -> list[dict]:
        from app.services.postman_collection_parser import PostmanCollectionParser

        return PostmanCollectionParser()._extract_assertions(script)

    def test_pm_expect_equal(self):
        out = self._extract("pm.expect(pm.response.json().Code).to.eql(0);")
        assert any(a["type"] == "expect_equal" for a in out), out

    def test_status_assertion(self):
        out = self._extract("pm.response.to.have.status(200);")
        status = [a for a in out if a["type"] == "status"]
        assert status, out
        assert status[0]["expected"] == "200"

    def test_response_code_equal(self):
        out = self._extract("pm.expect(pm.response.code).to.equal(200);")
        assert any(a["type"] == "status" for a in out), out

    def test_body_contains(self):
        out = self._extract("pm.expect(pm.response.text()).to.include('Success');")
        contains = [a for a in out if a["type"] == "body_contains"]
        assert contains, out
        assert "Success" in json.dumps(contains, ensure_ascii=False)

    def test_json_path(self):
        out = self._extract("jsonData = pm.response.json(); pm.expect(jsonData.Code).to.eql(0);")
        assert any(a["type"] == "json_path" for a in out), out

    def test_multiple_patterns_in_one_script(self):
        script = "\n".join([
            "pm.response.to.have.status(200);",
            "const jsonData = pm.response.json();",
            "pm.test('code ok', () => { pm.expect(jsonData.Code).to.eql(0); });",
            "pm.expect(pm.response.text()).to.include('Success');",
        ])
        out = self._extract(script)
        kinds = {a["type"] for a in out}
        assert {"status", "json_path", "body_contains"} <= kinds, kinds

    def test_empty_script_yields_nothing(self):
        assert self._extract("") == []

    def test_script_without_assertions_yields_nothing(self):
        assert self._extract("console.log('no assertion here');") == []

    def test_every_assertion_is_traceable_to_script(self):
        """每条断言都必须带来源标记，否则无法追溯。"""
        out = self._extract("pm.response.to.have.status(201);")
        assert out and all(a.get("source") == "collection_script" for a in out)


class TestRealCollectionBoundary:
    """真实样本的边界：断言数 0 是数据事实，必须显式记录而不是当成缺陷。"""

    from tests.sample_paths import COLLECTION_PATH

    SAMPLE = str(COLLECTION_PATH)

    def test_real_collection_has_no_test_scripts(self):
        from pathlib import Path

        if not Path(self.SAMPLE).exists():
            pytest.skip(f"真实样本不存在: {self.SAMPLE}")

        from app.services.postman_collection_parser import PostmanCollectionParser

        coll = PostmanCollectionParser().parse(Path(self.SAMPLE).read_bytes())
        assert len(coll.requests) > 0, "样本应有请求"
        with_script = [r for r in coll.requests if (r.test_script or "").strip()]
        # 这是**数据事实**：该 Collection 未定义任何 test script。
        # 一旦业务方补上脚本，这条断言会失败并提醒更新验收预期。
        assert with_script == [], (
            f"真实 Collection 出现了 {len(with_script)} 个 test script，"
            "请更新本测试与 P0-2 的验收预期"
        )
        assert sum(len(r.assertions) for r in coll.requests) == 0


# ---------------------------------------------------------------------------
# 2. Collection 断言 → 契约 → 落库（端到端，纯函数段）
# ---------------------------------------------------------------------------

class TestAssertionsReachContract:
    """解析出的断言必须进入统一契约的 assertions 字段（供 confirm 落库）。"""

    def _collection_with_script(self) -> dict:
        return {
            "info": {
                "name": "断言样本集合",
                "schema": "https://schema.getpostman.com/json/collection/v2.1.0/collection.json",
            },
            "item": [{
                "name": "query member",
                "request": {
                    "method": "POST",
                    "url": {"raw": "https://uat.example.com/api/json/queryMember",
                            "path": ["api", "json", "queryMember"]},
                },
                "event": [{
                    "listen": "test",
                    "script": {"exec": [
                        "pm.response.to.have.status(200);",
                        "const jsonData = pm.response.json();",
                        "pm.expect(jsonData.Code).to.eql(0);",
                    ]},
                }],
            }],
        }

    def test_parser_extracts_assertions_from_json(self):
        from app.services.postman_collection_parser import PostmanCollectionParser

        coll = PostmanCollectionParser().parse(
            json.dumps(self._collection_with_script()).encode("utf-8")
        )
        request = coll.requests[0]
        assert request.test_script, "test script 应被保留"
        kinds = {a["type"] for a in request.assertions}
        assert "status" in kinds and "json_path" in kinds, kinds

    def test_parser_keeps_script_for_traceability(self):
        from app.services.postman_collection_parser import PostmanCollectionParser

        coll = PostmanCollectionParser().parse(
            json.dumps(self._collection_with_script()).encode("utf-8")
        )
        assert "pm.response.to.have.status(200)" in (coll.requests[0].test_script or "")


# ---------------------------------------------------------------------------
# 3. 启动迁移预检（P0-4 部署接入）
# ---------------------------------------------------------------------------

class TestStartupMigrationPreflight:
    """`run_migration_preflight` 必须把"结构未就绪"暴露在部署路径上。"""

    async def test_disabled_by_settings(self, monkeypatch):
        from app import main as main_module
        from app.config.settings import settings

        monkeypatch.setattr(settings, "migration_preflight_enabled", False)
        called = {"n": 0}

        async def _boom(*args, **kwargs):
            called["n"] += 1

        monkeypatch.setattr(main_module.asyncio, "to_thread", _boom)
        await main_module.run_migration_preflight()
        assert called["n"] == 0, "禁用时不应执行预检"

    async def test_pending_migration_warns_without_raising(self, monkeypatch):
        from app import main as main_module
        from app.config.settings import settings

        monkeypatch.setattr(settings, "migration_preflight_enabled", True)
        monkeypatch.setattr(settings, "migration_preflight_strict", False)

        async def _report(*args, **kwargs):
            return {"exit_code": 0, "head": "0099", "pending": ["0099"], "drift": []}

        monkeypatch.setattr(main_module.asyncio, "to_thread", _report)
        # 不抛异常即为通过（默认只告警）
        await main_module.run_migration_preflight()

    async def test_drift_raises_when_strict(self, monkeypatch):
        from app import main as main_module
        from app.config.settings import settings

        monkeypatch.setattr(settings, "migration_preflight_enabled", True)
        monkeypatch.setattr(settings, "migration_preflight_strict", True)

        async def _report(*args, **kwargs):
            return {
                "exit_code": 2,
                "head": "0021",
                "pending": [],
                "drift": [{"revision": "0017", "file": "0017_x.py", "partial": False}],
            }

        monkeypatch.setattr(main_module.asyncio, "to_thread", _report)
        with pytest.raises(RuntimeError, match="版本漂移"):
            await main_module.run_migration_preflight()

    async def test_drift_only_warns_when_not_strict(self, monkeypatch):
        from app import main as main_module
        from app.config.settings import settings

        monkeypatch.setattr(settings, "migration_preflight_enabled", True)
        monkeypatch.setattr(settings, "migration_preflight_strict", False)

        async def _report(*args, **kwargs):
            return {
                "exit_code": 2,
                "head": "0021",
                "pending": [],
                "drift": [{"revision": "0017", "partial": True}],
            }

        monkeypatch.setattr(main_module.asyncio, "to_thread", _report)
        await main_module.run_migration_preflight()

    async def test_preflight_failure_never_blocks_startup(self, monkeypatch):
        """预检本身异常（如库连不上）不得阻断启动。"""
        from app import main as main_module
        from app.config.settings import settings

        monkeypatch.setattr(settings, "migration_preflight_enabled", True)

        async def _boom(*args, **kwargs):
            raise RuntimeError("db down")

        monkeypatch.setattr(main_module.asyncio, "to_thread", _boom)
        await main_module.run_migration_preflight()

    async def test_all_clear_path_does_not_raise(self, monkeypatch):
        from app import main as main_module
        from app.config.settings import settings

        monkeypatch.setattr(settings, "migration_preflight_enabled", True)
        monkeypatch.setattr(settings, "migration_preflight_strict", False)

        async def _report(*args, **kwargs):
            return {"exit_code": 0, "head": "0021", "pending": [], "drift": []}

        monkeypatch.setattr(main_module.asyncio, "to_thread", _report)
        await main_module.run_migration_preflight()

    def test_lifespan_is_still_a_context_manager(self):
        """回归：曾经误删 lifespan 的装饰器，导致应用无法启动。"""
        from app.main import lifespan

        assert hasattr(lifespan, "__wrapped__"), "lifespan 必须仍由 asynccontextmanager 包装"
