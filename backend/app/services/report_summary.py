# -*- coding: utf-8 -*-
"""Playwright 报告 → 可视化结果摘要（确定性解析，无 LLM、无外部依赖）。

动机
----
API 测试执行后保存的成果物是 Playwright HTML 报告的 ZIP（内含自包含的 `index.html`）。
前端"成果物"面板对 API 报告只提供"打开原始 HTML /下载 ZIP"，
而 Web 侧已有可视化仪表盘（`ui/components/web-tests/visual-report-dashboard.tsx`）
与解析器（`parseResultSummary` / `parsePlaywrightJsonReport`）。

本模块把已存在的报告**离线解析**成前端可视化所需的结果摘要，
无需重跑测试，因此对历史成果物同样适用。

数据来源
--------
Playwright HTML 报告是自包含的：`index.html` 中内嵌一个
``data:application/zip;base64,...`` 负载，解开后含：

- ``report.json``：``{metadata, startTime, duration, files, projectNames, stats, errors, options, machines}``
  - ``stats``：``{total, expected, unexpected, flaky, skipped, ok}``
  - ``files[]``：``{fileId, fileName, tests[], stats}``
  - ``tests[]``：``{testId, title, projectName, location, duration, outcome, ok, results[]}``
- ``<fileId>.json``：单文件的测试明细

``outcome`` 取值 ``expected`` / ``unexpected`` / ``flaky`` / ``skipped``。

输出形状
--------
与前端 ``parseResultSummary`` 期望的键保持一致：
``{total, passed, failed, skipped, timed_out, duration_ms, tests:[{title, status, duration_ms, errors, steps}]}``
"""

from __future__ import annotations

import base64
import binascii
import io
import json
import logging
import re
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

#: HTML 报告内嵌数据的前缀。
EMBEDDED_ZIP_PREFIX = "data:application/zip;base64,"

#: base64 字符集（用于确定负载结束位置）。
_B64_CHARS = set("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/=")

#: Playwright 结果 outcome → 前端 status
OUTCOME_TO_STATUS: dict[str, str] = {
    "expected": "passed",
    "unexpected": "failed",
    "flaky": "passed",  # 重试后通过：前端无 flaky 桶，计为 passed 并单独统计
    "skipped": "skipped",
}


# ---------------------------------------------------------------------------
# 0. 断言提取（从错误堆栈/消息中确定性解析）
# ---------------------------------------------------------------------------

#: Playwright expect 断言的 matcher → 人类可读的中文说明
_MATCHER_DESCRIPTIONS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"\.toBe\("), "等于 (toBe)"),
    (re.compile(r"\.toEqual\("), "深度相等 (toEqual)"),
    (re.compile(r"\.toContain\("), "包含 (toContain)"),
    (re.compile(r"\.toContainEqual\("), "包含对象 (toContainEqual)"),
    (re.compile(r"\.toMatch\("), "正则匹配 (toMatch)"),
    (re.compile(r"\.toMatchObject\("), "对象匹配 (toMatchObject)"),
    (re.compile(r"\.toHaveProperty\("), "具有属性 (toHaveProperty)"),
    (re.compile(r"\.toBeTruthy\("), "为真值 (toBeTruthy)"),
    (re.compile(r"\.toBeFalsy\("), "为假值 (toBeFalsy)"),
    (re.compile(r"\.toBeNull\("), "为 null (toBeNull)"),
    (re.compile(r"\.toBeDefined\("), "已定义 (toBeDefined)"),
    (re.compile(r"\.toBeUndefined\("), "未定义 (toBeUndefined)"),
    (re.compile(r"\.toBeGreaterThan\("), "大于 (toBeGreaterThan)"),
    (re.compile(r"\.toBeGreaterThanOrEqual\("), "大于等于 (toBeGreaterThanOrEqual)"),
    (re.compile(r"\.toBeLessThan\("), "小于 (toBeLessThan)"),
    (re.compile(r"\.toBeLessThanOrEqual\("), "小于等于 (toBeLessThanOrEqual)"),
    (re.compile(r"\.toBeCloseTo\("), "近似相等 (toBeCloseTo)"),
    (re.compile(r"\.toBeInstanceOf\("), "实例类型 (toBeInstanceOf)"),
    (re.compile(r"\.toHaveLength\("), "长度等于 (toHaveLength)"),
    (re.compile(r"\.toHaveCount\("), "数量等于 (toHaveCount)"),
    (re.compile(r"\.toHaveText\("), "文本等于 (toHaveText)"),
    (re.compile(r"\.toContainText\("), "包含文本 (toContainText)"),
    (re.compile(r"\.toHaveURL\("), "URL 等于 (toHaveURL)"),
    (re.compile(r"\.toHaveTitle\("), "标题等于 (toHaveTitle)"),
    (re.compile(r"\.toHaveValue\("), "值等于 (toHaveValue)"),
    (re.compile(r"\.toBeVisible\("), "可见 (toBeVisible)"),
    (re.compile(r"\.toBeEnabled\("), "可用 (toBeEnabled)"),
    (re.compile(r"\.toBeDisabled\("), "禁用 (toBeDisabled)"),
    (re.compile(r"\.toBeChecked\("), "已勾选 (toBeChecked)"),
    (re.compile(r"\.toBeOK\("), "响应成功 2xx (toBeOK)"),
    (re.compile(r"\.toPass\("), "最终通过 (toPass)"),
]

