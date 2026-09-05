from __future__ import annotations

import io
import os
import time
import uuid

import httpx
import pytest
from openpyxl import Workbook

from endpoints import BATCH_IMPORT_PATH, OSS_POLICY_PATH, is_ok
from helpers import delta_within, get_balance

pytestmark = pytest.mark.skipif(
    _env_gate("LIVE_API") is False,
    reason="PR-2 API 骨架：需 LIVE_API=1（或兼容 PR2_LIVE_API=1）（并配置 access-token 或账号/验证码）才执行",
)


def test_p1_batch_import_recharge(marketing_client, storage_account_no):
    """批量导入（OSS POST policy 直传 → import/memberAccount type=0），延迟后余额回查 +0.01。"""
    before = get_balance(marketing_client, storage_account_no)

    wb = Workbook()
    ws = wb.active
    ws.append(["手机号", "充值金额", "备注"])
    ws.append([storage_account_no, 0.01, "api-batch"])
    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)

    policy = marketing_client.get(OSS_POLICY_PATH).json()
    assert is_ok(policy), policy.get("resultMsg")
    pd = policy.get("data") or {}
    for k in ("accessid", "policy", "signature", "dir", "host"):
        assert k in pd, f"OSS policy 缺失 {k}: {list(pd.keys())}"

    fname = "api_batch_" + uuid.uuid4().hex + ".xlsx"
    key = str(pd["dir"]) + fname
    files = {"file": (fname, buf.getvalue(),
                       "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")}
    data = {"key": key, "policy": pd["policy"], "OSSAccessKeyId": pd["accessid"],
            "signature": pd["signature"], "success_action_status": "200"}
    up = httpx.post(pd["host"], data=data, files=files, timeout=60, trust_env=False)
    assert up.status_code < 300, up.text[:200]

    public_host = pd.get("cdnHost") or "xysj-cdp-uat-mall.oss-cn-shanghai.aliyuncs.com"
    file_url = f"https://{public_host}/{key}"
    resp = marketing_client.get(BATCH_IMPORT_PATH, params={"fileUrl": file_url, "type": 0})
    body = resp.json()
    assert is_ok(body), body.get("resultMsg")
    print("BATCH_RESPONSE:", body)
    time.sleep(4)
    after = get_balance(marketing_client, storage_account_no)
    assert delta_within(after - before, 0.01), f"批量导入后余额增量异常: {after-before}"
