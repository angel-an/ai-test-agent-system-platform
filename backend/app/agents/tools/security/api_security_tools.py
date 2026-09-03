"""
API 接口安全扫描工具

基于发现的 API 端点，执行专项安全测试：
- 认证绕过
- SQL 注入（JSON/Query 参数）
- 越权访问（IDOR）
- 输入验证
- 敏感信息泄露

不依赖外部工具，使用 Python httpx + Playwright 原生实现
"""

import json
import asyncio
import urllib.parse
from typing import Optional, List, Dict, Any
from datetime import datetime
from pathlib import Path

from langchain_core.tools import tool
import httpx

from app.config.settings import settings

SECURITY_WORKSPACE = Path(settings.security_workspace_root).resolve()

# SQL 注入错误关键词
SQL_ERROR_PATTERNS = [
    "sql syntax",
    "mysql_fetch",
    "pg_query",
    "ora-",
    "sqlite_",
    "sqlstate",
    "syntax error",
    "unexpected token",
    "incorrect syntax",
    "unterminated",
    "you have an error in your sql syntax",
]

# 测试 Payload
SQLI_PAYLOADS = [
    "' OR '1'='1",
    "' OR 1=1--",
    "1' UNION SELECT null--",
    "1' AND 1=1--",
    "1' AND 1=2--",
    "'; DROP TABLE users; --",
    "\" OR \"1\"=\"1",
    "' UNION SELECT username,password FROM users--",
]

XSS_PAYLOADS = [
    "<script>alert('XSS')</script>",
    "<img src=x onerror=alert('XSS')>",
    "<svg onload=alert('XSS')>",
]

CMD_PAYLOADS = [
    "; cat /etc/passwd",
    "| whoami",
    "`id`",
    "$(whoami)",
]

PATH_TRAVERSAL_PAYLOADS = [
    "../../../etc/passwd",
    "....//....//....//etc/passwd",
    "%2e%2e%2f%2e%2e%2f%2e%2e%2fetc%2fpasswd",
]

# 常见 API 路径列表（用于自动探测）
COMMON_API_PATHS = [
    "/api", "/api/v1", "/api/v2",
    "/rest", "/rest/v1",
    "/graphql",
    "/swagger.json", "/api-docs", "/openapi.json",
    "/health", "/healthz", "/actuator/health",
    "/api/users", "/api/auth", "/api/login",
    "/api/admin", "/api/system", "/api/config",
    "/api/upload", "/api/file", "/api/order",
]


async def _probe_common_api_paths(base_url: str) -> List[Dict[str, str]]:
    """
    当没有提供具体 endpoints 时，自动探测常见 API 路径。
    """
    discovered = []
    browser_headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
    }

    try:
        async with httpx.AsyncClient(follow_redirects=True, timeout=10, headers=browser_headers) as client:
            for path in COMMON_API_PATHS:
                url = f"{base_url.rstrip('/')}{path}"
                try:
                    response = await client.get(url)
                    # 如果返回 200/401/403/405/500，说明端点可能存在
                    if response.status_code in [200, 201, 204, 401, 403, 405, 500]:
                        discovered.append({"path": path, "method": "GET"})
                except Exception:
                    continue
    except Exception:
        pass

    return discovered


def _normalize_endpoints(endpoints: List[Dict[str, Any]]) -> List[Dict[str, str]]:
    """
    将 discover_apis_from_page 的原始输出转换为标准 endpoints 格式。

    支持两种输入格式：
    1. 标准格式: [{"path": "/api/users", "method": "GET"}, ...]
    2. 原始 API 发现格式: [{"url": "https://example.com/api/users", "method": "GET"}, ...]
    """
    normalized = []
    for ep in endpoints:
        if not isinstance(ep, dict):
            continue

        # 已经是标准格式
        if "path" in ep and "method" in ep:
            normalized.append({
                "path": ep["path"],
                "method": ep["method"].upper(),
            })
            continue

        # 从原始 API 发现格式转换
        if "url" in ep:
            url = ep["url"]
            method = ep.get("method", "GET").upper()
            try:
                parsed = urllib.parse.urlparse(url)
                path = parsed.path or "/"
                # 保留 query string
                if parsed.query:
                    path = f"{path}?{parsed.query}"
                normalized.append({"path": path, "method": method})
            except Exception:
                continue

    # 去重
    seen = set()
    unique = []
    for ep in normalized:
        key = f"{ep['method']} {ep['path']}"
        if key not in seen:
            seen.add(key)
            unique.append(ep)

    return unique


