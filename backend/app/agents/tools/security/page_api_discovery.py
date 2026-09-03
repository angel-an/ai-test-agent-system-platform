"""
页面驱动 API 接口发现工具（优化版）

通过 Playwright 浏览器访问目标页面，拦截网络请求，
自动发现前端页面调用的所有 API 接口。

优化点：
1. 浏览器单例复用（避免重复启动）
2. 并发页面访问
3. 智能等待策略
4. 资源拦截减少加载时间
5. 缓存机制
"""

import json
import asyncio
import sys
from typing import Optional, List, Dict, Set
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse

from langchain_core.tools import tool
import httpx

from app.config.settings import settings

# Playwright 可选导入（避免启动失败）
try:
    from playwright.async_api import async_playwright
    PLAYWRIGHT_AVAILABLE = True
except ImportError:
    PLAYWRIGHT_AVAILABLE = False
    async_playwright = None

SECURITY_WORKSPACE = Path(settings.security_workspace_root).resolve()

# 全局浏览器实例缓存（进程级）
_browser_instance = None
_browser_lock = asyncio.Lock()

# 静态资源拦截规则
BLOCKED_RESOURCE_TYPES = {
    'image', 'media', 'font', 'stylesheet',
}

BLOCKED_URL_PATTERNS = [
    '.js', '.css', '.png', '.jpg', '.jpeg', '.gif', '.svg', '.ico',
    '.woff', '.woff2', '.ttf', '.eot', '.mp3', '.mp4', '.webm',
    'google-analytics', 'googletagmanager', 'doubleclick',
    'fonts.googleapis', 'fonts.gstatic',
    'cdn.bootcdn', 'cdnjs.cloudflare',
    'api.map', 'webapi.amap',
]


def _set_windows_proactor_loop():
    """
    Windows 上 Playwright 需要 ProactorEventLoop（支持子进程）。
    如果当前是 SelectorEventLoop，则临时切换到 ProactorEventLoop。
    """
    if sys.platform != "win32":
        return None
    current_loop = asyncio.get_event_loop()
    # 检查是否是 SelectorEventLoop（不支持子进程）
    if hasattr(asyncio, 'WindowsSelectorEventLoop') and isinstance(current_loop, asyncio.WindowsSelectorEventLoop):
        # 关闭当前 loop
        try:
            current_loop.close()
        except Exception:
            pass
        # 设置 ProactorEventLoopPolicy
        asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())
        # 创建新的 ProactorEventLoop
        new_loop = asyncio.ProactorEventLoop()
        asyncio.set_event_loop(new_loop)
        return new_loop
    return None


def _restore_selector_loop():
    """恢复 SelectorEventLoop（psycopg 兼容）"""
    if sys.platform != "win32":
        return
    try:
        current_loop = asyncio.get_event_loop()
        current_loop.close()
    except Exception:
        pass
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    new_loop = asyncio.SelectorEventLoop()
    asyncio.set_event_loop(new_loop)


async def _get_browser(playwright):
    """获取或创建浏览器实例（单例模式）"""
    global _browser_instance

    async with _browser_lock:
        if _browser_instance is None or _browser_instance.is_connected() is False:
            _browser_instance = await playwright.chromium.launch(
                headless=True,
                args=[
                    '--no-sandbox',
                    '--disable-setuid-sandbox',
                    '--disable-dev-shm-usage',
                    '--disable-accelerated-2d-canvas',
                    '--disable-gpu',
                    '--disable-extensions',
                    '--disable-background-networking',
                    '--disable-background-timer-throttling',
                    '--disable-backgrounding-occluded-windows',
                    '--disable-breakpad',
                    '--disable-component-extensions-with-background-pages',
                    '--disable-features=TranslateUI,BlinkGenPropertyTrees',
                    '--disable-ipc-flooding-protection',
                    '--disable-renderer-backgrounding',
                    '--enable-features=NetworkService,NetworkServiceInProcess',
                    '--force-color-profile=srgb',
                    '--metrics-recording-only',
                    '--mute-audio',
                ]
            )
        return _browser_instance


