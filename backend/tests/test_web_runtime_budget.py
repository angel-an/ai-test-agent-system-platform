# -*- coding: utf-8 -*-
"""rev116（2026-10-19）：执行预算三层对齐 + 取帧失败重取。

为什么要有这个文件（来源是 2026-10-19 的真实 UAT 运行，不是推断）：

1. **三层超时互相矛盾**。脚本里写着 ``test.setTimeout(1800000)``（30 分钟），
   而 ``WebTestService`` 给 runner 的进程超时是写死的 600s：
   600s 一到进程被硬杀，Playwright 来不及把结论写进报告 ——
   实测 3 条 job 日志里断言都跑完了，状态却是 failed（按状态统计就是假失败）。
   修法：三层严格递减，脚本预算由 runner 经 ``WEB_TEST_BUDGET_MS`` 下发。

2. **取帧只在入口做一次**。``getCrmScope`` 若在 iframe 插入 DOM 之前调用，
   会以 iframes=0 选中 page，之后 20s 菜单探测 + 15s 列表轮询全打在错的 scope 上，
   日志看起来像"菜单不存在"。修法：失败后等帧数变化再取一次（``refreshCrmScope``），
   且**只有帧数真的变了才重试**，避免在同一个错 scope 上重复白等。

本文件守的边界：
- 预算必须**严格小于**进程超时（含下限保护、非法输入回退）；
- 未显式配置 timeout 时跟随引擎作业预算，而不是回到硬编码 600；
- 三个模板都不再有硬编码 1800000；
- 重取只发生在帧数变化时，且诊断行 ``SCOPE_REFRESH`` 不丢。
"""

from __future__ import annotations

import os
from types import SimpleNamespace

import pytest

from app.services.execution.runner import (
    MIN_JOB_BUDGET_S,
    MIN_RUNNER_TIMEOUT_S,
    SCRIPT_BUDGET_GRACE_S,
    TimeoutBudgetError,
    resolve_timeout_tiers,
    script_budget_ms,
)
from app.services.web_case_compiler import render_form_executor
from app.services.web_case_link_service import WebCaseLinkService
from app.services.web_test_service import resolve_runner_timeout


def _case():
    return SimpleNamespace(
        identifier="TC-BUDGET01", name="店铺-新建", module="YL-主數據管理-店鋪",
        feature="店鋪", scenario="", background="", description="",
        custom_fields={}, test_case_steps=[],
    )


def _render_link(kind: str) -> str:
    svc = WebCaseLinkService.__new__(WebCaseLinkService)
    if kind == "yl":
        return svc._generate_yl_masterdata_script(
            _case(), "https://uat.example", ["CRM", "Master Data Management"],
        )
    return svc._generate_cs_module_script(
        _case(), "https://uat.example", ["CRM", "Customer Service"],
        project_identifier="PR-9",
    )


# ---------------------------------------------------------------- 预算换算

class TestScriptBudget:
    @pytest.mark.parametrize(
        "timeout,expected",
        [
            (600, 570_000),      # 默认档：留 30s 收尾
            (1800, 1_770_000),   # 引擎默认作业预算
            (120, 90_000),
        ],
    )
    def test_budget_is_timeout_minus_grace(self, timeout, expected):
        assert script_budget_ms(timeout) == expected

    def test_budget_is_strictly_less_than_process_timeout(self):
        """核心不变式：否则进程先被杀，报告和结论一起丢。

        rev116-fix（复核 §1）：**全域**成立，不再只取 ≥61 的样本 ——
        旧实现里 30s/60s 会返回 60s 预算（等于甚至大于进程超时）。
        """
        for timeout in (2, 3, 30, 60, 89, 90, 91, 120, 600, 1800, 3600):
            assert script_budget_ms(timeout) < timeout * 1000, timeout

    def test_floor_applies_from_min_runner_up(self):
        """进程超时 ≥90s 时才谈得上 60s 下限（90 = 60 预算 + 30 收尾）。"""
        assert MIN_RUNNER_TIMEOUT_S == 90
        assert script_budget_ms(90) == 60_000
        assert script_budget_ms(91) == 61_000
        assert script_budget_ms(120) == 90_000

    def test_below_min_runner_never_exceeds_process_timeout(self):
        """装不下下限时，「严格小于」优先于「预算够大」——留 60s 反而是倒挂。"""
        assert script_budget_ms(30) == 29_000
        assert script_budget_ms(60) == 59_000
        assert script_budget_ms(89) == 88_000


    @pytest.mark.parametrize("bad", [None, "abc", 0, -5])
    def test_bad_input_falls_back_to_default_600(self, bad):
        assert script_budget_ms(bad) == (600 - SCRIPT_BUDGET_GRACE_S) * 1000

    def test_grace_override(self):
        assert script_budget_ms(600, 120) == 480_000