@tool
async def api_auth_bypass_test(
    base_url: str,
    endpoints: List[Dict[str, Any]],
    auth_token: Optional[str] = None,
    output_file: Optional[str] = None,
) -> str:
    """
    API 认证绕过测试

    测试 API 端点在没有认证的情况下是否可访问：
    1. 不带任何认证信息访问
    2. 使用无效/过期的 Token
    3. 使用错误格式的认证头

    Args:
        base_url: API 基础 URL
        endpoints: API 端点列表，支持两种格式：
            - 标准格式: [{"path": "/api/users", "method": "GET"}, ...]
            - 原始发现格式: [{"url": "https://example.com/api/users", "method": "GET"}, ...]
        auth_token: 有效的认证 Token（用于对比测试）
        output_file: 输出文件路径（可选）

    Returns:
        JSON 格式的认证绕过测试结果
    """
    findings = []

    # 标准化 endpoints 格式
    normalized_endpoints = _normalize_endpoints(endpoints)

    # 如果 endpoints 为空或只有根路径，探测常见 API 路径
    if not normalized_endpoints or all(ep["path"] == "/" for ep in normalized_endpoints):
        normalized_endpoints = await _probe_common_api_paths(base_url)

    if not normalized_endpoints:
        return json.dumps({
            "success": True,
            "vulnerable": False,
            "total_tested": 0,
            "findings": [],
            "message": "没有可测试的 API 端点",
        }, ensure_ascii=False, indent=2)

    async with httpx.AsyncClient(follow_redirects=True, timeout=30) as client:
        for endpoint in normalized_endpoints:
            path = endpoint["path"]
            method = endpoint["method"]
            full_url = f"{base_url.rstrip('/')}{path}"

            # 跳过公开接口（如登录、注册）
            public_paths = ["/login", "/register", "/auth", "/health", "/public"]
            if any(pub in path.lower() for pub in public_paths):
                continue

            # 测试 1: 不带认证
            try:
                if method == "GET":
                    response = await client.get(full_url)
                elif method == "POST":
                    response = await client.post(full_url, json={})
                elif method == "PUT":
                    response = await client.put(full_url, json={})
                elif method == "DELETE":
                    response = await client.delete(full_url)
                elif method == "PATCH":
                    response = await client.patch(full_url, json={})
                else:
                    continue

                # 如果未认证也能访问非公开资源，则存在漏洞
                if response.status_code in [200, 201, 204]:
                    content = response.text
                    # 检查响应内容是否包含敏感数据（排除错误页面）
                    if len(content) > 50 and "error" not in content.lower()[:100]:
                        findings.append({
                            "type": "unauthorized_access",
                            "severity": "High",
                            "endpoint": full_url,
                            "method": method,
                            "status_code": response.status_code,
                            "response_length": len(content),
                            "description": "接口未认证即可访问，存在未授权访问漏洞",
                            "recommendation": "添加认证中间件，确保所有敏感接口需要认证",
                        })

            except Exception as e:
                pass

            # 测试 2: 使用无效 Token
            try:
                headers = {"Authorization": "Bearer invalid_token_12345"}
                if method == "GET":
                    response = await client.get(full_url, headers=headers)
                elif method == "POST":
                    response = await client.post(full_url, headers=headers, json={})
                elif method in ["PUT", "PATCH"]:
                    response = await client.request(method, full_url, headers=headers, json={})
                elif method == "DELETE":
                    response = await client.delete(full_url, headers=headers)
                else:
                    continue

                if response.status_code in [200, 201, 204]:
                    findings.append({
                        "type": "weak_token_validation",
                        "severity": "Critical",
                        "endpoint": full_url,
                        "method": method,
                        "status_code": response.status_code,
                        "description": "使用无效 Token 仍能访问接口，Token 验证存在严重缺陷",
                        "recommendation": "严格验证 JWT Token 的签名和过期时间",
                    })
            except:
                pass

            # 测试 3: 使用错误格式的认证头
            try:
                headers = {"Authorization": "Basic invalid"}
                if method == "GET":
                    response = await client.get(full_url, headers=headers)
                elif method == "POST":
                    response = await client.post(full_url, headers=headers, json={})

                if response.status_code in [200, 201, 204]:
                    findings.append({
                        "type": "weak_auth_validation",
                        "severity": "High",
                        "endpoint": full_url,
                        "method": method,
                        "description": "使用错误格式的认证头仍能访问接口",
                        "recommendation": "严格验证认证头的格式和有效性",
                    })
            except:
                pass

    return json.dumps({
        "success": True,
        "total_endpoints_tested": len(endpoints),
        "total_findings": len(findings),
        "findings": findings,
        "timestamp": datetime.now().isoformat(),
    }, ensure_ascii=False, indent=2)


