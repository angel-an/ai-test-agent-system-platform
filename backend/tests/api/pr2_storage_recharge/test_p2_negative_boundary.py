from __future__ import annotations

import os

import pytest

from endpoints import TYPE_RECHARGE, is_ok
from helpers import recharge

pytestmark = pytest.mark.skipif(
    _env_gate("LIVE_API") is False,
    reason="PR-2 API 骨架：需 LIVE_API=1（或兼容 PR2_LIVE_API=1）（并配置 access-token 或账号/验证码）才执行",
)


def test_p2_negative_non_member_rejected(marketing_client, non_member_no):
    """负向：非会员手机号充值应被拒绝（无 successPhones 命中）。"""
    body = recharge(marketing_client, TYPE_RECHARGE, [non_member_no], 0.01)
    data = body.get("data") or {}
    success = data.get("successPhones") or []
    noexist = data.get("noExistPhones") or []
    assert non_member_no not in success
    assert non_member_no in noexist or (body.get("errorPhones") or {}).get(non_member_no) is not None


@pytest.mark.skipif(_env_gate("RUN_RISKY_NEG") is False,
                    reason="大额/异常金额负向可能产生异常流水，需 RUN_RISKY_NEG=1（或兼容 PR2_RUN_RISKY_NEG=1）显式开启")
def test_p2_negative_amount_boundaries(marketing_client, storage_account_no):
    """金额边界负向：0 与 99999.01 应被业务拒绝（若系统放行将 FAIL=潜在缺陷标记）。"""
    for amount in (0, 99999.01):
        body = recharge(marketing_client, TYPE_RECHARGE, [storage_account_no], amount)
        data = body.get("data") or {}
        success = data.get("successPhones") or []
        rejected = not is_ok(body) or storage_account_no not in success
        assert rejected, f"金额 {amount} 被放行(潜在缺陷)，请确认后端校验"
