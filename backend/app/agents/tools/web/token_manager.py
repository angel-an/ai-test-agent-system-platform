"""
Token Manager - Token 获取、刷新与持久化管理模块

功能：
1. 通过登录 API 获取 Token
2. Token 自动刷新（在过期前）
3. Token 持久化存储（文件/内存）
4. 多环境 Token 隔离管理

适用场景：
- 开发环境：免验证码，直接 API 登录获取 Token
- 测试环境：验证码开启，需要先手动获取一次 Token，后续自动刷新
- 多项目多环境 Token 管理

使用示例：
    # 初始化 Token 管理器
    tm = TokenManager(
        login_api="https://example.com/api/login",
        username="admin",
        password="123456"
    )

    # 获取 Token（自动处理刷新）
    token = await tm.get_token()

    # 使用 Token 后，保存到文件供其他脚本使用
    tm.save_to_file("auth_token.txt")
"""
import asyncio
import json
import logging
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional, Dict, Any, Callable
from urllib.parse import urljoin, urlparse

import aiohttp
import requests

logger = logging.getLogger(__name__)


@dataclass
class TokenInfo:
    """Token 信息"""
    token: str
    refresh_token: Optional[str] = None
    expires_at: Optional[datetime] = None
    token_type: str = "Bearer"
    scope: Optional[str] = None
    created_at: datetime = field(default_factory=datetime.now)

    @property
    def is_expired(self) -> bool:
        """检查 Token 是否已过期"""
        if not self.expires_at:
            return False  # 永不过期
        return datetime.now() >= self.expires_at

    @property
    def is_expiring_soon(self, threshold_minutes: int = 10) -> bool:
        """检查 Token 是否即将过期"""
        if not self.expires_at:
            return False
        return datetime.now() >= self.expires_at - timedelta(minutes=threshold_minutes)

    @property
    def expires_in_seconds(self) -> Optional[int]:
        """获取剩余有效时间（秒）"""
        if not self.expires_at:
            return None
        remaining = (self.expires_at - datetime.now()).total_seconds()
        return max(0, int(remaining))

    def to_dict(self) -> dict:
        """序列化为字典"""
        return {
            "token": self.token,
            "refresh_token": self.refresh_token,
            "expires_at": self.expires_at.isoformat() if self.expires_at else None,
            "token_type": self.token_type,
            "scope": self.scope,
            "created_at": self.created_at.isoformat(),
        }

    @classmethod
    def from_dict(cls, data: dict) -> "TokenInfo":
        """从字典反序列化"""
        expires_at = None
        if data.get("expires_at"):
            expires_at = datetime.fromisoformat(data["expires_at"])
        created_at = datetime.now()
        if data.get("created_at"):
            created_at = datetime.fromisoformat(data["created_at"])

        return cls(
            token=data["token"],
            refresh_token=data.get("refresh_token"),
            expires_at=expires_at,
            token_type=data.get("token_type", "Bearer"),
            scope=data.get("scope"),
            created_at=created_at,
        )


@dataclass
class LoginConfig:
    """登录配置"""
    login_api: str  # 登录 API URL
    username: str
    password: str
    verify_code: Optional[str] = None  # 验证码（可选）
    unique_id: Optional[str] = None  # 验证码唯一ID（可选）

    # API 参数映射
    username_field: str = "username"
    password_field: str = "password"
    verify_code_field: str = "verifyCode"
    unique_id_field: str = "uniqueId"

    # 请求配置
    headers: Dict[str, str] = field(default_factory=lambda: {
        "Content-Type": "application/json",
        "Referer": "",
    })
    timeout: int = 30

    # Token 提取路径
    token_path: str = "data.token"  # JSON 路径，如 "data.token" 或 "data.accessToken"
    refresh_token_path: Optional[str] = None
    expires_in_path: Optional[str] = None  # 过期时间（秒）

    # 验证码 API（用于自动获取验证码）
    captcha_api: Optional[str] = None

    def get_login_payload(self) -> dict:
        """获取登录请求体"""
        payload = {
            self.username_field: self.username,
            self.password_field: self.password,
        }
        if self.verify_code:
            payload[self.verify_code_field] = self.verify_code
        if self.unique_id:
            payload[self.unique_id_field] = self.unique_id
        return payload


