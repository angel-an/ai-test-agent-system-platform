# -*- coding: utf-8 -*-
"""rev73：对知识库原始 PDF（CS Module方案説明2.0.pdf）跑分析预览（只分析、不生成）。"""
import json
import sys
import urllib.request

BASE = "http://localhost:8000/api/v2"
PR = "PR-9"
FILE = r"D:/code/Pyproject/ai-test-agent-system-platform/backend/workspace/uploads/CS_Module_方案説明2.0.pdf"
OUT = r"D:/code/Pyproject/ai-test-agent-system-platform/backend/rev73_analysis_pdf.json"

boundary = "----rev73boundary"
with open(FILE, "rb") as f:
    content = f.read()

body = b"\r\n".join([
    f"--{boundary}".encode(),
    b'Content-Disposition: form-data; name="output_type"',
    b"",
    b"requirement_analysis",
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
with urllib.request.urlopen(req, timeout=600) as resp:
    data = json.loads(resp.read().decode())

with open(OUT, "w", encoding="utf-8") as f:
    json.dump(data, f, ensure_ascii=False, indent=1)

sys.stdout.reconfigure(encoding="utf-8")
print("success:", data.get("success"))
print("message:", data.get("message"))
ar = data.get("analysis_report") or {}
print("analysis_report keys:", list(ar.keys())[:15] if isinstance(ar, dict) else type(ar))
txt = data.get("analysis_report_markdown") or ""
i = txt.find("可断言性预览")
print(txt[i:i+500] if i >= 0 else txt[:400])