@tool
async def api_idor_test(
    base_url: str,
    endpoints: List[Dict[str, Any]],
    auth_token: Optional[str] = None,
    id_parameters: Optional[List[str]] = None,
    output_file: Optional[str] = None,
) -> str:
    """
    API 越权访问（IDOR）测试

    测试带资源 ID 的接口是否存在越权访问：
    - 遍历 ID 参数，访问其他用户的资源
    - 修改资源 ID，测试水平越权

    Args:
        base_url: API 基础 URL
        endpoints: API 端点列表，支持两种格式：
            - 标准格式: [{"path": "/api/users/{id}", "method": "GET"}, ...]
            - 原始发现格式: [{"url": "https://example.com/api/users/123", "method": "GET"}, ...]
        auth_token: 认证 Token
        id_parameters: ID 参数名列表，默认 ["id", "user_id", "project_id"]
        output_file: 输出文件路径（可选）

    Returns:
        JSON 格式的 IDOR 测试结果
    """
    findings = []

    # 标准化 endpoints 格式
    normalized_endpoints = _normalize_endpoints(endpoints)
    if not normalized_endpoints:
        return json.dumps({
            "success": True,
            "vulnerable": False,
            "total_tested": 0,
            "findings": [],
            "message": "没有可测试的 API 端点",
        }, ensure_ascii=False, indent=2)

    if id_parameters is None:
        id_parameters = ["id", "user_id", "project_id", "project_identifier",
                         "test_case_id", "security_test_id", "api_test_id"]

    # 测试用的 ID 列表
    test_ids = ["1", "2", "3", "999", "1000", "9999"]

    headers = {"Content-Type": "application/json"}
    if auth_token:
        headers["Authorization"] = f"Bearer {auth_token}"

    async with httpx.AsyncClient(follow_redirects=True, timeout=30) as client:
        for endpoint in normalized_endpoints:
            path = endpoint["path"]
            method = endpoint["method"]

            # 只测试带路径参数的端点
            if "{" not in path:
                continue

            for test_id in test_ids:
                # 替换路径中的 ID 参数
                test_path = path
                for id_param in id_parameters:
                    test_path = test_path.replace(f"{{{id_param}}}", test_id)

                # 替换其他常见的 ID 模式
                test_path = test_path.replace("{id}", test_id)

                full_url = f"{base_url.rstrip('/')}{test_path}"

                try:
                    if method == "GET":
                        response = await client.get(full_url, headers=headers)
                    elif method == "DELETE":
                        # DELETE 操作风险较高，使用 HEAD 先探测
                        response = await client.head(full_url, headers=headers)
                    elif method in ["PUT", "PATCH"]:
                        response = await client.request(method, full_url, headers=headers, json={})
                    else:
                        continue

                    if response.status_code == 200:
                        # 检查响应内容是否包含有效数据（而非错误信息）
                        content = ""
                        if method != "HEAD":
                            content = response.text.lower()

                        # 排除错误响应
                        error_indicators = ["error", "not found", "forbidden", "unauthorized", "invalid"]
                        if not any(err in content[:200] for err in error_indicators):
                            findings.append({
                                "type": "idor",
                                "severity": "High",
                                "endpoint": full_url,
                                "original_path": path,
                                "method": method,
                                "test_id": test_id,
                                "status_code": response.status_code,
                                "description": f"通过遍历 ID ({test_id}) 成功访问资源，存在越权访问漏洞",
                                "recommendation": "在服务端验证当前用户是否有权限访问该资源",
                            })
                            break  # 发现一个即可停止对该端点的测试

                except Exception as e:
                    pass

    return json.dumps({
        "success": True,
        "total_endpoints_tested": len(endpoints),
        "total_findings": len(findings),
        "findings": findings,
        "timestamp": datetime.now().isoformat(),
    }, ensure_ascii=False, indent=2)


