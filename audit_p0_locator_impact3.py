"""只读静态分析 v3：按 check.type × field.kind 统计 472 条店铺脚本的检查结构。

关键：TR-484 的 required_error:營業時間 用了 selector `has-text("Business Hours") input`，
实际 DOM 是 checkbox ⇒ fill 必然失败。此处统计同类（required_error 且 selector 含 input）的规模。
"""
import json
import os
import sys
from collections import Counter

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
        if txt[j] == "{":
            depth += 1
        elif txt[j] == "}":
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

type_counter = Counter()
combo = Counter()
risk_scripts = set()
risk_fields = Counter()
parse_fail = 0

for ident, sp in rows:
    p = os.path.join(BACKUP, sp or "")
    if not os.path.isfile(p):
        continue
    plan = extract_plan(open(p, encoding="utf-8", errors="ignore").read())
    if not plan:
        parse_fail += 1
        continue
    fields = ((plan.get("locators") or {}).get("fields") or {})
    for ch in plan.get("checks") or []:
        t = str(ch.get("type") or "?")
        f = str(ch.get("field") or "")
        type_counter[t] += 1
        meta = fields.get(f) or {}
        fk = str(meta.get("kind") or "?")
        hint = str(meta.get("selector_hint") or "")
        combo[(t, fk)] += 1
        # 风险：对非文本控件做 required/format 这类"填值"检查，或 selector 直指 input 但 kind 非 input
        if t in {"required_error", "format", "length", "enum", "required"}:
            if fk in {"switch", "radio", "select", "checkbox"} or f == "營業時間":
                risk_scripts.add(ident)
                risk_fields[f"{f}({fk})"] += 1

print(f"店铺脚本 total={len(rows)} 解析失败={parse_fail}")
print("\n检查类型分布:")
for k, v in type_counter.most_common():
    print(f"  {k}: {v}")
print("\ncheck.type × field.kind（Top 15）:")
for (t, fk), n in combo.most_common(15):
    print(f"  {t} × {fk}: {n}")
print(f"\n存在『填值类检查 × 非文本控件』风险脚本: {len(risk_scripts)} / {len(rows)}")
for f, n in risk_fields.most_common(10):
    print(f"  {f}: {n}")
