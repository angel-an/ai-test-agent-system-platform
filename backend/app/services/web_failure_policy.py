# -*- coding: utf-8 -*-
"""Web 测试失败的可重试性判定（flake 策略）。

背景：UAT 上真实观测到「执行环境类」偶发失败（浏览器/页面被关闭、登录页超时），
这类失败**不代表产品缺陷**，若直接登记缺陷会持续产出垃圾单；而"重试掩盖问题"同样
不可接受。因此策略是：

- 只对**执行环境类**失败做**有界**重试（默认 1 次，最多 2 次），未知错误一律不重试；
- 每次都记录运行记录与证据；重试成功时在 stdout/result_summary 留下 FLAKE_RETRY 标记，
  即"曾在第 1 次失败过"这件事必须可查，不允许伪装成一次通过；
- 缺陷登记只在**最后一次**尝试失败时进行（非最终尝试传 register_defect=False）。

职责边界（**2026-10-08 明确**）
----------------------------
本模块只回答两个问题：① 这次失败**值不值得重试**（flake 策略）；② 这次失败是不是
**环境阻断**（WAF/网关/IP 白名单，见 :func:`classify_environment_block`）。

本模块**不回答**"这次失败该不该登记缺陷单"——该问题只有一个权威实现：
:meth:`app.services.defect_decision_service.DefectDecisionService.decide`
（API / Web / 安全三条链路共用，且自带"非 SUT 失败规则表"与 SUT 侧证据门控）。

执行器侧的登记判断是**两道闸的组合**（见 ``executors.py`` 的 WebTestExecutor）：

1. 闸门一（本模块）：``classify_environment_block`` 命中 → 环境阻断 → **不登记**（也不重试）；
2. 闸门二（判定层）：未阻断时交给 ``decide()`` → 无 SUT 侧证据的非 SUT 失败 → **不登记**。

⚠️ 历史上本模块曾提供一个 ``should_register_defect()``，其语义仅是"非环境阻断"
（等价于闸门一的取反），命名却承诺回答"是否登记"，容易被误当作登记决策的入口而
**绕过闸门二**。该函数已于 2026-10-08 删除，不再提供：登记口径请改判定层，
不要在本模块重新实现第二份。
"""

from __future__ import annotations

import json
import os
import re
from typing import Any

#: 结构性/产品类失败：重试没有意义（先判定，优先级高于可重试模式）
NON_RETRYABLE_PATTERNS = [
    # 我方执行器的定位器硬失败（定位器失效属真问题，不该靠重试掩盖）
    re.compile(r"无法打开企业充值/退款表单"),
    re.compile(r"定位器可能失效"),
    # Playwright 断言失败 = 业务/规则不符
    re.compile(r"^\s*Error:\s*expect\(", re.MULTILINE),
    re.compile(r"编译断言执行受阻"),
    # 脚本自身问题
    re.compile(r"SyntaxError"),
    re.compile(r"Cannot\s+find\s+module", re.IGNORECASE),
    re.compile(r"MODULE_NOT_FOUND"),
    # 凭据/配置问题：重试不会变好
    re.compile(r"缺少运行时登录凭据"),
    re.compile(r"WEB_TEST_USERNAME"),
    re.compile(r"placeholder_base_url"),
]

#: 执行环境类失败：值得再试一次
RETRYABLE_PATTERNS = [
    re.compile(r"browser has been closed", re.IGNORECASE),
    re.compile(r"Target (page|closed)", re.IGNORECASE),
    re.compile(r"Execution context was destroyed", re.IGNORECASE),
    re.compile(r"Test timeout of \d+ms exceeded", re.IGNORECASE),
    re.compile(r"Timeout \d+ms exceeded", re.IGNORECASE),
    re.compile(r"Navigation (failed|timeout)", re.IGNORECASE),
    re.compile(r"net::ERR_(CONNECTION|TIMED_OUT|NETWORK|EMPTY_RESPONSE|NAME_NOT_RESOLVED)", re.IGNORECASE),
    re.compile(r"socket hang up", re.IGNORECASE),
    re.compile(r"ECONNREFUSED|ECONNRESET|ETIMEDOUT"),
    re.compile(r"browserType\.launch", re.IGNORECASE),
    re.compile(r"Protocol error", re.IGNORECASE),
    # 登录未完成（账号密码未通过或被页面清空）——**偶发**登录失败，值得再试一次。
    # 实测（2026-10-07）：TR-181 报「登录未完成（3 次尝试后仍停留在登录页…）」，
    # 紧接着的 TR-182 / TR-183 用**同一账号**连续通过（25.7s / 21.5s）⇒ 判定为偶发。
    # 注意：**真正的凭据缺失/配置错误仍不重试** —— `缺少运行时登录凭据`、`WEB_TEST_USERNAME`
    # 已列在 NON_RETRYABLE_PATTERNS，且该列表优先判定；此处只覆盖"页面上登录没走完"的抖动。
    # 重试次数仍受 flake_retry_budget 有界约束（默认 1 次、上限 2 次），并留下 FLAKE_RETRY 痕迹。
    re.compile(r"登录未完成"),
    re.compile(r"仍停留在登录页|未离开登录页"),
]

