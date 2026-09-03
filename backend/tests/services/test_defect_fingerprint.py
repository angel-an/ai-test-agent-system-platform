"""
缺陷指纹服务单元测试

测试指纹生成、一致性
"""

import pytest

from app.services.defect_fingerprint import DefectFingerprintService


class TestDefectFingerprintService:
    """缺陷指纹服务测试"""

    def test_generate_basic(self):
        """测试基本指纹生成"""
        fp = DefectFingerprintService.generate(
            source_project_key="PR-2",
            method="GET",
            url="/api/v1/users",
            error_type="status_mismatch",
            failure_summary="Expected 200 but got 500",
        )
        assert len(fp) == 64  # SHA-256 长度为 64
        assert isinstance(fp, str)

    def test_generate_consistency(self):
        """测试相同输入生成相同指纹"""
        fp1 = DefectFingerprintService.generate(
            source_project_key="PR-2",
            method="GET",
            url="/api/v1/users",
            error_type="status_mismatch",
            failure_summary="Expected 200 but got 500",
        )
        fp2 = DefectFingerprintService.generate(
            source_project_key="PR-2",
            method="GET",
            url="/api/v1/users",
            error_type="status_mismatch",
            failure_summary="Expected 200 but got 500",
        )
        assert fp1 == fp2

    def test_generate_different_inputs(self):
        """测试不同输入生成不同指纹"""
        fp1 = DefectFingerprintService.generate(
            source_project_key="PR-2",
            method="GET",
            url="/api/v1/users",
            error_type="status_mismatch",
            failure_summary="Expected 200 but got 500",
        )
        fp2 = DefectFingerprintService.generate(
            source_project_key="PR-2",
            method="POST",
            url="/api/v1/users",
            error_type="status_mismatch",
            failure_summary="Expected 200 but got 500",
        )
        assert fp1 != fp2

    def test_normalize_url_remove_domain(self):
        """测试 URL 规范化：去除域名"""
        normalized = DefectFingerprintService._normalize_url(
            "https://api.example.com/api/v1/users"
        )
        assert normalized == "/api/v1/users"

    def test_normalize_url_remove_query(self):
        """测试 URL 规范化：去除查询参数"""
        normalized = DefectFingerprintService._normalize_url(
            "/api/v1/users?page=1&size=10"
        )
        assert normalized == "/api/v1/users"

    def test_normalize_url_replace_id(self):
        """测试 URL 规范化：替换动态 ID"""
        normalized = DefectFingerprintService._normalize_url(
            "/api/v1/users/12345"
        )
        assert normalized == "/api/v1/users/{id}"

    def test_normalize_url_replace_uuid(self):
        """测试 URL 规范化：替换 UUID"""
        normalized = DefectFingerprintService._normalize_url(
            "/api/v1/users/550e8400-e29b-41d4-a716-446655440000"
        )
        assert normalized == "/api/v1/users/{uuid}"

    def test_normalize_summary_remove_timestamp(self):
        """测试摘要规范化：去除时间戳"""
        normalized = DefectFingerprintService._normalize_summary(
            "Error at 2026-08-26 10:30:00"
        )
        assert "2026" not in normalized
        assert "{timestamp}" in normalized

    def test_normalize_summary_remove_uuid(self):
        """测试摘要规范化：去除 UUID"""
        normalized = DefectFingerprintService._normalize_summary(
            "Error with id 550e8400-e29b-41d4-a716-446655440000"
        )
        assert "550e8400" not in normalized

    def test_normalize_summary_truncate(self):
        """测试摘要规范化：截断过长内容"""
        long_summary = "a" * 300
        normalized = DefectFingerprintService._normalize_summary(long_summary)
        assert len(normalized) <= 200

    def test_generate_from_api_result(self):
        """测试从 API 结果生成指纹"""
        fp = DefectFingerprintService.generate_from_api_result(
            source_project_key="PR-2",
            method="GET",
            endpoint="/api/v1/users",
            error_type="server_error",
            failure_summary="Server returned 500",
        )
        assert len(fp) == 64
