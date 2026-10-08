"""API 失败链路的环境阻断闸（镜像 web 侧语义）。

背景
----
web 侧在缺陷登记前有一道环境阻断闸（``execution/executors.py:663-683``）：
WAF / 网关 / IP 白名单拦截表现成"测试失败"，但它不是 SUT 现象 ——
既不重试也不建单，只如实标记。

API 侧原先**没有**这道闸：``api_test_executor._process_idp_defect`` 直接把失败
交给登记服务，于是 WAF 拦截会被判定成一类业务失败并开单。

闸门的**判定输入**是关键：``ENVIRONMENT_BLOCKED_PATTERNS`` 里实战价值最高的三个
签名（``ESA RuleID`` / ``acw_tc`` / ``X-Swift-Error``）只出现在**进程 stdout**，
不在 Playwright 报告的错误文本里。因此本闸必须拿到 runner 的 stdout ——
这就是 ``_run_playwright_test`` 正常路径要把 ``result.stdout`` 挂到报告 dict 上的原因。

本文件锁定四件事：
1. 闸门在**登记调用之前**生效，且阻塞时**不发生登记调用**；
2. 阻塞判定**同时读 error_message 与 runner stdout**（stdout 独有签名也要能命中）；
3. ``_run_playwright_test`` 正常路径确实把进程 stdout 带出来，且**不覆盖**
   报告自有的同名键；
4. 判定权威未迁移：闸门只决定"是否登记"，不决定缺陷类型/优先级。
"""

from __future__ import annotations

import inspect
import io
import json
import tokenize
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest

import app.services.api_test_executor as executor_mod
from app.services.api_test_executor import APITestExecutor
from app.services.defect_registration_service import DefectRegistrationService
from app.services.web_failure_policy import classify_environment_block


def _executor() -> APITestExecutor:
    return APITestExecutor(session=None)


def _fake_api_test() -> SimpleNamespace:
    """最小可用替身：只需 test_config / project / test_case_id 三个属性。"""
    return SimpleNamespace(
        test_config={"base_url": "https://uat.example.com"},
        project=None,
        test_case_id=uuid4(),
    )


def _install_registration_spy(monkeypatch) -> list[dict]:
    calls: list[dict] = []

    async def _spy(self, **kwargs):  # noqa: ANN001, ANN202
        calls.append(kwargs)
        return None

    monkeypatch.setattr(DefectRegistrationService, "register_from_api_failure", _spy)
    return calls


def _code_only(source: str) -> str:
    """剥离注释后的代码文本（按 token 的列位置截断，不拼接 token 以免引入空格）。"""
    cuts: dict[int, int] = {}
    for tok in tokenize.generate_tokens(io.StringIO(source).readline):
        if tok.type == tokenize.COMMENT:
            row, col = tok.start
            cuts.setdefault(row, col)

    out_lines: list[str] = []
    for idx, line in enumerate(source.splitlines(keepends=True), start=1):
        if idx in cuts:
            body = line.rstrip("\n").rstrip("\r")[: cuts[idx]]
            out_lines.append(body + ("\n" if line.endswith("\n") else ""))
        else:
            out_lines.append(line)
    return "".join(out_lines)


# WAF 实测签名（2026-09-24 起 UAT 实测形态）：只在进程 stdout 出现
WAF_STDOUT = (
    "<html><head><title>403 Forbidden</title></head>\n"
    "ESA RuleID: 1164616 ; http_custom\n"
    "set-cookie: acw_tc=0a4b1c2d3e; cdn_sec_tc=xyz\n"
)
ORDINARY_STDOUT = "GET /v1/purchaseLimit/list P5_CONTRACT {\"ok\":true}\n"
ORDINARY_ERROR = "Error: expect(received).toBe('0')\nReceived: '401'"


# --------------------------------------------------------------------------
# 1. 闸门行为：阻塞时不发生登记调用
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_waf_signature_only_in_runner_stdout_blocks_registration(monkeypatch):
    """核心用例：签名只在 stdout（error_message 干净）也必须拦住 —— 这正是 A 方案会漏的形态。"""
    calls = _install_registration_spy(monkeypatch)
    blocked, _ = classify_environment_block(ORDINARY_ERROR, WAF_STDOUT)
    assert blocked, "前置：该报文应被判定为环境阻断（否则本用例失去意义）"

    await _executor()._process_idp_defect(
        run_id=uuid4(),
        api_test=_fake_api_test(),
        test_name="GET /v1/purchaseLimit/list",
        results=[{"error": {"message": ORDINARY_ERROR}}],
        report_url=None,
        runner_stdout=WAF_STDOUT,
    )

    assert calls == [], "环境阻断时不得调用登记服务"


