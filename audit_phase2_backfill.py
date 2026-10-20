# -*- coding: utf-8 -*-
"""阶段 2：受控逐条补归集（商品/支付/店铺）。

用法：python audit_phase2_backfill.py goods|pay|store [chunk=10]

设计（2026-10-17 专业判断定稿）：
- 候选 = 预检 predicted_action=create（执行时点实时拉取，天然幂等可续跑）；
- 显式 sub_function_id（GOODS/PAY/STORE），create_test_run=False（纯归集，不触发执行）；
- 每 chunk 批后 DB 核验 4 门：
    V1 service 族新增行数 == 本批调用数；
    V2 唯一键 (project_id, test_case_id) 0 违例；
    V3 本批用例的绑定标记（custom_fields.web_test_identifier）全部落库；
    V4 本批用例无 web_test_runs 产生（test_case 挂靠的资产均未新增 run）；
  任一门失败 → 立即停止后续批次，exit 1；
- 单条 ValueError/HTTP!=200 记入 failures 继续走完本批（逐条隔离），
  但 chunk 核验门照常判定——新增数按"成功调用数"对账。
产出：out_yl_case_audit/phase2_backfill_<family>.json
"""
import json
import sys
import time
from datetime import datetime
from pathlib import Path

import psycopg2
import requests

BASE = "http://127.0.0.1:8000"
PRECHECK = f"{BASE}/api/v2/projects/PR-9/web-case-links/minimal-loop/precheck"
LOOP = f"{BASE}/api/v2/projects/PR-9/web-case-links/minimal-loop"
OUT = Path("out_yl_case_audit")
OUT.mkdir(exist_ok=True)
PROJECT_ID = "942226d9-6071-43fa-b2bb-f7cbc93a0028"

FAMILIES = {
    "goods": {"module": "商品主數據", "sf": "d90c2034-0ac2-4f12-91c7-a0be5f2f2103"},
    "pay": {"module": "支付方式主數據", "sf": "af320aac-6d37-4cc9-b1d7-1213aa5eced4"},
    "store": {"module": "店鋪主數據管理", "sf": "e1c94163-d221-4ac8-afdc-c2103f989e5f"},
}


def db():
    c = psycopg2.connect(host="127.0.0.1", port=5432, user="postgres",
                         password="postgres", dbname="langgraph_platform_db")
    # autocommit：每条语句独立快照。REPEATABLE READ 长事务会持有首个快照，
    # 看不到服务端归集写入的新行（08:33 实测踩坑：delta 恒为 0）。
    c.autocommit = True
    return c


def svc_count(cur):
    cur.execute("""select count(*) from web_tests
                   where project_id=%s and generated_by_agent='web_case_link_service'""",
                (PROJECT_ID,))
    return cur.fetchone()[0]


def dup_keys(cur):
    cur.execute("""select test_case_id::text, count(*) from web_tests
                   where project_id=%s and generated_by_agent='web_case_link_service'
                     and test_case_id is not null
                   group by 1 having count(*)>1""", (PROJECT_ID,))
    return cur.fetchall()


