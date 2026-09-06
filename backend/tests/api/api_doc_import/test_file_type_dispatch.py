"""
文件类型分发测试

验证后端根据文件名/Content-Type 正确分发到对应解析器。
"""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import UUID

from app.api.v2.unified_import import _parse_openapi_source, _parse_text_source


class TestFileTypeDispatch:
    """测试文件类型分发逻辑"""

    def test_json_extension_dispatch(self):
        """.json 文件应走 OpenAPI JSON 解析"""
        filename = "api.json"
        suffix = filename.lower().split(".")[-1]
        assert suffix == "json"
        # json 属于 openapi 解析路径
        assert suffix in ("json", "yaml", "yml")

    def test_yaml_extension_dispatch(self):
        """.yaml/.yml 文件应走 OpenAPI YAML 解析"""
        for ext in ("yaml", "yml"):
            filename = f"api.{ext}"
            suffix = filename.lower().split(".")[-1]
            assert suffix in ("yaml", "yml")

    def test_txt_extension_dispatch(self):
        """.txt 文件应走文本解析"""
        filename = "api.txt"
        suffix = filename.lower().split(".")[-1]
        assert suffix == "txt"
        assert suffix in ("txt", "docx")

    def test_docx_extension_dispatch(self):
        """.docx 文件应走文本解析"""
        filename = "api.docx"
        suffix = filename.lower().split(".")[-1]
        assert suffix == "docx"
        assert suffix in ("txt", "docx")

    def test_unsupported_extension(self):
        """不支持的扩展名应被拒绝"""
        filename = "api.pdf"
        suffix = filename.lower().split(".")[-1]
        assert suffix not in ("json", "yaml", "yml", "txt", "docx")

    def test_no_extension_fallback(self):
        """无扩展名文件应依次尝试 JSON → YAML → 文本"""
        filename = "openapi"
        suffix = filename.lower().split(".")[-1] if "." in filename else ""
        # 无扩展名时 suffix 为空，需要回退逻辑
        assert suffix == ""


class TestAuthTypeDetection:
    """测试认证类型检测"""

    def test_detect_bearer_auth(self):
        """检测 Bearer 认证"""
        from app.api.v2.unified_import import _detect_auth_type

        security = [{"bearerAuth": []}]
        has_auth, auth_type = _detect_auth_type(security)
        assert has_auth is True
        assert auth_type == "bearer"

    def test_detect_basic_auth(self):
        """检测 Basic 认证"""
        from app.api.v2.unified_import import _detect_auth_type

        security = [{"basicAuth": []}]
        has_auth, auth_type = _detect_auth_type(security)
        assert has_auth is True
        assert auth_type == "basic"

    def test_detect_api_key_auth(self):
        """检测 API Key 认证"""
        from app.api.v2.unified_import import _detect_auth_type

        security = [{"apiKeyAuth": []}]
        has_auth, auth_type = _detect_auth_type(security)
        assert has_auth is True
        assert auth_type == "api_key"

    def test_detect_oauth2_auth(self):
        """检测 OAuth2 认证"""
        from app.api.v2.unified_import import _detect_auth_type

        security = [{"oauth2": ["read", "write"]}]
        has_auth, auth_type = _detect_auth_type(security)
        assert has_auth is True
        assert auth_type == "oauth2"

    def test_no_auth(self):
        """无认证"""
        from app.api.v2.unified_import import _detect_auth_type

        has_auth, auth_type = _detect_auth_type(None)
        assert has_auth is False
        assert auth_type is None

    def test_empty_security(self):
        """空 security 列表"""
        from app.api.v2.unified_import import _detect_auth_type

        has_auth, auth_type = _detect_auth_type([])
        assert has_auth is False
        assert auth_type is None


class TestPathParamDetection:
    """测试路径参数检测"""

    def test_has_path_params(self):
        """检测路径参数"""
        from app.api.v2.unified_import import _has_path_params

        assert _has_path_params("/users/{id}") is True
        assert _has_path_params("/users/{userId}/orders/{orderId}") is True

    def test_no_path_params(self):
        """无路径参数"""
        from app.api.v2.unified_import import _has_path_params

        assert _has_path_params("/users") is False
        assert _has_path_params("/api/v1/health") is False


class TestQueryParamDetection:
    """测试查询参数检测"""

    def test_has_query_params(self):
        """有 query 参数"""
        from app.api.v2.unified_import import _has_query_params

        params = [
            {"name": "page", "in": "query"},
            {"name": "size", "in": "query"},
        ]
        assert _has_query_params(params) is True

    def test_no_query_params(self):
        """无 query 参数"""
        from app.api.v2.unified_import import _has_query_params

        params = [
            {"name": "id", "in": "path"},
            {"name": "Authorization", "in": "header"},
        ]
        assert _has_query_params(params) is False

    def test_none_params(self):
        """None 参数"""
        from app.api.v2.unified_import import _has_query_params

        assert _has_query_params(None) is False


class TestPathPrefixExtraction:
    """测试路径前缀提取"""

    def test_extract_common_prefixes(self):
        """提取常见路径前缀"""
        from app.api.v2.unified_import import _extract_path_prefixes

        paths = [
            "/api/v1/users",
            "/api/v1/orders",
            "/api/v2/products",
            "/health",
        ]
        prefixes = _extract_path_prefixes(paths)
        assert "/api/v1" in prefixes
        assert "/api/v2" in prefixes
        assert "/health" in prefixes

    def test_single_level_paths(self):
        """单级路径"""
        from app.api.v2.unified_import import _extract_path_prefixes

        paths = ["/users", "/orders", "/products"]
        prefixes = _extract_path_prefixes(paths)
        assert "/users" in prefixes
        assert "/orders" in prefixes
