# -*- coding: utf-8 -*-
"""P1-② 配置侧：给商品/支付子功能的 field_locators 补 aliases（快照→写→回读）。"""
import json, sys, io
from datetime import datetime
import psycopg2
import psycopg2.extras

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
SNAP = "out_yl_case_audit/alias_config_snapshot_20261017.json"

GOODS = "d90c2034-0ac2-4f12-91c7-a0be5f2f2103"
PAY = "af320aac-6d37-4cc9-b1d7-1213aa5eced4"
ALIASES = {
    GOODS: {
        "Item Name (CN)商品名稱(CN)": "商品名稱(CN)",
        "Item Name (EN)商品內部名稱": "商品內部名稱",
        "Item Name商品內部名稱": "商品內部名稱",
        "SKU Item Key商品規格編碼": "商品規格編碼",
        "Item Code商品編碼": "商品編碼",
        "Brand所屬品牌": "所屬品牌",
    },
    PAY: {
        "Payment CodeName": "支付方式名稱",
        "Payment Code": "支付方式編碼",
        "POS PaymentCode": "POS Payment Code",
    },
}

conn = psycopg2.connect(host="127.0.0.1", port=5432, user="postgres", password="postgres", dbname="langgraph_platform_db")
conn.autocommit = True
cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

cur.execute("select id::text id, name, custom_config from web_sub_functions where id::text = any(%s)", ([GOODS, PAY],))
rows = {r["id"]: dict(r) for r in cur.fetchall()}
assert len(rows) == 2, f"应命中 2 行，实际 {len(rows)}"
json.dump({"snapshot_at": datetime.now().isoformat(), "rows": rows}, open(SNAP, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
print("快照:", SNAP)

for sid, aliases in ALIASES.items():
    name = rows[sid]["name"]
    cc = rows[sid]["custom_config"] or {}
    fl = cc.get("field_locators") or {}
    fields = sorted((fl.get("fields") or {}).keys())
    bad = [a for a in aliases.values() if a not in fields]
    assert not bad, f"{name} 别名目标不存在于定位器表: {bad}"
    fl["aliases"] = aliases
    cc["field_locators"] = fl
    cur.execute("update web_sub_functions set custom_config=%s::jsonb, updated_at=now() where id::text=%s returning name", (json.dumps(cc, ensure_ascii=False), sid))
    # 回读
    cur.execute("select custom_config from web_sub_functions where id::text=%s", (sid,))
    got = (cur.fetchone()["custom_config"] or {}).get("field_locators", {}).get("aliases")
    ok = got == aliases
    print(f"{name}: aliases {len(aliases)} 条写入，回读{'一致' if ok else '不一致!'}")
    assert ok

print("PASS")
