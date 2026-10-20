# -*- coding: utf-8 -*-
"""阶段 0：归集口径复核（只读，REPEATABLE READ 单快照）。
产出：out_yl_case_audit/phase0_recheck_20261017.json + 终端摘要。
原则：每个指标记录 分子/分母/时间范围/SQL 依据；字段匹配标注为静态预检结果。
"""
import json, re, hashlib, sys
from pathlib import Path
from datetime import datetime
import psycopg2
from psycopg2.extras import RealDictCursor

OUT = Path("out_yl_case_audit")
OUT.mkdir(exist_ok=True)
RUN_AT = datetime.now().isoformat(timespec="seconds")

BATCHES = [
    ("店铺", "店鋪主數據管理", "WSF-YL-STORE",  "e1c94163-d221-4ac8-afdc-c2103f989e5f"),
    ("商品", "商品主數據",     "WSF-YL-GOODS",  "d90c2034-0ac2-4f12-91c7-a0be5f2f2103"),
    ("支付", "支付方式主數據", "WSF-YL-PAY",    "af320aac-6d37-4cc9-b1d7-1213aa5eced4"),
]
RW_MAP = {"店鋪主數據管理": "WSF-YL-STORE-RW", "商品主數據": "WSF-YL-GOODS-RW",
          "支付方式主數據": "WSF-YL-PAY-RW"}

MATCH_ALGO = ("norm(s)=仅保留[a-z0-9\\u4e00-\\u9fff]并 lower；命中判据：断言 field 或 "
              "requirement_rule.field 的 norm 值与 field_locators.fields 键 norm 值互为包含"
              "（双向 substring）。静态文本匹配，不验证控件可执行性。")

def norm(s):
    return re.sub(r"[^0-9a-z\u4e00-\u9fff]", "", str(s or "").lower())

