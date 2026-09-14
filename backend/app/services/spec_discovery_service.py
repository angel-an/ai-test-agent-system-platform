"""
OpenAPI/Swagger Spec 自动发现服务

接收 Swagger UI / Knife4j 页面地址，自动发现背后的 OpenAPI JSON/YAML 文档地址。
"""

from __future__ import annotations

import json
import re
from typing import Any
from urllib.parse import urljoin, urlparse

import httpx


# 标准候选地址（按优先级排序）
DEFAULT_CANDIDATE_PATHS = [
    "/v3/api-docs",
    "/v2/api-docs",
    "/openapi.json",
    "/swagger-resources",
]

# Swagger UI HTML 特征
SWAGGER_UI_MARKERS = [
    "swagger-ui",
    "swaggerui",
    "swagger ui",
    'id="swagger-ui"',
    "swagger-ui-bundle",
    "swagger-ui-standalone-preset",
]

# Knife4j HTML 特征
KNIFE4J_MARKERS = [
    "knife4j",
    "doc.html",
    "xknife",
]

# 登录页面特征
LOGIN_PAGE_MARKERS = [
    "登录",
    "login",
    "sign in",
    "用户名",
    "password",
    "请输入密码",
]


class SpecDiscoveryResult:
    """Spec 发现结果"""

    def __init__(
        self,
        source_type: str,
        ui_url: str | None = None,
        spec_url: str | None = None,
        format: str | None = None,
        version: str | None = None,
        endpoint_count: int = 0,
        requires_auth: bool = False,
        candidates: list[dict] | None = None,
        message: str | None = None,
    ):
        self.source_type = source_type
        self.ui_url = ui_url
        self.spec_url = spec_url
        self.format = format
        self.version = version
        self.endpoint_count = endpoint_count
        self.requires_auth = requires_auth
        self.candidates = candidates or []
        self.message = message

    def to_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {
            "source_type": self.source_type,
            "spec_url": self.spec_url,
            "format": self.format,
            "version": self.version,
            "endpoint_count": self.endpoint_count,
            "requires_auth": self.requires_auth,
            "candidates": self.candidates,
        }
        if self.ui_url:
            result["ui_url"] = self.ui_url
        if self.message:
            result["message"] = self.message
        return result


