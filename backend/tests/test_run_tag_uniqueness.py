# -*- coding: utf-8 -*-
"""A2（rev124）：真实落库的唯一化 —— ``{{RUN_TAG}}`` 运行期展开。

要防的三件事（都有过实测代价）：
1. **占位符被编译期吃掉**：``FORM_PLAN`` 是产物快照，若生成期就替换成具体值，
   重跑同一份产物还是同一个编号 ⇒ 服务端唯一约束拒绝；而期望受理通道会把它
   判成「疑似缺陷坐实」= 假缺陷。
2. **值里没占位符却被改写**：唯一化必须显式声明，不能根据"看起来像编码"去猜。
3. **``RUN_TAG`` 只是常量、没有任何人用它**：所以既测替换函数，也测 fill/type
   两条真实写入路径确实调用了它。
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess

import pytest

from app.services.web_case_compiler import (
    RUN_TAG_TOKEN,
    build_form_plan,
    parse_negative_scenarios,
    render_form_executor,
    run_tag_gaps,
    run_tag_usage,
)

ACCEPT = {"api_path_hint": "/api/icdp/v1/md/item/add",
          "allow": {"path_prefix": "/api/icdp/v1/md/item/add"}}


def _locators(setup: list[dict], *, accept: bool = True) -> dict:
    scenario = {
        "scenario_id": "S1",
        "expect_accept" if accept else "expect_reject": ACCEPT,
        "setup": setup,
    }
    if not accept:
        scenario["expect_reject"] = {"api_path_hint": "/x", "result_code_not": "0",
                                     "allow": {"path_prefix": "/x"}}
    return {"fields": {"商品編碼": {"kind": "input"}, "商品名稱(CN)": {"kind": "input"}},
            "negative_scenarios": [scenario]}


def test_usage_counts_only_steps_with_token():
    scenarios, _ = parse_negative_scenarios(_locators([
        {"action": "fill", "field": "商品編碼", "value": "YLUATI" + RUN_TAG_TOKEN},
        {"action": "fill", "field": "商品名稱(CN)", "value": "固定名"},
        {"action": "wait", "ms": 100},
    ])["negative_scenarios"])
    usage = run_tag_usage(scenarios)
    assert usage == {"enabled": True, "fields": ["商品編碼"], "steps": 1}


def test_usage_empty_when_no_token():
    scenarios, _ = parse_negative_scenarios(_locators([
        {"action": "fill", "field": "商品編碼", "value": "YLUATI10170902"},
    ])["negative_scenarios"])
    assert run_tag_usage(scenarios) == {"enabled": False, "fields": [], "steps": 0}


def test_gaps_flag_fixed_code_in_accept_scenario():
    scenarios, _ = parse_negative_scenarios(_locators([
        {"action": "fill", "field": "商品編碼", "value": "YLUATI10170902"},
    ])["negative_scenarios"])
    gaps = run_tag_gaps(scenarios)
    assert len(gaps) == 1
    assert gaps[0]["field"] == "商品編碼"
    assert RUN_TAG_TOKEN in gaps[0]["hint"]


def test_gaps_skip_tokenised_and_non_numeric_values():
    scenarios, _ = parse_negative_scenarios(_locators([
        {"action": "fill", "field": "商品編碼", "value": "YLUATI" + RUN_TAG_TOKEN},
        {"action": "fill", "field": "商品名稱(CN)", "value": "固定中文名"},
    ])["negative_scenarios"])
    assert run_tag_gaps(scenarios) == []


def test_gaps_skip_pure_digits():
    """纯数字是业务值（电话/数量），不是唯一编码 —— 报出来只是噪声。

    TR-733 实测：店鋪電話 ``12345678`` 被旧判据命中，每轮一条 RUN_TAG_GAP。
    """
    scenarios, _ = parse_negative_scenarios(_locators([
        {"action": "fill", "field": "商品編碼", "value": "12345678"},
    ])["negative_scenarios"])
    assert run_tag_gaps(scenarios) == []


def test_gaps_ignore_reject_scenarios():
    """负路径场景本来就该用"注定被拒"的值，不该催人加时间戳。"""
    scenarios, _ = parse_negative_scenarios(_locators([
        {"action": "fill", "field": "商品編碼", "value": "YLUATI10170902"},
    ], accept=False)["negative_scenarios"])
    assert run_tag_gaps(scenarios) == []


def test_plan_carries_usage_and_gap():
    plan = build_form_plan({"cases": []}, _locators([
        {"action": "fill", "field": "商品編碼", "value": "YLUATI" + RUN_TAG_TOKEN},
        {"action": "fill", "field": "商品名稱(CN)", "value": "YLUAT_TEST_20261016"},
    ]))
    assert plan is not None
    assert plan["run_tag"]["enabled"] is True
    assert plan["run_tag"]["fields"] == ["商品編碼"]
    assert [g["field"] for g in plan["run_tag_gap"]] == ["商品名稱(CN)"]


def test_rendered_keeps_token_and_ships_logs():
    plan = build_form_plan({"cases": []}, _locators([
        {"action": "fill", "field": "商品編碼", "value": "YLUATI" + RUN_TAG_TOKEN},
    ]))
    src = render_form_executor(plan)
    # 快照里必须还是占位符：编译期替换 = 每次跑同一个编号
    assert f"YLUATI{RUN_TAG_TOKEN}" in src
    assert "const RUN_TAG_INFO = (() => {" in src
    assert "const effective = runTagValue(field, step.value);" in src
    assert "await input.fill(effective, {timeout: OP_T});" in src
    assert "const typed = runTagValue(" in src
    assert "console.log('RUN_TAG ' + JSON.stringify({" in src
    assert "console.log('RUN_TAG_USED ' + JSON.stringify({" in src
    assert "console.log('RUN_TAG_GAP ' + JSON.stringify(gap));" in src
    # 填入后回读：控件截断会让"已唯一化"变成假绿
    assert "console.log('RUN_TAG_APPLIED ' + JSON.stringify({" in src
    assert "truncated: actual !== effective" in src
    # 「数据重复」不得再进「疑似缺陷坐实」分支
    assert "} else if (dupLikely) {" in src


NODE = shutil.which("node")


def _runtime_snippet(src: str) -> str:
    """从渲染产物里**原样截取** RUN_TAG 相关片段（不手抄逻辑，避免测到副本）。"""
    start = src.index("const RUN_TAG_INFO = (() => {")
    end = src.index("const runTagValue = (field: string, raw: any): string => {")
    body = src[start:end]
    body = body.replace("(process.env as any)", "(process.env)")
    body = body.replace("(v: any)", "(v)")
    harness = body + "\nconsole.log(JSON.stringify({tag: RUN_TAG, sub: substRunTag('A{{RUN_TAG}}B')}));\n"
    return harness


@pytest.mark.skipif(not NODE, reason="需要 node 才能验证运行期语义")
def test_runtime_tag_is_pinned_by_env_and_substituted():
    plan = build_form_plan({"cases": []}, _locators([
        {"action": "fill", "field": "商品編碼", "value": "YLUATI" + RUN_TAG_TOKEN},
    ]))
    harness = _runtime_snippet(render_form_executor(plan))
    # Windows 上 node 必须拿到完整环境（缺 SystemRoot 会 CSPRNG 断言崩溃），
    # 所以只在 os.environ 副本上叠加/摘除我们的变量。
    out = subprocess.run([NODE, "-e", harness], capture_output=True, text=True,
                         encoding="utf-8", errors="replace",
                         env={**os.environ, "WEB_RUN_TAG": "MMDD1234XY"})
    assert out.returncode == 0, out.stderr[-400:]
    got = json.loads(out.stdout.strip().splitlines()[-1])
    assert got["tag"] == "MMDD1234XY"
    assert got["sub"] == "AMMDD1234XYB"


@pytest.mark.skipif(not NODE, reason="需要 node 才能验证运行期语义")
def test_runtime_tag_generated_is_time_based_unique():
    plan = build_form_plan({"cases": []}, _locators([
        {"action": "fill", "field": "商品編碼", "value": "YLUATI" + RUN_TAG_TOKEN},
    ]))
    harness = _runtime_snippet(render_form_executor(plan))
    env = {k: v for k, v in os.environ.items() if k != "WEB_RUN_TAG"}
    out = subprocess.run([NODE, "-e", harness], capture_output=True, text=True,
                         encoding="utf-8", errors="replace", env=env)
    assert out.returncode == 0, out.stderr[-400:]
    got = json.loads(out.stdout.strip().splitlines()[-1])
    # MMDDHHMMSS + 3 位随机：13 字符，便于拼进业务编码（实测 18 字符未被截断）
    assert re.fullmatch(r"\d{10}[A-Z0-9]{3}", got["tag"]), got["tag"]
    assert got["sub"] == f"A{got['tag']}B"
