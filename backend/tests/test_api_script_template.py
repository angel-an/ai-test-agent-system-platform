"""P2：脚本资产跟随模板版本的指纹与重建口径。

锁三件事：
1. 指纹本身（digest 稳定、可解析、rev 与 digest 两个判据各自生效）
2. stale 判据的四种结果互斥且不误伤（同 rev 同 digest 必须判 fresh）
3. 重建服务：dry_run 不动资产；真跑时覆盖同一 path、更新版本戳、单条失败不牵连
"""

from __future__ import annotations

import uuid
from types import SimpleNamespace

import pytest

from app.services import api_script_refresh_service as refreshmod
from app.services.api_script_template import (
    API_SCRIPT_TEMPLATE_REV,
    STALE_DIGEST,
    STALE_LEGACY,
    STALE_REV,
    fingerprint_line,
    parse_fingerprint,
    stale_reason,
    template_digest,
)


# --------------------------------------------------------------------------
# 指纹本体
# --------------------------------------------------------------------------

def test_template_digest_stable_and_short():
    first = template_digest(["a", "b"])
    assert first == template_digest(["a", "b"])
    assert len(first) == 12
    int(first, 16)  # 十六进制


def test_template_digest_separator_prevents_collision():
    """("ab","c") 与 ("a","bc") 拼接后同为 "abc"，必须靠分隔符区分。"""
    assert template_digest(["ab", "c"]) != template_digest(["a", "bc"])


def test_template_digest_changes_with_content():
    assert template_digest(["template v1"]) != template_digest(["template v2"])


def test_fingerprint_line_requires_digest():
    with pytest.raises(ValueError):
        fingerprint_line(digest="")


def test_fingerprint_roundtrip():
    line = fingerprint_line(digest="abc123def456")
    script = f"{line}\nimport {{ test }} from '@playwright/test';\n"
    assert parse_fingerprint(script) == (API_SCRIPT_TEMPLATE_REV, "abc123def456")


def test_parse_fingerprint_missing_returns_none():
    assert parse_fingerprint("// 手工脚本，没有指纹\nconsole.log(1);\n") == (None, None)
    assert parse_fingerprint("") == (None, None)


# --------------------------------------------------------------------------
# stale 判据
# --------------------------------------------------------------------------

def test_stale_legacy_when_no_stamp():
    assert stale_reason(None) == STALE_LEGACY
    assert stale_reason({}) == STALE_LEGACY


def test_stale_rev_mismatch():
    stamp = {"rev": "api-rev0", "digest": "whatever"}
    assert stale_reason(stamp, current_digest="whatever") == STALE_REV


def test_stale_digest_mismatch():
    stamp = {"rev": API_SCRIPT_TEMPLATE_REV, "digest": "olddigest123"}
    assert stale_reason(stamp, current_digest="newdigest456") == STALE_DIGEST


def test_fresh_when_rev_and_digest_match():
    stamp = {"rev": API_SCRIPT_TEMPLATE_REV, "digest": "same12345678"}
    assert stale_reason(stamp, current_digest="same12345678") is None


def test_no_false_positive_when_digest_unknown():
    """历史戳只有 rev（早于 digest 引入）时，不能凭空判过期。"""
    stamp = {"rev": API_SCRIPT_TEMPLATE_REV}
    assert stale_reason(stamp, current_digest="same12345678") is None


def test_current_module_digest_is_usable():
    """真实模板的 digest 必须是稳定 12 位——否则任一资产生成后立刻判过期。"""
    from app.services.api_case_link_service import script_template_digest

    digest = script_template_digest()
    assert len(digest) == 12
    assert script_template_digest() == digest


# --------------------------------------------------------------------------
# 重建服务
# --------------------------------------------------------------------------

class _Result:
    def __init__(self, rows):
        self._rows = list(rows)

    def scalars(self):
        return self

    def all(self):
        return self._rows

    def scalar_one_or_none(self):
        return self._rows[0] if self._rows else None


