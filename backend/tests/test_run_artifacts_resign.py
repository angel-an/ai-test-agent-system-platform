"""报告/证据链接"过期可重签"的门禁测试（2026-10-28）。

背景：证据与报告的下载地址是**预签名 URL**，S3 协议上限 7 天。
执行时把链接写进了 IDP 缺陷描述（不可变），一周后点开必然 403；
对象本身是长期保留的，所以正确做法是"对象名恒定、链接现签"。

本文件锁住：
1. ``GET .../runs/{run_id}/artifacts`` 每次都**新签**（不复用执行时那串）；
2. 报告对象缺失时 url 为 None，不返回坏链接；
3. 证据按 run 前缀列出并签到，trace 排在最前；
4. 不是本项目的 run 一律 404（防止跨项目猜 uuid 取证据）。
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.services import evidence_artifacts as ev_mod
from app.services.api_test_service import APITestService
from app.utils.exceptions import NotFoundException

#: UUID 形参：实现里会 UUID(run_id)，非 UUID 串在进入逻辑前就抛 ValueError
RUN1 = "11111111-1111-1111-1111-111111111111"
RUN2 = "22222222-2222-2222-2222-222222222222"
RUN3 = "33333333-3333-3333-3333-333333333333"


def _service(run):
    svc = APITestService.__new__(APITestService)

    async def _proj(_identifier):
        return SimpleNamespace(id="proj-1")

    class _Repo:
        async def get_by_id(self, _run_id):
            return run

    svc._get_project_by_identifier = _proj
    svc.api_test_run_repo = _Repo()
    return svc


@pytest.mark.asyncio
async def test_artifacts_resigns_report_and_evidence(monkeypatch):
    run = SimpleNamespace(
        id=RUN1,
        identifier="ATR-20261028-009",
        project_id="proj-1",
        report_path=f"api-test-reports/{RUN1}/allure-report.zip",
    )

    signed_calls: list[str] = []

    def _presign(object_name, days=None):
        signed_calls.append(object_name)
        return f"https://minio.example/{object_name}?X-Amz-Signature=fresh"

    monkeypatch.setattr(ev_mod, "presigned_url_for", _presign)
    monkeypatch.setattr(
        ev_mod,
        "list_run_evidence",
        lambda run_id, **kw: [
            {"kind": "trace", "name": "trace.zip", "object_name": f"api-test-evidence/{run_id}/t/trace.zip",
             "url": "https://minio.example/trace?fresh", "size": 10},
            {"kind": "screenshot", "name": "1.png", "object_name": f"api-test-evidence/{run_id}/t/1.png",
             "url": "https://minio.example/1.png?fresh", "size": 20},
        ],
    )

    result = await _service(run).get_run_artifacts("PR-2", "api-1", RUN1)

    assert signed_calls == [f"api-test-reports/{RUN1}/allure-report.zip"]
    assert "X-Amz-Signature" in result["report"]["url"]
    assert result["evidence"]["count"] == 2
    assert result["evidence"]["trace_url"].endswith("trace?fresh")
    assert result["evidence"]["screenshot_url"].endswith("1.png?fresh")
    assert result["expires_days"] == ev_mod.PRESIGN_DAYS


@pytest.mark.asyncio
async def test_missing_report_yields_none_not_broken_link(monkeypatch):
    run = SimpleNamespace(id=RUN2, identifier="ATR-X", project_id="proj-1", report_path=None)
    monkeypatch.setattr(ev_mod, "presigned_url_for", lambda *a, **k: "https://should-not-be-used")
    monkeypatch.setattr(ev_mod, "list_run_evidence", lambda run_id, **kw: [])

    result = await _service(run).get_run_artifacts("PR-2", "api-1", RUN2)

    assert result["report"]["url"] is None
    assert result["evidence"]["count"] == 0
    assert result["evidence"]["trace_url"] is None


@pytest.mark.asyncio
async def test_foreign_run_is_404(monkeypatch):
    run = SimpleNamespace(id=RUN3, identifier="ATR-Y", project_id="other-project", report_path=None)
    monkeypatch.setattr(ev_mod, "list_run_evidence", lambda run_id, **kw: [])

    with pytest.raises(NotFoundException):
        await _service(run).get_run_artifacts("PR-2", "api-1", RUN3)


def test_list_run_evidence_signs_every_object(monkeypatch):
    """列对象 → 逐条现签；trace 置顶；坏对象名跳过而不是整批失败。"""
    objects = [
        {"object_name": "api-test-evidence/run-9/abc/1.png", "size": 5, "last_modified": None},
        {"object_name": "api-test-evidence/run-9/abc/trace.zip", "size": 7, "last_modified": None},
    ]
    monkeypatch.setattr(
        ev_mod.MinIOClient, "list_objects", classmethod(lambda cls, prefix="", **kw: objects)
    )
    monkeypatch.setattr(ev_mod, "presigned_url_for", lambda name, days=None: f"u/{name}")

    items = ev_mod.list_run_evidence("run-9")

    assert [i["kind"] for i in items] == ["trace", "screenshot"]
    assert items[0]["parent"] == "abc"
    assert all(i["url"].startswith("u/api-test-evidence/run-9/") for i in items)


def test_list_run_evidence_survives_storage_failure(monkeypatch):
    def _boom(cls, prefix="", **kw):
        raise RuntimeError("minio down")

    monkeypatch.setattr(ev_mod.MinIOClient, "list_objects", classmethod(_boom))
    assert ev_mod.list_run_evidence("run-9") == []
