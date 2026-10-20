# -*- coding: utf-8 -*-
"""测试运行保留策略（归档/清理）——2026-09-29 客观复核第 5 项。

现状：113 个 TestRun / 155 个作业，stdout 约 0.48 MB，且 runner 工作目录下累计
442 个 ``playwright-report-*`` / ``test-results-*`` 目录（约 104 MB）——没有任何
保留策略，历史只增不减。

设计口径（**无损优先**）：
1. 归档 ≠ 删除运行。运行行、作业行、用例状态、计数、``result_summary`` 既有字段全部保留，
   UI/报告仍能看到"当时跑出什么结论"。
2. 只把**大体积且可重建**的证据降级为**结构化摘要**：``job.stdout/stderr`` 抽取成
   ``result_summary.retention.evidence``（断言通过/未验证/阻塞分布、覆盖率、环境阻断标记、
   原字符数），然后清空原文。抽取失败也只是少一条摘要，不影响归档本身。
3. 只处理**终态**运行（done / rejected / closed）；``new_run`` / ``in_progress`` /
   ``under_review`` 永不触碰——避免把正在跑或待评审的证据清掉。
4. 幂等：已带 ``result_summary.retention`` 的作业不会重复处理。
5. 磁盘清理只删"**没有被任何作业 report_path 引用** + 超过保留期"的报告目录，
   且始终在给定工作目录内（``resolve()`` + 前缀校验），默认 dry-run。

本模块只提供**计划**（纯函数，可单测）+ **应用**两段，API 层负责 dry_run 开关。
"""

from __future__ import annotations

import json
import logging
import shutil
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Sequence

logger = logging.getLogger(__name__)

#: 允许归档的运行状态（终态）。其余状态一律跳过。
TERMINAL_RUN_STATES = frozenset({"done", "rejected", "closed"})

#: 证据行标记（与执行器输出契约一致）
_ASSERTION_PREFIX = "COMPILED_ASSERTION "
_SUMMARY_PREFIX = "COMPILED_ASSERTION_SUMMARY "
_COVERAGE_PREFIX = "COMPILED_COVERAGE "
_UPLOAD_PREFIX = "UPLOAD_EVIDENCE "
_ENV_BLOCKED_MARKER = "ENVIRONMENT_BLOCKED"

#: 磁盘报告目录命名（runner 产出）
_DISK_PATTERNS = ("playwright-report-*", "test-results-playwright-report-*", "test-results-*")


def _json_after(line: str, prefix: str) -> Any:
    try:
        return json.loads(line.split(prefix, 1)[1])
    except Exception:  # noqa: BLE001 - 证据行损坏时按"抽取不到"处理
        return None


#: 证据摘要白名单：只保留**结构化计数**与**断言名称**，绝不保留原始 observed 文本
#: （observed 里可能出现被测系统的真实数据）。执行器输出的 summary/coverage 是 JSON，
#: 直接落库会把未来新增字段（可能含敏感值）一并带进去 —— 这里按白名单裁剪。
_SUMMARY_ALLOWED_KEYS = (
    "total", "pass", "unverified", "blocked", "submit_phase",
    "skipped_no_locator", "skipped_by_reason", "plan_checks", "attributed_checks",
    "upload_evidence", "isolation_blocked",
)
_COVERAGE_ALLOWED_KEYS = (
    "cases", "rule_cases", "narrative_cases", "dangling_rule_ids", "assertions",
    "form_executable_checks", "pending_locator", "field_labels_found", "field_labels_total",
)
_MAX_SAMPLE_ITEMS = 5
_MAX_SAMPLE_CHARS = 80
_MAX_RULE_ID_ITEMS = 10


def _pick(payload: Any, allowed: Sequence[str]) -> dict[str, Any] | None:
    if not isinstance(payload, dict):
        return None
    picked = {key: payload[key] for key in allowed if key in payload}
    return picked or None