#: 期望/实际值对：Playwright 把每个值单独成行，前缀固定
_EXPECTED_PREFIX = re.compile(r"^Expected\s+(?:value|string|number|object|array)?:?\s*", re.IGNORECASE)
_RECEIVED_PREFIX = re.compile(r"^Received\s+(?:value|string|number|object|array)?:?\s*", re.IGNORECASE)
#: expect(actual) 的 actual 部分（单行，长度截断）
_EXPECT_ACTUAL_RE = re.compile(r"expect\((.{1,120}?)\)\.")

#: 截断长值，避免报告膨胀
_MAX_VALUE_LEN = 300


def _truncate(value: Any, limit: int = _MAX_VALUE_LEN) -> str:
    text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, default=str)
    text = text.replace("\n", " ").strip()
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _matcher_description(snippet: str) -> str:
    for pattern, desc in _MATCHER_DESCRIPTIONS:
        if pattern.search(snippet):
            return desc
    return "断言校验"


def _extract_expected_actual(lines: list[str], start: int) -> tuple[Optional[str], Optional[str]]:
    """从断言错误文本中解析 Expected / Received 值对。

    Playwright expect 失败格式（每行一个值）::

        Expected value: 200
        Received value: 500

    或带颜色码的变体。返回 (expected, received)，缺失为 None。
    """
    expected: Optional[str] = None
    received: Optional[str] = None
    for line in lines[start : start + 8]:
        stripped = line.strip()
        m = _EXPECTED_PREFIX.match(stripped)
        if m:
            expected = _truncate(stripped[m.end() :])
            continue
        m = _RECEIVED_PREFIX.match(stripped)
        if m:
            received = _truncate(stripped[m.end() :])
            continue
        if expected is not None and received is not None:
            break
    return expected, received


def _extract_assertions(error_text: str) -> list[dict[str, Any]]:
    """从单条错误消息中确定性提取断言明细。

    输出形状::

        {
          "matcher": "等于 (toBe)",          # 断言类型（人类可读）
          "expression": "expect(data.code).toBe(200)",  # 原始表达式（截断）
          "expected": "200",                  # 期望值（无则 null）
          "received": "500",                  # 实际值（无则 null）
          "passed": false,                    # 该断言是否通过（错误中提取的必然失败）
        }
    """
    assertions: list[dict[str, Any]] = []
    if not error_text:
        return assertions

    lines = error_text.splitlines()
    for idx, line in enumerate(lines):
        if "expect(" not in line:
            continue
        m = _EXPECT_ACTUAL_RE.search(line)
        if not m:
            continue
        actual = _truncate(m.group(1), 120)
        matcher = _matcher_description(line)
        expected, received = _extract_expected_actual(lines, idx + 1)
        assertions.append({
            "matcher": matcher,
            "expression": _truncate(f"expect({actual}).{line[m.end():]}".strip(), 200),
            "expected": expected,
            "received": received,
            "passed": False,
        })

    # 去重：同一表达式在同一错误中可能出现多次（堆栈重打）
    seen: set[str] = set()
    unique: list[dict[str, Any]] = []
    for a in assertions:
        key = a["expression"]
        if key in seen:
            continue
        seen.add(key)
        unique.append(a)
    return unique[:20]  # 上限保护


def _extract_location(location: Any) -> Optional[str]:
    """从 Playwright 的 location 对象提取 文件:行 位置。"""
    if not isinstance(location, dict):
        return None
    file = location.get("file")
    line = location.get("line")
    if not file:
        return None
    return f"{file}:{line}" if isinstance(line, int) else str(file)


def _looks_like_request_title(title: str) -> bool:
    """Playwright APIRequestContext 步骤形如 'POST http://host/path'。"""
    return bool(re.match(r"^(GET|POST|PUT|DELETE|PATCH|HEAD|OPTIONS)\s+\S+", title.strip(), re.IGNORECASE))


def _short_endpoint(url: str) -> str:
    """从完整 URL 提取 路径?查询 部分，便于报告阅读。"""
    m = re.match(r"^[a-zA-Z][a-zA-Z0-9+.-]*://[^/]+(/[^?#]*)?(\?[^#]*)?", url.strip())
    if not m:
        return url
    path = m.group(1) or "/"
    query = m.group(2) or ""
    return (path + query)[:160]


