# -*- coding: utf-8 -*-
"""P1-② 验证：真实用例离线编译，对比 aliases 配置前后的表单计划。
用 backend venv 运行（依赖齐全）。只读 DB + 内存编译，不写库、不建资产。"""
import json, sys, io
sys.path.insert(0, ".")
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

import psycopg2
import psycopg2.extras
from app.services.web_case_compiler import compile_cases, build_form_plan_for_template

PROJ = "942226d9-6071-43fa-b2bb-f7cbc93a0028"
SUBS = {"商品主數據": "d90c2034-0ac2-4f12-91c7-a0be5f2f2103",
        "支付方式主數據": "af320aac-6d37-4cc9-b1d7-1213aa5eced4"}

conn = psycopg2.connect(host="127.0.0.1", port=5432, user="postgres", password="postgres", dbname="langgraph_platform_db")
cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

total_before = total_after = 0
report = {}
for mod, sid in SUBS.items():
    cur.execute("select name, custom_config from web_sub_functions where id::text=%s", (sid,))
    r = cur.fetchone()
    name, cc = r["name"], r["custom_config"] or {}
    loc_with = cc.get("field_locators") or {}
    loc_without = {k: v for k, v in loc_with.items() if k != "aliases"}

    # 该子功能下有断言的已绑定用例（编译批次口径：同子功能全部一起编译）
    cur.execute("""select id, identifier, name, description, preconditions, custom_fields
        from test_cases where project_id=%s and module=%s
        and custom_fields->>'sub_function_id'=%s
        and jsonb_array_length(coalesce(custom_fields->'assertions','[]'::jsonb))>0""", (PROJ, mod, sid))
    rows = cur.fetchall()

    # 构造 TestCase 对象（最小字段，compile_cases 兼容 duck typing）
    from types import SimpleNamespace
    cases = []
    for row in rows:
        cf = row["custom_fields"] or {}
        cases.append(SimpleNamespace(
            identifier=row["identifier"], name=row["name"], description=row["description"],
            preconditions=row["preconditions"], custom_fields=cf,
            assertions=cf.get("assertions") or [],
            requirement_rule_id=cf.get("requirement_rule_id"),
        ))
    manifest = compile_cases(cases)
    n_cases = len(manifest.get("cases") or [])

    plan_no, src_no = build_form_plan_for_template("yl_masterdata", manifest, loc_without, None)
    plan_yes, src_yes = build_form_plan_for_template("yl_masterdata", manifest, loc_with, None)
    before = len((plan_no or {}).get("checks") or [])
    after = len((plan_yes or {}).get("checks") or [])
    skipped_no = (plan_no or {}).get("skipped") or []
    skipped_yes = (plan_yes or {}).get("skipped") or []
    total_before += before
    total_after += after
    print(f"== {mod}（{name}）: 编译用例 {n_cases}，来源 {src_yes}")
    print(f"   别名前: checks={before} skipped={len(skipped_no)}；别名后: checks={after} skipped={len(skipped_yes)}")
    if skipped_yes:
        print("   仍被跳过:", [(s['field'], s['reason']) for s in skipped_yes][:6])
    report[mod] = {"compiled_cases": n_cases, "before_checks": before, "after_checks": after,
                   "before_skipped": [s["field"] for s in skipped_no],
                   "after_skipped": [s["field"] for s in skipped_yes]}

json.dump(report, open("D:/code/Pyproject/ai-test-agent-system-platform/out_yl_case_audit/alias_compile_verify_20261017.json", "w", encoding="utf-8"), ensure_ascii=False, indent=1)
print(f"\n合计：别名前 {total_before} 项检查 → 别名后 {total_after} 项（+{total_after-total_before}）")
