# -*- coding: utf-8 -*-
"""真实样本路径与 UAT 执行凭据的**唯一来源**。

为什么需要它
------------
同类错误已连续发生两次：`tests/test_excel_api_parser.py` 与
`tests/test_api_spec_generator.py` 都漏写路径中的 `美心POS` 层级，
导致用例被 `skipif not exists` **静默跳过**，而报告仍显示"通过"。
根因是每个文件各自硬编码路径。本模块把路径收敛到一处。

用法::

    from tests.sample_paths import EXCEL_PATH, COLLECTION_PATH, ENVIRONMENT_PATH
    from tests.sample_paths import MX_SAMPLE_DIR, uat_base_url, uat_sign_credentials

维护约定
--------
- 任何需要真实样本的测试/脚本**必须**从本模块取路径，不得再硬编码；
- `MX_SAMPLE_DIR` 可用环境变量 `MX_SAMPLE_DIR` 覆盖（样本迁移时只改一处）；
- UAT 执行所需凭据通过环境变量注入，**不落库、不写文件**。
"""

from __future__ import annotations

import os
from pathlib import Path

#: 样本目录（可用环境变量覆盖，便于样本迁移或 CI 挂载）
MX_SAMPLE_DIR = Path(
    os.environ.get("MX_SAMPLE_DIR")
    or r"D:\测试管理\项目管理\美心项目\美心POS\API"
)

#: 单个样本文件也可单独覆盖（兼容既有用法）
EXCEL_PATH = Path(
    os.environ.get("MX_SAMPLE_XLSX") or (MX_SAMPLE_DIR / "YX CRM API Spec For Non POS (1).xlsx")
)
COLLECTION_PATH = Path(
    os.environ.get("MX_SAMPLE_COLLECTION") or (MX_SAMPLE_DIR / "美心.postman_collection.json")
)
ENVIRONMENT_PATH = Path(
    os.environ.get("MX_SAMPLE_ENVIRONMENT")
    or (MX_SAMPLE_DIR / "UAT_eatizen HK CPO.postman_environment.json")
)

#: 本机验收用真实 UAT 目标（由用户提供）
UAT_BASE_URL = os.environ.get(
    "MX_UAT_BASE_URL", "https://console.uat.eatizen.com.hk/yundt-saas-gateway"
)

#: 用户提供的测试手机号（查重接口用）
UAT_TEST_MOBILE = os.environ.get("MX_UAT_TEST_MOBILE", "18320101265")

#: 签名凭据用的环境变量名。
#:
#: 注意：真实环境文件里只有 `api-appid` / `api-secret` **引用**，
#: 没有 appid/appsercret 的**值**（这正是导入时 `env_missing` 高等级冲突的来源）。
#: 因此要执行真实请求，必须由外部注入下列环境变量。
UAT_APPID_ENV = "MX_APPID"
UAT_SECRET_ENV = "MX_APPSECRET"


def sample_files() -> dict[str, Path]:
    return {
        "excel": EXCEL_PATH,
        "collection": COLLECTION_PATH,
        "environment": ENVIRONMENT_PATH,
    }


def missing_samples() -> list[str]:
    """返回不存在的样本路径（供测试/脚本显式失败，而非静默跳过）。"""
    return [str(p) for p in sample_files().values() if not p.exists()]


def uat_base_url() -> str:
    return UAT_BASE_URL.rstrip("/")


def uat_sign_credentials() -> tuple[str | None, str | None]:
    """返回 (appid, appsecret)。

    未注入时返回 (None, None) —— 调用方应据此**阻断**（blocked），
    不得伪造或跳过签名，也不得把缺失误判为失败。
    """
    return os.environ.get(UAT_APPID_ENV), os.environ.get(UAT_SECRET_ENV)


def uat_credential_status() -> dict[str, object]:
    appid, secret = uat_sign_credentials()
    return {
        "base_url": uat_base_url(),
        "test_mobile": UAT_TEST_MOBILE,
        "appid_configured": bool(appid),
        "appsecret_configured": bool(secret),
        "required_env_vars": [UAT_APPID_ENV, UAT_SECRET_ENV],
        "ready": bool(appid and secret),
    }
