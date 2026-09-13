"""Postman Environment 解析器。

- 解析变量（key/value/type/disabled）
- 标记 secret 变量（type=secret 或 key 含 password/secret/token）
- 识别 UAT/生产环境（从 name 推断）
- 变量引用转换：值一律转为 env:XXX 引用，不明文入库
- 检查空值和禁用变量
- 禁止在日志中打印 value（只输出 key 和状态）
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)

# 敏感键模式（与 api_endpoint_script_executor._SENSITIVE_KEYS 对齐）
_SECRET_KEY_PATTERNS = re.compile(
    r"password|secret|token|apikey|api_key|appsecret|appsercret|authorization",
    re.IGNORECASE,
)

# 环境识别
_UAT_PATTERNS = re.compile(r"uat|test|staging|sit|qa", re.IGNORECASE)
_PROD_PATTERNS = re.compile(r"prod|live|production", re.IGNORECASE)


@dataclass
class ParsedEnvironmentVariable:
    """解析出的单个环境变量（不含明文值）。"""
    key: str
    value_ref: str  # env:XXX 引用，绝不存明文
    is_secret: bool
    enabled: bool
    has_value: bool  # 原值是否非空
    original_type: str  # postman 的 type 字段


@dataclass
class ParsedEnvironment:
    """解析结果。"""
    name: str
    environment: str  # uat / production / unknown
    variables: list[ParsedEnvironmentVariable]
    empty_keys: list[str] = field(default_factory=list)      # 值为空的变量名
    disabled_keys: list[str] = field(default_factory=list)   # 禁用的变量名
    secret_keys: list[str] = field(default_factory=list)     # 敏感变量名（不输出值）


class PostmanEnvironmentParser:
    """Postman Environment 解析器。"""

    def parse(self, content: bytes | str) -> ParsedEnvironment:
        if isinstance(content, bytes):
            content = content.decode("utf-8")
        data = json.loads(content)

        name = data.get("name", "Unnamed Environment")
        environment = self._detect_environment(name)

        raw_vars = data.get("values", data.get("variable", []))
        variables: list[ParsedEnvironmentVariable] = []
        empty_keys: list[str] = []
        disabled_keys: list[str] = []
        secret_keys: list[str] = []

        for v in raw_vars:
            if not isinstance(v, dict) or not v.get("key"):
                continue
            key = v["key"]
            raw_value = v.get("value", "")
            var_type = v.get("type", "default")
            # Postman 用 enabled=True/False 或 disabled=True/False
            enabled = v.get("enabled", True) and not v.get("disabled", False)

            is_secret = var_type == "secret" or bool(_SECRET_KEY_PATTERNS.search(key))
            has_value = bool(raw_value and str(raw_value).strip())

            # 值一律转为引用，不明文存储
            value_ref = f"env:{key}"

            variables.append(ParsedEnvironmentVariable(
                key=key,
                value_ref=value_ref,
                is_secret=is_secret,
                enabled=enabled,
                has_value=has_value,
                original_type=var_type,
            ))

            if not has_value:
                empty_keys.append(key)
            if not enabled:
                disabled_keys.append(key)
            if is_secret:
                secret_keys.append(key)

        result = ParsedEnvironment(
            name=name,
            environment=environment,
            variables=variables,
            empty_keys=empty_keys,
            disabled_keys=disabled_keys,
            secret_keys=secret_keys,
        )

        # 日志只输出 key 和状态，绝不输出值
        logger.info(
            "解析 Environment: name=%s env=%s vars=%d empty=%d disabled=%d secrets=%d",
            name, environment, len(variables),
            len(empty_keys), len(disabled_keys), len(secret_keys),
        )

        return result

    @staticmethod
    def _detect_environment(name: str) -> str:
        if _PROD_PATTERNS.search(name):
            return "production"
        if _UAT_PATTERNS.search(name):
            return "uat"
        return "unknown"
