# -*- coding: utf-8 -*-
"""MongoDB 模型序列化回归测试 — 验证 ConfigDict 迁移后 json_encoders 行为不变。"""

from datetime import datetime
from uuid import UUID

import pytest

from app.models.mongodb.version_history import TestCaseVersionHistory
from app.models.mongodb.audit_log import AuditLog, AuditAction, EntityType
from app.models.mongodb.attachment import TestCaseAttachment
from app.models.mongodb.api_test_log import APITestDetailLog as ApiTestLog


class TestVersionHistorySerialization:
    def test_model_dump_json_encodes_uuid(self):
        uid = UUID("12345678-1234-5678-1234-567812345678")
        obj = TestCaseVersionHistory(
            test_case_id=uid,
            version=1,
            snapshot={"title": "T1"},
            changed_by=uid,
            change_description="init",
        )
        dumped = obj.model_dump(mode="json")
        assert dumped["test_case_id"] == str(uid)
        assert dumped["changed_by"] == str(uid)

    def test_model_dump_json_encodes_datetime(self):
        dt = datetime(2026, 1, 15, 10, 30, 0)
        uid = UUID("12345678-1234-5678-1234-567812345678")
        obj = TestCaseVersionHistory(
            test_case_id=uid,
            version=1,
            snapshot={},
            changed_by=uid,
            changed_at=dt,
        )
        dumped = obj.model_dump(mode="json")
        assert dumped["changed_at"] == "2026-01-15T10:30:00"


class TestAuditLogSerialization:
    def test_model_dump_json_encodes_uuid_and_datetime(self):
        uid = UUID("87654321-4321-8765-4321-876543218765")
        obj = AuditLog(
            entity_type=EntityType.TEST_CASE,
            entity_id=uid,
            action=AuditAction.CREATE,
            performed_by=uid,
            performed_at=datetime(2026, 3, 1, 8, 0, 0),
        )
        dumped = obj.model_dump(mode="json")
        assert dumped["entity_id"] == str(uid)
        assert dumped["performed_by"] == str(uid)
        assert dumped["performed_at"] == "2026-03-01T08:00:00"
        assert dumped["action"] == "create"
        assert dumped["entity_type"] == "test_case"


class TestAttachmentSerialization:
    def test_model_dump_json_encodes_uuid_and_datetime(self):
        uid = UUID("aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee")
        obj = TestCaseAttachment(
            test_case_id=uid,
            filename="test.png",
            original_filename="test.png",
            file_type="image/png",
            file_size=1024,
            file_path="/tmp/test.png",
            uploaded_by=uid,
            uploaded_at=datetime(2026, 6, 1, 12, 0, 0),
        )
        dumped = obj.model_dump(mode="json")
        assert dumped["test_case_id"] == str(uid)
        assert dumped["uploaded_by"] == str(uid)
        assert dumped["uploaded_at"] == "2026-06-01T12:00:00"


class TestApiTestLogSerialization:
    def test_model_dump_json_encodes_uuid_and_datetime(self):
        uid = UUID("11111111-2222-3333-4444-555555555555")
        obj = ApiTestLog(
            log_id="log-001",
            test_result_id=uid,
            test_run_id=uid,
            api_test_id=uid,
            scenario_name="s1",
            endpoint="/api/test",
            method="POST",
            started_at=datetime(2026, 7, 1, 9, 0, 0),
            completed_at=datetime(2026, 7, 1, 9, 0, 1),
        )
        dumped = obj.model_dump(mode="json")
        assert dumped["test_result_id"] == str(uid)
        assert dumped["started_at"] == "2026-07-01T09:00:00"
        assert dumped["completed_at"] == "2026-07-01T09:00:01"

    def test_model_dump_with_optional_none(self):
        uid = UUID("11111111-2222-3333-4444-555555555555")
        obj = ApiTestLog(
            log_id="log-002",
            test_result_id=uid,
            test_run_id=uid,
            api_test_id=uid,
            scenario_name="s2",
            endpoint="/api/test2",
            method="GET",
        )
        dumped = obj.model_dump(mode="json")
        assert dumped["completed_at"] is None
        assert dumped["error"] is None
        assert dumped["duration_ms"] is None


class TestToDocument:
    """验证 to_document() 与 model_dump(mode='json') 输出一致。"""

    def test_version_history_to_document_matches_json_dump(self):
        uid = UUID("12345678-1234-5678-1234-567812345678")
        dt = datetime(2026, 1, 15, 10, 30, 0)
        obj = TestCaseVersionHistory(
            test_case_id=uid,
            version=2,
            snapshot={"title": "T2"},
            changed_by=uid,
            changed_at=dt,
            change_description="update",
        )
        doc = obj.to_document()
        dumped = obj.model_dump(mode="json")
        # to_document 手动映射应与 json dump 一致
        assert doc["test_case_id"] == str(uid)
        assert doc["version"] == 2
        assert doc["changed_by"] == str(uid)
        # datetime 在 to_document 中直接传递对象，但在 json dump 中转为字符串
        # 这是预期差异：to_document 供 MongoDB driver 处理，model_dump 供 JSON 序列化
        assert isinstance(doc["changed_at"], datetime)
        assert dumped["changed_at"] == "2026-01-15T10:30:00"

    def test_audit_log_to_document_enum_value(self):
        uid = UUID("87654321-4321-8765-4321-876543218765")
        obj = AuditLog(
            entity_type=EntityType.FOLDER,
            entity_id=uid,
            action=AuditAction.MOVE,
            performed_by=uid,
        )
        doc = obj.to_document()
        assert doc["entity_type"] == "folder"
        assert doc["action"] == "move"
        assert doc["entity_id"] == str(uid)

    def test_attachment_to_document_uuid_str(self):
        uid = UUID("aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee")
        obj = TestCaseAttachment(
            test_case_id=uid,
            filename="f.png",
            original_filename="f.png",
            file_type="image/png",
            file_size=2048,
            file_path="/tmp/f.png",
            uploaded_by=uid,
        )
        doc = obj.to_document()
        assert doc["test_case_id"] == str(uid)
        assert doc["uploaded_by"] == str(uid)
        assert doc["file_size"] == 2048

    def test_api_test_log_to_document_uses_model_dump(self):
        uid = UUID("11111111-2222-3333-4444-555555555555")
        obj = ApiTestLog(
            log_id="log-003",
            test_result_id=uid,
            test_run_id=uid,
            api_test_id=uid,
            scenario_name="s3",
            endpoint="/api/v3",
            method="PUT",
        )
        doc = obj.to_document()
        dumped = obj.model_dump(mode="json")
        # api_test_log 的 to_document 直接调用 model_dump(mode='json')
        assert doc == dumped
        assert doc["test_result_id"] == str(uid)

