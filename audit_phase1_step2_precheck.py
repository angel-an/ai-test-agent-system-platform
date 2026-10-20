# -*- coding: utf-8 -*-
"""阶段 1 第 2 步验收：只读预检接口。

验收项（用户批复的固定顺序第 2 步）：
  A. 零写入：调用前/后 DB 写入足迹快照完全一致
     （web_tests 总行数、按 generated_by_agent 分组、test_cases 总数、
       custom_fields 含 role/web_test_id 的行数、web_test_runs 行数）；
  B. 筛选准确：双口径对照——
     module-only 口径 472/61/44（对齐阶段 0 快照）；
     prd_section 严格口径 470/57/42（8 条 prd_section_evidence 单列）；
     店铺 predicted_reuse=72 / predicted_create=400；
  C. service 族行数守恒：generated_by_agent='web_case_link_service' 行数不变；
  D. 两套标签不混计：混合 cohort 下 label_families 分组中
     PRD 中文族（店鋪主數據管理/商品主數據/支付方式主數據）与
     人工导入族（YL-主數據管理-*）各自独立成组，无交叉。

接口：POST /api/v2/projects/PR-9/web-case-links/minimal-loop/precheck
产出：out_yl_case_audit/phase1_step2_precheck_acceptance.json
"""
import json
from datetime import datetime
from pathlib import Path

import psycopg2
import requests

BASE = "http://127.0.0.1:8000"
URL = f"{BASE}/api/v2/projects/PR-9/web-case-links/minimal-loop/precheck"
OUT = Path("out_yl_case_audit")
OUT.mkdir(exist_ok=True)
RUN_AT = datetime.now().isoformat(timespec="seconds")

PRD_MODULES = ["店鋪主數據管理", "商品主數據", "支付方式主數據"]


def db_snapshot():
    """写入足迹快照（只读连接）。"""
    c = psycopg2.connect(host="127.0.0.1", port=5432, user="postgres",
                         password="postgres", dbname="langgraph_platform_db")
    c.set_session(isolation_level="REPEATABLE READ", readonly=True)
    cur = c.cursor()
    snap = {}
    cur.execute("select count(*) from web_tests")
    snap["web_tests_total"] = cur.fetchone()[0]
    cur.execute("select generated_by_agent, count(*) from web_tests group by 1 order by 1")
    snap["web_tests_by_agent"] = {r[0] or "": r[1] for r in cur.fetchall()}
    cur.execute("select count(*) from test_cases")
    snap["test_cases_total"] = cur.fetchone()[0]
    cur.execute("select count(*) from web_test_runs")
    snap["web_test_runs_total"] = cur.fetchone()[0]
    cur.execute("select count(*) from test_cases where custom_fields ? 'role'")
    snap["cases_with_role_key"] = cur.fetchone()[0]
    cur.execute("select count(*) from test_cases where custom_fields ? 'web_test_id'")
    snap["cases_with_webtestid_key"] = cur.fetchone()[0]
    cur.execute("select count(*) from web_tests where generated_by_agent='web_case_link_service'")
    snap["service_family_rows"] = cur.fetchone()[0]
    c.close()
    return snap


def call_precheck(payload):
    r = requests.post(URL, json=payload, timeout=120)
    body = r.json()
    return r.status_code, body


