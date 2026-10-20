# -*- coding: utf-8 -*-
"""运行级「四指标」：生成用例数 / 规则用例数 / 编译断言数 / 实际执行资产数。

背景（客观复核意见第 3 条）：平台此前只有一个笼统的"用例数"，
于是出现两种相反的误读：

- **高估**：拿"1 个 WebTest 冒烟 passed 1/1"当成"53 条生成用例已自动验证"；
- **低估**：把 306 条历史用例当分母算覆盖率，掩盖本次批次真实生成了多少。

实测两批数据（2026-09-23 / 09-24）：

===========  ==========  ============  ==============  ==================
批次          生成用例数   规则用例数     编译断言数       实际执行资产数
===========  ==========  ============  ==============  ==================
PR-2 小杨生煎  53          17            45              1（TR-82 冒烟通过）
PR-9 CS Module 36          0             0               1（TR-84 冒烟通过）
===========  ==========  ============  ==============  ==================

四个数**互不等价**，本模块刻意并列返回、不合成任何"总覆盖率"，
并对偏差给出显式告警（``warnings``）。逐条用例的编译状态由编译层给出
（``web_case_compiler.compile_cases`` 的 ``compile_status`` / ``uncompiled_cases``）。
"""

from __future__ import annotations

from typing import Any, Iterable

from sqlalchemy import select

from app.models.test_case import TestCase
from app.models.test_run import TestRun, TestRunScriptJob, TestRunTestCase
from app.models.web_test import WebTest
from app.schemas.enums import JobStatus, ScriptType, TestResultStatus
from app.services.web_case_compiler import compile_cases

#: 视为"真的跑过"的作业状态（pending 表示尚未开始；skipped 不算跑过）
_EXECUTED_JOB_STATUSES = {
    JobStatus.RUNNING.value,
    JobStatus.COMPLETED.value,
    JobStatus.FAILED.value,
    JobStatus.CANCELLED.value,
}

#: 用例级"已被自动验证"的结果状态（passed/failed 都算有断言结论）
_VERIFIED_CASE_STATUSES = {
    TestResultStatus.PASSED.value,
    TestResultStatus.FAILED.value,
}

METRIC_DEFINITIONS: dict[str, str] = {
    "generated_case_count": "本次批次生成的需求用例数（按 requirement_id 归批；无批次键时退化为锚点用例）",
    "rule_case_count": "其中携带规则体/断言、可编译执行的用例数（编译清单 rule_case_count 之和）",
    "compiled_assertion_count": "编译出的断言条数（含软校验；不等于执行通过的断言数）",
    "executed_asset_count": "本次运行中**真的启动过**的脚本资产数（job.started_at 非空或状态为 running/completed/failed/cancelled）",
}


def _job_was_executed(job: Any) -> bool:
    """作业是否真的启动过（不用"已挂载"冒充"已执行"）。"""
    if getattr(job, "started_at", None) is not None:
        return True
    status = getattr(job, "status", None)
    status_value = getattr(status, "value", status)
    return str(status_value or "") in _EXECUTED_JOB_STATUSES


def _enum_value(value: Any) -> str:
    return str(getattr(value, "value", value) or "")


