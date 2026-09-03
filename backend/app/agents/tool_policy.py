"""工具安全策略 —— 执行治理层 P0-1

借鉴 DeepSeek Harness guard 思想的两种纪律：
1. "看不到"优先于"拦得住"：黑名单工具在注册/代理时直接过滤，模型根本看不到；
2. 拒绝即终局：命中策略的调用返回 final 拒绝，不给模型"换个说法再试"的路径。

策略构成：
- DENIED_TOOLS 工具黑名单（browser_run_code_unsafe 为 RCE 等价工具，browser_evaluate 可执行任意 JS）
- 可配置额外禁用模式（env: MCP_DENY_TOOL_PATTERNS，逗号分隔子串）
- 导航目标校验（P0-1 复审修正，堵住非 HTTP(S) 绕过）：
    * scheme 门禁：导航工具默认只允许 http/https/about，file:/data:/javascript: 等一律拒绝
      （env: NAVIGATION_ALLOW_SCHEMES 可显式追加，如 "data,file"）；
    * origin 白名单：env: NAVIGATION_ORIGIN_ALLOWLIST（".example.com" 后缀 / host 精确 / loopback 放行）；
    * 未配置白名单时 http(s) 放行（过渡行为），启动时告警；
    * env: NAVIGATION_ORIGIN_ALLOWLIST_REQUIRED=1 时未配置白名单直接拒绝启动（fail-fast）。
"""

from __future__ import annotations

import logging
import os
import re
from urllib.parse import urlparse

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# 1) 工具黑名单（默认禁用）
# ---------------------------------------------------------------------------
DENIED_TOOLS: frozenset[str] = frozenset({
    # Playwright MCP：RCE 等价工具（官方文档明示 "RCE-equivalent"），必须禁
    "browser_run_code_unsafe",
    # 任意 JS 执行，等价于给模型一个浏览器内 shell，禁
    "browser_evaluate",
    # 阻塞/挂起类工具（沿用原 mcp_servers.py 的排除项）
    "browser_start_tracing",
    "browser_stop_tracing",
})


def _deny_patterns() -> tuple[str, ...]:
    """额外的禁用模式（env: MCP_DENY_TOOL_PATTERNS="run_code,code_unsafe"）。"""
    raw = os.getenv("MCP_DENY_TOOL_PATTERNS", "")
    return tuple(p.strip().lower() for p in raw.split(",") if p.strip())


def is_tool_denied(name: str | None) -> bool:
    """判断工具名是否命中黑名单（支持 'server/tool' 前缀形式）。"""
    if not name:
        return False
    n = name.strip().lower()
    base = n.rsplit("/", 1)[-1]
    if n in DENIED_TOOLS or base in DENIED_TOOLS:
        return True
    return any(p in n or p in base for p in _deny_patterns())


def filter_tools(tools: list) -> list:
    """过滤工具列表（注册/代理时调用）：黑名单工具直接移除（"看不到"原则）。"""
    kept: list = []
    dropped: list[str] = []
    for tool in tools:
        name = getattr(tool, "name", None) or getattr(tool, "key", None) or str(tool)
        if is_tool_denied(name):
            dropped.append(name)
        else:
            kept.append(tool)
    if dropped:
        logger.warning("[ToolPolicy] 过滤掉危险工具: %s", dropped)
    return kept


# ---------------------------------------------------------------------------
# 2) 导航目标校验：scheme 门禁 + origin 白名单
# ---------------------------------------------------------------------------
# 默认允许的导航 scheme：http/https（业务）
# 其余（file:/data:/javascript:/chrome: 等）一律拒绝，除非 NAVIGATION_ALLOW_SCHEMES 显式追加。
# about: 单独处理：仅精确放行 about:blank（P1 复审修正）。
ALLOWED_NAVIGATION_SCHEMES_DEFAULT: frozenset[str] = frozenset({"http", "https"})


def _allowed_navigation_schemes() -> frozenset[str]:
    raw = os.getenv("NAVIGATION_ALLOW_SCHEMES", "")
    extras = frozenset(s.strip().lower() for s in raw.split(",") if s.strip())
    return ALLOWED_NAVIGATION_SCHEMES_DEFAULT | extras


def _allowlist_entries() -> tuple[str, ...]:
    raw = os.getenv("NAVIGATION_ORIGIN_ALLOWLIST", "").strip()
    return tuple(e.strip().lower() for e in raw.split(",") if e.strip())


def origin_allowlist_enforced() -> bool:
    return bool(os.getenv("NAVIGATION_ORIGIN_ALLOWLIST", "").strip())


# loopback 始终放行
_LOOPBACK_HOSTS = {"localhost", "127.0.0.1", "::1", "[::1]"}


def check_navigation_origin(url: str | None) -> tuple[bool, str]:
    """校验 http(s) 导航目标的 origin。返回 (allowed, reason)。

    未配置白名单时放行（过渡行为）；仅对 http(s) 有意义。
    """
    if not origin_allowlist_enforced():
        return True, "allowlist 未配置（放行模式，生产建议配置 NAVIGATION_ORIGIN_ALLOWLIST）"
    if not url:
        return True, "无 URL 参数，跳过校验"
    try:
        parsed = urlparse(url)
    except ValueError:
        return False, f"URL 解析失败: {url!r}"
    if parsed.scheme not in ("http", "https"):
        # scheme 门禁由 check_navigation_target 负责；本函数只做 http(s) 的 origin 校验
        return True, "非 http(s) 目标，无 origin 概念"
    host = (parsed.hostname or "").lower()
    if host in _LOOPBACK_HOSTS:
        return True, "loopback 放行"
    for entry in _allowlist_entries():
        if entry.startswith("."):
            suffix = entry[1:]
            if host == suffix or host.endswith("." + suffix):
                return True, f"后缀匹配 .{suffix}"
        else:
            # 精确匹配：支持 host 或 host:port
            if host == entry or f"{host}:{parsed.port}" == entry:
                return True, f"精确匹配 {entry}"
    return False, f"origin '{host}' 不在导航白名单内（NAVIGATION_ORIGIN_ALLOWLIST）"


