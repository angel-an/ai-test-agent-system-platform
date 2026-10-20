"""执行器环境检查、脱敏与状态判定测试。"""

from __future__ import annotations

from app.services.api_endpoint_script_executor import (
    _build_subprocess_env,
    _parse_playwright_json,
    check_env_requirements,
    redact_config,
)


def test_check_env_not_required_by_default():
    assert check_env_requirements({}) == []
    assert check_env_requirements({"base_url": ""}) == []


def test_check_env_missing_base_url():
    missing = check_env_requirements({"requires_env": True})
    assert "base_url" in missing


def test_check_env_pass_with_base_url():
    assert check_env_requirements({"requires_env": True, "base_url": "https://x.com"}) == []


def test_check_env_missing_refs():
    cfg = {"requires_env": True, "base_url": "https://x.com", "secret_ref": ""}
    missing = check_env_requirements(cfg)
    assert "secret_ref" in missing


def test_redact_config_masks_secrets():
    cfg = {
        "base_url": "https://x.com",
        "appsecret": "super-secret-value",
        "nested": {"password": "p@ss", "ok": 1},
        "appid": "public-id",
    }
    out = redact_config(cfg)
    assert out["appsecret"] == "***"
    assert out["nested"]["password"] == "***"
    assert out["nested"]["ok"] == 1
    assert out["appid"] == "public-id"
    assert out["base_url"] == "https://x.com"


def _report(statuses: list[str]) -> dict:
    return {
        "suites": [{
            "title": "s",
            "specs": [{
                "title": f"t{i}",
                "tests": [{"results": [{"status": s, "stdout": []}]}],
            } for i, s in enumerate(statuses)],
        }]
    }


def test_parse_counts_skipped():
    total, passed, failed, skipped, rows = _parse_playwright_json(_report(["passed", "skipped", "skipped"]))
    assert (total, passed, failed, skipped) == (3, 1, 0, 2)
    assert rows[1]["status"] == "skipped"


def test_parse_all_skipped_not_failed():
    total, passed, failed, skipped, _ = _parse_playwright_json(_report(["skipped", "skipped"]))
    assert failed == 0 and skipped == 2 and passed == 0


def test_build_subprocess_env_maps_base_url_to_api_base_url():
    """执行配置里的 ``base_url`` 必须落到脚本读的 ``API_BASE_URL``。

    生成的 API 脚本读 ``process.env.API_BASE_URL``；不映射就回退到模板里的
    ``http://localhost:8000`` 占位 —— 于是断言打的是本机，报告里的
    "契约断言失败"看着像 SUT 有问题，实际是打错了机器（归因完全错向）。
    ``runner.py`` / ``script_execution_tools.py`` 早已补了这一手。
    """
    env = _build_subprocess_env({"base_url": "https://sut.example.com/"}, base_env={})
    assert env["API_BASE_URL"] == "https://sut.example.com", "尾部斜杠会造成双斜杠路径"


def test_build_subprocess_env_explicit_env_wins_over_base_url():
    env = _build_subprocess_env(
        {"base_url": "https://a.example.com",
         "env": {"API_BASE_URL": "https://b.example.com"}},
        base_env={},
    )
    assert env["API_BASE_URL"] == "https://b.example.com", "显式配置优先级更高"


def test_build_subprocess_env_without_base_url_keeps_inherited_value():
    env = _build_subprocess_env({}, base_env={"API_BASE_URL": "https://deploy.example.com"})
    assert env["API_BASE_URL"] == "https://deploy.example.com"


def test_build_subprocess_env_without_base_url_invents_nothing():
    assert "API_BASE_URL" not in _build_subprocess_env({}, base_env={})