# ---------------------------------------------------------------------------
# 环境阻断（environment blocked）：不算产品缺陷、也不该重试
# ---------------------------------------------------------------------------
#: 我方脚本显式报出的环境阻断标记（登录页 HTTP 状态非 2xx/3xx 时由脚本抛出）
ENVIRONMENT_BLOCK_MARKER = "ENVIRONMENT_BLOCKED"

#: 环境阻断信号：网关/WAF/IP 白名单拦截，或登录页根本没渲染出来
ENVIRONMENT_BLOCKED_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("script_reported", re.compile(r"ENVIRONMENT_BLOCKED")),
    ("waf_forbidden", re.compile(r"\b403\b.*(Forbidden|WAF|ESA|拦截|禁止)|(Forbidden|WAF|ESA).{0,20}\b403\b", re.IGNORECASE)),
    ("waf_esa", re.compile(r"ESA\s*RuleID|RuleID\s*\d{6,}|http_custom", re.IGNORECASE)),
    ("waf_cookie", re.compile(r"acw_tc|cdn_sec_tc", re.IGNORECASE)),
    # 网关 5xx：**只认网关私有特征**（``orig response 5xx`` / ``X-Swift-Error``）。
    #
    # 2026-10-08 收紧：原口径还接受裸 ``Service Unavailable``，但那是**标准 HTTP
    # reason phrase** —— 真实 SUT 自身故障返回 503 时，文本与网关 503 完全一致，
    # **无法区分**。逐条实测（改动前）：
    #
    # ================================================  ==========  ================
    # 输入                                              判定        性质
    # ================================================  ==========  ================
    # ``503 X-Swift-Error: upstream``                   blocked     真阳性（网关）
    # ``503 orig response 5xx``                         blocked     真阳性（网关）
    # ``503 Service Unavailable``                        blocked     ❌ 误伤（标准短语）
    # ``HTTP 503 Service Unavailable``                   blocked     ❌ 误伤
    # ``Received: 503 Service Unavailable``              blocked     ❌ 误伤（断言消息）
    # ================================================  ==========  ================
    #
    # 误伤的后果是**不对称的**：被判阻断 → 两侧都不登记 ⇒ 真实 SUT 503 **被吞单**
    # （与"把缺陷写成没问题"同族：给错结论）。故取"宁可漏拦、不吞真缺陷"，
    # 与网关**不可区分**的形态一律不判阻断，交判定层
    # :meth:`DefectDecisionService.decide` 依 SUT 证据裁决。
    # 代价：网关自身 503 若**恰好**只带 reason phrase 会漏拦 → 走判定层，
    # 可能产出噪声单（少数、且人工易识别），属可接受的保守侧。
    ("gateway_5xx", re.compile(r"\b503\b.{0,40}(orig response 5xx|X-Swift-Error)", re.IGNORECASE)),
    ("access_denied", re.compile(r"Access Denied|请求被拦截|访问被拒绝", re.IGNORECASE)),
    # 登录页没渲染：WAF 拦截时 UAT 的典型表现（选择器等不到，而网络本身是通的）
    ("login_page_not_rendered", re.compile(r"waiting for locator\(['\"]#login_username['\"]\)", re.IGNORECASE)),
]


def classify_environment_block(
    error_message: str | None, stdout: str | None = None
) -> tuple[bool, str]:
    """判断失败是否属于**环境阻断**（WAF/网关/IP 白名单）。

    Returns:
        ``(blocked, reason_code)``；未命中返回 ``(False, "")``。

    背景（2026-09-24 实测）：UAT 域名被 WAF 拦截时返回 403，登录页不渲染，
    脚本只能报 `locator.waitFor: Timeout` —— 看起来像产品缺陷，
    会触发重试并**登记 IDP 缺陷单**。这类失败既不该重试（IP 拦截重试无用、还可能加重），
    也不该建单，必须被单独区分出来。
    """
    haystack = "\n".join(part for part in (error_message, stdout) if part)
    if not haystack:
        return False, ""
    for reason, pattern in ENVIRONMENT_BLOCKED_PATTERNS:
        if pattern.search(haystack):
            return True, reason
    return False, ""