@tool
async def api_input_validation_test(
    base_url: str,
    endpoints: List[Dict[str, Any]],
    auth_token: Optional[str] = None,
    output_file: Optional[str] = None,
) -> str:
    """
    API 输入验证测试

    测试 API 端点的输入验证是否完善：
    - SQL 注入 Payload（JSON/Query 参数）
    - XSS Payload
    - 命令注入 Payload
    - 路径遍历
    - 特殊字符处理
    - 超长输入处理

    Args:
        base_url: API 基础 URL
        endpoints: API 端点列表，支持两种格式：
            - 标准格式: [{"path": "/api/users", "method": "GET"}, ...]
            - 原始发现格式: [{"url": "https://example.com/api/users", "method": "GET"}, ...]
        auth_token: 认证 Token
        output_file: 输出文件路径（可选）

    Returns:
        JSON 格式的输入验证测试结果
    """
    findings = []

    # 标准化 endpoints 格式
    normalized_endpoints = _normalize_endpoints(endpoints)
    if not normalized_endpoints:
        return json.dumps({
            "success": True,
            "vulnerable": False,
            "total_tested": 0,
            "findings": [],
            "message": "没有可测试的 API 端点",
        }, ensure_ascii=False, indent=2)

    headers = {"Content-Type": "application/json"}
    if auth_token:
        headers["Authorization"] = f"Bearer {auth_token}"

    async with httpx.AsyncClient(follow_redirects=True, timeout=30) as client:
        for endpoint in normalized_endpoints:
            path = endpoint["path"]
            method = endpoint["method"]
            full_url = f"{base_url.rstrip('/')}{path}"

            # 测试 POST/PUT/PATCH 端点的请求体注入
            if method in ["POST", "PUT", "PATCH"]:
                # SQL 注入测试
                for payload in SQLI_PAYLOADS[:4]:
                    try:
                        test_data = {
                            "name": payload,
                            "description": payload,
                            "title": payload,
                            "content": payload,
                            "test": payload,
                        }

                        response = await client.request(
                            method, full_url,
                            headers=headers,
                            json=test_data
                        )

                        response_text = response.text.lower()

                        # 检测 SQL 错误信息
                        if any(err in response_text for err in SQL_ERROR_PATTERNS):
                            findings.append({
                                "type": "sql_injection",
                                "severity": "Critical",
                                "endpoint": full_url,
                                "method": method,
                                "payload": payload,
                                "evidence": response.text[:500],
                                "description": "接口存在 SQL 注入漏洞",
                                "recommendation": "使用参数化查询或 ORM，对用户输入进行严格过滤",
                            })
                            break

                        # 检测时间盲注（响应时间异常）
                        # 简化处理：检查响应中是否包含 payload 原样返回
                        if payload in response.text and "error" not in response_text[:100]:
                            findings.append({
                                "type": "potential_sql_injection",
                                "severity": "Medium",
                                "endpoint": full_url,
                                "method": method,
                                "payload": payload,
                                "description": "Payload 原样返回，可能存在 SQL 注入（需进一步验证）",
                                "recommendation": "使用参数化查询",
                            })
                            break

                    except Exception as e:
                        pass

                # XSS 测试
                for payload in XSS_PAYLOADS:
                    try:
                        test_data = {
                            "name": payload,
                            "description": payload,
                            "content": payload,
                        }

                        response = await client.request(
                            method, full_url,
                            headers=headers,
                            json=test_data
                        )

                        if payload in response.text:
                            findings.append({
                                "type": "xss",
                                "severity": "High",
                                "endpoint": full_url,
                                "method": method,
                                "payload": payload,
                                "description": "接口存在存储型 XSS 漏洞",
                                "recommendation": "对用户输入进行 HTML 编码，使用 Content-Type 限制",
                            })
                            break

                    except:
                        pass

                # 命令注入测试
                for payload in CMD_PAYLOADS:
                    try:
                        test_data = {
                            "name": payload,
                            "command": payload,
                            "exec": payload,
                        }

                        response = await client.request(
                            method, full_url,
                            headers=headers,
                            json=test_data
                        )

                        response_text = response.text.lower()
                        cmd_indicators = ["root:", "bin/bash", "uid=", "gid=", "windows"]
                        if any(ind in response_text for ind in cmd_indicators):
                            findings.append({
                                "type": "command_injection",
                                "severity": "Critical",
                                "endpoint": full_url,
                                "method": method,
                                "payload": payload,
                                "evidence": response.text[:500],
                                "description": "接口存在命令注入漏洞",
                                "recommendation": "禁止用户输入直接拼接命令，使用白名单验证",
                            })
                            break

                    except:
                        pass

            # 测试 GET 端点的 Query 参数注入
            elif method == "GET":
                for payload in SQLI_PAYLOADS[:3]:
                    try:
                        test_url = f"{full_url}?test={urllib.parse.quote(payload)}&name={urllib.parse.quote(payload)}"
                        response = await client.get(test_url, headers=headers)

                        response_text = response.text.lower()
                        if any(err in response_text for err in SQL_ERROR_PATTERNS):
                            findings.append({
                                "type": "sql_injection",
                                "severity": "Critical",
                                "endpoint": full_url,
                                "method": method,
                                "payload": payload,
                                "evidence": response.text[:500],
                                "description": "Query 参数存在 SQL 注入漏洞",
                                "recommendation": "使用参数化查询",
                            })
                            break

                    except:
                        pass

                # 路径遍历测试（针对文件下载类接口）
                if any(keyword in path.lower() for keyword in ["file", "download", "path", "doc"]):
                    for payload in PATH_TRAVERSAL_PAYLOADS[:2]:
                        try:
                            test_url = f"{full_url}?file={urllib.parse.quote(payload)}"
                            response = await client.get(test_url, headers=headers)

                            response_text = response.text
                            if "root:" in response_text or "[boot loader]" in response_text:
                                findings.append({
                                    "type": "path_traversal",
                                    "severity": "High",
                                    "endpoint": full_url,
                                    "method": method,
                                    "payload": payload,
                                    "evidence": response_text[:500],
                                    "description": "接口存在路径遍历/文件包含漏洞",
                                    "recommendation": "使用白名单验证文件路径，禁止路径遍历字符",
                                })
                                break

                        except:
                            pass

    return json.dumps({
        "success": True,
        "total_endpoints_tested": len(endpoints),
        "total_findings": len(findings),
        "findings": findings,
        "timestamp": datetime.now().isoformat(),
    }, ensure_ascii=False, indent=2)