def _extract_api_steps(
    test: dict[str, Any], result: dict[str, Any] | None = None
) -> list[dict[str, Any]]:
    """提取 API 请求步骤（含断言子步骤），供报告"请求/断言"明细使用。

    result 为单文件明细（<fileId>.json）中的首个 result；缺省时回退 report.json。

    步骤形状::

        {
          "title": "POST /cop/json",
          "status": "passed" | "failed",
          "duration_ms": 123,
          "assertions": [ ... _extract_assertions ... ],
          "location": "api_memRegister.spec.ts:42",
        }
    """
    raw_steps = _collect_steps(test, result)
    if not raw_steps:
        return []

    api_steps: list[dict[str, Any]] = []
    for step in raw_steps:
        title = str(step.get("title") or step.get("name") or "")
        if not _looks_like_request_title(title):
            continue  # 只保留真实 HTTP 请求步骤，过滤 hook/杂项
        m = re.match(r"^(GET|POST|PUT|DELETE|PATCH|HEAD|OPTIONS)\s+(\S+)", title.strip(), re.IGNORECASE)
        method = m.group(1).upper() if m else ""
        url = m.group(2) if m else title
        duration = step.get("duration")
        error = step.get("error") if isinstance(step.get("error"), dict) else {}
        api_steps.append({
            "title": f"{method} {_short_endpoint(url)}",
            "status": "failed" if error else "passed",
            "duration_ms": int(duration) if isinstance(duration, (int, float)) else 0,
            "assertions": _extract_assertions(str(error.get("message", "") or "")),
            "location": _extract_location(step.get("location")),
        })
    return api_steps[:50]  # 上限保护


def _first_error_message(
    test: dict[str, Any], result: dict[str, Any] | None = None
) -> str:
    errors = _result_errors(result) or _test_errors(test)
    return errors[0] if errors else ""


def _classify_failure(
    test: dict[str, Any], result: dict[str, Any] | None = None
) -> dict[str, Any]:
    """失败原因分析（确定性规则，无 LLM）。

    从错误文本归类失败根因，并给出可执行的修复建议，
    帮助核对"是接口真缺陷，还是脚本/环境问题"。
    """
    text = _first_error_message(test, result)
    lowered = text.lower()
    status = _resolve_status(test)
    assertions = _extract_assertions(text)

    # 从错误文本提取 HTTP 状态码（如 "404 Not Found"、status: 500）
    status_code: Optional[int] = None
    m = re.search(r"\b(\d{3})\s+(?:Not Found|Bad Request|Unauthorized|Forbidden|Internal Server Error|Service Unavailable)\b", text)
    if m:
        status_code = int(m.group(1))
    else:
        m = re.search(r"[Ss]tatus[:\s]+(\d{3})\b", text)
        if m:
            status_code = int(m.group(1))

    if status != "failed":
        return {
            "category": "none",
            "label": "—",
            "detail": "",
            "suggestion": "",
            "http_status_code": status_code,
            "is_product_defect": False,
        }

    def result(category: str, label: str, detail: str, suggestion: str, defect: bool) -> dict[str, Any]:
        return {
            "category": category,
            "label": label,
            "detail": detail,
            "suggestion": suggestion,
            "http_status_code": status_code,
            "is_product_defect": defect,
        }

    # 1) 网络/连接层
    if any(k in lowered for k in ("enotfound", "econnrefused", "econnreset", "etimedout", "socket hang up", "getaddrinfo")):
        return result(
            "network", "网络/连接失败",
            "请求未到达服务端：DNS 解析失败、连接被拒绝或超时",
            "核对该端点环境配置中的 base_url 是否可达、服务是否启动、防火墙/WAF 是否拦截本机 IP",
            False,
        )

    # 2) 超时
    if "timeout" in lowered and status_code is None:
        return result(
            "timeout", "请求超时",
            "服务端在限定时间内未返回响应",
            "确认服务负载与慢查询；如属正常慢响应，在脚本中调大 expect/response 超时时间",
            False,
        )

    # 3) 断言失败（有明确 Expected/Received）
    if assertions:
        a0 = assertions[0]
        expected, received = a0.get("expected"), a0.get("received")
        if status_code and status_code >= 500:
            return result(
                "server_error", "服务端 5xx 错误",
                f"接口返回 {status_code}，服务端内部错误（断言 {a0['matcher']} 也因此失败）",
                "属服务端缺陷：附 reqid/时间点后端排查日志；修复后回归本用例",
                True,
            )
        if status_code and status_code == 401:
            return result(
                "auth", "认证失败 (401)",
                "接口拒绝访问：token 缺失/过期，或鉴权签名（appid/appsecret）配置有误",
                "检查环境配置的凭证与 token 获取流程；确认 key/appid/appsecret 未过期",
                False,
            )
        if status_code and status_code == 403:
            return result(
                "forbidden", "权限不足 (403)",
                "已认证但无权限访问该资源",
                "核对接口 key 的权限范围；确认测试账号具备该接口访问授权",
                False,
            )
        if status_code and status_code == 404:
            return result(
                "not_found", "接口不存在 (404)",
                "请求路径未命中任何路由",
                "核对请求路径与大小写是否与接口文档一致；确认服务版本部署了该接口",
                False,
            )
        if status_code and status_code == 400:
            return result(
                "bad_request", "请求参数错误 (400)",
                "服务端判定请求体/参数不合法（断言失败是参数未通过服务端校验的连带结果）",
                "对照接口文档核对必填字段、类型与枚举值；修正测试数据后重跑",
                False,
            )
        if status_code and status_code == 429:
            return result(
                "rate_limit", "触发限流 (429)",
                "请求频率超过服务端限制",
                "降低并发/增加用例间隔；确认是否需要压测白名单",
                False,
            )
        return result(
            "assertion", "断言未通过",
            f"响应已返回（HTTP {status_code or '—'}），但业务校验未满足："
            f"期望 {expected or '—'}，实际 {received or '—'}（{a0['matcher']}）",
            "若为业务规则变更 → 更新脚本断言；若与文档不符 → 提缺陷并附 reqid",
            status_code is not None and 200 <= status_code < 300 and False or False,
        )

    # 4) 有状态码但无断言明细
    if status_code:
        if status_code >= 500:
            return result(
                "server_error", "服务端 5xx 错误",
                f"接口返回 {status_code}，未执行到业务断言",
                "属服务端缺陷：附 reqid/时间点后端排查日志；修复后回归本用例",
                True,
            )
        if status_code == 401:
            return result("auth", "认证失败 (401)", "接口拒绝访问：token 缺失/过期或签名有误", "检查环境配置凭证与 token 流程", False)
        if status_code == 403:
            return result("forbidden", "权限不足 (403)", "已认证但无权限访问该资源", "核对 key 权限范围与测试账号授权", False)
        if status_code == 404:
            return result("not_found", "接口不存在 (404)", "请求路径未命中路由", "核对路径与部署版本", False)
        if status_code == 400:
            return result("bad_request", "请求参数错误 (400)", "服务端判定请求不合法", "对照文档核对请求体与参数", False)
        if status_code == 429:
            return result("rate_limit", "触发限流 (429)", "请求频率超限", "降低并发或增加间隔", False)

    # 5) 脚本自身错误
    if any(k in lowered for k in ("referenceerror", "typeerror", "syntaxerror", "cannot read propert", "is not defined", "is not a function")):
        return result(
            "script_error", "测试脚本错误",
            "脚本运行期异常（变量未定义/类型错误/语法问题），与接口行为无关",
            "修复脚本后重跑；可用「AI 修复」重新生成该用例脚本",
            False,
        )

    # 6) 环境/数据问题
    if any(k in lowered for k in ("test data", "fixture", "prerequisite", "前置", "测试数据")):
        return result(
            "test_data", "测试数据/前置条件不足",
            "依赖的测试数据或前置状态不满足（如必须先创建的会员/订单不存在）",
            "检查环境数据初始化；按业务流先执行前置用例再执行本用例",
            False,
        )

    # 7) 兜底
    return result(
        "unknown", "其他失败",
        _truncate(text, 200) or "未提取到错误详情",
        "打开原始报告查看完整错误堆栈；必要时人工复核是否产品缺陷",
        False,
    )


