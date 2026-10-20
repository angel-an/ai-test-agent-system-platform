# -*- coding: utf-8 -*-
"""YL 三模块「需求直生成 vs 人工导入」用例的双向验证取证脚本。

产出（落盘到 out_yl_case_audit/）：
  A_inventory.json      用例来源盘点（子功能 × 来源）
  B_execution.json      执行覆盖（总数 / 有运行记录 / 最新状态分布）
  C_quality.json        需求直生成用例的结构化质量体检
  D_scriptability.json  人工导入用例的脚本化能力（是否生成 web_test 脚本 + 内容特征）
  E_detail.json         逐用例明细（来源 / 是否脚本化 / 最新状态）
运行：./.venv/Scripts/python.exe audit_yl_cases.py
"""
from __future__ import annotations

import json
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path

import psycopg2

OUT = Path(__file__).parent / "out_yl_case_audit"
OUT.mkdir(exist_ok=True)

SFS = {
    "e1c94163-d221-4ac8-afdc-c2103f989e5f": "WSF-YL-STORE",
    "d90c2034-0ac2-4f12-91c7-a0be5f2f2103": "WSF-YL-GOODS",
    "af320aac-6d37-4cc9-b1d7-1213aa5eced4": "WSF-YL-PAY",
    "f04ac33d-eb21-477e-8b2c-944c571193bb": "WSF-YL-STORE-RW",
    "bb36f2e0-4a5b-408e-950a-8a34dc504e95": "WSF-YL-GOODS-RW",
    "b5b8569a-0238-42eb-9cc3-99c2e9c8a97c": "WSF-YL-PAY-RW",
}
IDS = tuple(SFS)


