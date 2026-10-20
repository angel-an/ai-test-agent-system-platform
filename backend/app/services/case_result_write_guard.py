# -*- coding: utf-8 -*-
"""用例结论写入守卫（N2 双写收口）。

背景（2026-10-17 复核 N2）：同一次执行里，一条用例的结论有**两条路径**会写
``test_run_test_cases``：

1. **细粒度**：``web_test_service.persist_case_level_results`` 用编译断言的 stdout
   逐条归因（mapper），结论可解释到具体断言；
2. **粗粒度**：``ScriptExecutionEngine._sync_test_run_case_status`` 按"作业成功即用例
   通过"回写（engine），以及 ``_finalize_failed_job_cases`` 的失败终态（finalize）。

两条路径都会无条件写同一行，谁后写谁生效。实测已复现**分歧**：同一份产物
（1 pass + 1 unverified）mapper 得 ``SKIPPED``、engine 得 ``PASSED``。当前库里
100/100 一致，只是因为写入顺序恰好让 mapper 后写——**没有任何机制保证顺序**。

本模块把"该不该写"从调用点抽成**纯函数**，让四条写入路径共用同一判据：

- **轮次**：``(execution_order, retry_count)``。旧作业（更小）不得覆盖新轮次结果；
- **写入者优先级**：细粒度（mapper）> 手工映射（manual）> 失败终态（finalize）
  > 场景级兜底（engine）。同轮次下低优先级写入者让位，**与完成顺序无关**；
- **明细归属**：行上已有明细（``latest_result_id``）时，场景级兜底（engine）不得
  覆盖——这是既有 ``_should_skip_coarse_case_sync`` 语义的显式化，覆盖迁移前的
  历史行（这些行没有写入者/轮次信息）。

刻意**不**做的事：不引入新状态、不改用例结论语义（fail 语义属 N4）、不在守卫里
判断业务（"只有 NOT_EXECUTED 才升级"仍由 finalize 调用方决定）。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional

#: 写入者优先级（越大越权威）。同轮次下低优先级不得覆盖高优先级。
WRITER_PRIORITY: dict[str, int] = {
    "engine": 10,     # 场景级兜底：作业成功 → 用例通过
    "manual": 20,     # 手工"作业结果映射到用例"端点（同样是粗粒度）
    "mapper": 30,     # 编译断言逐条归因：唯一能给出可解释结论的写入者
    # 失败终态回写：全部作业完成后才跑，且调用方限定"只升级 NOT_EXECUTED"——
    # 必须能盖掉 mapper 留下的 NOT_EXECUTED（作业失败且脚本没产出断言时，
    # 只有它能给出 BLOCKED/FAILED），因此优先级高于 mapper。
    "finalize": 40,
}

#: 场景级（粗粒度）写入者：行上已有明细时必须让位。
COARSE_WRITERS = frozenset({"engine"})

#: 历史行没有轮次信息时的哨兵值（迁移前的行、以及非作业路径创建的行）。
UNKNOWN_ORDER = -1
UNKNOWN_RETRY = 0

REASON_OK = "ok"
REASON_STALE_ROUND = "stale_round"
REASON_STALE_RETRY = "stale_retry"
REASON_LOWER_PRIORITY = "lower_priority_writer"
REASON_DETAIL_OWNER = "detail_owner"


@dataclass(frozen=True)
class CaseWriteAttempt:
    """一次写入尝试：谁、哪一轮。"""

    writer: str
    execution_order: int = UNKNOWN_ORDER
    retry_count: int = UNKNOWN_RETRY
    job_id: Optional[str] = None

    @property
    def priority(self) -> int:
        return WRITER_PRIORITY.get(self.writer, 0)

    @property
    def round(self) -> tuple[int, int]:
        return (int(self.execution_order or 0), int(self.retry_count or 0))


@dataclass(frozen=True)
class CaseWriteState:
    """目标行的当前写入状态。"""

    latest_status: str = ""
    execution_order: int = UNKNOWN_ORDER
    retry_count: int = UNKNOWN_RETRY
    writer: Optional[str] = None
    has_detail: bool = False

    @classmethod
    def from_row(cls, row: Any) -> "CaseWriteState":
        """从 ORM 行（或任意同名字段对象）构造；缺列时按历史行处理。"""
        if row is None:
            return cls()
        status = getattr(row, "latest_status", "") or ""
        order = getattr(row, "latest_execution_order", None)
        retry = getattr(row, "latest_retry_count", None)
        return cls(
            latest_status=str(getattr(status, "value", status)),
            execution_order=UNKNOWN_ORDER if order is None else int(order),
            retry_count=UNKNOWN_RETRY if retry is None else int(retry),
            writer=getattr(row, "latest_writer", None),
            has_detail=getattr(row, "latest_result_id", None) is not None,
        )

    @property
    def round(self) -> tuple[int, int]:
        return (int(self.execution_order or 0), int(self.retry_count or 0))


@dataclass(frozen=True)
class WriteDecision:
    allowed: bool
    reason: str = REASON_OK
    detail: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {"allowed": self.allowed, "reason": self.reason, "detail": self.detail}


def decide_write(state: CaseWriteState, attempt: CaseWriteAttempt) -> WriteDecision:
    """判断本次写入是否生效。纯函数，顺序无关性由"轮次 + 优先级"共同保证。"""
    # 1) 场景级兜底不得覆盖已有明细（迁移前历史行没有 writer/round，只能靠明细判定）
    if attempt.writer in COARSE_WRITERS and state.has_detail:
        return WriteDecision(
            False,
            REASON_DETAIL_OWNER,
            f"{attempt.writer} 让位：该行已有明细（latest_result_id），结论由细粒度写入者负责",
        )

    # 2) 轮次：旧作业不得覆盖新轮次
    if attempt.round < state.round:
        return WriteDecision(
            False,
            REASON_STALE_ROUND if attempt.execution_order < state.execution_order else REASON_STALE_RETRY,
            f"本轮 {attempt.round} 早于已落库轮次 {state.round}，拒绝覆盖",
        )

    # 3) 同轮次：低优先级写入者让位
    if attempt.round == state.round and state.writer:
        current_priority = WRITER_PRIORITY.get(state.writer, 0)
        if attempt.priority < current_priority:
            return WriteDecision(
                False,
                REASON_LOWER_PRIORITY,
                f"{attempt.writer}({attempt.priority}) 低于已写入者 {state.writer}({current_priority})，拒绝覆盖",
            )

    return WriteDecision(True, REASON_OK, "")


def build_attempt(
    writer: str,
    *,
    job_id: Any = None,
    execution_order: Any = None,
    retry_count: Any = None,
) -> CaseWriteAttempt:
    """从作业上下文构造写入尝试；缺字段按"无轮次信息"处理（不阻断写入）。"""
    try:
        order = int(execution_order) if execution_order is not None else UNKNOWN_ORDER
    except (TypeError, ValueError):
        order = UNKNOWN_ORDER
    try:
        retry = int(retry_count) if retry_count is not None else UNKNOWN_RETRY
    except (TypeError, ValueError):
        retry = UNKNOWN_RETRY
    return CaseWriteAttempt(
        writer=writer,
        execution_order=order,
        retry_count=retry,
        job_id=str(job_id) if job_id else None,
    )
