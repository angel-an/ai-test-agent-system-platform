# -*- coding: utf-8 -*-
"""P0-3: 统一报告模板 — 四类结果、证据引用、回写格式。

目标：让结果回写有统一口径，生成/API/Web 报告可以对齐，
回写字段可被直接复用。
"""

from __future__ import annotations

from enum import Enum
from typing import Any, Optional


# ---------------------------------------------------------------------------
# 四类结果枚举
# ---------------------------------------------------------------------------

class TestResult(str, Enum):
    """统一四类测试结果"""
    PASSED = "通过"
    PENDING_REVIEW = "待复核"
    PENDING_CONFIRM = "待确认"
    DEFECT = "缺陷"


class TestResultCode(str, Enum):
    """结果代码（机器可读）"""
    PASSED = "PASSED"
    PENDING_REVIEW = "PENDING_REVIEW"
    PENDING_CONFIRM = "PENDING_CONFIRM"
    DEFECT = "DEFECT"


RESULT_CODE_MAP: dict[TestResult, TestResultCode] = {
    TestResult.PASSED: TestResultCode.PASSED,
    TestResult.PENDING_REVIEW: TestResultCode.PENDING_REVIEW,
    TestResult.PENDING_CONFIRM: TestResultCode.PENDING_CONFIRM,
    TestResult.DEFECT: TestResultCode.DEFECT,
}


# ---------------------------------------------------------------------------
# 证据引用
# ---------------------------------------------------------------------------

class EvidenceRef:
    """证据引用位 — 统一证据标识。

    支持多种证据类型：
    - screenshot: 截图路径（Web）
    - api_response: API 返回体摘要
    - db_record: DB / 流水记录
    - log: 日志片段
    - pytest_report: pytest 报告路径
    """

    def __init__(
        self,
        evidence_type: str,
        path: Optional[str] = None,
        content: Optional[str] = None,
        description: Optional[str] = None,
    ):
        self.evidence_type = evidence_type  # screenshot | api_response | db_record | log | pytest_report
        self.path = path
        self.content = content  # 摘要或片段
        self.description = description

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {"type": self.evidence_type}
        if self.path:
            d["path"] = self.path
        if self.content:
            d["content"] = self.content
        if self.description:
            d["description"] = self.description
        return d


# ---------------------------------------------------------------------------
# 回写记录
# ---------------------------------------------------------------------------

class WriteBackRecord:
    """结果回写记录 — 单条用例的执行结果。"""

    def __init__(
        self,
        case_id: str,
        case_title: str,
        result: TestResult,
        executed_at: Optional[str] = None,
        duration_ms: Optional[int] = None,
        evidence: Optional[list[EvidenceRef]] = None,
        error_message: Optional[str] = None,
        runner: Optional[str] = None,  # web | api
    ):
        self.case_id = case_id
        self.case_title = case_title
        self.result = result
        self.executed_at = executed_at
        self.duration_ms = duration_ms
        self.evidence = evidence or []
        self.error_message = error_message
        self.runner = runner

    def to_dict(self) -> dict[str, Any]:
        return {
            "case_id": self.case_id,
            "case_title": self.case_title,
            "result": self.result.value,
            "result_code": RESULT_CODE_MAP.get(self.result, self.result.value).value,
            "executed_at": self.executed_at,
            "duration_ms": self.duration_ms,
            "evidence": [e.to_dict() for e in self.evidence],
            "error_message": self.error_message,
            "runner": self.runner,
        }


# ---------------------------------------------------------------------------
# 报告结构
# ---------------------------------------------------------------------------

class ReportHeader:
    """报告头部 — 项目信息、执行概览。"""

    def __init__(
        self,
        project: str,
        report_type: str,
        generated_at: Optional[str] = None,
        version: Optional[str] = None,
        executor: Optional[str] = None,
    ):
        self.project = project
        self.report_type = report_type
        self.generated_at = generated_at
        self.version = version
        self.executor = executor

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {
            "project": self.project,
            "report_type": self.report_type,
        }
        if self.generated_at:
            d["generated_at"] = self.generated_at
        if self.version:
            d["version"] = self.version
        if self.executor:
            d["executor"] = self.executor
        return d


