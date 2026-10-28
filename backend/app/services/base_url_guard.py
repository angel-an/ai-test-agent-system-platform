"""base_url 覆盖防呆。

背景（2026-10-28 实测）：``POST /api-tests/{id}/run`` 的 ``execution_config.base_url``
是**整体覆盖**语义。调用方按"换个环境"的直觉传域级地址（``https://host``），
而用例配置里存的是带网关段的完整前缀（``https://host/yundt-saas-gateway``）。
覆盖之后网关段被吃掉，请求打到不存在的路径，网关返回**空 body 404**；
断言层只看到"HTTP 4xx"，于是被判成被测系统的缺陷并自动登记工单
（ATR-20261028-004 → IDP ``jf-20260122392-01-3229``，事后核实为纯假单）。

这类失败最危险的地方是**看起来跑通了**：脚本正常执行、断言正常失败、证据正常落库，
唯独目标是错的。所以这里不做"告警继续"，而是默认拒绝，需要覆盖时必须显式声明。
"""

from typing import Optional
from urllib.parse import urlsplit

#: 显式强制覆盖的开关名（放在 execution_config 里）。
ALLOW_BASE_URL_OVERRIDE_KEY = "allow_base_url_override"

_DEFAULT_PORTS = {"http": 80, "https": 443}


def _origin(url: str) -> tuple[str, str, Optional[int]]:
    parts = urlsplit(url)
    scheme = (parts.scheme or "").lower()
    host = (parts.hostname or "").lower()
    port = parts.port or _DEFAULT_PORTS.get(scheme)
    return scheme, host, port


def _normalize(url: str) -> str:
    return (url or "").strip().rstrip("/")


def check_base_url_override(
    configured: Optional[str],
    override: Optional[str],
    *,
    allow: bool = False,
) -> Optional[str]:
    """校验本次执行的 base_url 覆盖是否安全。

    Returns:
        None 表示放行；否则返回可直接展示给调用方的中文原因。
    """
    cfg = _normalize(configured or "")
    ov = _normalize(override or "")

    # 没传覆盖、或与配置完全一致：无风险
    if not ov or ov == cfg:
        return None
    # 配置里本来就没有 base_url：没有"被吃掉的前缀"可丢，交给执行期兜底
    if not cfg:
        return None
    # 显式声明强制覆盖
    if allow:
        return None

    cfg_scheme, cfg_host, cfg_port = _origin(cfg)
    ov_scheme, ov_host, ov_port = _origin(ov)

    # 解析不出 host（相对路径、占位符等）：不阻断合法自定义值
    if not cfg_host or not ov_host:
        return None

    cfg_path = urlsplit(cfg).path.rstrip("/")
    ov_path = urlsplit(ov).path.rstrip("/")

    if (ov_scheme, ov_host, ov_port) != (cfg_scheme, cfg_host, cfg_port):
        return (
            f"base_url 覆盖指向了不同目标：用例配置={cfg}，本次执行={ov}。"
            "若确需切换环境执行，请在 execution_config 里显式传 "
            f"{ALLOW_BASE_URL_OVERRIDE_KEY}=true。"
        )

    # 同源但路径变短 ⇒ 覆盖值把配置里的路径前缀吃掉了，请求必然打到不存在的路径
    if cfg_path.startswith(ov_path):
        lost = cfg_path[len(ov_path):].rstrip("/") or cfg_path
        return (
            f"base_url 覆盖会丢失用例配置里的路径前缀「{lost or cfg_path}」："
            f"用例配置={cfg}，本次执行={ov}。"
            "按此地址执行，请求会打到网关上不存在的路径并返回空响应，"
            "容易被误判为被测系统缺陷。若确实需要覆盖，请在 execution_config 里显式传 "
            f"{ALLOW_BASE_URL_OVERRIDE_KEY}=true。"
        )

    return None