class SpecDiscoveryService:
    """OpenAPI/Swagger Spec 自动发现服务"""

    def __init__(self):
        self.page_timeout = 15.0
        self.candidate_timeout = 10.0

    async def discover(self, url: str, headers: dict[str, str] | None = None) -> SpecDiscoveryResult:
        """
        从给定 URL 自动发现 OpenAPI/Swagger Spec 地址

        Args:
            url: Swagger UI 页面地址或 OpenAPI JSON/YAML 地址
            headers: 可选的请求头（用于认证等）

        Returns:
            SpecDiscoveryResult: 发现结果
        """
        headers = headers or {}

        # 步骤 1：请求给定 URL
        page_content, page_error = await self._fetch_page(url, headers)

        if page_error:
            # URL 不可达
            return SpecDiscoveryResult(
                source_type="unknown",
                ui_url=url,
                message=f"无法访问该地址: {page_error}",
            )

        # 步骤 2：判断内容类型
        content_type = self._detect_content_type(page_content, url)

        if content_type == "openapi_json" or content_type == "openapi_yaml":
            # 直接是 OpenAPI 文档
            spec_data = self._parse_spec(page_content)
            if spec_data:
                return SpecDiscoveryResult(
                    source_type="direct_json" if content_type == "openapi_json" else "direct_yaml",
                    spec_url=url,
                    format="openapi" if "openapi" in spec_data else "swagger",
                    version=spec_data.get("openapi") or spec_data.get("swagger", "unknown"),
                    endpoint_count=len(spec_data.get("paths", {})),
                )

        # 步骤 3：检测是否为 HTML 页面
        if content_type == "html":
            return await self._discover_from_html(url, page_content, headers)

        # 步骤 4：未知内容类型
        return SpecDiscoveryResult(
            source_type="unknown",
            ui_url=url,
            message="无法识别内容类型，请提供 Swagger UI 页面地址或 OpenAPI JSON/YAML 地址",
        )

    async def _fetch_page(self, url: str, headers: dict[str, str]) -> tuple[str | None, str | None]:
        """获取页面内容"""
        try:
            async with httpx.AsyncClient(timeout=self.page_timeout, follow_redirects=True) as client:
                response = await client.get(url, headers=headers)
                response.raise_for_status()
                return response.text, None
        except httpx.HTTPStatusError as e:
            if e.response.status_code in (401, 403):
                return None, f"认证失败 (HTTP {e.response.status_code})，请检查认证信息"
            return None, f"HTTP {e.response.status_code} {e.response.reason_phrase}"
        except httpx.RequestError as e:
            return None, f"请求失败: {type(e).__name__}: {e}"

    def _detect_content_type(self, content: str, url: str) -> str:
        """检测内容类型"""
        stripped = content.lstrip()
        lower = stripped[:256].lower()
        url_lower = url.lower()

        # 检测 HTML
        if stripped.startswith("<!doctype") or stripped.startswith("<html") or stripped.startswith("<"):
            return "html"

        # 检测 JSON（OpenAPI/Swagger）
        if stripped.startswith("{") or stripped.startswith("["):
            # 大文档不能整体 json.loads（性能），先用关键字扫描判断是否为 OpenAPI/Swagger，
            # 再交给 _parse_spec 做完整解析。
            head = stripped[:8192]
            if '"openapi"' in head or '"swagger"' in head or '"paths"' in head:
                return "openapi_json"
            # 关键字不在前 8KB（字段顺序靠后）时，尝试解析前 4KB 兜底
            try:
                data = json.loads(stripped[:4096])
                if isinstance(data, dict) and ("openapi" in data or "swagger" in data):
                    return "openapi_json"
            except json.JSONDecodeError:
                pass
            return "json"

        # 检测 YAML（通过 URL 后缀或内容特征）
        if url_lower.endswith((".yaml", ".yml")) or "openapi" in lower or "swagger" in lower:
            return "openapi_yaml"

        return "unknown"

    def _parse_spec(self, content: str) -> dict[str, Any] | None:
        """解析 OpenAPI/Swagger 文档内容"""
        stripped = content.lstrip()

        # 尝试 JSON
        if stripped.startswith("{"):
            try:
                data = json.loads(content)
                if isinstance(data, dict) and ("openapi" in data or "swagger" in data) and "paths" in data:
                    return data
            except json.JSONDecodeError:
                pass

        # 尝试 YAML
        try:
            import yaml
            data = yaml.safe_load(content)
            if isinstance(data, dict) and ("openapi" in data or "swagger" in data) and "paths" in data:
                return data
        except ImportError:
            pass
        except Exception:
            pass

        return None

    async def _discover_from_html(
        self, ui_url: str, html_content: str, headers: dict[str, str]
    ) -> SpecDiscoveryResult:
        """从 HTML 页面发现 Spec 地址"""
        lower_html = html_content.lower()

        # 判断页面类型
        is_swagger_ui = any(marker in lower_html for marker in SWAGGER_UI_MARKERS)
        is_knife4j = any(marker in lower_html for marker in KNIFE4J_MARKERS)
        is_login_page = any(marker in lower_html for marker in LOGIN_PAGE_MARKERS)

        source_type = "swagger_ui" if is_swagger_ui else "knife4j" if is_knife4j else "html_page"

        # 如果是登录页面，直接返回提示
        if is_login_page and not (is_swagger_ui or is_knife4j):
            return SpecDiscoveryResult(
                source_type=source_type,
                ui_url=ui_url,
                requires_auth=True,
                message="页面需要登录，请提供认证信息后重试",
            )

        candidates: list[dict] = []
        discovered_urls: set[str] = set()

        # 步骤 1：从 HTML 中解析配置地址
        parsed_urls = self._extract_spec_urls_from_html(html_content, ui_url)
        for spec_url in parsed_urls:
            if spec_url in discovered_urls:
                continue
            discovered_urls.add(spec_url)
            valid, endpoint_count, error, spec_data = await self._validate_spec_url(spec_url, headers)
            candidates.append({
                "url": spec_url,
                "valid": valid,
                "endpoint_count": endpoint_count,
                "error": error,
                "spec_data": spec_data,
            })

        # 步骤 2：尝试标准候选地址
        # base_url 保留 context-path 前缀（如 /yundt-saas-gateway/api/imarketing），
        # 候选地址拼在该前缀下；同时保留根级候选作兜底。
        base_url = self._get_base_url(ui_url)
        root_url = self._get_root_url(ui_url)
        candidate_paths: list[str] = []
        for path in DEFAULT_CANDIDATE_PATHS:
            candidate_paths.append(base_url + path.lstrip("/"))  # context-path 下
            candidate_paths.append(root_url + path.lstrip("/"))  # 站点根级兜底
        for candidate_url in candidate_paths:
            if candidate_url in discovered_urls:
                continue
            discovered_urls.add(candidate_url)
            valid, endpoint_count, error, spec_data = await self._validate_spec_url(candidate_url, headers)
            candidates.append({
                "url": candidate_url,
                "valid": valid,
                "endpoint_count": endpoint_count,
                "error": error,
                "spec_data": spec_data,
            })

        # 找出第一个有效的候选
        valid_candidates = [c for c in candidates if c["valid"]]

        if valid_candidates:
            best = valid_candidates[0]
            spec_data = best.get("spec_data")
            return SpecDiscoveryResult(
                source_type=source_type,
                ui_url=ui_url,
                spec_url=best["url"],
                format="openapi" if spec_data and "openapi" in spec_data else "swagger" if spec_data and "swagger" in spec_data else "unknown",
                version=spec_data.get("openapi") or spec_data.get("swagger", "unknown") if spec_data else "unknown",
                endpoint_count=best["endpoint_count"],
                requires_auth=False,
                candidates=[{k: v for k, v in c.items() if k != "spec_data"} for c in candidates],
            )

        # 没有找到有效的 Spec 地址
        return SpecDiscoveryResult(
            source_type=source_type,
            ui_url=ui_url,
            requires_auth=False,
            candidates=[{k: v for k, v in c.items() if k != "spec_data"} for c in candidates],
            message="已访问 UI 页面，但未找到可访问的 OpenAPI 文档接口",
        )

    def _extract_spec_urls_from_html(self, html_content: str, base_url: str) -> list[str]:
        """从 HTML 内容中提取可能的 Spec URL"""
        urls: list[str] = []

        # 1. 匹配 Swagger UI 的 configUrl 配置
        # Swagger UI 3.x: configUrl: "..."
        config_url_pattern = re.compile(
            r'configUrl\s*[:=]\s*["\']([^"\']+)["\']',
            re.IGNORECASE,
        )
        for match in config_url_pattern.finditer(html_content):
            url = match.group(1)
            urls.append(urljoin(base_url, url))

        # 2. 匹配 Swagger UI 的 url 配置
        # Swagger UI: url: "..." 或 url: '...'
        url_pattern = re.compile(
            r'url\s*[:=]\s*["\']([^"\']+(?:\.json|\.yaml|api-docs|swagger)[^"\']*)["\']',
            re.IGNORECASE,
        )
        for match in url_pattern.finditer(html_content):
            url = match.group(1)
            # 过滤掉 hash 路由（如 #/home）
            if not url.startswith("#") and not url.startswith("/") or ".json" in url or ".yaml" in url or "api-docs" in url or "swagger" in url:
                urls.append(urljoin(base_url, url))

        # 3. 匹配 Knife4j 的全局配置
        # Knife4j 通常在 script 标签中有配置
        knife4j_pattern = re.compile(
            r'["\']?url["\']?\s*[:=]\s*["\']([^"\']+)["\']',
            re.IGNORECASE,
        )
        for match in knife4j_pattern.finditer(html_content):
            url = match.group(1)
            if "api-docs" in url.lower() or ".json" in url.lower() or ".yaml" in url.lower():
                urls.append(urljoin(base_url, url))

        # 4. 匹配 data-url 属性
        data_url_pattern = re.compile(
            r'data-url\s*=\s*["\']([^"\']+)["\']',
            re.IGNORECASE,
        )
        for match in data_url_pattern.finditer(html_content):
            url = match.group(1)
            urls.append(urljoin(base_url, url))

        # 去重并保持顺序
        seen: set[str] = set()
        unique_urls: list[str] = []
        for url in urls:
            if url not in seen:
                seen.add(url)
                unique_urls.append(url)

        return unique_urls

    async def _validate_spec_url(
        self, url: str, headers: dict[str, str]
    ) -> tuple[bool, int, str | None, dict[str, Any] | None]:
        """
        验证给定 URL 是否为有效的 OpenAPI/Swagger Spec

        Returns:
            (是否有效, 接口数量, 错误信息, 解析后的 Spec 数据)
        """
        try:
            async with httpx.AsyncClient(timeout=self.candidate_timeout, follow_redirects=True) as client:
                response = await client.get(url, headers=headers)

                if response.status_code != 200:
                    return False, 0, f"HTTP {response.status_code}", None

                content = response.text
                spec_data = self._parse_spec(content)

                if spec_data is None:
                    return False, 0, "返回内容不是有效的 OpenAPI/Swagger 文档", None

                endpoint_count = len(spec_data.get("paths", {}))
                return True, endpoint_count, None, spec_data

        except httpx.RequestError as e:
            return False, 0, f"请求失败: {type(e).__name__}", None
        except Exception as e:
            return False, 0, f"验证失败: {e}", None

    def _get_base_url(self, url: str) -> str:
        """
        从完整 URL 中提取带 context-path 的基础 URL（以 / 结尾）。

        保留 UI 页面所在目录作为 context-path 前缀（Spring Cloud Gateway /
        带 context-path 部署的常见场景），候选地址拼在该前缀下；
        若页面位于站点根路径，则退化为 scheme + netloc。
        """
        parsed = urlparse(url)
        origin = f"{parsed.scheme}://{parsed.netloc}"
        path = parsed.path or "/"
        # 去掉页面文件名（如 swagger-ui.html、doc.html、index.html），保留目录部分
        if path.endswith(".html"):
            path = path.rsplit("/", 1)[0] + "/"
        if not path.endswith("/"):
            path += "/"
        # 站点根路径时退化为 origin
        if path == "/":
            return origin + "/"
        return origin + path

    def _get_root_url(self, url: str) -> str:
        """站点根级基础 URL（scheme + netloc + /），用于候选地址兜底。"""
        parsed = urlparse(url)
        return f"{parsed.scheme}://{parsed.netloc}/"


# 全局服务实例
spec_discovery_service = SpecDiscoveryService()