def is_retryable_web_failure(error_message: str | None) -> bool:
    """执行环境类失败返回 True；结构性失败与未知错误返回 False。

    **环境阻断（WAF/网关拦截）不重试**：IP 级拦截重试没有意义，
    且连续自动登录可能加重拦截。
    """
    if not error_message:
        return False
    blocked, _ = classify_environment_block(error_message)
    if blocked:
        return False
    if any(p.search(error_message) for p in NON_RETRYABLE_PATTERNS):
        return False
    return any(p.search(error_message) for p in RETRYABLE_PATTERNS)


def flake_retry_budget(config: dict[str, Any] | None = None) -> int:
    """额外重试次数（不含首次执行）。默认 1 次，最多 2 次，可被 config/env 覆盖。

    与作业层重试的协同：``job_retry_count > 0`` 表示本次执行本身就是作业层重试
    （人工/接口触发），此时**不再**叠加执行器内的自动重试 —— 一次失败只应被一套机制
    计数一次，总尝试次数由作业层 ``max_retries`` 兜底。
    """
    if config and int(config.get("job_retry_count") or 0) > 0:
        return 0
    raw: Any = None
    if config and config.get("flake_retry") is not None:
        raw = config.get("flake_retry")
    else:
        raw = os.getenv("WEB_TEST_FLAKE_RETRY", "1")
    try:
        budget = int(raw)
    except (TypeError, ValueError):
        budget = 1
    return max(0, min(budget, 2))


def should_retry_web_attempt(result: dict[str, Any], attempt: int,
                             max_attempts: int) -> tuple[bool, str]:
    """是否应再试一次。返回 (是否重试, 原因)。"""
    if attempt >= max_attempts:
        return False, "已达重试上限"
    if result.get("status") != "failed":
        return False, "本次未失败"
    error = result.get("error_message") or result.get("error") or ""
    if not is_retryable_web_failure(error):
        return False, "非执行环境类失败，不重试"
    return True, f"执行环境类失败，进行第 {attempt + 1} 次尝试：{str(error)[:120]}"


def flake_retry_note(attempts: int, first_error: str | None) -> str:
    """重试留痕：让"曾失败过"可查，而不是伪装成一次通过。"""
    return "FLAKE_RETRY " + json.dumps(
        {"attempts": attempts, "first_error": str(first_error or "")[:200],
         "note": "首次为执行环境类失败，已重试；最终结论取自最后一次"},
        ensure_ascii=False,
    )


async def run_attempts_with_policy(run_once, *, max_attempts: int,
                                   should_stop=None) -> dict[str, Any]:
    """按策略执行若干次尝试，返回最后一次结果与尝试次数。

    重要：调用方在拿到结果后，**只要最终结果是失败就必须登记缺陷** —— 不能因为
    "本来打算重试"就把缺陷吞掉（真实教训：结构性失败不重试，但当时已按"非最终尝试"
    抑制了登记，导致失败彻底不可见）。

    参数：
        run_once: 无参可等待函数，执行一次尝试并返回 WebTestService 的 result。
        max_attempts: 总尝试次数上限（含首次）。
        should_stop: 可选无参函数，返回 True 时立刻停止（用于取消）。
    """
    attempts = 0
    first_error = ""
    result: dict[str, Any] = {}
    while True:
        attempts += 1
        result = await run_once()
        if should_stop is not None and should_stop():
            break

        retry, reason = should_retry_web_attempt(result, attempts, max_attempts)
        if not retry:
            break
        if not first_error:
            first_error = str(result.get("error_message") or result.get("error") or "")
        print(f"[WebFailurePolicy] {reason}")

    return {"result": result, "attempts": attempts, "first_error": first_error}


__all__ = [
    "ENVIRONMENT_BLOCK_MARKER",
    "ENVIRONMENT_BLOCKED_PATTERNS",
    "NON_RETRYABLE_PATTERNS",
    "RETRYABLE_PATTERNS",
    "classify_environment_block",
    "flake_retry_budget",
    "flake_retry_note",
    "is_retryable_web_failure",
    "run_attempts_with_policy",
    "should_retry_web_attempt",
]
