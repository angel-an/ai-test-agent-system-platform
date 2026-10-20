# -*- coding: utf-8 -*-
"""把「编译清单」从 ``generation_params`` 里抽成可查询的资产列（N7）。

背景（2026-10-17 复核）：编译产物其实**有落库**，但埋在
``web_tests.generation_params.compiled_cases`` 这个 JSONB 里：

- 选执行范围时无法用 SQL 直接筛"可生成检查数"（要 ``->`` 逐层解 JSON，且缺失与 0 不可分）；
- 前端列表拿不到，只能打开资产逐个翻；
- 106 条旧 ``web_agent`` 资产根本没有该键，和"编译了但零检查"在数据上长得一样。

本模块只做一件事：给定 ``generation_params``，**确定地**推导出四个列值。
判定规则必须能区分四种状态，否则又会把"不知道"读成"零"：

=====================  ==========================================  ================
compile_status         含义                                          compiled_check_count
=====================  ==========================================  ================
``compiled``           编译产物存在且有可执行检查                      >0
``zero_checks``        编译产物存在、检查计划也生成了，但 0 条可执行      0
``check_plan_missing`` 编译产物存在，但没有检查计划（未配置定位器）     NULL（不知道）
``not_compiled``       没有编译产物（旧资产 / 编译前失败）              NULL（不知道）
=====================  ==========================================  ================

**NULL 与 0 的区分是本模块的核心**：``compiled_check_count = 0`` 只能由
"确实编译过、确实没有可执行检查"产生；旧资产一律 NULL，避免"一条 SQL 筛零检查"
把 106 条从没编译过的资产混进来冒充问题资产。
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

#: 编译产物存在且有可执行检查
COMPILE_STATUS_COMPILED = "compiled"
#: 编译产物存在、检查计划已生成，但零条可执行检查（定位器命中、断言全被跳过）
COMPILE_STATUS_ZERO_CHECKS = "zero_checks"
#: 编译产物存在，但没有检查计划（未配置定位器 / 模板未注册）→ 检查数未知
COMPILE_STATUS_PLAN_MISSING = "check_plan_missing"
#: 没有编译产物（旧资产或编译前失败）→ 检查数未知
COMPILE_STATUS_NOT_COMPILED = "not_compiled"

COMPILE_STATUSES: tuple[str, ...] = (
    COMPILE_STATUS_COMPILED,
    COMPILE_STATUS_ZERO_CHECKS,
    COMPILE_STATUS_PLAN_MISSING,
    COMPILE_STATUS_NOT_COMPILED,
)

#: ``compile_status`` 列宽（varchar），迁移里必须与之一致
COMPILE_STATUS_MAX_LENGTH = 24

#: 视为"历史资产"的生成者标识：这两类资产从未走过用例编译链，没有
#: ``generation_params.compiled_cases``，不能与"新链路编译失败"混为一谈。
LEGACY_GENERATORS: frozenset[str] = frozenset({"web_agent", "web-agent"})

#: ``compiled_cases`` 内承载检查数的键
CHECK_COUNT_KEY = "form_executable_checks"
SKIPPED_COUNT_KEY = "form_skipped_no_locator"


def _optional_int(value: Any) -> int | None:
    """把 JSON 里的计数字段转成 int；**缺失/null/不可解析一律返回 None**（不返回 0）。

    ``bool`` 要挡掉：JSON 的 ``true`` 在 Python 里是 ``int`` 的子类，
    直接 ``int(True)`` 会把"标记位"错读成"1 条检查"。
    """
    if value is None or isinstance(value, bool):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _normalize_generator(generated_by_agent: Any) -> str:
    return str(generated_by_agent or "").strip().lower()


def is_legacy_asset(generated_by_agent: Any) -> bool:
    """是否历史资产（``web_agent`` / ``web-agent`` 生成，从未经过用例编译链）。"""
    return _normalize_generator(generated_by_agent) in LEGACY_GENERATORS


def derive_compile_manifest(
    generation_params: Any,
    generated_by_agent: Any = None,
) -> dict[str, Any]:
    """由生成参数推导资产的编译清单列值（纯函数，无副作用、可单测）。

    返回键与 ``WebTest`` 上的列同名，调用方直接 ``setattr`` 即可。
    ``compiled_at`` 不在这里给：本函数无法知道"何时编译"，由调用方在**确有编译产物**时
    写入当前时刻；回填历史数据时由迁移脚本按 ``updated_at`` 近似。
    """
    params = generation_params if isinstance(generation_params, dict) else {}
    summary = params.get("compiled_cases")
    summary = summary if isinstance(summary, dict) else {}
    legacy = is_legacy_asset(generated_by_agent)

    if not summary:
        # 旧资产 / 编译前失败：产物不存在。计数保持 NULL，绝不填 0。
        return {
            "compile_status": COMPILE_STATUS_NOT_COMPILED,
            "compiled_check_count": None,
            "compiled_skipped_count": None,
            "is_legacy_asset": legacy,
        }

    checks = _optional_int(summary.get(CHECK_COUNT_KEY))
    skipped = _optional_int(summary.get(SKIPPED_COUNT_KEY))
    if checks is None:
        # 有产物但没生成检查计划（未配置定位器）→ "可生成检查数"未知。
        status = COMPILE_STATUS_PLAN_MISSING
    elif checks > 0:
        status = COMPILE_STATUS_COMPILED
    else:
        status = COMPILE_STATUS_ZERO_CHECKS

    return {
        "compile_status": status,
        "compiled_check_count": checks,
        "compiled_skipped_count": skipped,
        "is_legacy_asset": legacy,
    }


def apply_compile_manifest(
    web_test: Any,
    *,
    generation_params: Any = None,
    generated_by_agent: Any = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """把推导结果写到资产对象上（就地赋值，不 flush）。

    ``generation_params`` 缺省时读资产自身的；``generated_by_agent`` 同理。
    ``compiled_at`` 只在**本次确有编译产物**时刷新，否则保留原值——
    否则每次改资产名都会把"编译时刻"刷成当前时间，等于伪造血缘。
    """
    params = generation_params
    if params is None:
        params = getattr(web_test, "generation_params", None)
    agent = generated_by_agent
    if agent is None:
        agent = getattr(web_test, "generated_by_agent", None)

    derived = derive_compile_manifest(params, agent)
    for key, value in derived.items():
        setattr(web_test, key, value)

    has_manifest = bool(
        isinstance(params, dict) and isinstance(params.get("compiled_cases"), dict)
        and params.get("compiled_cases")
    )
    if has_manifest:
        web_test.compiled_at = now or datetime.now(timezone.utc)
    return derived


def compile_manifest_view(web_test: Any) -> dict[str, Any]:
    """列表/详情接口的对外视图（字段名与数据库列一致，另附一句人话解释）。

    解释文案属于**契约的一部分**：前端要把 NULL 与 0 分开显示，
    不能让用户把"未编译"读成"零检查"。
    """
    status = getattr(web_test, "compile_status", None)
    checks = getattr(web_test, "compiled_check_count", None)
    compiled_at = getattr(web_test, "compiled_at", None)
    return {
        "compile_status": status,
        "compiled_check_count": checks,
        "compiled_skipped_count": getattr(web_test, "compiled_skipped_count", None),
        "compiled_at": compiled_at.isoformat() if compiled_at else None,
        "is_legacy_asset": bool(getattr(web_test, "is_legacy_asset", False)),
        "compile_status_note": COMPILE_STATUS_NOTES.get(str(status or ""), "编译状态未知"),
    }


#: 状态解释（对外展示用；NULL 与 0 必须一眼可分）
COMPILE_STATUS_NOTES: dict[str, str] = {
    COMPILE_STATUS_COMPILED: "可生成可执行检查，执行时会注入表单校验",
    COMPILE_STATUS_ZERO_CHECKS: "已编译但零条可执行检查（断言被跳过，脚本退化为骨架）",
    COMPILE_STATUS_PLAN_MISSING: "已编译但未生成检查计划（未配置定位器），检查数未知",
    COMPILE_STATUS_NOT_COMPILED: "无编译产物（历史资产或编译前失败），检查数未知",
}
