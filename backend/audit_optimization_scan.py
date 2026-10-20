"""只读体检：量化当前生成链路的覆盖缺口与可优化点（不改任何数据）。

用法（在 backend/ 下）：
    ..\\.venv\\Scripts\\python.exe audit_optimization_scan.py
落盘 audit_optimization_scan.md
"""
from __future__ import annotations

import io
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path

from app.api.v2.ai import _build_minimal_cases, _is_filter_field_rule
from app.services.requirement_structure import extract_constraints, parse_requirement_document

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

DOCS = [
    {"label": "YL-主数据管理.pdf",
     "path": Path("C:/Users/admin/WorkBuddy/2026-09-29-15-29-56/.workbuddy/tmp/yulon_md/requirement.pdf")},
    {"label": "小杨生煎-订单评价(问卷调查)功能 (1).pdf",
     "path": Path("D:/360Downloads/小杨生煎-订单评价(问卷调查)功能 (1).pdf")},
]

out = io.StringIO()


def p(*parts):
    print(*parts)
    out.write(" ".join(str(x) for x in parts) + "\n")


for doc in DOCS:
    content = doc["path"].read_bytes()
    structure = parse_requirement_document(doc["label"], "application/pdf", content)
    cases, gaps = _build_minimal_cases(structure.full_text, doc["label"], structure=structure)
    p("=" * 100)
    p(f"# {doc['label']}")
    p(f"字段规则 {len(structure.field_rules)} 条 / 用例 {len(cases)} 条 / 断言缺口 {len(gaps)} 个需求点")

    # ---- 1) 字段规则覆盖缺口：哪些规则一条用例都没产出 ----
    used_tables: Counter = Counter()
    used_pairs: Counter = Counter()
    for case in cases:
        cf = case.custom_fields or {}
        src = ((cf.get("requirement_rule") or {}).get("source") or {})
        if src.get("table") is not None:
            used_tables[src["table"]] += 1
            used_pairs[(src.get("table"), src.get("row"))] += 1
    uncovered = []
    for rule in structure.field_rules:
        table, row = rule.source.get("table"), rule.source.get("row")
        hit = used_pairs.get((table, row), 0)
        if not hit:
            # 行号可能被跨页合并改写 → 再用字段名兜一次（同名同表视为已覆盖）
            hit = sum(1 for case in cases
                      if ((case.custom_fields or {}).get("requirement_rule") or {}).get("field")
                      == rule.name)
        if not hit:
            uncovered.append(rule)
    p(f"\n## 1. 零覆盖字段规则（没有任何用例引用该行/该字段）：{len(uncovered)} 条")
    p(f"   （该表用例数：{dict(used_tables)}）")
    for rule in uncovered:
        p(f"  - table={rule.source.get('table')} row={rule.source.get('row')} {rule.name!r}")
        p(f"      desc={rule.description[:90]!r}")
        p(f"      constraints={[c.kind for c in rule.constraints]} "
          f"required={rule.required} filter={_is_filter_field_rule(rule)}"
          f" heading={rule.source.get('heading')!r}")

    # ---- 2) 断言覆盖（按规则前缀）----
    by_prefix = defaultdict(lambda: [0, 0])
    for case in cases:
        cf = case.custom_fields or {}
        prefix = (cf.get("requirement_rule_id") or "NONE").split("-")[0]
        by_prefix[prefix][0] += 1
        if cf.get("assertions"):
            by_prefix[prefix][1] += 1
    p("\n## 2. 断言覆盖（有断言 / 总数）")
    for prefix, (total, with_assert) in sorted(by_prefix.items()):
        p(f"  - {prefix:9s} {with_assert:3d}/{total:3d}  ({with_assert * 100 // max(total, 1)}%)")

    # ---- 3) 文本里的"数量类"约束识别情况 ----
    probes = ["最多支持選擇7組，", "最多選擇30天", "最多输入20个字", "最多設置10道",
              "最多支持選擇", "限制最多设置10道", "最大長度50"]
    p("\n## 3. 数量/范围类表述的约束识别")
    for probe in probes:
        if probe not in structure.full_text:
            continue
        got = [c.kind for c in extract_constraints(probe)]
        p(f"  - {probe!r} → {got or '未识别为任何约束'}")

    # ---- 4) 模块/脚本文案一致性 ----
    modules = Counter(c.module for c in cases)
    p("\n## 4. 模块分布")
    for name, count in modules.most_common():
        p(f"  - {count:4d}  {name}")
    p(f"  模块总数={len(modules)}；含简化字模块名={[m for m in modules if m == '通用建议（非PRD条款）']}")

    # ---- 5) 无断言的需求点（可改进空间）----
    p(f"\n## 5. 未提取到可判定断言的需求点（{len(gaps)} 个）")
    for gap in gaps[:6]:
        p(f"  - {gap['point'][:70]!r} → 用例 {len(gap['titles'])} 条")

    # ---- 6) 来源追溯字段齐备度 ----
    have_line = sum(1 for r in structure.field_rules if r.source.get("line") is not None)
    have_section = sum(1 for r in structure.field_rules if r.source.get("section"))
    p(f"\n## 6. 溯源字段齐备度：line {have_line}/{len(structure.field_rules)}，"
      f"section {have_section}/{len(structure.field_rules)}")
    p(f"  用例侧 source_location 覆盖率：{sum(1 for c in cases if c.source_location)}/{len(cases)}")

Path("audit_optimization_scan.md").write_text(out.getvalue(), encoding="utf-8")
print("\nwrote audit_optimization_scan.md")
