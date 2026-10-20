# -*- coding: utf-8 -*-
"""用例结论写入守卫回归（N2，2026-10-17）。

要证明的**不是**"函数返回 False"，而是三条顺序无关性：

1. 同一份产物，mapper 与 engine 无论谁先写完，最终结论都必须是 mapper 的；
2. 旧作业（更小的 ``execution_order``/``retry_count``）晚完成，也不能覆盖新轮次；
3. 迁移前的历史行（无 writer/round）不能因为"没有信息"就被粗粒度结论覆盖，
   只要它已经有明细（``latest_result_id``）。

故意不测的东西：``finalize`` 只在 NOT_EXECUTED 才升级——那是调用方的业务规则，
放这里测会让守卫承担它不该承担的判断。
"""

from __future__ import annotations

from types import SimpleNamespace

from app.services.case_result_write_guard import (
    REASON_DETAIL_OWNER,
    REASON_LOWER_PRIORITY,
    REASON_STALE_RETRY,
    REASON_STALE_ROUND,
    CaseWriteAttempt,
    CaseWriteState,
    build_attempt,
    decide_write,
)


def _row(
    *,
    status: str = "NOT_EXECUTED",
    order: int = -1,
    retry: int = 0,
    writer: str | None = None,
    result_id: str | None = None,
):
    return SimpleNamespace(
        latest_status=status,
        latest_execution_order=order,
        latest_retry_count=retry,
        latest_writer=writer,
        latest_result_id=result_id,
    )


def _state(**kw) -> CaseWriteState:
    return CaseWriteState.from_row(_row(**kw))


class TestRoundOrdering:
    def test_newer_job_overwrites_older(self):
        decision = decide_write(
            _state(order=1, retry=0, writer="mapper"),
            CaseWriteAttempt("mapper", execution_order=2, retry_count=0),
        )
        assert decision.allowed, decision

    def test_older_job_is_rejected_even_if_more_authoritative(self):
        """旧轮次晚完成：mapper 再权威也不能覆盖新轮次结论。"""
        decision = decide_write(
            _state(status="PASSED", order=2, retry=0, writer="engine"),
            CaseWriteAttempt("mapper", execution_order=1, retry_count=0),
        )
        assert not decision.allowed
        assert decision.reason == REASON_STALE_ROUND

    def test_older_retry_of_same_job_is_rejected(self):
        decision = decide_write(
            _state(status="PASSED", order=1, retry=2, writer="mapper"),
            CaseWriteAttempt("mapper", execution_order=1, retry_count=1),
        )
        assert not decision.allowed
        assert decision.reason == REASON_STALE_RETRY

    def test_newer_retry_overwrites(self):
        decision = decide_write(
            _state(status="FAILED", order=1, retry=1, writer="mapper"),
            CaseWriteAttempt("mapper", execution_order=1, retry_count=2),
        )
        assert decision.allowed


class TestWriterPriority:
    def test_engine_cannot_overwrite_mapper_same_round(self):
        """核心回归：engine 后写不得把 SKIPPED 冲成 PASSED。"""
        decision = decide_write(
            _state(status="SKIPPED", order=1, retry=0, writer="mapper", result_id="r1"),
            CaseWriteAttempt("engine", execution_order=1, retry_count=0),
        )
        assert not decision.allowed
        assert decision.reason in (REASON_LOWER_PRIORITY, REASON_DETAIL_OWNER)

    def test_mapper_can_overwrite_engine_same_round(self):
        """反序：engine 先写 PASSED，mapper 后写 SKIPPED → 允许（最终与顺序无关）。"""
        decision = decide_write(
            _state(status="PASSED", order=1, retry=0, writer="engine"),
            CaseWriteAttempt("mapper", execution_order=1, retry_count=0),
        )
        assert decision.allowed, decision

    def test_manual_does_not_overwrite_mapper(self):
        decision = decide_write(
            _state(status="SKIPPED", order=1, writer="mapper"),
            CaseWriteAttempt("manual", execution_order=1, retry_count=0),
        )
        assert not decision.allowed
        assert decision.reason == REASON_LOWER_PRIORITY

    def test_finalize_may_upgrade_mapper_not_executed(self):
        """失败终态必须能盖掉 mapper 留下的 NOT_EXECUTED（作业失败且脚本没产出断言）。"""
        decision = decide_write(
            _state(status="NOT_EXECUTED", order=1, writer="mapper"),
            CaseWriteAttempt("finalize", execution_order=1, retry_count=0),
        )
        assert decision.allowed, decision


class TestLegacyRows:
    def test_engine_skips_row_with_detail_but_no_round_info(self):
        """迁移前的行没有 writer/round，只能靠"已有明细"判断细粒度结果。"""
        decision = decide_write(
            _state(status="SKIPPED", order=-1, writer=None, result_id="legacy-result"),
            CaseWriteAttempt("engine", execution_order=3, retry_count=0),
        )
        assert not decision.allowed
        assert decision.reason == REASON_DETAIL_OWNER

    def test_engine_writes_row_without_detail(self):
        decision = decide_write(
            _state(status="NOT_EXECUTED", order=-1, writer=None, result_id=None),
            CaseWriteAttempt("engine", execution_order=3, retry_count=0),
        )
        assert decision.allowed

    def test_row_object_without_new_columns_is_tolerated(self):
        """模型列尚未迁移（或单测用简化对象）时按历史行处理，不抛异常。"""
        state = CaseWriteState.from_row(SimpleNamespace(latest_status="NOT_EXECUTED"))
        assert state.execution_order == -1
        assert state.retry_count == 0
        assert state.has_detail is False


class TestBuildAttempt:
    def test_missing_context_is_unknown_round(self):
        attempt = build_attempt("mapper", job_id=None, execution_order=None, retry_count=None)
        assert attempt.round == (-1, 0)

    def test_invalid_values_do_not_raise(self):
        attempt = build_attempt("mapper", execution_order="abc", retry_count=object())
        assert attempt.execution_order == -1
        assert attempt.retry_count == 0

    def test_string_numbers_are_parsed(self):
        attempt = build_attempt("engine", job_id="j1", execution_order="7", retry_count="2")
        assert attempt.round == (7, 2)
        assert attempt.job_id == "j1"
