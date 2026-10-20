# -*- coding: utf-8 -*-
"""脚本化能力复核（SUT 无关）：把三模块「人工导入」用例**整批**走生产编译路径，
检查产物脚本是否真的为每条用例注入了断言检查。

做法：POST /web-case-links/minimal-loop（create_test_run=False，不产生空运行），
      test_case_ids 传整批；再读产物脚本，核对：
        - FORM_PLAN.checks 的条数与 type 分布
        - 每个 case_id 是否在 checks 里被归属
        - 产物是否含真实断言执行器（required_error / input_validation 分支），而非骨架
产出 out_yl_case_audit/J_batch_compile.json
"""
from __future__ import annotations

import json
import re
from collections import Counter
from pathlib import Path

import httpx
import psycopg2

BASE = "http://127.0.0.1:8000/api/v2/projects/PR-9"
ART = Path(r"D:/code/Pyproject/ai-test-agent-system-platform/backend/workspace/web_cli/artifacts_backup")
OUT = Path(__file__).parent / "out_yl_case_audit"
OUT.mkdir(exist_ok=True)
TRANSPORT = httpx.HTTPTransport(retries=1, proxy=None)

SFS = {
    "e1c94163-d221-4ac8-afdc-c2103f989e5f": "WSF-YL-STORE",
    "d90c2034-0ac2-4f12-91c7-a0be5f2f2103": "WSF-YL-GOODS",
    "af320aac-6d37-4cc9-b1d7-1213aa5eced4": "WSF-YL-PAY",
}


def get_cases(sf: str) -> list[tuple[str, str]]:
    conn = psycopg2.connect(host="127.0.0.1", port=5432, user="postgres",
                            password="postgres", dbname="langgraph_platform_db")
    cur = conn.cursor()
    cur.execute("""
        select id, identifier from test_cases
        where custom_fields->>'sub_function_id' = %s
          and custom_fields->>'generated_from' = 'human_import'
        order by identifier
    """, (sf,))
    return [(str(a), b) for a, b in cur.fetchall()]


def extract_plan(script: str):
    m = re.search(r'(\{\s*"checks")', script)
    if not m:
        return None
    start = m.start(1)
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
                except Exception:  # noqa: BLE001
                    return None
    return None


def main() -> None:
    result = {}
    for sf, name in SFS.items():
        cases = get_cases(sf)
        ids = [c[0] for c in cases]
        ident_map = {c[0]: c[1] for c in cases}
        body = {
            "test_case_ids": ids, "sub_function_id": sf,
            "base_url": "https://console.uat.eatizen.com.hk/dtcloud-console-web-pc/#/login",
            "create_test_run": False, "primary_test_case_id": ids[0],
        }
        with httpx.Client(transport=TRANSPORT, timeout=300.0) as c:
            r = c.post(f"{BASE}/web-case-links/minimal-loop", json=body)
        if r.status_code != 200:
            result[name] = {"error": f"http {r.status_code}", "body": r.text[:300]}
            print(f"[{name}] FAIL {r.status_code} {r.text[:200]}")
            continue
        data = r.json()["data"]
        wts = data.get("web_tests") or []
        wt = wts[0] if wts else {}
        path = wt.get("script_path") or ""
        script = ""
        cand = ART / path
        if cand.exists():
            script = cand.read_text(encoding="utf-8", errors="replace")
        plan = extract_plan(script) or {}
        checks = plan.get("checks") or []
        by_type = Counter(c.get("type") for c in checks)
        attributed = sorted({str(c.get("case_id")) for c in checks if c.get("case_id")})
        missing = [ident_map[i] for i in ids if ident_map[i] not in attributed]
        receipt = data.get("compile_receipt") or {}
        result[name] = dict(
            requested_cases=len(ids), web_tests_returned=len(wts),
            script_chars=len(script), test_run=data.get("test_run"),
            plan_checks=len(checks), by_type=dict(by_type),
            checks_attributed_to_cases=len(attributed),
            cases_without_own_check=missing,
            has_required_executor="if (check.type === 'required_error')" in script,
            has_input_validation="input_validation" in script,
            has_real_assert=("expect(summary.blocked" in script),
            receipt=receipt,
        )
        print(f"[{name}] cases={len(ids)} wts={len(wts)} script={len(script)} "
              f"plan_checks={len(checks)} {dict(by_type)} attributed={len(attributed)} "
              f"missing={len(missing)} receipt_checks={receipt.get('form_executable_checks')}")
        if missing:
            print(f"      缺自身检查的用例({len(missing)}): {missing[:8]}")
    (OUT / "J_batch_compile.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8")
    print("\n-> J_batch_compile.json")


if __name__ == "__main__":
    main()
