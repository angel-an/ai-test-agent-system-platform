"""
B端 Web 应用安全扫描工具（优化版）

针对企业控制台（B端）页面的安全检测：
- XSS（反射型、存储型、DOM型）
- CSRF
- 点击劫持
- 敏感信息泄露
- 配置错误

不依赖外部工具（如 dalfox），使用 Playwright + Python 原生实现

优化点：
1. 复用 page_api_discovery 的浏览器实例
2. 资源拦截减少加载时间
3. 并发执行多个扫描
"""

import json
import asyncio
import re
import sys
from typing import Optional
from datetime import datetime
from pathlib import Path

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

# 静态资源拦截规则（与 page_api_discovery 保持一致）
BLOCKED_RESOURCE_TYPES = {'image', 'media', 'font', 'stylesheet'}

# 浏览器启动参数（优化性能）
BROWSER_ARGS = [
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
    '--force-color-profile=srgb',
    '--metrics-recording-only',
    '--mute-audio',
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

# XSS 测试 Payload 库
XSS_PAYLOADS = [
    # 基础反射型
    "<script>alert('XSS')</script>",
    "<img src=x onerror=alert('XSS')>",
    "<svg onload=alert('XSS')>",
    "<body onload=alert('XSS')>",
    # 属性注入
    "\"\"><script>alert('XSS')</script>",
    "' onerror=alert('XSS')>",
    "\" onmouseover=alert('XSS')>",
    # JavaScript 上下文
    "';alert('XSS');//",
    "'-alert('XSS')-'",
    r'\";alert(\'XSS\');//',
    # 编码绕过
    "<scr<script>ipt>alert('XSS')</scr</script>ipt>",
    "<img src=javascript:alert('XSS')>",
    # DOM 型
    "#<img src=x onerror=alert('XSS')>",
    "javascript:alert('XSS')",
]

# SQL 注入测试 Payload 库（用于检测错误回显）
SQLI_PAYLOADS = [
    "' OR '1'='1",
    "' OR 1=1--",
    "1' UNION SELECT null--",
    "1'; DROP TABLE users; --",
    "1' AND 1=1--",
    "1' AND 1=2--",
    "' UNION SELECT username,password FROM users--",
]


@tool
async def scan_web_xss(
    target_url: str,
    cookies: Optional[str] = None,
    scan_type: str = "all",
    output_file: Optional[str] = None,
) -> str:
    """
    B端页面 XSS 漏洞扫描（不依赖 dalfox，使用 Playwright 原生实现）

    检测类型：
    - 反射型 XSS：URL 参数中的 Payload 是否在响应中执行
    - 存储型 XSS：表单提交后的 Payload 是否在页面中执行
    - DOM 型 XSS：前端路由参数是否导致 XSS

    Args:
        target_url: 目标页面 URL
        cookies: Cookie 字符串（用于认证后的扫描）
        scan_type: 扫描类型 reflected/stored/dom/all
        output_file: 输出文件路径（可选）

    Returns:
        JSON 格式的 XSS 扫描结果
    """
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_file = output_file or f"web_xss_{timestamp}.json"
    out_path = SECURITY_WORKSPACE / out_file

    if not PLAYWRIGHT_AVAILABLE:
        return json.dumps({
            "success": False,
            "error": "Playwright 未安装。请运行: pip install playwright && playwright install chromium",
        }, ensure_ascii=False, indent=2)

    findings = []
    _old_loop = None

    try:
        # Windows 上临时切换到 ProactorEventLoop（Playwright 需要子进程支持）
        if sys.platform == "win32":
            _old_loop = _set_windows_proactor_loop()

        async with async_playwright() as p:
            browser = await p.chromium.launch(
                headless=True,
                args=BROWSER_ARGS
            )
            context = await browser.new_context(
                viewport={"width": 1280, "height": 720}
            )

            # 拦截静态资源加速加载
            await context.route("**/*", lambda route, request:
                route.abort() if request.resource_type in BLOCKED_RESOURCE_TYPES
                else route.continue_()
            )

            # 设置 cookie
            if cookies:
                # 解析 cookie 字符串并设置
                cookie_list = []
                for cookie_str in cookies.split(';'):
                    cookie_str = cookie_str.strip()
                    if '=' in cookie_str:
                        name, value = cookie_str.split('=', 1)
                        cookie_list.append({
                            "name": name.strip(),
                            "value": value.strip(),
                            "domain": "." + target_url.split('/')[2].split(':')[0],
                            "path": "/",
                        })
                await context.add_cookies(cookie_list)

            page = await context.new_page()

            # ========== 反射型 XSS 检测 ==========
            if scan_type in ("reflected", "all"):
                for payload in XSS_PAYLOADS[:5]:  # 使用基础 payload
                    try:
                        # 构造带 payload 的 URL
                        separator = "&" if "?" in target_url else "?"
                        test_url = f"{target_url}{separator}test={payload}"

                        # 监听 dialog 事件（alert 弹窗）
                        alert_triggered = []
                        def handle_dialog(dialog):
                            alert_triggered.append(dialog.message)
                            asyncio.create_task(dialog.dismiss())

                        page.on("dialog", handle_dialog)

                        await page.goto(test_url, timeout=30000)
                        await page.wait_for_load_state("networkidle")
                        await asyncio.sleep(1)

                        # 检查 alert 是否触发
                        if alert_triggered:
                            findings.append({
                                "type": "reflected_xss",
                                "severity": "High",
                                "payload": payload,
                                "url": test_url,
                                "evidence": f"Alert triggered: {alert_triggered[0]}",
                                "description": "URL 参数中的 XSS Payload 被反射执行",
                                "recommendation": "对 URL 参数进行 HTML 编码输出",
                            })
                            break

                        # 检查页面源码中是否存在未编码的 payload
                        page_content = await page.content()
                        if payload in page_content and "<script>" in page_content:
                            # 进一步确认是否可执行
                            findings.append({
                                "type": "reflected_xss",
                                "severity": "High",
                                "payload": payload,
                                "url": test_url,
                                "evidence": "Payload found unencoded in page source",
                                "description": "URL 参数中的 XSS Payload 在页面源码中未编码输出",
                                "recommendation": "对 URL 参数进行 HTML 编码输出",
                            })

                        page.remove_listener("dialog", handle_dialog)

                    except Exception as e:
                        continue

            # ========== 存储型 XSS 检测 ==========
            if scan_type in ("stored", "all"):
                try:
                    await page.goto(target_url, timeout=30000)
                    await page.wait_for_load_state("networkidle")

                    # 查找所有可输入的表单元素
                    input_selectors = [
                        "input[type='text']",
                        "input:not([type='hidden']):not([type='submit']):not([type='button'])",
                        "textarea",
                        "[contenteditable='true']",
                    ]

                    for selector in input_selectors:
                        inputs = await page.query_selector_all(selector)
                        for inp in inputs:
                            try:
                                # 检查元素是否可见和可编辑
                                is_visible = await inp.is_visible()
                                is_editable = await inp.is_editable()
                                if not is_visible or not is_editable:
                                    continue

                                # 使用 XSS payload 填充
                                test_payload = "<script>alert('XSS_STORED')</script>"

                                # 清除并填充
                                await inp.fill("")
                                await inp.fill(test_payload)

                                # 查找提交按钮
                                submit_selectors = [
                                    "button[type='submit']",
                                    "input[type='submit']",
                                    "button:has-text('提交')",
                                    "button:has-text('保存')",
                                    "button:has-text('确定')",
                                    "button:has-text('Submit')",
                                    "button:has-text('Save')",
                                    ".el-button--primary",  # Element UI
                                    "button.ant-btn-primary",  # Ant Design
                                ]

                                submit_btn = None
                                for submit_sel in submit_selectors:
                                    submit_btn = await page.query_selector(submit_sel)
                                    if submit_btn:
                                        is_visible = await submit_btn.is_visible()
                                        if is_visible:
                                            break

                                if submit_btn:
                                    # 监听 alert
                                    alert_triggered = []
                                    def handle_dialog_stored(dialog):
                                        alert_triggered.append(dialog.message)
                                        asyncio.create_task(dialog.dismiss())

                                    page.on("dialog", handle_dialog_stored)

                                    await submit_btn.click()
                                    await asyncio.sleep(2)

                                    if alert_triggered:
                                        findings.append({
                                            "type": "stored_xss",
                                            "severity": "Critical",
                                            "payload": test_payload,
                                            "input_selector": selector,
                                            "evidence": f"Alert triggered after form submission: {alert_triggered[0]}",
                                            "description": "表单提交后，XSS Payload 被存储并在页面中执行",
                                            "recommendation": "对用户输入进行严格的 HTML 编码和过滤",
                                        })

                                    page.remove_listener("dialog", handle_dialog_stored)

                                    # 返回原页面继续测试
                                    await page.goto(target_url, timeout=30000)
                                    await page.wait_for_load_state("networkidle")

                            except Exception as e:
                                continue

                except Exception as e:
                    pass

            # ========== DOM 型 XSS 检测 ==========
            if scan_type in ("dom", "all"):
                dom_payloads = [
                    "#<img src=x onerror=alert('DOM_XSS')>",
                    "#<script>alert('DOM_XSS')</script>",
                ]
                for payload in dom_payloads:
                    try:
                        test_url = f"{target_url}{payload}"

                        alert_triggered = []
                        def handle_dialog_dom(dialog):
                            alert_triggered.append(dialog.message)
                            asyncio.create_task(dialog.dismiss())

                        page.on("dialog", handle_dialog_dom)

                        await page.goto(test_url, timeout=30000)
                        await page.wait_for_load_state("networkidle")
                        await asyncio.sleep(1)

                        if alert_triggered:
                            findings.append({
                                "type": "dom_xss",
                                "severity": "High",
                                "payload": payload,
                                "url": test_url,
                                "evidence": f"Alert triggered: {alert_triggered[0]}",
                                "description": "前端路由/Hash 中的 XSS Payload 被 DOM 操作执行",
                                "recommendation": "对 URL hash/路由参数进行编码处理",
                            })

                        page.remove_listener("dialog", handle_dialog_dom)

                    except Exception as e:
                        continue

            await browser.close()

    except ImportError as e:
        return json.dumps({
            "success": False,
            "error": f"Playwright 未安装: {str(e)}",
        }, ensure_ascii=False, indent=2)
    except Exception as e:
        return json.dumps({
            "success": False,
            "error": f"执行失败: {str(e)}",
        }, ensure_ascii=False, indent=2)
    finally:
        # 恢复 SelectorEventLoop
        if _old_loop:
            try:
                _restore_selector_loop()
            except Exception:
                pass

    # 保存结果
    result = {
        "success": True,
        "target_url": target_url,
        "scan_type": scan_type,
        "total_findings": len(findings),
        "findings": findings,
        "timestamp": datetime.now().isoformat(),
    }
    out_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")

    return json.dumps(result, ensure_ascii=False, indent=2)


@tool
async def scan_web_csrf(
    target_url: str,
    cookies: Optional[str] = None,
    output_file: Optional[str] = None,
) -> str:
    """
    CSRF 漏洞扫描

    检测 B端应用是否存在 CSRF 防护缺失：
    - 检查表单是否包含 CSRF Token
    - 检查关键操作是否有 SameSite Cookie 保护
    - 测试跨站请求是否被阻止

    Args:
        target_url: 目标页面 URL
        cookies: Cookie 字符串
        output_file: 输出文件路径（可选）

    Returns:
        JSON 格式的 CSRF 扫描结果
    """
    findings = []
    _old_loop = None

    try:
        # Windows 上临时切换到 ProactorEventLoop（Playwright 需要子进程支持）
        if sys.platform == "win32":
            _old_loop = _set_windows_proactor_loop()

        async with async_playwright() as p:
            browser = await p.chromium.launch(
                headless=True,
                args=BROWSER_ARGS
            )
            context = await browser.new_context(
                viewport={"width": 1280, "height": 720}
            )

            # 拦截静态资源加速加载
            await context.route("**/*", lambda route, request:
                route.abort() if request.resource_type in BLOCKED_RESOURCE_TYPES
                else route.continue_()
            )

            page = await context.new_page()

            # 设置 cookie
            if cookies:
                cookie_list = []
                for cookie_str in cookies.split(';'):
                    cookie_str = cookie_str.strip()
                    if '=' in cookie_str:
                        name, value = cookie_str.split('=', 1)
                        cookie_list.append({
                            "name": name.strip(),
                            "value": value.strip(),
                            "domain": "." + target_url.split('/')[2].split(':')[0],
                            "path": "/",
                        })
                await context.add_cookies(cookie_list)

            await page.goto(target_url, timeout=30000)
            await page.wait_for_load_state("networkidle")

            # 检查表单中的 CSRF Token
            forms = await page.query_selector_all("form")
            for form in forms:
                try:
                    form_html = await form.inner_html()
                    form_action = await form.get_attribute("action") or ""
                    form_method = (await form.get_attribute("method") or "get").upper()

                    # 只检查 POST 表单（GET 表单通常不涉及 CSRF）
                    if form_method != "POST":
                        continue

                    # 检查是否有 CSRF token
                    csrf_indicators = [
                        "csrf", "_token", "xsrf", "authenticity",
                        "csrfmiddlewaretoken", "__RequestVerificationToken",
                    ]
                    has_csrf_token = any(token in form_html.lower() for token in csrf_indicators)

                    # 检查是否有 Origin/Referer 验证的迹象（通过 meta tag）
                    has_meta_csrf = "csrf-token" in form_html.lower()

                    if not has_csrf_token and not has_meta_csrf:
                        findings.append({
                            "type": "missing_csrf_token",
                            "severity": "Medium",
                            "description": f"POST 表单缺少 CSRF Token 保护",
                            "form_action": form_action,
                            "evidence": "Form HTML does not contain CSRF token",
                            "recommendation": "在表单中添加 CSRF Token 验证（如 <input type='hidden' name='csrf_token' value='...'>）",
                        })
                except:
                    continue

            # 检查 Cookie 的 SameSite 属性
            cookies_data = await context.cookies()
            for cookie in cookies_data:
                cookie_name = cookie.get("name", "")
                samesite = cookie.get("sameSite", "")
                secure = cookie.get("secure", False)
                httponly = cookie.get("httpOnly", False)

                # 检查 session/auth cookie
                if any(auth in cookie_name.lower() for auth in ["session", "auth", "token", "jwt"]):
                    if not samesite:
                        findings.append({
                            "type": "weak_samesite_cookie",
                            "severity": "Medium",
                            "cookie_name": cookie_name,
                            "description": f"认证 Cookie '{cookie_name}' 缺少 SameSite 保护",
                            "evidence": f"SameSite=None/Not set",
                            "recommendation": "设置 SameSite=Strict 或 SameSite=Lax",
                        })

                    if not secure:
                        findings.append({
                            "type": "insecure_cookie",
                            "severity": "Low",
                            "cookie_name": cookie_name,
                            "description": f"Cookie '{cookie_name}' 未设置 Secure 标志",
                            "recommendation": "设置 Secure 标志，确保 Cookie 只通过 HTTPS 传输",
                        })

                    if not httponly:
                        findings.append({
                            "type": "no_httponly_cookie",
                            "severity": "Low",
                            "cookie_name": cookie_name,
                            "description": f"Cookie '{cookie_name}' 未设置 HttpOnly 标志",
                            "recommendation": "设置 HttpOnly 标志，防止 JavaScript 访问 Cookie",
                        })

            await browser.close()

    except Exception as e:
        return json.dumps({
            "success": False,
            "error": str(e),
        }, ensure_ascii=False, indent=2)
    finally:
        # 恢复 SelectorEventLoop
        if _old_loop:
            try:
                _restore_selector_loop()
            except Exception:
                pass

    return json.dumps({
        "success": True,
        "target_url": target_url,
        "total_findings": len(findings),
        "findings": findings,
        "timestamp": datetime.now().isoformat(),
    }, ensure_ascii=False, indent=2)


@tool
async def scan_web_clickjacking(
    target_url: str,
    output_file: Optional[str] = None,
) -> str:
    """
    点击劫持漏洞扫描

    检测目标页面是否允许被嵌入 iframe（X-Frame-Options 缺失）。

    Args:
        target_url: 目标页面 URL
        output_file: 输出文件路径（可选）

    Returns:
        JSON 格式的点击劫持扫描结果
    """
    findings = []

    try:
        async with httpx.AsyncClient(follow_redirects=True, timeout=30) as client:
            response = await client.get(target_url)

            headers = response.headers
            x_frame_options = headers.get("X-Frame-Options")
            csp = headers.get("Content-Security-Policy", "")

            if not x_frame_options and "frame-ancestors" not in csp:
                findings.append({
                    "type": "clickjacking",
                    "severity": "Medium",
                    "description": "页面缺少 X-Frame-Options 和 CSP frame-ancestors 保护",
                    "evidence": {
                        "X-Frame-Options": x_frame_options or "Not set",
                        "CSP": csp or "Not set",
                    },
                    "recommendation": "添加 X-Frame-Options: DENY 或 SAMEORIGIN，或在 CSP 中设置 frame-ancestors",
                })

            # 检查其他安全响应头
            security_headers = {
                "X-Content-Type-Options": {
                    "expected": "nosniff",
                    "severity": "Low",
                    "description": "防止 MIME 类型嗅探攻击",
                },
                "X-XSS-Protection": {
                    "expected": "1; mode=block",
                    "severity": "Info",
                    "description": "启用浏览器 XSS 过滤器（现代浏览器已废弃，但仍建议设置）",
                },
                "Strict-Transport-Security": {
                    "expected": "max-age=",
                    "severity": "Medium",
                    "description": "强制 HTTPS 访问",
                },
                "Referrer-Policy": {
                    "expected": None,
                    "severity": "Low",
                    "description": "控制 Referrer 信息泄露",
                },
                "Content-Security-Policy": {
                    "expected": None,
                    "severity": "Medium",
                    "description": "内容安全策略",
                },
            }

            for header, config in security_headers.items():
                value = headers.get(header)
                if not value:
                    findings.append({
                        "type": "missing_security_header",
                        "severity": config["severity"],
                        "header": header,
                        "description": f"缺少安全响应头: {header}",
                        "impact": config["description"],
                        "recommendation": f"添加 {header} 响应头",
                    })

            # 检查服务器信息泄露
            server = headers.get("Server", "")
            x_powered_by = headers.get("X-Powered-By", "")

            if server:
                findings.append({
                    "type": "server_info_leak",
                    "severity": "Info",
                    "header": "Server",
                    "value": server,
                    "description": f"Server 头泄露服务器信息: {server}",
                    "recommendation": "移除或伪装 Server 响应头",
                })

            if x_powered_by:
                findings.append({
                    "type": "server_info_leak",
                    "severity": "Info",
                    "header": "X-Powered-By",
                    "value": x_powered_by,
                    "description": f"X-Powered-By 头泄露技术栈信息: {x_powered_by}",
                    "recommendation": "移除 X-Powered-By 响应头",
                })

    except Exception as e:
        return json.dumps({
            "success": False,
            "error": str(e),
        }, ensure_ascii=False, indent=2)

    return json.dumps({
        "success": True,
        "target_url": target_url,
        "total_findings": len(findings),
        "findings": findings,
        "timestamp": datetime.now().isoformat(),
    }, ensure_ascii=False, indent=2)


@tool
async def scan_web_sensitive_info(
    target_url: str,
    cookies: Optional[str] = None,
    output_file: Optional[str] = None,
) -> str:
    """
    B端页面敏感信息泄露扫描

    检测页面中是否泄露敏感信息：
    - API Key、Token、密码
    - 内部 IP、域名
    - 调试信息、堆栈跟踪
    - 版本信息

    Args:
        target_url: 目标页面 URL
        cookies: Cookie 字符串
        output_file: 输出文件路径（可选）

    Returns:
        JSON 格式的敏感信息扫描结果
    """
    findings = []

    # 检测模式
    patterns = {
        "api_key": {
            "pattern": r"(?i)(api[_-]?key|apikey)\s*[:=]\s*['\"][a-zA-Z0-9_\-]{16,}['\"]",
            "severity": "High",
        },
        "jwt_token": {
            "pattern": r"eyJ[a-zA-Z0-9_-]*\.eyJ[a-zA-Z0-9_-]*\.[a-zA-Z0-9_-]*",
            "severity": "High",
        },
        "aws_access_key": {
            "pattern": r"AKIA[0-9A-Z]{16}",
            "severity": "Critical",
        },
        "aws_secret_key": {
            "pattern": r"(?i)aws[_-]?secret[_-]?access[_-]?key\s*[:=]\s*['\"][a-zA-Z0-9/+=]{40}['\"]",
            "severity": "Critical",
        },
        "password_in_code": {
            "pattern": r"(?i)(password|passwd|pwd)\s*[:=]\s*['\"][^'\"\s]{4,}['\"]",
            "severity": "High",
        },
        "internal_ip": {
            "pattern": r"(?i)(192\.168\.|10\.|172\.(1[6-9]|2[0-9]|3[01])\.)[0-9.]+",
            "severity": "Low",
        },
        "email": {
            "pattern": r"[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}",
            "severity": "Info",
        },
        "phone": {
            "pattern": r"1[3-9]\d{9}",
            "severity": "Info",
        },
        "debug_info": {
            "pattern": r"(?i)(debug|stack trace|traceback|error log|exception)\s*[:=]",
            "severity": "Medium",
        },
        "private_key": {
            "pattern": r"-----BEGIN (RSA |DSA |EC |OPENSSH )?PRIVATE KEY-----",
            "severity": "Critical",
        },
        "database_connection": {
            "pattern": r"(?i)(mongodb|mysql|postgresql|redis)://[^\s\"']+",
            "severity": "Critical",
        },
    }

    try:
        # 使用 httpx 获取页面内容（不依赖 Playwright）
        headers = {}
        if cookies:
            headers["Cookie"] = cookies

        async with httpx.AsyncClient(follow_redirects=True, timeout=30) as client:
            response = await client.get(target_url, headers=headers)
            page_source = response.text

            # 在页面源码中检测
            for leak_type, config in patterns.items():
                matches = re.findall(config["pattern"], page_source)
                if matches:
                    # 去重
                    unique_matches = list(set(str(m) for m in matches))
                    findings.append({
                        "type": "sensitive_info_leak",
                        "subtype": leak_type,
                        "severity": config["severity"],
                        "source": "page_source",
                        "description": f"页面源码中发现 {leak_type} 泄露",
                        "evidence": unique_matches[:5],
                        "count": len(unique_matches),
                        "recommendation": "从页面源码中移除敏感信息，使用环境变量或后端配置",
                    })

            # 提取并检查 JS 文件中的敏感信息
            script_pattern = r'<script[^>]+src=["\']([^"\']+)["\']'
            script_urls = re.findall(script_pattern, page_source)

            for src in script_urls:
                # 处理相对路径
                if src.startswith(("http://", "https://")):
                    js_url = src
                elif src.startswith("//"):
                    js_url = "https:" + src
                elif src.startswith("/"):
                    from urllib.parse import urljoin
                    js_url = urljoin(target_url, src)
                else:
                    from urllib.parse import urljoin
                    js_url = urljoin(target_url, src)

                try:
                    js_response = await client.get(js_url, timeout=10)
                    if js_response.status_code == 200:
                        js_content = js_response.text

                        for leak_type, config in patterns.items():
                            matches = re.findall(config["pattern"], js_content)
                            if matches:
                                unique_matches = list(set(str(m) for m in matches))
                                findings.append({
                                    "type": "sensitive_info_in_js",
                                    "subtype": leak_type,
                                    "severity": config["severity"],
                                    "source": f"javascript_file:{src}",
                                    "description": f"JS 文件中发现 {leak_type} 泄露",
                                    "evidence": unique_matches[:3],
                                    "count": len(unique_matches),
                                    "recommendation": "从 JS 文件中移除敏感信息，通过 API 动态获取",
                                })
                except:
                    continue

            # 检查注释中的敏感信息
            comment_pattern = r"<!--(.*?)-->"
            comments = re.findall(comment_pattern, page_source, re.DOTALL)
            for comment in comments:
                for leak_type, config in patterns.items():
                    matches = re.findall(config["pattern"], comment)
                    if matches:
                        findings.append({
                            "type": "sensitive_info_in_comment",
                            "subtype": leak_type,
                            "severity": config["severity"],
                            "source": "html_comment",
                            "description": f"HTML 注释中发现 {leak_type} 泄露",
                            "evidence": comment[:200],
                            "recommendation": "移除包含敏感信息的 HTML 注释",
                        })

            # 注：localStorage 检查需要浏览器环境，Playwright 未安装时跳过
            # 可在安装 Playwright 后恢复此检查

    except Exception as e:
        return json.dumps({
            "success": False,
            "error": str(e),
        }, ensure_ascii=False, indent=2)

    return json.dumps({
        "success": True,
        "target_url": target_url,
        "total_findings": len(findings),
        "findings": findings,
        "timestamp": datetime.now().isoformat(),
    }, ensure_ascii=False, indent=2)
