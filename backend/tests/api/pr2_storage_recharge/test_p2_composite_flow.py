from __future__ import annotations

import os

import pytest

pytestmark = pytest.mark.skipif(
    _env_gate("LIVE_API") is False,
    reason="PR-2 API 骨架：需 LIVE_API=1（或兼容 PR2_LIVE_API=1）（并配置 access-token 或账号/验证码）才执行",
)


def test_p2_composite_recharge_refund_flow(marketing_client, storage_account_no):
    """端到端：充值0.01 → 余额+0.01 → 退款0.01 → 余额回到基线（流水/余额联动）。"""
    from helpers import assert_success, delta_within, get_balance, recharge
    from endpoints import TYPE_RECHARGE, TYPE_REFUND
    base = get_balance(marketing_client, storage_account_no)
    r1 = recharge(marketing_client, TYPE_RECHARGE, [storage_account_no], 0.01)
    assert_success(r1, [storage_account_no])
    mid = get_balance(marketing_client, storage_account_no)
    assert delta_within(mid - base, 0.01)
    r2 = recharge(marketing_client, TYPE_REFUND, [storage_account_no], 0.01)
    assert_success(r2, [storage_account_no])
    end = get_balance(marketing_client, storage_account_no)
    assert delta_within(end - base, 0.0), f"未回到基线: {end-base}"