def _check_protocol_relative(parsed, url: str) -> tuple[bool, str]:
    """校验协议相对 URL（//host/path，P1 复审修正）。

    浏览器会把 //host/path 解析为"当前协议 + 该 host"的外部站点；
    白名单启用时按 host 校验，未启用时与 http(s) 一致放行（过渡）。
    """
    host = (parsed.hostname or "").lower()
    if not host:
        return False, f"协议相对 URL 无法解析 host: {url[:40]!r}"
    if host in _LOOPBACK_HOSTS:
        return True, "loopback 放行"
    if not origin_allowlist_enforced():
        return True, "协议相对 URL（allowlist 未配置，放行）"
    for entry in _allowlist_entries():
        if entry.startswith("."):
            suffix = entry[1:]
            if host == suffix or host.endswith("." + suffix):
                return True, f"后缀匹配 .{suffix}"
        else:
            if host == entry or f"{host}:{parsed.port}" == entry:
                return True, f"精确匹配 {entry}"
    return False, f"协议相对 URL 的 host '{host}' 不在导航白名单内"


def check_navigation_target(url: str | None) -> tuple[bool, str]:
    """校验导航目标：scheme 门禁 +（http(s)）origin 白名单 + 协议相对 URL。返回 (allowed, reason)。

    与 check_navigation_origin 的区别：本函数对非 http(s) scheme 默认拒绝，
    堵住 file:/data:/javascript: 等绕过路径（P0-1 复审修正）；
    //host/path 协议相对 URL 与 about:（仅 blank）单独处理（P1 复审修正）。
    """
    if not url:
        return True, "无 URL 参数，跳过校验"
    try:
        parsed = urlparse(url)
    except ValueError:
        return False, f"URL 解析失败: {url!r}"
    scheme = (parsed.scheme or "").lower()
    # 协议相对 URL（//host/path）
    if scheme == "" and url.lstrip().startswith("//"):
        return _check_protocol_relative(parsed, url)
    # about: 精确放行：scheme == "about" 且 path == "blank"，仅允许 fragment
    # （P1 复审修正：禁止前缀匹配——about:blankevil / about:blank?url= / about:blank/ 必须拒绝）
    if scheme == "about":
        if (parsed.path or "").lower() == "blank" and not parsed.query and not parsed.params:
            return True, "about:blank 放行"
        return False, f"about: scheme 仅允许精确 about:blank（收到 {url[:40]!r}）"
    if scheme not in _allowed_navigation_schemes():
        extra = os.getenv("NAVIGATION_ALLOW_SCHEMES", "").strip()
        return False, (
            f"导航 scheme '{scheme}' 被拒绝（默认仅允许 http/https"
            + (f"，已显式追加: {extra}" if extra else "")
            + "，可用 NAVIGATION_ALLOW_SCHEMES 显式放行）"
        )
    if scheme not in ("http", "https"):
        return True, f"安全 scheme '{scheme}' 放行（无 origin 概念）"
    # http(s)：走 origin 白名单
    return check_navigation_origin(url)


# ---------------------------------------------------------------------------
# 3) 导航类工具：调用前校验 URL 参数
# ---------------------------------------------------------------------------
NAVIGATION_TOOLS: frozenset[str] = frozenset({
    "browser_navigate",
    "browser_goto",
    "browser_open",
    "browser_new_page",
    "web_navigate",
    "web_open",
})

_URL_ARG_KEYS = ("url", "path", "target_url", "href")

# 绝对 URI / 协议相对 URL 识别：scheme:（http://、file:///、data:、javascript: 等）
# 或 //host/path（协议相对，P1 复审修正——浏览器会按当前协议解析到外部 host）
_URI_SCHEME_RE = re.compile(r"^(?:[a-zA-Z][a-zA-Z0-9+.\-]*:|//)")


def extract_navigation_url(tool_name: str, args: dict) -> str | None:
    """从工具调用参数中提取疑似导航 URI（任意 scheme，仅对导航类工具生效）。"""
    base = tool_name.rsplit("/", 1)[-1].lower()
    if base not in NAVIGATION_TOOLS:
        return None
    for key in _URL_ARG_KEYS:
        val = args.get(key)
        if isinstance(val, str):
            v = val.strip()
            if _URI_SCHEME_RE.match(v):
                return v
    return None


# ---------------------------------------------------------------------------
# 4) 启动期配置告警 / fail-fast（P1 复审修正）
# ---------------------------------------------------------------------------
_ORIGIN_ALLOWLIST_REQUIRED = os.getenv(
    "NAVIGATION_ORIGIN_ALLOWLIST_REQUIRED", ""
).strip().lower() in ("1", "true", "yes")

if _ORIGIN_ALLOWLIST_REQUIRED and not origin_allowlist_enforced():
    raise RuntimeError(
        "NAVIGATION_ORIGIN_ALLOWLIST_REQUIRED=1 但未配置 NAVIGATION_ORIGIN_ALLOWLIST："
        "导航 origin 校验无法生效，拒绝启动（fail-fast）。请先在 .env 配置白名单。"
    )

if not origin_allowlist_enforced():
    logger.warning(
        "[ToolPolicy] NAVIGATION_ORIGIN_ALLOWLIST 未配置：导航 origin 校验处于放行模式（过渡行为）。"
        "生产环境请配置白名单；需要强制生效可设 NAVIGATION_ORIGIN_ALLOWLIST_REQUIRED=1。"
    )
