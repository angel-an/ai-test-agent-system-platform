# -*- coding: utf-8 -*-
"""rev82：离线解析 CS PDF——145 条字段规则按页归类 + 推断所属表单。
为分批生成（每批≤80）与 Task/Note/Post 子功能分发提供依据。"""
import json
import sys
from collections import defaultdict

import fitz

sys.path.insert(0, ".")
from app.services.requirement_structure import parse_requirement_document  # noqa: E402

PDF = r"D:/code/Pyproject/ai-test-agent-system-platform/backend/workspace/uploads/CS_Module_方案説明2.0.pdf"
OUT = r"D:/code/Pyproject/ai-test-agent-system-platform/backend/artifacts/rev82_rules_by_page.json"

doc = fitz.open(PDF)
sys.stdout.reconfigure(encoding="utf-8")
print("pages:", len(doc))

FORM_HINTS = [
    ("PhoneCall", ["Phone Call", "PhoneCall", "呼叫", "Call From", "Call To"]),
    ("Task", ["Task", "任務", "任务"]),
    ("Post", ["Post", "帖子", "回復", "回复", "reply"]),
    ("Note", ["Note", "備註", "备注"]),
    ("RetroClaim", ["Retro Claim", "RetroClaim", "補錄", "补录"]),
    ("GoodWill", ["GoodWill", "Goodwill", "補發", "补发"]),
    ("CaseCreate", ["New Case", "Create Case", "Case Title", "創建", "创建"]),
]

pages_info = []
all_rules = []
for pno in range(len(doc)):
    page = doc[pno]
    text = page.get_text("text") or ""
    single = fitz.open()
    single.insert_pdf(doc, from_page=pno, to_page=pno)
    content = single.tobytes()
    single.close()
    try:
        struct = parse_requirement_document(f"p{pno}.pdf", "application/pdf", content)
    except Exception as exc:
        print(f"page {pno+1}: parse fail {exc}")
        continue
    rules = struct.field_rules
    if not rules:
        continue
    # 表单推断：页文本命中关键词
    hits = []
    for form, kws in FORM_HINTS:
        if any(k in text for k in kws):
            hits.append(form)
    for r in rules:
        all_rules.append({"page": pno + 1, "name": r.name, "required": r.required,
                          "constraints": [c.kind for c in r.constraints],
                          "desc": (r.description or "")[:60],
                          "form_hint": hits})
    pages_info.append({"page": pno + 1, "rules": len(rules), "forms": hits,
                       "names": [r.name for r in rules][:12]})

dst = {"total_rules": len(all_rules), "pages": pages_info, "rules": all_rules}
with open(OUT, "w", encoding="utf-8") as f:
    json.dump(dst, f, ensure_ascii=False, indent=1)

print(f"total rules: {len(all_rules)} -> {OUT}")
for p in pages_info:
    print(f"  p{p['page']}: {p['rules']} rules forms={p['forms']} {p['names'][:8]}")
# 按表单统计
by_form = defaultdict(int)
for r in all_rules:
    key = "/".join(r["form_hint"]) or "unknown"
    by_form[key] += 1
print("by form hint:", dict(by_form))
