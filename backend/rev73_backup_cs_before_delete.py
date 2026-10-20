# -*- coding: utf-8 -*-
"""rev73：删除 CS Module 文件夹（abc46f5b）全部用例前的全量备份。
导出 test_cases 全字段行 + 关联统计（tags/results/run_links），落盘 JSON。
"""
import json
import psycopg2
from psycopg2.extras import RealDictCursor

FOLDER = "abc46f5b-1653-46e8-a73e-a6aad46ee39c"
OUT = r"D:/code/Pyproject/ai-test-agent-system-platform/backend/artifacts/backup_cs_module_before_delete_20260928.json"

conn = psycopg2.connect(host="localhost", port=5432, user="postgres",
                        password="postgres", dbname="langgraph_platform_db")
cur = conn.cursor(cursor_factory=RealDictCursor)

cur.execute("SELECT * FROM test_cases WHERE folder_id=%s ORDER BY created_at", (FOLDER,))
cases = [dict(r) for r in cur.fetchall()]
for c in cases:
    for k, v in c.items():
        if hasattr(v, "isoformat"):
            c[k] = v.isoformat()

case_ids = [c["id"] for c in cases]
stats = {}
for table in ("test_case_tags", "test_results", "test_run_test_cases", "test_steps",
              "test_run_artifacts", "web_tests"):
    cur.execute(
        f"SELECT count(*) AS n FROM {table} WHERE test_case_id = ANY(%s::uuid[])", (case_ids,))
    stats[table] = cur.fetchone()["n"]

backup = {
    "folder_id": FOLDER,
    "project": "PR-9",
    "case_count": len(cases),
    "related_row_counts": stats,
    "cases": cases,
}
with open(OUT, "w", encoding="utf-8") as f:
    json.dump(backup, f, ensure_ascii=False, indent=1, default=str)

print(f"[backup] {len(cases)} cases -> {OUT}")
print("[related]", json.dumps(stats, ensure_ascii=False))