def _build_analysis(tests: list[dict[str, Any]]) -> dict[str, Any]:
    """基于失败分类聚合整份报告的分析结论。"""
    failed_tests = [t for t in tests if t.get("status") == "failed"]
    total = len(tests)
    passed = len([t for t in tests if t.get("status") == "passed"])
    product_defects = [t for t in failed_tests if t.get("failure_analysis", {}).get("is_product_defect")]
    env_issues = [t for t in failed_tests if not t.get("failure_analysis", {}).get("is_product_defect")]

    # 按类别聚合
    by_category: dict[str, int] = {}
    for t in failed_tests:
        cat = t.get("failure_analysis", {}).get("category", "unknown")
        by_category[cat] = by_category.get(cat, 0) + 1

    pass_rate = round(passed / total, 4) if total else 0.0

    if total == 0:
        conclusion = "本次未执行到任何测试用例，请检查脚本与环境配置。"
    elif not failed_tests:
        conclusion = f"全部 {total} 条用例通过（通过率 100%），接口行为符合脚本断言预期。"
    elif product_defects and not env_issues:
        conclusion = (
            f"通过 {passed}/{total}（通过率 {pass_rate*100:.1f}%）；"
            f"失败 {len(failed_tests)} 条均为服务端异常（5xx），判定为产品缺陷，建议登记缺陷并附 reqid 交由后端修复。"
        )
    elif env_issues and not product_defects:
        conclusion = (
            f"通过 {passed}/{total}（通过率 {pass_rate*100:.1f}%）；"
            f"失败 {len(failed_tests)} 条均为环境/脚本/数据类问题，非接口缺陷，"
            "建议按各条修复建议处理后重跑，再评估接口真实质量。"
        )
    else:
        conclusion = (
            f"通过 {passed}/{total}（通过率 {pass_rate*100:.1f}%）；"
            f"失败 {len(failed_tests)} 条中 {len(product_defects)} 条疑似产品缺陷、"
            f"{len(env_issues)} 条为环境/脚本/数据问题，需分类处理。"
        )

    return {
        "total": total,
        "passed": passed,
        "failed": len(failed_tests),
        "pass_rate": pass_rate,
        "product_defect_count": len(product_defects),
        "env_issue_count": len(env_issues),
        "failed_by_category": by_category,
        "conclusion": conclusion,
    }


