"""本轮收口「落库侧」逐条核对表：直接从新批次文件夹读取，证明修复后的真实落库状态。

用法（在 backend/ 下，需 8000 后端已启动）：
    ..\\.venv\\Scripts\\python.exe round_closeout_report.py
落盘 round_closeout_report.md
"""
from __future__ import annotations

import io
import json
import sys
from collections import Counter
from pathlib import Path

import httpx

#: 控制台是 GBK 时，报告里的箭头/勾选符号会让 print 抛 UnicodeEncodeError →
#: 报告写盘失败。统一改成 UTF-8 输出，避免"只看得到一半输出"。
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

API = "http://localhost:3000/api/v2/projects"

#: 本轮最终复验批次（由 verify_round_final.py 生成；取证只读，不再写入）
BATCHES = [
    {"label": "YL-主数据管理.pdf", "project": "PR-9",
     "folder_id": "9ec477f6-fc7c-42b3-9147-fec0342894bd",
     "stem": "YL-主数据管理",
     "previous_label": "YL-主数据管理9",
     "previous_folder_id": "1afacc2c-fe73-4c3d-aa8a-390eb0d74b97"},
    {"label": "小杨生煎-订单评价(问卷调查)功能 (1).pdf", "project": "PR-2",
     "folder_id": "85f9c7ea-35ea-4da7-a92e-ce13de9532c9",
     "stem": "小杨生煎-订单评价(问卷调查)功能 (1)",
     "previous_label": "订单评价(问卷调查)-复验3",
     "previous_folder_id": "34d550ed-afed-4796-9779-9a18bc993dba"},
]

#: 上一轮（修复前）3 组同名标题的规则 ID → 供对照（来自 audit_report_local.md 取证）
PREVIOUS_DUPLICATE_GROUPS = {
    "店鋪編號-边界值与格式校验（最长30字）": ["FR-c7e567458e", "FR-a646aae381"],
    "所屬品牌-必填与取值校验": ["FR-b9feafb81f", "FR-6f15762281"],
    "營業狀態-必填与取值校验": ["FR-d031c65879", "FR-93b955c87b"],
}

out = io.StringIO()


def p(*parts):
    print(*parts)
    out.write(" ".join(str(x) for x in parts) + "\n")


def fetch_all(client: httpx.Client, project: str, folder_id: str) -> list[dict]:
    url = f"{API}/{project}/folders/{folder_id}/test-cases"
    cases: list[dict] = []
    page = 1
    while True:
        response = client.get(url, params={"p": page, "page_size": 200})
        response.raise_for_status()
        payload = response.json()
        batch = payload.get("data") or []
        cases.extend(batch)
        total = int((payload.get("info") or {}).get("total") or len(cases))
        if not batch or len(cases) >= total:
            return cases
        page += 1


def match_counterpart(old: dict, new_cases: list[dict]) -> dict | None:
    """把上一轮的用例映射到本轮用例：先按规则 ID，再按标题，最后按标题前缀（改名后）。

    筛选字段用例的规则 ID 本轮变了（键加入表号，同名字段在不同表的规则不再互相吞掉），
    所以必须有标题/前缀兜底，否则会误判成"用例丢了"。
    """
    old_cf = old.get("custom_fields") or {}
    old_id = old_cf.get("requirement_rule_id")
    old_name = old.get("name") or ""
    if old_id:
        hit = next((c for c in new_cases
                    if (c.get("custom_fields") or {}).get("requirement_rule_id") == old_id), None)
        if hit:
            return hit
    hit = next((c for c in new_cases if (c.get("name") or "") == old_name), None)
    if hit:
        return hit
    return next((c for c in new_cases if (c.get("name") or "").startswith(old_name)), None)


def attribution_basis(case: dict) -> str:
    cf = case.get("custom_fields") or {}
    evidence = cf.get("module_evidence") or {}
    source = ((cf.get("requirement_rule") or {}).get("source") or {})
    if cf.get("module_source") == "generic_suggestion":
        return "通用建议（非PRD条款）· 不归类"
    if evidence:
        return (f"文档第{evidence.get('line')}行《{evidence.get('heading')}》"
                f"（scope={evidence.get('scope')}）")
    if source.get("heading"):
        return (f"字段规则表{source.get('table')}第{source.get('row')}行 → "
                f"{source.get('heading')}")
    return cf.get("module_note") or "—"


