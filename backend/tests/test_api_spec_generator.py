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
from app.services.signing.mx_md5_signer import (
    canonical_msg,
    mx_md5_js_helper,
    mx_md5_sign,
)
from tests.sample_paths import COLLECTION_PATH

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


class TestContractDivergenceWithRealCollection:
    """固化"生成器约定"与"真实 Collection 约定"的差异（**不做泛化**）。

    背景（UAT 调查期间实测）：
    - Excel 导读要求：`MD5(appid+appsercret+ts+msg)`，`ts = yyyyMMddHHmmss`
    - 真实 Collection（pre-request 脚本）与用户 Postman 实测请求要求：
      `MD5(appid + secret + ts + 原始 body 字符串)`，`ts = 毫秒`，
      且额外发送 `model` / `method` / `request-channel` 三个请求头

    **为什么只是固化差异而不是直接改生成器**：
    1. 两套约定可能对应不同服务（Excel 导读面向 COP/MP 系列；
       实测请求是 `aigens` 通道的 `ord_memLogin`）；
    2. 仅有一条真实请求样本，不足以推广到全部接口；
    3. Excel 导读是 P0 的契约来源，擅自改动会让生成产物与上游文档不一致。

    因此本测试的作用是：**一旦生成器或上游约定发生变化，立即失败并提醒**
    "需要业务文档确认后再统一"，避免差异被静默掩盖。
    """

    def test_timestamp_format_differs_from_real_collection(self):
        """生成器用秒级 yyyyMMddHHmmss；真实 Collection 用毫秒。"""
        helper = mx_md5_js_helper()
        assert "yyyyMMddHHmmss" in helper, "生成器时间戳格式应仍为秒级（Excel 导读约定）"
        assert "Date.now()" not in helper, (
            "生成器未使用毫秒时间戳；若已改为毫秒，请同步更新本差异测试与交付说明"
        )

    def test_sign_input_is_compact_json_not_raw_body(self):
        """生成器对紧凑 JSON 签名；真实 Collection 对原始 body 字符串签名。"""
        payload = {"MobilePhone": "123", "Touchpoint": "W"}
        assert canonical_msg(payload) == '{"MobilePhone":"123","Touchpoint":"W"}'
        # 真实约定下，带缩进的原始 body 会产生不同 msg（因此签名也不同）
        raw_body = json.dumps(payload, ensure_ascii=False, indent=2)
        assert raw_body != canonical_msg(payload)
        assert mx_md5_sign("a", "b", "1", raw_body) != mx_md5_sign("a", "b", "1", canonical_msg(payload))

    def test_generator_emits_only_three_auth_headers(self, checkmobile_spec):
        """生成器只发 3 个鉴权头；真实 Collection 另有 model/method/request-channel。"""
        script = generate_playwright_script(checkmobile_spec, {"base_url": "https://mx.example.com"})
        assert "appid: APPID, ts, sign," in script
        for extra in ("'model'", "'method'", "'request-channel'"):
            assert extra not in script, (
                f"生成器新增了 {extra} 头；请同步更新本差异测试与交付说明"
            )

    def test_real_collection_uses_raw_body_signing(self):
        """真实 Collection 的 pre-request 脚本以原始 body 字符串计算签名。"""
        if not COLLECTION_PATH.exists():
            pytest.skip(f"真实样本不存在: {COLLECTION_PATH}")

        raw = json.loads(COLLECTION_PATH.read_text(encoding="utf-8"))

        def collect(items):
            out = []
            for item in items or []:
                if "item" in item:
                    out += collect(item["item"])
                elif "event" in item:
                    out.append(item)
            return out

        scripts = []
        for item in collect(raw.get("item")):
            for event in item.get("event") or []:
                if event.get("listen") == "prerequest":
                    exec_list = event.get("script", {}).get("exec") or []
                    scripts.append("\n".join(exec_list))
        assert scripts, "真实 Collection 应含 pre-request 脚本"
        joined = "\n".join(scripts)
        # 这是差异的证据：真实脚本用 body 原串 + 毫秒 ts
        assert "pm.request.body.raw" in joined, "真实脚本应以原始 body 字符串参与签名"
        assert "Date.now()" in joined, "真实脚本应使用毫秒时间戳"


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