class TokenManager:
    """
    Token 管理器

    管理 Token 的获取、刷新和持久化。

    使用示例：
        tm = TokenManager(
            login_config=LoginConfig(
                login_api="https://example.com/api/login",
                username="admin",
                password="123456",
                token_path="data.token"
            ),
            storage_path="tokens/auth.json"
        )

        # 获取有效 Token（自动刷新）
        token = await tm.get_valid_token()

        # 强制重新登录
        token = await tm.force_login()
    """

    def __init__(
        self,
        login_config: LoginConfig,
        storage_path: Optional[Path] = None,
        auto_refresh: bool = True,
        refresh_threshold_minutes: int = 10,
    ):
        """
        Args:
            login_config: 登录配置
            storage_path: Token 持久化文件路径
            auto_refresh: 是否自动刷新
            refresh_threshold_minutes: 提前刷新阈值（分钟）
        """
        self.config = login_config
        self.storage_path = Path(storage_path) if storage_path else None
        self.auto_refresh = auto_refresh
        self.refresh_threshold = refresh_threshold_minutes

        self._token_info: Optional[TokenInfo] = None
        self._lock = asyncio.Lock()

        # 尝试从文件加载
        self._load_from_file()

    # =========================================================================
    # 核心方法
    # =========================================================================

    async def get_token(self) -> Optional[str]:
        """获取 Token（自动处理过期和刷新）"""
        async with self._lock:
            # 1. 检查当前 Token 是否有效
            if self._token_info and not self._token_info.is_expired:
                if not self._token_info.is_expiring_soon(self.refresh_threshold):
                    logger.debug("Token 有效且未接近过期，直接返回")
                    return self._token_info.token

            # 2. 尝试刷新
            if self.auto_refresh and self._token_info and self._token_info.refresh_token:
                logger.info("Token 即将过期，尝试刷新")
                refreshed = await self._refresh_token()
                if refreshed:
                    return self._token_info.token

            # 3. 重新登录
            logger.info("Token 无效或刷新失败，执行重新登录")
            success = await self._do_login()
            if success:
                return self._token_info.token

            return None

    async def get_valid_token(self) -> Optional[str]:
        """获取有效 Token（get_token 的别名）"""
        return await self.get_token()

    async def force_login(self) -> Optional[str]:
        """强制重新登录获取新 Token"""
        async with self._lock:
            self._token_info = None
            success = await self._do_login()
            return self._token_info.token if success else None

    def get_token_info(self) -> Optional[TokenInfo]:
        """获取完整 Token 信息"""
        return self._token_info

    def is_token_valid(self) -> bool:
        """检查当前 Token 是否有效"""
        return self._token_info is not None and not self._token_info.is_expired

    # =========================================================================
    # 登录实现
    # =========================================================================

    async def _do_login(self) -> bool:
        """执行登录请求"""
        try:
            # 如果需要验证码，先获取
            if self.config.captcha_api and not self.config.verify_code:
                await self._fetch_captcha()

            payload = self.config.get_login_payload()
            headers = self.config.headers.copy()

            # 设置 Referer
            if not headers.get("Referer"):
                parsed = urlparse(self.config.login_api)
                headers["Referer"] = f"{parsed.scheme}://{parsed.netloc}/"

            logger.info(f"执行登录请求: {self.config.login_api}")
            logger.debug(f"登录用户名: {self.config.username}")

            # 发送请求
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    self.config.login_api,
                    json=payload,
                    headers=headers,
                    timeout=aiohttp.ClientTimeout(total=self.config.timeout),
                    ssl=False,  # 允许自签名证书
                ) as response:
                    status = response.status
                    text = await response.text()

                    logger.info(f"登录响应状态: {status}")
                    logger.debug(f"登录响应: {text[:500]}")

                    if status != 200:
                        logger.error(f"登录请求失败: HTTP {status}")
                        return False

                    # 解析响应
                    try:
                        data = json.loads(text)
                    except json.JSONDecodeError:
                        logger.error(f"响应不是有效 JSON: {text[:200]}")
                        return False

                    # 检查业务状态码
                    result_code = data.get("resultCode") or data.get("code")
                    if result_code not in (200, "200", "0", 0, "success"):
                        result_msg = data.get("resultMsg") or data.get("message") or "未知错误"
                        logger.error(f"登录失败: {result_msg} (code={result_code})")
                        return False

                    # 提取 Token
                    token = self._extract_value(data, self.config.token_path)
                    if not token:
                        logger.error(f"无法从响应中提取 Token，路径: {self.config.token_path}")
                        logger.debug(f"响应数据: {json.dumps(data, ensure_ascii=False)[:500]}")
                        return False

                    # 提取刷新 Token
                    refresh_token = None
                    if self.config.refresh_token_path:
                        refresh_token = self._extract_value(data, self.config.refresh_token_path)

                    # 提取过期时间
                    expires_at = None
                    if self.config.expires_in_path:
                        expires_in = self._extract_value(data, self.config.expires_in_path)
                        if expires_in:
                            try:
                                expires_seconds = int(expires_in)
                                expires_at = datetime.now() + timedelta(seconds=expires_seconds)
                            except (ValueError, TypeError):
                                pass

                    # 创建 TokenInfo
                    self._token_info = TokenInfo(
                        token=token,
                        refresh_token=refresh_token,
                        expires_at=expires_at,
                    )

                    logger.info(f"✅ 登录成功，Token: {token[:20]}...")
                    if expires_at:
                        logger.info(f"Token 过期时间: {expires_at.isoformat()}")

                    # 保存到文件
                    self._save_to_file()
                    return True

        except asyncio.TimeoutError:
            logger.error("登录请求超时")
        except Exception as e:
            logger.error(f"登录请求异常: {e}")

        return False

    async def _refresh_token(self) -> bool:
        """刷新 Token"""
        if not self._token_info or not self._token_info.refresh_token:
            return False

        # TODO: 实现刷新逻辑（根据具体 API）
        # 大多数系统刷新 API: POST /api/refresh-token
        # body: { refreshToken: "xxx" }
        logger.info("Token 刷新未实现，执行重新登录")
        return False

    async def _fetch_captcha(self):
        """获取验证码（用于自动登录）"""
        try:
            from .captcha_ocr import CaptchaOCR

            headers = self.config.headers.copy()
            parsed = urlparse(self.config.login_api)
            headers["Referer"] = f"{parsed.scheme}://{parsed.netloc}/"

            async with aiohttp.ClientSession() as session:
                async with session.get(
                    self.config.captcha_api,
                    headers=headers,
                    timeout=aiohttp.ClientTimeout(total=10),
                    ssl=False,
                ) as response:
                    data = await response.json()

                    # 提取验证码图片和 uniqueId
                    unique_id = self._extract_value(data, "data.uniqueId")
                    image_b64 = self._extract_value(data, "data.image")

                    if not unique_id or not image_b64:
                        logger.warning("无法从验证码 API 提取数据")
                        return

                    # 解码图片并 OCR 识别
                    import base64
                    import tempfile

                    image_bytes = base64.b64decode(image_b64.replace("\n", ""))

                    with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as f:
                        f.write(image_bytes)
                        temp_path = f.name

                    try:
                        ocr = CaptchaOCR()
                        captcha_text = ocr.recognize(temp_path)

                        if captcha_text:
                            self.config.verify_code = captcha_text
                            self.config.unique_id = unique_id
                            logger.info(f"验证码自动识别成功: {captcha_text}")
                        else:
                            logger.warning("验证码自动识别失败")
                    finally:
                        Path(temp_path).unlink(missing_ok=True)

        except Exception as e:
            logger.warning(f"获取验证码失败: {e}")

    # =========================================================================
    # 持久化
    # =========================================================================

    def save_to_file(self, path: Optional[Path] = None):
        """保存 Token 到文件"""
        save_path = path or self.storage_path
        if not save_path:
            logger.warning("未指定存储路径，跳过保存")
            return

        self._save_to_file(save_path)

    def _save_to_file(self, path: Optional[Path] = None):
        """内部保存方法"""
        save_path = path or self.storage_path
        if not save_path or not self._token_info:
            return

        try:
            save_path.parent.mkdir(parents=True, exist_ok=True)
            data = self._token_info.to_dict()
            save_path.write_text(json.dumps(data, ensure_ascii=False, indent=2))
            logger.info(f"Token 已保存到: {save_path}")
        except Exception as e:
            logger.warning(f"保存 Token 失败: {e}")

    def _load_from_file(self):
        """从文件加载 Token"""
        if not self.storage_path or not self.storage_path.exists():
            return

        try:
            data = json.loads(self.storage_path.read_text())
            self._token_info = TokenInfo.from_dict(data)

            if self._token_info.is_expired:
                logger.info("加载的 Token 已过期，将在下次使用时重新登录")
                self._token_info = None
            else:
                logger.info(f"从文件加载 Token: {self._token_info.token[:20]}...")
                if self._token_info.expires_at:
                    remaining = self._token_info.expires_in_seconds
                    logger.info(f"Token 剩余有效时间: {remaining} 秒")

        except Exception as e:
            logger.warning(f"加载 Token 文件失败: {e}")
            self._token_info = None

    # =========================================================================
    # 工具方法
    # =========================================================================

    @staticmethod
    def _extract_value(data: dict, path: str) -> Any:
        """从嵌套字典中提取值，路径如 'data.token'"""
        current = data
        for key in path.split("."):
            if isinstance(current, dict) and key in current:
                current = current[key]
            else:
                return None
        return current

    def clear(self):
        """清除 Token"""
        self._token_info = None
        if self.storage_path and self.storage_path.exists():
            self.storage_path.unlink()
            logger.info("Token 已清除")


