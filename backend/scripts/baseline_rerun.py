#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""YL/CS 基线一键复跑（分诊 → 执行 → 汇总）。

背景（2026-10-26 实测结论，详见 docs/基线刷新-分诊与首批结果-20261026.md）：

- 全项目 184 条「有规则文本」用例里，编译期真正能提取出可判断言的只有一部分
  （首轮 20/184）。规则文本写成流程叙述时编译器提取不出断言，盲跑只会产出
  NOT_EXECUTED，稀释基线分母。
- **执行单位 = 规则组**，不是单条用例：minimal-loop 每条 run 只编译一个主用例，
  但断言归属（owners）覆盖规则文本完全相同的整组用例（TR-833：1 脚本 → 8 条同结论）。
- 断言失败的 run 终态是 **REJECTED** 不是 DONE，汇总必须数终态全集。

用法::

    # 一键全流程（分诊 → 执行可判组 → 汇总 CSV+摘要）
    .venv/Scripts/python.exe backend/scripts/baseline_rerun.py --all

    # 只分诊（0.4-1.2s/规则组，13 秒扫全项目）
    .venv/Scripts/python.exe backend/scripts/baseline_rerun.py --triage

    # 只执行已分诊的可判组（自动跳过 run log 里已跑过的组）
    .venv/Scripts/python.exe backend/scripts/baseline_rerun.py --run [--max-groups 5]

    # 只汇总（不跑任何东西）
    .venv/Scripts/python.exe backend/scripts/baseline_rerun.py --report

    # 限定子功能（web_sub_functions.id 前 8 位，如店铺 e1c94163）
    .venv/Scripts/python.exe backend/scripts/baseline_rerun.py --all --sub e1c94163

工件目录 ``.workbuddy/baseline/``：``triage_<tag>.json`` 分诊结果、
``run_<tag>.jsonl`` 执行流水（追加式，重跑自动续）。汇总输出
``docs/基线分诊明细-<今天>.csv`` + 控制台 markdown 摘要。

