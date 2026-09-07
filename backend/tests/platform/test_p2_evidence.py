# -*- coding: utf-8 -*-
"""P2-3 测试：统一证据链。"""

import tempfile
from pathlib import Path

import pytest

from app.test_platform.evidence_chain import (
    WebEvidence,
    ApiEvidence,
    UnifiedEvidence,
    build_web_evidence,
    build_api_evidence,
    build_evidence_package,
)


class TestWebEvidence:
    def test_to_dict(self):
        ev = WebEvidence(
            screenshots=["01_login.png", "02_home.png"],
            self_reflect_path="/tmp/self_reflect.json",
        )
        d = ev.to_dict()
        assert d["type"] == "web"
        assert len(d["screenshots"]) == 2
        assert d["self_reflect_path"] == "/tmp/self_reflect.json"


class TestApiEvidence:
    def test_to_dict(self):
        ev = ApiEvidence(
            status_code=200,
            response_body={"code": 0},
            db_records=[{"id": 1}],
        )
        d = ev.to_dict()
        assert d["type"] == "api"
        assert d["status_code"] == 200
        assert len(d["db_records"]) == 1


class TestUnifiedEvidence:
    def test_has_web_evidence(self):
        ue = UnifiedEvidence(
            run_id="run-1",
            web=WebEvidence(screenshots=["a.png"]),
        )
        assert ue.has_web_evidence() is True
        assert ue.has_api_evidence() is False

    def test_has_api_evidence(self):
        ue = UnifiedEvidence(
            run_id="run-1",
            api=ApiEvidence(response_body={"code": 0}),
        )
        assert ue.has_web_evidence() is False
        assert ue.has_api_evidence() is True

    def test_summary(self):
        ue = UnifiedEvidence(
            run_id="run-1",
            web=WebEvidence(screenshots=["a.png", "b.png"]),
            api=ApiEvidence(db_records=[{"id": 1}]),
        )
        s = ue.summary()
        assert s["run_id"] == "run-1"
        assert s["has_web"] is True
        assert s["has_api"] is True
        assert s["screenshot_count"] == 2
        assert s["db_record_count"] == 1

    def test_to_dict(self):
        ue = UnifiedEvidence(run_id="run-1", metadata={"project": "PROJ-1"})
        d = ue.to_dict()
        assert d["run_id"] == "run-1"
        assert d["metadata"]["project"] == "PROJ-1"


class TestBuildWebEvidence:
    def test_from_empty_dir(self):
        with tempfile.TemporaryDirectory() as tmp:
            ev = build_web_evidence(tmp)
            assert ev.screenshots == []
            assert ev.self_reflect_path is None

    def test_with_screenshots(self):
        with tempfile.TemporaryDirectory() as tmp:
            ss_dir = Path(tmp) / "screenshots"
            ss_dir.mkdir()
            (ss_dir / "01_login.png").write_bytes(b"png")
            (ss_dir / "02_home.png").write_bytes(b"png")

            ev = build_web_evidence(tmp)
            assert len(ev.screenshots) == 2

    def test_with_self_reflect(self):
        with tempfile.TemporaryDirectory() as tmp:
            sr = Path(tmp) / "self_reflect_result.json"
            sr.write_text('{"status": "passed"}', encoding="utf-8")

            ev = build_web_evidence(tmp)
            assert ev.self_reflect_path == str(sr)


class TestBuildApiEvidence:
    def test_basic(self):
        ev = build_api_evidence(
            response_body={"code": 0, "data": {}},
            status_code=200,
            db_records=[{"id": 1, "name": "test"}],
        )
        assert ev.status_code == 200
        assert ev.response_body == {"code": 0, "data": {}}
        assert len(ev.db_records) == 1


class TestBuildEvidencePackage:
    def test_creates_zip(self):
        with tempfile.TemporaryDirectory() as tmp:
            web_ev = WebEvidence(
                screenshots=[],
                self_reflect_path=None,
            )
            api_ev = ApiEvidence(
                response_body={"code": 0},
            )

            zip_path = build_evidence_package(
                run_id="run-test",
                web_ev=web_ev,
                api_ev=api_ev,
                output_dir=tmp,
            )
            assert zip_path.exists()
            assert zip_path.suffix == ".zip"

    def test_contains_evidence_json(self):
        with tempfile.TemporaryDirectory() as tmp:
            zip_path = build_evidence_package(
                run_id="run-test",
                web_ev=WebEvidence(),
                api_ev=ApiEvidence(),
                output_dir=tmp,
            )
            import zipfile
            with zipfile.ZipFile(zip_path, "r") as zf:
                assert "evidence.json" in zf.namelist()