class TestRunnerTimeout:
    def test_explicit_config_wins(self):
        assert resolve_runner_timeout({"timeout": 900}) == 900

    def test_empty_config_follows_job_budget_not_hardcoded_600(self, monkeypatch):
        from app.services import web_test_service as mod

        monkeypatch.setattr(mod.settings, "job_timeout_seconds", 1800, raising=False)
        assert resolve_runner_timeout(None) == 1740
        assert resolve_runner_timeout({}) == 1740

    def test_explicit_over_job_budget_is_clamped(self, monkeypatch):
        """rev116-fix（复核 §1）：显式 7200s 不能反超 1800s 作业预算。

        旧实现原样返回 7200 ⇒ 引擎到点取消，而进程还以为自己有两小时。
        """
        from app.services import web_test_service as mod

        monkeypatch.setattr(mod.settings, "job_timeout_seconds", 1800, raising=False)
        assert resolve_runner_timeout({"timeout": 7200}) == 1740

    def test_small_job_budget_is_rejected(self, monkeypatch):
        """rev116-fix：预算小到三层装不下时**拒绝执行**，不再静默造出 90>60=60。

        断言报错要指名"作业预算"：否则一旦这道闸门被摘掉，后面那道"进程超时过小"也会兜住，
        测试照样绿 —— 错误信息就再也指不到真正该改的那个配置项了。
        """
        from app.services import web_test_service as mod

        monkeypatch.setattr(mod.settings, "job_timeout_seconds", 90, raising=False)
        with pytest.raises(TimeoutBudgetError, match=r"作业预算 90s 过小"):
            resolve_runner_timeout({})

    def test_explicit_too_small_is_rejected(self):
        """显式 60s 装不下「脚本 60s + 收尾 30s」，同样是配置错误（错误信息指向进程超时）。"""
        with pytest.raises(TimeoutBudgetError, match="进程超时"):
            resolve_runner_timeout({"timeout": 60})

    @pytest.mark.parametrize(
        "job,explicit",
        [
            (None, None),
            (1800, None),
            (1800, 900),
            (1800, 7200),   # 超上限 → 裁剪
            (600, None),
            (300, 120),
            (150, None),    # 恰好等于最小作业预算
        ],
    )
    def test_three_layers_are_strictly_decreasing(self, job, explicit):
        """引擎作业预算 > 进程超时 > 脚本预算，对所有合法组合都成立。"""
        from app.config.settings import settings

        job_budget = getattr(settings, "job_timeout_seconds", 1800) if job is None else job
        tiers = resolve_timeout_tiers(job_budget_s=job_budget, explicit_runner_s=explicit)
        assert tiers.job_s > tiers.runner_s > tiers.script_s >= 60
        assert tiers.script_ms == tiers.script_s * 1000

    @pytest.mark.parametrize("job", [MIN_JOB_BUDGET_S - 1, 60, 90, 120])
    def test_job_budget_below_minimum_is_rejected(self, job):
        with pytest.raises(TimeoutBudgetError, match=rf"作业预算 {job}s 过小"):
            resolve_timeout_tiers(job_budget_s=job)



class TestRunnerInjectsBudgetEnv:
    """真接一次 `PlaywrightRunner.run`，确认预算**确实进了子进程 env**。

    只测 ``script_budget_ms`` 是不够的：函数算得对、但没塞进 env，脚本照样回退到 30 分钟，
    现象与"注入成功"完全一样。这里把 ``run_with_job`` 换成记录器，直接看它收到的 env。
    """

    @pytest.mark.skipif(os.name != "nt", reason="Windows 走 run_with_job 路径")
    async def test_run_passes_budget_to_child_process(self, tmp_path, monkeypatch):
        from app.agents.tools.web import process_guard
        from app.services.execution import runner as runner_mod

        captured: dict = {}

        class _Done:
            returncode = 0
            stdout = b""
            stderr = b""

        def fake_run_with_job(cmd, cwd, env, timeout, capture, stdout_path=None, stderr_path=None):
            captured.setdefault("calls", []).append({
                "cmd": list(cmd), "env": dict(env), "timeout": timeout,
            })
            return _Done()

        monkeypatch.setattr(process_guard, "run_with_job", fake_run_with_job)
        monkeypatch.setattr(runner_mod, "_get_playwright_cmd", lambda _d: ["playwright.cmd"])

        script = tmp_path / "x.spec.ts"
        script.write_text("// noop\n", encoding="utf-8")
        runner = runner_mod.PlaywrightRunner(tmp_path)
        await runner.run(script_path=script, config={"reporter": "list"}, timeout=900)

        run_calls = [c for c in captured["calls"] if "--version" not in c["cmd"]]
        assert run_calls, "没有真正启动测试进程"
        env = run_calls[-1]["env"]
        assert env["WEB_TEST_BUDGET_MS"] == str(script_budget_ms(900))
        assert int(env["WEB_TEST_BUDGET_MS"]) < 900 * 1000

    def test_default_budget_matches_default_runner_timeout(self):
        """三层串起来算一次：默认 1800 → 1740 → 1710s。"""
        runner_timeout = resolve_runner_timeout({})
        assert script_budget_ms(runner_timeout) == 1_710_000


# ------------------------------------------------- 模板里不再有硬编码 1800000

