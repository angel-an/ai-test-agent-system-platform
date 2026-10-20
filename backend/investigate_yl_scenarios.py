"""只读证据调查（第二阶段）：同名重复规则的场景归属 + 兜底用例的 PRD 位置。

用法（在 backend/ 下）：
    ..\\.venv\\Scripts\\python.exe investigate_yl_scenarios.py
"""
from __future__ import annotations

import builtins
import io
import re
from pathlib import Path

import fitz

from app.api.v2.ai import _prd_sections, _ZERO_WIDTH_RE
from app.services.requirement_structure import is_heading_line, parse_requirement_document

PDF = Path("C:/Users/admin/WorkBuddy/2026-09-29-15-29-56/.workbuddy/tmp/yulon_md/requirement.pdf")
NAME = "YL-主数据管理.pdf"

out = io.StringIO()


def p(*parts):
    builtins.print(*parts)
    out.write(" ".join(str(x) for x in parts) + "\n")


content = PDF.read_bytes()
structure = parse_requirement_document(NAME, "application/pdf", content)
lines = structure.full_text.splitlines()

# 表格 → 页码
doc = fitz.open(stream=content, filetype="pdf")
table_pages: list[tuple[int, int]] = []
for page_index, page in enumerate(doc, start=1):
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
            table_pages.append((len(table_pages), page_index))

p("===== 全文标题行（含层级）=====")
for i, raw in enumerate(lines):
    line = _ZERO_WIDTH_RE.sub("", raw).strip()
    if not line or len(line) > 60:
        continue
    if re.match(r"^\d+(?:[.．]\d+)*\s*[.．、]?\s*\S", line) and is_heading_line(line):
        p(f"  line[{i:5d}] {line!r}")

p("\n===== 目标表格所在行的文档位置 =====")
for rule_name, table in (("店鋪編號", 1), ("所屬品牌", 2), ("營業狀態", 2),
                         ("店鋪編號", 8), ("營業狀態", 8), ("所屬品牌", 8),
                         ("創建人", 17), ("更新人", 22)):
    rule = next((r for r in structure.field_rules
                 if r.name == rule_name and r.source.get("table") == table), None)
    if not rule:
        p(f"  {rule_name} table={table}: NOT FOUND")
        continue
    token = _ZERO_WIDTH_RE.sub("", rule.description or rule.name)[:14]
    line_no = next((i for i, ln in enumerate(lines)
                    if token and token in _ZERO_WIDTH_RE.sub("", ln)), None)
    heading = ""
    if line_no is not None:
        for j in range(line_no, -1, -1):
            cand = _ZERO_WIDTH_RE.sub("", lines[j]).strip()
            if cand and re.match(r"^\d+(?:[.．]\d+)*\s*[.．、]?\s*\S", cand) and is_heading_line(cand):
                heading = cand
                break
    page = next((pg for idx, pg in table_pages if idx == table), None)
    p(f"  {rule_name!r} table={table} page={page} line={line_no} nearest_heading={heading!r}")

p("\n===== 关键证据串的文档位置 =====")
probes = {
    "RQ_e853(繁體/英文導入模板)": "繁體/英文導入模板",
    "manual_supplement(特殊字段)": "特殊字段",
    "import_clause(創建新店鋪時)": "創建新店鋪時",
    "所屬品牌=品牌主數據中的所有啟用的品牌": "品牌主數據中的所有啟用的品牌",
    "所屬品牌=取主數據中的所有品牌": "取主數據中的所有品牌",
    "營業狀態=默認開啟關閉代表暫停營業": "默認開啟關閉代表暫停營業",
    "營業狀態=全部、營業、不營業": "全部、營業、不營業",
    "店鋪編號=小於等於30個字符默認為空": "默認為空",
}
for label, probe in probes.items():
    hits = [i for i, ln in enumerate(lines) if probe in _ZERO_WIDTH_RE.sub("", ln)]
    p(f"- {label}: lines={hits[:6]}")
    for i in hits[:2]:
        heading = ""
        for j in range(i, -1, -1):
            cand = _ZERO_WIDTH_RE.sub("", lines[j]).strip()
            if cand and re.match(r"^\d+(?:[.．]\d+)*\s*[.．、]?\s*\S", cand) and is_heading_line(cand):
                heading = cand
                break
        p(f"    line[{i}] heading={heading!r} text={_ZERO_WIDTH_RE.sub('', lines[i])[:110]!r}")

p("\n===== 各一级章节行区间 =====")
bounds = []
for i, raw in enumerate(lines):
    line = _ZERO_WIDTH_RE.sub("", raw).strip()
    m = re.match(r"^\s*(\d+(?:[.．]\d+)*)\s*[.．、]?\s+([^\d].{1,35})$", line)
    if m and is_heading_line(line) and re.search(
            r"管理|主數據|主数据|任务|任務|模块|模塊|中心|设置|設置|问卷|問卷|评价|評價", m.group(2)
    ) and not re.search(
            r"必填|限制|字符|可编辑|可編輯|不可|支持|允许|允許|不得|用于|用於|显示|顯示|点击|點擊|校验|校驗|初始|完成|未填|未选", m.group(2)):
        bounds.append((i, m.group(2).strip(), m.group(1).count(".")))
p(f"  raw candidates={[(i, n, d) for i, n, d in bounds]}")
sections = _prd_sections(structure.full_text)
for name, body in sections:
    body_lines = body.splitlines()
    start = next((i for i, ln in enumerate(lines) if body_lines and body_lines[0] and body_lines[0] == ln), None)
    p(f"  {name!r}: body_lines={len(body_lines)} approx_start={start}")

Path("investigate_scenarios.md").write_text(out.getvalue(), encoding="utf-8")
builtins.print("wrote investigate_scenarios.md")
