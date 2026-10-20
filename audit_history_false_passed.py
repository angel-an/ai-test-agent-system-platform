# -*- coding: utf-8 -*-
"""历史误判 PASSED 用例：只读更正清单。
判据（与 P1-① 修复同源）：WEB_TEST 作业 COMPLETED、stdout 无 COMPILED_ASSERTION/UPLOAD_EVIDENCE、
用例状态被判 PASSED。只读，不回刷历史。"""
import json, sys, io
from datetime import datetime
import psycopg2
import psycopg2.extras

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
OUT = "out_yl_case_audit/history_false_passed_20261017.json"

conn = psycopg2.connect(host="127.0.0.1", port=5432, user="postgres", password="postgres", dbname="langgraph_platform_db")
cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
cur.execute("select distinct script_type::text from test_run_script_jobs")
print("script_type 取值:", [r["script_type"] for r in cur.fetchall()])

cur.execute(r"""
select r.identifier run_id, r.run_state::text run_state, r.created_at run_created,
       j.id job_id, j.script_identifier, j.status::text job_status,
       coalesce(j.stdout,'') stdout,
       t.identifier case_id, t.name case_name,
       rtc.latest_status::text case_status, rtc.updated_at status_at
from test_run_script_jobs j
join test_runs r on r.id = j.test_run_id
join web_tests w on w.id = j.script_id
join test_cases t on t.id = w.test_case_id
join test_run_test_cases rtc on rtc.test_run_id = r.id and rtc.test_case_id = t.id
where j.script_type::text = 'web_test'
  and j.status::text = 'completed'
  and rtc.latest_status::text = 'PASSED'
order by r.created_at
""")
rows = cur.fetchall()
false_pass = []
for r in rows:
    s = r.pop("stdout") or ""
    r["has_compiled_assertion"] = "COMPILED_ASSERTION " in s
    r["has_upload_evidence"] = "UPLOAD_EVIDENCE " in s
    if not (r["has_compiled_assertion"] or r["has_upload_evidence"]):
        r["n_compiled_assertion"] = s.count("COMPILED_ASSERTION ")
        false_pass.append(r)
print(f"WEB_TEST×COMPLETED×PASSED 总计 {len(rows)}，其中零断言误判 {len(false_pass)}")
by_run = {}
for r in false_pass:
    by_run.setdefault(r["run_id"], []).append(r["case_id"])
print("涉及 run:", len(by_run), "| 涉及用例:", len({r['case_id'] for r in false_pass}))
for run, cs in sorted(by_run.items()):
    print(f"  {run}: {len(cs)} 条")

json.dump(
    {"generated_at": datetime.now().isoformat(), "policy": "只读清单，不回刷历史状态；建议后续人工重跑或按此清单修正报表口径",
     "total_webtest_passed": len(rows), "false_passed": len(false_pass),
     "rows": false_pass},
    open(OUT, "w", encoding="utf-8"), ensure_ascii=False, indent=1, default=str,
)
print("已写", OUT)