#: 生成脚本 testInfo.attach 的附件名（入参/出参留证）。
IO_REQUEST_ATTACHMENT = "api-request"
IO_RESPONSE_ATTACHMENT = "api-response"


def _collect_steps(
    test: dict[str, Any], result: dict[str, Any] | None = None
) -> list[dict[str, Any]]:
    """取步骤列表：优先用单文件明细的 result.steps，回退 report.json 的 results[].steps。"""
    if isinstance(result, dict) and isinstance(result.get("steps"), list):
        return [s for s in result["steps"] if isinstance(s, dict)]
    collected: list[dict[str, Any]] = []
    for entry in _iter_result_entries(test):
        raw = entry.get("steps")
        if isinstance(raw, list):
            collected.extend(s for s in raw if isinstance(s, dict))
    return collected


def _result_errors(result: dict[str, Any] | None) -> list[str]:
    """单文件明细里的错误（report.json 不含 errors）。"""
    errors: list[str] = []
    for err in (result or {}).get("errors") or []:
        if isinstance(err, dict):
            message = err.get("message") or err.get("text") or err.get("value")
            if message:
                errors.append(str(message))
        elif err:
            errors.append(str(err))
    return errors


def _decode_attachment_body(attachment: dict[str, Any]) -> Any:
    """附件 body 是脚本 JSON.stringify 后的字符串；解析失败则原样保留。"""
    body = attachment.get("body")
    if body is None:
        return None
    if isinstance(body, (dict, list)):
        return body
    text = str(body)
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return {"raw": text[:4000]}


def _extract_io(result: dict[str, Any] | None) -> tuple[Any, Any]:
    """从 result.attachments 提取入参/出参（脚本侧 testInfo.attach 留证，已脱敏）。"""
    request_io: Any = None
    response_io: Any = None
    for attachment in (result or {}).get("attachments") or []:
        if not isinstance(attachment, dict):
            continue
        name = str(attachment.get("name") or "")
        if name == IO_REQUEST_ATTACHMENT:
            request_io = _decode_attachment_body(attachment)
        elif name == IO_RESPONSE_ATTACHMENT:
            response_io = _decode_attachment_body(attachment)
    return request_io, response_io


def _detail_results(
    details: dict[str, dict[str, Any]]
) -> dict[str, dict[str, dict[str, Any]]]:
    """fileId → {testId: 首个 result}，用于补齐 report.json 缺失的明细。"""
    index: dict[str, dict[str, dict[str, Any]]] = {}
    for file_id, doc in (details or {}).items():
        tests = doc.get("tests") if isinstance(doc, dict) else None
        if not isinstance(tests, list):
            continue
        per_test: dict[str, dict[str, Any]] = {}
        for test in tests:
            if not isinstance(test, dict):
                continue
            results = test.get("results")
            first = (
                results[0]
                if isinstance(results, list) and results and isinstance(results[0], dict)
                else {}
            )
            per_test[str(test.get("testId") or test.get("title") or "")] = first
        index[file_id] = per_test
    return index


def read_report_payloads(
    html: bytes | str,
) -> tuple[dict[str, Any] | None, dict[str, dict[str, Any]]]:
    """一次解包内嵌 zip：返回 (report.json, {fileId: 单文件明细})。

    Playwright 的 report.json 只带统计与结果骨架，步骤/错误/附件都在
    ``<fileId>.json`` 单文件明细里；明细缺失时由调用方回退到 report.json。
    """
    blob = extract_embedded_zip(html)
    if not blob:
        return None, {}
    report: dict[str, Any] | None = None
    details: dict[str, dict[str, Any]] = {}
    try:
        with zipfile.ZipFile(io.BytesIO(blob)) as archive:
            for name in archive.namelist():
                if not name.endswith(".json"):
                    continue
                if name == "report.json":
                    try:
                        report = json.loads(archive.read(name).decode("utf-8"))
                    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
                        logger.warning("[report-summary] 解析内嵌 report.json 失败: %s", exc)
                        return None, {}
                    continue
                try:
                    details[Path(name).stem] = json.loads(archive.read(name).decode("utf-8"))
                except (json.JSONDecodeError, UnicodeDecodeError) as exc:
                    logger.warning("[report-summary] 解析单文件明细 %s 失败: %s", name, exc)
    except zipfile.BadZipFile as exc:
        logger.warning("[report-summary] 内嵌 zip 损坏: %s", exc)
        return None, {}
    if report is None:
        logger.warning("[report-summary] 内嵌 zip 中缺少 report.json")
    return report, details


class ReportParseError(ValueError):
    """报告无法解析（格式不符或负载损坏）。"""


# ---------------------------------------------------------------------------
# 1. 从 HTML 中提取内嵌 zip
# ---------------------------------------------------------------------------

