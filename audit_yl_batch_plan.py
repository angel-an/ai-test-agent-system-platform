# -*- coding: utf-8 -*-
"""解析批量编译产物里的 FORM_PLAN，核对「人工导入用例 → 检查注入」的对应关系。

修正点：FORM_PLAN 的 JSON 里 `checks` 不在首位（顺序是 locators → ... → checks），
       必须从 `const FORM_PLAN = ` 起做括号配平解析。
产出 out_yl_case_audit/J2_batch_plan.json
"""
from __future__ import annotations

import json
import re
from collections import Counter, defaultdict
from pathlib import Path

OUT = Path(__file__).parent / "out_yl_case_audit"
ART = Path(r"D:/code/Pyproject/ai-test-agent-system-platform/backend/workspace/web_cli/artifacts_backup")

# batch 产物目录（按首个 case uuid）
BATCHES = {
    "WSF-YL-STORE": "dd406891-f11f-4964-b14f-6b50cfca384a",
    "WSF-YL-GOODS": "2169859f-9b88-4aad-a745-2d23d796194d",
    "WSF-YL-PAY": "9fe11aea-61ef-4968-b9a5-8a5100493f4f",
}


def extract_form_plan(script: str):
    m = re.search(r"const FORM_PLAN\s*=\s*\{", script)
    if not m:
        return None
    start = script.index("{", m.start())
    depth = 0
    in_str = False
    esc = False
    for i in range(start, len(script)):
        ch = script[i]
        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                try:
                    return json.loads(script[start:i + 1])
                except Exception as e:  # noqa: BLE001
                    print("  parse fail:", e)
                    return None
    return None


def find_script(batch_dir: str) -> Path | None:
    root = ART / "web-tests/PR-9/minimal-loop" / batch_dir
    hits = list(root.rglob("test-script.spec.ts"))
    return hits[0] if hits else None


def main() -> None:
    import psycopg2
    conn = psycopg2.connect(host="127.0.0.1", port=5432, user="postgres",
                            password="postgres", dbname="langgraph_platform_db")
    cur = conn.cursor()
    out = {}
    for name, d in BATCHES.items():
        f = find_script(d)
        if not f:
            out[name] = {"error": "artifact not found"}
            continue
        script = f.read_text(encoding="utf-8", errors="replace")
        plan = extract_form_plan(script)
        if not plan:
            out[name] = {"error": "FORM_PLAN parse failed", "file": str(f)}
            continue
        checks = plan.get("checks") or []
        by_type = Counter(c.get("type") for c in checks)
        by_owner = defaultdict(list)
        for c in checks:
            own = c.get("case_id") or ""
            if not own:
                for o in (c.get("owners") or []):
                    if isinstance(o, dict) and o.get("case_id"):
                        own = o["case_id"]
            by_owner[own or "(无归属)"].append(c.get("type"))
        # 该子功能的人工导入用例
        cur.execute("""
            select identifier from test_cases
            where custom_fields->>'sub_function_id' = (
                select id::text from web_sub_functions where identifier = %s limit 1)
              and custom_fields->>'generated_from' = 'human_import'
            order by identifier
        """, (name,))
        idents = [r[0] for r in cur.fetchall()]
        covered = [i for i in idents if i in by_owner]
        out[name] = dict(
            artifact=str(f).split("artifacts_backup")[-1],
            script_chars=len(script),
            plan_keys=sorted(plan.keys()),
            plan_checks=len(checks),
            by_type=dict(by_type),
            locators_name=(plan.get("locators") or {}).get("name"),
            skipped_count=plan.get("skipped_count"),
            checks_per_case=dict(Counter(len(v) for k, v in by_owner.items() if k != "(无归属)")),
            unowned_checks=len(by_owner.get("(无归属)", [])),
            imported_cases=len(idents),
            imported_cases_with_own_check=len(covered),
            imported_cases_without_check=[i for i in idents if i not in by_owner],
        )
        print(f"== {name}")
        for k, v in out[name].items():
            print(f"   {k} = {v}")
    (OUT / "J2_batch_plan.json").write_text(json.dumps(out, ensure_ascii=False, indent=1),
                                            encoding="utf-8")
    print("\n-> J2_batch_plan.json")


if __name__ == "__main__":
    main()
