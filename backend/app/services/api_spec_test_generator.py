"""契约驱动的需求分析 / 测试计划 / Playwright 脚本生成。

输入统一接口数据模型（api_spec_normalizer 产出，或从 APIEndpoint.custom_config 还原），输出：
- 需求分析（业务目标/输入约束/响应契约/业务分支/前置条件/需求疑问）
- 测试计划（正常/必填/边界/枚举/鉴权用例，每项含依据、数据需求、明确断言）
- Playwright 脚本（统一处理 ts、JSON 序列化、MD5 签名、请求发送）

原则：
- 无依据的预期标为"待确认"，不生成严格业务断言。
- 签名用的 msg 与实际发送 body 完全一致。
- 密钥不进入脚本（用环境变量占位），报告侧脱敏。
"""

from __future__ import annotations

from typing import Any

from app.services.signing.mx_md5_signer import mx_md5_js_helper


# ---------- 需求分析 ----------

def analyze_requirement(spec: dict[str, Any]) -> dict[str, Any]:
    """基于统一模型产出需求分析。区分 Data.Existed='MX1' 与 JSON null。"""
    req_fields = spec.get("request", {}).get("fields", [])
    resp_fields = spec.get("response", {}).get("fields", [])
    required = [f for f in req_fields if f.get("required")]
    optional = [f for f in req_fields if not f.get("required")]

    # 业务分支：从响应字段的示例/差异推导（如 Existed 的 MX1/null）
    branches = []
    for f in resp_fields:
        diff = (f.get("diff") or "") + " " + (f.get("description") or "")
        example = f.get("example") or ""
        if "MX1" in diff or "MX1" in example:
            branches.append({
                "field": f["name"],
                "when": f"{f['name']} == 'MX1'",
                "then": "资源已存在（命中 MX1）",
                "source": f.get("source"),
            })
            branches.append({
                "field": f["name"],
                "when": f"{f['name']} is null",
                "then": "资源不存在（返回 JSON null，注意区分字符串 'null'）",
                "source": f.get("source"),
            })

    questions = [
        {"text": i["message"], "source": i.get("source")}
        for i in spec.get("pending_confirmations", [])
    ]

    return {
        "api_name": spec.get("name"),
        "business_goal": spec.get("scenario") or spec.get("summary"),
        "input_constraints": {
            "required": [{"name": f["name"], "type": f.get("type"), "length": f.get("length")} for f in required],
            "optional": [{"name": f["name"], "type": f.get("type"), "length": f.get("length")} for f in optional],
        },
        "response_contract": {
            "fields": [{"name": f["name"], "type": f.get("type"), "example": f.get("example")} for f in resp_fields],
            "error_codes": spec.get("response", {}).get("error_codes", []),
        },
        "business_branches": branches,
        "preconditions": _derive_preconditions(spec),
        "open_questions": questions,
        "source": spec.get("source"),
    }


def _derive_preconditions(spec: dict[str, Any]) -> list[str]:
    pre = []
    auth = spec.get("auth") or {}
    if auth.get("type") == "mx_md5":
        pre.append("需提供有效 appid / appsercret（经环境变量注入，不入库明文）")
        pre.append(f"ts 时间戳格式 {auth.get('ts_format', 'yyyyMMddHHmmss')}，时区 {auth.get('ts_timezone', 'Asia/Hong_Kong')}")
    for i in spec.get("pending_confirmations", []):
        if i.get("type") == "placeholder_url":
            pre.append("需确认真实环境 base_url（当前文档为占位地址）")
            break
    return pre


# ---------- 测试计划 ----------

