"""平台 api-tests 执行链优化（2026-10-27）门禁测试。

覆盖四条改动，都是为了"结果可信"：
1. reporter 必须产出 JSON（机读）；allure 是第三方包，缺失时不能拦住整次执行 ——
   只配 allure 曾导致 ``results.json`` 永不生成，``suites=[]`` → total_tests 恒 0
   （实测 101 个 run 全零），更早一步是 reporter 解析阶段直接崩、一个测试都不跑。
2. 状态按 Playwright JSON 的 ``status``（expected/unexpected/flaky/skipped）分派，
   不再用布尔 ``ok`` 二分（skipped 曾被算成失败或通过）。
3. 用例名取 ``spec.title``：``tests[].title`` 在 JSON 报告里恒为 null（实测）。
4. 生成的脚本必须消费必填 query 参数（此前 ``in=query`` 全丢，带参接口打残缺 URL）。
"""
from __future__ import annotations

import json
import subprocess
from pathlib import Path
from types import SimpleNamespace

from app.models.api_test import TestResultStatus as Status
from app.services.api_case_link_service import APICaseLinkService
from app.services.api_test_executor import APITestExecutor


def _executor() -> APITestExecutor:
    return APITestExecutor.__new__(APITestExecutor)


def _patch_npx(monkeypatch) -> None:  # noqa: ANN001
    monkeypatch.setattr("shutil.which", lambda name: "npx.cmd")


# ---------------------------------------------------------------- 1) reporter

def test_config_declares_json_reporter_and_allure_switch():
    """json 写进配置（命令行的数组形式在 Windows 会被拆错，实测）。"""
    config = _executor()._generate_playwright_config({"base_url": "http://x"})
    assert "outputFile: 'results.json'" in config
    assert "PW_ALLURE_REPORTER" in config
    assert "allure-playwright" in config


async def test_reporter_produces_json_and_parses_it(tmp_path: Path, monkeypatch):
    captured: dict[str, object] = {}

    def fake_run(cmd, **kwargs):  # noqa: ANN001
        if "--version" in cmd:
            return SimpleNamespace(returncode=0, stdout="1.0", stderr="")
        captured["cmd"] = list(cmd)
        captured["env"] = kwargs.get("env") or {}
        (tmp_path / "results.json").write_text(
            json.dumps({"suites": [{"specs": [{"title": "GET /x", "tests": [
                {"title": None, "status": "expected", "results": []}
            ]}]}]}),
            encoding="utf-8",
        )
        return SimpleNamespace(returncode=0, stdout="WAF acw_tc=1", stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)
    _patch_npx(monkeypatch)

    report = await _executor()._run_playwright_test(
        tmp_path, {"base_url": "http://x", "timeout": 60}
    )
    assert report["suites"], "must parse results.json instead of falling back"
    assert report["stdout"] == "WAF acw_tc=1", "环境证据（WAF 签名）必须随报告返回"
    assert captured["env"]["PW_ALLURE_REPORTER"] == "1"
    # 不再从命令行传数组形式 reporter
    assert not any(str(a).startswith("--reporter=") for a in captured["cmd"])


async def test_falls_back_when_allure_reporter_missing(tmp_path: Path, monkeypatch):
    """allure-playwright 不可用时，必须关掉它、仅用 json 重跑（实测报错措辞会变）。"""
    calls: list[dict] = []

    def fake_run(cmd, **kwargs):  # noqa: ANN001
        if "--version" in cmd:
            return SimpleNamespace(returncode=0, stdout="1.0", stderr="")
        calls.append({"cmd": list(cmd), "env": kwargs.get("env") or {}})
        if calls[-1]["env"].get("PW_ALLURE_REPORTER") == "1":
            return SimpleNamespace(
                returncode=1,
                stdout="",
                stderr='Error: Package subpath \'./allure-playwright\' is not defined by "exports"',
            )
        (tmp_path / "results.json").write_text(
            json.dumps({"suites": [{"specs": [{"title": "t", "tests": [
                {"title": None, "status": "expected", "results": []}
            ]}]}]}),
            encoding="utf-8",
        )
        return SimpleNamespace(returncode=0, stdout="ok", stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)
    _patch_npx(monkeypatch)

    report = await _executor()._run_playwright_test(
        tmp_path, {"base_url": "http://x", "timeout": 60}
    )
    assert len(calls) == 2, "must retry exactly once with allure disabled"
    assert calls[0]["env"]["PW_ALLURE_REPORTER"] == "1"
    assert calls[1]["env"]["PW_ALLURE_REPORTER"] == "0"
    assert report["suites"]


