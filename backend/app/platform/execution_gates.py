# -*- coding: utf-8 -*-
"""P0-4: 统一执行门控 — 默认安全、显式 live、风险负向独立控制。

目标：确保默认安全回归，无环境变量时默认 skip，
live 与风险项必须显式开启。

环境变量约定：
- LIVE_API / LIVE_WEB: 启用 live 执行（默认 0=skip）
- RUN_RISKY_NEG: 启用风险负向用例（默认 0=skip）
- AUTH_NEG: 启用鉴权负向测试（默认 0=skip）
- FAIL_CLOSED: 执行失败时终局拒绝（默认 0=降级）
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Optional


# ---------------------------------------------------------------------------
# 门控配置
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class GateConfig:
    """执行门控配置 — 不可变，从环境变量或显式参数构造。"""

    live_api: bool = False
    live_web: bool = False
    run_risky_negative: bool = False
    auth_negative: bool = False
    fail_closed: bool = False

    @classmethod
    def from_env(cls) -> "GateConfig":
        """从环境变量读取配置（默认全部关闭）。

        标准命名：LIVE_API / LIVE_WEB / RUN_RISKY_NEG / AUTH_NEG / FAIL_CLOSED
        """
        return cls(
            live_api=os.getenv("LIVE_API", "0") == "1",
            live_web=os.getenv("LIVE_WEB", "0") == "1",
            run_risky_negative=os.getenv("RUN_RISKY_NEG", "0") == "1",
            auth_negative=os.getenv("AUTH_NEG", "0") == "1",
            fail_closed=os.getenv("FAIL_CLOSED", "0") == "1",
        )

    def any_live(self) -> bool:
        """是否有任何 live 开关开启"""
        return self.live_api or self.live_web

    def is_safe_mode(self) -> bool:
        """是否为安全模式（无任何 live）"""
        return not self.any_live()


# ---------------------------------------------------------------------------
# 门控判断函数
# ---------------------------------------------------------------------------

def should_skip_live(config: Optional[GateConfig] = None) -> bool:
    """是否应该跳过 live 执行（默认安全：无环境变量时返回 True）。"""
    cfg = config or GateConfig.from_env()
    return not cfg.any_live()


def should_run_risky(config: Optional[GateConfig] = None) -> bool:
    """风险负向用例是否可执行（必须显式开启）。"""
    cfg = config or GateConfig.from_env()
    return cfg.run_risky_negative


def should_run_auth_negative(config: Optional[GateConfig] = None) -> bool:
    """鉴权负向测试是否可执行（必须显式开启）。"""
    cfg = config or GateConfig.from_env()
    return cfg.auth_negative


def check_live_gate(
    track: str,  # "api" | "web"
    config: Optional[GateConfig] = None,
) -> tuple[bool, Optional[str]]:
    """检查 live 门控。

    Returns:
        (allowed, reason): allowed=True 表示可通过；reason 为拒绝原因。
    """
    cfg = config or GateConfig.from_env()
    if track == "api" and not cfg.live_api:
        return False, "LIVE_API 未开启（默认安全模式，跳过 live API 执行）"
    if track == "web" and not cfg.live_web:
        return False, "LIVE_WEB 未开启（默认安全模式，跳过 live Web 执行）"
    return True, None


def check_risky_gate(
    case_keyword: Optional[str] = None,
    config: Optional[GateConfig] = None,
) -> tuple[bool, Optional[str]]:
    """检查风险负向门控。

    当用例关键词为反向/边界时，需显式开启 RUN_RISKY_NEG。
    """
    cfg = config or GateConfig.from_env()
    if case_keyword in ("反向", "边界", "negative", "boundary"):
        if not cfg.run_risky_negative:
            return False, f"用例关键词为 '{case_keyword}'，需显式开启 RUN_RISKY_NEG=1"
    return True, None


# ---------------------------------------------------------------------------
# 授权门控（复用 script_provenance）
# ---------------------------------------------------------------------------

async def check_auth_gate(
    session,
    project_identifier: str,
    script_hash: str,
) -> tuple[bool, str]:
    """来源授权检查 — 复用 script_provenance.check_script_registered。

    Returns:
        (authorized, reason): authorized=True 表示授权通过。
    """
    from app.agents.tools.web.script_provenance import check_script_registered

    ok, reason = await check_script_registered(session, project_identifier, script_hash)
    return ok, reason


# ---------------------------------------------------------------------------
# 统一门控入口
# ---------------------------------------------------------------------------

class ExecutionGate:
    """统一执行门控入口 — 组合所有门控检查。"""

    def __init__(self, config: Optional[GateConfig] = None):
        self.config = config or GateConfig.from_env()

    def check_all(
        self,
        track: str,
        case_keyword: Optional[str] = None,
    ) -> tuple[bool, list[str]]:
        """执行全部门控检查。

        Returns:
            (allowed, reasons): allowed=True 表示全部通过；reasons 为所有拒绝原因。
        """
        reasons: list[str] = []

        # 1. Live 门控
        allowed, reason = check_live_gate(track, self.config)
        if not allowed:
            reasons.append(reason or "")

        # 2. 风险负向门控
        allowed, reason = check_risky_gate(case_keyword, self.config)
        if not allowed:
            reasons.append(reason or "")

        return len(reasons) == 0, reasons

    def require_live(self, track: str) -> None:
        """要求 live 开启，否则抛出 RuntimeError（fail-closed 语义）。"""
        allowed, reason = check_live_gate(track, self.config)
        if not allowed:
            if self.config.fail_closed:
                raise RuntimeError(f"[fail-closed] {reason}")
            # 降级：不抛异常，但记录原因（由调用方处理）

    def is_safe_regression(self) -> bool:
        """是否为安全回归模式（无 live，只读/本地执行）"""
        return self.config.is_safe_mode()


# ---------------------------------------------------------------------------
# 便捷函数
# ---------------------------------------------------------------------------

def get_gate_status() -> dict[str, bool]:
    """获取当前门控状态（用于报告和调试）。"""
    cfg = GateConfig.from_env()
    return {
        "live_api": cfg.live_api,
        "live_web": cfg.live_web,
        "run_risky_negative": cfg.run_risky_negative,
        "auth_negative": cfg.auth_negative,
        "fail_closed": cfg.fail_closed,
        "safe_mode": cfg.is_safe_mode(),
    }
