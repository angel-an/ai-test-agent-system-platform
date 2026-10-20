# -*- coding: utf-8 -*-
"""rev73：CS v2 需求 txt 的分析预览（只分析、不生成用例）。"""
import json
import urllib.request

BASE = "http://localhost:8000/api/v2"
PR = "PR-9"
FILE = r"D:/code/Pyproject/ai-test-agent-system-platform/backend/workspace/uploads/CS_Module_需求_功能点清单_v2.txt"
OUT = r"D:/code/Pyproject/ai-test-agent-system-platform/backend/rev73_analysis_preview.json"

boundary = "----rev73boundary"
with open(FILE, "rb") as f:
    content = f.read()

body = b"\r\n".join([
    f"--{boundary}".encode(),
    b'Content-Disposition: form-data; name="output_type"',
    b"",
    b"requirement_analysis",
    f"--{boundary}".encode(),
    'Content-Disposition: form-data; name="file"; filename="CS_Module_需求_功能点清单_v2.txt"'.encode("utf-8"),
    b"Content-Type: text/plain",
    b"",
    content,
    f"--{boundary}--".encode(),
])
req = urllib.request.Request(
    f"{BASE}/projects/{PR}/ai/generate-from-document",
    data=body, method="POST",
    headers={"Content-Type": f"multipart/form-data; boundary={boundary}"})
with urllib.request.urlopen(req, timeout=300) as resp:
    data = json.loads(resp.read().decode())

with open(OUT, "w", encoding="utf-8") as f:
    json.dump(data, f, ensure_ascii=False, indent=1)

print("success:", data.get("success"))
print("message:", data.get("message"))
rs = data.get("requirement_structure") or {}
print("structure keys:", list(rs.keys())[:10])
print("field_rules:", len(rs.get("field_rules") or []))
print("feature_points:", len(data.get("feature_points") or []))
print("can_generate:", (data.get("analysis") or {}).get("can_generate_testcases",
      (data.get("quality") or {}).get("can_generate_testcases")))
for k in ("analysis", "quality", "gate", "status"):
    if data.get(k):
        print(k, "->", json.dumps(data[k], ensure_ascii=False)[:400])
