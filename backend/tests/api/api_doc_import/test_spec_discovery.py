"""
Spec 自动发现服务测试

覆盖以下场景：
- 直接 OpenAPI JSON 地址
- 直接 YAML 地址
- Swagger UI HTML 页面
- Knife4j doc.html 页面
- 需要认证的页面
- 页面存在但文档地址不可访问
- 页面返回登录 HTML
- 候选地址全部无效
"""

import json
import pytest
from unittest.mock import AsyncMock, patch

from app.services.spec_discovery_service import (
    SpecDiscoveryService,
    SpecDiscoveryResult,
    DEFAULT_CANDIDATE_PATHS,
)


# _validate_spec_url 的返回值形状：(valid, endpoint_count, error, spec_data)。
# spec_data 是后续 rev 新增的第 4 项（解析后的 spec 原文），mock 必须一并提供，
# 否则 discover() 的 4 元组解包会抛 ValueError。
_FAKE_SPEC_DATA = {"openapi": "3.0.0", "info": {"title": "t", "version": "1"}, "paths": {}}


# =============================================================================
# Fixtures
# =============================================================================

@pytest.fixture
def service():
    return SpecDiscoveryService()


# 有效的 OpenAPI JSON 内容
OPENAPI_JSON_CONTENT = json.dumps({
    "openapi": "3.0.1",
    "info": {"title": "Test API", "version": "1.0.0"},
    "paths": {
        "/api/v1/users": {"get": {"tags": ["Users"], "summary": "List users"}},
        "/api/v1/users/{id}": {"get": {"tags": ["Users"], "summary": "Get user"}},
    },
})

# 有效的 Swagger 2.0 JSON 内容
SWAGGER_2_CONTENT = json.dumps({
    "swagger": "2.0",
    "info": {"title": "Test API", "version": "1.0.0"},
    "paths": {
        "/api/v1/items": {"get": {"tags": ["Items"], "summary": "List items"}},
    },
})

# Swagger UI HTML 页面（包含 configUrl）
SWAGGER_UI_HTML = """
<!DOCTYPE html>
<html>
<head>
    <title>Swagger UI</title>
</head>
<body>
    <div id="swagger-ui"></div>
    <script>
        window.onload = function() {
            const ui = SwaggerUIBundle({
                configUrl: "/v3/api-docs/swagger-config",
                url: "/v3/api-docs",
                dom_id: '#swagger-ui',
            });
        };
    </script>
</body>
</html>
"""

# Knife4j HTML 页面
KNIFE4J_HTML = """
<!DOCTYPE html>
<html>
<head>
    <title>Knife4j</title>
</head>
<body>
    <div id="knife4j"></div>
    <script>
        var config = {
            url: "/v3/api-docs",
            basePath: "/"
        };
    </script>
</body>
</html>
"""

# 登录页面
LOGIN_HTML = """
<!DOCTYPE html>
<html>
<head><title>登录</title></head>
<body>
    <form>
        <input type="text" name="username" placeholder="用户名">
        <input type="password" name="password" placeholder="请输入密码">
        <button type="submit">登录</button>
    </form>
</body>
</html>
"""

# 普通 HTML 页面（非 Swagger/Knife4j）
PLAIN_HTML = """
<!DOCTYPE html>
<html>
<head><title>Welcome</title></head>
<body><h1>Hello World</h1></body>
</html>
"""


# =============================================================================
# 测试：直接 OpenAPI JSON 地址
# =============================================================================

class TestDirectOpenAPI:
    """测试直接传入 OpenAPI JSON 地址"""

    @pytest.mark.asyncio
    async def test_direct_openapi_json(self, service):
        """直接传入有效的 OpenAPI 3.0 JSON 地址"""
        with patch.object(service, '_fetch_page', return_value=(OPENAPI_JSON_CONTENT, None)):
            result = await service.discover("https://api.example.com/openapi.json")

        assert result.source_type == "direct_json"
        assert result.spec_url == "https://api.example.com/openapi.json"
        assert result.format == "openapi"
        assert result.version == "3.0.1"
        assert result.endpoint_count == 2
        assert result.message is None

    @pytest.mark.asyncio
    async def test_direct_swagger_2_json(self, service):
        """直接传入有效的 Swagger 2.0 JSON 地址"""
        with patch.object(service, '_fetch_page', return_value=(SWAGGER_2_CONTENT, None)):
            result = await service.discover("https://api.example.com/swagger.json")

        assert result.source_type == "direct_json"
        assert result.format == "swagger"
        assert result.version == "2.0"
        assert result.endpoint_count == 1

    @pytest.mark.asyncio
    async def test_direct_yaml_url(self, service):
        """直接传入 YAML URL（通过后缀判断）"""
        yaml_content = """
openapi: 3.0.0
info:
  title: YAML API
  version: 1.0.0
paths:
  /health:
    get:
      summary: Health check
"""
        with patch.object(service, '_fetch_page', return_value=(yaml_content, None)):
            result = await service.discover("https://api.example.com/openapi.yaml")

        assert result.source_type == "direct_yaml"
        assert result.spec_url == "https://api.example.com/openapi.yaml"
        assert result.format == "openapi"