@pytest.mark.asyncio
async def test_waf_signature_in_error_message_blocks_registration(monkeypatch):
    calls = _install_registration_spy(monkeypatch)
    msg = "Access Denied：请求被拦截（IP 白名单外）"

    await _executor()._process_idp_defect(
        run_id=uuid4(),
        api_test=_fake_api_test(),
        test_name="GET /v1/x",
        results=[{"error": {"message": msg}}],
        runner_stdout=None,
    )

    assert calls == [], "环境阻断时不得调用登记服务"


@pytest.mark.asyncio
async def test_ordinary_failure_still_reaches_registration(monkeypatch):
    """对照：普通业务失败必须照常进入登记（闸门不得顺手拦掉正常路径）。"""
    calls = _install_registration_spy(monkeypatch)

    await _executor()._process_idp_defect(
        run_id=uuid4(),
        api_test=_fake_api_test(),
        test_name="GET /v1/purchaseLimit/list",
        results=[{"error": {"message": ORDINARY_ERROR}}],
        runner_stdout=ORDINARY_STDOUT,
    )

    assert len(calls) == 1, "普通失败应照常交由登记服务判定"
    assert calls[0]["error_message"] == ORDINARY_ERROR
    # 判定权威仍在登记层：本闸不改变 allow_create 的语义
    assert calls[0]["allow_create"] is False


@pytest.mark.asyncio
async def test_no_stdout_supplied_still_exercises_the_gate(monkeypatch):
    """降级路径：拿不到 stdout 时闸门仍在（宁可少拦，也不能没有）。"""
    calls = _install_registration_spy(monkeypatch)

    await _executor()._process_idp_defect(
        run_id=uuid4(),
        api_test=_fake_api_test(),
        test_name="GET /v1/x",
        results=[{"error": {"message": "503 Service Unavailable orig response 5xx X-Swift-Error: upstream"}}],
    )

    assert calls == [], "error_message 里的网关签名也须拦住"


# --------------------------------------------------------------------------
# 2. stdout 管道（B 方案的三跳之一、二跳）：正常路径必须带出进程 stdout
# --------------------------------------------------------------------------


class _FakeCompleted:
    def __init__(self, stdout: str = "", returncode: int = 0):
        self.stdout = stdout
        self.stderr = ""
        self.returncode = returncode


def _patch_subprocess(monkeypatch, report_stdout: str):
    """让两次 subprocess.run 都返回可控对象（第一次是 npx 探测）。"""
    def _fake_run(*args, **kwargs):  # noqa: ANN001, ANN202
        return _FakeCompleted(stdout=report_stdout)

    monkeypatch.setattr(executor_mod.subprocess, "run", _fake_run)


@pytest.mark.asyncio
async def test_runner_attaches_process_stdout_on_normal_path(tmp_path: Path, monkeypatch):
    """正常路径（results.json 存在）也必须带出 stdout —— 否则闸门拿不到 WAF 签名。"""
    (tmp_path / "results.json").write_text(
        json.dumps({"suites": [], "config": {}}), encoding="utf-8"
    )
    _patch_subprocess(monkeypatch, WAF_STDOUT)

    out = await _executor()._run_playwright_test(tmp_path, {})

    assert out["stdout"] == WAF_STDOUT, "进程 stdout 必须随报告一起返回"


@pytest.mark.asyncio
async def test_runner_does_not_override_report_owned_stdout_key(tmp_path: Path, monkeypatch):
    """setdefault 语义：报告自带 stdout 键时必须保留，不得被进程输出覆盖。"""
    (tmp_path / "results.json").write_text(
        json.dumps({"suites": [], "stdout": "report-owned"}), encoding="utf-8"
    )
    _patch_subprocess(monkeypatch, WAF_STDOUT)

    out = await _executor()._run_playwright_test(tmp_path, {})

    assert out["stdout"] == "report-owned"


# --------------------------------------------------------------------------
# 3. 静态守卫：闸门必须在登记调用之前，且阻塞分支真的 return
# --------------------------------------------------------------------------


def test_gate_precedes_registration_and_returns():
    src = _code_only(inspect.getsource(APITestExecutor._process_idp_defect))

    assert "classify_environment_block" in src, "API 链路缺少环境阻断闸"
    gate_at = src.index("classify_environment_block")
    register_at = src.index("register_from_api_failure")
    assert gate_at < register_at, "闸门必须在登记调用之前生效"

    between = src[gate_at:register_at]
    assert "if blocked" in between, "闸门结果必须真的被用于短路"
    assert "return" in between, "阻塞分支必须 return（只标记，不登记）"


def test_gate_reads_runner_stdout_not_only_error_message():
    """守住 B 方案的核心：单参调用（只看 error_message）会让最典型的 WAF 签名全部漏掉。"""
    src = _code_only(inspect.getsource(APITestExecutor._process_idp_defect))
    assert "classify_environment_block(error_message, runner_stdout)" in src
