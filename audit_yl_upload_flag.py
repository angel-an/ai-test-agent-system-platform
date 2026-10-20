# -*- coding: utf-8 -*-
"""验证：4 条「零检查」的店铺人工导入用例，开启 allow_upload_checks 后能否产出检查。
结论指向「参数开关」还是「能力缺失」。
"""
import json
import re
from collections import Counter
from pathlib import Path

import httpx
import psycopg2

BASE = "http://127.0.0.1:8000/api/v2/projects/PR-9"
ART = Path(r"D:/code/Pyproject/ai-test-agent-system-platform/backend/workspace/web_cli/artifacts_backup")
SF_STORE = "e1c94163-d221-4ac8-afdc-c2103f989e5f"
TARGETS = ["TC-183413", "TC-422506", "TC-779696", "TC-793688"]
T = httpx.HTTPTransport(retries=1, proxy=None)


def parse_plan(s: str):
    m = re.search(r"const FORM_PLAN\s*=\s*\{", s)
    if not m:
        return None
    st = s.index("{", m.start())
    depth = 0
    ins = False
    esc = False
    for i in range(st, len(s)):
        ch = s[i]
        if ins:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                ins = False
            continue
        if ch == '"':
            ins = True
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return json.loads(s[st:i + 1])
    return None


def run(flag: bool):
    conn = psycopg2.connect(host="127.0.0.1", port=5432, user="postgres",
                            password="postgres", dbname="langgraph_platform_db")
    cur = conn.cursor()
    cur.execute("select id::text, identifier from test_cases where identifier = any(%s) order by identifier",
                (TARGETS,))
    rows = cur.fetchall()
    ids = [r[0] for r in rows]
    ident = {r[0]: r[1] for r in rows}
    body = {
        "test_case_ids": ids, "sub_function_id": SF_STORE,
        "base_url": "https://console.uat.eatizen.com.hk/dtcloud-console-web-pc/#/login",
        "create_test_run": False, "primary_test_case_id": ids[0],
        "allow_upload_checks": flag,
    }
    with httpx.Client(transport=T, timeout=300) as cl:
        r = cl.post(f"{BASE}/web-case-links/minimal-loop", json=body)
    if r.status_code != 200:
        print(f"allow_upload_checks={flag} -> http {r.status_code} {r.text[:200]}")
        return
    d = r.json()["data"]
    rec = d.get("compile_receipt") or {}
    wt = (d.get("web_tests") or [{}])[0]
    f = ART / (wt.get("script_path") or "")
    plan = parse_plan(f.read_text(encoding="utf-8", errors="replace")) if f.exists() else None
    checks = (plan or {}).get("checks") or []
    owned = sorted({c.get("case_id") for c in checks if c.get("case_id")})
    print(f"--- allow_upload_checks={flag}")
    print(f"    receipt: executable={rec.get('executable')} form_executable_checks={rec.get('form_executable_checks')} "
          f"assertion_check_count={rec.get('assertion_check_count')} skipped_assertion_count={rec.get('skipped_assertion_count')}")
    print(f"    plan_checks={len(checks)} by_type={dict(Counter(c.get('type') for c in checks))}")
    print(f"    skipped_assertions={json.dumps(rec.get('skipped_assertions'), ensure_ascii=False)[:300]}")
    print(f"    归属用例={[ident.get(x, x) for x in owned]}")
    print(f"    无检查的用例={[ident[i] for i in ids if i not in owned]}")


if __name__ == "__main__":
    run(False)
    run(True)
