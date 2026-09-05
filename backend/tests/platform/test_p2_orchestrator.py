# -*- coding: utf-8 -*-
"""P2-4 测试：编排入口。"""

import pytest

from app.platform.orchestrator import (
    OrchestratorConfig,
    select_scenarios,
    TrackResult,
    DualTrackRunner,
    aggregate_results,
    run_orchestration,
)
from app.platform.report_templates import TestResult, WriteBackRecord, EvidenceRef


class TestSelectScenarios:
    def test_no_criteria(self):
        cases = [
            {"id": "TC-001", "keyword": "正向"},
            {"id": "TC-002", "keyword": "反向"},
        ]
        result = select_scenarios(cases)
        assert len(result) == 2

    def test_by_keyword(self):
        cases = [
            {"id": "TC-001", "keyword": "正向"},
            {"id": "TC-002", "keyword": "反向"},
            {"id": "TC-003", "keyword": "边界"},
        ]
        result = select_scenarios(cases, {"keywords": ["正向", "边界"]})
        assert len(result) == 2
        assert result[0]["id"] == "TC-001"
        assert result[1]["id"] == "TC-003"

    def test_by_module(self):
        cases = [
            {"id": "TC-001", "module": "登录"},
            {"id": "TC-002", "module": "支付"},
        ]
        result = select_scenarios(cases, {"modules": ["登录"]})
        assert len(result) == 1
        assert result[0]["id"] == "TC-001"

    def test_executable_only(self):
        cases = [
            {"id": "TC-001", "executable_flag": True},
            {"id": "TC-002", "executable_flag": False},
        ]
        result = select_scenarios(cases, {"executable_only": True})
        assert len(result) == 1
        assert result[0]["id"] == "TC-001"

    def test_by_ids(self):
        cases = [
            {"id": "TC-001"},
            {"id": "TC-002"},
            {"id": "TC-003"},
        ]
        result = select_scenarios(cases, {"ids": ["TC-001", "TC-003"]})
        assert len(result) == 2

    def test_combined_criteria(self):
        cases = [
            {"id": "TC-001", "keyword": "正向", "module": "登录", "executable_flag": True},
            {"id": "TC-002", "keyword": "反向", "module": "登录", "executable_flag": True},
            {"id": "TC-003", "keyword": "正向", "module": "支付", "executable_flag": True},
        ]
        result = select_scenarios(cases, {
            "keywords": ["正向"],
            "modules": ["登录"],
            "executable_only": True,
        })
        assert len(result) == 1
        assert result[0]["id"] == "TC-001"


class TestTrackResult:
    def test_to_dict(self):
        tr = TrackResult(
            track="api",
            status="completed",
            records=[WriteBackRecord("TC-001", "T1", TestResult.PASSED)],
        )
        d = tr.to_dict()
        assert d["track"] == "api"
        assert d["status"] == "completed"
        assert len(d["records"]) == 1


class TestDualTrackRunner:
    def test_api_skipped_when_not_enabled(self):
        config = OrchestratorConfig(project="PROJ-1", run_api=False)
        runner = DualTrackRunner(config)
        result = runner.run_api([])
        assert result.status == "skipped"
        assert "未启用" in result.error

    def test_web_skipped_when_not_enabled(self):
        config = OrchestratorConfig(project="PROJ-1", run_web=False)
        runner = DualTrackRunner(config)
        result = runner.run_web([])
        assert result.status == "skipped"

    def test_api_skipped_without_live(self, monkeypatch):
        monkeypatch.delenv("LIVE_API", raising=False)
        monkeypatch.delenv("LEGACY_LIVE_API", raising=False)
        config = OrchestratorConfig(project="PROJ-1", run_api=True)
        runner = DualTrackRunner(config)
        result = runner.run_api([])
        assert result.status == "skipped"
        assert "LIVE_API" in result.error

    def test_run_both_tracks(self):
        config = OrchestratorConfig(project="PROJ-1", run_api=True, run_web=True)
        runner = DualTrackRunner(config)
        results = runner.run([])
        assert len(results) == 2

    def test_run_single_track(self):
        config = OrchestratorConfig(project="PROJ-1", run_api=True, run_web=False)
        runner = DualTrackRunner(config)
        results = runner.run([])
        assert len(results) == 1
        assert results[0].track == "api"


class TestAggregateResults:
    def test_empty(self):
        config = OrchestratorConfig(project="PROJ-1")
        report = aggregate_results([], config)
        assert report["header"]["project"] == "PROJ-1"

    def test_with_records(self):
        config = OrchestratorConfig(project="PROJ-1")
        track_results = [
            TrackResult(
                track="api",
                status="completed",
                records=[
                    WriteBackRecord("TC-001", "查询", TestResult.PASSED, runner="api"),
                    WriteBackRecord("TC-002", "写入", TestResult.DEFECT, runner="api"),
                ],
            ),
        ]
        report = aggregate_results(track_results, config)
        summary = report["sections"][0]["summary"]
        assert summary["count"] == 2


class TestRunOrchestration:
    def test_full_flow(self, monkeypatch):
        monkeypatch.delenv("LIVE_API", raising=False)
        cases = [
            {"id": "TC-001", "title": "查询", "keyword": "正向", "executable_flag": True},
            {"id": "TC-002", "title": "错误密码", "keyword": "反向", "executable_flag": True},
        ]
        config = OrchestratorConfig(
            project="PROJ-1",
            run_api=True,
            run_web=False,
        )
        result = run_orchestration(cases, config)
        assert result["project"] == "PROJ-1"
        assert result["scenarios_selected"] == 2
        assert "report" in result
        assert "track_results" in result

    def test_with_criteria(self, monkeypatch):
        monkeypatch.delenv("LIVE_API", raising=False)
        cases = [
            {"id": "TC-001", "keyword": "正向", "module": "A"},
            {"id": "TC-002", "keyword": "反向", "module": "B"},
        ]
        config = OrchestratorConfig(
            project="PROJ-1",
            run_api=True,
            run_web=False,
            selected_layers=["api"],
        )
        # 通过 criteria 过滤
        result = run_orchestration(cases, config)
        assert result["scenarios_selected"] == 2  # 无 criteria 不过滤
