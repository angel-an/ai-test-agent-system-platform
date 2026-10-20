# -*- coding: utf-8 -*-
"""阶段一：测试用例生成硬门控。

门控语义（V1.2）：

- ``analysis.can_generate_testcases == False`` → 不进入生成链路，直接返回质量报告；
- 只有 ``status == Complete`` 才会被判定为可生成；
- 结构化结果解析失败 → 默认 Incomplete（由 :mod:`analysis` 保证），同样被门控拦下。

例外（B1，2026-09-24）：**字段规格类文档**（``applicable_profile == "field_spec"``，
即文档提供 ≥ :data:`FIELD_SPEC_MIN_FIELD_RULES` 条结构化字段规则）按适用档案放行：
这类文档没有 PRD 章节体（业务目标/操作流程天然不存在），
`status` 仍如实保留 Incomplete，但允许生成，依据记录在 ``profile_reason`` 与报告里。

本模块同时维护一次生成会话的状态机：
``not_started`` → ``generated`` / ``blocked``（被门控拦下时）。
"""

from __future__ import annotations

import hmac
import json
import logging
import os
import threading
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from app.test_platform.requirements.analysis import (
    FIELD_SPEC_PROFILE,
    PRDAnalysisResult,
    PRDQualityStatus,
    TestCaseGenerationStatus,
    render_analysis_report,
)


logger = logging.getLogger(__name__)

# 人工放行凭证：必须由服务端运维显式配置；未配置时人工放行完全关闭。
OVERRIDE_TOKEN_ENV = "REQUIREMENT_GATE_OVERRIDE_TOKEN"
# 审计落盘目录（默认 backend/workspace/test_platform_audit）
AUDIT_DIR_ENV = "REQUIREMENT_GATE_AUDIT_DIR"
AUDIT_FILENAME = "requirement_gate_overrides.jsonl"
# 放行原因最短长度（保证可审计，拒绝空泛理由）
MIN_OVERRIDE_REASON_LENGTH = 10

_audit_lock = threading.Lock()
_override_audit_log: list[dict[str, Any]] = []


class RequirementNotReadyError(RuntimeError):
    """需求未达到可生成状态时抛出（硬门控拒绝）。

    Attributes:
        analysis: 对应的需求分析结果
        reasons: 拒绝原因清单
    """

    def __init__(self, analysis: PRDAnalysisResult, reasons: Optional[list[str]] = None):
        self.analysis = analysis
        self.reasons = list(reasons or _reject_reasons(analysis))
        super().__init__(
            "需求未通过质量门控（"
            f"{analysis.status.value}）：" + "；".join(self.reasons)
        )


class GateOverrideNotAuthorizedError(PermissionError):
    """在未获服务端授权的情况下尝试人工放行。

    V1.2 要求 Incomplete / Blocked 不得生成正式测试用例；人工放行只能由服务端运维
    通过配置放行凭证启用，客户端不得凭一个布尔参数绕过门控。
    """


class GateOverrideAuditError(RuntimeError):
    """审计写入失败 —— 拒绝放行（fail-closed）。

    理由：若"放行成功但审计未落盘"，会形成不可追踪的生产绕过，
    因此审计失败必须阻止放行，而不是降级为仅打印日志。
    """


def _reject_reasons(analysis: PRDAnalysisResult) -> list[str]:
    reasons: list[str] = []
    reasons.extend(analysis.blocked_reasons)
    if analysis.parse_error:
        reasons.append(f"需求分析解析降级：{analysis.parse_error}")
    for gap in analysis.missing_items:
        if gap.detail not in reasons:
            reasons.append(gap.detail)
    if not reasons:
        reasons.append(
            f"需求质量为 {analysis.status.label}（{analysis.status.value}），"
            "不满足测试设计条件"
        )
    return reasons


@dataclass(frozen=True)
class GateOverride:
    """人工放行请求（服务端受控）。

    Args:
        token: 放行凭证，必须与服务端 ``REQUIREMENT_GATE_OVERRIDE_TOKEN`` 完全一致
        reason: 放行原因（>= MIN_OVERRIDE_REASON_LENGTH 字符，写入审计记录）
        operator: 操作人标识（写入审计记录）
        requirement_id: 需求编号（写入审计记录，便于回指）
        source: 需求来源（文件名/章节）
        request_id: 请求追踪 ID；为空时自动生成
    """

    token: str
    reason: str
    operator: str = "unknown"
    requirement_id: str = ""
    source: str = ""
    request_id: str = ""