class ReportSection:
    """报告章节 — 按模块/层级分组的结果。"""

    def __init__(
        self,
        title: str,
        records: list[WriteBackRecord],
        summary: Optional[dict[str, Any]] = None,
    ):
        self.title = title
        self.records = records
        self.summary = summary or {}

    def to_dict(self) -> dict[str, Any]:
        return {
            "title": self.title,
            "records": [r.to_dict() for r in self.records],
            "summary": self.summary,
        }


class ReportFooter:
    """报告尾部 — 结论与建议。"""

    def __init__(
        self,
        conclusion: str,
        recommendations: Optional[list[str]] = None,
        next_steps: Optional[list[str]] = None,
    ):
        self.conclusion = conclusion
        self.recommendations = recommendations or []
        self.next_steps = next_steps or []

    def to_dict(self) -> dict[str, Any]:
        return {
            "conclusion": self.conclusion,
            "recommendations": self.recommendations,
            "next_steps": self.next_steps,
        }


class ExecutionReport:
    """统一执行报告 — 摘要、详情、结论三段式。"""

    def __init__(
        self,
        header: ReportHeader,
        sections: list[ReportSection],
        footer: ReportFooter,
    ):
        self.header = header
        self.sections = sections
        self.footer = footer

    def to_dict(self) -> dict[str, Any]:
        return {
            "header": self.header.to_dict(),
            "sections": [s.to_dict() for s in self.sections],
            "footer": self.footer.to_dict(),
        }

    def summary(self) -> dict[str, Any]:
        """生成统计摘要"""
        total = 0
        passed = 0
        pending_review = 0
        pending_confirm = 0
        defect = 0
        for sec in self.sections:
            for r in sec.records:
                total += 1
                if r.result == TestResult.PASSED:
                    passed += 1
                elif r.result == TestResult.PENDING_REVIEW:
                    pending_review += 1
                elif r.result == TestResult.PENDING_CONFIRM:
                    pending_confirm += 1
                elif r.result == TestResult.DEFECT:
                    defect += 1
        return {
            "total": total,
            "passed": passed,
            "pending_review": pending_review,
            "pending_confirm": pending_confirm,
            "defect": defect,
            "pass_rate": round(passed / total, 4) if total > 0 else 0.0,
        }


# ---------------------------------------------------------------------------
# 报告生成函数
# ---------------------------------------------------------------------------

