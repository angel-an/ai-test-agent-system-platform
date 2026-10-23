# -*- coding: utf-8 -*-
"""rev200：组类必填（checkbox_group）要把"填了"和"勾上了"分开。

背景（2026-10-23）：回显通道的 5 条 unverified 里，有若干条不是"回显不一致"，
而是**保存根本没发出去**——`required_fields` 的复选组没勾上，前端必填把提交拦了。
旧实现统计的是 `count() > 0`（控件存在即算命中），覆盖层挡住点击时会给出
"已预填"的假象，随后 save / reopen 全线退化成 unverified。

本文件守三件事：
1. 命中口径 = **回读 isChecked**（点完再读一次，不是点完就信）；
2. 勾上数不足 opts 长度即记 miss，并写明"勾上 m/n"（部分成功也要能看见）；
3. `value` 为空时构造最小合法值（勾第一个可用选项），但必须**标出来**
   （`via: auto_first_option`）——自动构造的值不能冒充"配置里要求的那个"。
"""

from __future__ import annotations

import re

from app.services.web_case_compiler import COMPILER_REV, _FORM_EXECUTOR_TS


def _rev_num() -> int:
    return int(re.search(r"rev(\d+)", COMPILER_REV).group(1))


def _checkbox_block() -> str:
    start = _FORM_EXECUTOR_TS.index("if (String(rf.kind) === 'checkbox_group'")
    end = _FORM_EXECUTOR_TS.index("const sel2 = (String(rf.kind) === 'textarea'", start)
    return _FORM_EXECUTOR_TS[start:end]


def test_rev_at_least_200():
    assert _rev_num() >= 200


def test_counted_only_after_readback():
    block = _checkbox_block()
    # 点击前读一次（已勾则不重复点），点击后再读一次（回读判据）
    assert block.count("await box.isChecked()") >= 2
    # 命中计数来自 checkedNow，而不是"找到控件"
    assert "if (checked) checkedNow.push(opt)" in block
    assert "checkedNow.length === opts.length" in block


def test_old_count_hit_standard_is_gone():
    """旧口径：`hit += 1` 只要控件存在就算命中 —— 必须删干净，否则两种口径同时存在。"""
    block = _checkbox_block()
    assert "hit += 1" not in block
    assert "let hit = 0" not in block


def test_partial_pick_is_a_miss_with_numbers():
    block = _checkbox_block()
    assert "勾上 ${checkedNow.length}/${opts.length} 个选项" in block


def test_empty_value_constructs_minimal_legal_value_but_is_marked():
    block = _checkbox_block()
    assert "const autoPick = opts.length === 0;" in block
    assert "autoPick ? 'auto_first_option'" in block
    # 自动构造只许进 trace，不许静默当成配置值
    assert "'auto_first_option'" in block


def test_auto_pick_does_not_leak_into_echo_compare():
    """自动勾选的值不得进入回显比对口径（pickedByField 只由 fields 决定）。"""
    start = _FORM_EXECUTOR_TS.index("if (String(rf.kind) === 'checkbox_group'")
    end = _FORM_EXECUTOR_TS.index("const sel2 = (String(rf.kind) === 'textarea'", start)
    block = _FORM_EXECUTOR_TS[start:end]
    assert "pickedByField" not in block