def extract_embedded_zip(html: bytes | str) -> bytes | None:
    """从 Playwright HTML 报告中取出内嵌 zip 的原始字节；没有则返回 None。"""
    text = html.decode("utf-8", errors="replace") if isinstance(html, bytes) else html
    idx = text.find(EMBEDDED_ZIP_PREFIX)
    if idx == -1:
        return None

    start = idx + len(EMBEDDED_ZIP_PREFIX)
    end = start
    while end < len(text) and text[end] in _B64_CHARS:
        end += 1

    payload = text[start:end]
    if not payload:
        return None
    try:
        return base64.b64decode(payload)
    except (binascii.Error, ValueError) as exc:
        logger.warning("[report-summary] 内嵌 zip 负载 base64 解码失败: %s", exc)
        return None


def extract_report_json(html: bytes | str) -> dict[str, Any] | None:
    """解析出 Playwright 的 ``report.json``；失败返回 None。"""
    blob = extract_embedded_zip(html)
    if not blob:
        return None
    try:
        with zipfile.ZipFile(io.BytesIO(blob)) as archive:
            if "report.json" not in archive.namelist():
                logger.warning("[report-summary] 内嵌 zip 中缺少 report.json")
                return None
            return json.loads(archive.read("report.json").decode("utf-8"))
    except (zipfile.BadZipFile, json.JSONDecodeError, KeyError) as exc:
        logger.warning("[report-summary] 解析内嵌 report.json 失败: %s", exc)
        return None


# ---------------------------------------------------------------------------
# 2. 归一化
# ---------------------------------------------------------------------------

def _epoch_ms_to_iso(value: Any) -> str | None:
    if not isinstance(value, (int, float)):
        return None
    try:
        return datetime.fromtimestamp(value / 1000.0, tz=timezone.utc).isoformat()
    except (OverflowError, OSError, ValueError):
        return None


def _iter_result_entries(test: dict[str, Any]) -> list[dict[str, Any]]:
    results = test.get("results")
    return [r for r in results if isinstance(r, dict)] if isinstance(results, list) else []


def _test_duration_ms(test: dict[str, Any]) -> int:
    total = 0
    for result in _iter_result_entries(test):
        duration = result.get("duration")
        if isinstance(duration, (int, float)):
            total += int(duration)
    if total:
        return total
    duration = test.get("duration")
    return int(duration) if isinstance(duration, (int, float)) else 0


