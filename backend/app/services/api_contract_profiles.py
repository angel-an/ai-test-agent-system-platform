"""来源级 API 契约配置。

仅声明已知规则；未确认的真实通道不会被默认套用 CRM 规则。
"""
from __future__ import annotations

from typing import Any

CONTRACT_PROFILES: dict[str, dict[str, Any]] = {
    "crm_non_pos": {
        "timestamp": "yyyyMMddHHmmss",
        "sign_body": "canonical_json",
        "required_headers": ("appid", "sign", "ts"),
        "confirmed": True,
    },
    "aigens": {
        "timestamp": "milliseconds",
        "sign_body": "raw_body",
        "required_headers": ("model", "method", "request-channel"),
        "confirmed": False,
    },
}


def get_contract_profile(source: str | None, channel: str | None = None) -> dict[str, Any] | None:
    """返回契约副本；aigens 在业务确认前保持 pending。"""
    key = (channel or source or "").strip().lower()
    profile = CONTRACT_PROFILES.get(key)
    return dict(profile) if profile else None


def validate_contract_for_execution(config: dict[str, Any]) -> list[str]:
    """执行前验证来源契约是否已确认。"""
    if not config.get("requires_env"):
        return []
    # 兼容历史调用方：未声明来源契约时只走原有环境门禁。
    if not (config.get("source_type") or config.get("channel")):
        return []
    profile = get_contract_profile(config.get("source_type"), config.get("channel"))
    if profile is None:
        return ["contract_profile"]
    if not profile.get("confirmed"):
        return ["contract_confirmation"]
    return []