def override_enabled() -> bool:
    """人工放行是否已被服务端启用（默认未启用）。"""
    return bool(os.getenv(OVERRIDE_TOKEN_ENV, "").strip())


def audit_file_path() -> Path:
    """审计文件路径（append-only JSONL）。"""
    configured = os.getenv(AUDIT_DIR_ENV, "").strip()
    if configured:
        return Path(configured) / AUDIT_FILENAME
    # 默认落在 backend/workspace/test_platform_audit/
    backend_root = Path(__file__).resolve().parents[3]
    return backend_root / "workspace" / "test_platform_audit" / AUDIT_FILENAME


def _validate_override(override: Optional[GateOverride]) -> None:
    """校验人工放行凭证；不通过直接抛错（fail-closed，绝不静默放行）。

    注意：任何分支都**不得**把 token 内容写入日志或异常信息。
    """
    expected = os.getenv(OVERRIDE_TOKEN_ENV, "").strip()
    if not expected:
        raise GateOverrideNotAuthorizedError(
            "人工放行未启用：服务端未配置 "
            f"{OVERRIDE_TOKEN_ENV}。Incomplete/Blocked 需求不得生成正式测试用例。"
        )
    if override is None or not override.token:
        raise GateOverrideNotAuthorizedError(
            "人工放行需要有效的服务端放行凭证（token），客户端参数不足以绕过需求质量门控。"
        )
    if not hmac.compare_digest(str(override.token), expected):
        raise GateOverrideNotAuthorizedError("人工放行凭证无效。")
    if len(str(override.reason or "").strip()) < MIN_OVERRIDE_REASON_LENGTH:
        raise GateOverrideNotAuthorizedError(
            f"人工放行必须提供不少于 {MIN_OVERRIDE_REASON_LENGTH} 个字符的放行原因（用于审计）。"
        )


def _build_audit_entry(
    analysis: Optional[PRDAnalysisResult],
    override: GateOverride,
) -> dict[str, Any]:
    """构建审计条目（**绝不包含 token**）。"""
    request_id = override.request_id or uuid.uuid4().hex
    return {
        "action": "requirement_gate_override",
        "audit_id": request_id,
        "request_id": request_id,
        "at": datetime.now(timezone.utc).isoformat(),
        "operator": override.operator or "unknown",
        "reason": str(override.reason).strip(),
        "requirement_id": override.requirement_id or "",
        "source": override.source or "",
        "status": analysis.status.value if analysis else "unknown",
        "reasons": _reject_reasons(analysis) if analysis else [],
    }


def _persist_audit_entry(entry: dict[str, Any]) -> Path:
    """把审计条目追加写入 JSONL 文件（append-only，仅本进程加锁）。"""
    path = audit_file_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    line = json.dumps(entry, ensure_ascii=False)
    with open(path, "a", encoding="utf-8") as handle:
        handle.write(line + "\n")
        handle.flush()
        os.fsync(handle.fileno())
    return path


def _record_override(
    analysis: Optional[PRDAnalysisResult],
    override: GateOverride,
) -> dict[str, Any]:
    """记录人工放行审计：先落盘，再入内存。

    审计写入失败 → 抛 :class:`GateOverrideAuditError`，**拒绝放行**。
    """
    entry = _build_audit_entry(analysis, override)

    try:
        path = _persist_audit_entry(entry)
    except Exception as exc:  # noqa: BLE001 — 任何落盘失败都必须 fail-closed
        # 仅记录异常类型与路径，绝不记录 token
        logger.error(
            "需求质量门控人工放行审计写入失败，已拒绝放行：target=%s error=%s",
            audit_file_path(),
            type(exc).__name__,
        )
        raise GateOverrideAuditError(
            "人工放行审计写入失败，已拒绝放行（不允许出现不可追踪的绕过）。"
            f"审计路径: {audit_file_path()}；错误类型: {type(exc).__name__}"
        ) from exc

    entry["audit_file"] = str(path)
    with _audit_lock:
        _override_audit_log.append(entry)

    logger.warning(
        "需求质量门控人工放行：request_id=%s operator=%s status=%s requirement_id=%s reason=%s",
        entry["request_id"],
        entry["operator"],
        entry["status"],
        entry["requirement_id"] or "-",
        entry["reason"],
    )
    return entry


