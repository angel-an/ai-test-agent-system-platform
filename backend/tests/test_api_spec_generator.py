"""签名一致性与契约测试生成器测试。

核心验收：签名用的 msg JSON 与实际发送 body 完全一致（同一序列化结果）。
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

import pytest

from app.services.api_spec_normalizer import APISpecNormalizer
from app.services.api_spec_test_generator import (
    analyze_requirement,
    generate_playwright_script,
    generate_test_plan,
)
from app.services.excel_api_parser import ExcelAPIParser
from app.services.signing.mx_md5_signer import canonical_msg, mx_md5_sign

# 路径统一来自 tests/sample_paths.py（曾因硬编码漏写 `美心POS` 层级导致 7 个用例静默跳过）
from tests.sample_paths import EXCEL_PATH

SAMPLE = str(EXCEL_PATH)

pytestmark = pytest.mark.skipif(not Path(SAMPLE).exists(), reason=f"美心样本不存在: {SAMPLE}")


@pytest.fixture(scope="module")
def checkmobile_spec():
    p = ExcelAPIParser().load(Path(SAMPLE).read_bytes())
    guide_ws = next(p._wb[n] for n in p.sheet_names() if p.is_guide_sheet(p._wb[n]))
    guide = p.parse_guide(guide_ws)
    iface = p.parse_interface_sheet(p._wb["COP api_checkMobileDuplicat"])
    return APISpecNormalizer().to_unified_spec(iface, guide, attachment_id="att-1")


def test_canonical_msg_stable():
    payload = {"MobilePhone": "99887766", "Touchpoint": "W"}
    m1 = canonical_msg(payload)
    m2 = canonical_msg(payload)
    assert m1 == m2
    # 紧凑分隔符、无空格
    assert ", " not in m1 and ": " not in m1
    # 不转义非 ASCII
    assert json.loads(m1) == payload


def test_mx_md5_sign_matches_manual():
    appid, secret, ts = "ABCDEFG", "123456", "20200420135441"
    msg = '{"member_phone":"13459071393"}'
    expect = hashlib.md5(f"{appid}{secret}{ts}{msg}".encode("utf-8")).hexdigest()
    assert mx_md5_sign(appid, secret, ts, msg) == expect
    assert len(expect) == 32 and expect == expect.lower()


def test_sign_msg_equals_send_body(checkmobile_spec):
    """脚本内签名 msg 必须与实际发送 data 完全一致。"""
    script = generate_playwright_script(checkmobile_spec, {"base_url": "https://mx.example.com"})
    # 脚本中 data: msg 且 msg = canonicalMsg(body)
    assert "const msg = canonicalMsg(body);" in script
    assert "data: msg," in script
    # 签名基于同一 msg
    assert "mxMd5Sign(APPID, APPSECRET, ts, msg)" in script


def test_generate_script_requires_base_url(checkmobile_spec):
    with pytest.raises(ValueError):
        generate_playwright_script(checkmobile_spec, {})


def test_analyze_requirement_distinguishes_mx1_and_null(checkmobile_spec):
    analysis = analyze_requirement(checkmobile_spec)
    branches = analysis["business_branches"]
    whens = [b["when"] for b in branches]
    assert any("'MX1'" in w for w in whens), "应区分 Existed=='MX1'"
    assert any("null" in w.lower() for w in whens), "应区分 Existed is null"


def test_test_plan_has_categories_and_basis(checkmobile_spec):
    plan = generate_test_plan(checkmobile_spec)
    cats = {c["category"] for c in plan["cases"]}
    assert {"normal", "required", "auth"} <= cats
    # 每个用例都有依据
    for c in plan["cases"]:
        assert c.get("basis"), f"用例缺依据: {c['title']}"
        assert c.get("assertions"), f"用例缺断言: {c['title']}"
    # 错误码缺失 -> 存在待确认断言，而非严格业务断言
    assert plan["pending_count"] > 0, "缺错误码时应标待确认"


def test_script_contains_required_fields(checkmobile_spec):
    script = generate_playwright_script(checkmobile_spec, {"base_url": "https://mx.example.com"})
    assert "MobilePhone" in script
    assert "Touchpoint" in script
    # 密钥走环境变量，不出现明文
    assert "process.env.MX_APPSECRET" in script
    assert "123456" not in script
