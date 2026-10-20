"""只读核查：三族归集资产的脚本落盘与执行覆盖（阶段 3 计划依据）。"""
import os
from collections import Counter

import psycopg2

BASE = r"D:/code/Pyproject/ai-test-agent-system-platform/backend/workspace"
PROJ = "942226d9-6071-43fa-b2bb-f7cbc93a0028"
MODS = ("店鋪主數據管理", "商品主數據", "支付方式主數據")

c = psycopg2.connect(host="127.0.0.1", port=5432, user="postgres",
                     password="postgres", dbname="langgraph_platform_db")
cur = c.cursor()

cur.execute("""
select t.module, w.identifier, w.script_path, w.created_at
from web_tests w join test_cases t on t.id = w.test_case_id
where w.project_id = %s and w.generated_by_agent = 'web_case_link_service'
  and t.module = any(%s)
""", (PROJ, list(MODS)))
rows = cur.fetchall()
stat = Counter()
missing_sample = []
for mod, ident, sp, _ca in rows:
    exists = bool(sp) and os.path.exists(os.path.join(BASE, sp))
    stat[(mod, exists)] += 1
    if not exists and len(missing_sample) < 3:
        missing_sample.append((ident, sp))

print("== 脚本落盘 ==")
for (mod, ex), n in sorted(stat.items()):
    print(f"  {mod}  file_exists={ex} -> {n}")
for ident, sp in missing_sample:
    print("  miss sample:", ident, (sp or "")[-60:])

print("\n== 交付资产 ==")
cur.execute("""
select identifier, script_path, created_at from web_tests
where identifier in ('WT-111DBD5A','WT-5D1D7699','WT-C39CFD02','WT-FD24E5E8')
""")
for ident, sp, ca in cur.fetchall():
    p = os.path.join(BASE, sp) if sp else None
    print(f"  {ident} exists={bool(p) and os.path.exists(p)} {(sp or '')[-45:]} {ca}")

print("\n== 执行覆盖（三族资产上是否有 run）==")
cur.execute("""
select count(distinct w.id), count(distinct r.web_test_id)
from web_tests w join test_cases t on t.id = w.test_case_id
left join web_test_runs r on r.web_test_id = w.id
where w.project_id = %s and w.generated_by_agent = 'web_case_link_service'
  and t.module = any(%s)
""", (PROJ, list(MODS)))
print("  assets / assets_with_run:", cur.fetchone())
c.close()
