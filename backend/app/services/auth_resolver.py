"""
认证解析器

支持多种认证方式：
- bearer: Bearer Token（支持环境变量读取、AES/RSA 解密、401 自动刷新）
- basic: Basic Auth（username + password）
- api_key: API Key（header 或 query 参数）
- oauth2: OAuth2 Client Credentials / Refresh Token
"""

from __future__ import annotations

import base64
import json
import logging
import os
import re
from typing import Any, Dict, Optional

import httpx

# cryptography imports for AES/RSA decryption
from cryptography.hazmat.backends import default_backend
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

from app.schemas.intent import AuthConfig

logger = logging.getLogger(__name__)


class AuthResolver:
    """认证解析器 - 将 AuthConfig 解析为 HTTP 请求头"""

    def __init__(self, environ: Optional[Dict[str, str]] = None):
        self.environ = environ or os.environ
        self._token_cache: Dict[str, str] = {}  # 缓存 token 避免重复获取

    async def resolve(self, auth: Optional[AuthConfig]) -> Dict[str, str]:
        """
        解析认证配置，返回 HTTP 请求头字典

        Args:
            auth: 认证配置

        Returns:
            请求头字典，如 {"Authorization": "Bearer xxx"}
        """
        if not auth or auth.type == "none":
            return {}

        try:
            if auth.type == "bearer":
                return await self._resolve_bearer(auth)
            elif auth.type == "basic":
                return self._resolve_basic(auth)
            elif auth.type == "api_key":
                return self._resolve_api_key(auth)
            elif auth.type == "oauth2":
                return await self._resolve_oauth2(auth)
            else:
                logger.warning(f"不支持的认证类型: {auth.type}")
                return {}
        except Exception as e:
            logger.error(f"认证解析失败: {e}", exc_info=True)
            return {}

    async def _resolve_bearer(self, auth: AuthConfig) -> Dict[str, str]:
        """解析 Bearer Token"""
        token = auth.token

        # 从环境变量读取
        if not token and auth.token_env_var:
            token = self.environ.get(auth.token_env_var)
            if token:
                logger.debug(f"从环境变量 {auth.token_env_var} 读取 token")

        if not token:
            logger.warning("Bearer token 未配置")
            return {}

        # 解密（如果需要）
        if auth.decrypt_before_use and auth.encrypt_type != "none":
            token = self._decrypt_token(token, auth)

        return {"Authorization": f"Bearer {token}"}

    def _resolve_basic(self, auth: AuthConfig) -> Dict[str, str]:
        """解析 Basic Auth"""
        username = auth.username or ""
        password = auth.password

        # 从环境变量读取密码
        if not password and auth.password_env_var:
            password = self.environ.get(auth.password_env_var)

        if not username or password is None:
            logger.warning("Basic auth 用户名或密码未配置")
            return {}

        # 解密（如果需要）
        if auth.decrypt_before_use and auth.encrypt_type != "none" and password:
            password = self._decrypt_token(password, auth)

        credentials = base64.b64encode(f"{username}:{password}".encode()).decode()
        return {"Authorization": f"Basic {credentials}"}

    def _resolve_api_key(self, auth: AuthConfig) -> Dict[str, str]:
        """解析 API Key"""
        key_name = auth.api_key_name or "X-API-Key"
        key_value = auth.api_key_value or ""

        if not key_value:
            logger.warning("API Key 值未配置")
            return {}

        if auth.api_key_in == "query":
            # query 参数在 URL 中处理，这里返回空 headers
            return {"_api_key_query": f"{key_name}={key_value}"}

        return {key_name: key_value}

    async def _resolve_oauth2(self, auth: AuthConfig) -> Dict[str, str]:
        """解析 OAuth2 - 支持 client_credentials 和 refresh_token"""
        cache_key = f"oauth2_{auth.client_id}_{auth.token_url}"

        # 检查缓存
        if cache_key in self._token_cache:
            return {"Authorization": f"Bearer {self._token_cache[cache_key]}"}

        if not auth.token_url:
            logger.warning("OAuth2 token_url 未配置")
            return {}

        try:
            # 优先使用 refresh_token
            if auth.refresh_token:
                token_data = await self._oauth2_refresh(auth)
            else:
                token_data = await self._oauth2_client_credentials(auth)

            access_token = token_data.get("access_token")
            if access_token:
                self._token_cache[cache_key] = access_token
                return {"Authorization": f"Bearer {access_token}"}

        except Exception as e:
            logger.error(f"OAuth2 认证失败: {e}", exc_info=True)

        return {}

    async def _oauth2_client_credentials(self, auth: AuthConfig) -> Dict[str, Any]:
        """OAuth2 Client Credentials 流程"""
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(
                auth.token_url,
                data={
                    "grant_type": "client_credentials",
                    "client_id": auth.client_id or "",
                    "client_secret": auth.client_secret or "",
                    "scope": auth.scope or "",
                },
                headers={"Content-Type": "application/x-www-form-urlencoded"},
            )
            response.raise_for_status()
            return response.json()

    async def _oauth2_refresh(self, auth: AuthConfig) -> Dict[str, Any]:
        """OAuth2 Refresh Token 流程"""
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(
                auth.token_url,
                data={
                    "grant_type": "refresh_token",
                    "refresh_token": auth.refresh_token or "",
                    "client_id": auth.client_id or "",
                    "client_secret": auth.client_secret or "",
                },
                headers={"Content-Type": "application/x-www-form-urlencoded"},
            )
            response.raise_for_status()
            return response.json()

    def _decrypt_token(self, encrypted: str, auth: AuthConfig) -> str:
        """
        解密 token

        支持 AES 和 RSA 解密，密钥从环境变量读取
        """
        if auth.encrypt_type == "none" or not auth.encrypt_key_env_var:
            return encrypted

        key = self.environ.get(auth.encrypt_key_env_var)
        if not key:
            logger.warning(f"解密密钥环境变量 {auth.encrypt_key_env_var} 未设置")
            return encrypted

        try:
            if auth.encrypt_type == "aes":
                return self._aes_decrypt(encrypted, key)
            elif auth.encrypt_type == "rsa":
                return self._rsa_decrypt(encrypted, key)
        except Exception as e:
            logger.error(f"解密失败: {e}", exc_info=True)

        return encrypted

    def _aes_decrypt(self, encrypted: str, key: str) -> str:
        """AES 解密"""
        try:
            # key 和 iv 从密钥派生（简化实现，实际应根据具体加密方案调整）
            key_bytes = key.encode()[:32].ljust(32, b"\0")
            iv = key_bytes[:16]

            cipher = Cipher(algorithms.AES(key_bytes), modes.CBC(iv), backend=default_backend())
            decryptor = cipher.decryptor()

            encrypted_bytes = base64.b64decode(encrypted)
            decrypted = decryptor.update(encrypted_bytes) + decryptor.finalize()

            # 去除 PKCS7 填充
            padding_len = decrypted[-1]
            return decrypted[:-padding_len].decode("utf-8")
        except Exception as e:
            logger.error(f"AES 解密失败: {e}")
            raise

    def _rsa_decrypt(self, encrypted: str, key: str) -> str:
        """RSA 解密"""
        try:
            from cryptography.hazmat.primitives import serialization
            from cryptography.hazmat.primitives.asymmetric import padding as asym_padding
            from cryptography.hazmat.primitives import hashes

            private_key = serialization.load_pem_private_key(
                key.encode(), password=None, backend=default_backend()
            )

            encrypted_bytes = base64.b64decode(encrypted)
            decrypted = private_key.decrypt(
                encrypted_bytes,
                asym_padding.OAEP(
                    mgf=asym_padding.MGF1(algorithm=hashes.SHA256()),
                    algorithm=hashes.SHA256(),
                    label=None,
                ),
            )
            return decrypted.decode("utf-8")
        except Exception as e:
            logger.error(f"RSA 解密失败: {e}")
            raise
            raise

    async def refresh_on_401(
        self,
        auth: Optional[AuthConfig],
        original_headers: Dict[str, str],
    ) -> Optional[Dict[str, str]]:
        """
        401 时尝试刷新认证

        Returns:
            新的请求头，或 None（刷新失败）
        """
        if not auth or not auth.auto_refresh_on_401:
            return None

        if auth.type == "oauth2" and auth.refresh_token:
            # OAuth2 refresh
            try:
                token_data = await self._oauth2_refresh(auth)
                access_token = token_data.get("access_token")
                if access_token:
                    return {"Authorization": f"Bearer {access_token}"}
            except Exception as e:
                logger.error(f"OAuth2 刷新失败: {e}")

        elif auth.type == "bearer" and auth.token_env_var:
            # 尝试重新从环境变量读取
            token = self.environ.get(auth.token_env_var)
            if token:
                if auth.decrypt_before_use and auth.encrypt_type != "none":
                    token = self._decrypt_token(token, auth)
                return {"Authorization": f"Bearer {token}"}

        return None


def resolve_auth_from_profile(profile: str, environ: Optional[Dict[str, str]] = None) -> AuthConfig:
    """
    从 data_profile 解析认证配置

    环境变量命名规范：
    - API_TEST_{PROFILE}_AUTHORIZATION
    - API_TEST_{PROFILE}_AUTH_HEADER
    """
    env = environ or os.environ
    normalized = re.sub(r"[^A-Za-z0-9]", "_", profile).upper()
    prefix = f"API_TEST_{normalized}_"

    auth = AuthConfig(type="none")

    # 尝试读取各种认证信息
    token = env.get(f"{prefix}AUTHORIZATION")
    auth_header = env.get(f"{prefix}AUTH_HEADER")

    if token:
        auth.type = "bearer"
        auth.token = token
        auth.token_env_var = f"{prefix}AUTHORIZATION"
    elif auth_header:
        # 解析 Basic auth header
        if auth_header.startswith("Basic "):
            auth.type = "basic"
            try:
                decoded = base64.b64decode(auth_header[6:]).decode()
                username, password = decoded.split(":", 1)
                auth.username = username
                auth.password = password
            except Exception:
                pass
        elif auth_header.startswith("Bearer "):
            auth.type = "bearer"
            auth.token = auth_header[7:]

    return auth