# =============================================================================
# 测试：Swagger UI / Knife4j 页面发现
# =============================================================================

class TestSwaggerUIDiscovery:
    """测试从 Swagger UI 页面发现 Spec"""

    @pytest.mark.asyncio
    async def test_swagger_ui_with_configurl(self, service):
        """Swagger UI 页面包含 configUrl 配置"""
        with patch.object(service, '_fetch_page', return_value=(SWAGGER_UI_HTML, None)):
            with patch.object(service, '_validate_spec_url',
                              return_value=(True, 42, None, _FAKE_SPEC_DATA)):
                result = await service.discover("https://api.example.com/swagger-ui.html")

        assert result.source_type == "swagger_ui"
        assert result.ui_url == "https://api.example.com/swagger-ui.html"
        assert result.spec_url is not None
        assert result.endpoint_count == 42
        assert result.message is None

    @pytest.mark.asyncio
    async def test_knife4j_page(self, service):
        """Knife4j 页面发现 Spec"""
        with patch.object(service, '_fetch_page', return_value=(KNIFE4J_HTML, None)):
            with patch.object(service, '_validate_spec_url',
                              return_value=(True, 126, None, _FAKE_SPEC_DATA)):
                result = await service.discover("https://api.example.com/doc.html#/home")

        assert result.source_type == "knife4j"
        assert result.ui_url == "https://api.example.com/doc.html#/home"
        assert result.spec_url is not None
        assert result.endpoint_count == 126

    @pytest.mark.asyncio
    async def test_swagger_ui_no_valid_candidates(self, service):
        """Swagger UI 页面存在但所有候选地址都无效"""
        with patch.object(service, '_fetch_page', return_value=(SWAGGER_UI_HTML, None)):
            with patch.object(service, '_validate_spec_url',
                              return_value=(False, 0, "HTTP 404", None)):
                result = await service.discover("https://api.example.com/swagger-ui.html")

        assert result.source_type == "swagger_ui"
        assert result.ui_url == "https://api.example.com/swagger-ui.html"
        assert result.spec_url is None
        assert result.message == "已访问 UI 页面，但未找到可访问的 OpenAPI 文档接口"
        assert len(result.candidates) > 0
        assert all(not c["valid"] for c in result.candidates)


# =============================================================================
# 测试：认证相关
# =============================================================================

class TestAuthentication:
    """测试认证场景"""

    @pytest.mark.asyncio
    async def test_auth_failure_401(self, service):
        """401 认证失败"""
        with patch.object(service, '_fetch_page', return_value=(None, "认证失败 (HTTP 401)，请检查认证信息")):
            result = await service.discover("https://api.example.com/doc.html")

        assert result.source_type == "unknown"
        assert "401" in result.message

    @pytest.mark.asyncio
    async def test_auth_failure_403(self, service):
        """403 禁止访问"""
        with patch.object(service, '_fetch_page', return_value=(None, "认证失败 (HTTP 403)，请检查认证信息")):
            result = await service.discover("https://api.example.com/doc.html")

        assert result.source_type == "unknown"
        assert "403" in result.message

    @pytest.mark.asyncio
    async def test_login_page_detected(self, service):
        """检测到登录页面"""
        with patch.object(service, '_fetch_page', return_value=(LOGIN_HTML, None)):
            result = await service.discover("https://api.example.com/login")

        assert result.source_type == "html_page"
        assert result.requires_auth is True
        assert "登录" in result.message

    @pytest.mark.asyncio
    async def test_headers_passed_to_fetch(self, service):
        """自定义 headers 被传递到请求中"""
        custom_headers = {"Authorization": "Bearer test-token"}

        with patch.object(service, '_fetch_page', return_value=(OPENAPI_JSON_CONTENT, None)) as mock_fetch:
            await service.discover("https://api.example.com/openapi.json", headers=custom_headers)

        mock_fetch.assert_called_once_with("https://api.example.com/openapi.json", custom_headers)


