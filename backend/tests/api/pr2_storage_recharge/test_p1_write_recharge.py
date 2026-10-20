from __future__ import annotations

import os

import pytest

# _env_gate 定义在同目录 conftest.py；conftest 中的函数不会注入测试模块命名空间，必须显式导入
from conftest import _env_gate

pytestmark = pytest.mark.skipif(
    _env_gate("LIVE_API") is False,
    reason="PR-2 API 骨架：需 LIVE_API=1（或兼容 PR2_LIVE_API=1）（并配置 access-token 或账号/验证码）才执行",
)


def test_p1_write_recharge_and_balance(marketing_client, storage_account_no):
    """手动企业储值充值 0.01：成功且账户余额增加≈0.01。"""
    from helpers import assert_success, delta_within, get_balance, wait_balance, recharge
    from endpoints import TYPE_RECHARGE
    before = get_balance(marketing_client, storage_account_no)
    body = recharge(marketing_client, TYPE_RECHARGE, [storage_account_no], 0.01)
    assert_success(body, [storage_account_no])
    after = wait_balance(marketing_client, storage_account_no, before, 0.01)
    assert delta_within(after - before, 0.01), f"余额增量异常: {after-before}"