def main():
    family = sys.argv[1]
    chunk_size = int(sys.argv[2]) if len(sys.argv) > 2 else 10
    cfg = FAMILIES[family]
    sf_id = cfg["sf"]

    c = db()
    cur = c.cursor()

    # 候选：预检实时拉取 predicted_action=create（含 prd_section + evidence 同族）
    r = requests.post(PRECHECK, json={"module": cfg["module"], "limit": 1000}, timeout=120)
    cases = [it for it in r.json()["data"]["cases"] if it["predicted_action"] == "create"]
    report = {
        "run_at": datetime.now().isoformat(timespec="seconds"),
        "family": family, "sub_function_id": sf_id,
        "candidates": len(cases), "chunk_size": chunk_size,
        "calls": [], "chunk_verifications": [], "failures": [],
    }
    print(f"[{family}] 候选 {len(cases)} 条，chunk={chunk_size}")

    def verify_chunk(batch, success_ids, before_count):
        v = {"at": datetime.now().isoformat(timespec="seconds"),
             "batch_size": len(batch), "success_calls": len(success_ids)}
        cur.execute("""select count(*) from web_tests
                       where project_id=%s and generated_by_agent='web_case_link_service'""",
                    (PROJECT_ID,))
        after_count = cur.fetchone()[0]
        v["V1_rows_delta"] = after_count - before_count
        v["V1_expected"] = len(success_ids)
        v["V1_pass"] = (after_count - before_count) == len(success_ids)
        dups = dup_keys(cur)
        v["V2_dup_keys"] = dups
        v["V2_pass"] = not dups
        if success_ids:
            cur.execute("""select count(*) from test_cases
                           where id::text = any(%s) and custom_fields ? 'web_test_identifier'""",
                        (success_ids,))
            marked = cur.fetchone()[0]
            v["V3_marked"] = marked
            v["V3_pass"] = marked == len(success_ids)
        else:
            v["V3_marked"] = 0
            v["V3_pass"] = True
        if success_ids:
            cur.execute("""select count(*) from web_test_runs r
                           join web_tests w on w.id = r.web_test_id
                           where w.test_case_id::text = any(%s)""", (success_ids,))
            v["V4_runs_on_new_assets"] = cur.fetchone()[0]
            v["V4_pass"] = v["V4_runs_on_new_assets"] == 0
        else:
            v["V4_pass"] = True
        v["pass"] = v["V1_pass"] and v["V2_pass"] and v["V3_pass"] and v["V4_pass"]
        report["chunk_verifications"].append(v)
        print(f"  chunk verify: {'PASS' if v['pass'] else 'FAIL'} "
              f"(delta={v['V1_rows_delta']}/{v['V1_expected']}, dups={len(dups)}, "
              f"marked={v['V3_marked']}/{v['V1_expected']}, runs={v.get('V4_runs_on_new_assets', 0)})")
        return v["pass"]

    aborted = False
    total_before = svc_count(cur)
    for i in range(0, len(cases), chunk_size):
        batch = cases[i:i + chunk_size]
        before_count = svc_count(cur)
        success_ids = []
        for it in batch:
            tc_id, ident = it["id"], it["identifier"]
            t0 = time.time()
            try:
                rr = requests.post(LOOP, json={
                    "test_case_ids": [tc_id],
                    "sub_function_id": sf_id,
                    "create_test_run": False,
                }, timeout=300)
                # 429 平台限流：等待 61s 后重试一次（窗口按分钟重置，实测连续 429 后恢复）
                retries = 0
                while rr.status_code == 429 and retries < 5:
                    wait_s = 61 * (retries + 1)
                    print(f"  [{ident}] 429 限流，退避 {wait_s}s 后重试（第 {retries + 1} 次）")
                    time.sleep(wait_s)
                    rr = requests.post(LOOP, json={
                        "test_case_ids": [tc_id],
                        "sub_function_id": sf_id,
                        "create_test_run": False,
                    }, timeout=300)
                    retries += 1
                ok = rr.status_code == 200
                entry = {"identifier": ident, "http": rr.status_code,
                         "seconds": round(time.time() - t0, 2), "retries": retries}
                if ok:
                    wts = rr.json()["data"].get("web_tests") or []
                    entry["web_test"] = [w.get("identifier") for w in wts][:3]
                    success_ids.append(tc_id)
                else:
                    entry["error"] = str(rr.json())[:200]
                    report["failures"].append(entry)
            except Exception as exc:  # noqa: BLE001
                entry = {"identifier": ident, "http": None, "error": str(exc)[:200]}
                report["failures"].append(entry)
            report["calls"].append(entry)
        ok = verify_chunk(batch, success_ids, before_count)
        if not ok:
            aborted = True
            print("!! 核验门失败，停止后续批次")
            break

    c.close()
    report["aborted"] = aborted
    report["service_rows_before"] = total_before
    c = db()
    report["service_rows_after"] = svc_count(c.cursor())
    report["total_delta"] = report["service_rows_after"] - report["service_rows_before"]
    c.close()
    report["all_pass"] = (not aborted) and not report["failures"]

    out_file = OUT / f"phase2_backfill_{family}.json"
    out_file.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print("=" * 60)
    print(f"[{family}] 完成：调用 {len(report['calls'])}，失败 {len(report['failures'])}，"
          f"aborted={aborted}，service 行 {total_before} -> {report['service_rows_after']}")
    print("saved ->", out_file)
    return 0 if report["all_pass"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
