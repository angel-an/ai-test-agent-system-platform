"""只读核查 2：脚本编译产物真实位置（artifacts_backup）与内容可用性。"""
import os
from collections import Counter

import psycopg2

BASE = r"D:/code/Pyproject/ai-test-agent-system-platform/backend/workspace"
BACKUP = os.path.join(BASE, "artifacts_backup")
PROJ = "942226d9-6071-43fa-b2bb-f7cbc93a0028"
MODS = ("店鋪主數據管理", "商品主數據", "支付方式主數據")

c = psycopg2.connect(host="127.0.0.1", port=5432, user="postgres",
                     password="postgres", dbname="langgraph_platform_db")
cur = c.cursor()
cur.execute("""
select t.module, w.identifier, w.script_path, w.test_config
from web_tests w join test_cases t on t.id = w.test_case_id
where w.project_id = %s and w.generated_by_agent = 'web_case_link_service'
  and t.module = any(%s)
""", (PROJ, list(MODS)))
rows = cur.fetchall()
c.close()

stat = Counter()
minio_ok = 0
sizes = []
samples = []
for mod, ident, sp, cfg in rows:
    p = os.path.join(BACKUP, sp)
    ex = os.path.isfile(p)
    stat[(mod, ex)] += 1
    if isinstance(cfg, dict):
        st = cfg.get("storage") or {}
        if st.get("minio"):
            minio_ok += 1
    if ex:
        sz = os.path.getsize(p)
        sizes.append(sz)
        if len(samples) < 3:
            samples.append((ident, p, sz))

print("== 编译产物落盘（artifacts_backup）==")
print("  base_exists:", os.path.isdir(BACKUP))
for (mod, ex), n in sorted(stat.items()):
    print(f"  {mod}  compiled_file={ex} -> {n}")
print("  minio_ok(test_config.storage.minio):", minio_ok, "/", len(rows))
if sizes:
    print(f"  size min/med/max: {min(sizes)} / {sorted(sizes)[len(sizes)//2]} / {max(sizes)}")
for ident, p, sz in samples:
    print("  sample:", ident, sz, "bytes", p[-60:])
