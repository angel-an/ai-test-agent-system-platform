# -*- coding: utf-8 -*-
"""rev70：当前框架「需求生成用例 -> 自动执行」再次复验
复用 TR-101 同口径：PR-2 / CS Module 文件夹 32 条用例 -> minimal-loop 重编译 -> create_test_run -> 显式执行 -> 轮询 -> 核验
核验点：32 行结果无重复、状态分布、与 TR-101 基线(P15/S7/NE10)对比
"""
import json
import time
import urllib.error
import urllib.request

BASE = "http://localhost:8000/api/v2"
PR = "PR-2"
FOLDER = "2ed8df3b-8a37-4419-a184-ef83bc16a9a0"
SUB_FUNC = "b0a0b651-5ad2-4dfd-8763-8cb6265aa19b"
ENV = {"WEB_TEST_USERNAME": "lianqiao", "WEB_TEST_PASSWORD": "lianqiao123"}
OUT = r"D:/code/Pyproject/ai-test-agent-system-platform/backend/rev70_loop_result.json"


def req(method, path, body=None, timeout=120):
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
    # 1. 取当前文件夹全部用例
    r = req("GET", f"/projects/{PR}/test-cases?folder_id={FOLDER}&page_size=100")
    data = r.get("data")
    if isinstance(data, dict):
        cases = data.get("items") or data.get("test_cases") or data.get("list") or []
    elif isinstance(data, list):
        cases = data
    else:
        cases = []
    if not cases:
        # 兜底：列表接口结构不同则打印看看
        print("[cases] unexpected resp:", json.dumps(r, ensure_ascii=False)[:400])
        raise SystemExit(2)
    ids = [c["id"] for c in cases]
    print(f"[cases] {len(ids)} 条", flush=True)

    # 2. minimal-loop（重编译 + 建运行）
    payload = {"test_case_ids": ids, "folder_id": FOLDER, "create_test_run": True,
               "sub_function_id": SUB_FUNC, "execution_config": {"env": ENV}}
    r = req("POST", f"/projects/{PR}/web-case-links/minimal-loop", payload, timeout=300)
    json.dump(r, open(OUT, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print("[loop] resp:", json.dumps(r, ensure_ascii=False)[:300], flush=True)
    if r.get("__http__") or r.get("__err__"):
        raise SystemExit(3)
    data = r.get("data") or {}
    tr = (data.get("test_run") or {}).get("identifier")
    print(f"[loop] TR={tr}", flush=True)
    if not tr:
        raise SystemExit(4)

    # 3. 显式派发执行
    er = req("POST", f"/projects/{PR}/test-runs/{tr}/execute", {})
    print("[execute]", json.dumps(er, ensure_ascii=False)[:200], flush=True)

    # 4. 轮询（TR-101 用时约 5.4 分钟，上限 25 分钟）
    final = None
    for i in range(100):
        time.sleep(15)
        d = req("GET", f"/projects/{PR}/test-runs/{tr}")
        run = d.get("data") or {}
        state = run.get("run_state") or run.get("state")
        jobs = [(j.get("script_identifier"), j.get("status")) for j in (run.get("script_jobs") or [])]
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
