# -*- coding: utf-8 -*-
"""2026-09-26 错误数据清理：调试重复轮 + REJECTED/空轮（保留 TR-94/TR-101 等有效运行）
流程：备份 JSON -> 删子表 -> 删主表 -> 校验
"""
import json
import os
from datetime import datetime

import psycopg2
from psycopg2.extras import RealDictCursor

DSN = dict(host="localhost", port=5432, dbname="langgraph_platform_db",
           user="postgres", password="postgres")

# 待删：TR-95~100（rev69 调试重复轮，数据与 TR-101 完全一致）+ REJECTED/空轮
TARGETS = ["TR-83", "TR-85", "TR-89", "TR-90", "TR-91", "TR-92", "TR-93",
           "TR-95", "TR-96", "TR-97", "TR-98", "TR-99", "TR-100"]
# 必须保留的运行（删前校验）
KEEP = ["TR-94", "TR-101", "TR-84", "TR-86", "TR-87", "TR-88"]

CHILD_TABLES = [  # (表名, run引用列)
    ("test_run_test_cases", "test_run_id"),
    ("test_results", "test_run_id"),
    ("web_test_results", "test_run_id"),
    ("test_run_artifacts", "run_id"),
    ("test_run_script_jobs", "test_run_id"),
]

BACKUP_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                          "cleanup_backup_20260926")


def json_default(o):
    if isinstance(o, datetime):
        return o.isoformat()
    if isinstance(o, (bytes, memoryview)):
        return "<binary>"
    raise TypeError(repr(o))


def main():
    os.makedirs(BACKUP_DIR, exist_ok=True)
    conn = psycopg2.connect(**DSN)
    cur = conn.cursor(cursor_factory=RealDictCursor)

    cur.execute("SELECT identifier, id FROM test_runs WHERE identifier = ANY(%s)",
                (TARGETS,))
    rows = {r["identifier"]: r["id"] for r in cur.fetchall()}
    missing = set(TARGETS) - set(rows)
    if missing:
        print("[warn] 未找到，跳过:", missing)
    ids = [str(v) for v in rows.values()]
    if not ids:
        print("无可删数据，退出")
        return

    # ---- 1. 备份 ----
    backup = {"deleted_at": datetime.now().isoformat(),
              "runs": {}, "children": {}}
    cur.execute("SELECT * FROM test_runs WHERE id::text = ANY(%s)", (ids,))
    backup["runs"]["test_runs"] = cur.fetchall()
    n_total = 0
    for tbl, col in CHILD_TABLES:
        cur.execute(f"SELECT * FROM {tbl} WHERE {col}::text = ANY(%s)", (ids,))
        data = cur.fetchall()
        backup["children"][tbl] = data
        n_total += len(data)
    backup_path = os.path.join(BACKUP_DIR, "deleted_runs_backup.json")
    with open(backup_path, "w", encoding="utf-8") as f:
        json.dump(backup, f, ensure_ascii=False, default=json_default, indent=1)
    print(f"[1/4] 备份完成: {backup_path} (runs={len(backup['runs']['test_runs'])}, 子表行={n_total})")

    # ---- 2. 删子表 ----
    for tbl, col in CHILD_TABLES:
        cur.execute(f"DELETE FROM {tbl} WHERE {col}::text = ANY(%s)", (ids,))
        print(f"[2/4] DELETE {tbl}: {cur.rowcount} 行")
    # ---- 3. 删主表 ----
    cur.execute("DELETE FROM test_runs WHERE id::text = ANY(%s)", (ids,))
    print(f"[3/4] DELETE test_runs: {cur.rowcount} 行")
    conn.commit()

    # ---- 4. 校验 ----
    cur.execute("SELECT identifier FROM test_runs WHERE identifier = ANY(%s)", (KEEP,))
    kept = sorted(r["identifier"] for r in cur.fetchall())
    assert set(kept) == set(KEEP), f"保留运行缺失! 实际: {kept}"
    cur.execute("SELECT count(*) c FROM test_runs WHERE identifier = ANY(%s)", (TARGETS,))
    assert cur.fetchone()["c"] == 0, "仍有残留运行!"
    print(f"[4/4] 校验通过: {KEEP} 全部保留，目标运行已清空")


if __name__ == "__main__":
    main()
