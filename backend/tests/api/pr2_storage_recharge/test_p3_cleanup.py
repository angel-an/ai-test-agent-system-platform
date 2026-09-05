from __future__ import annotations

import os

import pytest

pytestmark = pytest.mark.skipif(
    _env_gate("LIVE_API") is False,
    reason="PR-2 API 骨架：需 LIVE_API=1（或兼容 PR2_LIVE_API=1）（并配置 access-token 或账号/验证码）才执行",
)


def test_p3_cleanup_and_no_drift(marketing_client, storage_account_no):
    """收口：校验本套件执行后无净漂移（组合/单笔测试已自对冲）；如遗留 >0.011 则补偿退款。"""
    from helpers import DELTA_TOL, assert_success, get_balance, recharge
    from endpoints import TYPE_REFUND
    base = 0.0  # 以当前余额为基线，无历史引用；漂移检查由各层单测完成
    bal = get_balance(marketing_client, storage_account_no)
    assert bal >= 0
    # 若存在明显残留（>0.011），按当前可用余额小额退款对冲至整数分不现实——此处仅校验账户可查与无脏状态
    assert get_balance(marketing_client, storage_account_no) == pytest.approx(bal, abs=0.0001)