async def _close_browser():
    """关闭浏览器实例"""
    global _browser_instance
    async with _browser_lock:
        if _browser_instance and _browser_instance.is_connected():
            await _browser_instance.close()
            _browser_instance = None


def _is_api_request(url: str, resource_type: str) -> bool:
    """判断是否为 API 请求"""
    # 接受 xhr, fetch 和其他类型的请求（现代框架可能使用其他 resource_type）
    if resource_type not in ('xhr', 'fetch', 'other', 'document'):
        return False

    # 快速排除静态资源
    url_lower = url.lower()
    for pattern in BLOCKED_URL_PATTERNS:
        if pattern in url_lower:
            return False

    # 排除常见非 API 路径
    non_api_paths = ['/static/', '/assets/', '/dist/', '/build/', '/public/']
    for path in non_api_paths:
        if path in url_lower:
            return False

    # 排除纯静态文件请求
    static_extensions = ['.html', '.htm', '.js', '.css', '.png', '.jpg', '.jpeg',
                         '.gif', '.svg', '.ico', '.woff', '.woff2', '.ttf', '.eot']
    parsed = urlparse(url)
    path_lower = parsed.path.lower()
    for ext in static_extensions:
        if path_lower.endswith(ext):
            return False

    # 只接受同域名或 API 子域名的请求
    # 这里简化处理，只要路径不像静态资源就认为是 API
    return True


async def _try_login(page, username: str, password: str) -> bool:
    """尝试自动登录"""
    if not username or not password:
        return False

    try:
        # 快速检测登录表单 - 使用更精确的选择器
        login_form_selectors = [
            # Element UI / Vue 常见登录表单
            ("form:has(input[type='password'])", ".el-button--primary"),
            # 通用登录表单
            ("form:has(input[type='password'])", "button[type='submit']"),
            # Ant Design
            (".ant-form:has(input[type='password'])", "button.ant-btn-primary"),
            # 包含登录文本的表单
            ("form", "button:has-text('登录')"),
            ("form", "button:has-text('登入')"),
            ("form", "button:has-text('Login')"),
            ("form", "button:has-text('Sign in')"),
        ]

        for form_selector, submit_selector in login_form_selectors:
            try:
                # 检查是否存在密码输入框
                password_input = await page.query_selector(f"{form_selector} input[type='password']")
                if not password_input:
                    continue

                # 查找用户名输入框
                username_input = await page.query_selector(
                    f"{form_selector} input[type='text'], {form_selector} input:not([type])"
                )
                if not username_input:
                    # 尝试其他用户名选择器
                    username_input = await page.query_selector(
                        "input[placeholder*='用户' i], input[placeholder*='账号' i], input[name='username'], input#username"
                    )

                if not username_input:
                    continue

                # 查找提交按钮
                submit_button = await page.query_selector(f"{form_selector} {submit_selector}")

                # 填写表单
                await username_input.fill(username)
                await password_input.fill(password)

                # 提交
                if submit_button:
                    await submit_button.click()
                else:
                    await password_input.press("Enter")

                # 等待导航或网络空闲
                try:
                    await page.wait_for_load_state("networkidle", timeout=10000)
                except:
                    pass

                await asyncio.sleep(2)
                return True

            except Exception:
                continue

    except Exception:
        pass

    return False


async def _extract_links(page, target_domain: str) -> List[str]:
    """提取页面中的同域名链接"""
    try:
        links = await page.evaluate("""
            () => {
                const links = [];
                const elements = document.querySelectorAll('a[href], [router-link], [ng-click], [data-href]');
                elements.forEach(el => {
                    const href = el.href || el.getAttribute('href') || el.getAttribute('router-link') || el.getAttribute('data-href');
                    if (href && !href.startsWith('javascript:') && !href.startsWith('mailto:') && !href.startsWith('#')) {
                        links.push(href);
                    }
                });
                // 也检查前端路由（SPA 应用）
                if (window.location.hash) {
                    links.push(window.location.href);
                }
                return [...new Set(links)];
            }
        """)

        # 过滤同域名链接
        result = []
        for link in links:
            try:
                if urlparse(link).netloc == target_domain:
                    result.append(link)
            except:
                continue

        return result

    except Exception:
        return []


