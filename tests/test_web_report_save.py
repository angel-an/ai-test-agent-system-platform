import asyncio
import sys
from pathlib import Path
from uuid import uuid4

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from app.agents.tools.web import artifacts_tools
from app.models.attachment import Attachment, AttachmentEntityType
from app.models.web_function import WebSubFunction
from app.models.web_test import WebTest, WebTestRun


class FakeResult:
    def __init__(self, value):
        self.value = value

    def scalar_one_or_none(self):
        return self.value


class FakeSession:
    def __init__(self, responses):
        self.responses = {key: list(values) for key, values in responses.items()}
        self.added = []
        self.committed = False
        self.flushed = 0

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def execute(self, stmt):
        entity = stmt.column_descriptions[0]["entity"]
        values = self.responses.get(entity, [])
        value = values.pop(0) if values else None
        return FakeResult(value)

    def add(self, obj):
        if getattr(obj, "id", None) is None:
            obj.id = uuid4()
        self.added.append(obj)

    async def flush(self):
        self.flushed += 1
        for obj in self.added:
            if getattr(obj, "id", None) is None:
                obj.id = uuid4()

    async def commit(self):
        self.committed = True


def _make_test_run():
    return WebTestRun(
        id=uuid4(),
        project_id=uuid4(),
        web_test_id=uuid4(),
        identifier="WTR-test",
        status="completed",
        total_tests=1,
        passed_tests=1,
        failed_tests=0,
    )


def _patch_session(monkeypatch, session):
    monkeypatch.setattr(artifacts_tools, "async_session_factory", lambda: session)


def _patch_upload(monkeypatch):
    uploads = []

    def upload_bytes(**kwargs):
        uploads.append(kwargs)

    monkeypatch.setattr(artifacts_tools.MinIOClient, "upload_bytes", upload_bytes)
    return uploads


def test_save_web_test_report_creates_attachment_from_content(monkeypatch):
    test_run = _make_test_run()
    session = FakeSession(
        {
            WebTestRun: [test_run],
            Attachment: [None],
        }
    )
    _patch_session(monkeypatch, session)
    uploads = _patch_upload(monkeypatch)

    result = asyncio.run(
        artifacts_tools.save_web_test_report.coroutine(
            test_run_id=str(test_run.id),
            report_content="<html>执行结果：通过</html>",
            project_identifier="PR-9",
        )
    )

    assert result["success"] is True
    assert result["report_path"] == f"web-tests/PR-9/runs/{test_run.id}/report.html"
    assert test_run.report_path == result["report_path"]
    assert test_run.updated_at is not None
    assert session.committed is True
    assert len(uploads) == 1
    assert len(session.added) == 1
    attachment = session.added[0]
    assert attachment.entity_type == AttachmentEntityType.WEB_TEST_REPORT
    assert attachment.object_name == result["report_path"]
    assert "测试报告-通过-" in attachment.file_name


def test_save_web_test_report_missing_report_file_does_not_upload_or_commit(monkeypatch):
    test_run = _make_test_run()
    session = FakeSession({WebTestRun: [test_run]})
    _patch_session(monkeypatch, session)
    uploads = _patch_upload(monkeypatch)

    result = asyncio.run(
        artifacts_tools.save_web_test_report.coroutine(
            test_run_id=str(test_run.id),
            report_path="final_runs/missing-report.html",
            project_identifier="PR-9",
        )
    )

    assert result == {"error": "Report file not found: final_runs/missing-report.html"}
    assert uploads == []
    assert session.committed is False


def test_save_web_test_report_updates_existing_attachment_and_sub_function_stats(monkeypatch):
    sub_function = WebSubFunction(
        id=uuid4(),
        project_id=uuid4(),
        function_id=uuid4(),
        folder_id=uuid4(),
        identifier="SF-test",
        display_name="Member Management",
        name="Member Management",
        total_test_runs=1,
    )
    web_test = WebTest(
        id=uuid4(),
        project_id=sub_function.project_id,
        sub_function_id=sub_function.id,
        function_id=sub_function.function_id,
        folder_id=sub_function.folder_id,
        identifier="WT-test",
        name="Member Management",
        script_path="",
        script_format="playwright",
        script_language="python",
        generated_by_agent="web-agent",
    )
    test_run = _make_test_run()
    test_run.project_id = web_test.project_id
    test_run.web_test_id = web_test.id
    existing_attachment = Attachment(
        id=uuid4(),
        entity_type=AttachmentEntityType.WEB_TEST_REPORT,
        entity_id=sub_function.id,
        project_id=web_test.project_id,
        file_name="old.html",
        file_size=1,
        content_type="text/html",
        object_name=f"web-tests/PR-9/runs/{test_run.id}/report.html",
        description="old",
        created_by="web-agent",
    )
    session = FakeSession(
        {
            WebSubFunction: [sub_function, sub_function, sub_function],
            WebTest: [web_test],
            WebTestRun: [test_run],
            Attachment: [existing_attachment],
        }
    )
    _patch_session(monkeypatch, session)
    uploads = _patch_upload(monkeypatch)

    result = asyncio.run(
        artifacts_tools.save_web_test_report.coroutine(
            sub_function_id=str(sub_function.id),
            report_content="<html>执行结果：失败</html>",
            project_identifier="PR-9",
        )
    )

    assert result["success"] is True
    assert result["attachment_id"] == str(existing_attachment.id)
    assert len(uploads) == 1
    assert session.committed is True
    assert existing_attachment.file_size == len("<html>执行结果：失败</html>".encode("utf-8"))
    assert "Member-Management-测试报告-失败-" in existing_attachment.file_name
    assert sub_function.total_test_runs == 2
    assert sub_function.last_run_status == "failed"
