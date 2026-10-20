# -*- coding: utf-8 -*-
"""P1-③ 配置侧：營業時間 由 input 改为 checkbox_group（快照→写→回读）。
依据 TR-505 探针：真实 DOM = .ant-checkbox-group，7 个勾选框 Mon~Sun，默认全不勾选。"""
import json, sys, io
from datetime import datetime
import psycopg2
import psycopg2.extras

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
SID = "e1c94163-d221-4ac8-afdc-c2103f989e5f"
SNAP = "out_yl_case_audit/store_checkbox_config_snapshot_20261017.json"

conn = psycopg2.connect(host="127.0.0.1", port=5432, user="postgres", password="postgres", dbname="langgraph_platform_db")
conn.autocommit = True
cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

cur.execute("select id::text id, name, custom_config from web_sub_functions where id::text=%s", (SID,))
row = dict(cur.fetchone())
cc = row["custom_config"] or {}
json.dump({"snapshot_at": datetime.now().isoformat(), "row": row}, open(SNAP, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
print("快照:", SNAP)

fl = cc.get("field_locators") or {}
fld = (fl.get("fields") or {}).get("營業時間")
assert fld, "未找到 營業時間 定位器"
print("原定义:", json.dumps(fld, ensure_ascii=False))

fld["kind"] = "checkbox_group"
fld["selector_hint"] = '.ant-form-item:has-text("Business Hours")'
fld["option_count"] = 7
fld["note"] = "TR-505 探针实测：.ant-checkbox-group，Mon~Sun 共 7 个勾选框，默认全不勾选"
fl["fields"]["營業時間"] = fld
cc["field_locators"] = fl

cur.execute("update web_sub_functions set custom_config=%s::jsonb, updated_at=now() where id::text=%s",
            (json.dumps(cc, ensure_ascii=False), SID))
cur.execute("select custom_config->'field_locators'->'fields'->'營業時間' from web_sub_functions where id::text=%s", (SID,))
got = cur.fetchone()["?column?"]
ok = got.get("kind") == "checkbox_group" and got.get("selector_hint") == '.ant-form-item:has-text("Business Hours")'
print("回读:", json.dumps(got, ensure_ascii=False))
print("PASS" if ok else "FAIL")
