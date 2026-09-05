# -*- coding: utf-8 -*-
"""业务断言助手：查询余额/账户、执行充值/退款、信封断言。"""
from __future__ import annotations

import httpx

from endpoints import (
    ACCOUNT_PAGE_PATH,
    RECHARGE_PATH,
    account_page_payload,
    is_ok,
    recharge_payload,
)

DELTA_TOL = 0.011  # 金额断言容差（0.01 级操作，容忍 ±0.001 舍入 + 并行干扰）


def account_rows(client: httpx.Client, mobile: str) -> list[dict]:
    body = client.post(ACCOUNT_PAGE_PATH, json=account_page_payload(mobile=mobile)).json()
    assert is_ok(body), f"账户查询失败: {body.get('resultMsg')}"
    rows = (body.get("data") or {}).get("list") or []
    return [r for r in rows if str(r.get("phone")) == mobile]


def get_balance(client: httpx.Client, mobile: str) -> float:
    rows = account_rows(client, mobile)
    assert rows, f"未找到手机号 {mobile} 的储值账户"
    bal = rows[0].get("principalBalance")
    if bal is None:
        bal = rows[0].get("balance")
    return float(bal or 0.0)


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
    return abs(delta - expected) <= tol
