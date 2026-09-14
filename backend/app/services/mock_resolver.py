"""
Mock 规则解析器

支持：
- URL 模式匹配（glob 风格）
- HTTP 方法匹配
- Header 匹配
- 返回预设响应或透传真实请求
"""

from __future__ import annotations

import fnmatch
import json
import logging
from typing import Any, Dict, Optional

from app.schemas.intent import MockConfig, MockRule

logger = logging.getLogger(__name__)


class MockResolver:
    """Mock 规则解析器"""

    def __init__(self, config: Optional[MockConfig] = None):
        self.config = config
        self._match_stats: Dict[str, int] = {}  # 统计匹配次数

    def is_enabled(self) -> bool:
        """Mock 是否启用"""
        return self.config is not None and self.config.enabled

    def resolve(
        self,
        method: str,
        url: str,
        headers: Optional[Dict[str, str]] = None,
    ) -> Optional[Dict[str, Any]]:
        """
        解析 mock 规则，返回 mock 响应或 None（透传）

        Args:
            method: HTTP 方法
            url: 请求 URL
            headers: 请求头

        Returns:
            mock 响应 {status, headers, body, delay_ms} 或 None
        """
        if not self.is_enabled():
            return None

        for rule in self.config.rules if self.config else []:
            if not rule.enabled:
                continue

            if self._match_rule(rule, method, url, headers):
                self._match_stats[rule.name] = self._match_stats.get(rule.name, 0) + 1
                logger.info(f"Mock 匹配: {rule.name} -> {url}")
                return {
                    "status": rule.response_status,
                    "headers": rule.response_headers or {},
                    "body": rule.response_body,
                    "delay_ms": rule.response_delay_ms,
                    "mocked": True,
                    "rule_name": rule.name,
                }

        # 未匹配，检查是否透传
        if self.config and self.config.rules:
            # 如果有规则但未匹配，检查是否有 passthrough 规则
            for rule in self.config.rules:
                if rule.passthrough:
                    return None  # 透传

        return None

    def _match_rule(
        self,
        rule: MockRule,
        method: str,
        url: str,
        headers: Optional[Dict[str, str]] = None,
    ) -> bool:
        """检查规则是否匹配请求"""
        # 方法匹配
        if rule.match_method and rule.match_method.upper() != method.upper():
            return False

        # URL 匹配（glob 模式）
        if rule.match_url_pattern:
            if not fnmatch.fnmatch(url, rule.match_url_pattern):
                return False

        # Header 匹配
        if rule.match_headers and headers:
            for key, expected_value in rule.match_headers.items():
                actual_value = headers.get(key)
                if actual_value != expected_value:
                    return False

        return True

    def get_stats(self) -> Dict[str, int]:
        """获取 mock 匹配统计"""
        return self._match_stats.copy()


def create_mock_from_dict(data: Dict[str, Any]) -> MockConfig:
    """从字典创建 MockConfig"""
    rules = []
    for rule_data in data.get("rules", []):
        rules.append(MockRule(**rule_data))

    return MockConfig(
        enabled=data.get("enabled", False),
        base_url=data.get("base_url"),
        rules=rules,
    )