def main():
    report = {"run_at": RUN_AT, "endpoint": URL, "project": "PR-9"}

    # ── 0. 前置快照 ─────────────────────────────────────────────
    before = db_snapshot()
    report["db_snapshot_before"] = before

    # ── A1. PRD 三族 cohort（module_source 稳定字段筛选）─────────
    checks = {}
    sc, body = call_precheck({
        "module_in": PRD_MODULES,
        "module_source": "prd_section",
        "limit": 1000,
    })
    data = body.get("data") or {}
    checks["A_prd_cohort"] = {
        "http": sc,
        "cohort_total": data.get("cohort_total"),
        "summary": data.get("summary"),
        "label_families": data.get("label_families"),
    }

    # 每族单独查，与阶段 0 逐族对比。
    # 口径修正（2026-10-17 验收实测）：阶段 0 的 472/61/44 是「只按 module 列」
    # 统计；DB 实测三族各有 module_source='prd_section_evidence' 的 2/4/2 条
    # （2026-10-14 证据补挂通道，共 8 条）。故双口径对照：
    #   module-only 口径 = 472/61/44（对齐阶段 0）
    #   prd_section 严格口径 = 470/57/42
    per_module = {}
    for mod in PRD_MODULES:
        sc, body = call_precheck({"module": mod, "limit": 1000})
        d = body.get("data") or {}
        sc2, body2 = call_precheck({
            "module": mod, "module_source": "prd_section", "limit": 1000,
        })
        d2 = body2.get("data") or {}
        per_module[mod] = {
            "http": sc,
            "cohort_total_module_only": d.get("cohort_total"),
            "summary_module_only": d.get("summary"),
            "cohort_total_prd_section": d2.get("cohort_total"),
            "summary_prd_section": d2.get("summary"),
        }
    checks["A_per_module"] = per_module

    # ── B. 混合 cohort：不加 module_source，验证标签不混计 ────────
    sc, body = call_precheck({
        "module_in": PRD_MODULES + [
            "YL-主數據管理-店鋪", "YL-主數據管理-商品",
            "YL-主數據管理-支付方式", "YL-主數據管理-通用",
        ],
        "limit": 5,
    })
    d = body.get("data") or {}
    families = d.get("label_families") or []
    prd_group = {f["module"] for f in families
                 if f["module"] in PRD_MODULES}
    yl_group = {f["module"] for f in families
                if str(f["module"] or "").startswith("YL-主數據管理")}
    overlap = prd_group & yl_group
    checks["B_mixed_label_families"] = {
        "http": sc,
        "n_family_groups": len(families),
        "prd_group": sorted(prd_group),
        "yl_group": sorted(yl_group),
        "cross_family_overlap": sorted(overlap),
        "families": families,
    }

    # ── C. 显式用例集合交集过滤（TC-900101/102/103）───────────────
    c = psycopg2.connect(host="127.0.0.1", port=5432, user="postgres",
                         password="postgres", dbname="langgraph_platform_db")
    cur = c.cursor()
    cur.execute("""select id::text, identifier from test_cases
                   where identifier in ('TC-900101','TC-900102','TC-900103')""")
    trio = {r[1]: r[0] for r in cur.fetchall()}
    c.close()
    sc, body = call_precheck({
        "test_case_ids": list(trio.values()),
        "limit": 10,
    })
    d = body.get("data") or {}
    checks["C_explicit_ids"] = {
        "http": sc,
        "cohort_total": d.get("cohort_total"),
        "cases": [
            {k: it.get(k) for k in ("identifier", "module", "module_source",
                                    "predicted_action", "existing_service_web_test",
                                    "web_test_rows_by_agent")}
            for it in (d.get("cases") or [])
        ],
    }

    # ── 1. 后置快照 + 零写入判定 ─────────────────────────────────
    after = db_snapshot()
    report["db_snapshot_after"] = after
    report["zero_write_diff"] = {
        k: {"before": before[k], "after": after[k], "equal": before[k] == after[k]}
        for k in before if k in after
    }
    zero_write = all(v["equal"] for v in report["zero_write_diff"].values())

    # ── 2. 与阶段 0 基准对比（双口径）─────────────────────────────
    expected = {"店鋪主數據管理": (472, 470, 72, 400),
                "商品主數據": (61, 57, 0, 61),
                "支付方式主數據": (44, 42, 0, 44)}
    compare = {}
    for mod, (exp_total, exp_strict, exp_reuse, exp_create) in expected.items():
        got = per_module.get(mod, {})
        s = got.get("summary_module_only") or {}
        compare[mod] = {
            "expected": {"total_module_only": exp_total,
                         "total_prd_section": exp_strict,
                         "reuse": exp_reuse, "create": exp_create},
            "actual": {"total_module_only": got.get("cohort_total_module_only"),
                       "total_prd_section": got.get("cohort_total_prd_section"),
                       "reuse": s.get("predicted_reuse"),
                       "create": s.get("predicted_create")},
            "pass": (got.get("cohort_total_module_only") == exp_total
                     and got.get("cohort_total_prd_section") == exp_strict
                     and s.get("predicted_reuse") == exp_reuse
                     and s.get("predicted_create") == exp_create),
        }
    report["baseline_compare"] = compare

    a_pass = (checks["A_prd_cohort"]["cohort_total"] == 569)
    b_pass = (not overlap) and len(yl_group) > 0 and len(prd_group) > 0
    report["acceptance"] = {
        "A_筛选准确_严格口径569": a_pass,
        "A_逐族与阶段0一致": all(v["pass"] for v in compare.values()),
        "B_两套标签不混计": b_pass,
        "C_显式ID交集可用": checks["C_explicit_ids"]["http"] == 200
                            and checks["C_explicit_ids"]["cohort_total"] == 3,
        "零写入": zero_write,
        "service族行数守恒": before["service_family_rows"] == after["service_family_rows"],
    }
    report["checks"] = checks
    report["all_pass"] = all(report["acceptance"].values())

    out_file = OUT / "phase1_step2_precheck_acceptance.json"
    out_file.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    print("=" * 60)
    for k, v in report["acceptance"].items():
        print(f"  {'PASS' if v else 'FAIL'}  {k}")
    print("=" * 60)
    print("ALL_PASS:", report["all_pass"])
    print("saved ->", out_file)
    return 0 if report["all_pass"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
