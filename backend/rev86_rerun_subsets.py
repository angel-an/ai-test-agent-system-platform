# -*- coding: utf-8 -*-
"""rev85：按规则来源表把全部 CS 用例分发到 7 个子功能，编译进同一 TestRun 统一执行。
归属规则（rev82 全文档解析 + rev75-78 探针）：
- table 25（Timeline 大表）按行：3-6=Note 弹窗，8-12=Task 新页，13-19=Post 弹窗，20-27=PhoneCall 新页
- tables 27/29/32 + table43 rows1-4 & 11-14 → RetroClaim
- tables 36/38/40/41 + table43 rows5-10 → GoodWill
- 其余 → 建单表单
"""
import json
import sys
import time
import urllib.request

import psycopg2

BASE = "http://localhost:8000/api/v2"
PR = "PR-9"
FOLDER = "abc46f5b-1653-46e8-a73e-a6aad46ee39c"
ENV = {"WEB_TEST_USERNAME": "lianqiao", "WEB_TEST_PASSWORD": "lianqiao123"}
OUT = r"D:/code/Pyproject/ai-test-agent-system-platform/backend/rev86_rerun_result.json"


def classify(table, row):
    if table == 25:
        if 3 <= row <= 6:
            return "WSF-TL-NOTE"
        if 8 <= row <= 12:
            return "WSF-TL-TASK"
        if 13 <= row <= 19:
            return "WSF-TL-POST"
        if 20 <= row <= 27:
            return "WSF-TL-PHONE"
        return "WSF-TL-PHONE"
    if table in (27, 29, 32):
        return "WSF-RC-CREATE"
    if table == 43:
        if 1 <= row <= 4 or 11 <= row <= 14:
            return "WSF-RC-CREATE"
        return "WSF-GW-CREATE"
    if table in (36, 38, 40, 41):
        return "WSF-GW-CREATE"
    return "WSF-48576960"


def req(method, path, body=None, timeout=600):
    data = json.dumps(body).encode() if body is not None else None
    r = urllib.request.Request(BASE + path, data=data, method=method,
                               headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(r, timeout=timeout) as resp:
            raw = resp.read().decode()
            return json.loads(raw) if raw else {}
    except urllib.error.HTTPError as e:
        return {"__http__": e.code, "body": e.read().decode()[:500]}


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    conn = psycopg2.connect(host="localhost", port=5432, user="postgres",
                            password="postgres", dbname="langgraph_platform_db")
    cur = conn.cursor()
    cur.execute("""SELECT id, identifier, custom_fields->'requirement_rule'->'source'
                   FROM test_cases WHERE folder_id=%s""", (FOLDER,))
    subsets = {}
    for cid, ident, src in cur.fetchall():
        src = src or {}
        sub = classify(int(src.get("table", -1)), int(src.get("row", -1)))
        subsets.setdefault(sub, []).append((str(cid), ident))
    for sub, items in sorted(subsets.items()):
        print(f"[subset] {sub}: {len(items)} cases: {[i[1] for i in items][:8]}")

    tr = None
    loops = []
    for sub in ("WSF-RC-CREATE", "WSF-GW-CREATE",
                "WSF-TL-TASK", "WSF-TL-POST", "WSF-TL-PHONE"):
        items = subsets.get(sub)
        if not items:
            continue
        cur.execute("SELECT id FROM web_sub_functions WHERE identifier=%s", (sub,))
        sub_id = cur.fetchone()[0]
        payload = {"test_case_ids": [i[0] for i in items], "folder_id": FOLDER,
                   "sub_function_id": sub_id,
                   "execution_config": {"env": ENV, "timeout": 1500}}
        if tr is None:
            payload["create_test_run"] = True
        else:
            payload["test_run_identifier"] = tr
        r = req("POST", f"/projects/{PR}/web-case-links/minimal-loop", payload)
        data = r.get("data") or {}
        tr = tr or (data.get("test_run") or {}).get("identifier")
        wts = [w.get("identifier") for w in (data.get("web_tests") or [])]
        print(f"[loop] {sub} TR={tr} WT={wts} http={r.get('__http__')}", flush=True)
        loops.append({"sub": sub, "wts": wts, "http": r.get("__http__")})
        if not wts:
            print("  FAIL:", json.dumps(r, ensure_ascii=False)[:300])

    json.dump({"test_run": tr, "loops": loops}, open(OUT, "w", encoding="utf-8"),
              ensure_ascii=False, indent=1)
    if not tr:
        raise SystemExit(3)

    er = req("POST", f"/projects/{PR}/test-runs/{tr}/execute", {})
    print("[execute]", json.dumps(er, ensure_ascii=False)[:200], flush=True)

    final = None
    for i in range(260):
        time.sleep(15)
        d = req("GET", f"/projects/{PR}/test-runs/{tr}")
        run = d.get("data") or {}
        state = run.get("run_state") or run.get("state")
        jobs = [(j.get("script_identifier"), j.get("status"))
                for j in (run.get("script_jobs") or [])]
        if i % 4 == 0 or state != "in_progress":
            print(f"[poll{i}] state={state} jobs={jobs}", flush=True)
        if jobs and all(j[1] in ("completed", "passed", "failed", "skipped", "error") for j in jobs):
            final = run
            break
    if not final:
        raise SystemExit(5)
    print("[final]", json.dumps({k: final.get(k) for k in ("identifier", "run_state")}, ensure_ascii=False))
    json.dump(final, open(OUT.replace("_result", "_run_final"), "w", encoding="utf-8"),
              ensure_ascii=False, indent=1, default=str)
    print("[done] TR=" + str(final.get("identifier")), flush=True)


if __name__ == "__main__":
    main()
