#!/usr/bin/env python3
"""
P1 修复验证脚本 - 系统重启后运行

用法:
    cd /d/code/Pyproject/ai-test-agent-system-platform
    python restart_verify_p1.py

步骤:
    1. 启动 FastAPI 服务 (端口 8000)
    2. 运行 HTTP 级验证
    3. 输出结果
"""

import subprocess
import sys
import time
import urllib.request
import urllib.error
import json

# 配置
BASE_URL = "http://127.0.0.1:8000/api/v2"
PROJECT_ID = "PR-1"
OTHER_PROJECT_ID = "PR-2"


def wait_for_service(url, timeout=30):
    """等待服务启动"""
    start = time.time()
    while time.time() - start < timeout:
        try:
            req = urllib.request.Request(url)
            resp = urllib.request.urlopen(req, timeout=2)
            if resp.status == 200:
                return True
        except Exception:
            pass
        time.sleep(1)
    return False


def test_cross_project_space(space_id):
    """测试跨项目空间访问"""
    req = urllib.request.Request(
        f"{BASE_URL}/projects/{OTHER_PROJECT_ID}/knowledge-base/spaces/{space_id}"
    )
    try:
        resp = urllib.request.urlopen(req)
        return False, f"HTTP {resp.status} (expected 403)"
    except urllib.error.HTTPError as e:
        if e.code == 403:
            return True, f"HTTP {e.code}"
        return False, f"HTTP {e.code} (expected 403)"


def test_cross_project_document(space_id, doc_id):
    """测试跨项目文档访问"""
    req = urllib.request.Request(
        f"{BASE_URL}/projects/{OTHER_PROJECT_ID}/knowledge-base/spaces/{space_id}/documents/{doc_id}"
    )
    try:
        resp = urllib.request.urlopen(req)
        return False, f"HTTP {resp.status} (expected 403)"
    except urllib.error.HTTPError as e:
        if e.code == 403:
            return True, f"HTTP {e.code}"
        return False, f"HTTP {e.code} (expected 403)"


def test_same_project_space(space_id):
    """测试同项目空间访问"""
    req = urllib.request.Request(
        f"{BASE_URL}/projects/{PROJECT_ID}/knowledge-base/spaces/{space_id}"
    )
    try:
        resp = urllib.request.urlopen(req)
        if resp.status == 200:
            return True, f"HTTP {resp.status}"
        return False, f"HTTP {resp.status} (expected 200)"
    except urllib.error.HTTPError as e:
        return False, f"HTTP {e.code} (expected 200)"


def test_unsupported_file_type(space_id):
    """测试不支持文件类型"""
    boundary = '----WebKitFormBoundary7MA4YWxkTrZu0gW'
    body = (
        f'------WebKitFormBoundary7MA4YWxkTrZu0gW\r\n'
        f'Content-Disposition: form-data; name="file"; filename="test.exe"\r\n'
        f'Content-Type: application/x-msdownload\r\n\r\n'
        f'MZ\x90\x00\r\n'
        f'------WebKitFormBoundary7MA4YWxkTrZu0gW--\r\n'
    ).encode()

    req = urllib.request.Request(
        f"{BASE_URL}/projects/{PROJECT_ID}/knowledge-base/spaces/{space_id}/documents",
        data=body,
        headers={'Content-Type': f'multipart/form-data; boundary={boundary}'},
        method='POST'
    )
    try:
        resp = urllib.request.urlopen(req)
        return False, f"HTTP {resp.status} (expected 400)"
    except urllib.error.HTTPError as e:
        if e.code == 400:
            return True, f"HTTP {e.code}"
        return False, f"HTTP {e.code} (expected 400)"


def test_large_file(space_id):
    """测试超大文件"""
    boundary = '----WebKitFormBoundary7MA4YWxkTrZu0gW'
    big_content = b'A' * (51 * 1024 * 1024)  # 51MB
    body = (
        f'------WebKitFormBoundary7MA4YWxkTrZu0gW\r\n'
        f'Content-Disposition: form-data; name="file"; filename="large.txt"\r\n'
        f'Content-Type: text/plain\r\n\r\n'.encode()
        + big_content
        + b'\r\n------WebKitFormBoundary7MA4YWxkTrZu0gW--\r\n'
    )

    req = urllib.request.Request(
        f"{BASE_URL}/projects/{PROJECT_ID}/knowledge-base/spaces/{space_id}/documents",
        data=body,
        headers={'Content-Type': f'multipart/form-data; boundary={boundary}'},
        method='POST'
    )
    try:
        resp = urllib.request.urlopen(req)
        return False, f"HTTP {resp.status} (expected 400)"
    except urllib.error.HTTPError as e:
        if e.code == 400:
            return True, f"HTTP {e.code}"
        return False, f"HTTP {e.code} (expected 400)"


