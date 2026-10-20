# -*- coding: utf-8 -*-
"""rev80：多表单自动执行——RetroClaim / GoodWill / PhoneCall 三个子功能各编译一个 WT，
追加到同一 TestRun 后统一执行。重点验证「生成→编译→多表单自动执行」链路。"""
import json
import sys
import time
import urllib.request

import psycopg2

BASE = "http://localhost:8000/api/v2"
PR = "PR-9"
FOLDER = "abc46f5b-1653-46e8-a73e-a6aad46ee39c"
ENV = {"WEB_TEST_USERNAME": "lianqiao", "WEB_TEST_PASSWORD": "lianqiao123"}
OUT = r"D:/code/Pyproject/ai-test-agent-system-platform/backend/rev80_multiform_result.json"

# 用例子集按字段归属划分（字段名↔表单 唯一映射部分）
SUBSETS = [
    ("WSF-RC-CREATE", ["TC-284588", "TC-834973", "TC-135305", "TC-719277",
                       "TC-194969", "TC-381853", "TC-236644", "TC-951454", "TC-638287"]),
    ("WSF-GW-CREATE", ["TC-834931", "TC-210283", "TC-680831", "TC-653476", "TC-646407"]),
    ("WSF-TL-PHONE", ["TC-120991", "TC-493252"]),
]


def req(method, path, body=None, timeout=600):
    data = json.dumps(body).encode() if body is not None else None
    r = urllib.request.Request(BASE + path, data=data, method=method,
                               headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(r, timeout=timeout) as resp:
            raw = resp.read().decode()
            return json.loads(raw) if raw else {}
    except urllib.error.HTTPError as e:
        return {"__http__": e.code, "body": e.read().decode()[:800]}


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    conn = psycopg2.connect(host="localhost", port=5432, user="postgres",
                            password="postgres", dbname="langgraph_platform_db")
    cur = conn.cursor()

    tr = None
    results = []
    for idx, (sub_ident, case_idents) in enumerate(SUBSETS):
        cur.execute("SELECT id FROM web_sub_functions WHERE identifier=%s", (sub_ident,))
        sub_id = cur.fetchone()[0]
        cur.execute("SELECT id, identifier FROM test_cases WHERE identifier = ANY(%s::text[])",
                    (case_idents,))
        cases = {r[1]: str(r[0]) for r in cur.fetchall()}
        missing = [c for c in case_idents if c not in cases]
        if missing:
            print(f"[warn] {sub_ident} 缺用例: {missing}")
        ids = [cases[c] for c in case_idents if c in cases]
        print(f"[subset] {sub_ident}: {len(ids)} cases", flush=True)

        payload = {"test_case_ids": ids, "folder_id": FOLDER,
                   "sub_function_id": sub_id,
                   "execution_config": {"env": ENV, "timeout": 1500}}
        if tr is None:
            payload["create_test_run"] = True
        else:
            payload["test_run_identifier"] = tr
        r = req("POST", f"/projects/{PR}/web-case-links/minimal-loop", payload)
        data = r.get("data") or {}
        run = data.get("test_run") or {}
        tr = tr or run.get("identifier")
        wts = [w.get("identifier") for w in (data.get("web_tests") or [])]
        print(f"[loop{idx}] TR={tr} WT={wts} http={r.get('__http__')}", flush=True)
        results.append({"sub": sub_ident, "response": r})
        if not wts:
            print(f"[fail] {sub_ident} 未创建 WT: {json.dumps(r, ensure_ascii=False)[:400]}")
            json.dump(results, open(OUT, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
            raise SystemExit(3)

    json.dump(results, open(OUT, "w", encoding="utf-8"), ensure_ascii=False, indent=1)

    er = req("POST", f"/projects/{PR}/test-runs/{tr}/execute", {})
    print("[execute]", json.dumps(er, ensure_ascii=False)[:200], flush=True)

    final = None
    for i in range(160):
        time.sleep(15)
        d = req("GET", f"/projects/{PR}/test-runs/{tr}")
        run = d.get("data") or {}
        state = run.get("run_state") or run.get("state")
        jobs = [(j.get("script_identifier"), j.get("status")) for j in (run.get("script_jobs") or [])]
        if i % 4 == 0 or state != "in_progress":
            print(f"[poll{i}] state={state} jobs={jobs}", flush=True)
        if jobs and all(j[1] in ("completed", "passed", "failed", "skipped", "error") for j in jobs):
            final = run
            break
    if not final:
        raise SystemExit(5)
    print("[final]", json.dumps({k: final.get(k) for k in
          ("identifier", "run_state")}, ensure_ascii=False), flush=True)
    json.dump(final, open(OUT.replace("_multiform_result", "_run_final"), "w", encoding="utf-8"),
              ensure_ascii=False, indent=1, default=str)
    print("[done] TR=" + str(final.get("identifier")), flush=True)


if __name__ == "__main__":
    main()
