"""
统一导入响应结构测试

验证 UnifiedImportResult 的字段完整性和类型正确性。
"""

import pytest
from uuid import UUID

from app.schemas.api_endpoint import (
    EndpointSummary,
    ImportPreview,
    TagFolderSummary,
    UnifiedImportResult,
    UnifiedImportRequest,
)


class TestUnifiedImportResult:
    """测试统一导入响应结构"""

    def test_minimal_valid_result(self):
        """最小有效响应"""
        result = UnifiedImportResult(
            source_type="swagger_online",
            schema_title="Test API",
            schema_version="1.0.0",
            total_endpoints=2,
            total_tags=1,
            tag_folders=[
                TagFolderSummary(folder_id="abc", folder_name="Users", endpoint_count=2)
            ],
            endpoints=[
                EndpointSummary(
                    endpoint_id=None,
                    display_name="GET /users",
                    method="GET",
                    path="/users",
                    summary="List users",
                    tag_group="Users",
                    has_auth=False,
                    auth_type=None,
                    has_request_body=False,
                    has_path_params=False,
                    has_query_params=False,
                ),
            ],
        )
        assert result.source_type == "swagger_online"
        assert result.total_endpoints == 2
        assert len(result.warnings) == 0
        assert len(result.errors) == 0

    def test_result_with_warnings_and_errors(self):
        """包含告警和错误的响应"""
        result = UnifiedImportResult(
            source_type="manual_text",
            schema_title="API文档",
            schema_version="1.0.0",
            total_endpoints=0,
            total_tags=0,
            tag_folders=[],
            endpoints=[],
            warnings=["未识别到 base_url"],
            errors=["未找到任何接口"],
        )
        assert len(result.warnings) == 1
        assert len(result.errors) == 1
        # preview.warnings 默认空列表，与外层 warnings 独立
        assert result.preview.warnings == []

    def test_preview_with_warnings(self):
        """预览包含告警"""
        preview = ImportPreview(
            warnings=["某个接口缺少响应定义"],
        )
        assert preview.warnings == ["某个接口缺少响应定义"]

    def test_preview_auth_summary(self):
        """预览鉴权统计"""
        preview = ImportPreview(
            auth_summary={"bearer": 3, "none": 2},
            method_summary={"GET": 4, "POST": 1},
            path_prefixes=["/api/v1", "/api/v2"],
            warnings=["某个接口缺少响应定义"],
        )
        assert preview.auth_summary["bearer"] == 3
        assert preview.method_summary["GET"] == 4
        assert "/api/v1" in preview.path_prefixes

    def test_endpoint_summary_auth_detection(self):
        """端点摘要认证检测"""
        ep = EndpointSummary(
            endpoint_id="123",
            display_name="POST /api/login",
            method="POST",
            path="/api/login",
            has_auth=True,
            auth_type="bearer",
            has_request_body=True,
            has_path_params=False,
            has_query_params=True,
        )
        assert ep.has_auth is True
        assert ep.auth_type == "bearer"
        assert ep.has_request_body is True
        assert ep.has_query_params is True


class TestUnifiedImportRequest:
    """测试统一导入请求结构"""

    def test_url_request(self):
        """URL 请求"""
        req = UnifiedImportRequest(
            project_identifier="PR-1234",
            source_type="swagger_online",
            url="https://api.example.com/openapi.json",
        )
        assert req.get_input_description() == "URL: https://api.example.com/openapi.json"

    def test_file_request(self):
        """文件请求"""
        req = UnifiedImportRequest(
            project_identifier="PR-1234",
            source_type="swagger_offline",
            file_content={"openapi": "3.0.0"},
        )
        assert req.get_input_description() == "文件上传"

    def test_text_request(self):
        """文本请求"""
        req = UnifiedImportRequest(
            project_identifier="PR-1234",
            source_type="manual_text",
            text_content="POST /api/login\n请求体: {...}",
        )
        desc = req.get_input_description()
        assert "文本粘贴" in desc
        # 验证包含字符数
        assert "字符" in desc

    def test_invalid_source_type(self):
        """无效来源类型——当前 Schema 未限制枚举值，验证可正常创建"""
        # NOTE: source_type 当前为 str 类型，未做 Literal 限制
        # 这是有意设计：后端路由会做校验，Schema 层保持灵活
        req = UnifiedImportRequest(
            project_identifier="PR-1234",
            source_type="invalid_type",
        )
        assert req.source_type == "invalid_type"


class TestSourceTypeConstants:
    """测试来源类型常量"""

    def test_all_source_types(self):
        """所有支持的来源类型"""
        from app.schemas.api_endpoint import SourceType

        assert SourceType.SWAGGER_ONLINE == "swagger_online"
        assert SourceType.SWAGGER_OFFLINE == "swagger_offline"
        assert SourceType.APIFOX_ONLINE == "apifox_online"
        assert SourceType.APIFOX_OFFLINE == "apifox_offline"
        assert SourceType.MANUAL_TEXT == "manual_text"
        assert SourceType.MANUAL_FILE == "manual_file"
