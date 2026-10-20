"""API 响应信封字段解析（复核 F1 修复）。

## 为什么需要

平台生成的 API 脚本此前把「信封字段＝``resultCode``，且必须 === ``'0'``」**写死在基础模板**里，
于是把一个业务的响应结构套到了所有业务上。本机实测（2026-10-01）：

* ``APIEndpoint`` 共 1892 条、分布在 14 个项目：PR-1（1010）、PR-9（543）、PR-12（27）… PR-2 只有 17 条；
* 非 PR-2 项目在 ``responses`` 里声明的是 ``Code``（MX/Aigens 系），843 条导入契约里**没有一条**用 ``resultCode``；
* 结果：拿生成器渲染一个 MX 形态接口、对着 MX 的真实成功响应跑，脚本报
  ``响应信封缺少 resultCode 字段`` —— **SUT 没错，是平台判错了**。

## 解析规则（只读数据，代码里不含任何项目名）

1. ``endpoint.custom_config["response_envelope_field"]``：显式声明（最可靠，导入/用例侧可写）；
2. 该接口已导入的 ``responses`` 里**恰好**出现一个已知信封键（见 ``ENVELOPE_KEYS``）时用它；
3. 都拿不到 → 返回 ``None``：调用方降级为"只判 HTTP + 可解析"，并在脚本里输出
   ``ENVELOPE_CHECK_SKIPPED`` 机读标记（**不静默、也不硬失败**）。

第 3 条是刻意的：判不出可信结论时，既不猜、也不拿别的业务的形状去冤枉 SUT ——
与平台在 Web 定位器上的既有原则一致（不得跨业务套用别人的表）。
"""
from __future__ import annotations

from typing import Any

#: 平台已观测到的信封键（``resultCode``：数盈/小杨生煎系，字符串；``Code``：MX/Aigens 系，整数）。
#: 新增形态时在这里补一行即可，不需要动生成器。
ENVELOPE_KEYS: tuple[str, ...] = ("resultCode", "Code")

#: 接口级显式声明字段名（写在 ``APIEndpoint.custom_config`` 里）。
CUSTOM_CONFIG_KEY = "response_envelope_field"


def resolve_envelope_field(endpoint: Any) -> str | None:
    """解析该接口的响应信封字段名；返回 ``None`` 表示"未声明/判不出"。"""
    declared = _from_custom_config(getattr(endpoint, "custom_config", None))
    if declared:
        return declared
    return _from_declared_responses(getattr(endpoint, "responses", None))


def _from_custom_config(custom_config: Any) -> str | None:
    if not isinstance(custom_config, dict):
        return None
    value = custom_config.get(CUSTOM_CONFIG_KEY)
    if isinstance(value, str) and value.strip():
        return value.strip()
    return None


def _from_declared_responses(responses: Any) -> str | None:
    """从接口声明的响应结构里推断信封键。

    只在**恰好命中一个**已知键时返回；命中 0 个或 ≥2 个都返回 ``None``
    （歧义时宁可不判，也不要猜错把结论张冠李戴）。
    """
    if not responses:
        return None
    hits = [key for key in ENVELOPE_KEYS if _declares_key(responses, key, depth=0)]
    return hits[0] if len(hits) == 1 else None


def _declares_key(node: Any, key: str, depth: int) -> bool:
    """在响应结构里查该键：既认 ``{"Code": {...}}``，也认 ``{"name": "Code"}`` 这种字段清单写法。"""
    if depth > 12:
        return False
    if isinstance(node, dict):
        if key in node:
            return True
        if str(node.get("name") or "").strip() == key:
            return True
        return any(_declares_key(v, key, depth + 1) for v in node.values())
    if isinstance(node, (list, tuple)):
        return any(_declares_key(v, key, depth + 1) for v in node)
    return False
