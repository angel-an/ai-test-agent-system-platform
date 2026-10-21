# -*- coding: utf-8 -*-
"""商品 / 支付两模块测试完成度汇总：编译能力 + 执行结论 + 缺口归因。"""
from __future__ import annotations

import json
from collections import Counter, defaultdict
from pathlib import Path

OUT = Path("out_yl_case_audit")
COMPILE_FILE = OUT / "gp_compile.jsonl"
RESULT_FILE = OUT / "gp_full_exec_results.jsonl"

MODULE_ORDER = ["商品主數據", "YL-主數據管理-商品", "支付方式主數據", "YL-主數據管理-支付方式"]
MODULE_LABEL = {
    "商品主數據": "商品·文档生成",
    "YL-主數據管理-商品": "商品·人工导入",
    "支付方式主數據": "支付·文档生成",
    "YL-主數據管理-支付方式": "支付·人工导入",
}


def read_jsonl(p: Path) -> list[dict]:
    if not p.exists():
        return []
    out = []
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            out.append(json.loads(line))
        except Exception:  # noqa: BLE001
            pass
    return out


def load_run_details() -> dict[str, dict]:
    """从 gp_runs/*.txt 读执行现场，用于对旧记录做二次判定（区分阻断 vs 零断言）。"""
    out: dict[str, dict] = {}
    d = OUT / "gp_runs"
    if not d.exists():
        return out
    for f in d.glob("*.txt"):
        txt = f.read_text(encoding="utf-8", errors="ignore")
        head, _, rest = txt.partition("=== error ===")
        case = ""
        job = ""
        for part in head.split():
            if part.startswith("case="):
                case = part[5:]
            elif part.startswith("job="):
                job = part[4:]
        err = rest.split("=== stdout ===")[0].strip()
        if not case:
            continue
        run_no = int("".join(ch for ch in f.stem if ch.isdigit()) or 0)
        prev = out.get(case)
        if prev and prev.get("run_no", 0) > run_no:
            continue
        out[case] = {"run": f.stem, "job": job, "error": err, "run_no": run_no}
    return out


def reclassify(row: dict, detail: dict | None) -> None:
    """按执行现场纠正结论：执行侧阻断不得算成『零断言/通过』。"""
    if not detail:
        return
    job = detail.get("job") or ""
    err = detail.get("error") or ""
    asserts = row.get("assertions") or []
    if job.startswith("failed"):
        bad = [a for a in asserts if a.get("outcome") in ("fail", "blocked")]
        if bad:
            row["verdict"] = "FAILED_ASSERT"
            row["reason"] = f"断言异常 {len(bad)} 条"
        elif any(k in err for k in ("菜单", "定位器", "无法打开表单", "超时", "timeout")):
            row["verdict"] = "BLOCKED_EXEC"
            row["reason"] = f"执行侧阻断：{err[:100]}"
        else:
            row["verdict"] = "FAILED_EXEC"
            row["reason"] = f"执行失败：{err[:100]}"
        row["run"] = detail.get("run")


def verdict_from_asserts(item: dict) -> str | None:
    """以断言结果重算结论，避免历史记录里的旧分类残留。"""
    asserts = item.get("assertions") or []
    if not asserts:
        return None
    pas = sum(1 for a in asserts if a.get("outcome") == "pass")
    unv = sum(1 for a in asserts if a.get("outcome") == "unverified")
    bad = sum(1 for a in asserts if a.get("outcome") in ("fail", "blocked"))
    if bad:
        return "FAILED_ASSERT"
    if pas and unv:
        return "PARTIAL"
    if pas:
        return "PASSED"
    if unv:
        return "UNVERIFIED"
    return None


