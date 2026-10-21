# -*- coding: utf-8 -*-
"""离线验证（不写库、不跑浏览器）：
把「A 类字段级人工用例」补一条断言后，用真正的编译器预演，看能否生成可执行检查。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parent / "backend"))

import psycopg2  # noqa: E402

from app.services.web_case_compiler import build_form_plan, compile_cases  # noqa: E402

PROJ = "942226d9-6071-43fa-b2bb-f7cbc93a0028"
DSN = dict(host="127.0.0.1", port=5432, user="postgres", password="postgres",
           dbname="langgraph_platform_db")

# 子功能：商品 / 支付方式 / 店铺
SF = {
    "商品": "d90c2034-0ac2-4f12-91c7-a0be5f2f2103",
    "支付方式": "af320aac-6d37-4cc9-b1d7-1213aa5eced4",
    "店铺": "e1c94163-d221-4ac8-afdc-c2103f989e5f",
}

# 待验证样本：用例号、模块、字段名、拟补断言
SAMPLES = [
    ("TC-206444", "商品", "商品規格編號",
     {"kind": "max_length", "field": "商品規格編號", "data": "51個字元",
      "expect": "reject", "rule": "最多50個字元"}),
    ("TC-387036", "商品", "商品內部名稱",
     {"kind": "max_length", "field": "商品內部名稱", "data": "51個字元",
      "expect": "reject", "rule": "最多50個字元"}),
    ("TC-251944", "支付方式", "支付方式編號",
     {"kind": "max_length", "field": "支付方式編號", "data": "51個字元",
      "expect": "reject", "rule": "最多50個字元"}),
    ("TC-628747", "支付方式", "支付方式名稱",
     {"kind": "max_length", "field": "支付方式名稱", "data": "51個字元",
      "expect": "reject", "rule": "最多50個字元"}),
    ("TC-380279", "店铺", "營業狀態",
     {"kind": "default", "field": "營業狀態", "expect": "hidden",
      "rule": "關閉代表暫停營業"}),
]


def load_locators(conn, sf_id: str) -> dict:
    cur = conn.cursor()
    cur.execute("select custom_config->'field_locators' from web_sub_functions where id=%s",
                (sf_id,))
    row = cur.fetchone()
    return row[0] if row else {}


def load_case(conn, identifier: str) -> SimpleNamespace | None:
    cur = conn.cursor()
    cur.execute("""
        select identifier, name, module, coalesce(description,''),
               coalesce(custom_fields,'{}'::jsonb)
          from test_cases where project_id=%s and identifier=%s
    """, (PROJ, identifier))
    row = cur.fetchone()
    if not row:
        return None
    custom = dict(row[4] or {})
    return SimpleNamespace(identifier=row[0], name=row[1], module=row[2],
                           description=row[3], custom_fields=custom,
                           test_case_steps=[])


def main() -> None:
    conn = psycopg2.connect(**DSN)
    results = []
    print("=" * 104)
    print("离线预演：补 1 条断言后，编译器能否产出可执行检查（不写库）")
    print("=" * 104)
    print(f"{'用例':<11}{'模块':<8}{'字段':<16}{'编译状态':<12}{'检查数':>6}"
          f"{'pending':>8}{'可执行':>7}  字段是否已配定位器")
    for cid, mod, field, assertion in SAMPLES:
        case = load_case(conn, cid)
        if not case:
            print(f"  {cid} 用例不存在")
            continue
        case.custom_fields["assertions"] = [assertion]
        case.custom_fields["assertions_source"] = "offline_probe"
        # 编译器取 case.field 的来源是 requirement_rule.field；只写 assertions 时
        # field 为空 → 所有断言被静默跳过（本次探针第一轮即撞到）
        case.custom_fields["requirement_rule"] = {
            "field": field, "type": "", "required": False, "condition": "",
        }
        manifest = compile_cases([case])
        locators = load_locators(conn, SF[mod])

        compiled = manifest["cases"][0] if manifest["cases"] else {}
        plan = build_form_plan(manifest, locators) if locators else None
        checks = (plan or {}).get("checks") or []
        executable = [c for c in checks if not c.get("pending_locator")]
        fields_cfg = (locators or {}).get("fields") or {}
        aliases = (locators or {}).get("aliases") or {}
        known = field in fields_cfg or aliases.get(field) in fields_cfg

        print(f"{cid:<11}{mod:<8}{field:<16}{compiled.get('compile_status',''):<12}"
              f"{len(checks):>6}{manifest.get('locator_depended_assertion_count',0):>8}"
              f"{len(executable):>7}  {'是' if known else '否'}")

        results.append({
            "case": cid, "module": mod, "field": field,
            "compile_status": compiled.get("compile_status"),
            "checks": len(checks),
            "executable": len(executable),
            "pending": manifest.get("locator_depended_assertion_count"),
            "field_configured": known,
            "plan_sample": checks[:2],
        })
    conn.close()
    p = Path(__file__).resolve().parent / "out_yl_case_audit" / "human_offline_probe_20261017.json"
    p.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n[saved] {p}")


if __name__ == "__main__":
    main()
