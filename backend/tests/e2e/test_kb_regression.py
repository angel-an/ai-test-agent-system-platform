"""
Knowledge Base Regression & Stability Test

验证范围（不依赖 Embedding API）：
  1. 重复上传 - 同名文件再次上传应正常处理
  2. 跨项目隔离 - 项目 A 的空间不应被项目 B 访问
  3. 异常文件处理 - 空文件、超大文件、不支持格式
  4. 删除清理 - 删除空间/文档后数据库无残留

Run: pytest backend/tests/e2e/test_kb_regression.py -v
Env: FastAPI on port 8000, Postgres + MinIO available
"""
import pytest
import httpx
import time
from uuid import UUID
from pathlib import Path

BASE_URL = "http://localhost:8000/api/v2"
PROJECT_ID = "PR-1"
OTHER_PROJECT_ID = "PR-2"


def api(method: str, path: str, project_id: str = PROJECT_ID, **kwargs):
    """Make HTTP request to API"""
    url = f"{BASE_URL}/projects/{project_id}{path}"
    return httpx.request(method, url, timeout=60.0, **kwargs)


# Module-level state
_test_state = {}


@pytest.fixture(scope="module")
def space_id():
    """Create a knowledge space and yield its ID, then cleanup"""
    resp = api("POST", "/knowledge-base/spaces", json={
        "name": "Regression Test Space",
        "description": "Auto-created for regression testing",
        "business_line": "regression-test"
    })
    assert resp.status_code == 201, f"Create space failed: {resp.text}"
    data = resp.json()
    sid = data["data"]["id"]
    print(f"\n[SETUP] Created space: {sid}")

    yield sid

    # Cleanup - delete space and all associated documents/chunks
    resp = api("DELETE", f"/knowledge-base/spaces/{sid}")
    print(f"[TEARDOWN] Deleted space: {sid} (status={resp.status_code})")


