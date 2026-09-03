"""
IDP 认证服务单元测试

测试 Token 注入、缓存、过期检测
"""

import pytest
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from app.services.idp_auth_service import IDPAuthService


class TestIDPAuthService:
    """IDP 认证服务测试"""

    @pytest.fixture(autouse=True)
    def reset_token(self):
        """每个测试前重置 Token 状态"""
        IDPAuthService._token = None
        IDPAuthService._token_expires_at = None
        yield
        IDPAuthService._token = None
        IDPAuthService._token_expires_at = None

    @pytest.mark.asyncio
    async def test_get_token_from_env(self):
        """测试从环境变量获取 Token"""
        with patch("app.config.settings.settings.idp_token", "env-token-12345"):
            token = await IDPAuthService.get_token()

        assert token == "env-token-12345"
        assert IDPAuthService._token == "env-token-12345"
        assert IDPAuthService._token_expires_at is not None

    @pytest.mark.asyncio
    async def test_get_token_cached(self):
        """测试 Token 缓存命中"""
        # 设置有效 Token
        IDPAuthService._token = "cached-token"
        IDPAuthService._token_expires_at = datetime.now(timezone.utc) + timedelta(hours=1)

        # 不设置环境变量，应该使用缓存
        with patch("app.config.settings.settings.idp_token", ""):
            token = await IDPAuthService.get_token()

        assert token == "cached-token"

    @pytest.mark.asyncio
    async def test_get_token_expired_refresh(self):
        """测试 Token 过期后从环境变量刷新"""
        # 设置过期 Token
        IDPAuthService._token = "expired-token"
        IDPAuthService._token_expires_at = datetime.now(timezone.utc) - timedelta(hours=1)

        with patch("app.config.settings.settings.idp_token", "new-env-token"):
            token = await IDPAuthService.get_token()

        assert token == "new-env-token"

    @pytest.mark.asyncio
    async def test_get_token_missing_error(self):
        """测试 Token 缺失时的错误提示"""
        with patch("app.config.settings.settings.idp_token", ""):
            with pytest.raises(ValueError, match="Token 未配置"):
                await IDPAuthService.get_token()

    @pytest.mark.asyncio
    async def test_clear_token(self):
        """测试清除 Token"""
        IDPAuthService._token = "test-token"
        IDPAuthService._token_expires_at = datetime.now(timezone.utc)

        await IDPAuthService.clear_token()

        assert IDPAuthService._token is None
        assert IDPAuthService._token_expires_at is None

    def test_get_token_status_configured(self):
        """测试 Token 状态 - 已配置"""
        IDPAuthService._token = None
        IDPAuthService._token_expires_at = None
        # 直接 patch settings 模块中的值
        from app.config.settings import settings as s
        original = s.idp_token
        try:
            s.idp_token = "test-token-12345"
            status = IDPAuthService.get_token_status()
            assert status["configured"] is True
            assert status["source"] == "env"
        finally:
            s.idp_token = original

    def test_get_token_status_not_configured(self):
        """测试 Token 状态 - 未配置"""
        with patch("app.config.settings.settings.idp_token", ""):
            status = IDPAuthService.get_token_status()

        assert status["configured"] is False
        assert status["source"] == "none"

    def test_get_token_status_expired(self):
        """测试 Token 状态 - 已过期"""
        IDPAuthService._token = "expired"
        IDPAuthService._token_expires_at = datetime.now(timezone.utc) - timedelta(hours=1)

        with patch("app.config.settings.settings.idp_token", ""):
            status = IDPAuthService.get_token_status()

        assert status["expired"] is True