class _FakeSession:
    """按查询实体分派结果，避免依赖调用顺序。"""

    def __init__(self, *, project, api_tests, endpoint=None, attachments=()):
        self.project = project
        self.api_tests = api_tests
        self.endpoint = endpoint
        self.attachments = list(attachments)
        self.committed = False

    async def execute(self, stmt):
        entity = stmt.column_descriptions[0].get("entity")
        name = getattr(entity, "__name__", "")
        if name == "Project":
            return _Result([self.project] if self.project else [])
        if name == "APITest":
            return _Result(self.api_tests)
        if name == "Attachment":
            return _Result(self.attachments)
        return _Result([])

    async def get(self, model, pk):
        if self.endpoint is not None and pk == self.endpoint.id:
            return self.endpoint
        return None

    async def commit(self):
        self.committed = True


def _project(identifier="PR-2"):
    return SimpleNamespace(id=uuid.uuid4(), identifier=identifier)


def _api_test(*, identifier, stamp, with_endpoint=True):
    endpoint_id = uuid.uuid4()
    return SimpleNamespace(
        id=uuid.uuid4(),
        identifier=identifier,
        script_path=f"api-tests/PR-2/endpoints/{endpoint_id}/linked/x/test-script.ts",
        test_config={"endpoint_id": str(endpoint_id)} if with_endpoint else {},
        generation_params={"mode": "minimal_loop", "script_template": stamp} if stamp is not None else {"mode": "minimal_loop"},
        generated_by_agent="api_case_link_service",
        endpoints=[],
    )


def _endpoint():
    return SimpleNamespace(
        id=uuid.uuid4(),
        path="/ping",
        method="GET",
        display_name="ping",
        tag_group=None,
        parameters=[],
        request_body=None,
        custom_config={},
    )


@pytest.mark.asyncio
async def test_dry_run_reports_without_touching_storage(monkeypatch):
    calls = []
    monkeypatch.setattr(
        refreshmod.MinIOClient, "upload_bytes",
        classmethod(lambda cls, **kw: calls.append(kw)),
    )
    fresh_stamp = {"rev": API_SCRIPT_TEMPLATE_REV, "digest": "stale-not-mine"}
    session = _FakeSession(
        project=_project(),
        api_tests=[
            _api_test(identifier="AT-1", stamp=None),
            _api_test(identifier="AT-2", stamp=fresh_stamp),
        ],
    )
    report = await refreshmod.APIScriptRefreshService(session).refresh("PR-2", dry_run=True)

    assert report["checked"] == 2
    assert report["stale"] == 2  # legacy + digest 不同
    assert report["by_reason"][STALE_LEGACY]["count"] == 1
    assert report["by_reason"][STALE_DIGEST]["count"] == 1
    assert report["regenerated"] == 0
    assert {item["action"] for item in report["items"]} == {"would_regenerate"}
    assert calls == []
    assert session.committed is False