# =============================================================================
# 测试：URL 不可达
# =============================================================================

class TestUnreachableURL:
    """测试 URL 不可达场景"""

    @pytest.mark.asyncio
    async def test_url_unreachable(self, service):
        """URL 完全不可达"""
        with patch.object(service, '_fetch_page', return_value=(None, "请求失败: ConnectError")):
            result = await service.discover("https://invalid.example.com/openapi.json")

        assert result.source_type == "unknown"
        assert "无法访问" in result.message

    @pytest.mark.asyncio
    async def test_http_404(self, service):
        """HTTP 404"""
        with patch.object(service, '_fetch_page', return_value=(None, "HTTP 404 Not Found")):
            result = await service.discover("https://api.example.com/not-found")

        assert result.source_type == "unknown"
        assert "404" in result.message


# =============================================================================
# 测试：内容类型检测
# =============================================================================

class TestContentTypeDetection:
    """测试内容类型检测"""

    def test_detect_html(self, service):
        """检测 HTML"""
        assert service._detect_content_type("<!DOCTYPE html><html></html>", "http://example.com") == "html"
        assert service._detect_content_type("<html></html>", "http://example.com") == "html"
        assert service._detect_content_type("<div>test</div>", "http://example.com") == "html"

    def test_detect_openapi_json(self, service):
        """检测 OpenAPI JSON"""
        assert service._detect_content_type(OPENAPI_JSON_CONTENT, "http://example.com") == "openapi_json"

    def test_detect_swagger_json(self, service):
        """检测 Swagger 2.0 JSON"""
        assert service._detect_content_type(SWAGGER_2_CONTENT, "http://example.com") == "openapi_json"

    def test_detect_plain_json(self, service):
        """检测普通 JSON（非 OpenAPI）"""
        assert service._detect_content_type('{"key": "value"}', "http://example.com") == "json"

    def test_detect_yaml_by_extension(self, service):
        """通过扩展名检测 YAML"""
        assert service._detect_content_type("some content", "http://example.com/api.yaml") == "openapi_yaml"

    def test_detect_unknown(self, service):
        """检测未知类型"""
        assert service._detect_content_type("random text", "http://example.com") == "unknown"


# =============================================================================
# 测试：HTML 中解析 Spec URL
# =============================================================================

class TestExtractSpecURLs:
    """测试从 HTML 中提取 Spec URL"""

    def test_extract_configurl(self, service):
        """提取 configUrl"""
        html = '<script>configUrl: "/v3/api-docs"</script>'
        urls = service._extract_spec_urls_from_html(html, "https://api.example.com")
        assert "https://api.example.com/v3/api-docs" in urls

    def test_extract_url_with_json(self, service):
        """提取包含 .json 的 url"""
        html = '<script>url: "/api/openapi.json"</script>'
        urls = service._extract_spec_urls_from_html(html, "https://api.example.com")
        assert "https://api.example.com/api/openapi.json" in urls

    def test_extract_knife4j_url(self, service):
        """提取 Knife4j 配置中的 url"""
        html = '<script>var cfg = { url: "/v3/api-docs" };</script>'
        urls = service._extract_spec_urls_from_html(html, "https://api.example.com")
        assert "https://api.example.com/v3/api-docs" in urls

    def test_extract_data_url(self, service):
        """提取 data-url 属性"""
        html = '<div data-url="/swagger-resources"></div>'
        urls = service._extract_spec_urls_from_html(html, "https://api.example.com")
        assert "https://api.example.com/swagger-resources" in urls

    def test_filter_hash_routes(self, service):
        """过滤 hash 路由"""
        html = '<script>url: "#/home"</script>'
        urls = service._extract_spec_urls_from_html(html, "https://api.example.com")
        assert "https://api.example.com/#/home" not in urls

    def test_deduplication(self, service):
        """URL 去重"""
        html = '<script>url: "/v3/api-docs"; url: "/v3/api-docs";</script>'
        urls = service._extract_spec_urls_from_html(html, "https://api.example.com")
        assert urls.count("https://api.example.com/v3/api-docs") == 1


# =============================================================================
# 测试：Spec 验证
# =============================================================================

