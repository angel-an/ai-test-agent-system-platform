import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from app.services.api_runtime_data_service import (
    resolve_api_runtime_config,
    sanitize_api_execution_config,
)


def test_data_profile_is_resolved_only_for_runtime():
    raw_config = {"data_profile": "XYSJG_UAT_READONLY", "timeout": 30}
    runtime = resolve_api_runtime_config(
        raw_config,
        {"base_url": "https://fallback.example"},
        environ={
            "API_TEST_XYSJG_UAT_READONLY_BASE_URL": "https://uat.example",
            "API_TEST_XYSJG_UAT_READONLY_AUTHORIZATION": "Bearer runtime-only",
            "API_TEST_XYSJG_UAT_READONLY_AUTH_HEADER": "access-token",
            "API_TEST_XYSJG_UAT_READONLY_DATA_JSON": '{"memberPhone":"18000000000"}',
        },
    )

    assert runtime["env"]["API_BASE_URL"] == "https://uat.example"
    assert runtime["env"]["API_TEST_AUTHORIZATION"] == "Bearer runtime-only"
    assert runtime["env"]["API_TEST_AUTH_HEADER"] == "access-token"
    assert runtime["env"]["API_TEST_DATA_JSON"] == '{"memberPhone":"18000000000"}'
    assert raw_config == {"data_profile": "XYSJG_UAT_READONLY", "timeout": 30}


def test_persisted_config_removes_direct_secret_values():
    persisted = sanitize_api_execution_config({
        "data_profile": "XYSJG_UAT_READONLY",
        "env": {
            "SAFE_FLAG": "1",
            "API_TEST_AUTHORIZATION": "Bearer should-not-persist",
            "MEMBER_TOKEN": "should-not-persist",
            "ALLOW_MUTATING_API_TESTS": "1",
        },
        "authorization": "Bearer should-not-persist",
    })

    assert persisted == {
        "data_profile": "XYSJG_UAT_READONLY",
        "env": {"SAFE_FLAG": "1"},
    }


def test_profile_without_injected_values_keeps_non_secret_fallback_only():
    runtime = resolve_api_runtime_config(
        {"data_profile": "DEFAULT"},
        {"base_url": "https://fallback.example"},
        environ={},
    )

    assert runtime["base_url"] == "https://fallback.example"
    assert "env" not in runtime


def test_mutating_api_requires_explicit_write_profile_and_flag():
    blocked = resolve_api_runtime_config(
        {"data_profile": "XYSJG_UAT_READONLY", "allow_mutating_api_tests": True},
        {},
        environ={},
    )
    allowed = resolve_api_runtime_config(
        {"data_profile": "XYSJG_UAT_WRITE", "allow_mutating_api_tests": True},
        {},
        environ={},
    )

    assert "env" not in blocked
    assert allowed["env"]["ALLOW_MUTATING_API_TESTS"] == "1"