@tool
async def api_rate_limit_test(
    base_url: str,
    endpoints: List[Dict[str, Any]],
    auth_token: Optional[str] = None,
    requests_count: int = 50,
    output_file: Optional[str] = None,
) -> str:
    """
    API 速率限制测试

    测试 API 是否存在速率限制，以及是否可以绕过：
    - 快速发送大量请求
    - 测试 X-Forwarded-For 绕过
    - 测试不同用户代理

    Args:
        base_url: API 基础 URL
        endpoints: API 端点列表，支持两种格式：
            - 标准格式: [{"path": "/api/users", "method": "GET"}, ...]
            - 原始发现格式: [{"url": "https://example.com/api/users", "method": "GET"}, ...]
        auth_token: 认证 Token
        requests_count: 测试请求数，默认 50
        output_file: 输出文件路径（可选）

    Returns:
        JSON 格式的速率限制测试结果
    """
    findings = []

    # 标准化 endpoints 格式
    normalized_endpoints = _normalize_endpoints(endpoints)
    if not normalized_endpoints:
        return json.dumps({
            "success": True,
            "vulnerable": False,
            "total_tested": 0,
            "findings": [],
            "message": "没有可测试的 API 端点",
        }, ensure_ascii=False, indent=2)

    # 选择一个端点进行测试（通常是登录或查询接口）
    test_endpoint = normalized_endpoints[0]
    path = test_endpoint["path"]
    method = test_endpoint["method"]
    full_url = f"{base_url.rstrip('/')}{path}"

    headers = {"Content-Type": "application/json"}
    if auth_token:
        headers["Authorization"] = f"Bearer {auth_token}"

    # 测试 1: 快速发送请求
    responses = []
    async with httpx.AsyncClient(follow_redirects=True, timeout=10) as client:
        start_time = datetime.now()

        for i in range(requests_count):
            try:
                if method == "GET":
                    response = await client.get(full_url, headers=headers)
                elif method == "POST":
                    response = await client.post(full_url, headers=headers, json={})
                else:
                    continue

                responses.append({
                    "status": response.status_code,
                    "index": i,
                })
            except Exception as e:
                responses.append({
                    "status": 0,
                    "error": str(e),
                    "index": i,
                })

        end_time = datetime.now()
        duration = (end_time - start_time).total_seconds()

    # 分析响应
    status_codes = [r["status"] for r in responses]
    success_count = sum(1 for s in status_codes if s == 200)
    rate_limited = sum(1 for s in status_codes if s == 429)
    blocked = sum(1 for s in status_codes if s in [403, 503])

    if rate_limited == 0 and blocked == 0 and success_count == requests_count:
        findings.append({
            "type": "missing_rate_limit",
            "severity": "Medium",
            "endpoint": full_url,
            "method": method,
            "requests_sent": requests_count,
            "successful_requests": success_count,
            "duration_seconds": duration,
            "description": f"发送 {requests_count} 个请求均未触发速率限制，可能存在 DoS 风险",
            "recommendation": "实施速率限制策略（如每 IP 每分钟 100 请求）",
        })
    elif rate_limited > 0:
        findings.append({
            "type": "rate_limit_present",
            "severity": "Info",
            "endpoint": full_url,
            "method": method,
            "requests_sent": requests_count,
            "rate_limited": rate_limited,
            "description": f"速率限制已启用，{rate_limited} 个请求被限制",
            "recommendation": "速率限制正常，可考虑增加更严格的限制",
        })

    # 测试 2: X-Forwarded-For 绕过
    async with httpx.AsyncClient(follow_redirects=True, timeout=10) as client:
        bypass_headers = headers.copy()
        bypass_headers["X-Forwarded-For"] = "1.2.3.4"
        bypass_headers["X-Real-IP"] = "1.2.3.4"

        try:
            if method == "GET":
                response = await client.get(full_url, headers=bypass_headers)
            elif method == "POST":
                response = await client.post(full_url, headers=bypass_headers, json={})

            if response.status_code == 200:
                findings.append({
                    "type": "rate_limit_bypass_possible",
                    "severity": "Low",
                    "endpoint": full_url,
                    "method": method,
                    "description": "使用 X-Forwarded-For 头可能绕过速率限制",
                    "recommendation": "在服务端获取真实客户端 IP，不依赖 X-Forwarded-For",
                })
        except:
            pass

    return json.dumps({
        "success": True,
        "total_findings": len(findings),
        "findings": findings,
        "timestamp": datetime.now().isoformat(),
    }, ensure_ascii=False, indent=2)
