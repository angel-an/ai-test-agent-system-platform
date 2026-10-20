# -*- coding: utf-8 -*-
"""编译器版本规范的**强制执行**测试（rev118）。

这些测试的目的不是"验证功能正确"，而是把三条口头规范变成「违反就红」：

1. 改执行器模板 / 编译逻辑 → 必须 bump ``COMPILER_REV``；
2. 看门狗预算只能通过 ``WEB_SUBMIT_ITEM_MS`` 调，不能改代码里的默认值；
3. 严格模式默认 ``auto``：历史无指纹产物放行，**打过指纹却版本倒退必须拒绝**。

规范文档见 ``docs/编译产物版本规范.md``。若这里的断言失败，不要改断言 —— 先看文档。
"""

from __future__ import annotations

import importlib.util
import json
import re
from pathlib import Path

import pytest

from app.services import web_case_compiler as wcc
from app.services.execution import runner as runner_mod

BACKEND_DIR = Path(__file__).resolve().parents[1]
REGISTRY_PATH = BACKEND_DIR / "tests" / "golden" / "compiler_rev_registry.json"
GUARD_PATH = BACKEND_DIR / "scripts" / "check_compiler_rev.py"


@pytest.fixture(autouse=True)
def _clear_digest_cache():
    """digest 带 lru_cache；monkeypatch 后必须清，否则测试之间互相污染。"""
    wcc.compiler_source_digest.cache_clear()
    yield
    wcc.compiler_source_digest.cache_clear()


