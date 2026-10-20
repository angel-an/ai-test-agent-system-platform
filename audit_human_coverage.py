# -*- coding: utf-8 -*-
"""只读盘点：人工编写用例的自动化可行性与已执行覆盖情况。

口径（与 2026-10-17 分族纠正一致）：
- 人工编写 = custom_fields.content_source in ('human_xlsx','self_defined_real_write')
- PRD 生成 = module 为 PRD 模块（商品主數據 / 支付方式主數據），或 module 被写成
  YL-主數據管理-* 但带 requirement_rule_id 且无 content_source 的产物
"""
from __future__ import annotations

import json
from collections import Counter, defaultdict
from pathlib import Path

import psycopg2

PROJ = "942226d9-6071-43fa-b2bb-f7cbc93a0028"
DSN = dict(host="127.0.0.1", port=5432, user="postgres", password="postgres",
           dbname="langgraph_platform_db")
HUMAN_SRC = ("human_xlsx", "self_defined_real_write")
ROOT = Path(__file__).resolve().parent
RESULT_FILE = ROOT / "out_yl_case_audit" / "gp_full_exec_results.jsonl"
COMPILE_FILE = ROOT / "out_yl_case_audit" / "gp_compile.jsonl"

FINAL_OK = {"PASSED", "PARTIAL", "UNVERIFIED", "FAILED_ASSERT"}


def load_latest(path: Path) -> dict[str, dict]:
    out: dict[str, dict] = {}
    if not path.exists():
        return out
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        out[row["case"]] = row  # 后写覆盖前写 = 取最新一次
    return out


def fam(module: str) -> str:
    if "店鋪" in module or "店铺" in module:
        return "店铺"
    if "商品" in module:
        return "商品"
    if "支付" in module:
        return "支付方式"
    if "通用" in module:
        return "通用"
    return module


