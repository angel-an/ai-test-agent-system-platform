# -*- coding: utf-8 -*-
"""Web 测试标识符解析回归测试。

背景：`GET /projects/{p}/web-tests/{id}` 用非 UUID 标识符（如 UI 上显示的 `WT-1C23A7A2`）
访问时会 `UUID(...)` 抛 ValueError 未被拦截 → **500 internal_server_error**。
现在要求：UUID → 按 id 查；`WT-` 形式 → 按 identifier 查；查不到 → None（由调用方 404）；
任何输入都**不允许抛出未处理异常**（即不允许 500）。
"""

from __future__ import annotations

import uuid

import pytest

from app.services.web_test_service import _resolve_web_test


class FakeRepo:
    """最小仓储替身：记录被调用的方法。"""

    def __init__(self, model=None, by_identifier=None):
        self.model = model
        self.by_identifier = by_identifier
        self.calls: list[tuple] = []

    async def get_by_id(self, value):
        self.calls.append(("get_by_id", value))
        return self.model

    async def get_by_id_with_relations(self, value):
        self.calls.append(("get_by_id_with_relations", value))
        return self.model

    async def get_by_identifier(self, value):
        self.calls.append(("get_by_identifier", value))
        return self.by_identifier


class TestResolveWebTest:
    def test_uuid_resolves_by_id(self):
        import asyncio

        uid = uuid.uuid4()
        repo = FakeRepo(model="M")
        got = asyncio.run(_resolve_web_test(repo, str(uid)))
        assert got == "M"
        assert repo.calls == [("get_by_id", uid)]

    def test_uuid_with_relations_uses_relations_loader(self):
        import asyncio

        uid = uuid.uuid4()
        repo = FakeRepo(model="M")
        asyncio.run(_resolve_web_test(repo, str(uid), with_relations=True))
        assert repo.calls == [("get_by_id_with_relations", uid)]

    def test_identifier_resolves_by_identifier(self):
        """UI 上显示的 WT- 标识符必须能直接访问（此前是 500）。"""
        import asyncio

        repo = FakeRepo(by_identifier="M2")
        got = asyncio.run(_resolve_web_test(repo, "WT-1C23A7A2"))
        assert got == "M2"
        assert repo.calls == [("get_by_identifier", "WT-1C23A7A2")]

    def test_unknown_identifier_returns_none_not_500(self):
        import asyncio

        repo = FakeRepo(by_identifier=None)
        assert asyncio.run(_resolve_web_test(repo, "WT-NOPE")) is None

    @pytest.mark.parametrize("raw", ["", "abc", "123", "WT-", "not-a-uuid", "../../etc/passwd"])
    def test_malformed_input_never_raises(self, raw):
        """任何畸形输入都不得抛出未处理异常（调用方据此返回 404）。"""
        import asyncio

        repo = FakeRepo(by_identifier=None)
        assert asyncio.run(_resolve_web_test(repo, raw)) is None
