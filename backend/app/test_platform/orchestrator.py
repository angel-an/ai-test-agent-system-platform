# -*- coding: utf-8 -*-
"""P2-4: 编排入口 — 一条指令选场景、自动分轨执行、自动聚合回写。

目标：支持一条指令驱动双轨执行，默认零写入。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional

from app.test_platform.execution_gates import ExecutionGate, GateConfig
from app.test_platform.report_templates import (
    TestResult,
    WriteBackRecord,
    build_execution_report,
)


# ---------------------------------------------------------------------------
# 编排配置
# ---------------------------------------------------------------------------

@dataclass
class OrchestratorConfig:
    """编排器配置"""

    project: str
    # 场景选择
    selected_layers: list[str] = field(default_factory=list)  # api/web
    selected_scenarios: Optional[list[str]] = None  # 指定场景 ID 列表
    # 分轨开关
    run_api: bool = True
    run_web: bool = True
    # 门控
    gate_config: Optional[GateConfig] = None
    # 报告
    report_version: Optional[str] = None
    report_executor: Optional[str] = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "project": self.project,
            "selected_layers": self.selected_layers,
            "selected_scenarios": self.selected_scenarios,
            "run_api": self.run_api,
            "run_web": self.run_web,
            "report_version": self.report_version,
        }


# ---------------------------------------------------------------------------
# 场景选择
# ---------------------------------------------------------------------------

def select_scenarios(
    cases: list[dict[str, Any]],
    criteria: Optional[dict[str, Any]] = None,
) -> list[dict[str, Any]]:
    """按条件筛选场景。

    Args:
        cases: 用例列表
        criteria: 筛选条件，支持：
            - keywords: list[str] 关键词过滤（正向/反向/边界）
            - modules: list[str] 模块过滤
            - executable_only: bool 仅可执行
            - ids: list[str] 指定 ID 列表

    Returns:
        筛选后的用例列表
    """
    if not criteria:
        return cases

    result = cases

    # 按关键词过滤
    if "keywords" in criteria:
        allowed = set(criteria["keywords"])
        result = [c for c in result if c.get("keyword") in allowed]

    # 按模块过滤
    if "modules" in criteria:
        allowed = set(criteria["modules"])
        result = [c for c in result if c.get("module") in allowed]

    # 仅可执行
    if criteria.get("executable_only"):
        result = [c for c in result if c.get("executable_flag")]

    # 按 ID 过滤
    if "ids" in criteria:
        allowed = set(criteria["ids"])
        result = [c for c in result if c.get("id") in allowed]

    return result


# ---------------------------------------------------------------------------
# 分轨执行
# ---------------------------------------------------------------------------

@dataclass
class TrackResult:
    """单轨执行结果"""

    track: str  # api | web
    status: str  # completed | failed | skipped
    records: list[WriteBackRecord] = field(default_factory=list)
    error: Optional[str] = None
    evidence_paths: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "track": self.track,
            "status": self.status,
            "records": [r.to_dict() for r in self.records],
            "error": self.error,
            "evidence_paths": self.evidence_paths,
        }


class DualTrackRunner:
    """双轨执行器 — API + Web"""

    def __init__(self, config: OrchestratorConfig):
        self.config = config
        self.gate = ExecutionGate(config.gate_config)

    def run_api(self, scenarios: list[dict[str, Any]]) -> TrackResult:
        """执行 API 轨"""
        if not self.config.run_api:
            return TrackResult(track="api", status="skipped", error="API 轨未启用")

        allowed, reasons = self.gate.check_all("api")
        if not allowed:
            return TrackResult(track="api", status="skipped", error="; ".join(reasons))

        # 抽象接口：实际执行由调用方注入
        return TrackResult(
            track="api",
            status="completed",
            records=[],
            error="DualTrackRunner.run_api 为抽象接口，需子类实现具体执行逻辑",
        )

    def run_web(self, scenarios: list[dict[str, Any]]) -> TrackResult:
        """执行 Web 轨"""
        if not self.config.run_web:
            return TrackResult(track="web", status="skipped", error="Web 轨未启用")

        allowed, reasons = self.gate.check_all("web")
        if not allowed:
            return TrackResult(track="web", status="skipped", error="; ".join(reasons))

        return TrackResult(
            track="web",
            status="completed",
            records=[],
            error="DualTrackRunner.run_web 为抽象接口，需子类实现具体执行逻辑",
        )

    def run(self, scenarios: list[dict[str, Any]]) -> list[TrackResult]:
        """执行双轨"""
        results = []
        if self.config.run_api:
            results.append(self.run_api(scenarios))
        if self.config.run_web:
            results.append(self.run_web(scenarios))
        return results


# ---------------------------------------------------------------------------
# 聚合回写
# ---------------------------------------------------------------------------

def aggregate_results(
    track_results: list[TrackResult],
    config: OrchestratorConfig,
) -> dict[str, Any]:
    """聚合双轨结果为统一报告。

    Args:
        track_results: 各轨执行结果
        config: 编排配置

    Returns:
        统一执行报告（dict 格式）
    """
    all_records: list[dict[str, Any]] = []
    for tr in track_results:
        for r in tr.records:
            all_records.append({
                "case_id": r.case_id,
                "case_title": r.case_title,
                "result": r.result.value,
                "runner": tr.track,
                "executed_at": r.executed_at,
                "duration_ms": r.duration_ms,
                "evidence": [e.to_dict() for e in r.evidence],
                "error_message": r.error_message,
            })

    report = build_execution_report(
        project=config.project,
        report_type="双轨执行报告",
        run_results=all_records,
        version=config.report_version,
        executor=config.report_executor,
    )

    return report.to_dict()


# ---------------------------------------------------------------------------
# 统一编排入口
# ---------------------------------------------------------------------------

def run_orchestration(
    cases: list[dict[str, Any]],
    config: OrchestratorConfig,
) -> dict[str, Any]:
    """统一编排入口 — 选场景 → 分轨执行 → 聚合回写。

    Args:
        cases: 全部用例列表
        config: 编排配置

    Returns:
        包含执行结果和报告的字典
    """
    # 1. 场景选择
    # 统一字段契约：config.to_dict() 中的 selected_scenarios 映射为 select_scenarios 期望的 ids
    criteria = config.to_dict()
    if criteria.get("selected_scenarios") and "ids" not in criteria:
        criteria["ids"] = criteria["selected_scenarios"]
    scenarios = select_scenarios(cases, criteria)

    # 2. 分轨执行
    runner = DualTrackRunner(config)
    track_results = runner.run(scenarios)

    # 3. 聚合回写
    report = aggregate_results(track_results, config)

    return {
        "project": config.project,
        "scenarios_selected": len(scenarios),
        "track_results": [tr.to_dict() for tr in track_results],
        "report": report,
    }
