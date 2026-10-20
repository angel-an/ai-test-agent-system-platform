# -*- coding: utf-8 -*-
"""rev73：删除 CS Module 文件夹全部 96 条旧用例（已备份到 artifacts/backup_cs_module_before_delete_20260928.json）。"""
import json
import sys
import urllib.request

BASE = "http://localhost:8000/api/v2"
PR = "PR-9"
FOLDER = "abc46f5b-1653-46e8-a73e-a6aad46ee39c"


def req(method, path, body=None, timeout=180):
    data = json.dumps(body).encode() if body is not None else None
    r = urllib.request.Request(BASE + path, data=data, method=method,
                               headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(r, timeout=timeout) as resp:
        raw = resp.read().decode()
        return json.loads(raw) if raw else {}


sys.stdout.reconfigure(encoding="utf-8")
ids = []
page = 1
while True:
    r = req("GET", f"/projects/{PR}/test-cases?folder_id={FOLDER}&page_size=100&p={page}")
    data = r.get("data") or []
    ids.extend(c["identifier"] for c in data)
    if not (r.get("pagination") or {}).get("next"):
        break
    page += 1
print(f"[fetch] {len(ids)} cases")
assert len(ids) == 96, f"expected 96, got {len(ids)}"

r = req("DELETE", f"/projects/{PR}/test-cases", {"test_case_ids": ids})
print("[delete]", json.dumps(r, ensure_ascii=False)[:300])

left = req("GET", f"/projects/{PR}/test-cases?folder_id={FOLDER}&page_size=10")
print("[remaining]", (left.get("pagination") or {}).get("total"))
