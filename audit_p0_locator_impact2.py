"""只读静态分析 v2：解析 472 条店铺脚本内嵌的 FORM_PLAN，量化'非文本控件被当输入框 fill'的风险面。

TR-484 实测：required_error:營業時間 对 checkbox 调 fill → blocked。
本脚本判断这类风险在 472 条脚本中的分布：检查项按字段的控件类型（input/radio/switch/checkbox）统计。
"""
import json
import os
import sys
from collections import Counter, defaultdict

import psycopg2

sys.path.insert(0, r"D:/code/Pyproject/ai-test-agent-system-platform/backend")
from app.config.settings import settings  # noqa: E402

BACKUP = os.path.join(settings.web_cli_workspace_root, "artifacts_backup")
PROJ = "942226d9-6071-43fa-b2bb-f7cbc93a0028"


def extract_plan(txt: str) -> dict | None:
    key = "const FORM_PLAN = "
    i = txt.find(key)
    if i < 0:
        return None
    start = i + len(key)
    depth = 0
    for j in range(start, len(txt)):
        ch = txt[j]
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                try:
                    return json.loads(txt[start:j + 1])
                except Exception:
                    return None
    return None


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

field_kind = Counter()
check_kind = Counter()
risky_scripts = set()
risky_fields = Counter()
n_parsed = 0
n_checks_per_script = []

for ident, sp in rows:
    p = os.path.join(BACKUP, sp or "")
    if not os.path.isfile(p):
        continue
    plan = extract_plan(open(p, encoding="utf-8", errors="ignore").read())
    if not plan:
        continue
    n_parsed += 1
    loc = plan.get("locators") or {}
    fields = loc.get("fields") or {}
    for name, meta in fields.items():
        field_kind[str((meta or {}).get("kind"))] += 1
    checks = plan.get("checks") or []
    n_checks_per_script.append(len(checks))
    for ch in checks:
        kind = str(ch.get("kind") or "")
        check_kind[kind] += 1
        fname = str(ch.get("field") or "")
        meta = fields.get(fname) or {}
        ckind = str(meta.get("kind") or "")
        # 检查类型为 required/format 等文本类，但字段是 radio/switch/checkbox ⇒ 有 fill 风险
        if kind in {"required", "format", "length", "enum", "pattern"} and ckind in {
            "radio", "switch", "checkbox", "select", "toggle"
        }:
            risky_scripts.add(ident)
            risky_fields[fname] += 1

print(f"店铺脚本 total={len(rows)}  成功解析 FORM_PLAN={n_parsed}")
if n_checks_per_script:
    s = sorted(n_checks_per_script)
    print(f"每脚本检查项 min/med/max = {s[0]} / {s[len(s)//2]} / {s[-1]}")
print("\n字段控件类型分布（按脚本计次）:")
for k, v in field_kind.most_common():
    print(f"  {k}: {v}")
print("\n检查类型分布:")
for k, v in check_kind.most_common():
    print(f"  {k}: {v}")
print(f"\n存在『文本类检查 × 非文本控件』风险的脚本: {len(risky_scripts)} / {n_parsed}")
for f, n in risky_fields.most_common(10):
    print(f"  风险字段 {f}: {n} 脚本")
