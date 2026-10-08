# -*- coding: utf-8 -*-
"""Web 测试执行前的"预检"（preflight）：把停机原因变成**机器可读的码**。

动机：此前执行前的拒绝只有自由文本（「脚本来源授权拒绝：…」「缺少运行时登录凭据…」），
UI/调用方无法据此给出确定的修复指引，也无法区分"没绑定子功能"和"脚本没登记"。

约定：预检失败时返回 ``{"code": ..., "message": ..., "hint": ...}``，
``code`` 取以下四个稳定值之一（另有兜底码），``message`` 保留原始细节，``hint`` 给出修复方向。
预检**只拦"注定失败"的运行**，不放过任何本来能跑的场景：
凭据判定与脚本自身 ``test.skip(!USERNAME||!PASSWORD)`` 的判据保持一致（含进程环境变量）。
"""

from __future__ import annotations

import os
import re
from typing import Any, Mapping, Optional

#: 未绑定子功能（三要素绑定不成立）
CODE_SUB_FUNCTION_UNBOUND = "sub_function_unbound"
#: 脚本未登记 / 哈希不是 effective（三要素里的"当前脚本附件 + 内容哈希"不成立）
CODE_SCRIPT_NOT_REGISTERED = "script_not_registered"
#: 缺少运行时登录凭据
CODE_MISSING_CREDENTIALS = "missing_credentials"
#: base_url 仍是占位地址
CODE_PLACEHOLDER_BASE_URL = "placeholder_base_url"
#: 兜底的授权拒绝码（能识别具体原因时不用它）
CODE_PROVENANCE_REJECTED = "script_provenance_rejected"

ALL_CODES = (
    CODE_SUB_FUNCTION_UNBOUND,
    CODE_SCRIPT_NOT_REGISTERED,
    CODE_MISSING_CREDENTIALS,
    CODE_PLACEHOLDER_BASE_URL,
    CODE_PROVENANCE_REJECTED,
)

#: 占位/不可达域名（与 web_case_link_service 的约定保持一致，并覆盖常见占位写法）
PLACEHOLDER_HOST_MARKERS = (
    "example.invalid",
    "example.com",
    "your-domain",
    "yourdomain",
    "todo.invalid",
    "test.invalid",
    "localhost.localdomain",
)

CREDENTIAL_KEYS = ("WEB_TEST_USERNAME", "WEB_TEST_PASSWORD")


def login_profile_key(test_config: Any) -> str:
    """test_config.login_profile → 环境变量前缀（与执行器注入逻辑一致）。"""
    profile = (test_config or {}).get("login_profile") if hasattr(test_config, "get") else None
    profile = profile or "WEB_TEST_DEFAULT"
    return re.sub(r"[^A-Z0-9_]", "_", str(profile).upper())


def resolve_runtime_credentials(
    execution_config: Any = None,
    test_config: Any = None,
    environ: Optional[Mapping[str, str]] = None,
) -> tuple[Optional[str], Optional[str]]:
    """解析运行期登录凭据（**单一事实来源**，执行器与预检共用）。

    取值顺序与执行器保持一致：
    1. execution_config["env"] / ["environment_variables"]（显式注入，优先）；
    2. ``{login_profile}_USERNAME/_PASSWORD``（来自进程环境）；
    3. ``WEB_TEST_USERNAME/WEB_TEST_PASSWORD``（来自进程环境）。
    """
    env_lookup = os.environ if environ is None else environ
    config = execution_config if hasattr(execution_config, "get") else {}
    env = dict(config.get("env") or config.get("environment_variables") or {})
    profile_key = login_profile_key(test_config)

    username = (
        env.get("WEB_TEST_USERNAME")
        or env_lookup.get(f"{profile_key}_USERNAME")
        or env_lookup.get("WEB_TEST_USERNAME")
    )
    password = (
        env.get("WEB_TEST_PASSWORD")
        or env_lookup.get(f"{profile_key}_PASSWORD")
        or env_lookup.get("WEB_TEST_PASSWORD")
    )
    return (str(username) if username else None, str(password) if password else None)

#: 脚本登记/授权失败原因 → 预检码（按关键词判定；命中不了则用兜底码）
_PROVENANCE_CODE_RULES: tuple[tuple[tuple[str, ...], str], ...] = (
    (("脚本附件不存在", "无当前脚本附件", "解析子功能", "解析脚本附件"), CODE_SCRIPT_NOT_REGISTERED),
    (("未登记", "未注册", "not registered", "哈希", "hash"), CODE_SCRIPT_NOT_REGISTERED),
    (("要求 sub_function", "sub_function_id", "子功能"), CODE_SUB_FUNCTION_UNBOUND),
)


