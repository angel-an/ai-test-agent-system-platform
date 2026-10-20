# -*- coding: utf-8 -*-
"""P1-③ 验证：店铺用例离线重编译，核对 checkbox 类字段的检查类型与 blocked 风险。"""
import json, sys, io
sys.path.insert(0, ".")
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

import psycopg2
import psycopg2.extras
from types import SimpleNamespace
from app.services.web_case_compiler import compile_cases, build_form_plan_for_template

PROJ = "942226d9-6071-43fa-b2bb-f7cbc93a0028"
SID = "e1c94163-d221-4ac8-afdc-c2103f989e5f"

conn = psycopg2.connect(host="127.0.0.1", port=5432, user="postgres", password="postgres", dbname="langgraph_platform_db")
cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
cur.execute("select name, custom_config from web_sub_functions where id::text=%s", (SID,))
r = cur.fetchone()
name, cc = r["name"], r["custom_config"] or {}
fl = cc.get("field_locators") or {}
fl_old = json.loads(json.dumps(fl))
fl_old["fields"]["營業時間"] = {"kind": "input",
                              "selector_hint": '.ant-form-item:has-text("Business Hours") input'}

cur.execute("""select identifier, name, description, preconditions, custom_fields
    from test_cases where project_id=%s and module='店鋪主數據管理'
    and custom_fields->>'sub_function_id'=%s
    and jsonb_array_length(coalesce(custom_fields->'assertions','[]'::jsonb))>0""", (PROJ, SID))
cases = []
for row in cur.fetchall():
    cf = row["custom_fields"] or {}
    cases.append(SimpleNamespace(identifier=row["identifier"], name=row["name"],
                                 description=row["description"], preconditions=row["preconditions"],
                                 custom_fields=cf, assertions=cf.get("assertions") or [],
                                 requirement_rule_id=cf.get("requirement_rule_id")))
manifest = compile_cases(cases)
print(f"店铺编译用例 {len(manifest.get('cases') or [])} 条")

def summarize(plan, label):
    checks = (plan or {}).get("checks") or []
    from collections import Counter
    kinds = Counter(c.get("type") for c in checks)
    bh = [c for c in checks if c.get("field") == "營業時間"]
    print(f"[{label}] checks={len(checks)} skipped={len((plan or {}).get('skipped') or [])}")
    print(f"  檢查类型: {dict(kinds)}")
    print(f"  營業時間 相关检查 {len(bh)} 项:")
    for c in bh[:8]:
        print(f"    {c['type']:16s} rule={c.get('rule','')[:20]!r} data={str(c.get('data'))[:12]!r}")
    return kinds, bh

k_old, bh_old = summarize(build_form_plan_for_template("yl_masterdata", manifest, fl_old, None)[0], "改前 input")
k_new, bh_new = summarize(build_form_plan_for_template("yl_masterdata", manifest, fl, None)[0], "改后 checkbox_group")

print("\n判定：")
print("  改前 required_error（会 fill 抛错→blocked）:", sum(1 for c in bh_old if c["type"] == "required_error"))
print("  改后 submit_only（转负路径/不适用）:", sum(1 for c in bh_new if c["type"] == "submit_only"))
print("  改后 choice_options（只读选项校验）:", sum(1 for c in bh_new if c["type"] == "choice_options"))
print("  改后仍存在 required_error 的營業時間检查:", sum(1 for c in bh_new if c["type"] == "required_error"))

json.dump({"checks_old": sum(k_old.values()), "checks_new": sum(k_new.values()),
           "types_old": dict(k_old), "types_new": dict(k_new),
           "bh_old": [c["type"] for c in bh_old], "bh_new": [c["type"] for c in bh_new]},
          open("D:/code/Pyproject/ai-test-agent-system-platform/out_yl_case_audit/store_checkbox_compile_verify_20261017.json", "w", encoding="utf-8"),
          ensure_ascii=False, indent=1)
print("\n已写 out_yl_case_audit/store_checkbox_compile_verify_20261017.json")