class TestSpecValidation:
    """测试 Spec URL 验证"""

    @pytest.mark.asyncio
    async def test_valid_spec(self, service):
        """验证有效的 Spec"""
        import httpx

        mock_response = AsyncMock()
        mock_response.status_code = 200
        mock_response.text = OPENAPI_JSON_CONTENT

        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)
        mock_client.get = AsyncMock(return_value=mock_response)

        with patch('httpx.AsyncClient', return_value=mock_client):
            valid, count, error, _spec = await service._validate_spec_url("https://api.example.com/v3/api-docs", {})

        assert valid is True
        assert count == 2
        assert error is None

    @pytest.mark.asyncio
    async def test_invalid_http_status(self, service):
        """验证 HTTP 非 200"""
        import httpx

        mock_response = AsyncMock()
        mock_response.status_code = 404

        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)
        mock_client.get = AsyncMock(return_value=mock_response)

        with patch('httpx.AsyncClient', return_value=mock_client):
            valid, count, error, _spec = await service._validate_spec_url("https://api.example.com/v3/api-docs", {})

        assert valid is False
        assert count == 0
        assert "404" in error

    @pytest.mark.asyncio
    async def test_invalid_content(self, service):
        """验证返回内容不是有效 Spec"""
        import httpx

        mock_response = AsyncMock()
        mock_response.status_code = 200
        mock_response.text = '{"not": "openapi"}'

        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)
        mock_client.get = AsyncMock(return_value=mock_response)

        with patch('httpx.AsyncClient', return_value=mock_client):
            valid, count, error, _spec = await service._validate_spec_url("https://api.example.com/v3/api-docs", {})

        assert valid is False
        assert "不是有效的" in error


# =============================================================================
# 测试：工具方法
# =============================================================================

class TestUtilityMethods:
    """测试工具方法"""

    def test_get_base_url(self, service):
        """提取带 context-path 的基础 URL（以 / 结尾）。

        设计意图：保留 UI 页面所在目录作为 context-path 前缀，以兼容
        Spring Cloud Gateway / 带 context-path 部署（如
        ``/yundt-saas-gateway/api/imarketing``）——候选地址需拼在该前缀下，
        否则会漏掉真实文档地址。页面位于站点根时退化为 origin。
        """
        # 目录路径保留为 context-path
        assert service._get_base_url("https://api.example.com/path") == "https://api.example.com/path/"
        # 页面文件名需剥离，并保留其所在目录
        assert service._get_base_url("https://api.example.com/gw/api/doc.html") == "https://api.example.com/gw/api/"
        # 站点根路径退化为 origin（统一带尾斜杠）
        assert service._get_base_url("http://localhost:8080/doc.html") == "http://localhost:8080/"
        assert service._get_base_url("http://localhost:8080/") == "http://localhost:8080/"

    def test_parse_spec_valid(self, service):
        """解析有效的 Spec"""
        result = service._parse_spec(OPENAPI_JSON_CONTENT)
        assert result is not None
        assert result["openapi"] == "3.0.1"
        assert "paths" in result

    def test_parse_spec_invalid(self, service):
        """解析无效的 Spec"""
        assert service._parse_spec('{"not": "openapi"}') is None
        assert service._parse_spec("not json") is None

    def test_result_to_dict(self, service):
        """结果转字典"""
        result = SpecDiscoveryResult(
            source_type="swagger_ui",
            ui_url="https://example.com/ui",
            spec_url="https://example.com/spec",
            format="openapi",
            version="3.0.0",
            endpoint_count=10,
            candidates=[{"url": "https://example.com/spec", "valid": True, "endpoint_count": 10}],
        )
        d = result.to_dict()
        assert d["source_type"] == "swagger_ui"
        assert d["spec_url"] == "https://example.com/spec"
        assert d["ui_url"] == "https://example.com/ui"
        assert d["endpoint_count"] == 10


# =============================================================================
# 测试：候选地址配置
# =============================================================================

class TestCandidatePaths:
    """测试标准候选地址"""

    def test_default_candidates(self):
        """默认候选地址列表"""
        assert "/v3/api-docs" in DEFAULT_CANDIDATE_PATHS
        assert "/v2/api-docs" in DEFAULT_CANDIDATE_PATHS
        assert "/openapi.json" in DEFAULT_CANDIDATE_PATHS
        assert "/swagger-resources" in DEFAULT_CANDIDATE_PATHS