def generate_test_plan(spec: dict[str, Any]) -> dict[str, Any]:
    """生成测试计划：正常/必填/边界/枚举/鉴权用例，每项含依据、数据需求、断言。"""
    cases: list[dict[str, Any]] = []
    req_fields = spec.get("request", {}).get("fields", [])
    resp_fields = spec.get("response", {}).get("fields", [])
    has_error_codes = bool(spec.get("response", {}).get("error_codes"))

    def src(f: dict) -> dict:
        return {"sheet": (f.get("source") or {}).get("sheet"), "row": (f.get("source") or {}).get("row")}

    # 1. 正常用例：所有必填字段给合法示例值
    normal_data = {f["name"]: f.get("example") or _default_value(f) for f in req_fields}
    cases.append({
        "category": "normal",
        "title": f"正常调用 {spec.get('name')}",
        "data": normal_data,
        "data_needs": [f["name"] for f in req_fields if f.get("required")],
        "assertions": _base_assertions(resp_fields, has_error_codes),
        "basis": {"type": "contract", "source": spec.get("source")},
    })

    # 2. 必填用例：逐个缺失必填字段
    for f in req_fields:
        if not f.get("required"):
            continue
        cases.append({
            "category": "required",
            "title": f"缺失必填字段 {f['name']}",
            "data": {k: v for k, v in normal_data.items() if k != f["name"]},
            "missing": f["name"],
            "data_needs": [],
            "assertions": [{"target": "Code", "expect": "nonzero_or_error", "note": "缺必填应报错，具体错误码待确认" if not has_error_codes else "refer error_codes"}],
            "basis": {"type": "field_required", "source": src(f)},
        })

    # 3. 边界用例：长度边界
    for f in req_fields:
        if not f.get("length"):
            continue
        over = _default_value(f)
        cases.append({
            "category": "boundary",
            "title": f"字段 {f['name']} 超长（>{f['length']}）",
            "data": {**normal_data, f["name"]: (over or "x") * (f["length"] + 1)},
            "data_needs": [],
            "assertions": [{"target": "Code", "expect": "nonzero_or_error", "note": "超长应校验失败，具体行为待确认"}],
            "basis": {"type": "field_length", "length": f["length"], "source": src(f)},
        })

    # 4. 枚举用例：描述里含枚举值的字段
    for f in req_fields:
        enums = _extract_enums(f)
        if not enums:
            continue
        for ev in enums:
            cases.append({
                "category": "enum",
                "title": f"字段 {f['name']} 取枚举值 {ev}",
                "data": {**normal_data, f["name"]: ev},
                "data_needs": [],
                "assertions": _base_assertions(resp_fields, has_error_codes),
                "basis": {"type": "field_enum", "value": ev, "source": src(f)},
            })

    # 5. 鉴权用例：签名错误应拒绝
    if (spec.get("auth") or {}).get("type") == "mx_md5":
        cases.append({
            "category": "auth",
            "title": "签名错误（篡改 sign）应被拒绝",
            "data": normal_data,
            "tamper": "sign",
            "data_needs": [],
            "assertions": [{"target": "Code", "expect": "auth_error", "note": "验签失败错误码待确认（文档未提供鉴权错误码）"}],
            "basis": {"type": "auth_sign", "source": spec.get("source")},
        })

    # 无依据的预期标待确认
    for c in cases:
        for a in c["assertions"]:
            if "待确认" in (a.get("note") or ""):
                a["pending"] = True

    return {
        "api_name": spec.get("name"),
        "total_cases": len(cases),
        "cases": cases,
        "pending_count": sum(1 for c in cases for a in c["assertions"] if a.get("pending")),
    }


def _base_assertions(resp_fields: list[dict], has_error_codes: bool) -> list[dict]:
    assertions = [
        {"target": "Code", "expect": "0", "note": "成功返回码为 0"},
        {"target": "HTTP", "expect": "2xx", "note": "HTTP 状态码 2xx"},
    ]
    if not has_error_codes:
        assertions.append({"target": "error_codes", "expect": "unknown", "note": "文档未提供错误码表，失败分支断言待确认", "pending": True})
    return assertions


def _default_value(f: dict) -> Any:
    t = (f.get("type") or "").lower()
    if "int" in t:
        return 1
    if "date" in t:
        return "2026-01-01T00:00:00"
    return f.get("example") or (f["name"][:1] or "x")


def _extract_enums(f: dict) -> list[str]:
    """从描述里提取单字符/短枚举值（如 M/W、TC/EN、MX1/ARO/CAKE）。"""
    import re
    text = f.get("description") or ""
    enums = set()
    # "X – desc" 或 "X - desc" 或 "X –desc" 形式
    for m in re.finditer(r"^\s*([A-Za-z0-9]{1,5})\s*[–—-]\s", text, re.MULTILINE):
        enums.add(m.group(1))
    return sorted(enums)


# ---------- Playwright 脚本生成 ----------