@pytest.mark.asyncio
async def test_regenerate_overwrites_same_path_and_stamps(monkeypatch):
    uploads = []

    def _fake_upload(cls, object_name=None, data=None, content_type=None):
        uploads.append({"object_name": object_name, "data": data, "content_type": content_type})
        return object_name

    monkeypatch.setattr(
        refreshmod.MinIOClient, "upload_bytes", classmethod(_fake_upload)
    )
    monkeypatch.setattr(
        refreshmod.MinIOClient, "download_file",
        classmethod(lambda cls, name: b"// old legacy script\n"),
    )
    endpoint = _endpoint()
    target = _api_test(identifier="AT-1", stamp=None)
    target.test_config = {"endpoint_id": str(endpoint.id)}
    attachment = SimpleNamespace(object_name=target.script_path, file_size=10)
    session = _FakeSession(
        project=_project(), api_tests=[target],
        endpoint=endpoint, attachments=[attachment],
    )

    report = await refreshmod.APIScriptRefreshService(session).refresh("PR-2", dry_run=False)

    assert report["regenerated"] == 1
    assert report["failed"] == 0

    # 先归档旧内容，再覆盖同一路径（顺序即安全保证：无备份不覆盖）
    assert "/history/" in uploads[0]["object_name"]
    assert uploads[0]["object_name"].endswith("-test-script.ts")
    assert uploads[0]["data"] == b"// old legacy script\n"

    assert uploads[1]["object_name"] == target.script_path
    assert uploads[1]["data"].startswith(b"// API_SCRIPT_TEMPLATE_REV: ")
    assert parse_fingerprint(uploads[1]["data"].decode("utf-8"))[0] == API_SCRIPT_TEMPLATE_REV

    # 版本戳与附件体积同步
    stamp = target.generation_params["script_template"]
    assert stamp["rev"] == API_SCRIPT_TEMPLATE_REV
    assert "regenerated_at" in stamp
    assert stamp["previous"]["archived_object"] == uploads[0]["object_name"]
    assert attachment.file_size == len(uploads[1]["data"])
    assert session.committed is True


@pytest.mark.asyncio
async def test_archive_failure_blocks_overwrite(monkeypatch):
    """归档失败必须中止该条重建：不能在没有备份的情况下覆盖旧脚本。"""
    def _boom_upload(cls, object_name=None, data=None, content_type=None):
        raise RuntimeError("archive upload failed")

    monkeypatch.setattr(refreshmod.MinIOClient, "upload_bytes", classmethod(_boom_upload))
    monkeypatch.setattr(
        refreshmod.MinIOClient, "download_file",
        classmethod(lambda cls, name: b"// old script\n"),
    )
    endpoint = _endpoint()
    target = _api_test(identifier="AT-1", stamp=None)
    target.test_config = {"endpoint_id": str(endpoint.id)}
    session = _FakeSession(project=_project(), api_tests=[target], endpoint=endpoint)

    report = await refreshmod.APIScriptRefreshService(session).refresh("PR-2", dry_run=False)

    assert report["regenerated"] == 0
    assert report["failed"] == 1
    assert "archive upload failed" in report["items"][0]["error"]


@pytest.mark.asyncio
async def test_missing_endpoint_is_reported_not_silently_skipped(monkeypatch):
    monkeypatch.setattr(
        refreshmod.MinIOClient, "upload_bytes",
        classmethod(lambda cls, **kw: pytest.fail("端点缺失时不该上传")),
    )
    orphan = _api_test(identifier="AT-9", stamp=None, with_endpoint=False)
    session = _FakeSession(project=_project(), api_tests=[orphan])
    report = await refreshmod.APIScriptRefreshService(session).refresh("PR-2", dry_run=False)

    assert report["regenerated"] == 0
    assert report["skipped"] == 1
    assert report["items"][0]["action"] == "skipped_missing_endpoint"
    assert "endpoint_id" in report["items"][0]["detail"]


@pytest.mark.asyncio
async def test_single_failure_does_not_abort_batch(monkeypatch):
    def _boom(cls, object_name=None, data=None, content_type=None):
        raise RuntimeError("minio down")

    monkeypatch.setattr(refreshmod.MinIOClient, "upload_bytes", classmethod(_boom))
    monkeypatch.setattr(
        refreshmod.MinIOClient, "download_file",
        classmethod(lambda cls, name: b"// old script\n"),
    )
    endpoint = _endpoint()
    first = _api_test(identifier="AT-1", stamp=None)
    second = _api_test(identifier="AT-2", stamp=None)
    session = _FakeSession(
        project=_project(), api_tests=[first, second], endpoint=endpoint,
    )
    # 两条都指向同一个端点
    first.test_config = second.test_config = {"endpoint_id": str(endpoint.id)}

    report = await refreshmod.APIScriptRefreshService(session).refresh("PR-2", dry_run=False)

    assert report["failed"] == 2
    assert "minio down" in report["items"][0]["error"]
