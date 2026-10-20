# -*- coding: utf-8 -*-
"""复原被「整批编译验证」污染的 YL 用例 web_coverage 绑定。

背景
----
2026-10-16 22:54~22:55，我为了验证编译产物，用 audit_yl_batch_compile.py
以「一个批次传 42/18/13 条用例」的方式调用了 POST /web-case-links/minimal-loop。
该接口的语义是：`_select_test_case` 只选 1 条主用例 → 只创建/刷新 1 个 WebTest →
`_bind_batch_cases_to_asset` 把**批次内其余用例**写成 `web_coverage.role='batch'`
且 `web_test_id` 指向**主用例那条资产**。

副作用（实测）：69 条原本 role='primary'、指向自身资产的用例，被改写成
role='batch' + 指向他人资产 + bound_at=2026-10-16。

修复口径（对齐 10-15 的规范形态：79/79 条 primary 的 cov.web_test_id 都命中自身资产）
--------------------------------------------------------------------------------
仅还原「用例 → 资产」的所有权指针，共 4 个键：
  role               batch → primary
  web_test_id        他人资产 → 自身资产
  web_test_identifier 同上
  bound_at           2026-10-16 → 自身资产的 created_at（重建值，非原始绑定时刻）
以及 test_cases.custom_fields.web_test_id（与 cov 保持一致）。

**不动** rule_id / compile_status / assertion_count / auto_executable：
这几项是本次真实编译按用例逐条算出来的，为有效值。

用法
----
  python fix_yl_webcoverage_role.py            # dry-run，只打印
  python fix_yl_webcoverage_role.py --apply    # 落库（先写 before 快照）
"""
from __future__ import annotations

import json
import sys
from datetime import datetime
from pathlib import Path

import psycopg2

ROOT = Path(r"D:/code/Pyproject/ai-test-agent-system-platform")
AUDIT = ROOT / "out_yl_case_audit"
DB = dict(host="127.0.0.1", port=5432, user="postgres",
          password="postgres", dbname="langgraph_platform_db")

YL_SUB_FUNCTIONS = [
    "e1c94163-d221-4ac8-afdc-c2103f989e5f",   # WSF-YL-STORE
    "f04ac33d-eb21-477e-8b2c-944c571193bb",   # WSF-YL-STORE-RW
    "d90c2034-0ac2-4f12-91c7-a0be5f2f2103",   # WSF-YL-GOODS
    "bb36f2e0-4a5b-408e-950a-8a34dc504e95",   # WSF-YL-GOODS-RW
    "af320aac-6d37-4cc9-b1d7-1213aa5eced4",   # WSF-YL-PAY
    "b5b8569a-0238-42eb-9cc3-99c2e9c8a97c",   # WSF-YL-PAY-RW
]


