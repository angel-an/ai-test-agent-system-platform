"""执行前自动跟随模板（``/run`` 链路）的口径与降级行为。

锁三件事：
1. 只有模板产物才比对（人工/导入脚本不该被改写）
2. 过期即重建，且**返回重建后的内容**（不是让调用方再下载一次）
3. 重建/比对失败**不阻塞执行**：按旧脚本继续跑，状态落到 run 上留痕
"""

from __future__ import annotations

import uuid
from types import SimpleNamespace

import pytest

from app.services import api_script_refresh_service as refreshmod
from app.services.api_script_refresh_service import (
    ACTION_CURRENT,
    ACTION_FAILED,
    ACTION_NOT_TEMPLATE,
    ACTION_REGENERATED,
    ACTION_SKIPPED_NO_ENDPOINT,
    APIScriptRefreshService,
)
from app.services.api_script_template import API_SCRIPT_TEMPLATE_REV, parse_fingerprint
from app.services.api_test_executor import SCRIPT_CHECK_FAILED, APITestExecutor

CURRENT_STAMP = {"rev": API_SCRIPT_TEMPLATE_REV, "digest": None}  # digest 在用例里补


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
    def __init__(self, endpoint=None):
        self.endpoint = endpoint
        self.committed = False

    async def execute(self, stmt):
        return _Result([])

    async def get(self, model, pk):
        if self.endpoint is not None and pk == self.endpoint.id:
            return self.endpoint
        return None

    async def commit(self):
        self.committed = True


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


def _api_test(*, stamp, endpoint=None, generated_by="api_case_link_service"):
    endpoint_id = endpoint.id if endpoint is not None else uuid.uuid4()
    return SimpleNamespace(
        id=uuid.uuid4(),
        identifier="AT-1",
        script_path=f"api-tests/PR-2/endpoints/{endpoint_id}/linked/x/test-script.ts",
        test_config={"endpoint_id": str(endpoint_id)},
        generation_params={"script_template": stamp} if stamp is not None else {},
        generated_by_agent=generated_by,
    )


def _stub_upload(monkeypatch, sink):
    def _fake(cls, object_name=None, data=None, content_type=None):
        sink.append({"object_name": object_name, "data": data})
        return object_name

    monkeypatch.setattr(refreshmod.MinIOClient, "upload_bytes", classmethod(_fake))


class TestEnsureCurrent:
    @pytest.mark.asyncio
    async def test_not_template_is_left_alone(self, monkeypatch):
        sink = []
        _stub_upload(monkeypatch, sink)
        endpoint = _endpoint()
        api_test = _api_test(stamp=None, endpoint=endpoint, generated_by="manual_upload")

        action, content, _ = await APIScriptRefreshService(_FakeSession(endpoint)).ensure_current(
            api_test, current_content="// 人工脚本"
        )

        assert action == ACTION_NOT_TEMPLATE
        assert content is None
        assert sink == []  # 一个字节都不该上传

    @pytest.mark.asyncio
    async def test_current_stamp_skips_rebuild(self, monkeypatch):
        from app.services.api_case_link_service import script_template_digest

        sink = []
        _stub_upload(monkeypatch, sink)
        endpoint = _endpoint()
        api_test = _api_test(
            stamp={"rev": API_SCRIPT_TEMPLATE_REV, "digest": script_template_digest()},
            endpoint=endpoint,
        )
        session = _FakeSession(endpoint)

        action, content, _ = await APIScriptRefreshService(session).ensure_current(
            api_test, current_content="// fresh"
        )

        assert action == ACTION_CURRENT
        assert content is None
        assert sink == []
        assert session.committed is False

    @pytest.mark.asyncio
    async def test_stale_rebuilds_and_returns_content(self, monkeypatch):
        sink = []
        _stub_upload(monkeypatch, sink)
        endpoint = _endpoint()
        api_test = _api_test(stamp=None, endpoint=endpoint)  # legacy：无指纹
        session = _FakeSession(endpoint)

        action, content, reason = await APIScriptRefreshService(session).ensure_current(
            api_test, current_content="// 旧脚本，没有指纹\n"
        )

        assert action == ACTION_REGENERATED
        assert reason == "legacy_no_fingerprint"
        # 归档旧内容 + 覆盖同一路径
        assert len(sink) == 2
        assert "/history/" in sink[0]["object_name"]
        assert sink[0]["data"] == b"// \xe6\x97\xa7\xe8\x84\x9a\xe6\x9c\xac\xef\xbc\x8c\xe6\xb2\xa1\xe6\x9c\x89\xe6\x8c\x87\xe7\xba\xb9\n"
        assert sink[1]["object_name"] == api_test.script_path
        # 返回的是新内容，调用方直接拿去执行（不用再下载一次）
        assert content is not None
        assert parse_fingerprint(content)[0] == API_SCRIPT_TEMPLATE_REV
        assert api_test.generation_params["script_template"]["rev"] == API_SCRIPT_TEMPLATE_REV
        # 后台会话：必须提交，否则戳被回滚而对象存储已覆盖
        assert session.committed is True

    @pytest.mark.asyncio
    async def test_rebuild_failure_returns_none(self, monkeypatch):
        def _boom(cls, **kw):
            raise RuntimeError("minio down")

        monkeypatch.setattr(refreshmod.MinIOClient, "upload_bytes", classmethod(_boom))
        endpoint = _endpoint()
        api_test = _api_test(stamp=None, endpoint=endpoint)
        session = _FakeSession(endpoint)

        action, content, detail = await APIScriptRefreshService(session).ensure_current(
            api_test, current_content="// old"
        )

        assert action == ACTION_FAILED
        assert content is None  # 调用方据此继续用旧内容
        assert "minio down" in detail

    @pytest.mark.asyncio
    async def test_missing_endpoint_reported_not_silent(self, monkeypatch):
        sink = []
        _stub_upload(monkeypatch, sink)
        api_test = _api_test(stamp=None, endpoint=None)
        session = _FakeSession(endpoint=None)

        action, content, detail = await APIScriptRefreshService(session).ensure_current(
            api_test, current_content="// old"
        )

        assert action == ACTION_SKIPPED_NO_ENDPOINT
        assert content is None
        assert "端点已不存在" in detail
        assert sink == []


