# -*- coding: utf-8 -*-
"""P1-3 测试：场景版本留档。"""

import tempfile
from pathlib import Path

import pytest

from app.test_platform.versioning import (
    VersionRecord,
    VersionManager,
    diff_versions,
    save_version,
    list_versions,
)


class TestVersionRecord:
    def test_to_dict(self):
        r = VersionRecord(
            version_no=1,
            timestamp="2026-01-01T00:00:00",
            cases_hash="abc123",
            cases_count=5,
        )
        d = r.to_dict()
        assert d["version_no"] == 1
        assert d["cases_hash"] == "abc123"

    def test_from_dict(self):
        d = {"version_no": 2, "timestamp": "2026-01-02T00:00:00", "cases_hash": "def456", "cases_count": 3}
        r = VersionRecord.from_dict(d)
        assert r.version_no == 2
        assert r.cases_count == 3


class TestVersionManager:
    def test_save_first_version(self):
        with tempfile.TemporaryDirectory() as tmp:
            vm = VersionManager("PROJ-1", tmp)
            cases = [
                {"id": "TC-001", "title": "测试登录"},
                {"id": "TC-002", "title": "测试注册"},
            ]
            record = vm.save_version(cases)
            assert record.version_no == 1
            assert record.cases_count == 2
            assert record.diff_from_prev == []  # 第一个版本无差异

    def test_save_second_version_with_diff(self):
        with tempfile.TemporaryDirectory() as tmp:
            vm = VersionManager("PROJ-1", tmp)
            cases_v1 = [{"id": "TC-001", "title": "测试登录"}]
            vm.save_version(cases_v1)

            cases_v2 = [
                {"id": "TC-001", "title": "测试登录"},
                {"id": "TC-003", "title": "测试退出"},
            ]
            record = vm.save_version(cases_v2)
            assert record.version_no == 2
            assert record.cases_count == 2
            assert len(record.diff_from_prev) > 0
            assert any("新增" in d for d in record.diff_from_prev)

    def test_list_versions(self):
        with tempfile.TemporaryDirectory() as tmp:
            vm = VersionManager("PROJ-1", tmp)
            vm.save_version([{"id": "TC-001", "title": "T1"}])
            vm.save_version([{"id": "TC-001", "title": "T1"}, {"id": "TC-002", "title": "T2"}])

            versions = vm.list_versions()
            assert len(versions) == 2
            assert versions[0].version_no == 1
            assert versions[1].version_no == 2

    def test_get_latest_version(self):
        with tempfile.TemporaryDirectory() as tmp:
            vm = VersionManager("PROJ-1", tmp)
            assert vm.get_latest_version() is None

            vm.save_version([{"id": "TC-001", "title": "T1"}])
            latest = vm.get_latest_version()
            assert latest is not None
            assert latest.version_no == 1

    def test_load_version(self):
        with tempfile.TemporaryDirectory() as tmp:
            vm = VersionManager("PROJ-1", tmp)
            cases = [{"id": "TC-001", "title": "T1"}]
            vm.save_version(cases)

            loaded = vm.load_version(1)
            assert loaded is not None
            assert len(loaded) == 1
            assert loaded[0]["id"] == "TC-001"

    def test_load_nonexistent_version(self):
        with tempfile.TemporaryDirectory() as tmp:
            vm = VersionManager("PROJ-1", tmp)
            assert vm.load_version(999) is None

    def test_same_content_hash(self):
        with tempfile.TemporaryDirectory() as tmp:
            vm = VersionManager("PROJ-1", tmp)
            cases = [{"id": "TC-001", "title": "T1"}]
            vm.save_version(cases)
            record = vm.save_version(cases)
            assert record.version_no == 2
            assert any("完全一致" in d for d in record.diff_from_prev)


class TestDiffVersions:
    def test_no_change(self):
        cases = [{"id": "TC-001", "title": "T1"}]
        diffs = diff_versions(cases, cases)
        assert any("无结构性差异" in d for d in diffs)

    def test_added_case(self):
        old = [{"id": "TC-001", "title": "T1"}]
        new = [{"id": "TC-001", "title": "T1"}, {"id": "TC-002", "title": "T2"}]
        diffs = diff_versions(old, new)
        assert any("新增" in d for d in diffs)

    def test_removed_case(self):
        old = [{"id": "TC-001", "title": "T1"}, {"id": "TC-002", "title": "T2"}]
        new = [{"id": "TC-001", "title": "T1"}]
        diffs = diff_versions(old, new)
        assert any("删除" in d for d in diffs)

    def test_modified_title(self):
        old = [{"id": "TC-001", "title": "旧标题"}]
        new = [{"id": "TC-001", "title": "新标题"}]
        diffs = diff_versions(old, new)
        assert any("修改" in d for d in diffs)

    def test_count_change(self):
        old = [{"id": "TC-001", "title": "T1"}]
        new = [{"id": "TC-001", "title": "T1"}, {"id": "TC-002", "title": "T2"}]
        diffs = diff_versions(old, new)
        assert any("总数" in d for d in diffs)


class TestSaveVersionConvenience:
    def test_save_and_list(self):
        with tempfile.TemporaryDirectory() as tmp:
            cases = [{"id": "TC-001", "title": "T1"}]
            record = save_version("PROJ-1", cases, tmp)
            assert record.version_no == 1

            versions = list_versions("PROJ-1", tmp)
            assert len(versions) == 1
