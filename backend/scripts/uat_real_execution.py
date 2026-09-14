# -*- coding: utf-8 -*-
"""真实 UAT 业务执行（低风险查询接口）。

由用户提供的真实参数
--------------------
- 三份样本：`YX CRM API Spec For Non POS (1).xlsx`、`美心.postman_collection.json`、
  `UAT_eatizen HK CPO.postman_environment.json`（路径见 `tests/sample_paths.py`）
- 域名：`https://console.uat.eatizen.com.hk/yundt-saas-gateway`
- 测试手机号：`18320101265`

行为约定（重要）
----------------
1. **只调用低风险查询类接口**（默认 `api_checkMobileDuplicate`，查重）；
2. **不**调用任何写操作（注册/发放/删除等）；
3. 签名凭据仅从环境变量读取（`MX_APPID` / `MX_APPSECRET`），**不落库、不写文件**；
4. 凭据缺失时返回 **blocked**（不是失败）——把"缺前置条件"与"业务失败"严格区分；
5. 请求耗时、状态码、响应摘要与脱敏后的请求信息写入结果文件，便于归档。

用法::

    cd backend
    # 先看前置状态（不发起请求）
    ..\\.venv\\Scripts\\python.exe -X utf8 scripts\\uat_real_execution.py --check

    # 凭据齐备后实际执行
    set MX_APPID=xxx && set MX_APPSECRET=yyy
    ..\\.venv\\Scripts\\python.exe -X utf8 scripts\\uat_real_execution.py --run --endpoint api_checkMobileDuplicate
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from tests.sample_paths import (  # noqa: E402
    COLLECTION_PATH,
    ENVIRONMENT_PATH,
    EXCEL_PATH,
    UAT_TEST_MOBILE,
    sample_files,
    uat_base_url,
    uat_credential_status,
    uat_sign_credentials,
)

RESULT_FILE = BACKEND / "uat_real_execution_result.json"

#: 允许执行的接口白名单（只读查询类）。**不在白名单的一律不执行。**
READONLY_ENDPOINTS = {
    "api_checkMobileDuplicate": {
        "method": "POST",
        "path": "/api/json/api_checkMobileDuplicate",
        "body_builder": lambda: {"Touchpoint": "W", "Language": "TC",
                                 "MobilePhone": UAT_TEST_MOBILE},
        "risk": "read-only（手机号查重，不产生数据变更）",
    },
}

MISSING = (None, None, None)


def _sign(appid: str, secret: str, body: dict) -> dict:
    """按美心规则生成签名参数（md5(appid+appsercret+ts+msg)，32 位小写）。"""
    from app.services.signing.mx_md5_signer import mx_md5_sign

    ts = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
    msg = json.dumps(body, ensure_ascii=False, separators=(",", ":"))
    return {
        "appid": appid,
        "ts": ts,
        "sign": mx_md5_sign(appid, secret, ts, msg),
        "_msg": msg,
    }


def _redact(payload: dict) -> dict:
    """结果归档前的脱敏：签名/密钥类字段只留长度。"""
    out = {}
    for key, value in payload.items():
        if any(token in key.lower() for token in ("sign", "secret", "password", "token")):
            out[key] = f"<len={len(str(value))}>"
        else:
            out[key] = value
    return out


def main() -> int:
    parser = argparse.ArgumentParser(description="真实 UAT 业务执行（低风险查询）")
    parser.add_argument("--check", action="store_true", help="只检查前置条件，不发起请求")
    parser.add_argument("--run", action="store_true", help="实际执行")
    parser.add_argument("--endpoint", default="api_checkMobileDuplicate")
    parser.add_argument("--timeout", type=int, default=30)
    args = parser.parse_args()

    status = uat_credential_status()
    print("=" * 72)
    print("真实 UAT 执行前置状态")
    print("=" * 72)
    print(f"目标域名        : {status['base_url']}")
    print(f"测试手机号      : {status['test_mobile']}")
    print(f"appid 已配置    : {status['appid_configured']}")
    print(f"appsecret 已配置: {status['appsecret_configured']}")
    print(f"所需环境变量    : {', '.join(status['required_env_vars'])}")

    missing_files = [str(p) for p in sample_files().values() if not p.exists()]
    print(f"样本文件缺失    : {missing_files or '无'}")

    if missing_files:
        print()
        print("结果: blocked（样本文件缺失）")
        return 3

    if not status["ready"]:
        print()
        print("结果: **blocked** —— 缺少签名凭据，未发起任何第三方请求。")
        print("      环境文件里只有 api-appid / api-secret 的引用，没有 appid/appsercret 的值")
        print("      （这正是导入时 env_missing 高等级冲突的来源）。")
        print(f"      请注入环境变量后重试：{', '.join(status['required_env_vars'])}")
        return 3

    if args.check and not args.run:
        print()
        print("结果: ready（凭据齐备，可执行 --run）")
        return 0

    if args.endpoint not in READONLY_ENDPOINTS:
        print(f"结果: 拒绝执行 —— `{args.endpoint}` 不在只读白名单内: "
              f"{sorted(READONLY_ENDPOINTS)}")
        return 2

    spec = READONLY_ENDPOINTS[args.endpoint]
    appid, secret = uat_sign_credentials()
    assert appid and secret

    body = spec["body_builder"]()
    signed = _sign(appid, secret, body)
    url = f"{uat_base_url()}{spec['path']}"
    query = {k: v for k, v in signed.items() if k != "_msg"}

    print()
    print(f"执行: POST {url}")
    print(f"  请求体（脱敏归档用）: {_redact(signed)}")

    started = time.time()
    outcome: dict = {
        "endpoint": args.endpoint,
        "url": url,
        "method": spec["method"],
        "risk": spec["risk"],
        "started_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "request_summary": _redact(signed),
        "response_status": None,
        "response_summary": None,
        "duration_ms": None,
        "status": "failed",
        "error": None,
    }

    request = urllib.request.Request(
        f"{url}?{'&'.join(f'{k}={v}' for k, v in query.items())}",
        data=signed["_msg"].encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=args.timeout) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
            outcome["response_status"] = resp.status
            outcome["status"] = "completed" if resp.status == 200 else "failed"
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="replace")
        outcome["response_status"] = exc.code
        outcome["status"] = "failed"
        outcome["error"] = f"HTTPError {exc.code}"
    except Exception as exc:  # noqa: BLE001
        raw = ""
        outcome["status"] = "failed"
        outcome["error"] = f"{type(exc).__name__}: {exc}"

    outcome["duration_ms"] = int((time.time() - started) * 1000)
    try:
        parsed = json.loads(raw)
        outcome["response_summary"] = {
            k: parsed[k] for k in ("Code", "Message") if k in parsed
        } or {"keys": sorted(parsed)[:8]} if isinstance(parsed, dict) else {"type": "list"}
    except json.JSONDecodeError:
        outcome["response_summary"] = {"raw_prefix": raw[:200]}

    RESULT_FILE.write_text(
        json.dumps(outcome, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print()
    print(f"状态: {outcome['status']}  HTTP={outcome['response_status']}  "
          f"{outcome['duration_ms']}ms")
    print(f"响应摘要: {outcome['response_summary']}")
    if outcome["error"]:
        print(f"错误: {outcome['error']}")
    print(f"结果已写入: {RESULT_FILE.name}")
    return 0 if outcome["status"] == "completed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
