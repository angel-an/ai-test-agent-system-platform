# -*- coding: utf-8 -*-
"""rev71：TR-109（CS Module 重测）用例级结果离线回填。

背景：TR-109 执行成功（job DONE，断言 3 pass / 6 unverified / 0 blocked），但当时
persist_case_level_results 按**锚点旧需求批次**圈定用例（bug：锚点 TC-824119 挂
req-mueukebw-25k1e4，而 96 条重建用例挂 req-cs-spec-2.0-20260924），断言归属全部
落空 → 36 条全 NOT_EXECUTED。

web_test_service.persist_case_level_results 已修复（folder ∪ stdout 引用圈定）。
本脚本用**同一 mapper 逻辑**对 TR-109 既有 stdout 做回填，避免重烧一轮 UAT 执行。
"""
import json
import sys
import uuid

import psycopg2
import psycopg2.extras

sys.path.insert(0, 'backend')
from app.services.web_case_result_mapper import (  # noqa: E402
    build_identifier_to_case,
    build_rule_to_case,
    map_case_results,
    parse_compiled_assertions,
)

TR = '9a2e225a-f4e4-4252-95f5-733f5ba7f1c1'  # TR-109
WT = 'WT-4FB0C849'

conn = psycopg2.connect(host='localhost', port=5432, dbname='langgraph_platform_db',
                        user='postgres', password='postgres')
cur = conn.cursor()

cur.execute("SELECT coalesce(stdout,'') FROM test_run_script_jobs WHERE test_run_id=%s", (TR,))
stdout = cur.fetchone()[0] or ''
assert 'COMPILED_ASSERTION ' in stdout, 'TR-109 stdout 无断言行'

# 锚点与批次（与修复后的 persist 同口径：锚点 folder ∪ stdout 引用）
cur.execute("""SELECT tc.id, tc.identifier, tc.custom_fields, tc.folder_id, tc.project_id
FROM web_tests wt JOIN test_cases tc ON tc.id=wt.test_case_id WHERE wt.identifier=%s""", (WT,))
anchor_id, _, _, anchor_folder, project_id = cur.fetchone()
cur.execute("SELECT id, identifier, custom_fields FROM test_cases WHERE project_id=%s AND folder_id=%s",
            (project_id, anchor_folder))
cases = [tuple(r) for r in cur.fetchall()]
refs = {a['case_id'] for a in parse_compiled_assertions(stdout) if a['case_id']}
have = {c[1] for c in cases}
missing = refs - have
if missing:
    cur.execute("SELECT id, identifier, custom_fields FROM test_cases WHERE project_id=%s AND identifier = ANY(%s)",
                (project_id, sorted(missing)))
    known = {c[0] for c in cases}
    cases += [c for c in cur.fetchall() if c[0] not in known]
print('批次用例数:', len(cases))


class C:
    def __init__(self, t):
        self.id, self.identifier, self.custom_fields = t


objs = [C(c) for c in cases]
results = map_case_results(stdout, build_rule_to_case(objs),
                           case_ids=[str(c.id) for c in objs],
                           identifier_to_case=build_identifier_to_case(objs))

STATUS_COLUMN = {'PASSED': 'PASSED', 'SKIPPED': 'SKIPPED', 'BLOCKED': 'BLOCKED',
                 'FAILED': 'FAILED', 'NOT_EXECUTED': 'NOT_EXECUTED'}

written = {}
for case, res in zip(objs, [results[str(c.id)] for c in objs]):
    status = STATUS_COLUMN[res['status']]
    case_uuid = str(case.id)
    # upsert test_run_test_cases
    cur.execute("SELECT id FROM test_run_test_cases WHERE test_run_id=%s AND test_case_id=%s",
                (TR, case_uuid))
    row = cur.fetchone()
    if row is None:
        rtc_id = str(uuid.uuid4())
        cur.execute("""INSERT INTO test_run_test_cases (id, test_run_id, test_case_id, latest_status)
        VALUES (%s,%s,%s,%s)""", (rtc_id, TR, case_uuid, status))
    else:
        rtc_id = row[0]
        cur.execute("UPDATE test_run_test_cases SET latest_status=%s, updated_at=now() WHERE id=%s",
                    (status, rtc_id))
    # 明细：先清同 run+case 旧行（对齐 rev68 upsert 修复口径）
    cur.execute("DELETE FROM test_results WHERE test_run_id=%s AND test_case_id=%s", (TR, case_uuid))
    summary = res.get('summary') or '（无明细）'
    cur.execute("""INSERT INTO test_results
    (id, test_run_id, test_case_id, test_run_test_case_id, status, description, custom_fields)
    VALUES (%s,%s,%s,%s,%s,%s,%s)""",
                (str(uuid.uuid4()), TR, case_uuid, rtc_id, status, summary,
                 json.dumps({
                     'assertions': res.get('assertions', 0),
                     'unverified': res.get('unverified_count', 0),
                     'passed': res.get('pass_count', 0),
                     'source': 'compiled_assertions',
                 })))
    cur.execute("UPDATE test_run_test_cases SET latest_result_id=(SELECT id FROM test_results WHERE test_run_id=%s AND test_case_id=%s ORDER BY created_at DESC LIMIT 1) WHERE id=%s",
                (TR, case_uuid, rtc_id))
    # rev70c：回写 automation_status（非 NE 用例 → AUTOMATED）
    if status != 'NOT_EXECUTED':
        cur.execute("UPDATE test_cases SET automation_status='AUTOMATED', updated_at=now() WHERE id=%s AND (automation_status IS NULL OR automation_status::text<>'AUTOMATED')", (case_uuid,))
    written[status] = written.get(status, 0) + 1

# 运行计数同步
counts = {k: written.get(k, 0) for k in ['PASSED', 'FAILED', 'SKIPPED', 'BLOCKED', 'NOT_EXECUTED']}
cur.execute("""UPDATE test_runs SET test_cases_count=%s, passed_count=%s, failed_count=%s,
skipped_count=%s, blocked_count=%s, not_executed_count=%s, updated_at=now() WHERE id=%s""",
            (len(cases), counts['PASSED'], counts['FAILED'], counts['SKIPPED'],
             counts['BLOCKED'], counts['NOT_EXECUTED'], TR))
conn.commit()

print('回填完成:', written)
cur.execute("""SELECT status, count(*) FROM test_results WHERE test_run_id=%s GROUP BY 1 ORDER BY 1""", (TR,))
print('test_results 分布:', cur.fetchall())
cur.execute("SELECT run_state, test_cases_count, passed_count, skipped_count, not_executed_count FROM test_runs WHERE id=%s", (TR,))
print('运行计数:', cur.fetchone())