# =============================================================================
# 多环境 Token 管理
# =============================================================================

class MultiEnvTokenManager:
    """
    多环境 Token 管理器

    管理多个环境（开发/测试/生产）的 Token。

    使用示例：
        manager = MultiEnvTokenManager(base_dir="tokens")

        # 注册环境
        manager.register_env("dev", LoginConfig(
            login_api="https://dev.example.com/api/login",
            username="admin",
            password="123456",
            token_path="data.token"
        ))

        manager.register_env("staging", LoginConfig(
            login_api="https://staging.example.com/api/login",
            username="admin",
            password="123456",
            token_path="data.token"
        ))

        # 获取指定环境的 Token
        token = await manager.get_token("dev")
    """

    def __init__(self, base_dir: Path = Path("tokens")):
        self.base_dir = Path(base_dir)
        self.base_dir.mkdir(parents=True, exist_ok=True)
        self._managers: Dict[str, TokenManager] = {}

    def register_env(self, env_name: str, login_config: LoginConfig):
        """注册环境"""
        storage_path = self.base_dir / f"{env_name}.json"
        manager = TokenManager(
            login_config=login_config,
            storage_path=storage_path,
        )
        self._managers[env_name] = manager
        logger.info(f"注册环境: {env_name}")

    async def get_token(self, env_name: str) -> Optional[str]:
        """获取指定环境的 Token"""
        manager = self._managers.get(env_name)
        if not manager:
            logger.error(f"未注册环境: {env_name}")
            return None
        return await manager.get_token()

    def get_manager(self, env_name: str) -> Optional[TokenManager]:
        """获取指定环境的 TokenManager"""
        return self._managers.get(env_name)

    def list_envs(self) -> list:
        """列出所有注册的环境"""
        return list(self._managers.keys())