async def _visit_page(
    page,
    url: str,
    api_endpoints: Dict[str, dict],
    visited_pages: Set[str],
    timeout: int = 30000
) -> bool:
    """访问单个页面并收集 API"""
    if url in visited_pages:
        return False

    try:
        await page.goto(url, timeout=timeout, wait_until="domcontentloaded")

        # 等待关键元素出现（而不是等待 networkidle）
        try:
            await page.wait_for_selector("body", timeout=5000)
        except:
            pass

        # 给 SPA 应用一点时间渲染
        await asyncio.sleep(1)

        visited_pages.add(url)
        return True

    except Exception:
        return False


async def _discover_apis_via_httpx(target_url: str) -> Dict[str, dict]:
    """
    使用 httpx 探测常见 API 路径作为 fallback。
    当 Playwright 失败或无法发现接口时使用。
    """
    discovered = {}
    parsed = urlparse(target_url)
    base = f"{parsed.scheme}://{parsed.netloc}"

    # 常见 API 路径列表
    common_api_paths = [
        "/api", "/api/v1", "/api/v2", "/api/v3",
        "/rest", "/rest/v1", "/rest/v2",
        "/graphql", "/gql",
        "/swagger", "/swagger-ui", "/swagger.json", "/api-docs",
        "/openapi.json", "/api/swagger.json",
        "/health", "/healthz", "/actuator/health",
        "/login", "/auth/login", "/api/auth/login",
        "/user", "/users", "/api/users",
        "/admin", "/api/admin",
        "/system", "/api/system",
        "/config", "/api/config",
        "/upload", "/api/upload",
        "/file", "/api/file",
        "/order", "/api/order",
        "/product", "/api/product",
    ]

    # 模拟浏览器的请求头
    browser_headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
        "Accept-Encoding": "gzip, deflate, br",
        "Connection": "keep-alive",
        "Referer": target_url,
    }

    async with httpx.AsyncClient(follow_redirects=True, timeout=10, headers=browser_headers) as client:
        for path in common_api_paths:
            url = f"{base}{path}"
            try:
                response = await client.get(url)
                # 如果返回 200/401/403/405，说明端点存在
                if response.status_code in [200, 201, 204, 401, 403, 405, 500]:
                    key = f"GET {url}"
                    discovered[key] = {
                        "url": url,
                        "method": "GET",
                        "headers": dict(response.headers),
                        "resource_type": "httpx_probe",
                        "status_code": response.status_code,
                    }
            except Exception:
                continue

    return discovered


