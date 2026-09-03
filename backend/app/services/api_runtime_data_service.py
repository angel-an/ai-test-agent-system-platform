"""API 测试运行时数据配置。

测试资产和运行记录只保存数据配置名称。连接地址、授权信息和测试数据
在实际启动 Playwright 子进程前从环境变量注入，避免写入数据库或报告。
"""

from __future__ import annotations

import os
import re
from collections.abc import Mapping
from copy import deepcopy
from typing import Any


_SENSITIVE_ENV_NAMES = {
    "API_TEST_AUTHORIZATION",
    "API_TEST_DATA_JSON",
    "API_BASE_URL",
    "ALLOW_MUTATING_API_TESTS",
}
_SENSITIVE_KEY_MARKERS = ("authorization", "password", "secret", "token")


def sanitize_api_execution_config(config: Mapping[str, Any] | None) -> dict[str, Any]:
    """Return a persistable API execution config without direct secret values."""
    source = deepcopy(dict(config or {}))
    for key in list(source):
        if key.lower() in _SENSITIVE_KEY_MARKERS or key in _SENSITIVE_ENV_NAMES:
            source.pop(key, None)

    for env_key in ("env", "environment_variables"):
        values = source.get(env_key)
        if not isinstance(values, Mapping):
            continue
        source[env_key] = {
            str(key): value
            for key, value in values.items()
            if not _is_sensitive_env_key(str(key))
        }
    return source


def resolve_api_runtime_config(
    execution_config: Mapping[str, Any] | None,
    test_config: Mapping[str, Any] | None,
    environ: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    """Build a local config and inject values selected by ``data_profile``.

    For example, profile ``XYSJG_UAT_READONLY`` resolves
    ``API_TEST_XYSJG_UAT_READONLY_BASE_URL``, ``..._AUTHORIZATION`` and
    ``..._DATA_JSON`` only for the child Playwright process.
    """
    persisted_config = sanitize_api_execution_config(execution_config)
    asset_config = dict(test_config or {})
    profile = persisted_config.get("data_profile") or asset_config.get("data_profile")
    runtime_config = deepcopy(persisted_config)
    runtime_env = {
        str(key): str(value)
        for key, value in (runtime_config.get("env") or {}).items()
    }
    source_env = environ if environ is not None else os.environ

    if profile:
        normalized_profile = re.sub(r"[^A-Za-z0-9]", "_", str(profile)).upper()
        prefix = f"API_TEST_{normalized_profile}_"
        for suffix, target in {
            "BASE_URL": "API_BASE_URL",
            "AUTHORIZATION": "API_TEST_AUTHORIZATION",
            "AUTH_HEADER": "API_TEST_AUTH_HEADER",
            "DATA_JSON": "API_TEST_DATA_JSON",
        }.items():
            value = source_env.get(f"{prefix}{suffix}")
            if value:
                runtime_env[target] = value
        runtime_config["data_profile"] = str(profile)

    # 写接口必须同时满足显式开关和写入专用 profile。profile 本身只是一
    # 个名称，实际认证信息仍仅由部署环境在运行时注入。
    if (
        runtime_config.get("allow_mutating_api_tests") is True
        and str(profile or "").upper().endswith("_WRITE")
    ):
        runtime_env["ALLOW_MUTATING_API_TESTS"] = "1"

    if not runtime_config.get("base_url"):
        runtime_config["base_url"] = asset_config.get("base_url")
    if runtime_env:
        runtime_config["env"] = runtime_env
    return runtime_config


def _is_sensitive_env_key(key: str) -> bool:
    lowered = key.lower()
    return key in _SENSITIVE_ENV_NAMES or any(marker in lowered for marker in _SENSITIVE_KEY_MARKERS)
