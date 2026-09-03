"""
缺陷指纹服务

用于生成缺陷的唯一指纹，实现去重功能。

指纹由以下内容生成：
- source_project_key
- HTTP method
- normalized URL
- error type
- failure summary
"""

import hashlib
import logging
import re
from typing import Optional

logger = logging.getLogger(__name__)


class DefectFingerprintService:
    """
    缺陷指纹服务

    生成缺陷的唯一标识，用于去重判断
    """

    @staticmethod
    def generate(
        source_project_key: str,
        method: str,
        url: str,
        error_type: str,
        failure_summary: str,
    ) -> str:
        """
        生成缺陷指纹

        Args:
            source_project_key: 本地项目标识符
            method: HTTP 方法
            url: API URL
            error_type: 错误类型
            failure_summary: 失败摘要

        Returns:
            str: 缺陷指纹（SHA-256 哈希值）
        """
        # 规范化 URL
        normalized_url = DefectFingerprintService._normalize_url(url)

        # 规范化错误摘要（去除动态内容）
        normalized_summary = DefectFingerprintService._normalize_summary(failure_summary)

        # 组合指纹内容
        fingerprint_content = "|".join([
            source_project_key.strip().upper(),
            method.strip().upper(),
            normalized_url,
            error_type.strip().lower(),
            normalized_summary,
        ])

        # 生成 SHA-256 哈希
        hash_value = hashlib.sha256(fingerprint_content.encode("utf-8")).hexdigest()

        logger.debug(
            "[DefectFingerprint] 生成指纹: content=%s, hash=%s",
            fingerprint_content,
            hash_value[:16] + "...",
        )

        return hash_value

    @staticmethod
    def _normalize_url(url: str) -> str:
        """
        规范化 URL

        - 去除协议和域名
        - 去除查询参数中的动态值
        - 去除尾部斜杠
        """
        if not url:
            return ""

        # 去除协议和域名
        url = re.sub(r"^https?://[^/]+", "", url)

        # 去除查询参数（保留路径）
        url = url.split("?")[0]

        # 去除路径中的动态 ID（如 /users/12345 → /users/{id}）
        url = re.sub(r"/\d+(/|$)", r"/{id}\1", url)
        url = re.sub(r"/[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}(/|$)", r"/{uuid}\1", url)

        # 去除尾部斜杠
        url = url.rstrip("/")

        return url

    @staticmethod
    def _normalize_summary(summary: str) -> str:
        """
        规范化错误摘要

        - 去除动态内容（如时间戳、随机数等）
        - 统一大小写
        - 去除多余空格
        """
        if not summary:
            return ""

        # 去除时间戳
        summary = re.sub(r"\d{4}-\d{2}-\d{2}[\sT]\d{2}:\d{2}:\d{2}(\.\d+)?", "{timestamp}", summary)

        # 去除 UUID
        summary = re.sub(
            r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}",
            "{uuid}",
            summary,
            flags=re.IGNORECASE,
        )

        # 去除数字 ID
        summary = re.sub(r"\b\d{5,}\b", "{id}", summary)

        # 去除堆栈跟踪中的行号
        summary = re.sub(r":\d+\)?$", "", summary, flags=re.MULTILINE)

        # 统一大小写并去除多余空格
        summary = " ".join(summary.lower().split())

        # 截断过长的摘要
        if len(summary) > 200:
            summary = summary[:200]

        return summary

    @staticmethod
    def generate_from_api_result(
        source_project_key: str,
        method: str,
        endpoint: str,
        error_type: str,
        failure_summary: str,
    ) -> str:
        """
        从 API 测试结果生成指纹

        便捷方法
        """
        return DefectFingerprintService.generate(
            source_project_key=source_project_key,
            method=method,
            url=endpoint,
            error_type=error_type,
            failure_summary=failure_summary,
        )