def build_execution_report(
    project: str,
    report_type: str,
    run_results: list[dict[str, Any]],
    version: Optional[str] = None,
    executor: Optional[str] = None,
    conclusion: Optional[str] = None,
) -> ExecutionReport:
    """从运行结果构建统一执行报告。

    Args:
        project: 项目标识符
        report_type: 报告类型（如 API执行报告、Web回归报告）
        run_results: 运行结果列表，每项为 dict，至少含 case_id, case_title, result, ...
        version: 版本号
        executor: 执行者
        conclusion: 结论（默认自动推导）
    """
    from datetime import datetime, timezone

    now = datetime.now(timezone.utc).isoformat()

    header = ReportHeader(
        project=project,
        report_type=report_type,
        generated_at=now,
        version=version,
        executor=executor,
    )

    # 按 runner 分组（web / api）
    web_records: list[WriteBackRecord] = []
    api_records: list[WriteBackRecord] = []

    for r in run_results:
        result_str = r.get("result", "待确认")
        try:
            result = TestResult(result_str)
        except ValueError:
            result = TestResult.PENDING_CONFIRM

        record = WriteBackRecord(
            case_id=r.get("case_id", ""),
            case_title=r.get("case_title", ""),
            result=result,
            executed_at=r.get("executed_at"),
            duration_ms=r.get("duration_ms"),
            evidence=[EvidenceRef(evidence_type=e.get("type", ""), path=e.get("path"), content=e.get("content"), description=e.get("description")) for e in r.get("evidence", [])],
            error_message=r.get("error_message"),
            runner=r.get("runner"),
        )
        if record.runner == "web":
            web_records.append(record)
        elif record.runner == "api":
            api_records.append(record)
        else:
            # 未指定 runner，按证据类型推断
            has_screenshot = any(e.evidence_type == "screenshot" for e in record.evidence)
            record.runner = "web" if has_screenshot else "api"
            if record.runner == "web":
                web_records.append(record)
            else:
                api_records.append(record)

    sections: list[ReportSection] = []
    if web_records:
        sections.append(ReportSection(
            title="Web 执行结果",
            records=web_records,
            summary={"count": len(web_records)},
        ))
    if api_records:
        sections.append(ReportSection(
            title="API 执行结果",
            records=api_records,
            summary={"count": len(api_records)},
        ))

    # 自动推导结论
    if conclusion is None:
        stats = {"total": 0, "passed": 0, "defect": 0}
        for sec in sections:
            for r in sec.records:
                stats["total"] += 1
                if r.result == TestResult.PASSED:
                    stats["passed"] += 1
                elif r.result == TestResult.DEFECT:
                    stats["defect"] += 1
        if stats["total"] == 0:
            conclusion = "无执行记录"
        elif stats["defect"] > 0:
            conclusion = f"发现 {stats['defect']} 项缺陷，需修复后复验"
        elif stats["passed"] == stats["total"]:
            conclusion = "全部通过"
        else:
            conclusion = f"通过 {stats['passed']}/{stats['total']}，部分待复核"

    footer = ReportFooter(
        conclusion=conclusion,
        recommendations=[],
        next_steps=[],
    )

    return ExecutionReport(header=header, sections=sections, footer=footer)


def render_report_markdown(report: ExecutionReport) -> str:
    """将执行报告渲染为 Markdown 格式。"""
    lines: list[str] = []
    h = report.header
    lines.append(f"# {h.report_type} — {h.project}")
    lines.append("")
    if h.version:
        lines.append(f"**版本**: {h.version}")
    if h.generated_at:
        lines.append(f"**生成时间**: {h.generated_at}")
    if h.executor:
        lines.append(f"**执行者**: {h.executor}")
    lines.append("")

    # 摘要
    summary = report.summary()
    lines.append("## 执行摘要")
    lines.append("")
    lines.append(f"- 总计: {summary['total']}")
    lines.append(f"- 通过: {summary['passed']}")
    lines.append(f"- 待复核: {summary['pending_review']}")
    lines.append(f"- 待确认: {summary['pending_confirm']}")
    lines.append(f"- 缺陷: {summary['defect']}")
    lines.append(f"- 通过率: {summary['pass_rate']*100:.1f}%")
    lines.append("")

    # 详情
    for sec in report.sections:
        lines.append(f"## {sec.title}")
        lines.append("")
        for r in sec.records:
            icon = "✅" if r.result == TestResult.PASSED else "❌" if r.result == TestResult.DEFECT else "⏳"
            lines.append(f"### {icon} {r.case_id}: {r.case_title}")
            lines.append(f"- **结果**: {r.result.value}")
            if r.duration_ms:
                lines.append(f"- **耗时**: {r.duration_ms}ms")
            if r.error_message:
                lines.append(f"- **错误**: {r.error_message}")
            for ev in r.evidence:
                lines.append(f"- **证据**({ev.evidence_type}): {ev.path or ev.content or ev.description}")
            lines.append("")

    # 结论
    lines.append("## 结论")
    lines.append("")
    lines.append(report.footer.conclusion)
    lines.append("")

    return "\n".join(lines)
