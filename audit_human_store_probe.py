# -*- coding: utf-8 -*-
"""单条实测：店铺「已有断言 + 已有编译产物」的人工用例，只差执行能否出结论。"""
from __future__ import annotations

import sys
import time

import httpx
import psycopg2

PROJ = "PR-9"
PROJ_ID = "942226d9-6071-43fa-b2bb-f7cbc93a0028"
BASE = f"http://127.0.0.1:8000/api/v2/projects/{PROJ}"
BASE_URL = "https://console.uat.eatizen.com.hk/dtcloud-console-web-pc/#/login"
DSN = dict(host="127.0.0.1", port=5432, user="postgres", password="postgres",
           dbname="langgraph_platform_db")
DONE = {"completed", "failed", "cancelled", "canceled", "timeout"}

CASE = sys.argv[1] if len(sys.argv) > 1 else "TC-122613"


def db():
    return psycopg2.connect(**DSN)


conn = db()
cur = conn.cursor()
cur.execute("""
select t.id::text, t.identifier, t.name, w.sub_function_id::text
  from test_cases t join web_tests w on w.test_case_id = t.id
 where t.project_id = %s and t.identifier = %s
""", (PROJ_ID, CASE))
row = cur.fetchone()
conn.close()
if not row:
    print("case not found")
    raise SystemExit(1)
case_id, ident, name, sf_id = row
print(f"[case] {ident} {name}")

t0 = time.time()
with httpx.Client(timeout=300.0) as client:
    body = {"test_case_ids": [case_id], "sub_function_id": sf_id,
            "base_url": BASE_URL, "create_test_run": True}
    r = client.post(f"{BASE}/web-case-links/minimal-loop", json=body)
    print(f"[minimal-loop] HTTP {r.status_code} {round(time.time()-t0,1)}s")
    if r.status_code != 200:
        print(r.text[:800])
        raise SystemExit(1)
    data = r.json().get("data") or {}
    tr = data.get("test_run") or {}
    receipt = data.get("compile_receipt") or {}
    print(f"[receipt] status={receipt.get('compile_status')} "
          f"checks={receipt.get('form_executable_checks')} "
          f"pending={receipt.get('pending_locator_count')} "
          f"skipped={receipt.get('skipped_assertion_count')}")
    tr_ident = tr.get("identifier")
    jobs = tr.get("script_jobs") or []
    if not tr_ident or not jobs:
        print("[error] 未生成测试运行/作业")
        raise SystemExit(1)
    job_id = jobs[0]["id"]
    print(f"[run] {tr_ident} job={job_id}")

    r = client.post(f"{BASE}/test-runs/{tr_ident}/execute", json={})
    print(f"[execute] HTTP {r.status_code}")

    st = None
    budget = 240
    while time.time() - t0 < budget:
        time.sleep(8)
        conn = db()
        cur = conn.cursor()
        cur.execute("select status from test_run_script_jobs where id=%s", (job_id,))
        got = cur.fetchone()
        conn.close()
        if got and str(got[0]) in DONE:
            st = str(got[0])
            break
    print(f"[status] {st or 'TIMEOUT'}  {round(time.time()-t0,1)}s")

    conn = db()
    cur = conn.cursor()
    cur.execute("""select status, duration_ms, error_message::text, stdout
                     from test_run_script_jobs where id=%s""", (job_id,))
    st2, dur, err, so = cur.fetchone()
    conn.close()
    so = so or ""
    print(f"[job] status={st2} duration={dur}ms")
    if err:
        print(f"[err] {err[:300]}")
    keys = ("COMPILED_ASSERTION", "SUMMARY", "COVERAGE", "UNVERIFIED", "pending_locator",
            "ASSERTION_RESULT", "BLOCKED")
    for ln in so.splitlines():
        if any(k in ln for k in keys):
            print("  |", ln.strip()[:200])
    print(f"[stdout len] {len(so)}")
