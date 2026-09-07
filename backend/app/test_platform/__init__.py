# -*- coding: utf-8 -*-
"""平台测试优化模块 — P0 基座统一 / P1 生成质量 / P2 执行层。"""

from .testcase_contract import (
    TestCaseContract,
    TestCaseStep,
    GenerationResult,
    validate_contract,
    validate_generation_result,
    TestCaseType,
    TestCasePriority,
    TestCaseKeyword,
    TestCaseStatus,
    RiskLevel,
    CaseKind,
)

from .directory_layout import (
    DirectoryLayout,
    NamingConvention,
    generate_filename,
    ensure_platform_dirs,
)

from .report_templates import (
    TestResult,
    TestResultCode,
    EvidenceRef,
    WriteBackRecord,
    ReportHeader,
    ReportSection,
    ReportFooter,
    ExecutionReport,
    build_execution_report,
    render_report_markdown,
)

from .execution_gates import (
    GateConfig,
    ExecutionGate,
    should_skip_live,
    should_run_risky,
    check_live_gate,
    check_risky_gate,
    get_gate_status,
)

from .runtime_context import (
    RunContext,
    create_run_context,
)

__all__ = [
    # P0-1 生成契约
    "TestCaseContract",
    "TestCaseStep",
    "GenerationResult",
    "validate_contract",
    "validate_generation_result",
    "TestCaseType",
    "TestCasePriority",
    "TestCaseKeyword",
    "TestCaseStatus",
    "RiskLevel",
    "CaseKind",
    # P0-2 目录规范
    "DirectoryLayout",
    "NamingConvention",
    "generate_filename",
    "ensure_platform_dirs",
    # P0-3 报告模板
    "TestResult",
    "TestResultCode",
    "EvidenceRef",
    "WriteBackRecord",
    "ReportHeader",
    "ReportSection",
    "ReportFooter",
    "ExecutionReport",
    "build_execution_report",
    "render_report_markdown",
    # P0-4 统一门控
    "GateConfig",
    "ExecutionGate",
    "should_skip_live",
    "should_run_risky",
    "check_live_gate",
    "check_risky_gate",
    "get_gate_status",
    # P0-5 统一运行上下文
    "RunContext",
    "create_run_context",
]