def get_override_audit_log() -> list[dict[str, Any]]:
    """读取本次进程内的人工放行审计记录（用于核查）。"""
    with _audit_lock:
        return list(_override_audit_log)


def read_persisted_audit_entries() -> list[dict[str, Any]]:
    """读取已落盘的审计记录（append-only JSONL）。"""
    path = audit_file_path()
    if not path.exists():
        return []
    entries: list[dict[str, Any]] = []
    with open(path, "r", encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            try:
                entries.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return entries


def clear_override_audit_log() -> None:
    """清空进程内审计记录（测试用；不删除已落盘文件）。"""
    with _audit_lock:
        _override_audit_log.clear()


def check_generation_gate(
    analysis: Optional[PRDAnalysisResult],
    *,
    override: Optional[GateOverride] = None,
) -> tuple[bool, list[str]]:
    """测试用例生成硬门控判断。

    Args:
        analysis: 需求分析结果；None 表示尚未做需求分析 → 视为未通过
        override: 人工放行请求（服务端受控）。None 时严格按 V1.2 判定；
            提供时必须通过 :func:`_validate_override` 校验，否则抛
            :class:`GateOverrideNotAuthorizedError`（fail-closed）

    Returns:
        (allowed, reasons)
    """
    allowed_by_analysis = (
        analysis is not None
        and analysis.can_generate_testcases
        and (
            analysis.status == PRDQualityStatus.COMPLETE
            # 字段规格类文档（B1）：文档没有 PRD 章节体，但有足量结构化字段规则；
            # 状态仍如实为 Incomplete，这里按「适用档案」放行，依据写在 profile_reason 与报告里。
            or analysis.applicable_profile == FIELD_SPEC_PROFILE
        )
    )

    if allowed_by_analysis:
        return True, []

    if override is not None:
        # 未通过门控才需要人工放行；校验不通过直接失败，不降级放行
        _validate_override(override)
        _record_override(analysis, override)
        return True, []

    if analysis is None:
        return False, ["尚未完成需求分析（结构化需求分析结果缺失），禁止生成正式用例"]

    return False, _reject_reasons(analysis)


def require_complete(
    analysis: Optional[PRDAnalysisResult],
    *,
    override: Optional[GateOverride] = None,
) -> None:
    """硬门控断言：不通过则抛出 :class:`RequirementNotReadyError`。"""
    allowed, reasons = check_generation_gate(analysis, override=override)
    if not allowed:
        assert analysis is not None
        raise RequirementNotReadyError(analysis, reasons)


def mark_generation_status(
    analysis: PRDAnalysisResult,
    status: TestCaseGenerationStatus,
) -> PRDAnalysisResult:
    """在分析结果上记录本次生成状态（不改动 status 判定）。"""
    analysis.testcase_generation_status = status
    return analysis


def build_gated_response(
    analysis: Optional[PRDAnalysisResult],
    reason: Optional[str] = None,
) -> dict[str, Any]:
    """构建"被门控拦下"的统一接口响应体。

    契约字段：
        - ``prd_quality``: 需求质量摘要
        - ``analysis_report``: 完整分析报告
        - ``testcase_generation``: 生成状态
    """
    if analysis is not None:
        mark_generation_status(analysis, TestCaseGenerationStatus.REJECTED)
        reasons = [reason] if reason else _reject_reasons(analysis)
        report = render_analysis_report(analysis)
        quality = {
            "status": analysis.status.value,
            "status_label": analysis.status.label,
            "can_generate_testcases": analysis.can_generate_testcases,
            "missing_count": len(analysis.missing_items),
            "ambiguous_count": len(analysis.ambiguous_items),
            "blocked_count": len(analysis.blocked_reasons),
        }
    else:
        reasons = [reason or "尚未完成需求分析，禁止生成正式用例"]
        report = {}
        quality = {
            "status": PRDQualityStatus.INCOMPLETE.value,
            "status_label": PRDQualityStatus.INCOMPLETE.label,
            "can_generate_testcases": False,
            "missing_count": 0,
            "ambiguous_count": 0,
            "blocked_count": 0,
        }

    return {
        "prd_quality": quality,
        "analysis_report": report,
        "testcase_generation": {
            "status": TestCaseGenerationStatus.BLOCKED.value,
            "reasons": reasons,
            "created_count": 0,
        },
    }


def build_generated_response(
    analysis: Optional[PRDAnalysisResult],
    created_count: int,
) -> dict[str, Any]:
    """构建"已生成"的接口响应体。"""
    if analysis is not None:
        mark_generation_status(analysis, TestCaseGenerationStatus.GENERATED)
        report = render_analysis_report(analysis)
        quality = {
            "status": analysis.status.value,
            "status_label": analysis.status.label,
            "can_generate_testcases": True,
            "missing_count": len(analysis.missing_items),
            "ambiguous_count": len(analysis.ambiguous_items),
            "blocked_count": len(analysis.blocked_reasons),
        }
    else:
        report = {}
        quality = {
            "status": PRDQualityStatus.COMPLETE.value,
            "status_label": PRDQualityStatus.COMPLETE.label,
            "can_generate_testcases": True,
            "missing_count": 0,
            "ambiguous_count": 0,
            "blocked_count": 0,
        }

    return {
        "prd_quality": quality,
        "analysis_report": report,
        "testcase_generation": {
            "status": TestCaseGenerationStatus.GENERATED.value,
            "reasons": [],
            "created_count": created_count,
        },
    }


# ---------------------------------------------------------------------------
# 生成会话（暂存门控状态）
# ---------------------------------------------------------------------------

@dataclass
class GenerationSession:
    """一次「需求 → 测试用例」生成会话。"""

    session_key: str
    requirement_id: str = ""
    source: str = ""
    analysis: Optional[PRDAnalysisResult] = None
    status: TestCaseGenerationStatus = TestCaseGenerationStatus.NOT_STARTED
    created_case_ids: list[str] = field(default_factory=list)
    reasons: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "session_key": self.session_key,
            "requirement_id": self.requirement_id,
            "source": self.source,
            "status": self.status.value,
            "created_case_ids": list(self.created_case_ids),
            "reasons": list(self.reasons),
            "analysis": self.analysis.to_dict() if self.analysis else None,
        }