def main():
    c = psycopg2.connect(host="127.0.0.1", port=5432, user="postgres",
                         password="postgres", dbname="langgraph_platform_db")
    c.set_session(isolation_level="REPEATABLE READ", readonly=True)
    cur = c.cursor()
    out = {"run_at": RUN_AT, "db": "langgraph_platform_db@127.0.0.1:5432",
           "isolation": "REPEATABLE READ (readonly)", "match_algo": MATCH_ALGO}

    # ── A. 定位器键快照 ──────────────────────────────────────────────
    out["locators"] = {}
    for _, mod, sf_ident, _sf in BATCHES + [("店铺RW", "", "WSF-YL-STORE-RW", ""),
                                            ("商品RW", "", "WSF-YL-GOODS-RW", ""),
                                            ("支付RW", "", "WSF-YL-PAY-RW", "")]:
        cur.execute("select custom_config from web_sub_functions where identifier=%s", (sf_ident,))
        row = cur.fetchone()
        cfg = (row[0] or {}) if row else {}
        fl = cfg.get("field_locators") or {}
        fields = fl.get("fields") or {}
        keys = list(fields.keys()) if isinstance(fields, dict) else \
               [f.get("name") for f in fields if isinstance(f, dict)]
        out["locators"][sf_ident] = {"keys": keys, "n": len(keys), "n_norm": len({norm(k) for k in keys})}

    # ── B. 模块级三种数（分母全显式）───────────────────────────────
    out["modules"] = {}
    for label, mod, sf_ident, sf_id in BATCHES:
        cur.execute("""select identifier, id::text, created_at, custom_fields
                       from test_cases where module=%s order by identifier""", (mod,))
        rows = cur.fetchall()
        ids = [r[0] for r in rows]
        with_rule = with_assert = 0
        gen_counter, src_counter = {}, {}
        tmin = tmax = None
        matchable, unmatched = [], {}
        loc_keys = {norm(k) for k in out["locators"][sf_ident]["keys"] if k}
        for ident, cid, ts, cf in rows:
            cf = cf or {}
            tmin = ts if tmin is None or ts < tmin else tmin
            tmax = ts if tmax is None or ts > tmax else tmax
            rr = cf.get("requirement_rule")
            if cf.get("requirement_rule_id") or rr: with_rule += 1
            if cf.get("assertions"): with_assert += 1
            gen_counter[str(cf.get("generated_from") or "(无标签)")] = \
                gen_counter.get(str(cf.get("generated_from") or "(无标签)"), 0) + 1
            src_counter[str(cf.get("content_source") or "(无标签)")] = \
                src_counter.get(str(cf.get("content_source") or "(无标签)"), 0) + 1
            flds = []
            if isinstance(rr, dict) and rr.get("field"): flds.append(rr["field"])
            for a in (cf.get("assertions") or []):
                if isinstance(a, dict) and a.get("field"): flds.append(a["field"])
            hit = False
            for f in flds:
                nf = norm(f)
                if not nf: continue
                if any(nf in k or k in nf for k in loc_keys): hit = True
                else: unmatched[str(f)[:40]] = unmatched.get(str(f)[:40], 0) + 1
            if hit: matchable.append(ident)
        cur.execute("""select count(*) from test_cases
                       where module=%s and custom_fields->>'sub_function_id' is not null""", (mod,))
        attached_any = cur.fetchone()[0]
        cur.execute("""select count(*) from test_cases
                       where module=%s and custom_fields->>'sub_function_id'=%s""", (mod, sf_id))
        attached_target = cur.fetchone()[0]
        cur.execute("""select coalesce(s.identifier,'?') sf, count(*) from test_cases tc
                       left join web_sub_functions s on s.id::text = tc.custom_fields->>'sub_function_id'
                       where tc.module=%s and tc.custom_fields->>'sub_function_id' is not null
                       group by 1 order by 2 desc""", (mod,))
        attached_breakdown = {r[0]: r[1] for r in cur.fetchall()}
        out["modules"][mod] = {
            "label": label, "target_sub_function": sf_ident,
            "total_cases": len(rows),                       # 分母1
            "with_requirement_rule": with_rule,
            "with_assertions": with_assert,
            "static_match_to_target_sf": len(matchable),     # 静态预检，非承诺
            "static_match_sample": matchable[:8],
            "static_unmatched_fields_top": dict(sorted(unmatched.items(), key=lambda x: -x[1])[:10]),
            "attached_any_sub_function": attached_any,       # 归集口径
            "attached_target_sub_function": attached_target,
            "attached_breakdown": attached_breakdown,
            "generated_from_dist": gen_counter,
            "content_source_dist": src_counter,
            "created_range": [str(tmin), str(tmax)] if rows else None,
            "case_id_set_sha1": hashlib.sha1("|".join(sorted(ids)).encode()).hexdigest(),
            "case_ids_head": ids[:5], "case_ids_tail": ids[-5:],
        }
        m = out["modules"][mod]
        m["coverage_all_case"] = (round(attached_any / len(rows) * 100, 1) if rows else None)      # 118/472
        m["coverage_matchable"] = (round(attached_target / len(matchable) * 100, 1) if matchable else None)  # 118/268

    # ── C. 「商品/支付 PRD 批 = 0 归集」闭环复核（ID 集合交集）───────
    out["zero_link_evidence"] = {}
    for label, mod, sf_ident, _sf in BATCHES[1:]:
        cur.execute("""select tc.id::text, tc.identifier from test_cases tc
                       where tc.module=%s and tc.custom_fields->>'requirement_rule_id' is not null
                         and tc.custom_fields->>'sub_function_id' is null""", (mod,))
        prd_unattached = cur.fetchall()
        prd_ids = {r[0] for r in prd_unattached}
        cur.execute("""select distinct wt.test_case_id::text from web_tests wt
                       join web_sub_functions s on s.id = wt.sub_function_id
                       where s.identifier in (%s, %s)""",
                    (sf_ident, RW_MAP[mod]))
        linked_ids = {r[0] for r in cur.fetchall()}
        inter = prd_ids & linked_ids
        out["zero_link_evidence"][mod] = {
            "prd_direct_unattached_n": len(prd_ids),
            "linked_to_goods_or_pay_rw_case_ids_n": len(linked_ids),
            "intersection_n": len(inter),
            "note": "交集为 0 ⇒ PRD 直生成且未归集的用例，无一拥有 WebTest 资产",
        }

    # ── D. WSF-YL-* web_tests 台账（含一对多复检）───────────────────
    cur.execute("""select s.identifier, count(*), count(distinct wt.test_case_id)
                   from web_tests wt join web_sub_functions s on s.id=wt.sub_function_id
                   where s.identifier like 'WSF-YL-%%' group by 1 order by 1""")
    out["wsf_yl_webtests"] = {r[0]: {"rows": r[1], "distinct_cases": r[2]} for r in cur.fetchall()}
    cur.execute("""select tc.identifier, tc.custom_fields->>'generated_from', count(*)
                   from test_cases tc join web_tests wt on wt.test_case_id=tc.id
                   join web_sub_functions s on s.id=wt.sub_function_id
                   where s.identifier like 'WSF-YL-%%'
                   group by 1,2 having count(*)>1 order by 3 desc""")
    out["webtest_multiplicity_over1"] = cur.fetchall()

    # ── E. role=batch 全库现状 + 修复证据 ───────────────────────────
    cur.execute("""select custom_fields->'web_coverage'->>'role' r,
                          substr(custom_fields->'web_coverage'->>'bound_at',1,10) d, count(*)
                   from test_cases where custom_fields->'web_coverage' is not null
                   group by 1,2 order by 1,2""")
    out["webcoverage_roles"] = [list(r) for r in cur.fetchall()]
    cur.execute("""select count(*) from test_cases
                   where custom_fields->'web_coverage'->>'role'='batch'
                     and custom_fields->'web_coverage'->>'bound_at' like '2026-10-16%%'""")
    out["batch_residual_1016"] = cur.fetchone()[0]
    ev = {}
    for p in sorted(OUT.glob("yl_webcoverage_before_fix_*.json")):
        ev[p.name] = hashlib.sha256(p.read_bytes()).hexdigest()[:16]
    fx = Path("fix_yl_webcoverage_role.py")
    if fx.exists():
        ev["fix_yl_webcoverage_role.py"] = hashlib.sha1(fx.read_bytes()).hexdigest()[:16]
    out["fix_evidence_files"] = ev

    # ── F. 复用/幂等素材（当前 multiplicity 事实，不判对错）─────────
    cur.execute("""select wt.identifier, tc.identifier, s.identifier, wt.created_at,
                          wt.generation_params->'compiled_cases'->>'assertion_count'
                   from web_tests wt join test_cases tc on tc.id=wt.test_case_id
                   join web_sub_functions s on s.id=wt.sub_function_id
                   where tc.identifier='TC-122613' order by wt.created_at""")
    out["idempotency_sample_TC122613"] = [
        {"web_test": r[0], "sf": r[2], "created_at": str(r[3]), "assertion_count": r[4]}
        for r in cur.fetchall()]

    c.rollback(); c.close()

    p = OUT / "phase0_recheck_20261017.json"
    p.write_text(json.dumps(out, ensure_ascii=False, indent=1, default=str), encoding="utf-8")

    print(f"[PHASE0] run_at={RUN_AT}  json={p}")
    for mod, m in out["modules"].items():
        print(f"\n== {m['label']} / {mod} / 目标 {m['target_sub_function']}")
        print(f"   全部生成用例      = {m['total_cases']}   (分母1: module='{mod}')")
        print(f"   有 requirement_rule = {m['with_requirement_rule']}   有 assertions = {m['with_assertions']}")
        print(f"   字段可匹配(静态预检) = {m['static_match_to_target_sf']}   (分母2: 断言 field 命中定位器键)")
        print(f"   已归集=任一子功能   = {m['attached_any_sub_function']}  → 覆盖率(全部用例口径) {m['coverage_all_case']}%")
        print(f"   已归集=目标子功能   = {m['attached_target_sub_function']}  → 覆盖率(可挂口径)   {m['coverage_matchable']}%")
        print(f"   归集明细: {m['attached_breakdown']}")
        print(f"   generated_from: {m['generated_from_dist']}")
        print(f"   未命中字段Top: {list(m['static_unmatched_fields_top'].items())[:5]}")
    print("\n== 零归集闭环复核:")
    for mod, e in out["zero_link_evidence"].items():
        print(f"   {mod}: PRD未归集 {e['prd_direct_unattached_n']} ∩ 有资产 {e['linked_to_goods_or_pay_rw_case_ids_n']} = {e['intersection_n']}")
    print("\n== WSF-YL-* web_tests:", json.dumps(out["wsf_yl_webtests"], ensure_ascii=False))
    print("   一对多(>1):", out["webtest_multiplicity_over1"])
    print("\n== role=batch 残留(2026-10-16):", out["batch_residual_1016"])
    print("   修复证据文件:", list(out["fix_evidence_files"]))
    return 0

if __name__ == "__main__":
    sys.exit(main())
