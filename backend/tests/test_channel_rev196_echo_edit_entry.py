# -*- coding: utf-8 -*-
"""rev196：重开编辑页那一步必须**验证效果**，否则会在列表页上假装读到了回显。

背景（2026-10-23，TR-807）：echo 走到最后一步报
`missing=["适用门店:编辑页字段不存在"]`，而探针实拍的编辑页
（`#/system/survey/surveyOperation/edit?type=1&id=…`）里
9 个表单项含「适用门店」、明细子表 2 行（8882/8888）全在。

根因：`row.locator('a').filter(编辑).click()` 抛错被 `.catch(() => undefined)` 吞掉
（本 SUT 操作列独立成表「查看/编辑/启用/更多」，数据行的 a 点不动），
页面仍停在列表页，于是「字段不存在」——判定假阴性 + 结论误导。

本用例锁：① 三策略点击；② 效果验证三条件；③ 无效时明确报错不继续读；
④ 新标签页形态在新页读回显。
"""

from __future__ import annotations

import re

import app.services.web_case_compiler as m


def _ts() -> str:
    return m._FORM_EXECUTOR_TS


def _edit_block() -> str:
    ts = _ts()
    start = ts.index("const urlBeforeEdit")
    return ts[start: start + 4200]


class TestClickEffectVerification:
    def test_effect_predicate_has_three_signals(self):
        block = _edit_block()
        assert "const editEntered = async (): Promise<boolean> => {" in block
        assert "pages().length > pagesBeforeEdit" in block
        assert "!== urlBeforeEdit" in block
        assert "n > itemsBeforeEdit" in block

    def test_three_click_strategies_present(self):
        block = _edit_block()
        assert "'locator'" in block
        assert "'mouse-on-link'" in block
        assert "'action-table'" in block

    def test_blocked_message_is_actionable(self):
        block = _edit_block()
        assert "编辑入口点击无效" in block
        assert "edit_blocked" in block

    def test_read_back_switches_to_new_tab(self):
        block = _edit_block()
        assert "const editPage: any = ctxPages.length > pagesBeforeEdit" in block
        assert "edit_new_tab" in block
        # editRoot 必须来自 editPage，而不是全局 page
        assert "})(editPage);" in block


class TestRev:
    def test_rev_at_least_196(self):
        assert int(re.match(r"rev(\d+)", m.COMPILER_REV).group(1)) >= 196