def extract_evidence(stdout: str | None) -> dict[str, Any]:
    """把 stdout 里**可机读**的证据抽成摘要（归档后仍能回答"当时什么结论"）。

    证据最小化（复核意见）：断言只留**名称**（不含 observed 原文），
    summary/coverage 只留白名单字段，样例条数与长度都有上限。
    """
    text = stdout or ""
    outcomes: dict[str, int] = {"pass": 0, "unverified": 0, "blocked": 0}
    assertions = 0
    sample: list[str] = []
    summary: Any = None
    coverage: Any = None
    upload_evidence = 0
    environment_blocked = False

    for line in text.splitlines():
        if _ASSERTION_PREFIX in line:
            record = _json_after(line, _ASSERTION_PREFIX)
            if isinstance(record, dict):
                assertions += 1
                outcome = str(record.get("outcome") or "")
                if outcome in outcomes:
                    outcomes[outcome] += 1
                if len(sample) < _MAX_SAMPLE_ITEMS:
                    sample.append(str(record.get("name") or "")[:_MAX_SAMPLE_CHARS])
            continue
        if _SUMMARY_PREFIX in line:
            summary = _json_after(line, _SUMMARY_PREFIX)
            continue
        if _COVERAGE_PREFIX in line:
            coverage = _json_after(line, _COVERAGE_PREFIX)
            continue
        if _UPLOAD_PREFIX in line:
            upload_evidence += 1
            continue
        if _ENV_BLOCKED_MARKER in line:
            environment_blocked = True

    evidence: dict[str, Any] = {
        "assertions": assertions,
        "assertion_outcomes": outcomes,
        "assertion_sample": sample,
        "upload_evidence": upload_evidence,
        "environment_blocked": environment_blocked,
        "stdout_chars": len(text),
        "evidence_policy": "仅结构化计数与断言名称；不保留 observed 原文与 stdout/stderr 正文",
    }
    picked_summary = _pick(summary, _SUMMARY_ALLOWED_KEYS)
    if picked_summary:
        evidence["summary"] = picked_summary
    picked_coverage = _pick(coverage, _COVERAGE_ALLOWED_KEYS)
    if picked_coverage:
        sample_ids = coverage.get("sample_rule_ids") if isinstance(coverage, dict) else None
        if isinstance(sample_ids, list):
            picked_coverage["sample_rule_ids"] = [str(x) for x in sample_ids[:_MAX_RULE_ID_ITEMS]]
        evidence["coverage"] = picked_coverage
    return evidence


@dataclass
class JobRetentionAction:
    """一个待归档作业：保留摘要、清空原文。"""

    job: Any
    run_identifier: str
    evidence: dict[str, Any] = field(default_factory=dict)
    stdout_chars: int = 0
    stderr_chars: int = 0
    report_path: str | None = None


@dataclass
class RetentionPlan:
    actions: list[JobRetentionAction] = field(default_factory=list)
    skipped: dict[str, int] = field(default_factory=dict)
    run_identifiers: list[str] = field(default_factory=list)

    @property
    def stdout_chars(self) -> int:
        return sum(a.stdout_chars for a in self.actions)

    @property
    def stderr_chars(self) -> int:
        return sum(a.stderr_chars for a in self.actions)

    def to_dict(self) -> dict[str, Any]:
        return {
            "dry_run": True,
            "runs": len(self.run_identifiers),
            "run_identifiers": self.run_identifiers[:50],
            "jobs": len(self.actions),
            "stdout_chars": self.stdout_chars,
            "stderr_chars": self.stderr_chars,
            "stdout_mb": round(self.stdout_chars / 1024 / 1024, 3),
            "skipped": dict(self.skipped),
            "planned": [
                {
                    "run": a.run_identifier,
                    "job": getattr(a.job, "script_identifier", ""),
                    "stdout_chars": a.stdout_chars,
                    "stderr_chars": a.stderr_chars,
                    "evidence": a.evidence,
                }
                for a in self.actions[:50]
            ],
        }


def _age_days(created_at: datetime | None, now: datetime) -> float | None:
    if created_at is None:
        return None
    if created_at.tzinfo is None:
        created_at = created_at.replace(tzinfo=timezone.utc)
    return (now - created_at).total_seconds() / 86400


def _state_value(run: Any) -> str:
    state = getattr(run, "run_state", None)
    return str(getattr(state, "value", state) or "")