def _test_errors(test: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    for result in _iter_result_entries(test):
        for err in result.get("errors") or []:
            if isinstance(err, dict):
                message = err.get("message") or err.get("text") or err.get("value")
                if message:
                    errors.append(str(message))
            elif err:
                errors.append(str(err))
    return errors


def _test_steps(
    test: dict[str, Any], result: dict[str, Any] | None = None
) -> list[dict[str, Any]]:
    """取首个 result 的步骤（Playwright 的 steps 挂在 result 上）。"""
    raw_steps = _collect_steps(test, result)
    if not raw_steps:
        return []

    steps: list[dict[str, Any]] = []
    for step in raw_steps:
        if not isinstance(step, dict):
            continue
        duration = step.get("duration")
        steps.append({
            "title": str(step.get("title") or step.get("name") or "未命名步骤"),
            "status": "failed" if step.get("error") else "passed",
            "duration_ms": int(duration) if isinstance(duration, (int, float)) else 0,
        })
    return steps


def _resolve_status(test: dict[str, Any]) -> str:
    """outcome 优先；无 outcome 时回退到首个 result 的 status。"""
    outcome = str(test.get("outcome") or "").lower()
    if outcome in OUTCOME_TO_STATUS:
        return OUTCOME_TO_STATUS[outcome]

    entries = _iter_result_entries(test)
    if entries:
        raw_status = str(entries[0].get("status") or "").lower()
        if raw_status == "timedout":
            return "timedOut"
        if raw_status in {"passed", "failed", "skipped"}:
            return raw_status
    return "skipped" if test.get("ok") is False and not entries else "passed"


def build_result_summary(
    report: dict[str, Any], details: dict[str, dict[str, Any]] | None = None
) -> dict[str, Any]:
    """把 Playwright ``report.json`` 归一化为前端口径的结果摘要。"""
    stats = report.get("stats") if isinstance(report.get("stats"), dict) else {}
    files = report.get("files") if isinstance(report.get("files"), list) else []
    detail_results = _detail_results(details or {})

    tests_out: list[dict[str, Any]] = []
    counts = {"passed": 0, "failed": 0, "skipped": 0, "timedOut": 0}
    flaky = 0

    for file_entry in files:
        if not isinstance(file_entry, dict):
            continue
        file_name = str(file_entry.get("fileName") or file_entry.get("fileId") or "")
        per_test = detail_results.get(str(file_entry.get("fileId") or ""), {})
        raw_tests = file_entry.get("tests")
        if not isinstance(raw_tests, list):
            continue
        for test in raw_tests:
            if not isinstance(test, dict):
                continue
            result = per_test.get(str(test.get("testId") or ""), {})
            status = _resolve_status(test)
            counts[status] = counts.get(status, 0) + 1
            if str(test.get("outcome") or "").lower() == "flaky":
                flaky += 1
            request_io, response_io = _extract_io(result)
            tests_out.append({
                "title": str(test.get("title") or "未命名测试"),
                "file": file_name,
                "status": status,
                "duration_ms": _test_duration_ms(test),
                "errors": _result_errors(result) or _test_errors(test),
                "steps": _test_steps(test, result),
                # ---- 详细内容：请求 / 断言 / 原因分析（供核对） ----
                "location": _extract_location(test.get("location")),
                "api_steps": _extract_api_steps(test, result),
                "request": request_io,
                "response": response_io,
                "assertions": _extract_assertions(_first_error_message(test, result)),
                "failure_analysis": _classify_failure(test, result),
            })

    total = stats.get("total")
    total = int(total) if isinstance(total, (int, float)) else len(tests_out)

    duration = report.get("duration")
    duration_ms = int(duration) if isinstance(duration, (int, float)) else sum(
        t["duration_ms"] for t in tests_out
    )

    project_names = report.get("projectNames")
    browser = (
        str(project_names[0])
        if isinstance(project_names, list) and project_names
        else "API request"
    )

    return {
        # 与前端 parseResultSummary 对齐的键
        "total": total,
        "passed": counts["passed"],
        "failed": counts["failed"],
        "skipped": counts["skipped"],
        "timed_out": counts["timedOut"],
        "duration_ms": duration_ms,
        "tests": tests_out,
        # 报告级分析结论（确定性规则：准确性评估 + 原因分析）
        "analysis": _build_analysis(tests_out),
        # 附加信息（前端忽略，便于排查）
        "flaky": flaky,
        "browser": browser,
        "start_time": _epoch_ms_to_iso(report.get("startTime")),
        "raw_stats": stats,
        "project_names": project_names if isinstance(project_names, list) else [],
        "source": "playwright_html_report",
    }


def summarize_html_report(html: bytes | str) -> dict[str, Any]:
    """一步到位：HTML 报告 → 结果摘要。

    Raises:
        ReportParseError: 报告不是自包含 Playwright HTML 报告，或负载已损坏。
    """
    report, details = read_report_payloads(html)
    if report is None:
        raise ReportParseError(
            "无法从报告中提取 Playwright report.json"
            "（报告可能不是自包含 HTML，或已被截断/损坏）"
        )
    return build_result_summary(report, details)


def summarize_report_archive(archive_bytes: bytes) -> dict[str, Any]:
    """从报告 ZIP 中定位 index.html 并解析。

    rev63（执行证据闭环）：Web 执行链（web_test_service）会把 runner 的
    stdout/stderr 落盘为 ``execution_stdout.log`` / ``execution_stderr.log``
    随 ZIP 持久化。ZIP 里存在执行日志时：

    - 解析其中的 ``COMPILED_ASSERTION`` 行（与用例级落库同源同口径），
      作为 ``compiled_assertions`` 附加到摘要与对应测试条目，
      逐断言证据（pass/unverified/blocked + observed）从此可回查；
    - index.html 缺失或不是 Playwright 自包含报告时，退化为基于执行日志的
      最小摘要（不再直接判"不可解析"——断言证据仍然有效）。

    Raises:
        ReportParseError: ZIP 中既没有可解析报告也没有执行日志。
    """
    html: bytes | None = None
    stdout_log: str | None = None
    screenshot_names: list[str] = []
    try:
        with zipfile.ZipFile(io.BytesIO(archive_bytes)) as archive:
            names = archive.namelist()
            candidates = [
                n for n in names
                if n.lower().endswith("index.html") and not n.startswith("__MACOSX")
            ]
            # 取层级最浅的 index.html，避免命中子目录里的同名文件
            candidates.sort(key=lambda n: (n.count("/"), len(n)))
            if candidates:
                html = archive.read(candidates[0])
            stdout_log = _read_archive_text(archive, EXECUTION_STDOUT_LOG)
            screenshot_names = sorted({
                Path(n).name for n in names
                if not n.startswith("__MACOSX")
                and n.lower().endswith((".png", ".jpg", ".jpeg"))
            })
    except zipfile.BadZipFile as exc:
        raise ReportParseError(f"报告不是有效的 ZIP: {exc}") from exc

    if html is not None and b"playwrightReportBase64" in html:
        summary = summarize_html_report(html)
    elif stdout_log:
        summary = _stdout_log_summary(stdout_log)
    elif html is None:
        raise ReportParseError("报告 ZIP 中未找到 index.html")
    else:
        raise ReportParseError("报告 ZIP 中的 index.html 不是 Playwright 自包含报告")

    if stdout_log:
        _attach_compiled_assertions(summary, stdout_log)
        summary["report_files"] = {
            "has_execution_stdout": True,
            "screenshots": screenshot_names[:50],
        }
    return summary


#: Web 执行链（web_test_service）落盘的执行日志文件名。
EXECUTION_STDOUT_LOG = "execution_stdout.log"


def _read_archive_text(archive: zipfile.ZipFile, suffix: str) -> str | None:
    """读取 zip 内第一个以 suffix 结尾（层级最浅）的文本文件；缺失返回 None。"""
    candidates = [
        n for n in archive.namelist()
        if n.lower().endswith(suffix) and not n.startswith("__MACOSX")
    ]
    if not candidates:
        return None
    candidates.sort(key=lambda n: (n.count("/"), len(n)))
    try:
        return archive.read(candidates[0]).decode("utf-8", errors="replace")
    except (KeyError, zipfile.BadZipFile) as exc:
        logger.warning("[report-summary] 读取 %s 失败: %s", suffix, exc)
        return None


#: COMPILED_ASSERTION outcome → 统计桶。
_OUTCOME_BUCKET = {"pass": "passed", "unverified": "unverified", "blocked": "blocked"}


def _bucket_counts(items: list[dict[str, Any]]) -> dict[str, int]:
    counts: dict[str, int] = {
        "total": len(items), "passed": 0, "unverified": 0, "blocked": 0, "other": 0,
    }
    for item in items:
        bucket = _OUTCOME_BUCKET.get(str(item.get("outcome") or ""), "other")
        counts[bucket] += 1
    return counts


def _parse_compiled_assertion_items(stdout_text: str) -> list[dict[str, Any]]:
    """从执行 stdout 解析 COMPILED_ASSERTION 逐条断言。

    解析复用 Web 侧 ``web_case_result_mapper.parse_compiled_assertions``，
    与用例级结果落库（persist_case_level_results）同源同口径。
    """
    if not stdout_text or "COMPILED_ASSERTION" not in stdout_text:
        return []
    try:
        from app.services.web_case_result_mapper import parse_compiled_assertions
        return parse_compiled_assertions(stdout_text)
    except Exception as exc:  # noqa: BLE001 —— 解析失败不影响既有摘要
        logger.warning("[report-summary] 解析 COMPILED_ASSERTION 失败: %s", exc)
        return []


def _attach_compiled_assertions(summary: dict[str, Any], stdout_text: str) -> None:
    """把逐断言证据附加到摘要与对应测试条目（按用例标识匹配，单测试全量归属）。"""
    items = _parse_compiled_assertion_items(stdout_text)
    if not items:
        return
    block: dict[str, Any] = {
        "counts": _bucket_counts(items),
        "items": items,
        "source": EXECUTION_STDOUT_LOG,
    }
    summary["compiled_assertions"] = block

    tests = summary.get("tests") or []
    if len(tests) == 1:
        # 单测试脚本（一次编译执行只产出一个 test，批次断言的 case_id 各不相同）：
        # 全部断言归属唯一条目。
        tests[0]["compiled_assertions"] = {"counts": _bucket_counts(items), "items": items}
        return
    for test in tests:
        title = str(test.get("title") or "")
        matched = [
            it for it in items
            if it.get("case_id") and str(it["case_id"]) in title
        ]
        if matched:
            test["compiled_assertions"] = {
                "counts": _bucket_counts(matched),
                "items": matched,
            }


def _stdout_log_summary(stdout_text: str) -> dict[str, Any]:
    """无 Playwright 自包含报告时，基于执行日志产出最小但形状一致的摘要。

    借助 build_result_summary 归一化（补齐 analysis 等键），outcome 映射：
    全部 pass → expected（通过）；出现 blocked → unexpected（失败）；
    其余（存在 unverified）→ skipped。
    """
    items = _parse_compiled_assertion_items(stdout_text)
    counts = _bucket_counts(items)
    if items and counts["blocked"]:
        outcome = "unexpected"
    elif items and not counts["unverified"] and not counts["other"]:
        outcome = "expected"
    else:
        outcome = "skipped"
    title = "Web 测试执行（HTML 报告缺失，结果来自执行日志）"
    test_entry: dict[str, Any] = {
        "testId": "execution-stdout",
        "title": title,
        "outcome": outcome,
        "duration": 0,
        "location": None,
    }
    report = {
        "stats": {"total": 1},
        "files": [{
            "fileId": "execution-stdout",
            "fileName": EXECUTION_STDOUT_LOG,
            "tests": [test_entry],
        }],
        "duration": 0,
        "startTime": None,
        "projectNames": ["webwright"],
    }
    summary = build_result_summary(report, {
        "execution-stdout": {"tests": [{"testId": "execution-stdout", "results": [{}]}]},
    })
    summary["source"] = "execution_stdout_log"
    return summary


__all__ = [
    "EMBEDDED_ZIP_PREFIX",
    "OUTCOME_TO_STATUS",
    "ReportParseError",
    "_build_analysis",
    "_classify_failure",
    "_extract_api_steps",
    "_extract_assertions",
    "build_result_summary",
    "extract_embedded_zip",
    "extract_report_json",
    "summarize_html_report",
    "summarize_report_archive",
]
