# -*- coding: utf-8 -*-
"""业务断言助手：查询余额/账户、执行充值/退款、信封断言。"""
from __future__ import annotations

import httpx
import os
import logging
from decimal import Decimal
from app.utils.consistency import ConsistencyPolicy, wait_for_consistency

from endpoints import (
    ACCOUNT_PAGE_PATH,
    RECHARGE_PATH,
    account_page_payload,
    is_ok,
    recharge_payload,
)

DELTA_TOL = Decimal("0")  # Money is compared exactly, never with a one-cent tolerance.


def account_rows(client: httpx.Client, mobile: str) -> list[dict]:
    body = client.post(ACCOUNT_PAGE_PATH, json=account_page_payload(mobile=mobile)).json()
    assert is_ok(body), f"账户查询失败: {body.get('resultMsg')}"
    rows = (body.get("data") or {}).get("list") or []
    return [r for r in rows if str(r.get("phone")) == mobile]


def get_balance(client: httpx.Client, mobile: str) -> Decimal:
    rows = account_rows(client, mobile)
    assert rows, f"未找到手机号 {mobile} 的储值账户"
    assert len(rows) == 1, "AMBIGUOUS_ACCOUNT: require exactly one matching account"
    field = os.getenv("PR2_BALANCE_FIELD")
    assert field, "Configure PR2_BALANCE_FIELD from the confirmed enterprise-account contract"
    assert field in rows[0] and rows[0][field] is not None, f"Missing balance field: {field}"
    value = Decimal(str(rows[0][field]))
    assert value.is_finite(), "Invalid monetary value"
    return value


def wait_balance(client, mobile, before, expected):
    target = before + Decimal(str(expected))
    result = wait_for_consistency(
        lambda: get_balance(client, mobile), lambda value: value == target,
        ConsistencyPolicy(float(os.getenv("PR2_CONSISTENCY_TIMEOUT", "10")),
                          float(os.getenv("PR2_POLL_INTERVAL", "1"))),
        observe=lambda sample: logging.getLogger(__name__).info("BALANCE_OBSERVATION %s", sample),
    )
    logging.getLogger(__name__).info("BALANCE_RESULT %s target=%s", result["status"], target)
    return result["value"]


def recharge(client: httpx.Client, type_: int, phones: list[str], amount: float) -> dict:
    resp = client.post(RECHARGE_PATH, json=recharge_payload(type_, phones, amount))
    assert resp.status_code == 200, f"HTTP {resp.status_code}"
    return resp.json()


def assert_success(body: dict, phones: list[str]) -> None:
    data = body.get("data") or {}
    success = data.get("successPhones") or []
    assert is_ok(body), f"业务失败: {body.get('resultMsg')} data={str(data)[:200]}"
    missing = [p for p in phones if p not in success]
    assert not missing, f"以下手机号未充值成功: {missing}；返回 success={success} error={data.get('errorPhones')}"


def delta_within(delta: float, expected: float, tol: float = DELTA_TOL) -> bool:
    return abs(Decimal(str(delta)) - Decimal(str(expected))) <= Decimal(str(tol))
