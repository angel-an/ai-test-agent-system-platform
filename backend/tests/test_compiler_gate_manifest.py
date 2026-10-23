# -*- coding: utf-8 -*-
"""锁住「编译器门禁集」本身：发现规则不能失效、排除清单不能腐烂。

背景：本轮排查发现两条红线被埋了十几个版本（rev181-rev196 / rev165-rev196），
根因不是代码，而是**没有人有一条约定的命令去跑编译器相关测试**。这个门禁集就是那条命令，
所以它自己被破坏时必须立刻红。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from tests import compiler_gate_manifest as m

BACKEND = Path(__file__).resolve().parents[1]


def test_discovery_is_not_empty():
    files = m.discover()
    assert len(files) >= 25, f"发现集只剩 {len(files)} 个文件，DEPENDENCY_MARKERS 可能失效"


def test_discovery_covers_echo_family():
    """echo / 对账 那条链上的关键文件必须都在门禁集里。"""
    files = set(m.discover())
    required = {
        "tests/test_channel_rev148.py",
        "tests/test_channel_rev186_echo.py",
        "tests/test_channel_rev188_echo_modal.py",
        "tests/test_channel_rev194_echo_save_judge.py",
        "tests/test_channel_rev195_echo_prefill_selector.py",
        "tests/test_channel_rev196_echo_edit_entry.py",
        "tests/test_skip_weight_reconcile_rev193.py",
        "tests/test_compiler_rev_guard.py",
        "tests/test_plan_skip_diagnostics.py",
    }
    missing = required - files
    assert not missing, f"门禁集漏了关键文件：{sorted(missing)}"


def test_browser_local_files_are_excluded():
    """真实浏览器测试不进内存基线：失败无法区分代码与环境。"""
    files = set(m.discover())
    browser = {f for f in files if "_browser_local" in f or "_local_node" in f}
    assert not browser, f"集成层文件混进了门禁集：{sorted(browser)}"


def test_every_exclusion_has_a_reason():
    for rel, why in m.INTEGRATION_EXCLUDED.items():
        assert why and len(why.strip()) >= 6, f"{rel} 的排除理由过于敷衍：{why!r}"


def test_excluded_files_still_exist():
    """排除项指向不存在的文件 = 清单腐烂（改过名却没更新），必须发现。"""
    missing = [rel for rel in m.INTEGRATION_EXCLUDED if not (BACKEND / rel).exists()]
    assert not missing, f"排除清单里有已不存在的文件：{missing}"


def test_shell_policy_exclusion_mentions_the_real_blocker():
    why = m.INTEGRATION_EXCLUDED["tests/test_shell_policy.py"]
    assert "anthropic" in why, "排除理由必须写明真正的阻断原因（缺 anthropic 包），否则下个人会以为是别的"


def test_discovery_is_deterministic():
    """同一棵树两次发现结果必须一致（排序稳定，否则耗时表/日志无法比对）。"""
    assert m.discover() == m.discover()


def test_manifest_does_not_hardcode_counts():
    """维护约定：不在清单里写死用例数量 —— 数字由执行决定。"""
    src = (BACKEND / "tests" / "compiler_gate_manifest.py").read_text(encoding="utf-8")
    assert "passed" not in src or "passed 数" not in src
    # 更直接：不该出现「= 574」这类钉死数字
    for n in ("567", "574", "719", "2830"):
        assert f"= {n}" not in src, f"清单里写死了历史数字 {n}"


def test_speed_budget_is_sane():
    assert 60 <= m.SPEED_BUDGET_SECONDS <= 600


@pytest.mark.parametrize("rel", sorted(m.INTEGRATION_EXCLUDED))
def test_excluded_reason_is_not_generic(rel):
    """排除理由要能说出「依赖什么」，不能只写「不适用」。"""
    why = m.INTEGRATION_EXCLUDED[rel]
    assert any(k in why for k in ("浏览器", "Node", "node", "anthropic", "UAT")), why
