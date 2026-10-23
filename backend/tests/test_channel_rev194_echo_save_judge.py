# -*- coding: utf-8 -*-
"""rev194：echo 保存判据必须与「弹窗是否存在」解耦；预填失败必须可见。

背景（2026-10-23，TR-806 实测，rev192 产物）：
  * `ECHO_STAGE save_result` 已经拿到 `notices="题目1：请输入标题"`（前端拦截的铁证），
    断言结论却仍是「保存后列表未找到记录行——可能保存失败或列表未刷新」；
  * 根因：rev192 的「任意提示即拦截」分支带着 `!modalGone` 守卫，而 route_form 页面
    根本没有弹窗 ⇒ `modalGone` 恒真 ⇒ 该分支永不生效；
  * 同轮暴露第二个口子：`required_fields` 的字段行找不到时**静默 continue**，
    「题目标题没填上」这件事在日志里一个字都没有，只能从 SUT 的提示倒推。

本用例锁三件事：
  ① 成功判据只看 URL 离开 / 成功提示，不再把「弹窗关闭」当成功信号；
  ② 拦截提示命中即判 blocked，不再被 modalGone 短路；
  ③ 预填逐字段留痕（`stageMark('prefill')`），未落地字段随结论给出。
"""

from __future__ import annotations

import re

import app.services.web_case_compiler as m


def _ts() -> str:
    return m._FORM_EXECUTOR_TS


def _echo_block() -> str:
    """截 echo 段的保存判定部分（从 save_result 打点往后 40 行内）。"""
    ts = _ts()
    idx = ts.index("stageMark('save_result'")
    return ts[idx: idx + 2600]


class TestSaveJudgeDecoupledFromModal:
    def test_success_signal_has_no_modal_dependency(self):
        block = _echo_block()
        assert "const saveConfirmed = okToast || leftForm;" in block

    def test_blocked_branch_no_longer_guarded_by_modal_gone(self):
        block = _echo_block()
        # 旧守卫：!modalGone && !okToast && !leftForm && noticeTxt
        assert "!modalGone && !okToast && !leftForm && noticeTxt" not in block
        assert "const blockedByNotice = !saveConfirmed && !!noticeTxt;" in block
        assert "const blockedNoSignal = !saveConfirmed && !noticeTxt && !modalGone;" in block

    def test_blocked_message_names_the_notice(self):
        block = _echo_block()
        assert "保存被拦截：${noticeTxt" in block


class TestPrefillIsVisible:
    def test_prefill_stage_is_marked(self):
        ts = _ts()
        assert "stageMark('prefill'" in ts

    def test_failed_prefill_is_collected_and_reported(self):
        ts = _ts()
        assert "const prefillMiss: string[] = [];" in ts
        # 三类失败都要登记：字段行找不到 / 行内无控件 / 填了但回读为空
        assert "未找到该字段行" in ts
        assert "字段行内无可填控件" in ts
        assert "填了但回读为空" in ts
        # 结论里必须带上，否则只能靠人倒推
        assert "const missNote = prefillMiss.length" in ts
        assert block_uses_miss_note()

    def test_prefill_readback_present(self):
        ts = _ts()
        assert "await box.inputValue()" in ts


def block_uses_miss_note() -> bool:
    """两个失败分支都必须拼上 missNote（否则等于没记）。"""
    block = _echo_block()
    return block.count("+ missNote") >= 2


class TestRev:
    def test_rev_at_least_194(self):
        assert int(re.match(r"rev(\d+)", m.COMPILER_REV).group(1)) >= 194
