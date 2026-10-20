from __future__ import annotations

import os

import pytest

# _env_gate 定义在同目录 conftest.py；conftest 中的函数不会注入测试模块命名空间，必须显式导入
from conftest import _env_gate

pytestmark = pytest.mark.skipif(
    _env_gate("LIVE_API") is False,
    reason="PR-2 API 骨架：需 LIVE_API=1（或兼容 PR2_LIVE_API=1）（并配置 access-token 或账号/验证码）才执行",
)


def test_p1_account_query_by_mobile(marketing_client, storage_account_no):
    """按手机号查询储值账户：应命中且余额为数值。"""
    from endpoints import ACCOUNT_PAGE_PATH, account_page_payload, is_ok
    body = marketing_client.post(ACCOUNT_PAGE_PATH, json=account_page_payload(mobile=storage_account_no)).json()
    assert is_ok(body), body.get("resultMsg")
    rows = [(r.get("list") or []) for r in [body.get("data") or {}]][0]
    hit = [r for r in rows if str(r.get("phone")) == storage_account_no]
    assert hit, f"未命中 {storage_account_no}"
    assert float(hit[0].get("principalBalance") or 0) >= 0
    assert hit[0].get("cardNo"), "缺少会员卡号"
