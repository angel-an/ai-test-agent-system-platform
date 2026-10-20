# -*- coding: utf-8 -*-
"""P0 执行抽样：按断言分层抽取三族用例，真实执行并采集结论。

用法：
    python audit_p0_sample_exec.py pilot          # 每族各 1 条试点
    python audit_p0_sample_exec.py full [budget]  # 全量抽样（默认 18 条）

安全前提（已验证）：
    577 条资产绑定 WSF-YL-STORE / GOODS / PAY（非 RW 子功能），其 custom_config
    无 negative_scenarios ⇒ 编译产物不含真实建单流程，只跑表单级检查。
    本脚本仍对每条 stdout 检测写请求（POST /org/shop/add、/org/item、/org/payment 等），
    若出现则记录并中止后续（防脏数据）。
"""
from __future__ import annotations

import json
import sys
import time
from collections import Counter, defaultdict
from pathlib import Path

import httpx
import psycopg2

PROJ_ID = "942226d9-6071-43fa-b2bb-f7cbc93a0028"
BASE = "http://127.0.0.1:8000/api/v2/projects/PR-9"
BASE_URL = "https://console.uat.eatizen.com.hk/dtcloud-console-web-pc/#/login"
OUT = Path("out_yl_case_audit/p0_runs")
OUT.mkdir(parents=True, exist_ok=True)
TRANSPORT = httpx.HTTPTransport(retries=1, proxy=None)
DSN = dict(host="127.0.0.1", port=5432, user="postgres",
           password="postgres", dbname="langgraph_platform_db")
DONE = {"completed", "failed", "blocked", "cancelled", "error"}
WRITE_MARKERS = ("/org/shop/add", "/org/item/add", "/org/item", "/org/payment")

FAMILIES = {
    "店铺": {"module": "店鋪主數據管理", "sf": "e1c94163-88b6-4e3f-a4c9-5ec8b49a8878"},
    "商品": {"module": "商品主數據", "sf": "d90c2034-0ac2-4f12-91c7-a0be5f2f2103"},
    "支付": {"module": "支付方式主數據", "sf": "af320aac-6b7d-4d4e-8d7e-2be5b8569a02"},
}


def db():
    return psycopg2.connect(**DSN)


def sf_uuid_map():
    """按 identifier 取真实子功能 UUID（避免硬编码错误）。"""
    conn = db()
    cur = conn.cursor()
    cur.execute("select identifier, id::text from web_sub_functions "
                "where identifier in (%s,%s,%s)",
                ("WSF-YL-STORE", "WSF-YL-GOODS", "WSF-YL-PAY"))
    m = dict(cur.fetchall())
    conn.close()
    return {"店铺": m["WSF-YL-STORE"], "商品": m["WSF-YL-GOODS"], "支付": m["WSF-YL-PAY"]}