async def test_no_retry_on_genuine_failure(tmp_path: Path, monkeypatch):
    """用例真红 / 超时等失败不重跑（重跑会掩盖现场、白耗时间）。"""
    calls: list[list[str]] = []

    def fake_run(cmd, **kwargs):  # noqa: ANN001
        if "--version" in cmd:
            return SimpleNamespace(returncode=0, stdout="1.0", stderr="")
        calls.append(list(cmd))
        return SimpleNamespace(returncode=1, stdout="1 failed", stderr="expect(status).toBe(200)")

    monkeypatch.setattr(subprocess, "run", fake_run)
    _patch_npx(monkeypatch)

    await _executor()._run_playwright_test(tmp_path, {"base_url": "http://x", "timeout": 60})
    assert len(calls) == 1


# ---------------------------------------------------------------- 2) status

def test_status_mapping_matches_playwright_json_values():
    assert APITestExecutor._map_test_status({"status": "expected"}) == Status.PASSED
    assert APITestExecutor._map_test_status({"status": "unexpected"}) == Status.FAILED
    assert APITestExecutor._map_test_status({"status": "skipped"}) == Status.SKIPPED
    # 重试后通过：算通过（flaky 语义保留在报告里）
    assert APITestExecutor._map_test_status({"status": "flaky"}) == Status.PASSED
    # skipped 在 JSON 里 ok=true，务必**先于** ok 兜底判断
    assert APITestExecutor._map_test_status({"status": "skipped", "ok": True}) == Status.SKIPPED


def test_status_falls_back_to_ok_when_missing():
    """旧格式报告没有 status 时沿用布尔 ok，避免升级即全红。"""
    assert APITestExecutor._map_test_status({"ok": True}) == Status.PASSED
    assert APITestExecutor._map_test_status({"ok": False}) == Status.FAILED


# ---------------------------------------------------------------- 3) 用例名

def test_test_name_prefers_spec_title():
    """Playwright JSON 里 tests[].title 恒为 null，标题在 spec 上。"""
    import app.services.api_test_executor as mod

    source = Path(mod.__file__).read_text(encoding="utf-8")
    assert 'spec.get("title") or test.get("title")' in source


# ---------------------------------------------------------------- 4) query

def _svc() -> APICaseLinkService:
    return APICaseLinkService.__new__(APICaseLinkService)


def test_sample_query_params_only_required():
    params = [
        {"name": "page", "in": "query", "required": True, "type": "integer"},
        {"name": "optionalFlag", "in": "query", "required": False, "type": "string"},
        {"name": "X-Trace", "in": "header", "required": True},
    ]
    assert _svc()._sample_query_params(params) == {"page": "1"}


def test_sample_query_params_value_priority():
    assert _svc()._sample_param_value({"default": "abc"}) == "abc"
    assert _svc()._sample_param_value({"enum": ["ON", "OFF"], "type": "string"}) == "ON"
    assert _svc()._sample_param_value({"type": "integer"}) == "1"
    assert _svc()._sample_param_value({"type": "boolean"}) == "false"
    assert _svc()._sample_param_value({"type": "string"}) == "test"
    # OpenAPI 3 里 type 可能嵌在 schema 下
    assert _svc()._sample_param_value({"schema": {"type": "integer"}}) == "1"


def test_generated_script_consumes_query_params():
    endpoint = SimpleNamespace(
        id="ep-1",
        method="GET",
        path="/api/v1/items/{id}",
        request_body=None,
        parameters=[
            {"name": "pageNo", "in": "query", "required": True, "type": "integer"},
            {"name": "keyword", "in": "query", "required": True, "type": "string"},
        ],
        custom_config={},
        responses=None,
    )
    script = _svc()._generate_playwright_script(endpoint)
    assert '"params"' in script
    assert '"pageNo": "1"' in script and '"keyword": "test"' in script
    # 样本值边界必须写在脚本里，读报告的人不用猜
    assert "样本值说明" in script


