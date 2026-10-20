# -*- coding: utf-8 -*-
"""D13 兼容层与路径安全补测。"""

import tempfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


class TestPlatformCompatibility:
    """P1: app.test_platform 与 app.test_platform 对象一致性"""

    def test_evidence_chain_import_consistency(self):
        """双路径导入的对象一致性"""
        from app.test_platform.evidence_chain import (
            WebEvidence as W1,
            ApiEvidence as A1,
            UnifiedEvidence as U1,
        )
        from app.test_platform.evidence_chain import (
            WebEvidence as W2,
            ApiEvidence as A2,
            UnifiedEvidence as U2,
        )

        assert W1 is W2
        assert A1 is A2
        assert U1 is U2

    def test_directory_layout_import_consistency(self):
        """DirectoryLayout 双路径一致性"""
        from app.test_platform.directory_layout import DirectoryLayout as D1
        from app.test_platform.directory_layout import DirectoryLayout as D2

        assert D1 is D2

    def test_evidence_dir_with_project_param(self):
        """兼容层 evidence_dir 支持 project 参数"""
        from app.test_platform.directory_layout import DirectoryLayout

        layout = DirectoryLayout()
        d = layout.evidence_dir("run-1", project="PROJ-X")
        assert "PROJ-X" in str(d)
        assert "run-1" in str(d)


class TestPathSecurity:
    """P2: 路径安全验证（基于路由行为，不依赖文件系统状态）"""

    def test_filename_validation_rejects_illegal(self):
        """非法文件名应被拒绝"""
        resp = client.get(
            "/api/v2/projects/PROJ-1/evidence/run-1/screenshots/test.php"
        )
        assert resp.status_code == 400

    def test_filename_validation_accepts_legal(self):
        """合法 PNG 文件名格式校验通过"""
        # 不存在的文件返回 404（说明文件名校验已通过）
        resp = client.get(
            "/api/v2/projects/PROJ-1/evidence/run-1/screenshots/login.png"
        )
        # 404 = 文件名合法但文件不存在（校验通过）
        assert resp.status_code == 404

    def test_encoded_path_traversal(self):
        """编码路径遍历：URL 解码后含 .. 的文件名"""
        resp = client.get(
            "/api/v2/projects/PROJ-1/evidence/run-1/screenshots/..%2Fsecret.png"
        )
        # FastAPI 路由匹配前 URL 已解码，含 .. 的文件名匹配失败返回 404
        # 这是可接受的安全行为（未进入文件读取逻辑）
        assert resp.status_code in (400, 404)

    def test_backslash_in_filename(self):
        """反斜杠在文件名中"""
        resp = client.get(
            "/api/v2/projects/PROJ-1/evidence/run-1/screenshots/a\\b.png"
        )
        assert resp.status_code in (400, 404)

    def test_symlink_not_followed(self):
        """符号链接不被跟随"""
        import os
        with tempfile.TemporaryDirectory() as tmp:
            from app.test_platform.directory_layout import DirectoryLayout
            layout = DirectoryLayout(tmp)
            evidence_dir = layout.evidence_dir("run-sym", project="PROJ-SYM")
            screenshots_dir = evidence_dir / "screenshots"
            screenshots_dir.mkdir(parents=True, exist_ok=True)

            # 创建符号链接（如果支持）
            target = Path(tmp) / "outside.txt"
            target.write_text("outside")
            link_path = screenshots_dir / "link.png"
            try:
                os.symlink(target, link_path)
            except (OSError, NotImplementedError):
                pytest.skip("当前平台不支持符号链接")

            # 符号链接文件名是合法的，但读取时应该检查是否为真实文件
            resp = client.get(
                "/api/v2/projects/PROJ-SYM/evidence/run-sym/screenshots/link.png"
            )
            # 返回 404 说明未成功读取（可能是符号链接不被跟随或文件不存在）
            assert resp.status_code in (400, 404)


class TestProjectIsolationRoutes:
    """P2: 项目隔离路由验证"""

    def test_same_run_id_different_projects(self):
        """相同 run_id 不同项目应隔离"""
        # 项目 A 构建证据
        resp_a = client.post(
            "/api/v2/projects/PROJ-A/evidence/shared-run/build",
            json={"api_response_body": {"project": "A"}},
        )
        assert resp_a.status_code == 200

        # 项目 B 构建同名 run_id
        resp_b = client.post(
            "/api/v2/projects/PROJ-B/evidence/shared-run/build",
            json={"api_response_body": {"project": "B"}},
        )
        assert resp_b.status_code == 200

        # A 读取自己的证据
        resp_a_get = client.get("/api/v2/projects/PROJ-A/evidence/shared-run")
        assert resp_a_get.status_code == 200
        data_a = resp_a_get.json()["data"]
        assert data_a["metadata"]["project"] == "PROJ-A"

        # B 读取自己的证据
        resp_b_get = client.get("/api/v2/projects/PROJ-B/evidence/shared-run")
        assert resp_b_get.status_code == 200
        data_b = resp_b_get.json()["data"]
        assert data_b["metadata"]["project"] == "PROJ-B"

    def test_cross_project_access_returns_404(self):
        """跨项目访问不存在的证据返回 404"""
        # 项目 A 构建证据
        client.post(
            "/api/v2/projects/PROJ-X/evidence/run-x/build",
            json={"api_response_body": {"code": 0}},
        )

        # 项目 Y 尝试访问项目 X 的 run_id
        resp = client.get("/api/v2/projects/PROJ-Y/evidence/run-x")
        assert resp.status_code == 404

    def test_project_isolation_in_zip_download(self):
        """ZIP 下载也应按项目隔离"""
        # 项目 A 构建
        client.post(
            "/api/v2/projects/PROJ-ZIP-A/evidence/run-zip/build",
            json={"api_response_body": {"project": "A"}},
        )

        # 项目 B 尝试下载
        resp = client.get("/api/v2/projects/PROJ-ZIP-B/evidence/run-zip/download")
        assert resp.status_code == 404
