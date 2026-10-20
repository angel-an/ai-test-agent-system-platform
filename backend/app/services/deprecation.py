# -*- coding: utf-8 -*-
"""用例废弃标记（deprecated）的共享判定。

背景（2026-09-29 复核）：5 条 pre-rev95 游离用例被标记
``custom_fields.deprecated=true``（不删除）。若查询/执行层不过滤，会出现
"已废弃但仍被统计或执行"的问题。口径：**默认排除、显式可查**——
- 执行链路与指标统计：排除废弃用例（本模块被这些链路调用）；
- 默认列表/报告展示：保留可见（前端按标记展示"已废弃"角标，不改查询）。
"""

from __future__ import annotations

from typing import Any, Iterable


def is_deprecated(case: Any) -> bool:
    """custom_fields.deprecated == True（宽松判定：缺失字段 = 未废弃）。"""
    fields = getattr(case, "custom_fields", None) or {}
    try:
        return bool(fields.get("deprecated"))
    except AttributeError:
        return False


def filter_active(cases: Iterable[Any]) -> list[Any]:
    """排除已废弃用例（执行/统计链路的默认口径）。"""
    return [c for c in cases if not is_deprecated(c)]
