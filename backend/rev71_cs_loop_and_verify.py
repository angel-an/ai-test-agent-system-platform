# -*- coding: utf-8 -*-
"""rev71：PR-9 美心 CS Module（96 条用例）用当前框架重新测试
流程：取文件夹全量用例 -> minimal-loop（重编译+建运行）-> 显式执行 -> 轮询 -> 落盘
"""
import json
import time
import urllib.error
import urllib.request

BASE = "http://localhost:8000/api/v2"
PR = "PR-9"
FOLDER = "abc46f5b-1653-46e8-a73e-a6aad46ee39c"  # CS Module
ENV = {"WEB_TEST_USERNAME": "lianqiao", "WEB_TEST_PASSWORD": "lianqiao123"}
OUT = r"D:/code/Pyproject/ai-test-agent-system-platform/backend/rev71_loop_result.json"


def req(method, path, body=None, timeout=180):
    data = json.dumps(body).encode() if body is not None else None
    r = urllib.request.Request(BASE + path, data=data, method=method,
                               headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(r, timeout=timeout) as resp:
            raw = resp.read().decode()
            return json.loads(raw) if raw else {}
    except urllib.error.HTTPError as e:
        return {"__http__": e.code, "body": e.read().decode()[:500]}
    except Exception as e:
        return {"__err__": str(e)}


def main():
    # 1. 全量用例
    r = req("GET", f"/projects/{PR}/test-cases?folder_id={FOLDER}&page_size=200")
    data = r.get("data")
    cases = data if isinstance(data, list) else (
        (data or {}).get("items") or (data or {}).get("test_cases") or [])
    ids = [c["id"] for c in cases]
    print(f"[cases] {len(ids)} 条", flush=True)
    if not ids:
        raise SystemExit(2)

    # 2. minimal-loop（显式指定子功能：cs-case-create-form，属 cs-module，绑定 UAT 路径模板）
    payload = {"test_case_ids": ids, "folder_id": FOLDER,
               "sub_function_id": "2e862766-b6b2-464b-8101-089f2122617a",
               "create_test_run": True,
               "execution_config": {"env": ENV, "timeout": 1500}}
    r = req("POST", f"/projects/{PR}/web-case-links/minimal-loop", payload, timeout=600)
    json.dump(r, open(OUT, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    data = r.get("data") or {}
    tr = (data.get("test_run") or {}).get("identifier")
    wts = [w.get("identifier") for w in (data.get("web_tests") or [])]
    print(f"[loop] TR={tr} WT={wts} http={r.get('__http__') or r.get('__err__')}", flush=True)
    if not tr:
        raise SystemExit(3)

    # 3. 显式派发
    er = req("POST", f"/projects/{PR}/test-runs/{tr}/execute", {})
    print("[execute]", json.dumps(er, ensure_ascii=False)[:200], flush=True)

    # 4. 轮询（96 条，上限 40 分钟）
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
    print("[final]", json.dumps(
        {k: final.get(k) for k in ("identifier", "run_state", "test_cases_count",
                                   "passed_count", "failed_count", "skipped_count",
                                   "blocked_count", "not_executed_count")},
        ensure_ascii=False), flush=True)
    json.dump(final, open(OUT.replace("_loop_result", "_run_final"), "w", encoding="utf-8"),
              ensure_ascii=False, indent=1, default=str)
    print("[done] TR=" + str(final.get("identifier")), flush=True)


if __name__ == "__main__":
    main()