def build_generation_metrics(
    *,
    batch_cases: Iterable[Any],
    web_tests: Iterable[Any],
    jobs: Iterable[Any],
    run_cases: Iterable[Any] = (),
) -> dict[str, Any]:
    """由已加载的对象计算四指标（纯函数，便于单测与复用）。

    - ``batch_cases``：本次批次的需求用例
    - ``web_tests``：本次运行关联的脚本资产（读取其 generation_params.compiled_cases）
    - ``jobs``：本次运行的脚本作业
    - ``run_cases``：运行内的用例级结果（TestRunTestCase）
    """
    cases = list(batch_cases)
    assets = list(web_tests)
    job_list = list(jobs)
    case_rows = list(run_cases)

    # --- 编译口径：资产上记录的编译摘要 ---
    #: 按**批次**归并（同一组用例 = 同一批次）。历史实现直接对多个资产求和，
    #: 于是"同一批次编译进 2 个资产"时规则用例数/编译断言数被翻倍
    #: （实测：36 条用例的批次 → rule_case_count 20 报成 40、assertion 50 报成 100），
    #: 而下方用例状态分类用的是 max 去重 —— 同一函数两套口径，四指标自相矛盾。
    #: 现在统一为"同一批次只计一次，取最大值"（跨批次仍求和）。
    batch_totals: dict[str, dict[str, int]] = {}
    rule_case_count = 0
    compiled_assertion_count = 0
    compiled_summary_case_count = 0
    status_counts = {"compiled": 0, "dangling_rule": 0, "narrative": 0}
    uncompiled: dict[str, dict[str, Any]] = {}
    status_source = "asset_summary"
    for asset_index, asset in enumerate(assets):
        params = getattr(asset, "generation_params", None) or {}
        summary = params.get("compiled_cases") or {}
        if not summary:
            continue
        identifiers = sorted(str(x) for x in (summary.get("case_identifiers") or []) if str(x))
        #: 缺 case_identifiers 的历史资产无法判重 → 退化为按资产逐个计数（诚实降级，不猜）
        batch_key = "|".join(identifiers) if identifiers else f"__asset_{asset_index}"
        slot = batch_totals.setdefault(batch_key, {"case": 0, "rule": 0, "assert": 0})
        slot["case"] = max(slot["case"], int(summary.get("case_count") or 0))
        slot["rule"] = max(slot["rule"], int(summary.get("rule_case_count") or 0))
        slot["assert"] = max(slot["assert"], int(summary.get("assertion_count") or 0))
        stored_counts = summary.get("case_status_counts") or {}
        if not stored_counts:
            # 历史资产（本版之前生成）没有分类字段：不能用"缺失"冒充"没有不可编译用例"，
            # 否则 53 条里 36 条叙述型会被静默报成 0 条未编译。
            status_source = "recomputed_from_batch"
            continue
        for status_key, value in stored_counts.items():
            if status_key in status_counts:
                # 同一次运行的多个资产覆盖**同一批次** → 取最大值而非求和，避免重复计数
                # （实测：同一批次编译到 2 个资产时，求和会把 36 报成 72）
                status_counts[status_key] = max(status_counts[status_key], int(value or 0))
        for item in summary.get("uncompiled_cases") or []:
            identifier = str(item.get("identifier") or "")
            if identifier and identifier not in uncompiled:
                uncompiled[identifier] = {
                    "identifier": identifier,
                    "status": item.get("status") or "unknown",
                    "reason": item.get("reason") or "",
                }

    compiled_summary_case_count = sum(s["case"] for s in batch_totals.values())
    rule_case_count = sum(s["rule"] for s in batch_totals.values())
    compiled_assertion_count = sum(s["assert"] for s in batch_totals.values())

    if status_source == "recomputed_from_batch" and cases:
        # 编译层是纯函数：按当前用例重算分类（枚举/规则未变则与生成时一致）。
        # 只补分类口径，rule_case_count / compiled_assertion_count 仍以生成时落库值为准。
        recomputed = compile_cases(cases)
        status_counts = {
            key: int((recomputed.get("case_status_counts") or {}).get(key) or 0)
            for key in ("compiled", "dangling_rule", "narrative")
        }
        uncompiled = {
            str(item.get("identifier")): {
                "identifier": str(item.get("identifier")),
                "status": item.get("status") or "unknown",
                "reason": item.get("reason") or "",
            }
            for item in (recomputed.get("uncompiled_cases") or [])
            if item.get("identifier")
        }

    # 未编译**计数**必须来自完整分类统计，而不是 `uncompiled_cases` 样本列表——
    # 落库摘要会把样本截断到 20 条，用列表长度当计数会把 36 报成 20（实测踩到）。
    # 最可靠的口径：批次规模 − 可编译用例数（两者都是完整计数）。
    classified = status_counts["dangling_rule"] + status_counts["narrative"]

    # --- 执行口径：资产是否真的跑过 ---
    attached_ids = {str(getattr(job, "script_id", "")) for job in job_list if getattr(job, "script_id", None)}
    executed_ids = {
        str(getattr(job, "script_id", ""))
        for job in job_list
        if getattr(job, "script_id", None) and _job_was_executed(job)
    }

    # --- 用例级结果（平台已有的细粒度结论）---
    case_status_breakdown: dict[str, int] = {}
    for row in case_rows:
        key = _enum_value(getattr(row, "latest_status", "")) or "unknown"
        case_status_breakdown[key] = case_status_breakdown.get(key, 0) + 1
    verified_case_count = sum(
        count for status, count in case_status_breakdown.items()
        if status in _VERIFIED_CASE_STATUSES
    )

    generated_case_count = len(cases) or compiled_summary_case_count
    # 未编译计数：分类统计（可能被样本截断）与"批次规模 − 可编译"取较大者，避免低报
    uncompiled_count = classified
    if generated_case_count:
        uncompiled_count = max(uncompiled_count, generated_case_count - status_counts["compiled"])
    metrics = {
        "generated_case_count": generated_case_count,
        "rule_case_count": rule_case_count,
        "compiled_assertion_count": compiled_assertion_count,
        "executed_asset_count": len(executed_ids),
    }

    warnings: list[str] = []
    if metrics["executed_asset_count"] and metrics["executed_asset_count"] < generated_case_count:
        warnings.append(
            f"实际执行资产数（{metrics['executed_asset_count']}）远小于生成用例数"
            f"（{generated_case_count}）：资产级通过不代表这些用例已逐条执行；"
            "需求级结论请以 rule_case_count / compiled_assertion_count 与用例级结果为准。"
        )
    if uncompiled_count:
        dangling = status_counts["dangling_rule"] or sum(
            1 for item in uncompiled.values() if item["status"] == "dangling_rule"
        )
        narrative = status_counts["narrative"] or sum(
            1 for item in uncompiled.values() if item["status"] == "narrative"
        )
        warnings.append(
            f"{uncompiled_count} 条用例无法自动执行（有规则号无可提取规则 {dangling} 条 / "
            f"无可提取规则编号 {narrative} 条）——用例本身均为可执行的测试用例，"
            f"需人工执行，或补齐需求规则后重生成转自动。"
        )
    if generated_case_count and metrics["compiled_assertion_count"] == 0:
        warnings.append(
            "编译断言数为 0：本次运行不产生需求级断言证据，"
            "资产通过只能证明入口可达，不能作为需求覆盖依据。"
        )
    if metrics["executed_asset_count"] == 0 and attached_ids:
        warnings.append("本次运行已挂载脚本资产但没有任何作业真正启动，结论为空跑。")
    #: 盘点可解释性（实测：全库 253 个作业里 87 个引用的资产在资产表中已不存在）：
    #: 这类作业仍会被算进「实际执行资产数」，但资产台账里查不到它 → 两边永远对不上。
    #: 这里显式点名，避免使用者把口径差异误当成"资产盘点错误"。
    asset_ids = {str(getattr(asset, "id", "")) for asset in assets}
    orphan_job_ids = sorted(
        str(getattr(job, "script_identifier", "") or getattr(job, "script_id", ""))
        for job in job_list
        if getattr(job, "script_id", None) and str(getattr(job, "script_id")) not in asset_ids
    )
    if orphan_job_ids:
        warnings.append(
            f"本次运行有 {len(orphan_job_ids)} 个作业引用的资产不在本次资产清单中"
            f"（如 {', '.join(orphan_job_ids[:3])}）——这些作业仍计入「实际执行资产数」，"
            "但资产台账中查不到对应资产，两处数字不可直接对齐。"
        )

    return {
        **metrics,
        "case_status_breakdown": case_status_breakdown,
        "verified_case_count": verified_case_count,
        "attached_asset_count": len(attached_ids),
        "compiled_case_count": status_counts["compiled"],
        "uncompiled_case_count": uncompiled_count,
        "case_status_counts": status_counts,
        # 样本列表可能被落库摘要截断到 20 条：仅供展示，**计数以 uncompiled_case_count 为准**
        "uncompiled_cases": list(uncompiled.values()),
        "uncompiled_cases_truncated": uncompiled_count > len(uncompiled),
        "case_status_source": status_source,
        "metric_definitions": dict(METRIC_DEFINITIONS),
        "metrics_are_independent": True,
        "scope_note": (
            "四个指标口径独立、互不折算：生成用例数 ⊃ 规则用例数；"
            "编译断言数由规则用例产生；实际执行资产数是资产级事实。"
            "任何「覆盖率」都必须写明分子分母取自哪一项。"
        ),
        "warnings": warnings,
    }