def main() -> None:
    details = load_run_details()
    compile_rows = {r["case"]: r for r in read_jsonl(COMPILE_FILE)}
    # 同一用例多次执行时取最后一次终态（TIMEOUT/ERROR 可被后续覆盖）
    exec_rows: dict[str, dict] = {}
    for r in read_jsonl(RESULT_FILE):
        prev = exec_rows.get(r["case"])
        if prev is None or prev.get("verdict") in ("ERROR", "TIMEOUT") or r.get("verdict") not in ("ERROR", "TIMEOUT"):
            exec_rows[r["case"]] = r

    cases = sorted(set(compile_rows) | set(exec_rows))
    merged = []
    for c in cases:
        cr = compile_rows.get(c, {})
        er = exec_rows.get(c, {})
        module = cr.get("module") or er.get("module")
        item = {
            "module": module,
            "case": c,
            "asset": cr.get("asset") or er.get("asset"),
            "priority": cr.get("priority") or er.get("priority"),
            "compile_status": cr.get("status") or "NOT_COMPILED_THIS_ROUND",
            "form_executable_checks": cr.get("form_executable_checks"),
            "locator_depended_assertion_count": cr.get("locator_depended_assertion_count"),
            "skipped_assertion_count": cr.get("skipped_assertion_count"),
            "verdict": er.get("verdict") or ("SKIPPED_NO_CHECK" if cr.get("status") and cr["status"] != "EXECUTABLE" else "NOT_RUN"),
            "reason": er.get("reason"),
            "plan_checks": er.get("plan_checks"),
            "pass": er.get("pass"),
            "unverified": er.get("unverified"),
            "blocked": er.get("blocked"),
            "attributed_checks": er.get("attributed_checks"),
            "config_only_checks": er.get("config_only_checks"),
            "run": er.get("run"),
            "job_status": er.get("job_status"),
            "assertions": er.get("assertions") or [],
            "web_path_match_status": cr.get("web_path_match_status"),
            "warnings": cr.get("warnings"),
        }
        reclassify(item, details.get(c))
        v2 = verdict_from_asserts(item)
        if v2 and item["verdict"] not in ("BLOCKED_EXEC", "SKIPPED_NO_CHECK", "NOT_RUN"):
            item["verdict"] = v2
        merged.append(item)

    print("=" * 100)
    print("一、总体：脚本资产执行结论")
    print("=" * 100)
    by_mod = defaultdict(Counter)
    for m in merged:
        by_mod[m["module"]][m["verdict"]] += 1
    cols = ["PASSED", "PARTIAL", "UNVERIFIED", "FAILED_ASSERT", "BLOCKED_EXEC",
            "UNFINISHED", "NOT_EXECUTED", "SKIPPED_NO_CHECK"]
    print(f"{'模块':<16}{'总数':>5}" + "".join(f"{c:>17}" for c in cols))
    total = Counter()
    for mod in MODULE_ORDER:
        c = by_mod.get(mod)
        if not c:
            continue
        c["UNFINISHED"] = sum(c[k] for k in ("ERROR", "TIMEOUT", "FAILED_EXEC", "NOT_RUN"))
        print(f"{MODULE_LABEL[mod]:<16}{sum(v for k, v in c.items() if k != 'UNFINISHED'):>5}"
              + "".join(f"{c[k]:>17}" for k in cols))
        total.update(c)
    total["UNFINISHED"] = sum(total[k] for k in ("ERROR", "TIMEOUT", "FAILED_EXEC", "NOT_RUN"))
    print(f"{'合计':<16}{sum(v for k, v in total.items() if k != 'UNFINISHED'):>5}"
          + "".join(f"{total[k]:>17}" for k in cols))

    print()
    print("=" * 100)
    print("二、断言层：检查项（plan）与断言行（assertion）分开看")
    print("=" * 100)
    executed = [m for m in merged if m["verdict"] in ("PASSED", "PARTIAL", "UNVERIFIED", "FAILED_ASSERT")]
    plan = sum(m["plan_checks"] or 0 for m in executed)
    pas = sum(m["pass"] or 0 for m in executed)
    unv = sum(m["unverified"] or 0 for m in executed)
    blk = sum(m["blocked"] or 0 for m in executed)
    all_asserts = [a for m in executed for a in (m.get("assertions") or [])]
    attr_lines = sum(1 for a in all_asserts if a.get("case_id") or a.get("rule_id"))
    conf_lines = len(all_asserts) - attr_lines
    print(f"拿到检查结果的用例 {len(executed)} 条")
    print(f"检查项（执行期 plan）：{plan} 个 —— 通过 {pas}、无法UI判定 {unv}、阻断 {blk}")
    if plan:
        print(f"检查项通过率 {pas}/{plan} = {pas/plan:.1%}")
    print(f"断言行：{len(all_asserts)} 条 —— 归因断言（带 case_id/rule_id）{attr_lines} 条，"
          f"配置级检查（无血缘）{conf_lines} 条")
    if all_asserts:
        print(f"归因断言占断言行 {attr_lines}/{len(all_asserts)} = {attr_lines/len(all_asserts):.1%}")

    print()
    print("=" * 92)
    print("三、未产生执行结论的用例（编译期已判定不可自动执行）")
    print("=" * 92)
    reasons = defaultdict(list)
    for m in merged:
        if m["verdict"] == "SKIPPED_NO_CHECK":
            key = (m["module"], m["compile_status"])
            reasons[key].append(m["case"])
    for (mod, st), cs in sorted(reasons.items()):
        print(f"  {MODULE_LABEL[mod]:<14} {st:<24} {len(cs):>3} 条")

    print()
    print("=" * 92)
    print("四、失败 / 部分通过 清单")
    print("=" * 92)
    for m in merged:
        if m["verdict"] in ("FAILED_ASSERT", "PARTIAL", "FAILED_EXEC", "BLOCKED_EXEC", "TIMEOUT", "ERROR"):
            print(f"  [{m['verdict']}] {MODULE_LABEL[m['module']]} {m['case']} {m['asset']} "
                  f"({m['pass']}/{m['plan_checks']}) {m['reason']}")
            for a in m["assertions"]:
                if a.get("outcome") in ("fail", "blocked", "unverified"):
                    print(f"        - {a.get('name')}: {a.get('observed')}")

    print()
    print("=" * 92)
    print("五、零断言用例明细（需补规则/补定位器/人工执行）")
    print("=" * 92)
    for m in merged:
        if m["verdict"] == "SKIPPED_NO_CHECK":
            print(f"  {MODULE_LABEL[m['module']]:<14} {m['case']:<14} {m['compile_status']:<24} "
                  f"pending={m['locator_depended_assertion_count']} skipped={m['skipped_assertion_count']} "
                  f"warn={(m['warnings'] or [''])[0][:60]}")

    out = OUT / "gp_summary.json"
    out.write_text(json.dumps({
        "generated_at": __import__("time").strftime("%Y-%m-%d %H:%M:%S"),
        "totals": dict(total),
        "assertion_layer": {"executed_cases": len(executed), "plan_checks": plan, "pass": pas,
                            "unverified": unv, "blocked": blk,
                            "attributed": attr_lines, "config_only": conf_lines},
        "cases": merged,
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n[json] {out}")


if __name__ == "__main__":
    main()
