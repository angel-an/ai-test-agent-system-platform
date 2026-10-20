# -*- coding: utf-8 -*-
"""rev90：按 TR-117~122 全部 job stdout 的直接证据，给 unverified/无证据用例
打「需人工执行」标签 + manual_execution 标记（与 supplement_case_rule_tool 撤销口径一致）。"""
import json
import re
import sys

import psycopg2

conn = psycopg2.connect(host="localhost", port=5432, user="postgres",
                        password="postgres", dbname="langgraph_platform_db")
cur = conn.cursor()
sys.stdout.reconfigure(encoding="utf-8")

cur.execute("""
SELECT j.stdout FROM test_run_script_jobs j
JOIN test_runs r ON r.id = j.test_run_id
WHERE r.identifier IN ('TR-117','TR-118','TR-119','TR-121','TR-122')
  AND j.stdout LIKE '%COMPILED_ASSERTION%'
""")
verdicts = {}
pat = re.compile(r"COMPILED_ASSERTION (\{.*\})")
for (so,) in cur.fetchall():
    for line in (so or "").splitlines():
        m = pat.search(line)
        if not m:
            continue
        try:
            item = json.loads(m.group(1))
        except Exception:
            continue
        cid = item.get("case_id")
        if cid:
            verdicts.setdefault(cid, set()).add(item.get("outcome"))

FOLDER = "abc46f5b-1653-46e8-a73e-a6aad46ee39c"
cur.execute("SELECT identifier, name FROM test_cases WHERE folder_id=%s", (FOLDER,))
all_cases = {ident: name for ident, name in cur.fetchall()}

manual = []
for ident, name in all_cases.items():
    v = verdicts.get(ident)
    if v is None:
        if any(k in (name or "") for k in ("Attachment", "reply")):
            manual.append((ident, name, "计划层跳过（上传/回复交互），无执行证据"))
    elif "unverified" in v:
        manual.append((ident, name, "存在 unverified 断言"))

print("需人工执行:", len(manual))
cur.execute("SELECT id FROM tags WHERE name='需人工执行'")
tag_id = cur.fetchone()[0]
for ident, name, why in manual:
    cur.execute(
        "UPDATE test_cases SET custom_fields = jsonb_set("
        "coalesce(custom_fields,'{}'::jsonb), '{manual_execution}', 'true'::jsonb) "
        "WHERE identifier=%s AND folder_id=%s", (ident, FOLDER))
    cur.execute(
        "INSERT INTO test_case_tags (test_case_id, tag_id) "
        "SELECT id, %s FROM test_cases WHERE identifier=%s AND folder_id=%s "
        "ON CONFLICT DO NOTHING", (tag_id, ident, FOLDER))
    print(f"  {ident} {why} | {(name or '')[:36]}")
conn.commit()
print("done")