with httpx.Client(timeout=180) as client:
    for batch in BATCHES:
        cases = fetch_all(client, batch["project"], batch["folder_id"])
        previous = fetch_all(client, batch["project"], batch["previous_folder_id"])
        previous_fallback = [c for c in previous if (c.get("module") or "") == batch["stem"]]
        p("=" * 100)
        p(f"# 批次：{batch['label']}  项目 {batch['project']}")
        p(f"上一轮批次：{batch['previous_label']}（用例 {len(previous)} 条，其中兜底模块 "
          f"{len(previous_fallback)} 条）")
        p(f"本轮批次：{batch['folder_id']}（用例 {len(cases)} 条）")

        # 1) 同名标题核对
        titles = Counter(c["name"] for c in cases)
        dup = {t: n for t, n in titles.items() if n > 1}
        p(f"\n## 1. 同名标题：{len(dup)} 组重复")
        for title, count in dup.items():
            p(f"  * {title!r} x{count}")
        p(f"  本轮兜底模块（{batch['stem']}）落库条数："
          f"{sum(1 for c in cases if (c.get('module') or '') == batch['stem'])}")

        # 2) 上一轮兜底模块用例的最终归属（逐条）
        p(f"\n## 2. 上一轮兜底模块 {len(previous_fallback)} 条用例的最终归属")
        p("| 上一轮用例 | 本轮最终模块 | 归属依据 | 上一轮规则ID → 本轮规则ID |")
        p("| --- | --- | --- | --- |")
        unmatched = []
        for old in previous_fallback:
            old_cf = old.get("custom_fields") or {}
            old_id = old_cf.get("requirement_rule_id") or "（无）"
            new = match_counterpart(old, cases)
            if new is None:
                unmatched.append(old["name"])
                p(f"| {old['name'][:40]} | **未找到** | — | {old_id} |")
                continue
            new_cf = new.get("custom_fields") or {}
            p(f"| {old['name'][:40]} | {new['module']} | {attribution_basis(new)[:60]} | "
              f"{old_id} → {new_cf.get('requirement_rule_id') or '（无）'} |")
        if unmatched:
            p(f"\n  ⚠ 未找到对应用例：{unmatched}")

        # 3) 待确认项
        p("\n## 3. 待确认项分类")
        for case in cases:
            cf = case.get("custom_fields") or {}
            if cf.get("confirmation_status") == "pending":
                p(f"  * [{cf.get('ambiguity_kind')}] {case['name']}")
                p(f"      理由：{cf.get('ambiguity_reason')}")
                p(f"      执行轨道：{cf.get('executable_track')}；断言数：{len(cf.get('assertions') or [])}")

        # 3b) 已确认项（产品回填）：结论照录，用例已按结论给出可判定预期
        p("\n## 3b. 已确认项（产品回填结论）")
        confirmed = [c for c in cases
                     if (c.get("custom_fields") or {}).get("confirmation_status") == "confirmed"]
        if not confirmed:
            p("  （无）")
        for case in confirmed:
            cf = case.get("custom_fields") or {}
            p(f"  * {case['name']}")
            p(f"      结论（原文照录）：{cf.get('confirmed_conclusion')}")
            p(f"      确认人/时间：{cf.get('confirmed_by')} / {cf.get('confirmed_at')}"
              f"　来源：{cf.get('confirmation_source')}")
            p(f"      登记键：{cf.get('confirmation_key')}；标签：{'、'.join(case.get('tags') or [])}")
            steps = case.get("test_case_steps") or []
            if steps:
                p(f"      可判定预期（末步）：{steps[-1].get('result')}")
            if cf.get("confirmation_open_questions"):
                p(f"      仍待明确：{cf['confirmation_open_questions']}")

        # 4) 汇总
        p("\n## 4. 落库汇总")
        p(f"  - 模块分布：{json.dumps(dict(Counter(c['module'] for c in cases)), ensure_ascii=False)}")
        p(f"  - 标签分布：{json.dumps(dict(Counter(t for c in cases for t in (c.get('tags') or []))), ensure_ascii=False)}")
        p(f"  - 规则 ID 前缀：{json.dumps(dict(Counter(((c.get('custom_fields') or {}).get('requirement_rule_id') or 'NONE').split('-')[0] for c in cases)), ensure_ascii=False)}")
        p(f"  - 无模块/无标签：{[c['name'] for c in cases if not c.get('module') or not (c.get('tags') or [])]}")
        p(f"  - 无步骤：{[c['name'] for c in cases if not (c.get('test_case_steps') or [])]}")
        p(f"  - 有断言的用例数：{sum(1 for c in cases if (c.get('custom_fields') or {}).get('assertions'))}")
        p(f"  - 确认状态分布：{json.dumps(dict(Counter((c.get('custom_fields') or {}).get('confirmation_status') or 'none' for c in cases)), ensure_ascii=False)}")
        p(f"  - 执行轨道分布：{json.dumps(dict(Counter((c.get('custom_fields') or {}).get('executable_track') or 'none' for c in cases)), ensure_ascii=False)}")

        # 5) 上一轮同名标题的规则是否都还在（追踪链未断）
        if batch["project"] == "PR-9":
            p("\n## 5. 上一轮 3 组同名标题的规则 ID 追踪（修复后必须仍存在）")
            for title, ids in PREVIOUS_DUPLICATE_GROUPS.items():
                for rule_id in ids:
                    hit = next((c for c in cases
                                if (c.get("custom_fields") or {}).get("requirement_rule_id") == rule_id), None)
                    mark = "✓" if hit else "✗"
                    p(f"  {mark} {rule_id}（{title}）→ {hit['name'] if hit else '缺失'}")

Path("round_closeout_report.md").write_text(out.getvalue(), encoding="utf-8")
print("\nwrote round_closeout_report.md")