_lock = threading.Lock()
_sessions: dict[str, GenerationSession] = {}
_DEFAULT_KEY = "__default__"


def _normalize_key(session_key: Optional[str]) -> str:
    return session_key.strip() if session_key and session_key.strip() else _DEFAULT_KEY


def start_session(
    session_key: Optional[str],
    *,
    requirement_id: str = "",
    source: str = "",
) -> GenerationSession:
    """开启（或重置）一次生成会话。"""
    key = _normalize_key(session_key)
    session = GenerationSession(session_key=key, requirement_id=requirement_id, source=source)
    with _lock:
        _sessions[key] = session
    return session


def update_session(
    session_key: Optional[str],
    analysis: PRDAnalysisResult,
) -> GenerationSession:
    """把需求分析结果写入会话，并按门控结果推进状态。"""
    key = _normalize_key(session_key)
    with _lock:
        session = _sessions.get(key) or GenerationSession(session_key=key)
        session.analysis = analysis
        allowed, reasons = check_generation_gate(analysis)
        session.reasons = reasons
        session.status = (
            TestCaseGenerationStatus.NOT_STARTED
            if allowed
            else TestCaseGenerationStatus.BLOCKED
        )
        _sessions[key] = session
        return session


def record_generated(session_key: Optional[str], case_ids: list[str]) -> GenerationSession:
    """记录本次生成的用例编号。"""
    key = _normalize_key(session_key)
    with _lock:
        session = _sessions.get(key) or GenerationSession(session_key=key)
        for case_id in case_ids:
            if case_id and case_id not in session.created_case_ids:
                session.created_case_ids.append(case_id)
        session.status = TestCaseGenerationStatus.GENERATED
        session.reasons = []
        _sessions[key] = session
        return session


def get_session(session_key: Optional[str]) -> Optional[GenerationSession]:
    key = _normalize_key(session_key)
    with _lock:
        return _sessions.get(key)


def clear_session(session_key: Optional[str]) -> None:
    key = _normalize_key(session_key)
    with _lock:
        _sessions.pop(key, None)
