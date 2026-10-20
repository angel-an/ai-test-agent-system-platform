# -*- coding: utf-8 -*-
"""D13 测试：统一证据链路由 + 混合证据测试。"""

import tempfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.test_platform.evidence_chain import (
    WebEvidence,
    ApiEvidence,
    UnifiedEvidence,
    build_evidence_package,
)

client = TestClient(app)


class TestEvidenceRoutes:
    """证据链路由联调测试"""

    def test_build_evidence(self):
        """POST 构建证据包"""
        response = client.post(
            "/api/v2/projects/PROJ-1/evidence/run-d13-test/build",
            json={
                "web_screenshots": ["01_login.png", "02_home.png"],
                "self_reflect_path": "/tmp/self_reflect.json",
                "api_response_body": {"code": 0, "data": {"id": 1}},
                "api_status_code": 200,
                "db_records": [{"id": 1, "name": "test"}],
                "metadata": {"test": "d13"},
            },
        )
        assert response.status_code == 200

        data = response.json()
        assert data["success"] is True
        assert data["data"]["run_id"] == "run-d13-test"
        assert data["data"]["has_web"] is True
        assert data["data"]["has_api"] is True

    def test_get_evidence_summary(self):
        """GET 证据摘要"""
        # 先构建证据
        client.post(
            "/api/v2/projects/PROJ-1/evidence/run-d13-summary/build",
            json={
                "web_screenshots": ["a.png"],
                "api_response_body": {"code": 0},
            },
        )

        response = client.get(
            "/api/v2/projects/PROJ-1/evidence/run-d13-summary"
        )
        assert response.status_code == 200

        data = response.json()["data"]
        assert data["run_id"] == "run-d13-summary"
        assert data["has_zip"] is True

    def test_get_evidence_not_found(self):
        """GET 不存在的证据"""
        response = client.get(
            "/api/v2/projects/PROJ-1/evidence/non-existent-run"
        )
        assert response.status_code == 404

    def test_list_screenshots(self):
        """GET 截图列表"""
        # 先构建带截图的证据
        client.post(
            "/api/v2/projects/PROJ-1/evidence/run-d13-ss/build",
            json={"web_screenshots": ["01.png", "02.png"]},
        )

        response = client.get(
            "/api/v2/projects/PROJ-1/evidence/run-d13-ss/screenshots"
        )
        assert response.status_code == 200

        screenshots = response.json()["data"]
        # 截图目录不存在（只有文件名记录），返回空列表
        assert isinstance(screenshots, list)

    def test_success_response_wrapper(self):
        """所有路由返回 SuccessResponse"""
        routes = [
            ("POST", "/api/v2/projects/PROJ-1/evidence/run-d13-wrapper/build", {"web_screenshots": []}),
            ("GET", "/api/v2/projects/PROJ-1/evidence/run-d13-wrapper", None),
            ("GET", "/api/v2/projects/PROJ-1/evidence/run-d13-wrapper/screenshots", None),
        ]

        # 先构建证据
        client.post(routes[0][1], json=routes[0][2])

        for method, url, body in routes[1:]:
            if method == "GET":
                resp = client.get(url)
            else:
                resp = client.post(url, json=body or {})

            data = resp.json()
            assert "success" in data, f"{method} {url} 缺少 success 字段"
            assert "data" in data, f"{method} {url} 缺少 data 字段"


class TestMixedEvidence:
    """混合证据测试"""

    def test_web_only_evidence(self):
        """仅 Web 证据"""
        with tempfile.TemporaryDirectory() as tmp:
            web_ev = WebEvidence(screenshots=["a.png"])
            zip_path = build_evidence_package(
                run_id="web-only",
                web_ev=web_ev,
                output_dir=tmp,
            )
            assert zip_path.exists()

            # 验证 ZIP 内容
            import zipfile
            with zipfile.ZipFile(zip_path, "r") as zf:
                names = zf.namelist()
                assert "evidence.json" in names

    def test_api_only_evidence(self):
        """仅 API 证据"""
        with tempfile.TemporaryDirectory() as tmp:
            api_ev = ApiEvidence(
                response_body={"code": 0},
                db_records=[{"id": 1}],
            )
            zip_path = build_evidence_package(
                run_id="api-only",
                api_ev=api_ev,
                output_dir=tmp,
            )
            assert zip_path.exists()

    def test_empty_evidence(self):
        """空证据"""
        with tempfile.TemporaryDirectory() as tmp:
            zip_path = build_evidence_package(
                run_id="empty",
                output_dir=tmp,
            )
            assert zip_path.exists()

            # 验证只有 evidence.json
            import zipfile
            with zipfile.ZipFile(zip_path, "r") as zf:
                assert zf.namelist() == ["evidence.json"]

    def test_mixed_evidence_summary(self):
        """混合证据摘要"""
        ue = UnifiedEvidence(
            run_id="mixed",
            web=WebEvidence(screenshots=["a.png", "b.png"]),
            api=ApiEvidence(
                response_body={"code": 0},
                db_records=[{"id": 1}, {"id": 2}],
            ),
        )
        s = ue.summary()
        assert s["has_web"] is True
        assert s["has_api"] is True
        assert s["screenshot_count"] == 2
        assert s["db_record_count"] == 2

    def test_unified_evidence_to_dict(self):
        """统一证据序列化"""
        ue = UnifiedEvidence(
            run_id="test",
            web=WebEvidence(screenshots=["a.png"]),
            api=ApiEvidence(status_code=200),
            metadata={"project": "PROJ-1"},
        )
        d = ue.to_dict()
        assert d["run_id"] == "test"
        assert d["web"]["type"] == "web"
        assert d["api"]["type"] == "api"
        assert d["metadata"]["project"] == "PROJ-1"