def dump(name: str, obj) -> None:
    (OUT / name).write_text(json.dumps(obj, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"  -> {name}")


def main() -> None:
    conn = psycopg2.connect(host="127.0.0.1", port=5432, user="postgres",
                            password="postgres", dbname="langgraph_platform_db")
    cur = conn.cursor()

    # ---------- 取全部目标用例 ----------
    cur.execute("""
        select id, identifier, name, description, preconditions, priority, template,
               custom_fields, module, feature, created_at
        from test_cases
        where custom_fields->>'sub_function_id' = any(%s)
        order by identifier
    """, (list(IDS),))
    cases = {}
    for (cid, ident, name, desc, pre, prio, tpl, cf, module, feature, created) in cur.fetchall():
        cf = cf or {}
        cases[cid] = dict(
            uuid=str(cid), identifier=ident, name=name, module=module, feature=feature,
            priority=prio, cf=cf, created=created,
            has_desc=bool(desc and str(desc).strip()),
            has_pre=bool(pre and (pre if isinstance(pre, str) else json.dumps(pre)).strip(" []\"")),
            tpl_len=len(tpl) if tpl else 0,
        )
    print(f"目标用例总数 = {len(cases)}")

    def src_of(cf: dict) -> str:
        return cf.get("generated_from") or "(未标注)"

    # ---------- A 盘点 ----------
    inv = defaultdict(Counter)
    for c in cases.values():
        sf = SFS[c["cf"].get("sub_function_id")]
        inv[sf][src_of(c["cf"])] += 1
    dump("A_inventory.json", {k: dict(v) for k, v in inv.items()})

    # ---------- B 执行覆盖 ----------
    cur.execute("""
        select tc.id, trtc.latest_status, rtr.identifier, trtc.updated_at
        from test_run_test_cases trtc
        join test_cases tc on tc.id = trtc.test_case_id
        join test_runs rtr on rtr.id = trtc.test_run_id
        where tc.custom_fields->>'sub_function_id' = any(%s)
    """, (list(IDS),))
    per_case = defaultdict(list)
    for cid, st, tr, upd in cur.fetchall():
        per_case[cid].append((tr, str(st), upd))

    execov = defaultdict(lambda: defaultdict(lambda: {
        "total": 0, "with_run": 0, "latest_status": Counter(), "runs": 0}))
    for c in cases.values():
        sf = SFS[c["cf"].get("sub_function_id")]
        s = src_of(c["cf"])
        e = execov[sf][s]
        e["total"] += 1
        recs = per_case.get(c["uuid"]) or []
        if recs:
            e["with_run"] += 1
            e["runs"] += len(recs)
            e["latest_status"][max(recs, key=lambda x: (str(x[2] or ""), str(x[0])))[1]] += 1
        else:
            e["latest_status"]["NEVER_IN_ANY_RUN"] += 1
    dump("B_execution.json", {
        sf: {s: {**v, "latest_status": dict(v["latest_status"])}
             for s, v in d.items()} for sf, d in execov.items()})

    # ---------- C 需求直生成：质量体检 ----------
    # 需求直生成 = generated_from 为 document_ai（含历史未标注但 module 属 YL 且无 human_xlsx 的）
    quality = {}
    for c in cases.values():
        cf = c["cf"]
        if src_of(cf) != "document_ai":
            continue
        sf = SFS[cf.get("sub_function_id")]
        q = quality.setdefault(sf, Counter())
        q["total"] += 1
        q["has_desc"] += int(c["has_desc"])
        q["has_pre"] += int(c["has_pre"])
        q["has_priority"] += int(bool(c["priority"]))
        q["has_expected"] += int(bool(cf.get("web_validation_path")))
        q["has_assertions"] += int(bool(cf.get("assertions")))
        q["has_rule_id"] += int(bool(cf.get("requirement_rule_id")))
        q["has_steps_tpl"] += int(c["tpl_len"] > 0)
        q["scriptable_flag"] += int(bool(cf.get("web_test_identifier") or cf.get("web_test_id")))
        q["manual_execution"] += int(bool(cf.get("manual_execution")))
    dump("C_quality.json", {k: dict(v) for k, v in quality.items()})

    # ---------- D 人工导入：脚本化能力 ----------
    # 平台侧脚本载体 = web_tests（test_case_id 指向用例）
    cur.execute("""
        select test_case_id, identifier, name, script_path, generated_by_agent,
               generation_params, test_config
        from web_tests
        where test_case_id is not null
    """)
    wt_by_case = {}
    for cid, ident, name, path, by_agent, gp, cfg in cur.fetchall():
        wt_by_case.setdefault(cid, []).append(dict(
            identifier=ident, name=name, path=path or "", by_agent=by_agent,
            gp=gp or {}, cfg_len=len(json.dumps(cfg or {}))))
    scriptability = {}
    for c in cases.values():
        cf = c["cf"]
        if src_of(cf) != "human_import":
            continue
        sf = SFS[cf.get("sub_function_id")]
        s = scriptability.setdefault(sf, Counter())
        s["total"] += 1
        wts = wt_by_case.get(c["uuid"])
        s["has_web_test"] += int(bool(wts))
        s["has_web_test_id_in_cf"] += int(bool(cf.get("web_test_identifier") or cf.get("web_test_id")))
        s["declared_executable_type"] += int(bool(cf.get("executable_type")))
        s["path_matched"] += int(str(cf.get("web_path_match_status") or "") in ("matched", "MATCHED", "ok"))
    dump("D_scriptability.json", {k: dict(v) for k, v in scriptability.items()})

    # ---------- E 明细 ----------
    detail = []
    for c in cases.values():
        cf = c["cf"]
        recs = per_case.get(c["uuid"]) or []
        detail.append(dict(
            identifier=c["identifier"], name=c["name"], module=c["module"],
            sub_function=SFS[cf.get("sub_function_id")], source=src_of(cf),
            content_source=cf.get("content_source"),
            priority=c["priority"], has_desc=c["has_desc"], has_pre=c["has_pre"],
            web_test_identifier=cf.get("web_test_identifier") or cf.get("web_test_id") or "",
            executable_type=cf.get("executable_type") or "",
            manual_execution=bool(cf.get("manual_execution")),
            run_count=len(recs),
            latest_status=(max(recs, key=lambda x: (str(x[2] or ""), str(x[0])))[1] if recs else "NEVER_IN_ANY_RUN"),
            runs=[r[0] for r in recs],
        ))
    detail.sort(key=lambda d: (d["sub_function"], d["source"], d["identifier"]))
    dump("E_detail.json", detail)
    print("完成。目录：", OUT)


if __name__ == "__main__":
    main()
