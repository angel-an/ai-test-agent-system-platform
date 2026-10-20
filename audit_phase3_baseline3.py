"""只读核查 3：577 条已归集资产的编译产物质量（断言强度分桶）。

用途：区分"已归集/已编译"与"编译产物是否具备业务断言"，为后续计划提供实测基线。
"""
import os
import re
import sys
from collections import Counter, defaultdict

import psycopg2

sys.path.insert(0, r"D:/code/Pyproject/ai-test-agent-system-platform/backend")
from app.config.settings import settings  # noqa: E402

BACKUP = os.path.join(settings.web_cli_workspace_root, "artifacts_backup")
PROJ = "942226d9-6071-43fa-b2bb-f7cbc93a0028"
MODS = ("店鋪主數據管理", "商品主數據", "支付方式主數據")

c = psycopg2.connect(host="127.0.0.1", port=5432, user="postgres",
                     password="postgres", dbname="langgraph_platform_db")
cur = c.cursor()
cur.execute("""
select t.module, w.identifier, w.script_path, w.test_config->>'web_template_key'
from web_tests w join test_cases t on t.id = w.test_case_id
where w.project_id = %s and w.generated_by_agent = 'web_case_link_service'
  and t.module = any(%s)
""", (PROJ, list(MODS)))
rows = cur.fetchall()
c.close()

buckets = defaultdict(Counter)
byte_stat = defaultdict(list)
zero_samples = {}
for mod, ident, sp, tpl in rows:
    p = os.path.join(BACKUP, sp or "")
    if not os.path.isfile(p):
        buckets[mod]["file_missing"] += 1
        continue
    txt = open(p, encoding="utf-8", errors="ignore").read()
    n = txt.count("COMPILED_ASSERTION ")
    buckets[mod]["assert_0" if n == 0 else ("assert_1_2" if n <= 2 else "assert_3plus")] += 1
    byte_stat[mod].append(len(txt))
    if n == 0 and mod not in zero_samples:
        zero_samples[mod] = (ident, len(txt))

print("== 编译产物断言强度（按模块）==")
for mod in MODS:
    b = buckets[mod]
    tot = sum(b.values())
    print(f"  {mod}  total={tot}  assert0={b['assert_0']}  assert1-2={b['assert_1_2']}  "
          f"assert3+={b['assert_3plus']}  missing={b['file_missing']}")
    if byte_stat[mod]:
        sz = sorted(byte_stat[mod])
        print(f"    bytes min/med/max = {sz[0]} / {sz[len(sz)//2]} / {sz[-1]}")
    if mod in zero_samples:
        print(f"    零断言样例: {zero_samples[mod]}")
