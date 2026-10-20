import asyncio
import json
import sys
from pathlib import Path
from uuid import uuid4

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from app.agents.tools.web import artifacts_tools
from app.agents.tools.web import execution_tools
from app.models.attachment import Attachment, AttachmentEntityType
from app.models.web_function import WebSubFunction


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
        self.refreshed = []

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

    async def commit(self):
        self.committed = True

    async def refresh(self, obj):
        self.refreshed.append(obj)


def test_save_test_report_prefers_native_webwright_html(monkeypatch, tmp_path):
    native_html = "<html><body>native CRUD report CREATE EDIT DELETE</body></html>"
    (tmp_path / "test_report.html").write_text(native_html, encoding="utf-8")
    (tmp_path / "self_reflect_result.json").write_text(
        json.dumps({"summary": {"PASS": 7, "WARN": 0, "FAIL": 0}}, ensure_ascii=False),
        encoding="utf-8",
    )

    sub_function = WebSubFunction(
        id=uuid4(),
        project_id=uuid4(),
        function_id=uuid4(),
        folder_id=uuid4(),
        identifier="WSF-1124",
        display_name="会员日管理 (Membership Day Management)",
        name="Membership Day Management",
    )
    session = FakeSession({Attachment: [None]})
    uploads = []
    backups = []

    monkeypatch.setattr(execution_tools, "async_session_factory", lambda: session)
    monkeypatch.setattr(
        execution_tools.MinIOClient,
        "upload_bytes",
        lambda **kwargs: uploads.append(kwargs),
    )
    monkeypatch.setattr(
        artifacts_tools,
        "_backup_to_local",
        lambda object_name, data: backups.append((object_name, data)),
    )

    attachment_id = asyncio.run(
        execution_tools._save_test_report(
            sub_function_id=str(sub_function.id),
            project_identifier="PR-9",
            sub_function=sub_function,
            report_path=str(tmp_path),
            execution_result={"success": True, "duration": 55.62},
            project_root=str(tmp_path),
        )
    )

    assert attachment_id == str(session.added[0].id)
    assert session.committed is True
    assert len(uploads) == 1
    assert uploads[0]["data"] == native_html.encode("utf-8")
    assert uploads[0]["content_type"] == "text/html"
    assert backups == [(uploads[0]["object_name"], native_html.encode("utf-8"))]

    attachment = session.added[0]
    assert attachment.entity_type == AttachmentEntityType.WEB_TEST_REPORT
    assert attachment.entity_id == sub_function.id
    assert attachment.project_id == sub_function.project_id
    assert "测试报告-通过-" in attachment.file_name
    assert "通过: 7 | 失败: 0 | 警告: 0" in attachment.description