class _RunRepo:
    def __init__(self, run):
        self.run = run
        self.updates = []

    async def get_by_id(self, _id):
        return self.run

    async def update(self, row, **kw):
        self.updates.append(kw)


class TestExecutorWiring:
    @staticmethod
    def _executor(session, run_repo):
        executor = APITestExecutor.__new__(APITestExecutor)
        executor.session = session
        executor.api_test_run_repo = run_repo
        return executor

    @pytest.mark.asyncio
    async def test_regenerated_stamps_run(self, monkeypatch):
        sink = []
        _stub_upload(monkeypatch, sink)
        endpoint = _endpoint()
        api_test = _api_test(stamp=None, endpoint=endpoint)
        run = SimpleNamespace(execution_config={"base_url": "https://x"})
        repo = _RunRepo(run)
        executor = self._executor(_FakeSession(endpoint), repo)

        action, content, reason = await executor._ensure_script_current(
            run_id=uuid.uuid4(), api_test=api_test, script_content="// old"
        )

        assert action == ACTION_REGENERATED
        assert content is not None
        assert repo.updates and repo.updates[0]["execution_config"]["script_template"] == {
            "action": ACTION_REGENERATED,
            "reason": reason,
            "rev": API_SCRIPT_TEMPLATE_REV,
        }
        # 原有 execution_config 不能被覆盖掉
        assert repo.updates[0]["execution_config"]["base_url"] == "https://x"

    @pytest.mark.asyncio
    async def test_current_does_not_touch_run(self, monkeypatch):
        from app.services.api_case_link_service import script_template_digest

        sink = []
        _stub_upload(monkeypatch, sink)
        endpoint = _endpoint()
        api_test = _api_test(
            stamp={"rev": API_SCRIPT_TEMPLATE_REV, "digest": script_template_digest()},
            endpoint=endpoint,
        )
        repo = _RunRepo(SimpleNamespace(execution_config={}))
        executor = self._executor(_FakeSession(endpoint), repo)

        action, content, _ = await executor._ensure_script_current(
            run_id=uuid.uuid4(), api_test=api_test, script_content="// fresh"
        )

        assert action == ACTION_CURRENT
        assert content is None
        assert repo.updates == []

    @pytest.mark.asyncio
    async def test_compare_crash_degrades_without_blocking(self, monkeypatch):
        """比对自身炸了也不能让用例跑不起来 —— 降级成 check_failed 继续用旧脚本。"""

        def _boom(self, *a, **kw):
            raise RuntimeError("db gone")

        monkeypatch.setattr(APIScriptRefreshService, "ensure_current", _boom)
        executor = self._executor(_FakeSession(), _RunRepo(None))

        action, content, detail = await executor._ensure_script_current(
            run_id=uuid.uuid4(),
            api_test=_api_test(stamp=None, endpoint=_endpoint()),
            script_content="// old",
        )

        assert action == SCRIPT_CHECK_FAILED
        assert content is None
        assert "db gone" in detail