def build_retention_plan(
    runs: Sequence[Any],
    jobs: Sequence[Any],
    *,
    now: datetime,
    older_than_days: float | None = None,
    run_identifiers: Iterable[str] | None = None,
    keep_latest_per_project: int | None = None,
) -> RetentionPlan:
    """挑选可归档的作业（纯函数）。三重筛选：终态 → 保留期/指定运行 → 未归档且有大体积原文。"""
    plan = RetentionPlan()
    skipped = {"not_terminal": 0, "too_new": 0, "not_selected": 0, "already_archived": 0,
               "empty_output": 0, "kept_latest": 0}
    wanted = {str(x) for x in run_identifiers} if run_identifiers else None

    selected: list[Any] = []
    for run in runs:
        if _state_value(run) not in TERMINAL_RUN_STATES:
            skipped["not_terminal"] += 1
            continue
        if wanted is not None and str(getattr(run, "identifier", "")) not in wanted:
            skipped["not_selected"] += 1
            continue
        if older_than_days is not None:
            age = _age_days(getattr(run, "created_at", None), now)
            if age is None or age < older_than_days:
                skipped["too_new"] += 1
                continue
        selected.append(run)

    if keep_latest_per_project:
        by_project: dict[Any, list[Any]] = {}
        for run in selected:
            by_project.setdefault(getattr(run, "project_id", None), []).append(run)
        keep: set[Any] = set()
        for members in by_project.values():
            ordered = sorted(
                members,
                key=lambda r: (getattr(r, "created_at", None) or now),
                reverse=True,
            )
            keep.update(id(r) for r in ordered[:keep_latest_per_project])
        filtered = []
        for run in selected:
            if id(run) in keep:
                skipped["kept_latest"] += 1
                continue
            filtered.append(run)
        selected = filtered

    run_by_id = {getattr(r, "id", None): r for r in selected}
    for job in jobs:
        run = run_by_id.get(getattr(job, "test_run_id", None))
        if run is None:
            continue
        summary = getattr(job, "result_summary", None)
        if isinstance(summary, dict) and isinstance(summary.get("retention"), dict):
            skipped["already_archived"] += 1
            continue
        stdout = getattr(job, "stdout", None) or ""
        stderr = getattr(job, "stderr", None) or ""
        if not stdout.strip() and not stderr.strip():
            skipped["empty_output"] += 1
            continue
        plan.actions.append(JobRetentionAction(
            job=job,
            run_identifier=str(getattr(run, "identifier", "")),
            evidence=extract_evidence(stdout),
            stdout_chars=len(stdout),
            stderr_chars=len(stderr),
            report_path=getattr(job, "report_path", None),
        ))
        if isinstance(summary, dict) and summary.get("summary"):
            # 结果汇总（total/pass/unverified/blocked）本就是结论级字段，单独留一份
            plan.actions[-1].evidence.setdefault("result_summary", summary.get("summary"))
    plan.skipped = skipped
    plan.run_identifiers = [str(getattr(r, "identifier", "")) for r in selected]
    return plan


def apply_retention_plan(
    plan: RetentionPlan,
    *,
    archived_at: str | None = None,
) -> dict[str, Any]:
    """把计划落到 ORM 对象上（调用方负责 commit）。返回归档统计。"""
    stamp = archived_at or datetime.now(timezone.utc).isoformat(timespec="seconds")
    for action in plan.actions:
        job = action.job
        summary = dict(getattr(job, "result_summary", None) or {})
        summary["retention"] = {
            "archived_at": stamp,
            "reason": "历史运行归档：保留结论摘要，清理大体积 stdout/stderr",
            "evidence": action.evidence,
            "stderr_chars": action.stderr_chars,
            "report_path": action.report_path,
        }
        job.result_summary = summary
        job.stdout = None
        job.stderr = None
    logger.info(
        "[run_retention] 归档 %d 个作业（%d 个运行），清理 stdout %d 字符 / stderr %d 字符",
        len(plan.actions), len(plan.run_identifiers), plan.stdout_chars, plan.stderr_chars,
    )
    return {
        "archived_jobs": len(plan.actions),
        "archived_runs": len(plan.run_identifiers),
        "cleared_stdout_chars": plan.stdout_chars,
        "cleared_stderr_chars": plan.stderr_chars,
        "archived_at": stamp,
    }