# ------------------------------------------------- 5) 计数循环（打桩，不触网）

async def test_process_results_counts_and_names(monkeypatch):
    """suites → 计数 / per-test 落库 / 仅失败进缺陷闸，三件事都要对。"""
    saved: list[dict] = []
    defects: list[str] = []
    updates: dict = {}

    class FakeRepo:
        async def get_by_id(self, _id):  # noqa: ANN001
            return object()

        async def update(self, _obj, **kwargs):  # noqa: ANN001
            updates.update(kwargs)

    ex = APITestExecutor.__new__(APITestExecutor)
    ex.api_test_run_repo = FakeRepo()

    async def fake_save(**kwargs):  # noqa: ANN003
        saved.append(kwargs)

    async def fake_defect(**kwargs):  # noqa: ANN003
        defects.append(kwargs["test_name"])

    monkeypatch.setattr(ex, "_save_test_result", fake_save)
    monkeypatch.setattr(ex, "_process_idp_defect", fake_defect)

    report = {"suites": [{"specs": [
        {"title": "passes", "tests": [{"title": None, "status": "expected", "results": []}]},
        {"title": "fails", "tests": [{"title": None, "status": "unexpected", "results": []}]},
        {"title": "skips", "tests": [{"title": None, "status": "skipped", "results": []}]},
    ]}]}

    await ex._process_test_results(
        run_id="00000000-0000-0000-0000-000000000000",
        api_test=SimpleNamespace(),
        test_result=report,
    )

    assert updates["total_tests"] == 3
    assert updates["passed_tests"] == 1
    assert updates["failed_tests"] == 1
    assert updates["skipped_tests"] == 1
    assert [row["test_name"] for row in saved] == ["passes", "fails", "skips"]
    assert defects == ["fails"], "只有失败用例进缺陷闸"


# ------------------------------------------------- 6) base_url / 自定义 env 注入

async def test_base_url_and_custom_env_injected(tmp_path: Path, monkeypatch):
    """脚本读 process.env.API_BASE_URL —— 不注入就永远打本机 localhost 占位。"""
    captured: dict = {}

    def fake_run(cmd, **kwargs):  # noqa: ANN001
        if "--version" in cmd:
            return SimpleNamespace(returncode=0, stdout="1.0", stderr="")
        captured["env"] = kwargs.get("env") or {}
        return SimpleNamespace(returncode=1, stdout="1 failed", stderr="boom")

    monkeypatch.setattr(subprocess, "run", fake_run)
    _patch_npx(monkeypatch)

    await _executor()._run_playwright_test(
        tmp_path,
        {"base_url": "https://sut.example.test/", "env": {"API_TEST_DATA_JSON": "{}"}},
    )
    assert captured["env"]["API_BASE_URL"] == "https://sut.example.test"
    assert captured["env"]["API_TEST_DATA_JSON"] == "{}"


async def test_save_failure_does_not_block_counts(monkeypatch):
    """单条结果落库报错不得拖垮整段统计（历史坑：一条异常 → total 恒 0）。"""
    updates: dict = {}

    class FakeRepo:
        async def get_by_id(self, _id):  # noqa: ANN001
            return object()

        async def update(self, _obj, **kwargs):  # noqa: ANN001
            updates.update(kwargs)

    ex = APITestExecutor.__new__(APITestExecutor)
    ex.api_test_run_repo = FakeRepo()

    async def broken_save(**kwargs):  # noqa: ANN003
        raise TypeError("boom")

    async def noop_defect(**kwargs):  # noqa: ANN003
        return None

    monkeypatch.setattr(ex, "_save_test_result", broken_save)
    monkeypatch.setattr(ex, "_process_idp_defect", noop_defect)

    report = {"suites": [{"specs": [
        {"title": "passes", "tests": [{"title": None, "status": "expected", "results": []}]},
        {"title": "fails", "tests": [{"title": None, "status": "unexpected", "results": []}]},
    ]}]}
    await ex._process_test_results(
        run_id="00000000-0000-0000-0000-000000000000",
        api_test=SimpleNamespace(),
        test_result=report,
    )
    assert updates["total_tests"] == 2
    assert updates["passed_tests"] == 1
    assert updates["failed_tests"] == 1
