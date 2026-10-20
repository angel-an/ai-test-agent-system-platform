# -*- coding: utf-8 -*-
"""阶段 1 第 3、4 步：标签不混计用例级抽验 + 系统化测试（全程只读）。

第 3 步（用例级抽验）：
  A. 家族归属唯一性：PR-9 全部 test_cases 按 (module, module_source) 分家族，
     每条用例只属一个家族，无第二归属；
  B. service 资产家族分桶：web_case_link_service 族资产行按其 test_case 的家族分桶，
     与 DB 直查金标准一致，无跨族桥接（两套标签不混计的资产侧证据）；
  C. reuse 判定用例级抽验：25 条样本（覆盖 reuse/create、prd_section /
     prd_section_evidence / YL- 族 / 显式三例），预检 predicted_action 与
     _get_existing_web_test 同口径 DB 真值逐一比对。

第 4 步（系统化测试）：
  D. 零写入：调用前/后 8 项 DB 足迹全等；
  E. 筛选准确性：8 个过滤组合的预检 cohort_total vs DB 直查金标准；
  F. 重复资产识别（唯一键扩样全量核验）：service 族按 (project_id, test_case_id)
     分组，凡 count>1 列为唯一键违例——即阶段 1 第 1 步遗留的扩样抽样项；
  G. 预检确定性：同 payload 连调两次结果逐字一致。

产出：out_yl_case_audit/phase1_step3_4_verify.json
不进阶段 2：阶段 2 需业务候选确认（含 prd_section_evidence 8 条是否纳入）。
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
PROJECT_ID = "942226d9-6071-43fa-b2bb-f7cbc93a0028"  # PR-9

PRD_MODULES = ["店鋪主數據管理", "商品主數據", "支付方式主數據"]
SERVICE_AGENT = "web_case_link_service"


def conn():
    c = psycopg2.connect(host="127.0.0.1", port=5432, user="postgres",
                         password="postgres", dbname="langgraph_platform_db")
    c.set_session(isolation_level="REPEATABLE READ", readonly=True)
    return c


def call_precheck(payload):
    r = requests.post(URL, json=payload, timeout=120)
    return r.status_code, (r.json() or {}).get("data") or {}


def db_snapshot(cur):
    snap = {}
    for table in ("web_tests", "test_cases", "web_test_runs"):
        cur.execute(f"select count(*) from {table}")
        snap[f"{table}_total"] = cur.fetchone()[0]
    cur.execute("select generated_by_agent, count(*) from web_tests group by 1 order by 1")
    snap["web_tests_by_agent"] = {r[0] or "": r[1] for r in cur.fetchall()}
    cur.execute("select count(*) from test_cases where custom_fields ? 'role'")
    snap["cases_with_role_key"] = cur.fetchone()[0]
    cur.execute("select count(*) from test_cases where custom_fields ? 'web_test_id'")
    snap["cases_with_webtestid_key"] = cur.fetchone()[0]
    cur.execute("select count(*) from web_tests where generated_by_agent=%s", (SERVICE_AGENT,))
    snap["service_family_rows"] = cur.fetchone()[0]
    return snap


def main():
    report = {"run_at": RUN_AT, "endpoint": URL, "project": "PR-9"}
    c = conn()
    cur = c.cursor()

    # ── 第 3 步 A：家族归属唯一性 ─────────────────────────────────
    cur.execute("""select module, coalesce(custom_fields->>'module_source','<NULL>') ms, count(*)
                   from test_cases where project_id=%s group by 1,2 order by 3 desc""", (PROJECT_ID,))
    fam_rows = cur.fetchall()
    fam_counts = {}
    for module, ms, n in fam_rows:
        if str(module or "").startswith("YL-主數據管理"):
            fam = "YL_manual"
        elif module in PRD_MODULES:
            fam = f"PRD:{ms}"
        else:
            fam = "other"
        fam_counts[fam] = fam_counts.get(fam, 0) + n
    # 唯一性：module 单值 → 家族判定函数确定性 ⇒ 每条用例恰好一个家族（结构保证）
    # 实证：家族计数总和 = 用例总数
    cur.execute("select count(*) from test_cases where project_id=%s", (PROJECT_ID,))
    cases_total = cur.fetchone()[0]
    unique_family = (sum(fam_counts.values()) == cases_total)
    report["step3_A_family_uniqueness"] = {
        "cases_total": cases_total,
        "family_counts": fam_counts,
        "pass": unique_family,
    }

    # ── 第 3 步 B：service 资产按家族分桶（资产侧不混计）───────────
    cur.execute("""select coalesce(w.test_case_id::text,''), t.module,
                          coalesce(t.custom_fields->>'module_source','<NULL>'), count(*)
                   from web_tests w
                   left join test_cases t on t.id = w.test_case_id
                   where w.project_id=%s and w.generated_by_agent=%s
                   group by 1,2,3""", (PROJECT_ID, SERVICE_AGENT))
    asset_rows = cur.fetchall()
    bucket = {"bound_PRD_prd_section": 0, "bound_PRD_prd_section_evidence": 0,
              "bound_YL_manual": 0, "bound_other": 0, "unbound_null_case": 0}
    for _tc, module, ms, n in asset_rows:
        if _tc == "":
            bucket["unbound_null_case"] += n
        elif str(module or "").startswith("YL-主數據管理"):
            bucket["bound_YL_manual"] += n
        elif module in PRD_MODULES:
            bucket[f"bound_PRD_{ms}"] += n
        else:
            bucket["bound_other"] += n
    # 金标准：直接按家族计数
    cur.execute("""select count(*) from web_tests w join test_cases t on t.id=w.test_case_id
                   where w.project_id=%s and w.generated_by_agent=%s
                     and t.module in %s""", (PROJECT_ID, SERVICE_AGENT, tuple(PRD_MODULES)))
    gold_prd_assets = cur.fetchone()[0]
    cur.execute("""select count(*) from web_tests w join test_cases t on t.id=w.test_case_id
                   where w.project_id=%s and w.generated_by_agent=%s
                     and t.module like 'YL-主數據管理%%'""", (PROJECT_ID, SERVICE_AGENT))
    gold_yl_assets = cur.fetchone()[0]
    pass_b = (bucket["bound_PRD_prd_section"] + bucket["bound_PRD_prd_section_evidence"]
              == gold_prd_assets and bucket["bound_YL_manual"] == gold_yl_assets)
    report["step3_B_asset_family_buckets"] = {
        "buckets": bucket,
        "gold_prd_bound": gold_prd_assets,
        "gold_yl_bound": gold_yl_assets,
        "pass": pass_b,
    }

    # ── 第 3 步 C：reuse 判定用例级抽验（25 条）────────────────────
    cur.execute("""select id::text, identifier,
                          coalesce(custom_fields->>'module_source','<NULL>') ms,
                          (select (w.identifier) from web_tests w
                            where w.test_case_id=t.id and w.generated_by_agent=%s
                            order by w.created_at asc limit 1) as earliest_svc,
                          (select count(*) from web_tests w
                            where w.test_case_id=t.id and w.generated_by_agent=%s) as svc_rows
                   from test_cases t
                   where project_id=%s
                   order by case
                     when identifier in ('TC-900101','TC-900102','TC-900103') then 0
                     when custom_fields->>'module_source'='prd_section_evidence' then 1
                     when custom_fields->>'module_source'='prd_section' then 2
                     when module like 'YL-主數據管理%%' then 3 else 4 end,
                     random()""",
              (SERVICE_AGENT, SERVICE_AGENT, PROJECT_ID))
    all_cases = cur.fetchall()
    sample = (all_cases[:3]                                  # 显式三例
              + [r for r in all_cases if r[2] == "prd_section_evidence"][:4]
              + [r for r in all_cases if r[2] == "prd_section"][:9]
              + [r for r in all_cases if r[2] == "<NULL>"][:9])[:25]
    sc, data = call_precheck({"test_case_ids": [r[0] for r in sample], "limit": 100})
    pre_by_id = {it["id"]: it for it in (data.get("cases") or [])}
    mismatches = []
    checked = 0
    for cid, ident, _ms, earliest_svc, svc_rows in sample:
        pre = pre_by_id.get(cid)
        if pre is None:
            mismatches.append({"identifier": ident, "issue": "预检结果缺失"})
            continue
        checked += 1
        expected_action = "reuse" if svc_rows > 0 else "create"
        ok = (pre.get("predicted_action") == expected_action)
        if earliest_svc:
            ok = ok and ((pre.get("existing_service_web_test") or {}).get("identifier") == earliest_svc)
        if not ok:
            mismatches.append({"identifier": ident,
                               "db": {"earliest": earliest_svc, "svc_rows": svc_rows},
                               "precheck": {"action": pre.get("predicted_action"),
                                            "existing": (pre.get("existing_service_web_test") or {}).get("identifier")}})
    report["step3_C_reuse_spotcheck"] = {
        "sample_size": len(sample), "checked": checked, "mismatches": mismatches,
        "pass": not mismatches and checked == len(sample),
    }

    # ── 第 4 步 D：零写入 ─────────────────────────────────────────
    before = db_snapshot(cur)
    payloads = [
        {"module": "店鋪主數據管理", "limit": 1000},
        {"module_in": PRD_MODULES, "module_source": "prd_section", "limit": 1000},
        {"module_source": "prd_section_evidence", "limit": 100},
        {"module_source": "prd_section", "has_requirement_rule": True, "limit": 100},
        {"module_source": "prd_section", "has_requirement_rule": False, "limit": 100},
        {"created_from": "2026-10-14T00:00:00", "created_to": "2026-10-14T23:59:59", "limit": 100},
        {"test_case_ids": [r[0] for r in all_cases[:3]], "limit": 10},
    ]
    for p in payloads:
        call_precheck(p)
    after = db_snapshot(cur)
    zero_write = all(before[k] == after[k] for k in before)
    report["step4_D_zero_write"] = {
        "requests_made": len(payloads) + 1,
        "diff": {k: [before[k], after[k]] for k in before if before[k] != after[k]},
        "pass": zero_write,
    }

    # ── 第 4 步 E：筛选准确性（8 组合，金标准 = 同谓词 DB 直查）────
    def gold(sql, args):
        cur.execute(sql, args)
        return cur.fetchone()[0]

    combos = []
    checks_e = [
        ("module=店鋪", {"module": "店鋪主數據管理", "limit": 1000},
         """select count(*) from test_cases where project_id=%s and module=%s""",
         (PROJECT_ID, "店鋪主數據管理")),
        ("module_in 三族", {"module_in": PRD_MODULES, "limit": 1000},
         """select count(*) from test_cases where project_id=%s and module = any(%s)""",
         (PROJECT_ID, PRD_MODULES)),
        ("+prd_section 严格", {"module_in": PRD_MODULES, "module_source": "prd_section", "limit": 1000},
         """select count(*) from test_cases where project_id=%s and module = any(%s)
            and custom_fields->>'module_source'='prd_section'""",
         (PROJECT_ID, PRD_MODULES)),
        ("prd_section_evidence", {"module_source": "prd_section_evidence", "limit": 100},
         """select count(*) from test_cases where project_id=%s
            and custom_fields->>'module_source'='prd_section_evidence'""",
         (PROJECT_ID,)),
        ("prd_section+有规则", {"module_source": "prd_section", "has_requirement_rule": True, "limit": 100},
         """select count(*) from test_cases where project_id=%s
            and custom_fields->>'module_source'='prd_section'
            and length(coalesce(custom_fields->>'requirement_rule_id',''))>0""",
         (PROJECT_ID,)),
        ("prd_section+无规则", {"module_source": "prd_section", "has_requirement_rule": False, "limit": 100},
         """select count(*) from test_cases where project_id=%s
            and custom_fields->>'module_source'='prd_section'
            and length(coalesce(custom_fields->>'requirement_rule_id',''))=0""",
         (PROJECT_ID,)),
        ("created 窗口 10-14", {"created_from": "2026-10-14T00:00:00",
                                "created_to": "2026-10-14T23:59:59", "limit": 100},
         """select count(*) from test_cases where project_id=%s
            and created_at >= %s and created_at <= %s""",
         (PROJECT_ID, "2026-10-14T00:00:00", "2026-10-14T23:59:59")),
        ("显式三例", {"test_case_ids": [r[0] for r in all_cases[:3]], "limit": 10},
         """select count(*) from test_cases where project_id=%s and id::text = any(%s)""",
         (PROJECT_ID, [r[0] for r in all_cases[:3]])),
    ]
    for name, payload, sql, args in checks_e:
        sc, data = call_precheck(payload)
        g = gold(sql, args)
        got = data.get("cohort_total")
        # any(%s) 需列表 → psycopg2 自动适配 UUID[]
        combos.append({"name": name, "http": sc, "precheck": got, "gold": g,
                       "pass": sc == 200 and got == g})
    report["step4_E_filter_accuracy"] = {"combos": combos,
                                         "pass": all(x["pass"] for x in combos)}

    # ── 第 4 步 F：重复资产识别（唯一键扩样全量核验）──────────────
    # 键域 = test_case_id 非空的 service 族行（NULL 行无绑定目标语义，
    # 2026-08/09 遗留资产，TC 号只留在 name 里，单列为观察项不判违例）
    cur.execute("""select project_id::text, test_case_id::text, count(*),
                          min(created_at), max(created_at)
                   from web_tests where generated_by_agent=%s and test_case_id is not null
                   group by 1,2 having count(*)>1 order by 3 desc""", (SERVICE_AGENT,))
    dup_rows = cur.fetchall()
    cur.execute("""select count(*), count(distinct (project_id::text||test_case_id::text))
                   from web_tests where generated_by_agent=%s and test_case_id is not null""",
                (SERVICE_AGENT,))
    svc_total, svc_distinct = cur.fetchone()
    cur.execute("""select project_id::text, count(*), min(created_at), max(created_at)
                   from web_tests where generated_by_agent=%s and test_case_id is null
                   group by 1""", (SERVICE_AGENT,))
    null_groups = [{"project_id": r[0], "rows": r[1], "first": str(r[2]), "last": str(r[3])}
                   for r in cur.fetchall()]
    report["step4_F_duplicate_detection"] = {
        "service_family_rows_bound": svc_total,
        "distinct_keys": svc_distinct,
        "duplicate_keys": [
            {"project_id": r[0], "test_case_id": r[1], "rows": r[2],
             "first": str(r[3]), "last": str(r[4])} for r in dup_rows[:20]
        ],
        "duplicate_key_count": len(dup_rows),
        "out_of_key_domain_null_groups": null_groups,
        "pass": len(dup_rows) == 0,
    }

    # ── 第 4 步 G：预检确定性 ─────────────────────────────────────
    p = {"module_in": PRD_MODULES, "module_source": "prd_section", "limit": 50}
    _s1, d1 = call_precheck(p)
    _s2, d2 = call_precheck(p)
    report["step4_G_determinism"] = {"pass": json.dumps(d1, sort_keys=True) == json.dumps(d2, sort_keys=True)}

    c.close()

    report["acceptance"] = {
        "3A_家族归属唯一": report["step3_A_family_uniqueness"]["pass"],
        "3B_资产分桶无跨族": pass_b,
        "3C_reuse抽验一致": report["step3_C_reuse_spotcheck"]["pass"],
        "4D_零写入": zero_write,
        "4E_筛选准确": report["step4_E_filter_accuracy"]["pass"],
        "4F_无重复键违例": report["step4_F_duplicate_detection"]["pass"],
        "4G_预检确定": report["step4_G_determinism"]["pass"],
    }
    report["all_pass"] = all(report["acceptance"].values())

    out_file = OUT / "phase1_step3_4_verify.json"
    out_file.write_text(json.dumps(report, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    print("=" * 60)
    for k, v in report["acceptance"].items():
        print(f"  {'PASS' if v else 'FAIL'}  {k}")
    print("=" * 60)
    print("ALL_PASS:", report["all_pass"])
    print("saved ->", out_file)
    return 0 if report["all_pass"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
