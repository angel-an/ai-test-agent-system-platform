"""只读静态分析：店铺脚本里 required_error 检查命中的字段与选择器形态。

用途：量化试点 TR-484 暴露的「定位器把开关/复选控件当输入框 fill」缺陷影响面。
"""
import os
import re
import sys
from collections import Counter

import psycopg2

sys.path.insert(0, r"D:/code/Pyproject/ai-test-agent-system-platform/backend")
from app.config.settings import settings  # noqa: E402

BACKUP = os.path.join(settings.web_cli_workspace_root, "artifacts_backup")
PROJ = "942226d9-6071-43fa-b2bb-f7cbc93a0028"

c = psycopg2.connect(host="127.0.0.1", port=5432, user="postgres",
                     password="postgres", dbname="langgraph_platform_db")
cur = c.cursor()
cur.execute("""
select w.identifier, w.script_path
from web_tests w join test_cases t on t.id = w.test_case_id
where w.project_id = %s and w.generated_by_agent = 'web_case_link_service'
  and t.module = '店鋪主數據管理'
""", (PROJ,))
rows = cur.fetchall()
c.close()

REQ = re.compile(r'required_error:([^"]+)"')
HOURS = "Business Hours"
hit_total = Counter()
field_freq = Counter()
n_files = 0
hours_like = 0
for ident, sp in rows:
    p = os.path.join(BACKUP, sp or "")
    if not os.path.isfile(p):
        continue
    n_files += 1
    txt = open(p, encoding="utf-8", errors="ignore").read()
    if HOURS in txt:
        hours_like += 1
    for f in REQ.findall(txt):
        field_freq[f] += 1
    # 该脚本是否包含「对 has-text("Business Hours") 的 input 做 fill」
    if re.search(r'has-text\("Business Hours"\)\s*input', txt):
        hit_total["hours_input_selector"] += 1

print(f"店铺脚本数: {n_files}")
print(f"含 'Business Hours' 文本: {hours_like} ({hours_like/max(n_files,1):.1%})")
print(f"选择器形如 has-text(\"Business Hours\") input 的脚本: {hit_total['hours_input_selector']}")
print("\nrequired_error 检查涉及的字段（Top 15）:")
for f, n in field_freq.most_common(15):
    print(f"  {f}: {n} ({n/max(n_files,1):.1%})")