前置条件：后端已在 127.0.0.1:8000 起好（UAT 资产可访问；后端重启会中断在跑的 run）。
"""
from __future__ import annotations

import argparse
import asyncio
import json
import re
import sys
import time
import urllib.error
import urllib.request
from collections import Counter
from datetime import date
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from sqlalchemy import text  # noqa: E402

from app.config.database import async_session_factory  # noqa: E402

REPO = BACKEND.parent
ART = REPO / ".workbuddy" / "baseline"
DOCS = REPO / "docs"
API = "http://127.0.0.1:8000/api/v2"
CASE_RE = re.compile(r'"case_id"\s*:\s*"(TC-\d+)"')
SCRIPT_BASES = ("backend/workspace/web_cli/artifacts/",
                "backend/workspace/web_cli/artifacts_backup/")
TERMINAL = {"DONE", "REJECTED", "CLOSED", "CANCELLED", "ABORTED"}
RUN_TIMEOUT_S = 900
_opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def call(method: str, path: str, body: dict | None = None, timeout: int = 1800):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(API + path, data=data, method=method,
                                 headers={"Content-Type": "application/json"})
    try:
        with _opener.open(req, timeout=timeout) as r:
            return r.status, r.read().decode()
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode()[:800]


def read_script(rel: str | None) -> str | None:
    for base in SCRIPT_BASES:
        fp = REPO / base / (rel or "")
        if fp.exists():
            return fp.read_text(encoding="utf-8")
    return None


# ---------------------------------------------------------------- 子功能发现

async def discover_subs(sub_prefix: str | None) -> list[dict]:
    """所有「有用例带 requirement_rule」的子功能 + 其项目号。"""
    like = (sub_prefix or "") + "%"
    async with async_session_factory() as s:
        rows = (await s.execute(text("""
            SELECT wsf.id::text, wsf.name, p.identifier, count(tc.id)
            FROM web_sub_functions wsf
            JOIN projects p ON p.id = wsf.project_id
            JOIN test_cases tc ON tc.custom_fields->>'sub_function_id' = wsf.id::text
            WHERE coalesce(tc.custom_fields->'requirement_rule'->>'rule','') <> ''
            GROUP BY wsf.id, wsf.name, p.identifier
            ORDER BY count(tc.id) DESC"""))).fetchall()
    return [{"id": r[0], "name": r[1], "project": r[2], "tag": r[0][:8], "cases": r[3]}
            for r in rows]


async def load_groups(sub_id: str):
    """按 (field, rule 文本) 分组；返回组列表与用例总数。"""
    async with async_session_factory() as s:
        rows = (await s.execute(text("""
            SELECT tc.id::text, tc.identifier,
                   coalesce(tc.custom_fields->'requirement_rule'->>'field','') AS f,
                   coalesce(tc.custom_fields->'requirement_rule'->>'rule','') AS rule
            FROM test_cases tc
            WHERE tc.custom_fields->>'sub_function_id' = :sub
              AND coalesce(tc.custom_fields->'requirement_rule'->>'rule','') <> ''
            ORDER BY tc.identifier"""), {"sub": sub_id})).fetchall()
    buckets: dict[tuple[str, str], list[tuple[str, str]]] = {}
    for cid, ident, f, rule in rows:
        buckets.setdefault((f, rule.strip()), []).append((cid, ident))
    groups = [(f, rule, mem) for (f, rule), mem in buckets.items()]
    groups.sort(key=lambda g: (-len(g[2]), g[0]))
    return groups, len(rows)


# ---------------------------------------------------------------- 分诊

def triage_one(project: str, sub_id: str, field: str, rule: str, members) -> dict:
    """编译一个规则组，判定是否可判（判据=脚本内出现 case_id 归属或断言数>0）。"""
    ids = [m[0] for m in members]
    t0 = time.time()
    st, body = call("POST", f"/projects/{project}/web-case-links/minimal-loop",
                    {"test_case_ids": ids, "sub_function_id": sub_id,
                     "primary_test_case_id": ids[0]})
    d = (json.loads(body).get("data") or {}) if body.startswith("{") else {}
    rc = d.get("compile_receipt") or {}
    wts = d.get("web_tests") or []
    src = read_script(wts[0].get("script_path")) if wts else None
    owners = set(CASE_RE.findall(src or "")) & {m[1] for m in members}
    ac = rc.get("assertion_check_count") or 0
    fc = rc.get("form_executable_checks") or 0
    executable = bool(ac) or bool(owners)
    skips = [x for x in (rc.get("skipped_assertions") or []) if isinstance(x, dict)]
    reason = ""
    if not executable:
        reason = ((skips[0].get("reason") if skips else "") or "编译期零断言/零归属")[:120]
    return {"field": field, "rule": rule[:100], "cases": [m[1] for m in members],
            "n": len(members), "attach": st, "assert": ac, "form_checks": fc,
            "owners": sorted(owners), "executable": executable,
            "skipped_by_scope": rc.get("skipped_by_scope"), "reason": reason,
            "sec": round(time.time() - t0, 1)}


async def do_triage(sub_prefix: str | None) -> list[dict]:
    ART.mkdir(parents=True, exist_ok=True)
    subs = await discover_subs(sub_prefix)
    done = []
    for sub in subs:
        groups, total = await load_groups(sub["id"])
        out = []
        for i, (field, rule, mem) in enumerate(groups, 1):
            g = triage_one(sub["project"], sub["id"], field, rule, mem)
            out.append(g)
            flag = "EXEC" if g["executable"] else "SKIP"
            print(f"[{sub['tag']}] [{i}/{len(groups)}] {flag} n={g['n']:3} "
                  f"assert={g['assert']} owners={len(g['owners'])} {g['sec']}s  {rule[:36]}",
                  flush=True)
        payload = {"tag": sub["tag"], "sub": sub["id"], "name": sub["name"],
                   "project": sub["project"], "total_cases": total, "groups": out}
        fp = ART / f"triage_{sub['tag']}.json"
        fp.write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
        ex = [g for g in out if g["executable"]]
        print(f"[triage] {sub['name']} candidates={total} groups={len(out)} "
              f"exec_groups={len(ex)} exec_cases={sum(g['n'] for g in ex)} -> {fp.name}",
              flush=True)
        done.append(sub)
    return done


# ---------------------------------------------------------------- 执行

async def db_run_row(run: str):
    async with async_session_factory() as s:
        return (await s.execute(text(
            "SELECT run_state, passed_count, failed_count, skipped_count, not_executed_count, "
            "case_attributed_total, case_attributed_pass, case_attributed_fail, "
            "case_attributed_unverified, unattributed_checks "
            "FROM test_runs WHERE identifier=:r"), {"r": run})).first()


async def db_case_rows(run: str):
    async with async_session_factory() as s:
        return (await s.execute(text("""
            SELECT tc.identifier, trtc.latest_status, left(coalesce(res.description,''),240)
            FROM test_run_test_cases trtc
            JOIN test_cases tc ON tc.id=trtc.test_case_id
            JOIN test_runs tr ON tr.id=trtc.test_run_id
            LEFT JOIN test_results res ON res.test_run_test_case_id=trtc.id
            WHERE tr.identifier=:r ORDER BY tc.identifier"""), {"r": run})).fetchall()


async def do_run(sub_prefix: str | None, max_groups: int) -> None:
    fps = latest_triage_files(sub_prefix)
    if not fps:
        print("[run] 没有分诊结果，先跑 --triage", flush=True)
        return
    for fp in fps:
        tri = json.loads(fp.read_text(encoding="utf-8"))
        tag, sub, project = tri["tag"], tri["sub"], tri.get("project", "PR-9")
        runlog = ART / f"run_{tag}.jsonl"
        done_rules: set[str] = set()
        if runlog.exists():
            for line in runlog.read_text(encoding="utf-8").splitlines():
                try:
                    done_rules.add(json.loads(line)["rule"])
                except Exception:
                    pass
        groups = [g for g in tri["groups"]
                  if g["executable"] and g["rule"] not in done_rules][:max_groups]
        print(f"[run] {tri.get('name', tag)} exec_groups={len(groups)} "
              f"cases={sum(g['n'] for g in groups)}", flush=True)
        for idx, g in enumerate(groups, 1):
            async with async_session_factory() as s:
                members = (await s.execute(text(
                    "SELECT id::text, identifier FROM test_cases WHERE identifier = ANY(:t)"),
                    {"t": g["cases"]})).fetchall()
            ids = [m[0] for m in members]
            t0 = time.time()
            st, body = call("POST", f"/projects/{project}/test-runs",
                            {"name": f"基线-{tag}-{g['field']}-{int(time.time()) % 100000}",
                             "test_cases": ids})
            run = (json.loads(body).get("data") or {}).get("identifier") if st == 201 else None
            if not run:
                print(f"[{idx}/{len(groups)}] CREATE_FAIL {st} {body[:120]}", flush=True)
                continue
            st2, body2 = call("POST", f"/projects/{project}/web-case-links/minimal-loop",
                              {"test_case_ids": ids, "sub_function_id": sub,
                               "test_run_identifier": run, "primary_test_case_id": ids[0]})
            if st2 not in (200, 201):
                print(f"[{idx}/{len(groups)}] {run} ATTACH_FAIL {st2} {body2[:120]}", flush=True)
                continue
            call("POST", f"/projects/{project}/test-runs/{run}/execute", {})
            deadline = time.time() + RUN_TIMEOUT_S
            row = None
            while time.time() < deadline:
                await asyncio.sleep(8)
                row = await db_run_row(run)
                if row and row[0] in TERMINAL:
                    break
            cases = await db_case_rows(run)
            rec = {"group": g["field"], "rule": g["rule"], "run": run,
                   "state": row[0] if row else "TIMEOUT",
                   "passed": row[1] if row else None, "failed": row[2] if row else None,
                   "skipped": row[3] if row else None, "ne": row[4] if row else None,
                   "attr": row[5] if row else None, "attr_pass": row[6] if row else None,
                   "attr_fail": row[7] if row else None, "attr_unv": row[8] if row else None,
                   "unattr": row[9] if row else None,
                   "cases": [{"id": c[0], "status": c[1], "desc": c[2]} for c in cases],
                   "sec": round(time.time() - t0, 1)}
            with runlog.open("a", encoding="utf-8") as f:
                f.write(json.dumps(rec, ensure_ascii=False) + "\n")
            cnt = Counter(c[1] for c in cases)
            print(f"[{idx}/{len(groups)}] {run} {rec['state']} attr={rec['attr']} "
                  f"unattr={rec['unattr']} {dict(cnt)} {rec['sec']}s  {g['field']}", flush=True)


# ---------------------------------------------------------------- 汇总

def _cat(g: dict) -> str:
    f, rule = g["field"], g["rule"]
    if f == "導入" or "導入" in rule[:6]:
        return "需导入任务入口（MDM 实测无该菜单）"
    if "篩選字段" in rule or "筛选字段" in rule:
        return "列表筛选条件（无列表筛选通道）"
    if "只讀/不可手工修改" in rule:
        return "字段只读态（无只读校验通道）"
    if "地圖組件" in f:
        return "地图组件交互（无控件通道）"
    return "其它无执行通道"


def latest_triage_files(sub_prefix: str | None = None) -> list[Path]:
    """每个子功能只保留**最新**一份分诊结果。

    文件名不足以判重：历史工件用过语义 tag（store/goods/...），新分诊用子功能
    id 前 8 位，同一子功能会并存两份。不按 ``sub`` 去重就会把同批用例统计两遍。
    """
    if not ART.exists():
        return []
    newest: dict[str, tuple[float, Path]] = {}
    for p in ART.glob("triage_*.json"):
        try:
            d = json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            continue
        key = d.get("sub") or p.stem
        mt = p.stat().st_mtime
        if key not in newest or mt > newest[key][0]:
            newest[key] = (mt, p)
    fps = sorted((v[1] for v in newest.values()), key=lambda p: p.name)
    if sub_prefix:
        fps = [p for p in fps if sub_prefix in p.name]
    return fps


async def known_defect_map() -> dict[str, str]:
    async with async_session_factory() as s:
        rows = (await s.execute(text("""
            SELECT identifier, custom_fields->'known_defect'
            FROM test_cases
            WHERE custom_fields->'known_defect' IS NOT NULL"""))).fetchall()
    out = {}
    for ident, kd in rows:
        if isinstance(kd, str):
            kd = json.loads(kd)
        issues = ",".join(i.get("id", "") for i in (kd.get("idp_issue") or [])) if kd else ""
        out[ident] = f"known_defect({kd.get('status', '')};{issues})" if kd else ""
    return out


async def do_report() -> Path:
    fps = latest_triage_files()
    if not fps:
        print("[report] 没有分诊结果", flush=True)
        raise SystemExit(1)
    kmap = await known_defect_map()
    import csv
    import glob
    rows, summary = [], []
    for fp in fps:
        tri = json.loads(fp.read_text(encoding="utf-8"))
        ex = [g for g in tri["groups"] if g["executable"]]
        sk = [g for g in tri["groups"] if not g["executable"]]
        cats = Counter()
        for g in sk:
            cats[_cat(g)] += g["n"]
        summary.append({"label": tri.get("name", tri["tag"]), "cases": tri["total_cases"],
                        "groups": len(tri["groups"]), "exec_groups": len(ex),
                        "exec_cases": sum(g["n"] for g in ex),
                        "skip_cases": sum(g["n"] for g in sk), "cats": cats})
        for g in tri["groups"]:
            cat = "" if g["executable"] else _cat(g)
            for c in g["cases"]:
                rows.append({"子功能": tri.get("name", tri["tag"]), "用例": c,
                             "规则字段": g["field"],
                             "可判": "是" if g["executable"] else "否",
                             "不可判原因": cat, "规则文本(前80)": g["rule"]})
    verdict = {}
    for rl in glob.glob(str(ART / "run_*.jsonl")):
        for line in open(rl, encoding="utf-8"):
            r = json.loads(line)
            for c in r.get("cases") or []:
                verdict[c["id"]] = (r["run"], c["status"], (c.get("desc") or "")[:160])
    for r in rows:
        v = verdict.get(r["用例"])
        r["运行"] = v[0] if v else ""
        r["结论"] = v[1] if v else "未执行（编译期无可判断言）"
        r["结果描述(前160)"] = v[2] if v else ""
        r["已知缺陷标注"] = kmap.get(r["用例"], "")
    DOCS.mkdir(parents=True, exist_ok=True)
    out = DOCS / f"基线分诊明细-{date.today():%Y%m%d}.csv"
    with out.open("w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=["子功能", "用例", "规则字段", "可判", "不可判原因",
                                          "运行", "结论", "已知缺陷标注",
                                          "规则文本(前80)", "结果描述(前160)"])
        w.writeheader()
        for r in rows:
            w.writerow({k: r.get(k, "") for k in w.fieldnames})
    print("| 子功能 | 有用例(有规则) | 规则组 | 可判组 | 可判用例 | 不可判用例 |")
    print("|---|---|---|---|---|---|")
    tot = [0] * 5
    for s in summary:
        print(f"| {s['label']} | {s['cases']} | {s['groups']} | {s['exec_groups']} | "
              f"{s['exec_cases']} | {s['skip_cases']} |")
        for i, v in enumerate((s["cases"], s["groups"], s["exec_groups"],
                               s["exec_cases"], s["skip_cases"])):
            tot[i] += v
    print(f"| **合计** | {tot[0]} | {tot[1]} | {tot[2]} | {tot[3]} | {tot[4]} |")
    print("\n不可判原因分布：")
    allcats: Counter = Counter()
    for s in summary:
        allcats.update(s["cats"])
    for k, v in allcats.most_common():
        print(f"  {k}: {v}")
    st = Counter(r["结论"] for r in rows)
    print("\n结论分布：", dict(st))
    print("CSV ->", out)
    return out


# ---------------------------------------------------------------- 入口

def main() -> None:
    ap = argparse.ArgumentParser(description="YL/CS 基线一键复跑")
    ap.add_argument("--all", action="store_true", help="分诊 → 执行 → 汇总")
    ap.add_argument("--triage", action="store_true", help="只分诊")
    ap.add_argument("--run", action="store_true", help="只执行可判组")
    ap.add_argument("--report", action="store_true", help="只汇总")
    ap.add_argument("--sub", default=None, help="限定子功能 id 前 8 位（如 e1c94163）")
    ap.add_argument("--max-groups", type=int, default=999, help="每个子功能最多执行的组数")
    a = ap.parse_args()
    if not (a.all or a.triage or a.run or a.report):
        ap.error("至少指定一个动作：--all / --triage / --run / --report")

    async def _go():
        if a.all or a.triage:
            await do_triage(a.sub)
        if a.all or a.run:
            await do_run(a.sub, a.max_groups)
        if a.all or a.report:
            await do_report()

    asyncio.run(_go())


if __name__ == "__main__":
    main()
