# -*- coding: utf-8 -*-
"""PR-2 储值充值 SUT API 映射（抓包确认，2026-09-04）。

统一信封：HTTP 200 + body {"resultCode":"0","resultMsg":"success","data":...}；业务失败 resultCode!='0'。
鉴权头：access-token: <marketing token>（来源：ssouser/login → panelInstanceToken → changeToken 换 imarketing 实例 token）。
余额/流水断言位：memberCard/account/page 返回 data.list[].balance / principalBalance（与充值/退款联动）。
"""
import base64
import os
from typing import Any

CONSOLE_GW = os.getenv("PR2_GW_CONSOLE", "https://console-uat.xysjg.com/yundt-saas-gateway")
MARKETING_GW = os.getenv("PR2_GW_MARKETING", "https://imarketing-uat.xysjg.com/yundt-saas-gateway")
TENANT_ID = os.getenv("PR2_TENANT_ID", "1323425265700000001")
MKT_INSTANCE_ID = os.getenv("PR2_MKT_INSTANCE", "1323425265910000011")

# auth
LOGIN_PATH = "/api/user/v1/users/ssouser/login"
CHANGE_TOKEN_PATH = "/api/user/v1/users/ssouser/changeToken"
VERIFY_IMG_PATH = "/api/icdp/v1/users/verify/img"

# business (icdp)
ACCOUNT_PAGE_PATH = "/api/icdp/v1/memberCard/account/page"
RECHARGE_PATH = "/api/icdp/v1/recharge/reSetRecharge"
BATCH_IMPORT_PATH = "/api/icdp/v1/memberInfoBestore/import/memberAccount"
OSS_POLICY_PATH = "/api/data/v1/huieryun/objectstorage/policy/cube/getpolicy"

# 充值业务类型：type=0 充值 / type=1 退款（仅扣减）
TYPE_RECHARGE = 0
TYPE_REFUND = 1
RECHARGE_BIZ = "ENTERPRISE"


def b64pwd(pwd: str) -> str:
    return base64.b64encode(pwd.encode("utf-8")).decode()


def login_payload(username: str, password: str, verify_code: str, unique_id: str, type_: str) -> dict[str, Any]:
    return {
        "loginType": "1",
        "userName": username,
        "password": b64pwd(password),
        "verifyCode": verify_code,
        "uniqueId": unique_id,
        "tenantId": TENANT_ID,
        "type": type_,
    }


def recharge_payload(type_: int, phones: list[str], amount: float) -> dict[str, Any]:
    return {"type": type_, "phones": phones, "amount": amount, "rechargeBizType": RECHARGE_BIZ}


def account_page_payload(page: int = 1, size: int = 10, mobile: str | None = None) -> dict[str, Any]:
    body: dict[str, Any] = {"pageNum": page, "pageSize": size}
    if mobile:
        body["mobile"] = mobile
    return body


def is_ok(body: dict[str, Any] | None) -> bool:
    return bool(body) and str(body.get("resultCode")) == "0"
