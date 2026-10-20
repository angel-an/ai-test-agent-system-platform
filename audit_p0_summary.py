"""汇总 P0 抽样结果：从逐条 stdout 中提取 COMPILED_COVERAGE / COMPILED_ASSERTION_SUMMARY。

产出：out_yl_case_audit/p0_sample_summary.json + 控制台表格。
判据：executed_assertions = pass + unverified + blocked（真正跑过的断言数）。
      executed_assertions == 0 而 job=completed ⇒ 记为「零断言即通过」（不可判定）。
"""
import json
import re
from collections import Counter, defaultdict
from pathlib import Path

RUNS = Path("out_yl_case_audit/p0_runs")
OUT = Path("out_yl_case_audit/p0_sample_summary.json")


def grab(txt: str, marker: str) -> dict | None:
    for ln in txt.splitlines():
        if ln.strip().startswith(marker):
            raw = ln.strip()[len(marker):].strip()
            try:
                return json.loads(raw)
            except Exception:
                return None
    return None


results: list[dict] = []
for f in ("out_yl_case_audit/p0_sample_exec_pilot.json",
          "out_yl_case_audit/p0_sample_exec_full.json"):
    p = Path(f)
    if p.exists():
        results += json.loads(p.read_text(encoding="utf-8")).get("results", [])

rows = []
seen: set[str] = set()
for r in results:
    if r.get("case") in seen:
        continue
    seen.add(r.get("case"))
    if r.get("phase") != "done":
        rows.append({**r, "note": "未执行成功"})
        continue
    fp = RUNS / f"{r['run']}.txt"
    txt = fp.read_text(encoding="utf-8", errors="ignore") if fp.exists() else ""
    cov = grab(txt, "COMPILED_COVERAGE ") or {}
    sm = grab(txt, "COMPILED_ASSERTION_SUMMARY ") or {}
    executed = (sm.get("pass", 0) or 0) + (sm.get("unverified", 0) or 0) + (sm.get("blocked", 0) or 0)
    rows.append({
        "family": r["family"], "bucket": r["bucket"], "case": r["case"],
        "asset": r.get("asset_used") or r.get("asset"),
        "run": r.get("run"), "job_status": r.get("job_status"),
        "seconds": r.get("seconds"),
        "compiled_assertions": cov.get("assertions"),
        "form_executable_checks": cov.get("form_executable_checks"),
        "pending_locator": cov.get("pending_locator"),
        "summary_total": sm.get("total"),
        "pass": sm.get("pass"), "unverified": sm.get("unverified"),
        "blocked": sm.get("blocked"),
        "skipped_no_locator": sm.get("skipped_no_locator"),
        "attributed_checks": sm.get("attributed_checks"),
        "config_only_checks": sm.get("config_only_checks"),
        "plan_checks": sm.get("plan_checks"),
        "submit_phase": sm.get("submit_phase"),
        "executed_assertions": executed,
        "verdict": ("零断言即通过" if executed == 0 and r.get("job_status") == "completed"
                    else ("有断言执行" if executed > 0 else "失败/未判定")),
    })

OUT.write_text(json.dumps({"rows": rows}, ensure_ascii=False, indent=2), encoding="utf-8")

hdr = f"{'族':<4}{'桶':<7}{'用例':<12}{'job':<11}{'编译断言':>6}{'可执行':>6}{'待定位':>6}{'执行':>5}{'pass':>5}{'unver':>6}{'block':>6}{'结论':<12}"
print(hdr)
print("-" * len(hdr))
for x in rows:
    if "summary_total" not in x:
        print(f"{x.get('family','?'):<4}{x.get('bucket',''):<7}{x.get('case',''):<12}{'EXEC_ERR':<11}"
              f"{'-':>6}{'-':>6}{'-':>6}{'-':>5}{'-':>5}{'-':>6}{'-':>6}{x.get('note','')}")
        continue
    print(f"{x['family']:<4}{x['bucket']:<7}{x['case']:<12}{str(x['job_status']):<11}"
          f"{str(x['compiled_assertions']):>6}{str(x['form_executable_checks']):>6}"
          f"{str(x['pending_locator']):>6}{x['executed_assertions']:>5}{str(x['pass']):>5}"
          f"{str(x['unverified']):>6}{str(x['blocked']):>6}{x['verdict']:<12}")

print("\n== 按族汇总 ==")
by_fam = defaultdict(Counter)
for x in rows:
    if "summary_total" not in x:
        continue
    by_fam[x["family"]][x["verdict"]] += 1
    by_fam[x["family"]]["n"] += 1
    by_fam[x["family"]]["executed"] += x["executed_assertions"]
    by_fam[x["family"]]["checks"] += (x["summary_total"] or 0)
for fam, c in by_fam.items():
    print(f"  {fam}: n={c['n']} 有断言执行={c['有断言执行']} 零断言即通过={c['零断言即通过']} "
          f"失败/未判定={c['失败/未判定']} 执行断言合计={c['executed']} 检查合计={c['checks']}")
