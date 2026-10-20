# -*- coding: utf-8 -*-
"""商品 / 支付方式两模块全量执行器（可续跑、增量落盘、可并发）。

用法：
    python audit_gp_full_exec.py list                     # 只读：打印本次执行范围
    python audit_gp_full_exec.py run --limit 6 --c 3      # 冒烟：6 条 3 并发
    python audit_gp_full_exec.py run --c 3                # 全量（跳过已在本轮完成的）

设计要点：
- 每个资产一次 minimal-loop（create_test_run=True）→ execute → 轮询 job 到终态；
- 并发由本脚本控制，单条独立 run，互不影响；
- 增量写 out_yl_case_audit/gp_full_exec_results.jsonl，中断可直接续跑；
- 检测到写请求（建单/新增）立即把该条标 dirty 并停止该 worker，防脏数据；
- 结论按「断言/检查的可执行性」分类，不用 run 的 completed 冒充通过。
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time
from collections import Counter
from pathlib import Path

import httpx
import psycopg2

PROJ_ID = "942226d9-6071-43fa-b2bb-f7cbc93a0028"
PROJ_IDENT = "PR-9"
BASE = f"http://127.0.0.1:8000/api/v2/projects/{PROJ_IDENT}"
BASE_URL = "https://console.uat.eatizen.com.hk/dtcloud-console-web-pc/#/login"
OUT_DIR = Path("out_yl_case_audit")
OUT_DIR.mkdir(parents=True, exist_ok=True)
RESULT_FILE = OUT_DIR / "gp_full_exec_results.jsonl"

DSN = dict(host="127.0.0.1", port=5432, user="postgres",
           password="postgres", dbname="langgraph_platform_db")
DONE = {"completed", "failed", "blocked", "cancelled", "error"}
WRITE_MARKERS = ("/org/shop/add", "/org/item/add", "/org/payment/add", "/org/payment/save",
                 "/org/item/save")

MODULES = {
    "商品-文档生成": "商品主數據",
    "商品-人工导入": "YL-主數據管理-商品",
    "支付-文档生成": "支付方式主數據",
    "支付-人工导入": "YL-主數據管理-支付方式",
}


def db():
    return psycopg2.connect(**DSN)


def load_scope() -> list[dict]:
    """取两个模块的全部 web 脚本资产（含上次断言数与上次结论）。"""
    conn = db()
    cur = conn.cursor()
    cur.execute("""
    select t.module, t.identifier, t.id::text, w.identifier, w.id::text, w.sub_function_id::text,
           coalesce((w.generation_params->'compiled_cases'->>'assertion_count')::int, 0),
           coalesce(t.custom_fields->>'content_source', t.custom_fields->>'generated_from', ''),
           coalesce(t.priority::text, ''),
           lr.status, lr.created_at
    from web_tests w
    join test_cases t on t.id = w.test_case_id
    left join lateral (
      select r.status, r.created_at from web_test_runs r
      where r.web_test_id = w.id order by r.created_at desc limit 1
    ) lr on true
    where w.project_id = %s and w.generated_by_agent = 'web_case_link_service'
      and t.module = any(%s)
    order by t.module, t.identifier
    """, (PROJ_ID, list(MODULES.values())))
    rows = []
    for (mod, case_i, case_id, wt_i, wt_id, sf, ac, src, prio, last_st, last_at) in cur.fetchall():
        rows.append({
            "module": mod, "case": case_i, "case_id": case_id,
            "asset": wt_i, "asset_id": wt_id, "sub_function_id": sf,
            "prev_assertions": ac, "source": src, "priority": prio,
            "last_status": str(last_st) if last_st else None,
            "last_at": last_at.isoformat() if last_at else None,
        })
    conn.close()
    return rows


def load_done() -> dict[str, dict]:
    """读取本轮已完成（有终态）的结果，用于续跑跳过。"""
    done: dict[str, dict] = {}
    if not RESULT_FILE.exists():
        return done
    for line in RESULT_FILE.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            r = json.loads(line)
        except Exception:  # noqa: BLE001
            continue
        if r.get("verdict") in {"PASSED", "PARTIAL", "UNVERIFIED", "FAILED_ASSERT", "NOT_EXECUTED", "DIRTY_ABORT"}:
            done[r["case"]] = r
    return done


def append_result(r: dict) -> None:
    with RESULT_FILE.open("a", encoding="utf-8") as f:
        f.write(json.dumps(r, ensure_ascii=False) + "\n")


def parse_summary(stdout: str) -> dict:
    for ln in (stdout or "").splitlines():
        if "COMPILED_ASSERTION_SUMMARY" in ln:
            try:
                return json.loads(ln[ln.index("{"):])
            except Exception:  # noqa: BLE001
                return {}
    return {}


def parse_coverage(stdout: str) -> dict:
    for ln in (stdout or "").splitlines():
        if "COMPILED_COVERAGE" in ln:
            try:
                return json.loads(ln[ln.index("{"):])
            except Exception:  # noqa: BLE001
                return {}
    return {}


def assertion_lines(stdout: str) -> list[dict]:
    out = []
    for ln in (stdout or "").splitlines():
        if "COMPILED_ASSERTION " in ln and "SUMMARY" not in ln:
            try:
                out.append(json.loads(ln[ln.index("{"):]))
            except Exception:  # noqa: BLE001
                pass
    return out


def verdict_of(job_status: str | None, stdout: str, summ: dict, cov: dict,
               err: str | None = None) -> tuple[str, str]:
    """按可执行性给结论；completed 不等于通过，执行侧失败也不等于是产品缺陷。"""
    asserts = assertion_lines(stdout)
    if job_status == "failed":
        bad = [a for a in asserts if a.get("outcome") in ("blocked", "fail")]
        if bad:
            return "FAILED_ASSERT", f"断言异常 {len(bad)}/{len(asserts)} 条"
        e = (err or "").strip()
        if any(k in e for k in ("菜单", "定位器", "无法打开表单", "timeout", "Timeout", "超时")):
            return "BLOCKED_EXEC", f"执行侧阻断：{e[:120]}"
        return "FAILED_EXEC", f"执行失败：{e[:120]}"
    if not asserts:
        return "NOT_EXECUTED", "零可执行断言（plan 无检查或全部 pending_locator）"
    if job_status != "completed":
        return "BLOCKED_EXEC", f"作业未完成：{job_status}"
    total = summ.get("total", len(asserts))
    unv = summ.get("unverified", 0)
    blk = summ.get("blocked", 0)
    pas = summ.get("pass", 0)
    if blk > 0:
        return "FAILED_ASSERT", f"{blk}/{total} 检查被阻断"
    if pas == 0 and unv > 0:
        return "UNVERIFIED", f"全部 {unv} 条无法在 UI 层判定（需 API 层）"
    if pas == 0:
        return "NOT_EXECUTED", "有断言行但 0 通过（断言状态未落地）"
    if unv > 0:
        return "PARTIAL", f"{pas}/{total} 通过，{unv} 条无法在 UI 层判定"
    return "PASSED", f"{pas}/{total} 检查通过"


async def post_retry(client: httpx.AsyncClient, url: str, body: dict, timeout: float,
                     *, attempts: int = 6) -> httpx.Response:
    """并发创建 run 时后端 TR 编号会撞唯一键（ix_test_runs_identifier），重试即可。"""
    last: httpx.Response | None = None
    for i in range(attempts):
        try:
            r = await client.post(url, json=body, timeout=timeout)
        except Exception:  # noqa: BLE001
            if i == attempts - 1:
                raise
            await asyncio.sleep(1.0 + i * 0.8)
            continue
        last = r
        if r.status_code == 200:
            return r
        text = r.text or ""
        if "ix_test_runs_identifier" in text or "duplicate key value" in text:
            await asyncio.sleep(1.2 + i * 1.0)
            continue
        return r
    return last  # type: ignore[return-value]


async def compile_one(client: httpx.AsyncClient, item: dict, sem: asyncio.Semaphore) -> dict:
    """只编译不建运行：拿到 compile_receipt，判定该用例是否有可执行检查。"""
    async with sem:
        body = {
            "test_case_ids": [item["case_id"]],
            "sub_function_id": item["sub_function_id"],
            "base_url": BASE_URL,
            "create_test_run": False,
        }
        try:
            r = await post_retry(client, f"{BASE}/web-case-links/minimal-loop", body, 300.0,
                                 attempts=4)
        except Exception as exc:  # noqa: BLE001
            return {**item, "phase": "compile", "status": "ERROR", "reason": f"请求异常: {exc}"}
        if r.status_code != 200:
            return {**item, "phase": "compile", "status": "ERROR",
                    "reason": f"HTTP {r.status_code}: {r.text[:300]}"}
        data = r.json().get("data") or {}
        rc = data.get("compile_receipt") or {}
        checks = rc.get("form_executable_checks") or 0
        pending = rc.get("pending_locator_count") or 0
        skipped = rc.get("skipped_assertion_count") or 0
        if checks > 0:
            status = "EXECUTABLE"
        elif pending > 0:
            status = "NO_CHECK_PENDING_LOCATOR"
        elif skipped > 0:
            status = "NO_CHECK_SKIPPED"
        else:
            status = "NO_ASSERTION"
        return {
            "phase": "compile", "module": item["module"], "case": item["case"],
            "asset": item["asset"], "priority": item["priority"], "source": item["source"],
            "status": status, "form_executable_checks": checks,
            "pending_locator_count": pending, "skipped_assertion_count": skipped,
            "assertion_check_count": rc.get("assertion_check_count"),
            "negative_scenario_count": rc.get("negative_scenario_count"),
            "locator_source": rc.get("form_locator_source"),
            "warnings": (rc.get("warnings") or [])[:3],
            "web_path_match_status": (data.get("web_tests") or [{}])[0].get("web_path_match_status"),
            "compiled_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        }


async def run_compile(items: list[dict], concurrency: int) -> None:
    sem = asyncio.Semaphore(concurrency)
    limits = httpx.Limits(max_connections=concurrency + 2, max_keepalive_connections=concurrency)
    transport = httpx.AsyncHTTPTransport(retries=1, limits=limits)
    path = OUT_DIR / "gp_compile.jsonl"
    path.write_text("", encoding="utf-8")
    lock = asyncio.Lock()
    n = 0

    async with httpx.AsyncClient(transport=transport) as client:
        async def worker(it):
            nonlocal n
            res = await compile_one(client, it, sem)
            async with lock:
                with path.open("a", encoding="utf-8") as f:
                    f.write(json.dumps(res, ensure_ascii=False) + "\n")
                n += 1
                print(f"[{n}/{len(items)}] {res['module']} {res['case']} -> {res['status']} "
                      f"checks={res.get('form_executable_checks')} pending={res.get('pending_locator_count')}",
                      flush=True)
        await asyncio.gather(*(worker(it) for it in items))


async def run_one(client: httpx.AsyncClient, item: dict, budget: int, sem: asyncio.Semaphore) -> dict:
    async with sem:
        t0 = time.time()
        base = {
            "module": item["module"], "case": item["case"], "asset": item["asset"],
            "prev_assertions": item["prev_assertions"], "source": item["source"],
            "priority": item["priority"], "started_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        }
        body = {
            "test_case_ids": [item["case_id"]],
            "sub_function_id": item["sub_function_id"],
            "base_url": BASE_URL,
            "create_test_run": True,
        }
        try:
            r = await post_retry(client, f"{BASE}/web-case-links/minimal-loop", body, 300.0)
        except Exception as exc:  # noqa: BLE001
            return {**base, "verdict": "ERROR", "reason": f"minimal-loop 请求异常: {exc}",
                    "seconds": round(time.time() - t0, 1)}
        if r.status_code != 200:
            return {**base, "verdict": "ERROR", "reason": f"minimal-loop HTTP {r.status_code}: {r.text[:300]}",
                    "seconds": round(time.time() - t0, 1)}

        data = r.json().get("data") or {}
        tr = data.get("test_run") or {}
        tr_ident = tr.get("identifier")
        jobs = tr.get("script_jobs") or []
        receipt = data.get("compile_receipt") or {}
        if not tr_ident or not jobs:
            return {**base, "verdict": "ERROR", "reason": "未生成测试运行/作业",
                    "compile_receipt": receipt, "seconds": round(time.time() - t0, 1)}
        job_id = jobs[0]["id"]

        try:
            r = await client.post(f"{BASE}/test-runs/{tr_ident}/execute", json={}, timeout=120.0)
        except Exception as exc:  # noqa: BLE001
            return {**base, "run": tr_ident, "verdict": "ERROR",
                    "reason": f"execute 请求异常: {exc}", "seconds": round(time.time() - t0, 1)}
        if r.status_code != 200:
            return {**base, "run": tr_ident, "verdict": "ERROR",
                    "reason": f"execute HTTP {r.status_code}: {r.text[:300]}",
                    "seconds": round(time.time() - t0, 1)}

        job_status = None
        while time.time() - t0 < budget:
            await asyncio.sleep(6)
            conn = db()
            cur = conn.cursor()
            cur.execute("select status from test_run_script_jobs where id=%s", (job_id,))
            row = cur.fetchone()
            conn.close()
            if row and str(row[0]) in DONE:
                job_status = str(row[0])
                break

        conn = db()
        cur = conn.cursor()
        cur.execute("""select status, duration_ms, error_message::text, result_summary::text, stdout
                       from test_run_script_jobs where id=%s""", (job_id,))
        row = cur.fetchone() or (None, None, None, None, "")
        conn.close()
        st, dur, err, _summ_txt, so = row
        so = so or ""
        summ = parse_summary(so)
        cov = parse_coverage(so)
        asserts = assertion_lines(so)
        verdict, reason = verdict_of(st, so, summ, cov, err)
        if st is not None and str(st) not in DONE:
            verdict, reason = "TIMEOUT", f"轮询 {budget}s 未到终态（当前 {st}）"
            # 主动取消：避免卡住的子进程拖到 600s 被进程守护强杀，连带影响后端
            try:
                await client.post(f"{BASE}/test-runs/{tr_ident}/cancel", json={}, timeout=30.0)
            except Exception:  # noqa: BLE001
                pass
        dirty = [m for m in WRITE_MARKERS if m in so]
        if dirty:
            verdict = "DIRTY_ABORT"

        detail_path = OUT_DIR / "gp_runs"
        detail_path.mkdir(parents=True, exist_ok=True)
        (detail_path / f"{tr_ident}.txt").write_text(
            f"case={item['case']} asset={item['asset']} job={st} dur={dur}\n"
            f"=== error ===\n{err or ''}\n=== stdout ===\n{so}\n", encoding="utf-8")

        attributed = [a for a in asserts if a.get("case_id") or a.get("rule_id")]
        config_only = [a for a in asserts if not (a.get("case_id") or a.get("rule_id"))]
        return {
            **base, "run": tr_ident, "job": job_id, "job_status": st,
            "verdict": verdict, "reason": reason,
            "plan_checks": summ.get("plan_checks", len(asserts)),
            "pass": summ.get("pass"), "unverified": summ.get("unverified"),
            "blocked": summ.get("blocked"), "total": summ.get("total"),
            "attributed_checks": len(attributed), "config_only_checks": len(config_only),
            "assertions": [
                {"name": a.get("name"), "outcome": a.get("outcome"),
                 "observed": (a.get("observed") or "")[:220],
                 "rule_id": a.get("rule_id") or "", "case_id": a.get("case_id") or ""}
                for a in asserts[:20]
            ],
            "coverage": cov, "dirty_markers": dirty,
            "stdout_len": len(so), "duration_ms": dur,
            "seconds": round(time.time() - t0, 1),
        }


RETRYABLE = {"BLOCKED_EXEC", "FAILED_EXEC", "ERROR", "TIMEOUT"}


async def wait_backend(client: httpx.AsyncClient, max_wait: int = 180) -> bool:
    """执行前后端存活检查：后端中断时等待其恢复，避免把环境故障记成用例结论。"""
    t0 = time.time()
    while time.time() - t0 < max_wait:
        try:
            r = await client.get("http://127.0.0.1:8000/health", timeout=5.0)
            if r.status_code == 200:
                return True
        except Exception:  # noqa: BLE001
            pass
        await asyncio.sleep(5)
    return False


async def run_all(items: list[dict], concurrency: int, budget: int, retry: int = 1) -> None:
    sem = asyncio.Semaphore(concurrency)
    limits = httpx.Limits(max_connections=concurrency + 2, max_keepalive_connections=concurrency)
    transport = httpx.AsyncHTTPTransport(retries=1, limits=limits)
    done_n = 0
    total = len(items)
    lock = asyncio.Lock()

    async with httpx.AsyncClient(transport=transport) as client:
        async def worker(it):
            nonlocal done_n
            attempts = 0
            while True:
                attempts += 1
                alive = await wait_backend(client)
                res = await run_one(client, it, budget, sem)
                res["attempt"] = attempts
                append_result(res)
                if res.get("verdict") in RETRYABLE and attempts <= retry:
                    print(f"    ↻ 重试 {it['case']}（首次 {res['verdict']}）", flush=True)
                    await asyncio.sleep(3)
                    continue
                break
            async with lock:
                done_n += 1
                print(f"[{done_n}/{total}] {res['module']} {res['case']} -> {res['verdict']} "
                      f"({res.get('pass')}/{res.get('total')}, plan={res.get('plan_checks')}, "
                      f"try{res.get('attempt')}, {res.get('seconds')}s) {res.get('reason','')[:70]}", flush=True)

        await asyncio.gather(*(worker(it) for it in items))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("mode", choices=["list", "compile", "run"])
    ap.add_argument("--module", default=None, help="模块键：商品-文档生成 / 商品-人工导入 / 支付-文档生成 / 支付-人工导入")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--c", type=int, default=3, help="并发数")
    ap.add_argument("--budget", type=int, default=420, help="单条超时秒")
    ap.add_argument("--retry", type=int, default=1, help="失败重试次数（0=不重试）")
    ap.add_argument("--force", action="store_true", help="忽略已完成的续跑跳过")
    ap.add_argument("--all", action="store_true", help="连零断言用例一起执行（默认只跑编译期可执行的）")
    ap.add_argument("--cases", default=None, help="只跑指定用例，逗号分隔（复测用）")
    args = ap.parse_args()

    scope = load_scope()
    if args.module:
        scope = [s for s in scope if s["module"] == args.module]
    by_mod = Counter(s["module"] for s in scope)

    if args.mode == "list":
        print(f"范围内脚本资产 {len(scope)} 条")
        for m, n in by_mod.items():
            ac1 = sum(1 for s in scope if s["module"] == m and s["prev_assertions"] >= 1)
            print(f"  {m}: {n} 条（上次编译有断言 {ac1}，零断言 {n-ac1}）")
        print(f"\n上批结论分布: {dict(Counter(s['last_status'] or 'never' for s in scope))}")
        return 0

    done = {} if args.force else load_done()

    if args.mode == "compile":
        todo = [s for s in scope if s["case"] not in done]
        if args.limit:
            todo = todo[: args.limit]
        print(f"[compile] 范围 {len(scope)}，本轮待编译 {len(todo)}，并发 {args.c}")
        t0 = time.time()
        asyncio.run(run_compile(todo, args.c))
        print(f"[done] {len(todo)} 条，用时 {round((time.time()-t0)/60,1)} 分钟")
        return 0

    todo = [s for s in scope if s["case"] not in done]
    # 默认只执行「编译期判定有可执行检查」的用例；零断言/纯 pending 的不启动浏览器
    if not args.all:
        comp_path = OUT_DIR / "gp_compile.jsonl"
        if comp_path.exists():
            cmp_rows = {}
            for line in comp_path.read_text(encoding="utf-8").splitlines():
                if line.strip():
                    try:
                        r = json.loads(line)
                    except Exception:  # noqa: BLE001
                        continue
                    cmp_rows[r["case"]] = r
            todo = [s for s in todo if cmp_rows.get(s["case"], {}).get("status") == "EXECUTABLE"]
    if args.cases:
        want = {c.strip() for c in args.cases.split(",") if c.strip()}
        todo = [s for s in todo if s["case"] in want]
    # 排序：从未跑过 → 中断/超时过 → 阻断过（阻断多的排最后，避免反复撞同一堵墙）
    prior = load_done()
    allrows: dict[str, list[str]] = {}
    if RESULT_FILE.exists():
        for line in RESULT_FILE.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                r = json.loads(line)
            except Exception:  # noqa: BLE001
                continue
            allrows.setdefault(r["case"], []).append(r.get("verdict") or "")

    def prio(s: dict) -> tuple:
        vs = allrows.get(s["case"], [])
        if not vs:
            return (0, 0)
        blocked = sum(1 for v in vs if v == "BLOCKED_EXEC")
        if any(v in ("ERROR", "TIMEOUT", "FAILED_EXEC") for v in vs):
            return (1, blocked)
        return (2, blocked)
    if args.limit:
        todo = sorted(todo, key=prio)[: args.limit]
    print(f"[run] 范围 {len(scope)}，已完成 {len(done)}，本轮待跑 {len(todo)}，并发 {args.c}")
    if not todo:
        print("[run] 无待跑条目")
        return 0
    t0 = time.time()
    asyncio.run(run_all(todo, args.c, args.budget, args.retry))
    print(f"[done] {len(todo)} 条，用时 {round((time.time()-t0)/60,1)} 分钟")
    return 0


if __name__ == "__main__":
    sys.exit(main())
