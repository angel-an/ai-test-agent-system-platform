# -*- coding: utf-8 -*-
"""rev198：软校验的字段标签要能用**别名**命中，别再把"别名早配好"读成"字段不存在"。

背景（2026-10-23）：覆盖度行长期打 ``field_labels_found: 0 / field_labels_total: N``。
排查下来不是字段缺失，而是两个来源对不上：

* 覆盖度只拿**文档字段名**去 ``getByText``；
* 配置方早就把「文档名 → 页面定位器键」写进了 ``field_locators.aliases``，
  但这段软校验从不读别名表。

于是"配了别名、页面上字段确实在、found 还是 0"三者同时成立，读日志的人只能去怀疑
字段不存在。rev116 放宽过全/半角括号与冒号，剩下的就是纯命名差异 —— 别名表本就是
为这类差异存在的。

本文件守四件事：
1. 别名表进了片段（空表也要显式发，便于确认"这次真的没配"）；
2. 候选 = ``[文档名, ...别名]``，且**去重掉与文档名相同的别名**；
3. 命中来源可分辨：靠别名救回的记进 ``field_labels_alias_used``，
   与"页面真有该文案"分开看 —— 否则等于用一种噪声换另一种；
4. **软校验仍不判定失败**，且真未命中照样进 ``field_labels_missing``。
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from types import SimpleNamespace

import pytest

from app.services.web_case_compiler import (
    COMPILER_REV,
    build_form_plan,
    compile_cases,
    render_coverage_snippet,
)

LOCATORS = {
    "name": "probe",
    "fields": {"店鋪名稱(CN）": {"kind": "input"}},
    "aliases": {"店铺名称(CN)": "店鋪名稱(CN）"},
}


def _rev_num() -> int:
    return int(re.search(r"rev(\d+)", COMPILER_REV).group(1))


def test_rev_at_least_198():
    assert _rev_num() >= 198


def test_empty_alias_map_is_emitted_explicitly():
    """没配别名时也要发一个空表：日志里能区分"没配"与"这版没带这个能力"。"""
    src = render_coverage_snippet()
    assert "__fieldAliases: Record<string, unknown> = {};" in src


def test_alias_payload_injected_verbatim():
    src = render_coverage_snippet(field_aliases=LOCATORS["aliases"])
    # 原文（含全/半角括号）要能读到，不能被 ensure_ascii 转成 \uXXXX
    assert "店铺名称(CN)" in src
    assert "店鋪名稱(CN）" in src
    assert "\\u5e97" not in src


def test_candidates_dedupe_self_alias():
    """别名指回文档名本身时不能重复匹配 —— 否则同一字段被数两次。"""
    src = render_coverage_snippet(field_aliases={"甲": "甲"})
    assert "x !== label" in src


def test_alias_used_is_reported_separately_from_missing():
    src = render_coverage_snippet(field_aliases=LOCATORS["aliases"])
    assert "field_labels_alias_used:" in src
    assert "field_labels_alias_used_count:" in src
    # 未命中照旧报，别名命中不能把真缺失掩盖掉
    assert "field_labels_missing: __labelMiss.slice(0, 8)" in src


def test_soft_check_never_fails_the_run():
    """软校验的定位：只记数。片段里不出现任何 throw/expect/exit。"""
    src = render_coverage_snippet(field_aliases=LOCATORS["aliases"])
    assert "throw " not in src
    assert "process.exit" not in src


def test_plan_keeps_alias_table_for_render():
    """别名要能从计划里取到 —— 渲染侧就是按这个 key 取，缺了等于新能力不生效。"""
    case = SimpleNamespace(
        identifier="TC-1",
        name="TC-1",
        custom_fields={
            "requirement_rule_id": "R-1",
            "requirement_rule": {"field": "店鋪名稱(CN）"},
            "assertions": [
                {"kind": "required", "field": "店鋪名稱(CN）", "rule": "是否必填=是"},
            ],
        },
    )
    plan = build_form_plan(compile_cases([case]), LOCATORS)
    assert plan["locators"]["aliases"] == {"店铺名称(CN)": "店鋪名稱(CN）"}


# --------------------------------------------------------------------------
# Node 实跑：把渲染出的片段原样取出执行，验证候选顺序与计数口径
# --------------------------------------------------------------------------

NODE = shutil.which("node")


def _strip_types(ts: str) -> str:
    """执行器是 TS，node 直接跑要剥掉本片段用到的几处类型注解（不手抄逻辑）。"""
    out = ts.replace(": Record<string, unknown>", "")
    out = out.replace(": Array<{label: string, matched: string}>", "")
    out = out.replace("(label: string): string[]", "(label)")
    out = out.replace(": string[]", "")
    out = out.replace(": string", "")
    return out


def _harness(src: str, labels: list[str], present: list[str]) -> str:
    start = src.index("const __labelRe = (label: string): RegExp => {")
    # 取到片段结尾（含 COMPILED_COVERAGE 那行输出）——只截到 console.log 之前，
    # 跑出来的就只是"算了但没打印"，看不到任何结论。
    body = _strip_types(src[start:])
    manifest = {
        "case_count": len(labels),
        "rule_case_count": len(labels),
        "narrative_case_count": 0,
        "dangling_rule_ids": [],
        "assertion_count": len(labels),
        "locator_depended_assertion_count": 0,
        "soft_assertion_count": len(labels),
        "field_labels": labels,
        "rule_ids": [],
    }
    return (
        f"const COMPILED_CASES = {json.dumps(manifest, ensure_ascii=False)};\n"
        f"const __present = {json.dumps(present, ensure_ascii=False)};\n"
        "const appScope = { getByText: (re) => ({ first: () => ({ count: async () => "
        "__present.some((p) => re.test(p)) ? 1 : 0 }) }) };\n"
        "(async () => {\n"
        f"{body}\n"
        "})();\n"
    )


def _run(src: str, labels: list[str], present: list[str]) -> dict:
    harness = _harness(src, labels, present)
    out = subprocess.run([NODE, "-e", harness], capture_output=True, text=True,
                         encoding="utf-8", errors="replace", env=dict(os.environ))
    assert out.returncode == 0, out.stderr[-600:]
    line = next(l for l in out.stdout.splitlines() if l.startswith("COMPILED_COVERAGE "))
    return json.loads(line[len("COMPILED_COVERAGE "):])


@pytest.mark.skipif(not NODE, reason="需要 node 才能验证运行期语义")
def test_runtime_alias_hit_counts_as_found_and_is_attributed():
    """页面只有别名后的文案 ⇒ found=1，且命中来源记在 alias_used 上。"""
    src = render_coverage_snippet(field_aliases={"店铺名称(CN)": "店鋪名稱(CN）"})
    got = _run(src, ["店铺名称(CN)"], ["店鋪名稱（CN）"])
    assert got["field_labels_found"] == 1
    assert got["field_labels_total"] == 1
    assert got["field_labels_missing"] == []
    assert got["field_labels_alias_used"] == [
        {"label": "店铺名称(CN)", "matched": "店鋪名稱(CN）"}
    ]
    assert got["field_labels_alias_used_count"] == 1


@pytest.mark.skipif(not NODE, reason="需要 node 才能验证运行期语义")
def test_runtime_exact_label_hit_is_not_reported_as_alias():
    """文档名本身就能命中时，不算"靠别名"——两个口径不能混。"""
    src = render_coverage_snippet(field_aliases={"店铺名称(CN)": "店鋪名稱(CN）"})
    got = _run(src, ["店铺名称(CN)"], ["店铺名称(CN)"])
    assert got["field_labels_found"] == 1
    assert got["field_labels_alias_used"] == []
    assert got["field_labels_alias_used_count"] == 0


@pytest.mark.skipif(not NODE, reason="需要 node 才能验证运行期语义")
def test_runtime_still_reports_true_miss():
    """页面两种写法都没有 ⇒ 仍然算未命中（别名不是免死金牌）。"""
    src = render_coverage_snippet(field_aliases={"店铺名称(CN)": "店鋪名稱(CN）"})
    got = _run(src, ["店铺名称(CN)"], ["毫不相干的字段"])
    assert got["field_labels_found"] == 0
    assert got["field_labels_missing"] == ["店铺名称(CN)"]
    assert got["field_labels_alias_used"] == []


@pytest.mark.skipif(not NODE, reason="需要 node 才能验证运行期语义")
def test_runtime_no_alias_config_behaves_like_before():
    """没配别名 ⇒ 候选只有文档名，行为与 rev197 一致。"""
    src = render_coverage_snippet()
    got = _run(src, ["甲字段"], ["甲字段"])
    assert got["field_labels_found"] == 1
    assert got["field_labels_alias_used_count"] == 0