def main() -> None:
    conn = psycopg2.connect(**DSN)
    cur = conn.cursor()

    latest_exec = load_latest(RESULT_FILE)
    compile_rows = load_latest(COMPILE_FILE)

    # ---------- A. 人工用例全集 ----------
    cur.execute("""
        select t.identifier, t.module, t.name, t.priority::text,
               coalesce(t.custom_fields->>'content_source','') src,
               coalesce(t.custom_fields->>'duplicate_of','') dup,
               coalesce(jsonb_array_length(t.custom_fields->'assertions'),0) assertion_cnt,
               length(coalesce(t.description,'')) desc_len,
               length(coalesce(t.preconditions,'')) pre_len,
               t.test_case_type::text,
               coalesce(t.custom_fields->'requirement_rule'->>'type','') rule_type,
               t.id::text
          from test_cases t
         where t.project_id = %s
           and t.custom_fields->>'content_source' = any(%s)
         order by t.module, t.identifier
    """, (PROJ, list(HUMAN_SRC)))
    human = cur.fetchall()
    print("=" * 96)
    print("A. 人工编写用例全集")
    print("=" * 96)
    by_mod = Counter()
    by_src = Counter()
    dup_n = 0
    for r in human:
        by_mod[r[1]] += 1
        by_src[r[4]] += 1
        if r[5]:
            dup_n += 1
    for m, n in by_mod.items():
        print(f"  {m:<28} {n:>4}")
    print(f"  合计 {len(human)}   来源 {dict(by_src)}   已标重复 {dup_n}   去重后真实 {len(human) - dup_n}")

    case_ids = [r[0] for r in human]

    # ---------- B. 脚本资产 ----------
    cur.execute("""
        select t.identifier, w.id::text, w.identifier, w.generated_by_agent,
               coalesce((w.generation_params->'compiled_cases'->>'assertion_count')::int, 0),
               w.updated_at
          from web_tests w join test_cases t on t.id = w.test_case_id
         where w.project_id = %s and t.identifier = any(%s)
    """, (PROJ, case_ids))
    asset_rows = cur.fetchall()
    assets: dict[str, list] = defaultdict(list)
    for r in asset_rows:
        assets[r[0]].append(r)
    print()
    print("=" * 96)
    print("B. 脚本资产覆盖（人工用例 → 是否有可执行脚本）")
    print("=" * 96)
    have = [r for r in human if assets.get(r[0])]
    miss = [r for r in human if not assets.get(r[0])]
    print(f"  有人工用例 {len(human)} 条：有脚本资产 {len(have)} 条 / 无脚本 {len(miss)} 条")
    print(f"  无脚本中已标重复 {sum(1 for r in miss if r[5])} 条 → 真实无脚本 {sum(1 for r in miss if not r[5])} 条")
    gen = Counter()
    for r in have:
        for a in assets[r[0]]:
            gen[a[3]] += 1
    print(f"  资产生成者分布：{dict(gen)}")
    multi = [c for c, v in assets.items() if len(v) > 1]
    print(f"  一用例多资产：{len(multi)} 条")

    # ---------- C. 有脚本的人工用例：执行结论 ----------
    print()
    print("=" * 96)
    print("C. 有脚本的人工用例：执行结论（取最新一次）")
    print("=" * 96)
    verdict_by_mod: dict[str, Counter] = defaultdict(Counter)
    never = []
    detail = []
    for r in have:
        cid = r[0]
        ex = latest_exec.get(cid)
        v = ex["verdict"] if ex else "NOT_RUN"
        verdict_by_mod[fam(r[1])][v] += 1
        if not ex:
            never.append(r)
        detail.append((fam(r[1]), cid, r[2][:34], r[3], v,
                       (ex or {}).get("pass"), (ex or {}).get("plan_checks"),
                       ((ex or {}).get("reason") or "")[:52]))
    cols = ["PASSED", "PARTIAL", "UNVERIFIED", "FAILED_ASSERT", "BLOCKED_EXEC",
            "NOT_EXECUTED", "NOT_RUN", "ERROR", "TIMEOUT", "DIRTY_ABORT"]
    hdr = f"{'模块':<8}{'有脚本':>7}" + "".join(f"{c[:12]:>14}" for c in cols)
    print(hdr)
    tot = Counter()
    for f in ("店铺", "商品", "支付方式", "通用"):
        c = verdict_by_mod.get(f)
        if not c:
            continue
        print(f"{f:<8}{sum(c.values()):>7}" + "".join(f"{c[k]:>14}" for k in cols))
        tot.update(c)
    print(f"{'合计':<8}{sum(tot.values()):>7}" + "".join(f"{tot[k]:>14}" for k in cols))
    done = sum(tot[k] for k in FINAL_OK)
    print(f"\n  跑出最终结论（PASSED/PARTIAL/UNVERIFIED/FAILED_ASSERT）：{done}/{len(have)} = {done/len(have):.1%}")
    print(f"  未跑出结论（BLOCKED_EXEC/NOT_EXECUTED/NOT_RUN/ERROR/TIMEOUT）："
          f"{len(have)-done} 条")
    if never:
        print(f"  从未执行过任何一次：{len(never)} 条 → {', '.join(r[0] for r in never)}")

    print("\n  明细（有脚本的人工用例）：")
    for f, cid, name, prio, v, pas, plan, reason in detail:
        print(f"    {f} {cid:<10}{prio:<9}{v:<15}pass={pas}/{plan}  {name}  {reason}")

    # ---------- D. 无脚本人工用例的可执行性画像 ----------
    print()
    print("=" * 96)
    print("D. 无脚本的人工用例：能不能自动化")
    print("=" * 96)
    gap = [r for r in miss if not r[5]]
    print(f"  真实无脚本 {len(gap)} 条")
    rule_types = Counter(r[10] or "(无规则类型)" for r in gap)
    print(f"  规则类型分布：{dict(rule_types)}")
    print(f"  带 assertions 的：{sum(1 for r in gap if r[6] > 0)} 条")
    print(f"  描述 >100 字的：{sum(1 for r in gap if r[7] > 100)} 条")
    print(f"  优先级分布：{dict(Counter(r[3] for r in gap))}")
    print(f"  用例类型：{dict(Counter(r[9] for r in gap))}")
    print("\n  明细：")
    for r in gap:
        print(f"    {fam(r[1])} {r[0]:<10}{r[3]:<9}assert={r[6]:<2}desc={r[7]:<5}"
              f"{r[10] or '-':<16}{r[2][:44]}")

    # ---------- E. 编译期视角（有脚本但跑不出检查） ----------
    print()
    print("=" * 96)
    print("E. 有脚本但编译期无检查（跑也出不了断言）")
    print("=" * 96)
    no_check = []
    for r in have:
        cr = compile_rows.get(r[0])
        if cr and cr.get("status") != "EXECUTABLE":
            no_check.append((r, cr))
    print(f"  {len(no_check)} 条")
    for r, cr in no_check:
        print(f"    {fam(r[1])} {r[0]:<10}{cr.get('status'):<22}"
              f"checks={cr.get('form_executable_checks')} "
              f"pending={cr.get('pending_locator_count')} skip={cr.get('skipped_assertion_count')} "
              f"{r[2][:36]}")

    # ---------- 落盘 ----------
    out = {
        "human_total": len(human),
        "human_dup": dup_n,
        "human_real": len(human) - dup_n,
        "with_asset": len(have),
        "without_asset": len(miss),
        "gap_real": len(gap),
        "verdict_of_assets": {k: dict(v) for k, v in verdict_by_mod.items()},
        "final_conclusion": done,
        "no_check_assets": len(no_check),
        "gap_rule_types": dict(rule_types),
        "rows": [
            {"module": r[1], "case": r[0], "name": r[2], "priority": r[3],
             "dup": r[5], "assertion_cnt": r[6], "rule_type": r[10],
             "has_asset": bool(assets.get(r[0])),
             "verdict": (latest_exec.get(r[0]) or {}).get("verdict", "NOT_RUN"),
             "compile_status": (compile_rows.get(r[0]) or {}).get("status")}
            for r in human
        ],
    }
    p = ROOT / "out_yl_case_audit" / "human_coverage_20261017.json"
    p.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n[saved] {p}")
    conn.close()


if __name__ == "__main__":
    main()