# =============================================================================
# 便捷函数
# =============================================================================

async def get_token_by_login(
    login_api: str,
    username: str,
    password: str,
    token_path: str = "data.token",
    verify_code: Optional[str] = None,
    **kwargs
) -> Optional[str]:
    """
    通过登录 API 获取 Token（一次性）

    Args:
        login_api: 登录 API URL
        username: 用户名
        password: 密码
        token_path: Token 在响应 JSON 中的路径
        verify_code: 验证码（可选）
        **kwargs: 其他配置

    Returns:
        Token 字符串，失败返回 None

    示例：
        token = await get_token_by_login(
            "https://example.com/api/login",
            "admin",
            "123456"
        )
    """
    config = LoginConfig(
        login_api=login_api,
        username=username,
        password=password,
        token_path=token_path,
        verify_code=verify_code,
        **kwargs
    )

    manager = TokenManager(config, auto_refresh=False)
    return await manager.force_login()


def create_token_manager_for_peets(
    username: str = "lianqiao_peets",
    password: str = "Lq123456",
    env: str = "staging",
    storage_dir: Path = Path("tokens")
) -> TokenManager:
    """
    创建针对 Peets 项目的 TokenManager

    Args:
        username: 用户名
        password: 密码
        env: 环境 (staging/prod)
        storage_dir: Token 存储目录

    Returns:
        TokenManager 实例
    """
    base_url = "https://console-stg-internal.peets.cn" if env == "staging" else "https://console.peets.cn"

    config = LoginConfig(
        login_api=f"{base_url}/yundt-saas-gateway/api/user/v1/users/ssouser/login",
        username=username,
        password=password,
        token_path="data.token",
        headers={
            "Content-Type": "application/json",
            "Referer": f"{base_url}/dtcloud-console-web-pc/",
        },
        captcha_api=f"{base_url}/yundt-saas-gateway/api/icdp/v1/users/verify/img",
    )

    storage_path = storage_dir / f"peets_{env}_token.json"

    return TokenManager(
        login_config=config,
        storage_path=storage_path,
    )


# =============================================================================
# 测试
# =============================================================================

if __name__ == "__main__":
    import asyncio

    async def test():
        # 测试 TokenManager
        tm = create_token_manager_for_peets()
        token = await tm.get_token()
        if token:
            print(f"获取 Token 成功: {token[:30]}...")
            print(f"Token 信息: {tm.get_token_info()}")
        else:
            print("获取 Token 失败")

    asyncio.run(test())
