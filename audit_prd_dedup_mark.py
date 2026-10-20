# -*- coding: utf-8 -*-
"""P1 后续：PRD 用例去重标记执行（用户已批准）。
流程：快照 → 分批 UPDATE custom_fields.duplicate_of → 每批回读逐字比对 → 全量核验。
只写 custom_fields 两个键，不动业务字段、不删行。
"""
import json, sys, io
from datetime import datetime
import psycopg2
import psycopg2.extras

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

PROJ = "942226d9-6071-43fa-b2bb-f7cbc93a0028"
PLAN = "out_yl_case_audit/prd_dedup_plan_20261017.json"
SNAPSHOT = "out_yl_case_audit/prd_dedup_snapshot_20261017.json"
RESULT = "out_yl_case_audit/prd_dedup_mark_result_20261017.json"
BATCH = 50

plan = json.load(open(PLAN, encoding="utf-8"))
# 待标记: id -> keep_identifier
marks = {}
keep_ids = set()
for g in plan["groups"]:
    keep_ids.add(g["keep"]["id"])
    for d in g["drop"]:
        assert d["id"] not in keep_ids, f"同 id 既是 keep 又是 drop: {d['id']}"
        marks[d["id"]] = {
            "identifier": d["identifier"],
            "duplicate_of": g["duplicate_of"],
            "group_name": g["fingerprint"]["name"],
        }
print(f"待标记 {len(marks)} 条，keep {len(keep_ids)} 条，组 {len(plan['groups'])} 组")

conn = psycopg2.connect(host="127.0.0.1", port=5432, user="postgres", password="postgres", dbname="langgraph_platform_db")
conn.autocommit = False
cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

# ---------- 1) 快照（受影响行 + keep 行） ----------
ids = list(marks.keys()) + list(keep_ids)
cur.execute(
    """select id::text, identifier, name, module, custom_fields
       from test_cases where project_id=%s and id::text = any(%s)""",
    (PROJ, ids),
)
snap = {r["id"]: dict(r) for r in cur.fetchall()}
missing = [i for i in ids if i not in snap]
assert not missing, f"计划中的行在库里缺失: {missing[:5]}"
# 快照中保留 custom_fields 原样（仅去重相关键），避免体积爆炸
for r in snap.values():
    cf = r["custom_fields"] or {}
    r["custom_fields_prev"] = {
        k: cf.get(k) for k in ("duplicate_of", "duplicate_of_group", "dedup_marked_at")
    }
    del r["custom_fields"]
json.dump({"snapshot_at": datetime.now().isoformat(), "rows": snap}, open(SNAPSHOT, "w", encoding="utf-8"), ensure_ascii=False)
print(f"快照已写 {SNAPSHOT}（{len(snap)} 行）")

# ---------- 2) 幂等预检 ----------
already = [i for i, m in marks.items() if (snap[i]["custom_fields_prev"].get("duplicate_of"))]
if already:
    print(f"已有 duplicate_of 标记 {len(already)} 条（将覆盖为计划值）")

# ---------- 3) 分批写入 + 逐批回读 ----------
now = datetime.now().isoformat(timespec="seconds")
ok, fail = [], []
conn.commit()  # 结束快照只读事务，再切 autocommit
conn.autocommit = True  # 每条 UPDATE 独立提交，避免长事务
items = list(marks.items())
for s in range(0, len(items), BATCH):
    batch = items[s : s + BATCH]
    for cid, m in batch:
        cur.execute(
            """update test_cases set custom_fields = jsonb_set(jsonb_set(jsonb_set(
                   coalesce(custom_fields,'{}'::jsonb),
                   '{duplicate_of}', to_jsonb(%s::text), true),
                   '{duplicate_of_group}', to_jsonb(%s::text), true),
                   '{dedup_marked_at}', to_jsonb(%s::text), true),
                   updated_at = now()
               where id::text=%s and project_id=%s
               returning identifier""",
            (m["duplicate_of"], m["group_name"], now, cid, PROJ),
        )
        row = cur.fetchone()
        if not row:
            fail.append({"id": cid, "identifier": m["identifier"], "err": "update returned 0 rows"})
            continue
        # 回读逐字比对
        cur.execute("select custom_fields from test_cases where id::text=%s", (cid,))
        cf = cur.fetchone()["custom_fields"]
        if (
            cf.get("duplicate_of") == m["duplicate_of"]
            and cf.get("duplicate_of_group") == m["group_name"]
            and cf.get("dedup_marked_at") == now
        ):
            ok.append(cid)
        else:
            fail.append({"id": cid, "identifier": m["identifier"], "err": "readback mismatch",
                         "got": {k: cf.get(k) for k in ("duplicate_of", "duplicate_of_group", "dedup_marked_at")}})
    print(f"  batch {s//BATCH+1}: 累计 ok={len(ok)} fail={len(fail)}")
    if fail:
        break  # 出错即停，不做全量

# ---------- 4) 全量核验 ----------
cur.execute(
    """select count(*) from test_cases where project_id=%s
       and custom_fields->>'dedup_marked_at'=%s and id::text = any(%s)""",
    (PROJ, now, list(marks.keys())),
)
verified = cur.fetchone()["count"]
# keep 行必须未被改动
cur.execute(
    """select count(*) from test_cases where project_id=%s and id::text = any(%s)
       and (custom_fields->>'duplicate_of') is not null""",
    (PROJ, list(keep_ids)),
)
keep_touched = cur.fetchone()["count"]

result = {
    "executed_at": now, "planned": len(marks), "marked_ok": len(ok),
    "fail": fail, "verified_rows": verified, "keep_rows_touched": keep_touched,
    "keep_expected_untouched": 0, "snapshot": SNAPSHOT,
}
json.dump(result, open(RESULT, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
print(f"核验: marked={verified}/{len(marks)}  keep 被误动={keep_touched}（应为0）")
print("PASS" if verified == len(marks) and keep_touched == 0 and not fail else "FAIL")
