# -*- coding: utf-8 -*-
"""rev117（2026-10-19）：产物编译器版本指纹（P0-2）+ 提交项看门狗（P0-1）。

来源是当天的真实运行，不是推断：

1. **旧产物无法识别**。``web_tests`` 里 657 个脚本都是 10-17 前的产物，编译器后来改过
   预算下发（rev116）与取帧（rev116-fix），但脚本里没有任何版本线索。
   TR-703 用旧产物跑满 1745s 被硬杀、连 ``TEST_BUDGET`` 行都没有；TR-725 用新产物
   1710s 自行收尾并报 ``Test timeout of 1710000ms exceeded``。
   **同一现象、两种根因，从数据上无法区分** ⇒ 生成期写指纹、执行期比对。

2. **单个提交项能吃掉整批预算**。rev90 的 5 分钟预算只在「项与项之间」检查；
   实测商品主数据 TC-238772 卡在提交层 1710s、stdout 零输出，既拿不到结论也定位不到卡点。
   修法：每项 90s 看门狗 + 阶段心跳 ``SUBMIT_STAGE``。

本文件守的边界（都是"改坏了就白跑一轮"的地方）：
- 指纹必须写进 **脚本本体**（执行器读的是文件，不是 DB 记录）；
- 无指纹/版本不同必须判 stale，且 ``rev11`` 与 ``rev110`` 不能被判成同一个；
- 看门狗必须真的包住**整个**提交项执行体（残留 ``continue`` 即意味着有代码在竞速之外）。
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.services.execution.runner import (  # noqa: E402
    artifact_stale_line,
    read_artifact_rev,
)
from app.services.web_case_compiler import (  # noqa: E402
    COMPILER_REV,
    build_form_plan,
    compile_cases,
    render_form_executor,
)


# ---------------------------------------------------------------- P0-2 指纹


def test_stamp_writes_rev_into_spec_header_and_is_idempotent():
    from app.services.web_case_link_service import WebCaseLinkService

    stamped = WebCaseLinkService._stamp_compiler_rev("const x = 1;\n")
    assert f"// COMPILER_REV: {COMPILER_REV}" in stamped
    assert "const ARTIFACT_COMPILER_REV =" in stamped
    # 幂等：重复盖章不应出现两行指纹（重生成路径会多次调用）
    again = WebCaseLinkService._stamp_compiler_rev(stamped)
    assert again == stamped
    assert again.count("COMPILER_REV:") == 1


def test_read_artifact_rev_reads_stamped_and_returns_none_for_legacy(tmp_path):
    from app.services.web_case_link_service import WebCaseLinkService

    fresh = tmp_path / "fresh.spec.ts"
    fresh.write_text(WebCaseLinkService._stamp_compiler_rev("// body\n"), encoding="utf-8")
    assert read_artifact_rev(fresh) == COMPILER_REV

    legacy = tmp_path / "legacy.spec.ts"
    legacy.write_text("import { test } from '@playwright/test';\n// 老产物，无指纹\n", encoding="utf-8")
    assert read_artifact_rev(legacy) is None

    missing = tmp_path / "nope.spec.ts"
    assert read_artifact_rev(missing) is None


def test_artifact_stale_line_distinguishes_legacy_mismatch_and_match():
    legacy = artifact_stale_line(None, "rev117", "a/b.spec.ts")
    assert json.loads(legacy.split(" ", 1)[1])["reason"] == "legacy_no_fingerprint"

    mismatch = artifact_stale_line("rev116", "rev117", "a/b.spec.ts")
    payload = json.loads(mismatch.split(" ", 1)[1])
    assert payload["reason"] == "rev_mismatch"
    assert payload["artifact_rev"] == "rev116"
    assert payload["current_rev"] == "rev117"

    assert artifact_stale_line("rev117", "rev117", "a/b.spec.ts") == ""


def test_artifact_stale_line_does_not_prefix_match_rev_names():
    """rev11 与 rev110 必须判为不同 —— 前缀匹配会让"禁用一个版本"连坐另一个。"""
    assert artifact_stale_line("rev11", "rev110", "s") != ""
    assert artifact_stale_line("rev110", "rev11", "s") != ""


def test_runner_run_uses_the_stale_gate():
    """源码级守卫：run() 里必须真的接入比对，否则常量写对了也不会生效。

    rev118 起判定拆成 ``artifact_stale_payload``（结构化 reason）+ ``should_block_stale``
    （模式 × reason → 是否拒绝），并额外比对 digest。断言的落点随之更新，
    但要求比原来**更强**：三个入口缺一个都算没接上。
    """
    src = Path(__file__).resolve().parents[1] / "app/services/execution/runner.py"
    text = src.read_text(encoding="utf-8")
    assert "artifact_stale_payload(" in text
    assert "should_block_stale(reason, mode)" in text
    assert "read_artifact_rev(script_path)" in text
    assert "read_artifact_digest(script_path)" in text
    assert "compiler_source_digest()" in text
    assert "WEB_TEST_STRICT_ARTIFACT" in text
    assert "artifact_note + stdout" in text


# ---------------------------------------------------------------- P0-1 看门狗


@pytest.fixture(scope="module")
def executor() -> str:
    from tests.test_web_case_compiler import RULE_CASES, RECHARGE_FORM_LOCATORS

    plan = build_form_plan(compile_cases(RULE_CASES), RECHARGE_FORM_LOCATORS)
    return render_form_executor(plan)


def _submit_body(executor: str) -> str:
    start = executor.index("const runSubmitItem = async (): Promise<void> => {")
    end = executor.index("        let wdTimer: any = null;", start)
    return executor[start:end]


def test_submit_item_is_wrapped_by_watchdog(executor):
    assert "const SUBMIT_ITEM_MS = Number(process.env.WEB_SUBMIT_ITEM_MS" in executor
    assert "Promise.race([runSubmitItem(), watchdog])" in executor
    assert "__submit_item_timeout__" in executor
    # 超时必须能说清卡在哪个阶段
    assert "SUBMIT_TIMEOUT ${JSON.stringify(" in executor


def test_whole_submit_body_runs_inside_the_race(executor):
    """执行体内不得残留 ``continue``：残留即代表有代码在 Promise.race 之外裸奔，
    看门狗管不到它（这正是 1710s 挂死的形态）。"""
    body = _submit_body(executor)
    assert body.count("continue;") == 0
    # 页面已关闭/只读字段/Select/未定位到输入框等早退，全部改成 return
    assert body.count("return;") >= 6


def test_timeout_marks_remaining_items_unverified(executor):
    tail = executor[executor.index("        let wdTimer: any = null;"):]
    assert "submitAborted = `提交层：前一项" in tail
    assert "本项未验证" in tail
    # 单项超时不许命中 blocked（blocked 代表定位器结构性失效，会把结论带偏）
    # rev139：提交层的发出口统一走 `submitRec`（= 计数 + recAll），断言跟着改名。
    # 语义未变，改名的目的见 test_submit_resubmit_reconcile_rev139：
    # 只有逐个计数才能把「提交层重发」与「真缺口」分开。
    assert "submitRec(check, item.name, 'unverified',\n                   `提交层：单项超时" in tail


def test_stage_markers_cover_the_risky_steps(executor):
    for stage in ("open_form", "field_ready", "prepare_value", "submit_click", "settle_done", "evidence"):
        assert f"markStage('{stage}')" in executor, stage
