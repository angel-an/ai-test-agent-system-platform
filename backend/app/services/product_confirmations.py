# -*- coding: utf-8 -*-
"""产品确认结论登记（确认回填）。

**为什么需要这个模块**：生成链路只按**文档原文**判定歧义。一旦产品对某条歧义给出结论，
这个结论必须被记住，否则每次重新生成都会把**已裁决**的项重新标成"待确认"——
人工要重复清一遍，门禁噪音也永远降不下来。

登记内容（每条）：
- ``document``：文档名，必须与生成时的 filename 完全一致；
- ``ambiguity_match``：命中该歧义理由的正则（作用在生成期判定的 ambiguity_reason 上）；
- ``case_match``：命中**具体用例**的正则（同一歧义理由可能覆盖多条用例，
  已确认的只应放行其中被裁决的那条，其余保持待确认）；
- ``conclusion``：产品结论**原文照录**（不得改写、不得补充业务规则）；
- ``step_result``：确认后用例的**可判定预期**（写进最后一步的结果）；
- ``confirmed_by`` / ``confirmed_at`` / ``source``：谁、何时、经由什么渠道确认。

纪律：本文件只登记**产品已经给出的结论**。没有登记 = 仍然待确认（不猜、不默认放行）。
"""

from __future__ import annotations

import re
from typing import Any

#: 已确认项登记表
CONFIRMATIONS: list[dict[str, Any]] = [
    {
        "key": "PR2-EVAL-ENTRY-DELAY",
        "document": "小杨生煎-订单评价(问卷调查)功能 (1).pdf",
        "ambiguity_match": r"正文30分钟与批注20分钟不一致",
        # 该歧义理由同时覆盖「评价入口」与「订阅消息推送」两条用例；
        # 产品本轮只裁决了评价入口那条（Q-09），订阅消息那条（Q-10）仍待确认。
        "case_match": r"訂單詳情|订单详情|啟用後的問卷|启用后的问卷",
        "conclusion": (
            "以正文 30min 为准；默认 30min，支持配置修改；"
            "配置前端实现，无配置功能"
        ),
        # Q-09a 追加确认（2026-10-14）：配置落点 = 前端配置文件/常量，无配置界面
        "config_landing": "前端配置文件/常量，无配置界面（改动配置由前端发版完成）",
        "step_result": (
            "按正文 30min 核对：前端配置默认 30min（配置文件/常量，无配置界面），"
            "支付完成满 30min 后订单详情出现「评价」入口；订单已取消时不出现该入口"
        ),
        "confirmed_by": "产品（用户转达）",
        "confirmed_at": "2026-10-14",
        "source": "《待产品确认清单-20261014.md》Q-09 + Q-09a 回填",
        "open_questions": [],
    },
]


def find_confirmation(
    document: str | None,
    ambiguity_reason: str | None,
    case_text: str | None = None,
) -> dict[str, Any] | None:
    """按「文档 + 歧义理由 + 用例文本」查已确认结论；查不到返回 ``None``（继续待确认）。"""
    for item in CONFIRMATIONS:
        doc = item.get("document")
        if doc and doc != (document or ""):
            continue
        if not re.search(item["ambiguity_match"], ambiguity_reason or ""):
            continue
        case_match = item.get("case_match")
        if case_match and not re.search(case_match, case_text or ""):
            continue
        return dict(item)
    return None


def confirmation_summary() -> list[dict[str, Any]]:
    """登记表摘要（供报告/清单核对用）。"""
    return [
        {
            "key": item["key"], "document": item["document"],
            "conclusion": item["conclusion"],
            "config_landing": item.get("config_landing"),
            "confirmed_by": item.get("confirmed_by"), "confirmed_at": item.get("confirmed_at"),
            "source": item.get("source"), "open_questions": item.get("open_questions") or [],
        }
        for item in CONFIRMATIONS
    ]


__all__ = ["CONFIRMATIONS", "find_confirmation", "confirmation_summary"]
