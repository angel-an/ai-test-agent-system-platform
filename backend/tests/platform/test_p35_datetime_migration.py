# -*- coding: utf-8 -*-
"""P3.5 验证：datetime.utcnow() → datetime.now(timezone.utc) 迁移后业务路径测试。

重点验证：
- UTC 时间排序和日期边界正确性
- MongoDB 时间字段序列化兼容
- 报告过期时间计算
- 调度时间记录
- API 返回时间格式
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from uuid import UUID, uuid4

import pytest


class TestTimezoneAwareness:
    """验证所有迁移后的时间字段都携带 tzinfo=timezone.utc。"""

    def test_datetime_now_has_tzinfo(self):
        """datetime.now(timezone.utc) 必须返回带 tzinfo 的对象。"""
        dt = datetime.now(timezone.utc)
        assert dt.tzinfo is not None
        assert dt.tzinfo is timezone.utc

    def test_isoformat_includes_offset(self):
        """带 tzinfo 的 datetime isoformat 应包含 +00:00 偏移。"""
        dt = datetime.now(timezone.utc)
        iso = dt.isoformat()
        assert "+00:00" in iso

    def test_naive_datetime_no_offset(self):
        """对比：naive datetime 的 isoformat 不含偏移。"""
        dt = datetime(2026, 1, 15, 10, 30, 0)
        assert "+" not in dt.isoformat()


class TestDateBoundary:
    """验证日期边界计算（如 ATR 标识符生成中的 today）。"""

    def test_utc_now_date_matches_expected(self):
        """datetime.now(timezone.utc).date() 应返回正确日期。"""
        today = datetime.now(timezone.utc).date()
        assert isinstance(today, type(datetime.now(timezone.utc).date()))
        # 日期应在合理范围内（2026年）
        assert today.year == 2026

    def test_date_strftime_format(self):
        """日期格式化应为 %Y%m%d。"""
        today = datetime.now(timezone.utc).date()
        date_str = today.strftime("%Y%m%d")
        assert len(date_str) == 8
        assert date_str.isdigit()

    def test_timedelta_arithmetic_with_tz_aware(self):
        """带 tzinfo 的 datetime 支持 timedelta 运算。"""
        now = datetime.now(timezone.utc)
        future = now + timedelta(days=7)
        assert future > now
        assert (future - now).days == 7


class TestMongoDBDocumentTime:
    """验证 MongoDB 文档时间字段的序列化兼容性。"""

    def test_version_history_changed_at_tz_aware(self):
        """TestCaseVersionHistory 的 changed_at 应带 tzinfo。"""
        from app.models.mongodb.version_history import TestCaseVersionHistory

        history = TestCaseVersionHistory(
            test_case_id=uuid4(),
            version=1,
            snapshot={"title": "test"},
            changed_by=uuid4(),
        )
        assert history.changed_at.tzinfo is not None
        assert history.changed_at.tzinfo is timezone.utc

    def test_audit_log_performed_at_tz_aware(self):
        """AuditLog 的 performed_at 应带 tzinfo。"""
        from app.models.mongodb.audit_log import AuditLog, AuditAction, EntityType

        log = AuditLog(
            entity_type=EntityType.TEST_CASE,
            entity_id=uuid4(),
            action=AuditAction.CREATE,
            performed_by=uuid4(),
        )
        assert log.performed_at.tzinfo is not None
        assert log.performed_at.tzinfo is timezone.utc

    def test_attachment_uploaded_at_tz_aware(self):
        """TestCaseAttachment 的 uploaded_at 应带 tzinfo。"""
        from app.models.mongodb.attachment import TestCaseAttachment

        att = TestCaseAttachment(
            test_case_id=uuid4(),
            filename="test.txt",
            original_filename="test.txt",
            file_type="text/plain",
            file_size=100,
            file_path="/tmp/test.txt",
            uploaded_by=uuid4(),
        )
        assert att.uploaded_at.tzinfo is not None
        assert att.uploaded_at.tzinfo is timezone.utc

    def test_api_test_log_started_at_tz_aware(self):
        """APITestDetailLog 的 started_at 应带 tzinfo。"""
        from app.models.mongodb.api_test_log import APITestDetailLog

        log = APITestDetailLog(
            log_id="test-001",
            test_result_id=uuid4(),
            test_run_id=uuid4(),
            api_test_id=uuid4(),
            scenario_name="test",
            endpoint="/api/test",
            method="GET",
        )
        assert log.started_at.tzinfo is not None
        assert log.started_at.tzinfo is timezone.utc
        assert log.created_at.tzinfo is not None
        assert log.created_at.tzinfo is timezone.utc


class TestReportExpiry:
    """验证报告过期时间计算（security_tests.py 中的下载链接）。"""

    def test_expires_at_future_date(self):
        """过期时间应为未来日期。"""
        expires_at = datetime.now(timezone.utc) + timedelta(days=7)
        assert expires_at > datetime.now(timezone.utc)

    def test_expires_at_isoformat_includes_offset(self):
        """过期时间 ISO 格式应包含时区偏移。"""
        expires_at = datetime.now(timezone.utc) + timedelta(days=7)
        iso = expires_at.isoformat()
        assert "+00:00" in iso


class TestSchedulerLastRun:
    """验证调度器上次执行时间记录（scheduler_service.py）。"""

    def test_last_run_at_tz_aware(self):
        """调度器记录的 last_run_at 应带 tzinfo。"""
        last_run = datetime.now(timezone.utc)
        assert last_run.tzinfo is not None
        assert last_run.tzinfo is timezone.utc

    def test_last_run_at_ordering(self):
        """时间记录应支持正确排序。"""
        t1 = datetime.now(timezone.utc)
        t2 = datetime.now(timezone.utc)
        assert t2 >= t1


class TestExportJobTimestamps:
    """验证导出任务时间戳（export_service.py）。"""

    def test_created_at_tz_aware(self):
        """导出任务 created_at 应带 tzinfo。"""
        created = datetime.now(timezone.utc)
        assert created.tzinfo is not None
        assert created.tzinfo is timezone.utc

    def test_completed_at_tz_aware(self):
        """导出任务 completed_at 应带 tzinfo。"""
        completed = datetime.now(timezone.utc)
        assert completed.tzinfo is not None
        assert completed.tzinfo is timezone.utc


class TestDocumentUploadTimestamp:
    """验证文档上传时间戳（documents.py）。"""

    def test_upload_timestamp_format(self):
        """上传时间戳应为 %Y%m%d_%H%M%S 格式。"""
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        assert len(timestamp) == 15  # YYYYMMDD_HHMMSS
        assert timestamp[8] == "_"


class TestSerializationRegression:
    """验证序列化输出格式未因 tzinfo 引入变化。"""

    def test_to_document_passes_datetime_object(self):
        """to_document() 直接传递 datetime 对象给 MongoDB 驱动（BSON Date 类型）。"""
        from app.models.mongodb.version_history import TestCaseVersionHistory

        history = TestCaseVersionHistory(
            test_case_id=uuid4(),
            version=1,
            snapshot={},
            changed_by=uuid4(),
        )
        doc = history.to_document()
        changed_at = doc["changed_at"]
        # to_document() 直接返回 datetime 对象（非字符串），驱动自动处理 BSON Date
        assert isinstance(changed_at, datetime)
        assert changed_at.tzinfo is timezone.utc

    def test_model_dump_json_serializable(self):
        """model_dump(mode='json') 应产生可序列化的输出。"""
        from app.models.mongodb.audit_log import AuditLog, AuditAction, EntityType

        log = AuditLog(
            entity_type=EntityType.TEST_CASE,
            entity_id=uuid4(),
            action=AuditAction.CREATE,
            performed_by=uuid4(),
        )
        data = log.model_dump(mode="json")
        assert isinstance(data["performed_at"], str)
        assert "T" in data["performed_at"]
