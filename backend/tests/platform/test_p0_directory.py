# -*- coding: utf-8 -*-
"""P0-2 测试：统一目录和命名规范。"""

import os
import tempfile
from pathlib import Path

import pytest

from app.platform.directory_layout import (
    DirectoryLayout,
    NamingConvention,
    generate_filename,
    ensure_platform_dirs,
    _safe_name,
    _now_str,
)


class TestSafeName:
    def test_removes_illegal_chars(self):
        assert _safe_name("a/b:c") == "a_b_c"
        assert _safe_name('a"b<c>d|e') == "a_b_c_d_e"

    def test_collapses_underscores(self):
        assert _safe_name("a___b") == "a_b"

    def test_strips_ends(self):
        assert _safe_name("_abc_") == "abc"

    def test_empty_fallback(self):
        assert _safe_name("") == "untitled"


class TestDirectoryLayout:
    def test_generated_dir(self):
        with tempfile.TemporaryDirectory() as tmp:
            layout = DirectoryLayout(tmp)
            d = layout.generated_dir("PROJ-1", "v1.0")
            assert d.exists()
            assert "generated" in str(d)
            assert "PROJ-1" in str(d)
            assert "v1.0" in str(d)

    def test_reports_dir(self):
        with tempfile.TemporaryDirectory() as tmp:
            layout = DirectoryLayout(tmp)
            d = layout.reports_dir("PROJ-1")
            assert d.exists()
            assert "reports" in str(d)

    def test_evidence_dir(self):
        with tempfile.TemporaryDirectory() as tmp:
            layout = DirectoryLayout(tmp)
            d = layout.evidence_dir("run-abc123")
            assert d.exists()
            assert "evidence" in str(d)
            assert "run-abc123" in str(d)

    def test_scripts_dir(self):
        with tempfile.TemporaryDirectory() as tmp:
            layout = DirectoryLayout(tmp)
            d = layout.scripts_dir("PROJ-1")
            assert d.exists()
            assert "scripts" in str(d)

    def test_api_dir(self):
        with tempfile.TemporaryDirectory() as tmp:
            layout = DirectoryLayout(tmp)
            d = layout.api_dir("PROJ-1")
            assert d.exists()
            assert "api" in str(d)

    def test_web_dir(self):
        with tempfile.TemporaryDirectory() as tmp:
            layout = DirectoryLayout(tmp)
            d = layout.web_dir("PROJ-1")
            assert d.exists()
            assert "web" in str(d)

    def test_versions_dir(self):
        with tempfile.TemporaryDirectory() as tmp:
            layout = DirectoryLayout(tmp)
            d = layout.versions_dir("PROJ-1")
            assert d.exists()
            assert "versions" in str(d)

    def test_tasks_dir(self):
        with tempfile.TemporaryDirectory() as tmp:
            layout = DirectoryLayout(tmp)
            d = layout.tasks_dir("PROJ-1")
            assert d.exists()
            assert "tasks" in str(d)

    def test_generated_track_dir(self):
        with tempfile.TemporaryDirectory() as tmp:
            layout = DirectoryLayout(tmp)
            d = layout.generated_track_dir("PROJ-1", "api", "v1")
            assert d.exists()
            assert "api" in str(d)


class TestNamingConvention:
    def test_testcase_filename(self):
        name = NamingConvention.testcase_filename("PROJ-1", "SIT测试用例", "20260101_120000")
        assert name.startswith("PROJ-1_SIT测试用例_")
        assert name.endswith(".xlsx")

    def test_report_filename(self):
        name = NamingConvention.report_filename("PROJ-1", "API执行", "20260101_120000")
        assert "API执行" in name
        assert "报告" in name

    def test_evidence_package_filename(self):
        name = NamingConvention.evidence_package_filename("run-1", "20260101_120000")
        assert name.startswith("evidence_run-1_")
        assert name.endswith(".zip")

    def test_version_filename(self):
        name = NamingConvention.version_filename("PROJ-1", 3)
        assert name == "PROJ-1_v3.json"

    def test_task_filename(self):
        name = NamingConvention.task_filename("PROJ-1", "回归", "20260101_120000")
        assert "回归" in name
        assert name.endswith(".json")

    def test_script_filename(self):
        name = NamingConvention.script_filename("test_login", "py")
        assert name == "test_login.py"


class TestGenerateFilename:
    def test_basic(self):
        name = generate_filename("PROJ-1", "SIT测试用例", "20260101_120000", "xlsx")
        assert name.startswith("PROJ-1_SIT测试用例_")
        assert name.endswith(".xlsx")

    def test_default_timestamp(self):
        name = generate_filename("PROJ-1", "测试")
        assert name.startswith("PROJ-1_测试_")


class TestEnsurePlatformDirs:
    def test_creates_all_dirs(self):
        with tempfile.TemporaryDirectory() as tmp:
            layout = ensure_platform_dirs(tmp)
            for sub in ("generated", "reports", "evidence", "scripts", "versions", "tasks"):
                assert (layout.root / sub).exists()

    def test_uses_env_var(self, monkeypatch):
        with tempfile.TemporaryDirectory() as tmp:
            monkeypatch.setenv("PLATFORM_WORKSPACE_ROOT", tmp)
            layout = ensure_platform_dirs()
            assert str(layout.root) == tmp