def get_or_create_space():
    """获取或创建一个测试空间"""
    # 先尝试列表获取
    req = urllib.request.Request(
        f"{BASE_URL}/projects/{PROJECT_ID}/knowledge-base/spaces?p=1&page_size=1"
    )
    try:
        resp = urllib.request.urlopen(req)
        data = json.loads(resp.read())
        if data.get('data') and len(data['data']) > 0:
            return data['data'][0]['id']
    except Exception:
        pass

    # 创建新空间
    req = urllib.request.Request(
        f"{BASE_URL}/projects/{PROJECT_ID}/knowledge-base/spaces",
        data=json.dumps({
            "name": "P1 Verify Space",
            "description": "HTTP semantic verification"
        }).encode(),
        headers={'Content-Type': 'application/json'},
        method='POST'
    )
    resp = urllib.request.urlopen(req)
    data = json.loads(resp.read())
    return data['data']['id']


def main():
    print("=" * 60)
    print("P1 HTTP Semantic Fix - Post-Reboot Verification")
    print("=" * 60)
    print()

    # 步骤 1: 启动服务
    print("[Step 1] Starting FastAPI service on port 8000...")
    print("  Command: cd backend && python -m uvicorn app.main:app --host 0.0.0.0 --port 8000")
    print()
    print("  Please start the service in another terminal, then press Enter to continue...")
    input()

    # 步骤 2: 等待服务就绪
    print("[Step 2] Waiting for service to be ready...")
    if not wait_for_service("http://127.0.0.1:8000/"):
        print("  ERROR: Service did not start within 30 seconds")
        sys.exit(1)
    print("  OK: Service is ready")
    print()

    # 步骤 3: 获取测试空间
    print("[Step 3] Getting test space...")
    try:
        space_id = get_or_create_space()
        print(f"  OK: Using space {space_id}")
    except Exception as e:
        print(f"  ERROR: Failed to get/create space: {e}")
        sys.exit(1)
    print()

    # 步骤 4: 运行验证
    print("[Step 4] Running P1 verification tests...")
    print()

    results = []

    # Test 1
    print("Test 1: Cross-project space access")
    ok, msg = test_cross_project_space(space_id)
    results.append(("Cross-project space", ok, msg))
    print(f"  {'PASS' if ok else 'FAIL'}: {msg}")

    # Test 2
    print("Test 2: Cross-project document access")
    ok, msg = test_cross_project_document(space_id, "00000000-0000-0000-0000-000000000001")
    results.append(("Cross-project document", ok, msg))
    print(f"  {'PASS' if ok else 'FAIL'}: {msg}")

    # Test 3
    print("Test 3: Same-project space access")
    ok, msg = test_same_project_space(space_id)
    results.append(("Same-project space", ok, msg))
    print(f"  {'PASS' if ok else 'FAIL'}: {msg}")

    # Test 4
    print("Test 4: Unsupported file type (.exe)")
    ok, msg = test_unsupported_file_type(space_id)
    results.append(("Unsupported file type", ok, msg))
    print(f"  {'PASS' if ok else 'FAIL'}: {msg}")

    # Test 5
    print("Test 5: Large file (>50MB)")
    ok, msg = test_large_file(space_id)
    results.append(("Large file rejection", ok, msg))
    print(f"  {'PASS' if ok else 'FAIL'}: {msg}")

    # 汇总
    print()
    print("=" * 60)
    print("Results Summary")
    print("=" * 60)
    passed = sum(1 for _, ok, _ in results if ok)
    total = len(results)
    for name, ok, msg in results:
        status = "✅ PASS" if ok else "❌ FAIL"
        print(f"  {status}: {name} ({msg})")
    print()
    print(f"Total: {passed}/{total} tests passed")

    if passed == total:
        print()
        print("🎉 All P1 fixes verified successfully!")
        return 0
    else:
        print()
        print("⚠️ Some tests failed. Please check the service logs.")
        return 1


if __name__ == "__main__":
    sys.exit(main())
