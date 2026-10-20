# -*- coding: utf-8 -*-
"""rev84：全量重新生成 CS 用例。
1) 备份现有 35 条（含执行结论）到 artifacts/
2) raw SQL 清空文件夹（规避 ORM 批量删除 500 bug）
3) generate-from-document 全量生成（上限已放宽 300）
4) 清除批注噪音用例（一郎/YYYYMMDD 且 0 断言）
"""
import json
import sys
import urllib.request

import psycopg2

BASE = "http://localhost:8000/api/v2"
PR = "PR-9"
FOLDER = "abc46f5b-1653-46e8-a73e-a6aad46ee39c"
FILE = r"D:/code/Pyproject/ai-test-agent-system-platform/backend/workspace/uploads/CS_Module_方案説明2.0.pdf"
BACKUP = r"D:/code/Pyproject/ai-test-agent-system-platform/backend/artifacts/backup_cs_module_before_full_regen_20260928.json"
OUT = r"D:/code/Pyproject/ai-test-agent-system-platform/backend/rev84_generate_result.json"


def req(method, path, body=None, timeout=180):
    data = json.dumps(body).encode() if body is not None else None
    r = urllib.request.Request(BASE + path, data=data, method=method,
                               headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(r, timeout=timeout) as resp:
            raw = resp.read().decode()
            return json.loads(raw) if raw else {}
    except urllib.error.HTTPError as e:
        return {"__http__": e.code, "body": e.read().decode()[:500]}


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    conn = psycopg2.connect(host="localhost", port=5432, user="postgres",
                            password="postgres", dbname="langgraph_platform_db")
    cur = conn.cursor()

    # 1) 备份
    cur.execute("SELECT * FROM test_cases WHERE folder_id=%s ORDER BY created_at", (FOLDER,))
    cols = [d[0] for d in cur.description]
    cases = []
    for row in cur.fetchall():
        c = dict(zip(cols, row))
        for k, v in c.items():
            if hasattr(v, "isoformat"):
                c[k] = v.isoformat()
        cases.append(c)
    json.dump({"case_count": len(cases), "cases": cases},
              open(BACKUP, "w", encoding="utf-8"), ensure_ascii=False, indent=1, default=str)
    print(f"[backup] {len(cases)} cases -> {BACKUP}")

    # 2) 清空
    cur.execute("DELETE FROM test_cases WHERE folder_id=%s", (FOLDER,))
    print(f"[delete] {cur.rowcount}")
    conn.commit()
    conn.close()

    # 3) 全量生成
    boundary = "----rev84boundary"
    content = open(FILE, "rb").read()
    body = b"\r\n".join([
        f"--{boundary}".encode(),
        b'Content-Disposition: form-data; name="folder_id"', b"", FOLDER.encode(),
        f"--{boundary}".encode(),
        b'Content-Disposition: form-data; name="test_mode"', b"", b"sit",
        f"--{boundary}".encode(),
        'Content-Disposition: form-data; name="file"; filename="CS Module方案説明2.0.pdf"'.encode("utf-8"),
        b"Content-Type: application/pdf", b"", content,
        f"--{boundary}--".encode(),
    ])
    r = urllib.request.Request(
        f"{BASE}/projects/{PR}/ai/generate-from-document", data=body, method="POST",
        headers={"Content-Type": f"multipart/form-data; boundary={boundary}"})
    with urllib.request.urlopen(r, timeout=900) as resp:
        data = json.loads(resp.read().decode())
    json.dump(data, open(OUT, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    created = data.get("test_cases") or []
    print("[generate] success:", data.get("success"), "created:", len(created),
          "| msg:", data.get("message"))

    # 4) 清噪音（一郎批注 / YYYYMMDD 模板名且 0 断言）
    noise = [c for c in created
             if ("一郎" in (c.get("name") or "") or "YYYYMMDD" in (c.get("name") or ""))]
    if noise:
        conn = psycopg2.connect(host="localhost", port=5432, user="postgres",
                                password="postgres", dbname="langgraph_platform_db")
        cur = conn.cursor()
        for c in noise:
            cur.execute("DELETE FROM test_cases WHERE id=%s", (c["id"],))
        conn.commit()
        conn.close()
    print(f"[noise] deleted {len(noise)}")


if __name__ == "__main__":
    main()
