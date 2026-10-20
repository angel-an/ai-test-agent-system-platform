# -*- coding: utf-8 -*-
"""rev73：上传原始需求 PDF（CS Module方案説明2.0.pdf）生成用例到 CS Module 文件夹。
等价于前端「上传需求文档 → 生成测试用例」流程（同一 generate-from-document 端点）。"""
import json
import sys
import urllib.request

BASE = "http://localhost:8000/api/v2"
PR = "PR-9"
FOLDER = "abc46f5b-1653-46e8-a73e-a6aad46ee39c"
FILE = r"D:/code/Pyproject/ai-test-agent-system-platform/backend/workspace/uploads/CS_Module_方案説明2.0.pdf"
OUT = r"D:/code/Pyproject/ai-test-agent-system-platform/backend/rev73_generate_result.json"

boundary = "----rev73boundary"
with open(FILE, "rb") as f:
    content = f.read()

body = b"\r\n".join([
    f"--{boundary}".encode(),
    b'Content-Disposition: form-data; name="folder_id"',
    b"",
    FOLDER.encode(),
    f"--{boundary}".encode(),
    b'Content-Disposition: form-data; name="test_mode"',
    b"",
    b"sit",
    f"--{boundary}".encode(),
    'Content-Disposition: form-data; name="file"; filename="CS Module方案説明2.0.pdf"'.encode("utf-8"),
    b"Content-Type: application/pdf",
    b"",
    content,
    f"--{boundary}--".encode(),
])
req = urllib.request.Request(
    f"{BASE}/projects/{PR}/ai/generate-from-document",
    data=body, method="POST",
    headers={"Content-Type": f"multipart/form-data; boundary={boundary}"})
try:
    with urllib.request.urlopen(req, timeout=900) as resp:
        data = json.loads(resp.read().decode())
except urllib.error.HTTPError as e:
    print("[HTTP]", e.code, e.read().decode()[:800])
    raise SystemExit(1)

with open(OUT, "w", encoding="utf-8") as f:
    json.dump(data, f, ensure_ascii=False, indent=1)

sys.stdout.reconfigure(encoding="utf-8")
print("success:", data.get("success"))
print("message:", data.get("message"))
cases = data.get("test_cases") or []
print("created:", len(cases))
for c in cases[:10]:
    print(" -", c.get("identifier"), (c.get("name") or "")[:60])
rs = (data.get("requirement_structure") or {})
print("field_rule_count:", rs.get("field_rule_count"), "| rebuilt_tables:", rs.get("rebuilt_table_count"))
print("compilability_warning:", (data.get("compilability_warning") or "无")[:200])
