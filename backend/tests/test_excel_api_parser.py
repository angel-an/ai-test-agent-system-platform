"""美心 Excel 解析器 + 归一化器测试（以固定样本为准）。

样本路径通过环境变量 MX_SAMPLE_XLSX 指定；未配置时跳过，
避免在 CI/无样本环境失败。
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from app.services.api_spec_normalizer import APISpecNormalizer
from app.services.excel_api_parser import ExcelAPIParser

SAMPLE = os.environ.get(
    "MX_SAMPLE_XLSX",
    r"D:\测试管理\项目管理\美心项目\美心POS\API\YX CRM API Spec For Non POS (1).xlsx",
)

pytestmark = pytest.mark.skipif(
    not Path(SAMPLE).exists(), reason=f"美心样本不存在: {SAMPLE}"
)


@pytest.fixture(scope="module")
def workbook_bytes() -> bytes:
    return Path(SAMPLE).read_bytes()


@pytest.fixture(scope="module")
def parser(workbook_bytes):
    return ExcelAPIParser().load(workbook_bytes)


def _interface_sheets(p: ExcelAPIParser):
    return [n for n in p.sheet_names() if not p.is_guide_sheet(p._wb[n])]


def test_sheet_counts(parser):
    names = parser.sheet_names()
    assert len(names) == 26, f"应识别 26 个 Sheet（1 导读 + 25 接口），实际 {len(names)}"
    guides = [n for n in names if parser.is_guide_sheet(parser._wb[n])]
    assert len(guides) == 1, f"应识别 1 个导读 Sheet，实际 {guides}"
    assert len(_interface_sheets(parser)) == 25, "应识别 25 个接口 Sheet"


def test_guide_rules(parser):
    guide_ws = next(parser._wb[n] for n in parser.sheet_names() if parser.is_guide_sheet(parser._wb[n]))
    guide = parser.parse_guide(guide_ws)
    assert guide["model"] and "api" in (guide["model"] or "").lower()
    assert guide["sign"], "应解析出签名规则"
    assert guide["sign"]["algo"] == "md5"
    order = guide["sign"]["concat_order"]
    assert order[:4] == ["appid", "appsercret", "ts", "msg"], f"签名拼接顺序错误: {order}"


def test_check_mobile_duplicate(parser):
    ws = parser._wb["COP api_checkMobileDuplicat"]
    iface = parser.parse_interface_sheet(ws)
    assert iface.api_name == "api_checkMobileDuplicate"
    assert iface.http_method == "POST"
    assert iface.path and "api_checkMobileDuplicate" in iface.path

    req_names = {f.name: f for f in iface.request_fields}
    # 必填：带 * 的字段
    assert req_names["MobilePhone"].required is True
    assert req_names["Touchpoint"].required is True
    # 可选：CountryCode 无 *
    assert "CountryCode" in req_names, "应识别出 CountryCode 字段"
    assert req_names["CountryCode"].required is False, "CountryCode 应为可选"

    # 出参 Existed 的 MX1/null 业务规则在差异列
    resp = {f.name: f for f in iface.response_fields}
    assert "Existed" in resp
    assert resp["Existed"].diff and "MX1" in resp["Existed"].diff


def test_return_code_region_parsed(parser):
    """返回码区必须被解析成 error_codes（回归：曾经恒为 0）。

    历史缺陷：分区标记常量写作 ``"return code"``，而比对前文本做了
    ``lower().replace(" ", "")`` → ``"returncode"``，``startswith`` 恒为 False，
    导致返回码区从未进入解析分支，error_codes 永远为空。
    """
    total = 0
    sheets_with_codes: list[str] = []

    for name in _interface_sheets(parser):
        iface = parser.parse_interface_sheet(parser._wb[name])
        if iface.error_codes:
            sheets_with_codes.append(name)
        total += len(iface.error_codes)

    assert total > 0, "真实样本含 3 个 Return Code 区，error_codes 不应为空"
    assert sheets_with_codes, "至少有一个接口应解析出错误码"

    # 逐条校验结构：码为数字、带含义、带可追溯来源位置
    sample_name = sheets_with_codes[0]
    sample = parser.parse_interface_sheet(parser._wb[sample_name])
    for code in sample.error_codes:
        assert str(code["code"]).strip().isdigit(), f"错误码应为数字: {code}"
        assert code.get("meaning"), f"错误码应带含义: {code}"
        assert code["source"]["sheet"] == sample_name
        assert code["source"]["row"] > 0

    # 错误码不得被误当作出参字段
    assert not any(
        str(f.name).strip().isdigit() for f in sample.response_fields
    ), "返回码区不应混入 response_fields"


def test_error_codes_reach_contract_and_clear_pending(parser):
    """有返回码区的接口，统一契约应带 error_codes 且不再报 missing_error_codes。"""
    guide_ws = next(parser._wb[n] for n in parser.sheet_names() if parser.is_guide_sheet(parser._wb[n]))
    guide = parser.parse_guide(guide_ws)
    normalizer = APISpecNormalizer()

    # 真实样本里并非每个接口都提供返回码区，动态选取带返回码的接口
    with_codes = [
        n for n in _interface_sheets(parser)
        if parser.parse_interface_sheet(parser._wb[n]).error_codes
    ]
    assert with_codes, "真实样本应有至少一个带返回码区的接口"

    name = with_codes[0]
    iface = parser.parse_interface_sheet(parser._wb[name])
    spec = normalizer.to_unified_spec(iface, guide)

    assert len(spec["response"]["error_codes"]) == len(iface.error_codes)
    pending_types = {i["type"] for i in spec["pending_confirmations"]}
    assert "missing_error_codes" not in pending_types, (
        f"已提供返回码区的接口不应再被标记为缺少错误码: {name}"
    )


def test_unified_spec_and_pending(parser):
    guide_ws = next(parser._wb[n] for n in parser.sheet_names() if parser.is_guide_sheet(parser._wb[n]))
    guide = parser.parse_guide(guide_ws)
    normalizer = APISpecNormalizer()

    ws = parser._wb["COP api_checkMobileDuplicat"]
    iface = parser.parse_interface_sheet(ws)
    spec = normalizer.to_unified_spec(iface, guide, attachment_id="test-att")

    assert spec["method_name"] == "checkMobileDuplicate"
    assert spec["auth"]["type"] == "mx_md5"
    assert spec["auth"]["sign_fields"][:4] == ["appid", "appsercret", "ts", "msg"]
    assert spec["source"]["sheet"] == "COP api_checkMobileDuplicat"

    types = {i["type"] for i in spec["pending_confirmations"]}
    # 占位地址应被识别
    assert "placeholder_url" in types, f"应识别占位地址，实际待确认项: {types}"
    # 错误码缺失应被识别（该接口无 Return Code 区）
    assert "missing_error_codes" in types


def test_all_interfaces_normalize(parser):
    guide_ws = next(parser._wb[n] for n in parser.sheet_names() if parser.is_guide_sheet(parser._wb[n]))
    guide = parser.parse_guide(guide_ws)
    normalizer = APISpecNormalizer()

    for name in _interface_sheets(parser):
        iface = parser.parse_interface_sheet(parser._wb[name])
        spec = normalizer.to_unified_spec(iface, guide)
        assert spec["request"]["fields"], f"{name} 应至少有一个入参字段"
        assert spec["response"]["fields"], f"{name} 应至少有一个出参字段"
        # 每个字段都带来源位置
        for f in spec["request"]["fields"] + spec["response"]["fields"]:
            assert f["source"].get("sheet") == name
            assert f["source"].get("row", 0) > 0