class TestNoHardcodedBudget:
    @pytest.mark.parametrize("kind", ["yl", "cs"])
    def test_link_templates_use_injected_budget(self, kind):
        script = _render_link(kind)
        assert "test.setTimeout(1800000)" not in script
        assert "WEB_TEST_BUDGET_MS" in script
        # 未注入时（本地直跑脚本）必须有回退，否则 Playwright 用默认 30s 必挂
        assert "|| 1800000" in script

    def test_form_executor_uses_injected_budget(self):
        snippet = render_form_executor({"checks": [], "skipped": [], "locators": {}})
        assert "test.setTimeout(1800000)" not in snippet
        assert "WEB_TEST_BUDGET_MS" in snippet

    def test_no_settimeout_escapes_the_injected_budget(self):
        """任何 test.setTimeout 站点都必须受下发预算约束。

        其余模板（储值免单/储值充值/会员限购）原来硬编码 240s：脚本自身 240s <
        进程 1740s 时没事，但一旦有人把 execution_config.timeout 调到 120s，
        就回到"进程先被杀"的老问题。所以它们也走 Math.min(原值, 下发预算)。
        """
        for kind in ("yl", "cs"):
            script = _render_link(kind)
            sites = [l.strip() for l in script.splitlines() if "test.setTimeout(" in l]
            assert sites, "模板里应至少有一处 setTimeout"
            for line in sites:
                assert "WEB_TEST_BUDGET_MS" in line, f"存在不受预算约束的站点：{line}"

    def test_budget_is_logged_at_runtime(self):
        """预算不能只"设了"，还要能在运行日志里看到它到底是多少、从哪来。

        否则无法证明 runner 真的注入了 env（脚本回退到 30 分钟时现象与注入成功一模一样）。
        """
        for kind in ("yl", "cs"):
            script = _render_link(kind)
            sites = script.count("test.setTimeout(")
            assert script.count("TEST_BUDGET") >= sites, f"{kind}: setTimeout 站点未被预算日志覆盖"
        snippet = render_form_executor({"checks": [], "skipped": [], "locators": {}})
        assert "TEST_BUDGET" in snippet
        assert "'form_executor'" in snippet

    def test_short_templates_cap_with_min(self):
        """短模板保留自己的上限（4 分钟），不因下发预算变大而无谓延长。"""
        svc = WebCaseLinkService.__new__(WebCaseLinkService)
        for render in (
            svc._generate_storage_writeoff_script,
            svc._generate_enterprise_storage_recharge_script,
            svc._generate_purchase_limit_script,
        ):
            script = render(_case(), "https://uat.example", ["CRM"])
            assert "Math.min(240000, Number(process.env.WEB_TEST_BUDGET_MS" in script, render.__name__
            assert "test.setTimeout(240000);" not in script, render.__name__


# ------------------------------------------------------ 取帧失败重取接线

class TestScopeRefreshWiring:
    @pytest.mark.parametrize("kind,label", [("yl", "yl-menu-expand"), ("cs", "cs-menu-expand")])
    def test_failure_branch_refreshes_before_page_fallback(self, kind, label):
        script = _render_link(kind)
        first_probe = script.index("let expanded = await expandSubmenu(scope")
        refresh_at = script.index(f"refreshCrmScope(appPage, scope, '{label}')")
        page_fallback = script.index("expanded = await expandSubmenu(scope, parentLabels, childLabels, 20000);", refresh_at)
        assert first_probe < refresh_at < page_fallback

    @pytest.mark.parametrize("kind", ["yl", "cs"])
    def test_refresh_gates_on_scope_staleness_not_frame_count(self, kind):
        """rev116-fix：重试门禁必须是「scope 类型是否过期」。

        旧门禁是 `refreshed.frames !== scopeFrames`，而 scopeFrames 由调用方**再读一次** DOM 得到；
        两次读之间 iframe 插入 ⇒ scope=page 但 scopeFrames=1 ⇒ 判"没变化"直接放弃（实测失效）。
        """
        script = _render_link(kind)
        at = script.index("const refreshed = await refreshCrmScope(")
        window = script[at:at + 400]
        assert "if (refreshed.stale) {" in window
        assert "scopeFrames" not in script, "入口不得再单独读一次帧数（就该由 scope 身份反推）"

    def test_entry_does_not_read_frame_count_twice(self):
        """入口那条"二次读 DOM"的行必须消失 —— 它就是竞态的成因。"""
        for kind in ("yl", "cs"):
            script = _render_link(kind)
            entry = script[script.index("let scope = await getCrmScope(appPage);"):]
            entry_head = entry[:entry.index("let expanded = await expandSubmenu(scope")]
            assert "frameCount(appPage)" not in entry_head
            assert "refreshCrmScope(appPage, scope," in script

    def test_helpers_defined_once_per_script(self):
        for kind in ("yl", "cs"):
            script = _render_link(kind)
            assert script.count("async function frameCount(") == 1
            assert script.count("async function refreshCrmScope(") == 1

    def test_refresh_emits_machine_readable_line(self):
        script = _render_link("yl")
        assert "SCOPE_REFRESH ${JSON.stringify(" in script
        # adopt 必须与 stale 同源，避免"日志说切了、实际没切"
        assert "adopt: stale" in script