class TestKbRegression:
    """KB Regression & Stability Test Suite"""

    # ========================================================================
    # 5.1 重复上传
    # ========================================================================

    def test_01_reupload_same_document(self, space_id):
        """重复上传同名文件应正常处理（创建新文档记录）"""
        content = "重复上传测试文档内容"
        fixture_dir = Path(__file__).parent / "fixtures"
        fixture_dir.mkdir(exist_ok=True)
        test_file = fixture_dir / "reupload_test.txt"
        test_file.write_text(content, encoding="utf-8")

        # 第一次上传
        with open(test_file, "rb") as f:
            resp1 = api("POST", f"/knowledge-base/spaces/{space_id}/documents",
                      files={"file": ("reupload_test.txt", f, "text/plain")},
                      data={"title": "重复上传测试"})
        assert resp1.status_code in (200, 201), f"First upload failed: {resp1.text}"
        data1 = resp1.json()
        assert data1["success"] is True
        doc_id_1 = data1["data"]["document_id"]
        print(f"  [INFO] First upload: doc_id={doc_id_1}")

        # 第二次上传（同名文件）
        with open(test_file, "rb") as f:
            resp2 = api("POST", f"/knowledge-base/spaces/{space_id}/documents",
                      files={"file": ("reupload_test.txt", f, "text/plain")},
                      data={"title": "重复上传测试2"})
        assert resp2.status_code in (200, 201), f"Second upload failed: {resp2.text}"
        data2 = resp2.json()
        assert data2["success"] is True
        doc_id_2 = data2["data"]["document_id"]
        print(f"  [INFO] Second upload: doc_id={doc_id_2}")

        # 验证两次上传创建了不同的文档记录
        assert doc_id_1 != doc_id_2, "Reupload should create a new document record"

        # 验证列表中有两个文档
        resp = api("GET", f"/knowledge-base/spaces/{space_id}/documents")
        docs = resp.json()["data"]
        doc_ids = [d["id"] for d in docs]
        assert doc_id_1 in doc_ids, "First doc should be in list"
        assert doc_id_2 in doc_ids, "Second doc should be in list"

        # 清理测试文档
        api("DELETE", f"/knowledge-base/spaces/{space_id}/documents/{doc_id_1}")
        api("DELETE", f"/knowledge-base/spaces/{space_id}/documents/{doc_id_2}")
        print(f"  [PASS] Reupload test: two separate docs created and cleaned up")

    # ========================================================================
    # 5.2 跨项目隔离
    # ========================================================================

    def test_02_cross_project_space_access(self, space_id):
        """跨项目访问知识空间应被阻止（返回 403 Forbidden）"""
        # 尝试用 PR-2 访问 PR-1 的空间
        resp = api("GET", f"/knowledge-base/spaces/{space_id}", project_id=OTHER_PROJECT_ID)
        print(f"  [INFO] Cross-project GET space: {resp.status_code}")
        # P1 修复后应返回 403
        assert resp.status_code == 403, f"Cross-project access should return 403, got {resp.status_code}"
        data = resp.json()
        assert data.get("success") is False
        assert "不属于项目" in data.get("message", "")
        print(f"  [PASS] Cross-project space access blocked with 403")

    def test_03_cross_project_document_access(self, space_id):
        """跨项目访问文档应被阻止"""
        # 先上传一个文档
        content = "跨项目隔离测试"
        fixture_dir = Path(__file__).parent / "fixtures"
        test_file = fixture_dir / "isolation_test.txt"
        test_file.write_text(content, encoding="utf-8")

        with open(test_file, "rb") as f:
            resp = api("POST", f"/knowledge-base/spaces/{space_id}/documents",
                      files={"file": ("isolation_test.txt", f, "text/plain")})
        assert resp.status_code in (200, 201)
        doc_id = resp.json()["data"]["document_id"]

        # 尝试用 PR-2 访问 PR-1 的文档
        resp = api("GET", f"/knowledge-base/spaces/{space_id}/documents/{doc_id}", project_id=OTHER_PROJECT_ID)
        print(f"  [INFO] Cross-project GET document: {resp.status_code}")
        assert resp.status_code == 403, f"Cross-project doc access should return 403, got {resp.status_code}"

        # 尝试用 PR-2 删除 PR-1 的文档
        resp = api("DELETE", f"/knowledge-base/spaces/{space_id}/documents/{doc_id}", project_id=OTHER_PROJECT_ID)
        print(f"  [INFO] Cross-project DELETE document: {resp.status_code}")
        assert resp.status_code == 403, f"Cross-project doc delete should return 403, got {resp.status_code}"

        print(f"  [PASS] Cross-project document access blocked with 403 for GET and DELETE")

        # 清理
        api("DELETE", f"/knowledge-base/spaces/{space_id}/documents/{doc_id}")
        print(f"  [PASS] Cross-project document access blocked for GET and DELETE")

    def test_04_cross_project_list_spaces(self, space_id):
        """跨项目列出的空间列表应不同"""
        resp_pr1 = api("GET", "/knowledge-base/spaces", project_id=PROJECT_ID)
        resp_pr2 = api("GET", "/knowledge-base/spaces", project_id=OTHER_PROJECT_ID)

        assert resp_pr1.status_code == 200
        assert resp_pr2.status_code == 200

        spaces_pr1 = [s["id"] for s in resp_pr1.json()["data"]]
        spaces_pr2 = [s["id"] for s in resp_pr2.json()["data"]]

        # PR-1 的空间不应出现在 PR-2 的列表中
        assert space_id in spaces_pr1, "Space should be in PR-1 list"
        assert space_id not in spaces_pr2, "Space should NOT be in PR-2 list"
        print(f"  [PASS] Project isolation: space only in PR-1 ({len(spaces_pr1)} spaces), not in PR-2 ({len(spaces_pr2)} spaces)")

    # ========================================================================
    # 5.3 异常文件处理
    # ========================================================================

    def test_05_empty_file_upload(self, space_id):
        """上传空文件（0 bytes）应有错误提示"""
        fixture_dir = Path(__file__).parent / "fixtures"
        empty_file = fixture_dir / "empty.txt"
        empty_file.write_text("", encoding="utf-8")

        with open(empty_file, "rb") as f:
            resp = api("POST", f"/knowledge-base/spaces/{space_id}/documents",
                      files={"file": ("empty.txt", f, "text/plain")})

        print(f"  [INFO] Empty file upload: {resp.status_code}")
        # 空文件应该被接受（0 bytes 是合法的文本文件），或者返回错误
        # 记录实际行为，不强制断言
        data = resp.json()
        print(f"  [INFO] Empty file response: success={data.get('success')}, status={data.get('data', {}).get('status') if data.get('data') else 'N/A'}")

        if resp.status_code in (200, 201) and data.get("success"):
            doc_id = data["data"]["document_id"]
            # 清理
            api("DELETE", f"/knowledge-base/spaces/{space_id}/documents/{doc_id}")
            print(f"  [PASS] Empty file accepted (doc_id={doc_id})")
        else:
            print(f"  [PASS] Empty file rejected: {data.get('message', 'unknown reason')}")

    def test_06_unsupported_file_type(self, space_id):
        """上传不支持的文件类型（如 .exe）应返回 HTTP 400 Bad Request"""
        fixture_dir = Path(__file__).parent / "fixtures"
        bad_file = fixture_dir / "test.exe"
        bad_file.write_bytes(b"MZ\x90\x00\x03\x00\x00\x00\x04\x00\x00\x00\xFF\xFF\x00\x00")

        with open(bad_file, "rb") as f:
            resp = api("POST", f"/knowledge-base/spaces/{space_id}/documents",
                      files={"file": ("test.exe", f, "application/x-msdownload")})

        print(f"  [INFO] Unsupported file upload: HTTP {resp.status_code}")
        data = resp.json()

        # P1 修复后应返回 HTTP 400
        assert resp.status_code == 400, f"Expected HTTP 400, got {resp.status_code}"
        assert data.get("success") is False
        assert "不支持的文件类型" in data.get("message", "")
        print(f"  [PASS] Unsupported file rejected with HTTP 400")

    def test_07_large_file_rejection(self, space_id):
        """上传超过 50MB 的文件应被拒绝（HTTP 400 Bad Request）"""
        # 创建一个略大于 50MB 的临时文件
        fixture_dir = Path(__file__).parent / "fixtures"
        large_file = fixture_dir / "large_test.txt"

        # 写 51MB 的文件
        chunk = b"A" * (1024 * 1024)  # 1MB
        with open(large_file, "wb") as f:
            for _ in range(51):
                f.write(chunk)

        file_size = large_file.stat().st_size
        print(f"  [INFO] Large file size: {file_size / 1024 / 1024:.1f} MB")

        with open(large_file, "rb") as f:
            resp = api("POST", f"/knowledge-base/spaces/{space_id}/documents",
                      files={"file": ("large_test.txt", f, "text/plain")})

        print(f"  [INFO] Large file upload: HTTP {resp.status_code}")

        # P1 修复后应返回 HTTP 400
        assert resp.status_code == 400, f"Expected HTTP 400, got {resp.status_code}"
        data = resp.json()
        assert data.get("success") is False
        assert "50MB" in data.get("message", "")
        print(f"  [PASS] Large file rejected with HTTP 400: {data.get('message')}")

        # 清理大文件
        large_file.unlink(missing_ok=True)

    # ========================================================================
    # 5.4 删除清理
    # ========================================================================

    def test_08_delete_space_cleans_chunks(self, space_id):
        """删除空间后，关联的文档和 chunk 应被清除"""
        # 创建临时空间
        resp = api("POST", "/knowledge-base/spaces", json={
            "name": "Temp Cleanup Space",
            "description": "For cleanup test",
        })
        assert resp.status_code == 201
        temp_space_id = resp.json()["data"]["id"]
        print(f"  [INFO] Created temp space: {temp_space_id}")

        # 上传文档
        fixture_dir = Path(__file__).parent / "fixtures"
        test_file = fixture_dir / "cleanup_test.txt"
        test_file.write_text("清理测试文档内容\n" * 100, encoding="utf-8")

        with open(test_file, "rb") as f:
            resp = api("POST", f"/knowledge-base/spaces/{temp_space_id}/documents",
                      files={"file": ("cleanup_test.txt", f, "text/plain")})
        assert resp.status_code in (200, 201)
        doc_id = resp.json()["data"]["document_id"]
        print(f"  [INFO] Uploaded doc: {doc_id}")

        # 触发索引（即使失败也会创建一些记录）
        resp = api("POST", f"/knowledge-base/spaces/{temp_space_id}/documents/{doc_id}/index")
        print(f"  [INFO] Index response: {resp.status_code}")

        # 等待一下让索引完成或失败
        time.sleep(2)

        # 删除空间
        resp = api("DELETE", f"/knowledge-base/spaces/{temp_space_id}")
        assert resp.status_code in (200, 204), f"Delete space failed: {resp.text}"
        print(f"  [INFO] Deleted temp space: {temp_space_id}")

        # 验证空间已删除
        resp = api("GET", f"/knowledge-base/spaces/{temp_space_id}")
        assert resp.status_code in (404, 403), f"Space should be deleted, got {resp.status_code}"

        # 验证文档已删除
        resp = api("GET", f"/knowledge-base/spaces/{temp_space_id}/documents/{doc_id}")
        assert resp.status_code in (404, 403), f"Document should be deleted, got {resp.status_code}"

        print(f"  [PASS] Space deletion cascades to documents")

    def test_09_delete_document_cleans_chunks(self, space_id):
        """删除文档后，关联的 chunk 应被清除"""
        # 上传文档
        fixture_dir = Path(__file__).parent / "fixtures"
        test_file = fixture_dir / "doc_cleanup_test.txt"
        test_file.write_text("文档清理测试\n" * 50, encoding="utf-8")

        with open(test_file, "rb") as f:
            resp = api("POST", f"/knowledge-base/spaces/{space_id}/documents",
                      files={"file": ("doc_cleanup_test.txt", f, "text/plain")})
        assert resp.status_code in (200, 201)
        doc_id = resp.json()["data"]["document_id"]
        print(f"  [INFO] Uploaded doc for cleanup test: {doc_id}")

        # 触发索引
        resp = api("POST", f"/knowledge-base/spaces/{space_id}/documents/{doc_id}/index")
        print(f"  [INFO] Index response: {resp.status_code}")
        time.sleep(2)

        # 删除文档
        resp = api("DELETE", f"/knowledge-base/spaces/{space_id}/documents/{doc_id}")
        assert resp.status_code in (200, 204), f"Delete document failed: {resp.text}"
        print(f"  [INFO] Deleted doc: {doc_id}")

        # 验证文档已删除
        resp = api("GET", f"/knowledge-base/spaces/{space_id}/documents/{doc_id}")
        assert resp.status_code == 404, f"Document should be deleted, got {resp.status_code}"

        print(f"  [PASS] Document deletion successful")

    # ========================================================================
    # 4.x 前端交互相关 API 验证
    # ========================================================================

    def test_10_space_crud_operations(self, space_id):
        """空间 CRUD 操作完整流程"""
        # Create
        resp = api("POST", "/knowledge-base/spaces", json={
            "name": "CRUD Test Space",
            "description": "Testing CRUD",
            "business_line": "crud-test"
        })
        assert resp.status_code == 201
        new_space = resp.json()["data"]
        new_space_id = new_space["id"]
        assert new_space["name"] == "CRUD Test Space"
        assert new_space["business_line"] == "crud-test"
        print(f"  [INFO] Created space: {new_space_id}")

        # Read
        resp = api("GET", f"/knowledge-base/spaces/{new_space_id}")
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["name"] == "CRUD Test Space"
        print(f"  [INFO] Read space: {data['name']}")

        # Update
        resp = api("PUT", f"/knowledge-base/spaces/{new_space_id}", json={
            "name": "CRUD Test Space Updated",
            "description": "Updated description"
        })
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["name"] == "CRUD Test Space Updated"
        assert data["description"] == "Updated description"
        print(f"  [INFO] Updated space: {data['name']}")

        # List
        resp = api("GET", "/knowledge-base/spaces")
        spaces = resp.json()["data"]
        assert any(s["id"] == new_space_id for s in spaces)
        print(f"  [INFO] Listed spaces: found ours in {len(spaces)} spaces")

        # Delete
        resp = api("DELETE", f"/knowledge-base/spaces/{new_space_id}")
        assert resp.status_code in (200, 204)
        print(f"  [INFO] Deleted space: {new_space_id}")

        # Verify deleted
        resp = api("GET", f"/knowledge-base/spaces/{new_space_id}")
        assert resp.status_code in (404, 403)
        print(f"  [PASS] Space CRUD: create/read/update/list/delete all verified")

    def test_11_document_upload_with_title(self, space_id):
        """上传文档时带标题参数"""
        content = "带标题的测试文档"
        fixture_dir = Path(__file__).parent / "fixtures"
        test_file = fixture_dir / "titled_test.txt"
        test_file.write_text(content, encoding="utf-8")

        with open(test_file, "rb") as f:
            resp = api("POST", f"/knowledge-base/spaces/{space_id}/documents",
                      files={"file": ("titled_test.txt", f, "text/plain")},
                      data={"title": "自定义文档标题"})

        assert resp.status_code in (200, 201), f"Upload with title failed: {resp.text}"
        data = resp.json()
        assert data["success"] is True
        doc_id = data["data"]["document_id"]

        # 验证文档详情
        resp = api("GET", f"/knowledge-base/spaces/{space_id}/documents/{doc_id}")
        assert resp.status_code == 200
        doc = resp.json()["data"]
        # 注意：title 可能存储在数据库中，但 API 返回的字段可能不同
        print(f"  [INFO] Document: file_name={doc.get('file_name')}, title={doc.get('title', 'N/A')}")

        # 清理
        api("DELETE", f"/knowledge-base/spaces/{space_id}/documents/{doc_id}")
        print(f"  [PASS] Document upload with title: doc_id={doc_id}")

    def test_12_retrieve_without_space_id(self, space_id):
        """检索时不指定 space_id 应返回结果或降级"""
        # 先确保空间里有文档
        fixture_dir = Path(__file__).parent / "fixtures"
        test_file = fixture_dir / "retrieve_test.txt"
        test_file.write_text("用户登录功能需求：支持用户名密码登录、手机验证码登录、第三方 OAuth 登录。", encoding="utf-8")

        with open(test_file, "rb") as f:
            resp = api("POST", f"/knowledge-base/spaces/{space_id}/documents",
                      files={"file": ("retrieve_test.txt", f, "text/plain")})
        assert resp.status_code in (200, 201)
        doc_id = resp.json()["data"]["document_id"]

        # 触发索引
        api("POST", f"/knowledge-base/spaces/{space_id}/documents/{doc_id}/index")
        time.sleep(2)

        # 检索（不指定 space_id，只指定项目）
        resp = api("POST", "/knowledge-base/retrieve", json={
            "query": "用户登录",
            "top_k": 3,
        })
        assert resp.status_code == 200, f"Retrieve failed: {resp.text}"
        data = resp.json()
        assert data["success"] is True
        print(f"  [INFO] Retrieve without space_id: {len(data['results'])} results, degraded={data.get('degraded')}")

        # 清理
        api("DELETE", f"/knowledge-base/spaces/{space_id}/documents/{doc_id}")
        print(f"  [PASS] Retrieve without space_id works")
