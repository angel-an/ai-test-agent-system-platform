"""待产品确认清单取证（第二步）：把每条问题的**上下文原文**取全（只读）。

用法（在 backend/ 下）：
    ..\\.venv\\Scripts\\python.exe collect_product_questions2.py
落盘 product_confirmation_evidence2.md
"""
from __future__ import annotations

import io
import sys
from pathlib import Path

import fitz

from app.services.requirement_structure import parse_requirement_document

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

YL = Path("C:/Users/admin/WorkBuddy/2026-09-29-15-29-56/.workbuddy/tmp/yulon_md/requirement.pdf")
PR2 = Path("D:/360Downloads/小杨生煎-订单评价(问卷调查)功能 (1).pdf")

out = io.StringIO()


def p(*parts):
    print(*parts)
    out.write(" ".join(str(x) for x in parts) + "\n")


def dump_table(content: bytes, index: int, label: str) -> None:
    doc = fitz.open(stream=content, filetype="pdf")
    idx = 0
    for page_no, page in enumerate(doc, start=1):
        try:
            found = page.find_tables()
        except Exception:
            continue
        for table in getattr(found, "tables", []) or []:
            try:
                rows = table.extract()
            except Exception:
                continue
            if not rows:
                continue
            if idx == index:
                p(f"\n### {label}（table[{index}] page={page_no}）")
                for r_i, row in enumerate(rows):
                    cells = [str(c or "").replace("\n", "/") for c in row]
                    p(f"  r{r_i}: {cells}")
                return
            idx += 1
    p(f"\n### {label}: table[{index}] 未找到")


# ---------------- YL ----------------
content = YL.read_bytes()
structure = parse_requirement_document("YL-主数据管理.pdf", "application/pdf", content)
lines = structure.full_text.splitlines()
p("# YL-主数据管理.pdf 关键上下文")
p("\n## 1.2.1.2 字段說明：店鋪編號（表单侧，含提示语）")
for i in range(258, 272):
    if i < len(lines):
        p(f"  line[{i}] {lines[i].strip()!r}")
p("\n## 1.2.1.2 字段說明：營業狀態 / 營業時間")
for i in range(168, 205):
    if i < len(lines) and lines[i].strip():
        p(f"  line[{i}] {lines[i].strip()!r}")
p("\n## 1.2.3.1 導入門店：導入規則第 8/9 條与子项")
for i in range(390, 400):
    if i < len(lines) and lines[i].strip():
        p(f"  line[{i}] {lines[i].strip()!r}")
dump_table(content, 8, "1.2.2.4 篩選查詢區（店鋪）")
p("\n## 列表顯示字段（1.2.2.2）是否含 EDW更新時間")
for d in structure.display_columns:
    if d.get("table") == 5:
        p(f"  列={[c['name'] for c in d['columns']]}")

# ---------------- PR-2 ----------------
content2 = PR2.read_bytes()
structure2 = parse_requirement_document(
    "小杨生煎-订单评价(问卷调查)功能 (1).pdf", "application/pdf", content2)
lines2 = structure2.full_text.splitlines()
pages2 = 0
p("\n\n# 小杨生煎-订单评价(问卷调查)功能 (1).pdf 关键上下文")
p("\n## 正文 30min（1. 需求背景 第2条）")
for i in range(36, 46):
    if i < len(lines2):
        p(f"  line[{i}] {lines2[i].strip()!r}")
p("\n## 批注 20min（第3条：用于内部区分问卷 附近）")
for i in range(46, 60):
    if i < len(lines2):
        p(f"  line[{i}] {lines2[i].strip()!r}")
p("\n## 2.5 调查问卷-推送订阅消息：条款上下文")
for i in range(232, 245):
    if i < len(lines2):
        p(f"  line[{i}] {lines2[i].strip()!r}")

Path("product_confirmation_evidence2.md").write_text(out.getvalue(), encoding="utf-8")
print("wrote product_confirmation_evidence2.md")
