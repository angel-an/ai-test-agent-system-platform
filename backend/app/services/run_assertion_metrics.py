# -*- coding: utf-8 -*-
"""把断言账目的指标模型**落到 run 级聚合列**（R5）。

为什么单独一层：
- ``assertion_ledger`` 保持"纯解析"（无 DB、可单测）；
- 指标的计算口径只应有一份，落库只是它的**物化**——明细仍走
  ``/assertion-ledger``，run 级只存聚合数（复核意见：不要为了 4 列就合并两类通过率）。

写入时机：一次运行的全部作业结束后（engine）、以及手工"作业结果映射到用例"之后。
中途不写——半成品数字比没有数字更容易被误读。
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from sqlalchemy import select, update

from app.models.test_run import TestRun, TestRunScriptJob
from app.services.assertion_ledger import build_assertion_ledger

_METRIC_KEYS = ("total", "pass", "fail", "unverified", "blocked")


def metrics_to_columns(metrics: dict[str, Any]) -> dict[str, int]:
    """指标模型 → ``test_runs`` 列值（两类各自独立，不合并）。"""
    values: dict[str, int] = {}
    for category in ("case_attributed", "config_only"):
        bucket = metrics.get(category) or {}
        for key in _METRIC_KEYS:
            values[f"{category}_{key}"] = int(bucket.get(key, 0) or 0)
    values["unattributed_checks"] = int(metrics.get("unattributed_checks", 0) or 0)
    return values


async def refresh_run_assertion_metrics(session: Any, test_run_id: UUID) -> dict[str, Any]:
    """重算并写入 run 级断言指标，返回完整 metrics（含 cross_check，便于核对）。"""
    jobs = list(
        (
            await session.execute(
                select(TestRunScriptJob)
                .where(TestRunScriptJob.test_run_id == test_run_id)
                .order_by(TestRunScriptJob.execution_order)
            )
        )
        .scalars()
        .all()
    )
    ledger = build_assertion_ledger(jobs)
    metrics = ledger["metrics"]
    await session.execute(
        update(TestRun)
        .where(TestRun.id == test_run_id)
        .values(**metrics_to_columns(metrics))
    )
    await session.flush()
    return metrics
