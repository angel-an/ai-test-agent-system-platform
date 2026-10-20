# -*- coding: utf-8 -*-
"""只读：核验人工用例历史执行的断言证据（区分真通过 / 空跑绿）"""
from __future__ import annotations

from collections import Counter

import psycopg2

PROJ = "942226d9-6071-43fa-b2bb-f7cbc93a0028"
SRC = ["human_xlsx", "self_defined_real_write"]
DSN = dict(host="127.0.0.1", port=5432, user="postgres", password="postgres",
           dbname="langgraph_platform_db")

conn = psycopg2.connect(**DSN)
cur = conn.cursor()

cur.execute("""
with latest as (
  select distinct on (t.id) t.id case_id, t.identifier, t.module,
         r.identifier run_id, r.status, r.created_at, r.id rid
    from test_cases t
    join web_tests w on w.test_case_id = t.id
    join web_test_runs r on r.web_test_id = w.id
   where t.project_id = %s and t.custom_fields->>'content_source' = any(%s)
   order by t.id, r.created_at desc
)
select l.module, l.run_id, l.identifier, l.status, l.created_at, l.rid,
       coalesce((select bool_or(j.stdout like '%%COMPILED_ASSERTION%%')
                   from test_run_script_jobs j where j.test_run_id = l.rid), false) ca,
       coalesce((select bool_or(j.stdout like '%%pending_locator%%')
                   from test_run_script_jobs j where j.test_run_id = l.rid), false) pend,
       coalesce((select bool_or(j.stdout like '%%UNVERIFIED%%')
                   from test_run_script_jobs j where j.test_run_id = l.rid), false) unv
  from latest l
 order by l.module, l.run_id
""", (PROJ, SRC))
rows = cur.fetchall()

print("=" * 92)
print("人工用例最新一次历史执行：有无真实断言证据")
print("=" * 92)
agg = Counter()
for mod, run, ident, st, ts, rid, ca, pend, unv in rows:
    if ca:
        tag = "有真实断言行"
    elif pend:
        tag = "仅 pending_locator（无断言落地）"
    else:
        tag = "无断言证据（空跑）"
    agg[(mod, tag)] += 1
print(f"{'模块':<24}{'证据等级':<34}{'条数':>5}")
for (mod, tag), n in sorted(agg.items()):
    print(f"{mod:<24}{tag:<34}{n:>5}")

print("\n时间分布：")
cur.execute("""
with latest as (
  select distinct on (t.id) t.id case_id, t.module, r.created_at
    from test_cases t
    join web_tests w on w.test_case_id = t.id
    join web_test_runs r on r.web_test_id = w.id
   where t.project_id = %s and t.custom_fields->>'content_source' = any(%s)
   order by t.id, r.created_at desc)
select module, to_char(created_at,'MM-DD HH24:MI'), count(*)
  from latest group by 1,2 order by 1,2
""", (PROJ, SRC))
for r in cur.fetchall():
    print(f"  {r[0]:<24}{r[1]}  {r[2]:>3} 条")

print("\n有真实断言行的用例（按 run 归组）：")
byrun = Counter()
for mod, run, ident, st, ts, rid, ca, pend, unv in rows:
    if ca:
        byrun[(mod, run, str(ts)[:16])] += 1
for k, v in sorted(byrun.items()):
    print(f"  {k[0]:<24}{k[1]:<10}{k[2]}  {v:>3} 条")

print("\n无断言证据的用例清单：")
for mod, run, ident, st, ts, rid, ca, pend, unv in rows:
    if not ca:
        print(f"  {mod:<24}{ident:<10}{run:<10}{st:<10}{str(ts)[:16]}")

conn.close()