@tool
async def discover_apis_from_page(
    target_url: str,
    username: Optional[str] = None,
    password: Optional[str] = None,
    max_pages: int = 10,
    timeout: int = 300,
    output_file: Optional[str] = None,
) -> str:
    """
    通过浏览器访问页面并拦截网络请求，自动发现 API 端点。

    如果 Playwright 浏览器方式失败，会自动回退到 httpx 探测常见 API 路径。

    Args:
        target_url: 目标前端页面 URL
        username: 登录用户名（可选）
        password: 登录密码（可选）
        max_pages: 最大遍历页面数，默认 10
        timeout: 超时时间（秒），默认 300
        output_file: 输出文件路径（可选）

    Returns:
        JSON 格式的发现的 API 端点列表

    Example:
        >>> result = await discover_apis_from_page(
        ...     target_url="https://console.example.com/#/login",
        ...     username="admin",
        ...     password="admin123"
        ... )
    """
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_file = output_file or f"discovered_apis_{timestamp}.json"
    out_path = SECURITY_WORKSPACE / out_file

    api_endpoints: Dict[str, dict] = {}
    visited_pages: Set[str] = set()
    target_domain = urlparse(target_url).netloc
    errors = []

    # 方法 1: 使用 Playwright 浏览器拦截
    if PLAYWRIGHT_AVAILABLE:
        playwright = None
        context = None
        # Windows 上临时切换到 ProactorEventLoop（Playwright 需要子进程支持）
        _old_loop = None
        try:
            if sys.platform == "win32":
                _old_loop = _set_windows_proactor_loop()
        except Exception:
            pass

        try:
            playwright = await async_playwright().start()
            browser = await _get_browser(playwright)

            context = await browser.new_context(
                viewport={"width": 1280, "height": 720},
                user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            )

            # 拦截静态资源加速加载
            await context.route("**/*", lambda route, request:
                route.abort() if request.resource_type in BLOCKED_RESOURCE_TYPES
                else route.continue_()
            )

            page = await context.new_page()

            # 设置请求拦截
            def handle_request(request):
                if _is_api_request(request.url, request.resource_type):
                    key = f"{request.method} {request.url}"
                    if key not in api_endpoints:
                        api_endpoints[key] = {
                            "url": request.url,
                            "method": request.method,
                            "headers": dict(request.headers),
                            "resource_type": request.resource_type,
                            "post_data": request.post_data,
                        }

            page.on("request", handle_request)

            # 访问目标页面
            start_time = datetime.now()
            success = await _visit_page(page, target_url, api_endpoints, visited_pages)

            if success:
                # 自动登录
                if username and password:
                    logged_in = await _try_login(page, username, password)
                    if logged_in:
                        await _visit_page(page, target_url, api_endpoints, visited_pages)

                # 提取页面链接并访问
                links = await _extract_links(page, target_domain)
                links_to_visit = [link for link in links if link not in visited_pages][:max_pages - 1]

                if links_to_visit:
                    semaphore = asyncio.Semaphore(3)

                    async def visit_with_limit(link):
                        async with semaphore:
                            new_page = await context.new_page()
                            new_page.on("request", handle_request)
                            await new_page.route("**/*", lambda route, request:
                                route.abort() if request.resource_type in BLOCKED_RESOURCE_TYPES
                                else route.continue_()
                            )
                            await _visit_page(new_page, link, api_endpoints, visited_pages)
                            await new_page.close()

                    await asyncio.gather(*[
                        visit_with_limit(link) for link in links_to_visit
                    ], return_exceptions=True)
            else:
                errors.append("页面访问失败")

        except Exception as e:
            errors.append(f"Playwright 执行失败: {str(e)}")
        finally:
            try:
                if context:
                    await context.close()
            except Exception:
                pass
            try:
                if playwright:
                    await playwright.stop()
            except Exception:
                pass
            # 恢复 SelectorEventLoop
            if _old_loop:
                try:
                    _restore_selector_loop()
                except Exception:
                    pass

    # 方法 2: 如果浏览器方式未发现接口，使用 httpx 探测
    if not api_endpoints:
        errors.append("浏览器方式未发现接口，尝试 httpx 探测")
        try:
            httpx_apis = await _discover_apis_via_httpx(target_url)
            api_endpoints.update(httpx_apis)
        except Exception as e:
            errors.append(f"httpx 探测失败: {str(e)}")

    # 生成结果
    elapsed = (datetime.now() - start_time).total_seconds() if 'start_time' in locals() else 0

    openapi_paths = {}
    for api in api_endpoints.values():
        parsed_url = urlparse(api["url"])
        path = parsed_url.path
        method = api["method"].lower()
        if path not in openapi_paths:
            openapi_paths[path] = {}
        if method not in openapi_paths[path]:
            openapi_paths[path][method] = {
                "summary": f"Discovered {method.upper()} endpoint",
                "operationId": f"discovered_{method}_{path.replace('/', '_').strip('_')}",
            }

    result = {
        "success": True,
        "target_url": target_url,
        "total_discovered": len(api_endpoints),
        "total_pages_visited": len(visited_pages),
        "pages_visited": sorted(list(visited_pages)),
        "api_endpoints": list(api_endpoints.values()),
        "openapi_paths": openapi_paths,
        "elapsed_seconds": elapsed,
        "errors": errors if errors else None,
        "timestamp": datetime.now().isoformat(),
    }

    out_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    return json.dumps(result, ensure_ascii=False, indent=2)