def generate_playwright_script(spec: dict[str, Any], env_config: dict[str, Any]) -> str:
    """生成 Playwright 脚本。

    env_config 关键项：
      base_url            完整环境地址（必填）
      appid_env           appid 环境变量名（默认 MX_APPID）
      secret_env          appsercret 环境变量名（默认 MX_APPSECRET）
      timezone            时区（默认 Asia/Hong_Kong，经 TZ 环境变量）
      existing_mobile     已存在手机号（查重接口"存在"分支）
      not_existing_mobile 不存在手机号（"不存在"分支）
    """
    auth = spec.get("auth") or {}
    if auth.get("type") != "mx_md5":
        raise ValueError("当前脚本生成仅支持 mx_md5 鉴权")

    base_url = (env_config.get("base_url") or "").rstrip("/")
    if not base_url:
        raise ValueError("缺少 env_config.base_url（环境地址），脚本生成被阻塞")

    appid_env = env_config.get("appid_env", "MX_APPID")
    secret_env = env_config.get("secret_env", "MX_APPSECRET")
    path = spec.get("path") or ""
    url = f"{base_url}{path}"
    method = spec.get("http_method") or "POST"

    req_fields = spec.get("request", {}).get("fields", [])
    required_names = [f["name"] for f in req_fields if f.get("required")]
    # 请求体：必填字段给示例/默认值，可选字段（如 CountryCode）按需
    body = {f["name"]: f.get("example") or _default_value(f) for f in req_fields if f.get("required")}

    plan = generate_test_plan(spec)
    helper = mx_md5_js_helper()

    lines = []
    lines.append("/**")
    lines.append(f" * 自动生成：{spec.get('name')} 契约测试（来源 sheet: {(spec.get('source') or {}).get('sheet')}）")
    lines.append(" * 签名：MD5(appid+appsercret+ts+msg)，msg 与实际发送 body 完全一致")
    lines.append(" */")
    lines.append("const { test, expect } = require('@playwright/test');")
    lines.append(helper)
    lines.append("")
    lines.append(f"const BASE_URL = {json_dumps(url)};")
    lines.append(f"const APPID = process.env.{appid_env};")
    lines.append(f"const APPSECRET = process.env.{secret_env};")
    lines.append(f"const REQUIRED_FIELDS = {json_dumps(required_names)};")
    lines.append("")
    lines.append("function buildBody(overrides = {}) {")
    lines.append(f"  return Object.assign({json_dumps(body)}, overrides);")
    lines.append("}")
    lines.append("")
    lines.append("async function callApi(request, body, { tamperSign = false } = {}) {")
    lines.append("  if (!APPID || !APPSECRET) test.skip(true, '缺 MX_APPID/MX_APPSECRET 环境变量');")
    lines.append("  const ts = mxTimestamp(new Date());")
    lines.append("  const msg = canonicalMsg(body);")
    lines.append("  let sign = mxMd5Sign(APPID, APPSECRET, ts, msg);")
    lines.append("  if (tamperSign) sign = sign.split('').reverse().join('');")
    lines.append("  return request.post(BASE_URL, {")
    lines.append("    headers: {")
    lines.append("      'Content-Type': 'application/json',")
    lines.append("      appid: APPID, ts, sign,")
    lines.append("    },")
    lines.append("    data: msg,  // 与签名 msg 完全一致的字符串")
    lines.append("  });")
    lines.append("}")
    lines.append("")

    # 为每个用例生成 test
    for i, c in enumerate(plan["cases"], 1):
        title = f"[{c['category']}] {c['title']}"
        lines.append(f"test({json_dumps(f'{i:02d} {title}')}, async ({{ request }}) => {{")
        if c["category"] == "required" and c.get("missing"):
            lines.append(f"  const body = buildBody(); delete body[{json_dumps(c['missing'])}];")
            lines.append("  const res = await callApi(request, body);")
            lines.append("  expect(res.status()).toBeGreaterThanOrEqual(200);")
        elif c["category"] == "auth":
            lines.append("  const body = buildBody();")
            lines.append("  const res = await callApi(request, body, { tamperSign: true });")
            lines.append("  // 验签失败应非 2xx 或返回非 0 错误码（具体错误码待确认）")
            lines.append("  expect([401, 403, 400]).toContain(res.status());")
        elif c["category"] == "boundary":
            lines.append(f"  const body = buildBody({json_dumps({k: v for k, v in c['data'].items() if k in body or k == c['title'].split()[1]})});")
            lines.append("  const res = await callApi(request, body);")
            lines.append("  expect(res.status()).toBeGreaterThanOrEqual(200);")
        else:
            overrides = {k: v for k, v in c.get("data", {}).items() if k in body}
            lines.append(f"  const body = buildBody({json_dumps(overrides)});")
            lines.append("  const res = await callApi(request, body);")
            lines.append("  expect(res.status()).toBeGreaterThanOrEqual(200);")
            lines.append("  const json = await res.json().catch(() => null);")
            lines.append("  if (json && 'Code' in json) expect(String(json.Code)).toBe('0');")
        lines.append("});")
        lines.append("")

    return "\n".join(lines)


def json_dumps(obj: Any) -> str:
    import json
    return json.dumps(obj, ensure_ascii=False)
