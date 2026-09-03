"""
IDP 认证服务

负责 IDP 平台的认证 Token 管理
MVP 阶段支持两种方式：
1. 环境变量直接注入 Token（推荐）
2. 用户名密码登录（联调发现该接口有重定向问题，暂不建议使用）

Token 安全要求：
- 禁止 Token 出现在代码、配置提交、日志和报告中
- 所有日志必须脱敏
- Token 通过环境变量 IDP_TOKEN 注入
"""

import asyncio
import logging
from datetime import datetime, timedelta, timezone
from typing import Optional

from app.config.settings import settings

logger = logging.getLogger(__name__)


class IDPAuthService:
    """
    IDP 认证服务

    MVP 阶段优先使用环境变量注入的 Token。
    支持 Token 缓存和过期检测。
    """

    _token: Optional[str] = None
    _token_expires_at: Optional[datetime] = None
    _lock: asyncio.Lock = asyncio.Lock()

    # Token 提前刷新时间（秒）
    TOKEN_REFRESH_BUFFER = 300  # 提前 5 分钟刷新

    @classmethod
    async def get_token(cls) -> str:
        """
        获取有效的认证 Token

        优先级：
        1. 缓存的 Token（未过期）
        2. 环境变量 IDP_TOKEN
        3. 尝试登录（不推荐，MVP 阶段可能不可用）

        Returns:
            str: 有效的认证 Token

        Raises:
            ValueError: Token 未配置
            Exception: 登录失败时抛出异常
        """
        async with cls._lock:
            # 1. 检查缓存的 Token 是否有效
            if cls._token and cls._token_expires_at:
                if datetime.now(timezone.utc) < cls._token_expires_at:
                    return cls._token

            # 2. 尝试从环境变量获取 Token（MVP 阶段主要方式）
            env_token = settings.idp_token
            if env_token and len(env_token) > 10:
                cls._token = env_token
                # 环境变量注入的 Token 默认 12 小时有效期
                cls._token_expires_at = datetime.now(timezone.utc) + timedelta(
                    seconds=43200 - cls.TOKEN_REFRESH_BUFFER
                )
                logger.info("[IDPAuth] 使用环境变量注入的 Token")
                return cls._token

            # 3. Token 未配置
            logger.error(
                "[IDPAuth] IDP Token 未配置。"
                "请设置环境变量 IDP_TOKEN=your_token "
                "或配置 IDP_USERNAME + IDP_PASSWORD"
            )
            raise ValueError(
                "IDP Token 未配置。"
                "MVP 阶段请通过环境变量 IDP_TOKEN 注入 Token。"
                "示例: export IDP_TOKEN='your-token-here'"
            )

    @classmethod
    async def clear_token(cls) -> None:
        """清除当前 Token（用于 401 后强制刷新）"""
        async with cls._lock:
            old_token = cls._token
            cls._token = None
            cls._token_expires_at = None
            # 脱敏日志
            if old_token:
                masked = old_token[:8] + "..." + old_token[-4:] if len(old_token) > 12 else "***"
                logger.info("[IDPAuth] Token 已清除 (原 Token: %s)", masked)

    @classmethod
    def get_token_status(cls) -> dict:
        """
        获取 Token 状态（用于健康检查）

        Returns:
            dict: Token 状态信息（不包含 Token 本身）
        """
        has_env_token = bool(settings.idp_token and len(settings.idp_token) > 10)
        has_cached_token = bool(cls._token)

        status = {
            "configured": has_env_token or has_cached_token,
            "source": "env" if has_env_token else ("cache" if has_cached_token else "none"),
            "expires_at": cls._token_expires_at.isoformat() if cls._token_expires_at else None,
            "expired": False,
        }

        if cls._token_expires_at:
            status["expired"] = datetime.now(timezone.utc) >= cls._token_expires_at

        return status
