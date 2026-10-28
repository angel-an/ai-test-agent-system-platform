"""Allure 报告生成必须"失败也说原因"的门禁测试（2026-10-28）。

背景：``_generate_allure_report`` 历史上把三种完全不同的情况都折叠成
``return None`` —— 本机没装 Allure CLI、CLI 报错、打包目录为空。
结果是 run 详情里 report_path 为空、排障时只能猜；更糟的是空目录也会
被上传成**空 zip**，报告"有"但打不开。

本文件锁住：
1. 没有 allure-results / 空目录 → no_results；
2. PATH 里没有 allure → cli_missing（且不调用 subprocess）；
3. CLI 退出码非 0 → generate_failed，带 stderr 尾巴；
4. 返回 0 但没产出 index.html → empty_output，**不上传空 zip**；
5. 正常产出 → generated + 上传对象名；
6. 失败状态会被写进 run.execution_config["allure_report"]。
"""
from __future__ import annotations

import subprocess
import sys
import types
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.services import api_test_executor as executor_mod
from app.services.api_test_executor import APITestExecutor


@pytest.fixture()
def executor() -> APITestExecutor:
    return APITestExecutor.__new__(APITestExecutor)


def _make_results(work_dir: Path, *, empty: bool = False) -> Path:
    results = work_dir / "allure-results"
    results.mkdir(parents=True)
    if not empty:
        (results / "a-result.json").write_text("{}", encoding="utf-8")
    return results


@pytest.mark.asyncio
async def test_missing_results_dir_reports_no_results(executor, tmp_path):
    path, info = await executor._generate_allure_report(run_id=None, work_dir=tmp_path)
    assert path is None
    assert info["status"] == executor_mod.REPORT_STATUS_NO_RESULTS
    assert "allure-results" in info["reason"]


@pytest.mark.asyncio
async def test_empty_results_dir_reports_no_results(executor, tmp_path):
    _make_results(tmp_path, empty=True)
    path, info = await executor._generate_allure_report(run_id=None, work_dir=tmp_path)
    assert path is None
    assert info["status"] == executor_mod.REPORT_STATUS_NO_RESULTS
    assert "空" in info["reason"]


@pytest.mark.asyncio
async def test_cli_missing_is_not_silent(executor, tmp_path, monkeypatch):
    _make_results(tmp_path)
    monkeypatch.setattr(executor_mod, "_resolve_allure_cli", lambda: None)

    called = {"subprocess": False}

    def _boom(*a, **k):
        called["subprocess"] = True
        raise AssertionError("CLI 缺失时不该再尝试执行 subprocess")

    monkeypatch.setattr(executor_mod.subprocess, "run", _boom)

    path, info = await executor._generate_allure_report(run_id=None, work_dir=tmp_path)
    assert path is None
    assert info["status"] == executor_mod.REPORT_STATUS_CLI_MISSING
    assert "allure" in info["reason"]
    assert called["subprocess"] is False


@pytest.mark.asyncio
async def test_nonzero_exit_records_stderr_tail(executor, tmp_path, monkeypatch):
    _make_results(tmp_path)
    monkeypatch.setattr(executor_mod, "_resolve_allure_cli", lambda: "/usr/bin/allure")

    def _fail(*a, **k):
        return subprocess.CompletedProcess(
            args=["allure"], returncode=2, stdout=b"", stderr=b"boom: cannot parse results"
        )

    monkeypatch.setattr(executor_mod.subprocess, "run", _fail)

    path, info = await executor._generate_allure_report(run_id=None, work_dir=tmp_path)
    assert path is None
    assert info["status"] == executor_mod.REPORT_STATUS_GENERATE_FAILED
    assert "2" in info["reason"]
    assert "cannot parse results" in info["detail"]


@pytest.mark.asyncio
async def test_zero_exit_without_index_html_does_not_upload_empty_zip(executor, tmp_path, monkeypatch):
    _make_results(tmp_path)
    monkeypatch.setattr(executor_mod, "_resolve_allure_cli", lambda: "/usr/bin/allure")
    monkeypatch.setattr(
        executor_mod.subprocess,
        "run",
        lambda *a, **k: subprocess.CompletedProcess(args=["allure"], returncode=0, stdout=b"", stderr=b""),
    )

    uploaded: list[str] = []
    monkeypatch.setattr(
        executor_mod.MinIOClient,
        "upload_bytes",
        staticmethod(lambda object_name, data, content_type=None: uploaded.append(object_name)),
    )

    path, info = await executor._generate_allure_report(run_id=None, work_dir=tmp_path)
    assert path is None
    assert info["status"] == executor_mod.REPORT_STATUS_EMPTY_OUTPUT
    assert uploaded == []


@pytest.mark.asyncio
async def test_generated_report_is_uploaded(executor, tmp_path, monkeypatch):
    _make_results(tmp_path)
    monkeypatch.setattr(executor_mod, "_resolve_allure_cli", lambda: "/usr/bin/allure")

    def _ok(*a, **k):
        # 模拟 CLI 产出：写出 index.html
        (tmp_path / "allure-report").mkdir(exist_ok=True)
        (tmp_path / "allure-report" / "index.html").write_text("<html/>", encoding="utf-8")
        return subprocess.CompletedProcess(args=["allure"], returncode=0, stdout=b"", stderr=b"")

    monkeypatch.setattr(executor_mod.subprocess, "run", _ok)

    uploaded: dict[str, bytes] = {}
    monkeypatch.setattr(
        executor_mod.MinIOClient,
        "upload_bytes",
        staticmethod(
            lambda object_name, data, content_type=None: uploaded.setdefault(object_name, data)
        ),
    )

    run_id = "11111111-1111-1111-1111-111111111111"
    path, info = await executor._generate_allure_report(run_id=run_id, work_dir=tmp_path)
    assert info["status"] == executor_mod.REPORT_STATUS_GENERATED
    assert path == f"api-test-reports/{run_id}/allure-report.zip"
    assert path in uploaded and uploaded[path]


@pytest.mark.asyncio
async def test_failed_status_is_stamped_into_run(monkeypatch):
    """失败原因要落进 run.execution_config，不能只写日志。"""
    ex = APITestExecutor.__new__(APITestExecutor)
    stamped: dict = {}

    class _Repo:
        async def get_by_id(self, _run_id):
            return SimpleNamespace(execution_config={"base_url": "https://x"})

        async def update(self, row, **kwargs):
            stamped.update(kwargs)
            return row

    ex.api_test_run_repo = _Repo()
    await ex._stamp_report_status(
        "run-1", {"status": executor_mod.REPORT_STATUS_CLI_MISSING, "reason": "没装 allure", "detail": ""}
    )

    cfg = stamped["execution_config"]
    assert cfg["base_url"] == "https://x"  # 不覆盖既有配置
    assert cfg["allure_report"]["status"] == "cli_missing"
    assert cfg["allure_report"]["reason"] == "没装 allure"


@pytest.mark.asyncio
async def test_success_does_not_stamp(monkeypatch):
    ex = APITestExecutor.__new__(APITestExecutor)
    called = {"update": False}

    class _Repo:
        async def get_by_id(self, _run_id):
            called["update"] = True
            return None

        async def update(self, row, **kwargs):
            called["update"] = True

    ex.api_test_run_repo = _Repo()
    await ex._stamp_report_status("run-1", {"status": executor_mod.REPORT_STATUS_GENERATED})
    assert called["update"] is False