def main() -> int:
    apply_ = "--apply" in sys.argv
    AUDIT.mkdir(exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")

    conn = psycopg2.connect(**DB)
    cur = conn.cursor()

    cur.execute(
        """
        select tc.id::text, tc.identifier, tc.name, tc.custom_fields
        from test_cases tc
        where tc.custom_fields->>'sub_function_id' = any(%s)
          and tc.custom_fields->'web_coverage'->>'role' = 'batch'
        order by tc.identifier
        """,
        (YL_SUB_FUNCTIONS,),
    )
    targets = cur.fetchall()
    print(f"待修复（role=batch 的 YL 用例）: {len(targets)} 条")
    if not targets:
        print("无需修复")
        return 0

    before = []
    plan = []
    skipped = []
    for tc_id, ident, name, fields in targets:
        fields = fields or {}
        cov = dict(fields.get("web_coverage") or {})

        cur.execute(
            """
            select id::text, identifier, created_at
            from web_tests where test_case_id = %s::uuid
            order by created_at desc
            """,
            (tc_id,),
        )
        own = cur.fetchall()
        if len(own) != 1:
            skipped.append({"identifier": ident, "reason": f"own_assets={len(own)}"})
            continue
        own_id, own_ident, own_created = own[0]

        before.append({
            "identifier": ident, "test_case_id": tc_id, "name": name,
            "custom_fields": fields,
        })
        plan.append({
            "identifier": ident, "test_case_id": tc_id,
            "old_role": cov.get("role"),
            "new_role": "primary",
            "old_wt": cov.get("web_test_id"),
            "new_wt": own_id,
            "old_wt_ident": cov.get("web_test_identifier"),
            "new_wt_ident": own_ident,
            "old_bound_at": cov.get("bound_at"),
            "new_bound_at": own_created.replace(microsecond=0, tzinfo=None).isoformat(),
        })

    print(f"可修复 {len(plan)} 条；跳过 {len(skipped)} 条 {skipped[:5]}")
    print("\n前 5 条明细：")
    for p in plan[:5]:
        print(f"  {p['identifier']:12} {p['old_role']} -> {p['new_role']}  "
              f"{str(p['old_wt'])[:8]} -> {str(p['new_wt'])[:8]}  "
              f"{p['old_bound_at']} -> {p['new_bound_at']}")

    before_path = AUDIT / f"yl_webcoverage_before_fix_{stamp}.json"
    before_path.write_text(
        json.dumps({"stamp": stamp, "count": len(before), "records": before},
                   ensure_ascii=False, indent=1),
        encoding="utf-8",
    )
    plan_path = AUDIT / f"yl_webcoverage_fix_plan_{stamp}.json"
    plan_path.write_text(
        json.dumps({"stamp": stamp, "apply": apply_, "count": len(plan),
                    "skipped": skipped, "plan": plan}, ensure_ascii=False, indent=1),
        encoding="utf-8",
    )
    print(f"\nbefore 快照: {before_path.name}")
    print(f"修复计划  : {plan_path.name}")

    if not apply_:
        print("\n[dry-run] 未落库。加 --apply 执行。")
        return 0

    # psycopg2 无法可靠改写嵌套 jsonb，用 jsonb_set 逐键更新
    done = 0
    for p in plan:
        cur.execute(
            """
            update test_cases
               set custom_fields = jsonb_set(
                     jsonb_set(
                       jsonb_set(
                         jsonb_set(
                           jsonb_set(custom_fields,
                             '{web_coverage,role}', to_jsonb(%s::text), true),
                           '{web_coverage,web_test_id}', to_jsonb(%s::text), true),
                         '{web_coverage,web_test_identifier}', to_jsonb(%s::text), true),
                       '{web_coverage,bound_at}', to_jsonb(%s::text), true),
                     '{web_test_id}', to_jsonb(%s::text), true),
                   updated_at = now()
             where id = %s::uuid
            """,
            ("primary", p["new_wt"], p["new_wt_ident"],
             p["new_bound_at"], p["new_wt"], p["test_case_id"]),
        )
        done += cur.rowcount
    conn.commit()
    print(f"\n[apply] 已更新 {done}/{len(plan)} 行")

    # 回读校验
    cur.execute(
        """
        select
          count(*) filter (where custom_fields->'web_coverage'->>'role'='batch'),
          count(*) filter (where custom_fields->'web_coverage'->>'role'='primary'),
          count(*) filter (where exists (
              select 1 from web_tests wt
              where wt.test_case_id = tc.id
                and wt.id::text = tc.custom_fields->'web_coverage'->>'web_test_id'))
        from test_cases tc
        where tc.custom_fields->>'sub_function_id' = any(%s)
        """,
        (YL_SUB_FUNCTIONS,),
    )
    n_batch, n_primary, n_own = cur.fetchone()
    print(f"[VERIFY] YL 已归集用例：role=batch {n_batch} / role=primary {n_primary} / "
          f"其中 cov.web_test_id 命中自身资产 {n_own}")
    ok = (n_batch == 0 and n_primary == 152 and n_own == 152)
    print("[RESULT]", "ALL_OK" if ok else "STILL_DIFF")
    return 0 if ok else 4


if __name__ == "__main__":
    raise SystemExit(main())
