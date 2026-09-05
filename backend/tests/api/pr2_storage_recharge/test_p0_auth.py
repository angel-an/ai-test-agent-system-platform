from __future__ import annotations

import os
import uuid

import pytest

pytestmark = pytest.mark.skipif(
    _env_gate("LIVE_API") is False,
    reason="PR-2 API 骨架：需 LIVE_API=1（或兼容 PR2_LIVE_API=1）（并配置 access-token 或账号/验证码）才执行",
)


def test_p0_login_and_marketing_token_valid(marketing_client):
    """准入：取得营销云 access-token 且业务域可访问（org 信息查询）。"""
    r = marketing_client.get("/api/icdp/v1/org/getOrgInfo", params={"queryType": 1})
    body = r.json()
    assert r.status_code == 200
    assert str(body.get("resultCode")) == "0", body.get("resultMsg")
    assert (body.get("data") or []), "未返回组织信息"


@pytest.mark.skipif(_env_gate("AUTH_NEG") is False,
                    reason="AUTH_NEG=1（或兼容 PR2_AUTH_NEG=1）才执行错误密码负向（涉及验证码）")
def test_p0_bad_password_rejected():
    """准入负向：错误密码应被拒绝（HTTP 或 resultCode 非 0）。"""
    import httpx

    from endpoints import CONSOLE_GW, LOGIN_PATH, login_payload

    user = os.getenv("PR2_API_USER", "andan")
    with httpx.Client(base_url=CONSOLE_GW, timeout=60.0) as c:
        r = c.post(LOGIN_PATH, json=login_payload(user, "wrong-pass-xyz", "1234", "image" + str(uuid.uuid4()), "user"))
        body = r.json() if r.headers.get("content-type", "").startswith("application/json") else {}
        assert r.status_code >= 400 or str(body.get("resultCode")) != "0"