def _load_guard():
    spec = importlib.util.spec_from_file_location("check_compiler_rev", GUARD_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def _registry() -> dict:
    return json.loads(REGISTRY_PATH.read_text(encoding="utf-8"))


# ---------------------------------------------------------------------------
# 1) 版本登记表：忘 bump 必红
# ---------------------------------------------------------------------------

def test_current_tree_passes_the_same_guard_used_by_precommit():
    """直接跑 pre-commit/CI 用的那个脚本逻辑，而不是另写一套判据。"""
    guard = _load_guard()
    ok, message = guard.check(_registry(), wcc.compiler_source_digest())
    assert ok, message


def test_guard_fails_when_template_changed_without_bumping_rev():
    """模板/逻辑变了但 rev 没动：digest 对不上登记值 → 必须失败。"""
    guard = _load_guard()
    data = {"entries": [{"rev": wcc.COMPILER_REV, "digest": "0" * 12}]}
    ok, message = guard.check(data, wcc.compiler_source_digest())
    assert not ok
    assert "bump" in message and wcc.COMPILER_REV in message


def test_guard_fails_when_new_rev_was_not_registered():
    """bump 了 rev 却没追加登记 → 也必须失败（否则登记表可以被绕开）。"""
    guard = _load_guard()
    data = {"entries": [{"rev": "rev1", "digest": wcc.compiler_source_digest()}]}
    ok, message = guard.check(data, wcc.compiler_source_digest())
    assert not ok
    assert "不一致" in message


def test_guard_rejects_duplicate_digest_and_non_increasing_rev():
    guard = _load_guard()
    dup = {"entries": [
        {"rev": "rev118", "digest": "a" * 12},
        {"rev": "rev119", "digest": "a" * 12},
    ]}
    assert not guard.check(dup, "a" * 12)[0]

    backwards = {"entries": [
        {"rev": "rev119", "digest": "b" * 12},
        {"rev": "rev118", "digest": "b" * 12},
    ]}
    assert not guard.check(backwards, "b" * 12)[0]


def test_guard_update_refuses_to_reuse_the_same_rev(tmp_path, monkeypatch):
    """``--update`` 不能当作"让检查通过"的后门：同 rev 拒绝写入。"""
    guard = _load_guard()
    fake = tmp_path / "registry.json"
    fake.write_text(json.dumps({"entries": [
        {"rev": wcc.COMPILER_REV, "digest": "0" * 12, "date": "2026-01-01", "summary": ""},
    ]}), encoding="utf-8")
    monkeypatch.setattr(guard, "REGISTRY", fake)
    assert guard.main(["--update"]) == 1


# ---------------------------------------------------------------------------
# 2) digest 的覆盖面：证明它真的盯着"会改变产物行为"的东西
# ---------------------------------------------------------------------------

def test_digest_changes_when_executor_template_changes(monkeypatch):
    before = wcc.compiler_source_digest()
    monkeypatch.setattr(wcc, "_FORM_EXECUTOR_TS", wcc._FORM_EXECUTOR_TS + "\n// tweak\n")
    wcc.compiler_source_digest.cache_clear()
    assert wcc.compiler_source_digest() != before


def test_digest_changes_when_compile_logic_changes(monkeypatch):
    before = wcc.compiler_source_digest()

    def _stub_build_form_plan(*args, **kwargs):  # pragma: no cover - 仅作为变更源
        return {}

    monkeypatch.setattr(wcc, "build_form_plan", _stub_build_form_plan)
    wcc.compiler_source_digest.cache_clear()
    assert wcc.compiler_source_digest() != before


def test_digest_does_not_depend_on_rev_string(monkeypatch):
    """rev 不能参与 digest：否则每次 bump 都会改变 digest，形成自指。"""
    before = wcc.compiler_source_digest()
    monkeypatch.setattr(wcc, "COMPILER_REV", "rev999")
    wcc.compiler_source_digest.cache_clear()
    assert wcc.compiler_source_digest() == before


def test_digest_is_stable_and_short():
    first = wcc.compiler_source_digest()
    wcc.compiler_source_digest.cache_clear()
    assert wcc.compiler_source_digest() == first
    assert re.fullmatch(r"[0-9a-f]{12}", first)


# ---------------------------------------------------------------------------
# 3) 看门狗预算：只能走环境变量，不许改代码调大
# ---------------------------------------------------------------------------

def test_watchdog_budget_is_env_overridable_with_bounded_default():
    match = re.search(r"const SUBMIT_ITEM_MS = ([^;]+);", wcc._FORM_EXECUTOR_TS)
    assert match, "执行器模板里找不到 SUBMIT_ITEM_MS 定义"
    expr = match.group(1)
    assert "process.env.WEB_SUBMIT_ITEM_MS" in expr, (
        "看门狗预算必须支持 WEB_SUBMIT_ITEM_MS 下发 —— 否则线上误判只能改代码发版"
    )
    defaults = re.findall(r"\|\|\s*(\d+)", expr)
    assert defaults, f"解析不到默认值: {expr}"
    assert int(defaults[-1]) <= 180_000, (
        f"默认看门狗预算 {defaults[-1]}ms 过大：单项超时保护形同虚设"
    )


def test_watchdog_race_wraps_the_whole_item_body():
    """执行体内不能有 ``continue``：它会跳出竞速范围，让看门狗管不到后续语句。

    循环外层的 ``if (submitAborted) { ... continue; }`` 是允许的 —— 那是整批已中止时的
    纯记录分支，没有任何 IO，不参与竞速。所以只检查 ``runSubmitItem`` 的函数体。
    """
    template = wcc._FORM_EXECUTOR_TS
    assert "Promise.race([runSubmitItem()" in template
    start = template.index("const runSubmitItem = async")
    end = template.index("let wdTimer", start)
    body = template[start:end]
    assert body.strip(), "执行体为空，锚点可能失效"
    assert "continue;" not in body, "执行体内仍有 continue：该路径不受看门狗保护"


# ---------------------------------------------------------------------------
# 4) 严格模式：默认 auto 的不对称语义
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("raw,expected", [
    (None, "auto"), ("", "auto"), ("auto", "auto"), ("whatever", "auto"),
    ("1", "on"), ("true", "on"), ("ON", "on"),
    ("0", "off"), ("false", "off"), ("Off", "off"),
])
def test_resolve_strict_mode(raw, expected):
    assert runner_mod.resolve_strict_mode(raw) == expected


@pytest.mark.parametrize("reason,mode,blocked", [
    ("legacy_no_fingerprint", "auto", False),   # 历史存量：放行但标记
    ("rev_mismatch", "auto", True),             # 打过指纹却倒退：拒绝
    ("digest_mismatch", "auto", True),          # 逻辑改了没 bump：拒绝
    ("legacy_no_fingerprint", "on", True),
    ("digest_mismatch", "off", False),
])
def test_should_block_stale(reason, mode, blocked):
    assert runner_mod.should_block_stale(reason, mode) is blocked


def test_stale_payload_flags_digest_mismatch_only_when_both_known():
    same_rev_diff_digest = runner_mod.artifact_stale_payload(
        "rev118", "rev118", "a.spec.ts",
        artifact_digest="aaaaaaaaaaaa", current_digest="bbbbbbbbbbbb",
    )
    assert same_rev_diff_digest and same_rev_diff_digest["reason"] == "digest_mismatch"

    # rev117 老产物没有 digest 行：不能因此误伤
    legacy_no_digest = runner_mod.artifact_stale_payload(
        "rev118", "rev118", "a.spec.ts",
        artifact_digest=None, current_digest="bbbbbbbbbbbb",
    )
    assert legacy_no_digest is None

    assert runner_mod.artifact_stale_payload(
        "rev118", "rev118", "a.spec.ts",
        artifact_digest="aaaaaaaaaaaa", current_digest="aaaaaaaaaaaa",
    ) is None
