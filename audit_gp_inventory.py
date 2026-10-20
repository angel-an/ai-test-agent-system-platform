# -*- coding: utf-8 -*-
"""只读盘点：商品 / 支付方式两模块的用例来源、脚本资产、执行完成度。"""
from __future__ import annotations
import psycopg2

PROJ = "942226d9-6071-43fa-b2bb-f7cbc93a0028"
DSN = dict(host="127.0.0.1", port=5432, user="postgres", password="postgres",
           dbname="langgraph_platform_db")
c = psycopg2.connect(**DSN); cur = c.cursor()

MODS = ("商品主數據", "支付方式主數據", "YL-主數據管理-商品", "YL-主數據管理-支付方式")

print("== A. 两模块 module 名下的用例来源分布 ==")
cur.execute("""
select t.module,
       coalesce(t.custom_fields->>'content_source', t.custom_fields->>'generated_from',
                t.case_kind, '(null)') src,
       count(*) cases,
       count(distinct w.test_case_id) with_asset
from test_cases t
left join web_tests w on w.test_case_id = t.id and w.project_id = %s
where t.project_id = %s and t.module = any(%s)
group by 1,2 order by 1,2
""", (PROJ, PROJ, list(MODS)))
for r in cur.fetchall():
    print("  ", r)

print("\n== B. 资产断言数分布（compiled assertion_count）==")
cur.execute("""
select t.module,
       count(*) assets,
       sum(case when coalesce((w.generation_params->'compiled_cases'->>'assertion_count')::int,0) >= 1 then 1 else 0 end) ac_ge1,
       sum(case when coalesce((w.generation_params->'compiled_cases'->>'assertion_count')::int,0) >= 3 then 1 else 0 end) ac_ge3,
       sum(case when coalesce((w.generation_params->'compiled_cases'->>'assertion_count')::int,0) = 0 then 1 else 0 end) ac_0
from web_tests w join test_cases t on t.id = w.test_case_id
where w.project_id = %s and w.generated_by_agent = 'web_case_link_service' and t.module = any(%s)
group by 1 order by 1
""", (PROJ, list(MODS)))
for r in cur.fetchall():
    print("  module=%s assets=%s ac>=1=%s ac>=3=%s ac=0=%s" % r)

print("\n== C. 每资产最近一次 run 的结论 ==")
cur.execute("""
with latest as (
  select distinct on (r.web_test_id) r.web_test_id, r.status, r.total_tests, r.passed_tests,
         r.failed_tests, r.created_at, t.module
  from web_test_runs r join web_tests w on w.id = r.web_test_id
  join test_cases t on t.id = w.test_case_id
  where w.project_id = %s and w.generated_by_agent = 'web_case_link_service' and t.module = any(%s)
  order by r.web_test_id, r.created_at desc
)
select module, status, count(*), sum(coalesce(passed_tests,0)), sum(coalesce(failed_tests,0))
from latest group by 1,2 order by 1,2
""", (PROJ, list(MODS)))
for r in cur.fetchall():
    print("  ", r)

print("\n== D. 未执行过任何 run 的资产（按模块）==")
cur.execute("""
select t.module, count(*) never_run
from web_tests w join test_cases t on t.id = w.test_case_id
where w.project_id = %s and w.generated_by_agent = 'web_case_link_service' and t.module = any(%s)
  and not exists (select 1 from web_test_runs r where r.web_test_id = w.id)
group by 1 order by 1
""", (PROJ, list(MODS)))
for r in cur.fetchall():
    print("  ", r)

print("\n== E. 子功能绑定情况 ==")
cur.execute("""
select s.identifier, s.name, count(w.id) assets
from web_sub_functions s left join web_tests w on w.web_sub_function_id = s.id
where s.project_id = %s group by 1,2 order by 3 desc limit 8
""", (PROJ,))
for r in cur.fetchall():
    print("  ", r)
c.close()
