"""只读证据调查：定位同名重复字段规则、兜底模块用例的 PRD 归属证据。

用法（在 backend/ 下）：
    ..\\.venv\\Scripts\\python.exe investigate_yl_evidence.py

落盘 UTF-8 报告 investigate_report.md，不写数据库。
"""
from __future__ import annotations

import builtins
import io
import json
from pathlib import Path

import fitz

from app.api.v2.ai import _prd_sections
from app.services.requirement_structure import parse_requirement_document

PDF = Path("C:/Users/admin/WorkBuddy/2026-09-29-15-29-56/.workbuddy/tmp/yulon_md/requirement.pdf")
NAME = "YL-主数据管理.pdf"
TARGET_FIELDS = ("店鋪編號", "所屬品牌", "營業狀態", "創建人", "創建時間", "更新人", "更新時間", "營業時間")

out = io.StringIO()


def p(*parts):
    builtins.print(*parts)
    out.write(" ".join(str(x) for x in parts) + "\n")


content = PDF.read_bytes()
structure = parse_requirement_document(NAME, "application/pdf", content)

# 表格 → 页码映射（与 requirement_structure._parse_pdf 同样的枚举顺序）
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

p(f"tables={len(table_pages)}  pages={doc.page_count}")
for idx, page in table_pages:
    if idx < 12:
        p(f"  table[{idx}] -> page {page}")

p("\n===== 一级章节（_prd_sections）=====")
sections = _prd_sections(structure.full_text)
for i, (name, body) in enumerate(sections):
    p(f"[{i}] {name!r} body_len={len(body)}")
    head = [ln for ln in body.splitlines() if ln.strip()][:2]
    p(f"     head={head}")

p("\n===== 目标字段规则 =====")
for rule in structure.field_rules:
    if rule.name not in TARGET_FIELDS:
        continue
    tbl = rule.source.get("table")
    page = next((pg for idx, pg in table_pages if idx == tbl), None)
    p(
        f"- {rule.name!r} table={tbl} page={page} row={rule.source.get('row')} "
        f"rebuilt={rule.source.get('rebalanced')}"
    )
    p(f"    required={rule.required} editable={rule.editable!r} type={rule.field_type!r} cond={rule.condition!r}")
    p(f"    desc={rule.description!r}")
    p(f"    summary={rule.summary()!r}")
    p(f"    constraints={[c.to_dict() for c in rule.constraints]}")

p("\n===== display_columns（列表显示字段表）=====")
for d in structure.display_columns:
    tbl = d.get("table")
    page = next((pg for idx, pg in table_pages if idx == tbl), None)
    names = [c["name"] for c in d.get("columns", [])]
    p(f"- table={tbl} page={page} columns={names}")

p("\n===== 兜底模块用例的证据归属 =====")
p("(检查每条证据能否唯一命中某个一级章节)")
import re


def compact(value: str) -> str:
    return re.sub(r"[\s\u200b\u200c\u200d\ufeff，。；、：:,.（）()]", "", value or "")


compacted = [(n, compact(b)) for n, b in sections]

from app.api.v2.ai import _build_minimal_cases, _extract_import_clauses

cases, _gaps = _build_minimal_cases(structure.full_text, NAME, structure=structure)
stem = NAME.rsplit(".", 1)[0]
fallback = [c for c in cases if c.module == stem]
p(f"fallback cases = {len(fallback)}")

for case in fallback:
    cf = case.custom_fields or {}
    rule = cf.get("requirement_rule") or {}
    candidates = [str(cf.get("source_evidence") or ""), str(cf.get("source_point") or ""),
                  str(rule.get("field") or ""), str(rule.get("rule") or "")]
    if cf.get("manual_supplement"):
        candidates.append(str(cf.get("source_evidence") or ""))
    hits: dict[str, list[str]] = {}
    for value in candidates:
        token = compact(value)
        if len(token) < 3:
            continue
        matched = [n for n, b in compacted if token in b]
        if matched:
            hits[value[:40]] = matched
    p(f"\n* {case.name!r} rule_id={cf.get('requirement_rule_id')} flags="
      f"{[k for k in ('companion_template','filter_field_case','manual_supplement','import_clause_case') if cf.get(k)]}")
    p(f"    table={rule.get('source', {}).get('table') if rule else None} field={rule.get('field')!r}")
    p(f"    section_hits={hits if hits else 'NONE'}")

p("\n===== 導入條款（_extract_import_clauses）=====")
clauses = _extract_import_clauses(structure.full_text)
p(f"count={len(clauses)}")
for c in clauses[:15]:
    p(f"  - {c[:160]!r}")

p("\n===== 導入補充（manual_supplement）來源段落 =====")
for case in fallback:
    cf = case.custom_fields or {}
    if cf.get("manual_supplement"):
        p(json.dumps({k: v for k, v in cf.items() if k != "assertions"},
                     ensure_ascii=False, indent=2)[:2000])

Path("investigate_report.md").write_text(out.getvalue(), encoding="utf-8")
builtins.print("wrote investigate_report.md")
