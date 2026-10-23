"""rev139 回归：``COVERAGE_RECONCILE`` 必须把**提交层重发**与**真缺口**分开。

背景（2026-10-20，TR-755 实测）：rev138 修好字段作用域后，两条 ``required_error``
在客户端层各记一条 unverified，随后进入提交层**又各记一条**（真实提交才算证据，
这是设计）。对账行旧公式：

    emitted = executable + scenario_executed + unexplained_emitted

把重发当成"多发"，于是打出 ``unexplained_emitted=2 / balanced=false`` ——
"没丢"被报成"丢了 2 条"。对账行的存在意义是**判断有没有丢失**，
一旦它自己制造假缺口，就会把排障引去查根本没丢的地方（与 rev136 的 -2 同一类错误）。

本文件锁死三件事：
1. 提交层有独立计数 ``submitRec``（不拿 ``queued`` 当代数：超时/中断也会写一条）；
2. ``submit_phase.emitted`` 随 summary 下发；
3. 恒等式与日志都显式包含 ``submit_resubmitted``。
"""

from __future__ import annotations

import json

from app.services.web_case_compiler import build_form_plan, render_form_executor


def _plan():
    return build_form_plan({
        "assertion_count": 1,
        "cases": [{
            "identifier": "TC-Q1", "rule_id": "FR-1", "field": "问卷标题",
            "assertions": [{"kind": "required", "field": "问卷标题"}],
        }],
    }, {"fields": {"问卷标题": {"kind": "input", "selector_hint": "#sbjTypeName"}}})


def _script() -> str:
    return render_form_executor(_plan())


class TestSubmitResubmitAccounting:
    def test_submit_rec_is_the_only_exit_of_submit_phase(self):
        """提交层所有结果写出都必须经过 submitRec，否则计数会漏。"""
        script = _script()
        seg = script.split("const submitQueue", 1)[1].split("// ===== 网络隔离", 1)[0]
        assert "let submitEmitted = 0;" in seg
        assert "submitRec = (check: any, name: string, outcome: CheckOutcome, observed: string)" in seg
        assert "recAll(check, name, outcome, observed);" in seg
        # 提交层内不应再有裸 recAll(check, item.name, ...)
        tail = script.split("const submitQueue", 1)[1]
        assert "recAll(check, item.name," not in tail

    def test_summary_exposes_submit_emitted(self):
        script = _script()
        seg = script.split("submit_phase: {", 1)[1][:400]
        assert "emitted: submitEmitted" in seg

    def test_reconcile_counts_resubmission_separately(self):
        script = _script()
        seg = script.split("COVERAGE_RECONCILE", 1)[0][-1200:]
        assert "const __submitA = summary.submit_phase.emitted || 0;" in seg
        # 减数用 **emitted_raw**（rev165 起）：summary.total 已剔除被提交层覆盖的
        # 过程态，拿它做减法会把「少发过程态」误读成异常。此处曾长期钉 ``summary.total -``，
        # 自 rev165 改实现后一直是红的（无人跑门禁集）。恒等式的语义没变：
        # 「实际写出的行数 = 执行层 + 负路径 + 重提交层」。
        assert "summary.emitted_raw - (__execA + __scenarioA + __submitA)" in seg
        rec = script.split("COVERAGE_RECONCILE", 1)[1][:700]
        assert "submit_resubmitted: __submitA" in rec

    def test_identity_is_documented_in_order(self):
        """注释里的恒等式与实际代码必须一致（否则下一个人照注释改回错公式）。"""
        script = _script()
        line = [l for l in script.splitlines()
                if "emitted   = executable + scenario_executed" in l]
        assert line, "缺少 emitted 恒等式注释"
        assert "submit_resubmitted" in line[0]
