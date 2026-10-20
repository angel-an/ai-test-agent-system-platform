"""
服务端敏感值加密工具

用于加密存储端点 env_config 里的密钥类字段（appsecret/secret/token 等），
避免明文落库。执行器在服务端读取时解密还原真实值注入子进程。

密钥来源优先级：
1. 环境变量 ENV_SECRET_KEY（生产应设置为 32+ 字节随机值，经 Fernet 规范化）；
2. 回退用 settings.secret_key 派生（兼容本地开发，生产必须设置 ENV_SECRET_KEY）。

加密值在存储与 API 响应中带 `enc:v1:` 前缀标识，便于区分明文/密文并平滑迁移。
"""

from __future__ import annotations

import base64
import hashlib
import os

from cryptography.fernet import Fernet, InvalidToken

from app.config.settings import settings

_PREFIX = "enc:v1:"
_fernet: Fernet | None = None


def _get_fernet() -> Fernet:
    global _fernet
    if _fernet is None:
        raw = os.environ.get("ENV_SECRET_KEY") or settings.secret_key or "dev-insecure-key"
        # Fernet 要求 32 字节 url-safe base64 密钥：对任意长度种子做 SHA256 派生
        key = base64.urlsafe_b64encode(hashlib.sha256(raw.encode("utf-8")).digest())
        _fernet = Fernet(key)
    return _fernet


def encrypt_value(plaintext: str) -> str:
    """加密单个敏感值，返回带前缀的密文字符串。"""
    token = _get_fernet().encrypt(plaintext.encode("utf-8")).decode("utf-8")
    return _PREFIX + token


def decrypt_value(stored: str) -> str:
    """解密存储值。非密文（无前缀）原样返回，兼容历史明文数据。"""
    if not isinstance(stored, str) or not stored.startswith(_PREFIX):
        return stored
    token = stored[len(_PREFIX):]
    try:
        return _get_fernet().decrypt(token.encode("utf-8")).decode("utf-8")
    except InvalidToken:
        # 密钥不匹配或数据损坏：原样返回，避免执行器拿到空值导致假执行
        return stored


def is_encrypted(stored: object) -> bool:
    return isinstance(stored, str) and stored.startswith(_PREFIX)