def build_preflight(code: str, message: str, hint: str = "") -> dict[str, str]:
    """构造预检结果（code 稳定，message 保留细节，hint 给修复方向）。"""
    payload = {"code": code, "message": message}
    if hint:
        payload["hint"] = hint
    return payload


def is_placeholder_base_url(url: Optional[str]) -> bool:
    """base_url 是否仍是占位/示例地址（用于提前拦截注定失败的运行）。"""
    if not url or not isinstance(url, str):
        return True  # 空 = 没配，同样不该启动浏览器
    lowered = url.strip().lower()
    if not lowered:
        return True
    return any(marker in lowered for marker in PLACEHOLDER_HOST_MARKERS)


def _credentials_from(source: Any) -> tuple[Optional[str], Optional[str]]:
    if not source:
        return None, None
    try:
        getter = source.get
    except AttributeError:
        return None, None
    user = getter(CREDENTIAL_KEYS[0])
    password = getter(CREDENTIAL_KEYS[1])
    return (str(user) if user else None, str(password) if password else None)


def has_runtime_credentials(
    execution_config: Any = None,
    test_config: Any = None,
    environ: Optional[Mapping[str, str]] = None,
) -> bool:
    """是否具备运行期登录凭据（判据与执行器注入环境变量时一致）。"""
    username, password = resolve_runtime_credentials(execution_config, test_config, environ)
    return bool(username and password)


def classify_provenance_reason(reason: str) -> str:
    """把授权门的原因文本归类成预检码（归类不了时返回兜底码）。"""
    text = str(reason or "")
    lowered = text.lower()
    for keywords, code in _PROVENANCE_CODE_RULES:
        for keyword in keywords:
            if keyword.lower() in lowered:
                return code
    return CODE_PROVENANCE_REJECTED


def preflight_provenance_reason(reason: str) -> dict[str, str]:
    """授权门拒绝 → 预检结果（消息里保留原始原因，不丢细节）。"""
    code = classify_provenance_reason(reason)
    hint = {
        CODE_SCRIPT_NOT_REGISTERED:
            "请先保存/登记该 WebTest 的脚本（save_web_test_script），使"
            "「项目+子功能当前附件+内容哈希」三要素成立后再执行。",
        CODE_SUB_FUNCTION_UNBOUND: "请先把该 WebTest 绑定到真实的子功能（sub_function_id）。",
        CODE_PROVENANCE_REJECTED: "请检查脚本归属、子功能绑定与登记状态。",
    }.get(code, "")
    return build_preflight(code, f"脚本来源授权拒绝（三要素绑定不成立）: {reason}", hint)


def check_web_preflight(
    *,
    sub_function_id: Any,
    base_url: Optional[str],
    execution_config: Any = None,
    test_config: Any = None,
    environ: Optional[Mapping[str, str]] = None,
) -> Optional[dict[str, str]]:
    """执行前的可判定的预检项（返回 None 表示通过）。

    注意：脚本登记/哈希校验需要访问数据库与脚本内容，仍由授权门负责（其结果经
    ``preflight_provenance_reason`` 归类）。
    """
    if not sub_function_id:
        return build_preflight(
            CODE_SUB_FUNCTION_UNBOUND,
            "Web 测试未绑定子功能（sub_function_id 为空），无法建立三要素绑定"
            "（项目+附件+哈希），拒绝执行。",
            "请先把该 WebTest 绑定到真实子功能后再执行。",
        )
    if is_placeholder_base_url(base_url):
        return build_preflight(
            CODE_PLACEHOLDER_BASE_URL,
            f"base_url 仍是占位/未配置地址（{base_url!r}），拒绝执行以免产生误导性结果。",
            "请配置真实的测试入口 URL（或在创建时传入 base_url）。",
        )
    if not has_runtime_credentials(execution_config, test_config, environ):
        return build_preflight(
            CODE_MISSING_CREDENTIALS,
            "缺少运行时登录凭据（WEB_TEST_USERNAME/WEB_TEST_PASSWORD 或"
            " {login_profile}_USERNAME/_PASSWORD），脚本会直接跳过登录，执行结果没有意义。",
            "请在执行环境变量中配置登录凭据，或设置 WebTest 的 login_profile。",
        )
    return None


__all__ = [
    "ALL_CODES",
    "CODE_MISSING_CREDENTIALS",
    "CODE_PLACEHOLDER_BASE_URL",
    "CODE_PROVENANCE_REJECTED",
    "CODE_SCRIPT_NOT_REGISTERED",
    "CODE_SUB_FUNCTION_UNBOUND",
    "build_preflight",
    "check_web_preflight",
    "classify_provenance_reason",
    "has_runtime_credentials",
    "is_placeholder_base_url",
    "login_profile_key",
    "preflight_provenance_reason",
    "resolve_runtime_credentials",
]