async def collect_run_generation_metrics(
    session: Any,
    *,
    project_identifier: str,
    test_run_identifier: str,
) -> dict[str, Any]:
    """从库中收集某次测试运行的四指标（批次口径与创建资产时一致）。"""
    from app.models.project import Project

    project = (
        await session.execute(select(Project).where(Project.identifier == project_identifier))
    ).scalar_one_or_none()
    if project is None:
        raise ValueError(f"项目不存在: {project_identifier}")

    test_run = (
        await session.execute(
            select(TestRun)
            .where(TestRun.project_id == project.id)
            .where(TestRun.identifier == test_run_identifier)
        )
    ).scalar_one_or_none()
    if test_run is None:
        raise ValueError(f"测试运行不存在: {test_run_identifier}")

    jobs = list(
        (
            await session.execute(
                select(TestRunScriptJob)
                .where(TestRunScriptJob.test_run_id == test_run.id)
                .order_by(TestRunScriptJob.execution_order.asc())
            )
        ).scalars().all()
    )
    web_test_ids = {
        job.script_id
        for job in jobs
        if _enum_value(job.script_type) == ScriptType.WEB_TEST.value and job.script_id
    }
    assets: list[WebTest] = []
    if web_test_ids:
        assets = list(
            (
                await session.execute(select(WebTest).where(WebTest.id.in_(web_test_ids)))
            ).scalars().all()
        )

    # 批次口径：与创建资产时一致（同一次生成会话共用 requirement_id）。
    # deprecated=true 的废弃用例不计入四指标（2026-09-29 复核：废弃 ≠ 删除，
    # 但不得再被统计为"生成/待执行"口径）。
    batch_cases: list[TestCase] = []
    anchor_id = next((asset.test_case_id for asset in assets if asset.test_case_id), None)
    if anchor_id is not None:
        anchor = (
            await session.execute(select(TestCase).where(TestCase.id == anchor_id))
        ).scalar_one_or_none()
        if anchor is not None:
            stmt = select(TestCase).where(TestCase.project_id == anchor.project_id)
            if anchor.requirement_id:
                stmt = stmt.where(TestCase.requirement_id == anchor.requirement_id)
            else:
                stmt = stmt.where(TestCase.id == anchor.id)
            batch_cases = list((await session.execute(stmt)).scalars().all())
            from app.services.deprecation import filter_active
            batch_cases = filter_active(batch_cases)

    run_cases = list(
        (
            await session.execute(
                select(TestRunTestCase).where(TestRunTestCase.test_run_id == test_run.id)
            )
        ).scalars().all()
    )

    data = build_generation_metrics(
        batch_cases=batch_cases, web_tests=assets, jobs=jobs, run_cases=run_cases
    )
    data["test_run_identifier"] = test_run.identifier
    data["run_state"] = _enum_value(test_run.run_state)
    data["asset_identifiers"] = [asset.identifier for asset in assets]
    data["batch_scope"] = "requirement_id" if batch_cases and batch_cases[0].requirement_id else "anchor_case_only"
    return data
