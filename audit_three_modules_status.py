"""只读核查：三族资产的真实执行覆盖与最近一次结论。"""
from collections import Counter, defaultdict

import psycopg2

PROJ = "942226d9-6071-43fa-b2bb-f7cbc93a0028"
MODS = ("店鋪主數據管理", "商品主數據", "支付方式主數據")

c = psycopg2.connect(host="127.0.0.1", port=5432, user="postgres",
                     password="postgres", dbname="langgraph_platform_db")
cur = c.cursor()

# 1) 每族：资产数 / 有 run 的资产数
cur.execute("""
select t.module,
       count(distinct w.id) as assets,
       count(distinct r.web_test_id) as assets_with_run,
       count(r.id) as total_runs
from web_tests w
join test_cases t on t.id = w.test_case_id
left join web_test_runs r on r.web_test_id = w.id
where w.project_id = %s and w.generated_by_agent = 'web_case_link_service'
  and t.module = any(%s)
group by 1 order by 1
""", (PROJ, list(MODS)))
print("== 每族资产与执行覆盖 ==")
print("  module | assets | with_run | total_runs")
for r in cur.fetchall():
    print("  ", r)

# 2) 每族最近一次 run 的状态分布（按资产取最新 run）
cur.execute("""
with latest as (
  select distinct on (r.web_test_id) r.web_test_id, r.status, r.created_at, t.module
  from web_test_runs r
  join web_tests w on w.id = r.web_test_id
  join test_cases t on t.id = w.test_case_id
  where w.project_id = %s and w.generated_by_agent = 'web_case_link_service'
    and t.module = any(%s)
  order by r.web_test_id, r.created_at desc
)
select module, status, count(*) from latest group by 1,2 order by 1,3 desc
""", (PROJ, list(MODS)))
print("\n== 每族『每资产最新一次 run』状态分布 ==")
for r in cur.fetchall():
    print("  ", r)

# 3) 三个交付资产各自最新 run
cur.execute("""
select w.identifier, t.module, r.identifier, r.status, r.created_at
from web_tests w
join test_cases t on t.id = w.test_case_id
left join lateral (
  select r.identifier, r.status, r.created_at from web_test_runs r
  where r.web_test_id = w.id order by r.created_at desc limit 1
) r on true
where w.identifier in ('WT-111DBD5A','WT-5D1D7699','WT-C39CFD02')
""")
print("\n== 三个交付资产的最新 run ==")
for r in cur.fetchall():
    print("  ", r)

# 4) 今日（10-17）新增 run 数，按族
cur.execute("""
select t.module, count(*)
from web_test_runs r
join web_tests w on w.id = r.web_test_id
join test_cases t on t.id = w.test_case_id
where w.project_id = %s and r.created_at >= '2026-10-17 00:00:00+08'
group by 1 order by 1
""", (PROJ,))
print("\n== 10-17 全天 run 数（所有 agent）==")
for r in cur.fetchall():
    print("  ", r)

c.close()
