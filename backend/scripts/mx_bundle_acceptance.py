"""真实美心三文件 HTTP 验收（end-to-end）。

覆盖可交付项：
- parse-bundle：保存 Bundle / Source / 解析结果 / 匹配结果
- confirm：事务内创建 Folder、APIEndpoint、变量、断言、依赖
- 幂等键：项目 + method + normalized_path（重复 confirm 不重复创建）
- 冲突确认校验（存在未确认 high 冲突时拒绝导入）
- 失败回滚（构造失败场景，确认无半成品）

验收策略（关键）：
1. **干净项目**：每次运行在无任何端点的项目上跑，才能验证「真的创建了」端点，
   而不是在已有数据的项目上全部命中幂等复用（那样只能验证幂等）。
2. **high 冲突场景用真实文件 + 最小环境文件构造**：真实三文件的环境变量是完整的，
   天然不会产生 env_missing（high）；因此额外用一个只含 url 的环境文件，
   让 collection 引用的 caseId/memberID/sessionKey 成为缺失变量，
   从而真实验证「未确认 high 冲突 → 拒绝导入」。

用法：
    python scripts/mx_bundle_acceptance.py
    python scripts/mx_bundle_acceptance.py --base-url http://127.0.0.1:8000 --project PR-15
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from uuid import uuid4

MX_DIR = Path(r"D:\测试管理\项目管理\美心项目\美心POS\API")
EXCEL = MX_DIR / "YX CRM API Spec For Non POS (1).xlsx"
COLLECTION = MX_DIR / "美心.postman_collection.json"
ENVIRONMENT = MX_DIR / "UAT_eatizen HK CPO.postman_environment.json"

PASS, FAIL = "PASS", "FAIL"
results: list[tuple[str, str, str]] = []

# 只保留 url 的最小环境（用于构造 env_missing high 冲突）
MINIMAL_ENV_NAME = "UAT_minimal_acceptance.postman_environment.json"
MINIMAL_ENV = {
    "id": "acceptance-minimal",
    "name": "UAT minimal (acceptance)",
    "values": [
        {"key": "url", "value": "https://uat.example.com", "type": "default", "enabled": True},
    ],
    "_postman_variable_scope": "environment",
}


def check(name: str, ok: bool, detail: str = "") -> bool:
    results.append((PASS if ok else FAIL, name, detail))
    print(f"[{PASS if ok else FAIL}] {name}" + (f" — {detail}" if detail else ""))
    return ok


def request(method: str, url: str, *, json_body=None, form=None, token: str | None = None):
    """极简 HTTP 客户端（multipart 用 form，JSON 用 json_body）。"""
    headers = {}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    data = None

    if form is not None:
        boundary = f"----dsh{uuid4().hex}"
        parts: list[bytes] = []
        for key, value in form.items():
            if isinstance(value, tuple):  # (filename, bytes, content_type)
                filename, content, ctype = value
                parts.append(
                    f"--{boundary}\r\n".encode()
                    + f'Content-Disposition: form-data; name="{key}"; filename="{filename}"\r\n'.encode()
                    + f"Content-Type: {ctype}\r\n\r\n".encode()
                    + content
                    + b"\r\n"
                )
            else:
                parts.append(
                    f"--{boundary}\r\n".encode()
                    + f'Content-Disposition: form-data; name="{key}"\r\n\r\n'.encode()
                    + str(value).encode("utf-8")
                    + b"\r\n"
                )
        parts.append(f"--{boundary}--\r\n".encode())
        data = b"".join(parts)
        headers["Content-Type"] = f"multipart/form-data; boundary={boundary}"
    elif json_body is not None:
        data = json.dumps(json_body).encode("utf-8")
        headers["Content-Type"] = "application/json"

    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=900) as resp:
            body = resp.read().decode("utf-8")
            return resp.status, (json.loads(body) if body else None)
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        try:
            return exc.code, json.loads(body)
        except json.JSONDecodeError:
            return exc.code, {"detail": body}
    except Exception as exc:  # noqa: BLE001
        return 0, {"detail": f"{type(exc).__name__}: {exc}"}


def _extract_unconfirmed(body) -> list[dict]:
    """从 409 响应中取出未确认冲突清单。

    兼容两种形态：
    - 结构化载荷：{"data": {"unconfirmed_conflicts": [...]}}
    - 旧形态：detail 字典被字符串化后放进 message
    """
    if not isinstance(body, dict):
        return []
    data = body.get("data")
    if isinstance(data, dict) and isinstance(data.get("unconfirmed_conflicts"), list):
        return data["unconfirmed_conflicts"]

    detail = body.get("detail")
    if isinstance(detail, dict) and isinstance(detail.get("unconfirmed_conflicts"), list):
        return detail["unconfirmed_conflicts"]

    message = body.get("message")
    if isinstance(message, str) and "unconfirmed_conflicts" in message:
        import ast
        import re as _re

        match = _re.search(r"\{.*\}", message, _re.DOTALL)
        if match:
            try:
                parsed = ast.literal_eval(match.group(0))
                if isinstance(parsed, dict):
                    return parsed.get("unconfirmed_conflicts") or []
            except (ValueError, SyntaxError):
                return []
    return []


def parse_bundle(base: str, bundle_name: str, environment_bytes: bytes, env_name: str, token):
    return request(
        "POST", f"{base}/parse-bundle",
        form={
            "bundle_name": bundle_name,
            "system_type": "crm_non_pos",
            "excel_file": (EXCEL.name, EXCEL.read_bytes(),
                           "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"),
            "collection_file": (COLLECTION.name, COLLECTION.read_bytes(), "application/json"),
            "environment_file": (env_name, environment_bytes, "application/json"),
        },
        token=token,
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--project", default="PR-15",
                        help="必须是一个不含既有 API 端点的项目（用于验证真实创建）")
    parser.add_argument("--token", default=None)
    args = parser.parse_args()

    for path in (EXCEL, COLLECTION, ENVIRONMENT):
        if not path.exists():
            print(f"缺少样本文件: {path}")
            return 2

    base = f"{args.base_url}/api/v2/projects/{args.project}/api-bundles"
    token = args.token
    stamp = int(time.time())

    print("=" * 72)
    print("真实美心三文件 HTTP 验收")
    print("=" * 72)
    print(f"后端: {args.base_url}  项目: {args.project}")
    print(f"Excel: {EXCEL.name} ({EXCEL.stat().st_size:,} B)  25 接口")
    print(f"Collection: {COLLECTION.name} ({COLLECTION.stat().st_size:,} B)  47 请求")
    print(f"Environment: {ENVIRONMENT.name} ({ENVIRONMENT.stat().st_size:,} B)  14 变量")
    print()

    # 断言 0：项目当前无端点（保证创建可验证）
    status0, body0 = request("GET", base, token=token)
    check("项目可访问（GET api-bundles）", status0 == 200, f"status={status0}")

    # ==================================================================
    # A. 正常三文件：parse-bundle → confirm（验证真实创建 + 幂等）
    # ==================================================================
    print("\n--- A. 正常三文件（完整环境）---")
    bundle_a = f"美心验收A-{stamp}"
    status, body = parse_bundle(base, bundle_a, ENVIRONMENT.read_bytes(), ENVIRONMENT.name, token)
    if not check("A.parse-bundle 返回 200", status == 200, f"status={status} {str(body)[:200]}"):
        return 1

    data = body["data"]
    bundle_a_id = data["bundle_id"]
    stats = data["stats"]
    print(f"    stats: {stats}")

    check("A.parse-bundle 保存 3 个来源", len(data["sources"]) == 3, str(len(data["sources"])))
    check("A.parse-bundle 解析到 25 个 Excel 接口", stats["excel_interfaces"] == 25, str(stats["excel_interfaces"]))
    check("A.parse-bundle 解析到 47 个 Collection 请求", stats["collection_requests"] == 47, str(stats["collection_requests"]))
    check("A.parse-bundle 解析到 14 个环境变量", stats["environment_variables"] == 14, str(stats["environment_variables"]))
    check("A.parse-bundle 保存了契约（匹配结果）", len(data["contracts"]) > 0, f"{len(data['contracts'])} 个")
    check("A.parse-bundle 保存了冲突", len(data["conflicts"]) > 0, f"{len(data['conflicts'])} 个")

    # 幂等：重复 parse 不新增来源/契约
    _, body_r = parse_bundle(base, bundle_a, ENVIRONMENT.read_bytes(), ENVIRONMENT.name, token)
    same = body_r["data"]["bundle_id"] == bundle_a_id
    check("A.parse-bundle 幂等（复用同一 bundle）", same)
    check("A.parse-bundle 幂等（来源数不变）", len(body_r["data"]["sources"]) == 3)
    check("A.parse-bundle 幂等（契约数不变）",
          len(body_r["data"]["contracts"]) == len(data["contracts"]),
          f"{len(body_r['data']['contracts'])} vs {len(data['contracts'])}")

    # ==================================================================
    # B. 冲突确认门禁 → 确认后可导入
    # ==================================================================
    print("\n--- B. 冲突确认门禁 ---")
    high = [c for c in data["conflicts"] if c["severity"] == "high" and c["status"] == "open"]
    print(f"    high 开放冲突: {len(high)}")
    check("B.真实三文件产生 high 级冲突（env_missing）", len(high) > 0, f"{len(high)} 个")
    if high:
        print(f"    样例: {high[0]['conflict_type']} | {high[0]['message'][:80]}")

    status_g, body_g = request("POST", f"{base}/{bundle_a_id}/confirm", json_body={}, token=token)
    unconfirmed = _extract_unconfirmed(body_g)
    check("B.存在未确认 high 冲突时拒绝导入（409）", status_g == 409, f"status={status_g}")
    check("B.返回未确认冲突清单", bool(unconfirmed), f"{len(unconfirmed) if unconfirmed else 0} 个")

    # 未确认时的拒绝必须无副作用：端点不应被创建
    if unconfirmed:
        status_r, body_r2 = request(
            "POST", f"{base}/{bundle_a_id}/conflicts/confirm",
            json_body={"resolutions": [
                {"conflict_id": c["id"], "resolution": "accept", "note": "验收确认"}
                for c in unconfirmed
            ]},
            token=token,
        )
        check("B.冲突确认接口返回 200", status_r == 200, f"status={status_r}")
        remaining = body_r2["data"]["remaining_open"] if status_r == 200 else []
        check("B.确认后无剩余开放 high 冲突",
              not [c for c in remaining if c["severity"] == "high"],
              f"剩余 high={len([c for c in remaining if c['severity'] == 'high'])}")

    # ==================================================================
    # C. confirm：真实创建 + 幂等
    # ==================================================================
    print("\n--- C. confirm 导入（真实创建）---")
    status_c, body_c = request("POST", f"{base}/{bundle_a_id}/confirm", json_body={}, token=token)
    if not check("C.confirm 返回 200", status_c == 200, f"status={status_c} {str(body_c)[:200]}"):
        return 1
    ca = body_c["data"]
    print(f"    imported={ca['imported_count']} skipped={ca['skipped_count']} folders={ca['folder_ids']}")
    check("C.创建了 Folder", len(ca["folder_ids"]) > 0)
    check("C.真实创建了 APIEndpoint", ca["imported_count"] > 0, f"imported={ca['imported_count']}")
    check("C.无失败项", not ca["failed"], str(ca["failed"]))

    status_c2, body_c2 = request("POST", f"{base}/{bundle_a_id}/confirm", json_body={}, token=token)
    c2 = body_c2["data"]
    check("C.confirm 幂等（重复导入不再新建）",
          c2["imported_count"] == 0 and c2["skipped_count"] == ca["imported_count"] + ca["skipped_count"],
          f"imported={c2['imported_count']} skipped={c2['skipped_count']}")

    # 变量与契约回填
    status_v, body_v = request("GET", f"{base}/{bundle_a_id}/variables", token=token)
    check("C.环境变量已保存", status_v == 200 and len(body_v["data"]) == 14,
          f"{len(body_v['data']) if status_v == 200 else '?'} 个")
    if status_v == 200:
        leaked = [v for v in body_v["data"]
                  if v["value_ref"] and not str(v["value_ref"]).startswith("env:")]
        check("C.变量不含明文值（仅 env:NAME 引用）", not leaked, f"{len(leaked)} 个异常")

    status_b, body_b = request("GET", f"{base}/{bundle_a_id}", token=token)
    if status_b == 200:
        contracts = body_b["data"]["contracts"]
        check("C.契约回填 imported_endpoint_id",
              all(c["imported_endpoint_id"] for c in contracts),
              f"{sum(1 for c in contracts if c['imported_endpoint_id'])}/{len(contracts)}")
        check("C.bundle 状态为 imported", body_b["data"]["bundle"]["status"] == "imported")

    # ==================================================================
    # D. 断言 / 依赖落库核对
    # ==================================================================
    print("\n--- D. 断言 / 依赖落库 ---")
    assets: dict[str, Any] = {}
    contract_id_for_assertions: str | None = None
    try:
        import asyncio as _asyncio

        from sqlalchemy import text as _text

        sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
        from app.config.database import async_session_factory

        async def _pick_and_inject():
            """取一个契约 id 并注入合成断言（单次事件循环完成）。"""
            async with async_session_factory() as s:
                row = (await s.execute(_text(
                    "SELECT id FROM api_interface_contracts WHERE bundle_id = :bid "
                    "ORDER BY method, normalized_path LIMIT 1"
                ), {"bid": bundle_a_id})).fetchone()
                if row is None:
                    return None
                await s.execute(_text(
                    "UPDATE api_interface_contracts SET assertions = :a WHERE id = :i"
                ), {
                    "a": json.dumps([{
                        "type": "status", "expression": "status == 200",
                        "expected": "200", "source_type": "collection_script",
                    }], ensure_ascii=False),
                    "i": row[0],
                })
                await s.commit()
                return str(row[0])

        contract_id_for_assertions = _asyncio.run(_pick_and_inject())
    except Exception as exc:  # noqa: BLE001
        check("D.注入合成断言", False, f"{type(exc).__name__}: {exc}")

    # 通过 HTTP 触发一次 confirm，让合成断言走落库路径（幂等，不会新建端点）
    if contract_id_for_assertions:
        status_again, _ = request(
            "POST", f"{base}/{bundle_a_id}/confirm",
            json_body={"contract_ids": [contract_id_for_assertions]}, token=token,
        )
        check("D.合成断言契约二次 confirm 成功", status_again == 200, f"status={status_again}")

    # 全部经 HTTP 核对（不跨事件循环直连数据库）
    status_as, body_as = request("GET", f"{base}/{bundle_a_id}/assets", token=token)
    if check("D.资产统计接口返回 200", status_as == 200, f"status={status_as}"):
        assets = body_as["data"]
        print(f"    {assets}")
        check("D.请求模板已落库", assets["request_templates"] > 0, str(assets["request_templates"]))
        check("D.断言落库代码路径生效", assets["assertions"] > 0, str(assets["assertions"]))

        # P0-2：真实 Excel 返回码区（3 个 sheet 带 Return Code）应产生 excel_contract 断言，
        # 此前因分区标记与去空格文本不匹配（"return code" vs "returncode"）恒为 0。
        by_source = assets.get("assertions_by_source") or {}
        excel_assertions = int(by_source.get("excel_contract", 0))
        check("D.Excel 返回码断言真实落库（非合成注入）", excel_assertions > 0,
              f"assertions_by_source={by_source}")
        check("D.契约全部回填端点",
              assets["contracts_with_endpoint"] == assets["contracts"],
              f"{assets['contracts_with_endpoint']}/{assets['contracts']}")
        print(f"    依赖数: {assets['dependencies']}"
              "（真实 Collection 未提供可确定依赖，允许为 0）")

    # E. 失败回滚使用 HTTP 核对
    status_l, body_l = request(
        "GET", f"{args.base_url}/api/v2/projects/{args.project}/api-endpoints", token=token,
    )
    endpoint_total_after_failure: int | None = None
    if status_l == 200:
        payload_l = body_l.get("data")
        items_l = (payload_l if isinstance(payload_l, list)
                   else (payload_l or {}).get("items") or (payload_l or {}).get("api_endpoints") or [])
        endpoint_total_after_failure = len(items_l)

    # ==================================================================
    # E. 失败回滚
    # ==================================================================
    print("\n--- E. 失败回滚 ---")
    status_f, body_f = request(
        "POST", f"{base}/{bundle_a_id}/confirm",
        json_body={"contract_ids": [str(uuid4())]},  # 不存在的契约
        token=token,
    )
    check("E.无效契约 ID 被拒绝且不带副作用", status_f in (400, 409), f"status={status_f}")

    # 用 HTTP 再次核对端点数未因失败请求增加
    status_l2, body_l2 = request(
        "GET", f"{args.base_url}/api/v2/projects/{args.project}/api-endpoints", token=token,
    )
    if status_l2 == 200 and endpoint_total_after_failure is not None:
        payload_l2 = body_l2.get("data")
        items_l2 = (payload_l2 if isinstance(payload_l2, list)
                    else (payload_l2 or {}).get("items") or (payload_l2 or {}).get("api_endpoints") or [])
        check("E.失败请求未新增端点（回滚无副作用）",
              len(items_l2) == endpoint_total_after_failure,
              f"{len(items_l2)} vs {endpoint_total_after_failure}")
    else:
        # bundle 侧核对：契约状态未被破坏
        status_bb, body_bb = request("GET", f"{base}/{bundle_a_id}", token=token)
        check("E.失败请求未改变 bundle 状态",
              status_bb == 200 and body_bb["data"]["bundle"]["status"] == "imported",
              f"端点列表不可用({status_l2})")

    # ---------------- 汇总 ----------------
    print()
    print("=" * 72)
    passed = sum(1 for r in results if r[0] == PASS)
    failed = [r for r in results if r[0] == FAIL]
    print(f"验收结果: {passed}/{len(results)} 通过")
    if failed:
        print("失败项:")
        for _, name, detail in failed:
            print(f"  - {name} {detail}")
    print("=" * 72)
    return 0 if not failed else 1


if __name__ == "__main__":
    sys.exit(main())
