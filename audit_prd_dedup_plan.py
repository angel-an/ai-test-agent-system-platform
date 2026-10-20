"""PRD 用例指纹去重清单（只读，不写库）。

指纹 = (标题, 断言集合 JSON)。同指纹 ⇒ 断言逐字相同，回归收益为零，
保留 1 条即可（保留策略：保留 rule_id 最完整/最早创建的一条）。
产物：out_yl_case_audit/prd_dedup_plan_20261017.json
"""
import json
import re
from collections import defaultdict
from datetime import datetime

import psycopg2

PROJ = "942226d9-6071-43fa-b2bb-f7cbc93a0028"
SCOPE = ("(module in ('店鋪主數據管理','商品主數據','支付方式主數據') "
         "or module like 'YL-主數據管理%%' or module like 'YL-主数据管理%%')")

c = psycopg2.connect(host="127.0.0.1", port=5432, user="postgres",
                     password="postgres", dbname="langgraph_platform_db")
c.set_session(autocommit=True)
cur = c.cursor()
cur.execute(f"""
select t.id::text, t.identifier, t.name, t.module, t.created_at,
       coalesce(t.custom_fields->>'requirement_rule_id',''),
       coalesce(t.custom_fields->>'sub_function_id',''),
       coalesce(t.custom_fields->'assertions','[]'::jsonb)::text
from test_cases t
where t.project_id = %s and {SCOPE}
  and (t.custom_fields->>'module_source' in ('prd_section','prd_section_evidence')
       or coalesce(t.description,'') like '基于上传需求文档%%')
order by t.created_at
""", (PROJ,))
rows = cur.fetchall()

groups = defaultdict(list)
for cid, ident, name, module, created, rule, sf, ajson in rows:
    key = (name.strip(), ajson)
    groups[key].append(dict(id=cid, identifier=ident, name=name, module=module,
                            created=str(created), rule_id=rule, bound=bool(sf)))

plan = {"generated_at": datetime.now().isoformat(timespec="seconds"),
        "total": len(rows), "groups": [], "stats": {}}
dup_rows = 0
bound_dup = 0
for (name, ajson), members in groups.items():
    if len(members) < 2:
        continue
    # 保留策略：优先「已绑定子功能」且 rule_id 非空的最早一条；否则最早一条
    ranked = sorted(members, key=lambda m: (not (m["bound"] and m["rule_id"]), m["created"]))
    keep = ranked[0]
    drops = ranked[1:]
    dup_rows += len(drops)
    bound_dup += sum(1 for d in drops if d["bound"])
    plan["groups"].append({
        "fingerprint": {"name": name, "assertions_sha": None,
                        "assertions_len": len(ajson)},
        "keep": keep, "duplicate_of": keep["identifier"],
        "drop": drops, "n_members": len(members),
    })

plan["stats"] = {
    "prd_total": len(rows),
    "duplicate_rows": dup_rows,
    "duplicate_groups": len(plan["groups"]),
    "duplicate_rows_bound": bound_dup,
    "after_dedup": len(rows) - dup_rows,
    "top10_groups": [
        {"name": g["keep"]["name"][:60], "n": g["n_members"]}
        for g in sorted(plan["groups"], key=lambda x: -x["n_members"])[:10]
    ],
}

json.dump(plan, open("out_yl_case_audit/prd_dedup_plan_20261017.json", "w", encoding="utf-8"),
          ensure_ascii=False, indent=1)
print("PRD total:", plan["stats"]["prd_total"])
print("重复组:", plan["stats"]["duplicate_groups"], " 可去重行:", dup_rows,
      "（其中已绑定子功能:", bound_dup, "）")
print("去重后:", plan["stats"]["after_dedup"])
print("Top 组:")
for g in plan["stats"]["top10_groups"]:
    print(f"   {g['n']}x {g['name']}")
print("saved -> out_yl_case_audit/prd_dedup_plan_20261017.json")