def _dir_size(path: Path) -> tuple[int, int]:
    total = files = 0
    for item in path.rglob("*"):
        if item.is_file():
            try:
                total += item.stat().st_size
                files += 1
            except OSError:
                continue
    return total, files


def build_disk_retention_plan(
    workspace: Path,
    referenced: Iterable[str],
    *,
    now: datetime,
    older_than_days: float,
    limit: int | None = None,
) -> dict[str, Any]:
    """挑选可删除的报告目录（**只读**，不删任何东西）。

    ``referenced``：全部作业的 ``report_path`` 原值；任何包含该目录名的引用都会保护它
    （路径格式不统一——实测有相对路径、裸 UUID、``web-test-reports/.../report.zip`` 三种）。
    """
    workspace = Path(workspace).resolve()
    refs = [str(r) for r in referenced if r]
    candidates: list[dict[str, Any]] = []
    protected = 0
    too_new = 0
    for pattern in _DISK_PATTERNS:
        for path in sorted(workspace.glob(pattern)):
            if not path.is_dir():
                continue
            if any(path.name in ref for ref in refs):
                protected += 1
                continue
            age = (now - datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc)).total_seconds() / 86400
            if age < older_than_days:
                too_new += 1
                continue
            size, files = _dir_size(path)
            candidates.append({
                "name": path.name,
                "path": str(path),
                "bytes": size,
                "files": files,
                "age_days": round(age, 1),
            })
    candidates.sort(key=lambda item: item["bytes"], reverse=True)
    total_bytes = sum(item["bytes"] for item in candidates)
    files_total = sum(item["files"] for item in candidates)
    trimmed = candidates if limit is None else candidates[:limit]
    return {
        "dirs": trimmed,
        "dir_count": len(trimmed),
        "total_dir_count": len(candidates),
        "bytes": sum(item["bytes"] for item in trimmed),
        "total_bytes": total_bytes,
        "files": files_total,
        "protected_by_db": protected,
        "too_new": too_new,
        "workspace": str(workspace),
    }


def apply_disk_retention_plan(plan: dict[str, Any]) -> dict[str, Any]:
    """删除计划中的报告目录（严格限制在 workspace 内、且名称匹配 runner 产物模式）。

    错误分类（复核意见）：**失效软链**（目录已不存在/WinError 2）单独记账，
    与真正的删除失败区分——前者是无害残留，后者需要人工处理。
    """
    workspace = Path(plan["workspace"]).resolve()
    removed = freed = 0
    errors: list[str] = []
    stale_links: list[str] = []
    for item in plan["dirs"]:
        path = Path(item["path"])
        try:
            resolved = path.resolve()
        except OSError:
            resolved = path
        try:
            resolved.relative_to(workspace)
        except ValueError:
            errors.append(f"越界拒绝删除: {resolved}")
            continue
        if not any(resolved.match(pattern) for pattern in _DISK_PATTERNS):
            errors.append(f"命名不匹配，拒绝删除: {resolved.name}")
            continue
        if not path.exists():
            stale_links.append(f"目录已不存在（失效软链/已被清理）: {path.name}")
            continue
        try:
            shutil.rmtree(path)
            removed += 1
            freed += int(item["bytes"])
        except FileNotFoundError:
            stale_links.append(f"删除时已不存在（失效软链）: {path.name}")
        except OSError as exc:
            errors.append(f"删除失败 {path.name}: {exc}")
    logger.info(
        "[run_retention] 磁盘清理 %d 个目录，释放约 %.2f MB（失效软链 %d，失败 %d）",
        removed, freed / 1024 / 1024, len(stale_links), len(errors),
    )
    return {
        "removed_dirs": removed,
        "freed_bytes": freed,
        "freed_mb": round(freed / 1024 / 1024, 2),
        "stale_link_count": len(stale_links),
        "stale_links": stale_links[:20],
        "error_count": len(errors),
        "errors": errors[:20],
    }
