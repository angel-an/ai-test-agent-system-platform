# -*- coding: utf-8 -*-
"""只读：A 类字段级用例的字段名能否命中子功能定位器（决定补断言后是否真能执行）"""
from __future__ import annotations

import json
import re
from pathlib import Path

import psycopg2

PROJ = "942226d9-6071-43fa-b2bb-f7cbc93a0028"
DSN = dict(host="127.0.0.1", port=5432, user="postgres", password="postgres",
           dbname="langgraph_platform_db")
ROOT = Path(__file__).resolve().parent
SF = {
    "商品": "d90c2034-0ac2-4f12-91c7-a0be5f2f2103",
    "支付方式": "af320aac-6d37-4cc9-b1d7-1213aa5eced4",
    "店铺": "e1c94163-d221-4ac8-afdc-c2103f989e5f",
    "通用": "f04ac33d-eb21-477e-8b2c-944c571193bb",
}
ACTION = re.compile(
    r"(輸入|输入|填寫|填写|選擇|选择|下拉|默認|默认|開關|开关|顯示|显示|超過|超过|"
    r"不輸入|为空|為空|為|進行|进行)"
)


def fam(module: str) -> str:
    if "店鋪" in module or "店铺" in module:
        return "店铺"
    if "商品" in module:
        return "商品"
    if "支付" in module:
        return "支付方式"
    return "通用"


def guess_field(name: str) -> str:
    head = re.split(r"[-—–]", name)[0]
    m = ACTION.search(head)
    cand = head[: m.start()] if m else head
    return cand.strip(" 　-")


conn = psycopg2.connect(**DSN)
cur = conn.cursor()

cfg: dict[str, tuple[set[str], dict[str, str]]] = {}
for mod, sid in SF.items():
    cur.execute("select custom_config->'field_locators' from web_sub_functions where id=%s", (sid,))
    row = cur.fetchone()
    fl = (row[0] if row else None) or {}
    cfg[mod] = (set((fl.get("fields") or {}).keys()), fl.get("aliases") or {})

tier_rows = json.loads((ROOT / "out_yl_case_audit" / "human_feasibility_20261017.json")
                       .read_text(encoding="utf-8"))["rows"]
a_rows = [r for r in tier_rows if r["tier"] == "A-字段级"]

print("=" * 104)
print("A 类字段级用例：字段名 vs 子功能定位器")
print("=" * 104)
print(f"{'模块':<8}{'用例':<11}{'推断字段':<20}{'命中':<6}备注")
hit = 0
for r in a_rows:
    mod = r["module"]
    field = guess_field(r["name"])
    fields, aliases = cfg.get(mod, (set(), {}))
    target = field if field in fields else aliases.get(field)
    ok = bool(target and target in fields)
    if ok:
        hit += 1
        note = f"→ {target}" if target != field else ""
    else:
        near = [f for f in fields if field and (field in f or f in field)][:3]
        note = f"未配置；相近: {near}" if near else "未配置"
    print(f"{mod:<8}{r['case']:<11}{field:<20}{'是' if ok else '否':<6}{note}")

print(f"\n  A 类 {len(a_rows)} 条，字段已配定位器 {hit} 条 → 补断言即可执行；"
      f"其余 {len(a_rows) - hit} 条需先补定位器/别名")

print("\n各子功能已配置字段数：")
for mod, (fields, aliases) in cfg.items():
    print(f"  {mod:<8}fields={len(fields):<3}aliases={len(aliases)}")

conn.close()
