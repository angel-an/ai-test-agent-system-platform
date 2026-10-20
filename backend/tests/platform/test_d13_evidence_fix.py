# -*- coding: utf-8 -*-
"""D13 修正测试：统一证据链修复验证。"""

import tempfile
import zipfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.test_platform.evidence_chain import (
    WebEvidence,
    ApiEvidence,
    build_evidence_package,
)
from app.test_platform.directory_layout import DirectoryLayout

client = TestClient(app)


class TestOutputDirFix:
    """P1: output_dir 参数生效验证"""

    def test_output_dir_is_used(self):
        """传入 output_dir 时，证据应写入该目录而非默认 workspace"""
        with tempfile.TemporaryDirectory() as tmp:
            custom_dir = Path(tmp) / "custom_evidence"
            zip_path = build_evidence_package(
                run_id="run-output-dir",
                web_ev=WebEvidence(screenshots=[]),
                output_dir=str(custom_dir),
            )
            # ZIP 应在自定义目录
            assert zip_path.parent == custom_dir
            assert zip_path.exists()

    def test_default_dir_with_project(self):
        """不传 output_dir 时，使用项目隔离目录"""
        zip_path = build_evidence_package(
            run_id="run-project-isolation",
            web_ev=WebEvidence(screenshots=[]),
            project="PROJ-TEST",
        )
        # 路径应包含项目标识
        assert "PROJ-TEST" in str(zip_path)
        assert zip_path.exists()


class TestScreenshotInZip:
    """P1: 截图实际进入 ZIP 验证"""

    def test_screenshot_packed_in_zip(self):
        """截图文件应被打包进 ZIP"""
        with tempfile.TemporaryDirectory() as tmp:
            output_dir = Path(tmp) / "evidence"
            output_dir.mkdir()

            # 创建真实截图文件
            screenshots_dir = output_dir / "screenshots"
            screenshots_dir.mkdir()
            (screenshots_dir / "01_login.png").write_bytes(b"fake_png_data")

            web_ev = WebEvidence(
                screenshots=["screenshots/01_login.png"],
            )

            zip_path = build_evidence_package(
                run_id="run-screenshot",
                web_ev=web_ev,
                output_dir=str(output_dir),
            )

            # 验证 ZIP 内容
            with zipfile.ZipFile(zip_path, "r") as zf:
                names = zf.namelist()
                assert "evidence.json" in names
                assert "screenshots/01_login.png" in names
                # 验证截图内容
                assert zf.read("screenshots/01_login.png") == b"fake_png_data"

    def test_self_reflect_in_zip(self):
        """self_reflect_result.json 应进入 ZIP"""
        with tempfile.TemporaryDirectory() as tmp:
            output_dir = Path(tmp) / "evidence"
            output_dir.mkdir()

            sr_file = output_dir / "self_reflect_result.json"
            sr_file.write_text('{"status": "passed"}', encoding="utf-8")

            web_ev = WebEvidence(
                screenshots=[],
                self_reflect_path=str(sr_file),
            )

            zip_path = build_evidence_package(
                run_id="run-sr",
                web_ev=web_ev,
                output_dir=str(output_dir),
            )

            with zipfile.ZipFile(zip_path, "r") as zf:
                assert "self_reflect_result.json" in zf.namelist()

    def test_db_records_in_zip(self):
        """db_records 应进入 ZIP"""
        with tempfile.TemporaryDirectory() as tmp:
            output_dir = Path(tmp) / "evidence"

            api_ev = ApiEvidence(
                db_records=[{"id": 1, "name": "test"}],
            )

            zip_path = build_evidence_package(
                run_id="run-db",
                api_ev=api_ev,
                output_dir=str(output_dir),
            )

            with zipfile.ZipFile(zip_path, "r") as zf:
                assert "db_records.json" in zf.namelist()
                content = zf.read("db_records.json").decode("utf-8")
                assert "test" in content


