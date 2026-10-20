"""待产品确认清单的取证：把每一条候选问题的**原文出处**钉出来（只读）。

用法（在 backend/ 下）：
    ..\\.venv\\Scripts\\python.exe collect_product_questions.py
落盘 product_confirmation_evidence.md（含行号/表号/原文引用），供人工核对后再定稿。
"""
from __future__ import annotations

import io
import re
import sys
from pathlib import Path

import fitz

from app.api.v2.ai import _prd_sections
from app.services.requirement_structure import parse_requirement_document

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

DOCS = [
    {"label": "YL-主数据管理.pdf", "tag": "YL",
     "path": Path("C:/Users/admin/WorkBuddy/2026-09-29-15-29-56/.workbuddy/tmp/yulon_md/requirement.pdf")},
    {"label": "小杨生煎-订单评价(问卷调查)功能 (1).pdf", "tag": "PR2",
     "path": Path("D:/360Downloads/小杨生煎-订单评价(问卷调查)功能 (1).pdf")},
]

out = io.StringIO()


def p(*parts):
    print(*parts)
    out.write(" ".join(str(x) for x in parts) + "\n")


def table_page_map(content: bytes) -> dict[int, int]:
    doc = fitz.open(stream=content, filetype="pdf")
    mapping: dict[int, int] = {}
    index = 0
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
            if rows:
                mapping[index] = page_no
                index += 1
    return mapping


for doc in DOCS:
    content = doc["path"].read_bytes()
    structure = parse_requirement_document(doc["label"], "application/pdf", content)
    lines = structure.full_text.splitlines()
    pages = table_page_map(content)
    p("=" * 100)
    p(f"# 文档：{doc['label']}")
    p(f"行数 {len(lines)}，字段规则 {len(structure.field_rules)} 条")

    def show(probe: str, *, label: str = "", limit: int = 4, width: int = 200):
        hits = [i for i, line in enumerate(lines) if probe in line]
        p(f"\n## 检索：{label or probe!r} → {len(hits)} 处")
        for i in hits[:limit]:
            head = ""
            for j in range(i, -1, -1):
                cand = lines[j].strip()
                if cand and re.match(r"^\d+(?:[.．]\d+)*\s*[.．、]?\s+\S", cand):
                    head = cand
                    break
            p(f"  line[{i}] heading={head!r}")
            p(f"    {lines[i].strip()[:width]!r}")

    if doc["tag"] == "YL":
        show("退款失敗", label="导入条款里的「退款失敗」（导入门店章节出现退款措辞）")
        show("emoji", label="字符类型是否含 emoji")
        show("符號", label="字符类型原文（疑似被 PDF 截断）")
        show("最多支持選擇7組", label="营业时间组数上限")
        show("第一組為必填", label="导入时第一组必填")
        show("券模板", label="筛选规则列出现「券模板」（疑似从券模板文档复制）")
        show("不營業", label="营业状态筛选项「不營業」")
        show("所有啟用的品牌", label="表单侧所属品牌：仅启用")
        show("取主數據中的所有品牌", label="筛选侧所属品牌：全部品牌？")

        p("\n## 表 8（1.2.2.4 篩選查詢區）逐行原文（页码/表号）")
        p(f"  表8 所在页：{pages.get(8)}")
        for rule in structure.field_rules:
            if rule.source.get("table") == 8:
                p(f"  - {rule.name!r} | 上下文字段说明={rule.description!r} "
                  f"| heading={rule.source.get('heading')!r} line={rule.source.get('line')}")

        p("\n## 店鋪名稱/內部名稱 字段原文（字符类型）")
        for rule in structure.field_rules:
            if rule.name.startswith(("店鋪名稱", "店铺内部名称")) or rule.name.startswith("店铺内部名称"):
                p(f"  - table={rule.source.get('table')} row={rule.source.get('row')} "
                  f"{rule.name!r} desc={rule.description[:110]!r}")

        p("\n## 一級章节（用于给清单标注归属）")
        for name, _body in _prd_sections(structure.full_text):
            p(f"  - {name!r}")
    else:
        show("20", label="批注里的 20 分钟（与正文 30 分钟冲突）", limit=8, width=120)
        show("30min", label="正文 30min", limit=4, width=120)
        show("跟隨訂單拉起", label="订阅消息触发次数表述")
        show("跟随订单拉起", label="订阅消息触发次数表述（简体）")
        show("參與次數", label="新增参与次数字段")
        show("10位數值", label="问卷编码生成规则")
        for name, _body in _prd_sections(structure.full_text):
            p(f"  - 一级章节 {name!r}")

Path("product_confirmation_evidence.md").write_text(out.getvalue(), encoding="utf-8")
print("\nwrote product_confirmation_evidence.md")