def pick_samples(per_family: dict[str, dict[str, int]]) -> list[dict]:
    """按断言数分桶均匀取样；桶内按 identifier 排序后等距抽取。"""
    conn = db()
    cur = conn.cursor()
    picks: list[dict] = []
    for fam, buckets in per_family.items():
        mod = FAMILIES[fam]["module"]
        cur.execute("""
        select t.identifier, t.id::text, w.identifier,
               coalesce((w.generation_params->'compiled_cases'->>'assertion_count')::int, 0) as ac
        from web_tests w join test_cases t on t.id = w.test_case_id
        where w.project_id = %s and w.generated_by_agent = 'web_case_link_service'
          and t.module = %s
        order by t.identifier
        """, (PROJ_ID, mod))
        rows = cur.fetchall()
        by_bucket = defaultdict(list)
        for r in rows:
            ac = r[3]
            key = "3plus" if ac >= 3 else ("1_2" if ac >= 1 else "0")
            by_bucket[key].append(r)
        for bucket, n in buckets.items():
            pool = by_bucket.get(bucket, [])
            if not pool or n <= 0:
                continue
            step = max(1, len(pool) // n)
            chosen = [pool[i * step] for i in range(min(n, len(pool)))]
            for case_ident, case_id, wt_ident, ac in chosen:
                picks.append({"family": fam, "bucket": bucket, "case": case_ident,
                              "case_id": case_id, "asset": wt_ident, "assertions": ac})
    conn.close()
    return picks


def post(path: str, payload: dict | None = None):
    with httpx.Client(transport=TRANSPORT, timeout=180.0) as c:
        return c.post(BASE + path, json=payload if payload is not None else {})


def run_one(p: dict, sf_id: str, budget: int) -> dict:
    body = {
        "test_case_ids": [p["case_id"]],
        "sub_function_id": sf_id,
        "base_url": BASE_URL,
        "create_test_run": True,
        "primary_test_case_id": p["case_id"],
    }
    t0 = time.time()
    r = post("/web-case-links/minimal-loop", body)
    if r.status_code != 200:
        return {**p, "phase": "minimal_loop", "http": r.status_code,
                "error": r.text[:400], "seconds": round(time.time() - t0, 1)}
    data = r.json()["data"]
    tr = data.get("test_run") or {}
    tr_ident = tr.get("identifier")
    jobs = tr.get("script_jobs") or []
    if not tr_ident or not jobs:
        return {**p, "phase": "no_test_run", "seconds": round(time.time() - t0, 1)}
    job_id = jobs[0]["id"]
    wt_ident = jobs[0].get("script_identifier")

    r = post(f"/test-runs/{tr_ident}/execute")
    if r.status_code != 200:
        return {**p, "phase": "execute", "http": r.status_code, "run": tr_ident,
                "error": r.text[:400], "seconds": round(time.time() - t0, 1)}

    status = None
    while time.time() - t0 < budget:
        conn = db()
        cur = conn.cursor()
        cur.execute("select status from test_run_script_jobs where id=%s", (job_id,))
        row = cur.fetchone()
        conn.close()
        if row and str(row[0]) in DONE:
            status = str(row[0])
            break
        time.sleep(8)

    conn = db()
    cur = conn.cursor()
    cur.execute("""select status, duration_ms, error_message::text,
                          result_summary::text, stdout
                   from test_run_script_jobs where id=%s""", (job_id,))
    row = cur.fetchone() or (None, None, None, None, "")
    cur.execute("select status, total_tests, passed_tests, failed_tests "
                "from web_test_runs where identifier=%s", (tr_ident,))
    run_row = cur.fetchone() or (None, None, None, None)
    conn.close()

    st, dur, err, summ, so = row
    so = so or ""
    (OUT / f"{tr_ident}.txt").write_text(
        f"case={p['case']} asset={wt_ident} job_status={st} dur_ms={dur}\n"
        f"=== error ===\n{err or ''}\n=== summary ===\n{summ or ''}\n"
        f"=== stdout ===\n{so}\n", encoding="utf-8")

    asserts = [ln.strip() for ln in so.splitlines() if "COMPILED_ASSERTION " in ln]
    summary_line = next((ln.strip() for ln in so.splitlines()
                         if "COMPILED_ASSERTION_SUMMARY" in ln), "")
    writes = [m for m in WRITE_MARKERS if m in so]
    return {**p, "phase": "done", "run": tr_ident, "job": job_id, "asset_used": wt_ident,
            "job_status": st, "run_status": run_row[0],
            "total_passed_failed": list(run_row[1:]) if run_row[0] else None,
            "duration_ms": dur, "assertion_lines": asserts[:12],
            "assertion_summary": summary_line[:400],
            "write_requests_seen": writes,
            "stdout_len": len(so), "seconds": round(time.time() - t0, 1)}


def main() -> int:
    mode = sys.argv[1] if len(sys.argv) > 1 else "pilot"
    budget = int(sys.argv[2]) if len(sys.argv) > 2 else 480
    sfs = sf_uuid_map()
    if mode == "pilot":
        plan = {"店铺": {"3plus": 1, "1_2": 0, "0": 0},
                "商品": {"3plus": 0, "1_2": 1, "0": 0},
                "支付": {"3plus": 0, "1_2": 1, "0": 0}}
    else:
        plan = {"店铺": {"3plus": 3, "1_2": 2, "0": 2},
                "商品": {"3plus": 0, "1_2": 2, "0": 1},
                "支付": {"3plus": 0, "1_2": 2, "0": 1}}

    samples = pick_samples(plan)
    # full 模式：跳过 pilot 已覆盖的用例，结论直接沿用，避免重复执行
    carried: list[dict] = []
    if mode == "full":
        prev = Path("out_yl_case_audit/p0_sample_exec_pilot.json")
        if prev.exists():
            prior = json.loads(prev.read_text(encoding="utf-8")).get("results", [])
            done = {r["case"] for r in prior if r.get("phase") == "done"}
            carried = [r for r in prior if r.get("phase") == "done"]
            samples = [s for s in samples if s["case"] not in done]
            print(f"[carry] 沿用 pilot 结论 {len(carried)} 条，剩余待跑 {len(samples)} 条")
    print(f"[plan] {len(samples)} 条")
    for p in samples:
        print(f"  {p['family']} {p['bucket']} {p['case']} assert={p['assertions']} {p['asset']}")

    results = []
    t_all = time.time()
    for i, p in enumerate(samples, 1):
        print(f"[{i}/{len(samples)}] {p['family']} {p['case']} ...", flush=True)
        res = run_one(p, sfs[p["family"]], budget)
        results.append(res)
        print(f"    -> {res.get('phase')} job={res.get('job_status')} run={res.get('run_status')} "
              f"pass/fail={res.get('total_passed_failed')} writes={res.get('write_requests_seen')} "
              f"{res.get('seconds')}s", flush=True)
        if res.get("write_requests_seen"):
            print("    !! 检测到写请求，中止后续执行以防脏数据", flush=True)
            break

    out = Path(f"out_yl_case_audit/p0_sample_exec_{mode}.json")
    out.write_text(json.dumps({
        "run_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "mode": mode, "samples": samples, "results": carried + results,
        "elapsed_min": round((time.time() - t_all) / 60, 1),
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[done] {len(results)} 条 -> {out}  ({round((time.time()-t_all)/60,1)} min)")

    agg = Counter()
    for r in carried + results:
        key = (r["family"], r.get("job_status"), "writes" if r.get("write_requests_seen") else "clean")
        agg[key] += 1
    for k, v in sorted(agg.items(), key=lambda x: str(x[0])):
        print("  ", k, v)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