class TestProjectIsolation:
    """P1: 项目隔离验证"""

    def test_different_projects_same_run_id(self):
        """不同项目使用相同 run_id 应隔离"""
        zip1 = build_evidence_package(
            run_id="shared-run",
            web_ev=WebEvidence(screenshots=[]),
            project="PROJ-A",
        )
        zip2 = build_evidence_package(
            run_id="shared-run",
            web_ev=WebEvidence(screenshots=[]),
            project="PROJ-B",
        )

        # 两个 ZIP 路径不同
        assert zip1 != zip2
        assert "PROJ-A" in str(zip1)
        assert "PROJ-B" in str(zip2)

    def test_evidence_dir_with_project(self):
        """DirectoryLayout.evidence_dir 支持 project 参数"""
        layout = DirectoryLayout()
        d = layout.evidence_dir("run-1", project="PROJ-X")
        assert "PROJ-X" in str(d)
        assert "run-1" in str(d)


class TestEvidenceBuildRequest:
    """P2: Body 模型验证"""

    def test_build_with_body_model(self):
        """使用 JSON body 构建证据"""
        response = client.post(
            "/api/v2/projects/PROJ-1/evidence/run-body-test/build",
            json={
                "web_screenshots": ["a.png"],
                "api_response_body": {"code": 0},
                "db_records": [{"id": 1}],
                "metadata": {"test": True},
            },
        )
        assert response.status_code == 200
        data = response.json()["data"]
        assert data["has_web"] is True
        assert data["has_api"] is True

    def test_build_empty_body(self):
        """空 body 构建空证据"""
        response = client.post(
            "/api/v2/projects/PROJ-1/evidence/run-empty/build",
            json={},
        )
        assert response.status_code == 200
        data = response.json()["data"]
        assert data["has_web"] is False
        assert data["has_api"] is False


class TestDownloadContent:
    """P2: 下载接口内容验证"""

    def test_download_zip_content(self):
        """下载 ZIP 内容正确"""
        # 先构建证据
        client.post(
            "/api/v2/projects/PROJ-1/evidence/run-dl-test/build",
            json={"api_response_body": {"code": 0}},
        )

        response = client.get(
            "/api/v2/projects/PROJ-1/evidence/run-dl-test/download"
        )
        assert response.status_code == 200
        assert response.headers["content-type"] == "application/zip"
        assert "run-dl-test_evidence.zip" in response.headers["content-disposition"]

        # 验证是合法 ZIP
        import io
        zip_data = io.BytesIO(response.content)
        with zipfile.ZipFile(zip_data, "r") as zf:
            assert "evidence.json" in zf.namelist()

    def test_download_not_found(self):
        """下载不存在的证据"""
        response = client.get(
            "/api/v2/projects/PROJ-1/evidence/non-existent/download"
        )
        assert response.status_code == 404


class TestScreenshotDownload:
    """P2: 单张截图下载验证"""

    def test_screenshot_path_security(self):
        """路径遍历应被阻止"""
        response = client.get(
            "/api/v2/projects/PROJ-1/evidence/run-1/screenshots/../../../etc/passwd"
        )
        # FastAPI 路由匹配失败时返回 404，文件名校验在匹配后执行
        # 这里由于路径结构不匹配，返回 404 也是可接受的安全行为
        assert response.status_code in (400, 404)

    def test_screenshot_invalid_filename(self):
        """非法文件名应被拒绝"""
        response = client.get(
            "/api/v2/projects/PROJ-1/evidence/run-1/screenshots/test.php"
        )
        assert response.status_code == 400

    def test_screenshot_not_found(self):
        """不存在的截图"""
        response = client.get(
            "/api/v2/projects/PROJ-1/evidence/run-1/screenshots/notfound.png"
        )
        assert response.status_code == 404


class TestRouteConsistency:
    """路由一致性"""

    def test_all_routes_return_success_response(self):
        """所有路由返回 SuccessResponse"""
        # 先构建证据
        client.post(
            "/api/v2/projects/PROJ-1/evidence/run-cons/build",
            json={"api_response_body": {"code": 0}},
        )

        routes = [
            ("GET", "/api/v2/projects/PROJ-1/evidence/run-cons", None),
            ("GET", "/api/v2/projects/PROJ-1/evidence/run-cons/screenshots", None),
        ]

        for method, url, body in routes:
            if method == "GET":
                resp = client.get(url)
            else:
                resp = client.post(url, json=body or {})

            data = resp.json()
            assert "success" in data, f"{method} {url} 缺少 success"
            assert "data" in data, f"{method} {url} 缺少 data"

        # 下载接口返回二进制 ZIP，不解析 JSON
        resp = client.get("/api/v2/projects/PROJ-1/evidence/run-cons/download")
        assert resp.status_code == 200
        assert resp.headers["content-type"] == "application/zip"
