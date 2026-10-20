import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from app.services.api_doc_parser_service import APIDocParserService


SAMPLE_API_DOC = """-- B端-营销云
-- 查询充值流水明细
POST /api/icdp/v1/memberCard/account/financeRecord/page
增加返回参数 canRefund true-能退款，false-不能退款

域名：https://imarketing-uat.xysjg.com/yundt-saas-gateway

-- 储值退款-计算可退金额
POST /api/icdp/v1/memberCard/storage-free-order/refund/calculate
请求体：
{"orderNo":"xxxxxxxxxxxxxx"}
响应：
{
  "data": {
    "actualPaidAmount": 0.01, -- 订单实付金额
    "backCoupons": [
        {
            "couponName": "券名称",
            "couponIds": [1111,2222],
            "couponCost": 1,
            "count": 3
        }
    ],
    "orderNo": "2026052215380904280530053",
    "recoverStorageAmount": 0.01,
    "refundableAmount": 0.01,
    "usedCouponCost": 0.0,
    "usedCoupons": [],
    "usedFreeOrderBenefit": 0.0,
    "usedStorageAmount": 0.0
  },
  "errno": 0,
  "ext": null,
  "message": "操作成功",
  "success": true
}

-- 储值退款-退款申请
POST /api/icdp/v1/memberCard/storage-free-order/refund/execute
请求体：
{"orderNo":"xxxxxxxxxxxxxx"}
"""


CURL_API_DOC = """curl --location --request POST 'https://uat.example/yundt-saas-gateway/api/icdp/v1/recharge/reSetRecharge' \\
--header 'access-token: should-not-be-imported' \\
--header 'content-type: application/json;charset=UTF-8' \\
--data-raw '{"type":0,"phones":["18000000000"],"amount":30,"rechargeBizType":"ENTERPRISE"}'
"""


def test_parse_text_to_draft_extracts_api_doc_fields():
    service = APIDocParserService()

    draft = service.parse_text_to_draft(SAMPLE_API_DOC)

    assert draft["base_url"] == "https://imarketing-uat.xysjg.com/yundt-saas-gateway"
    assert draft["total_endpoints"] == 3
    assert [endpoint["summary"] for endpoint in draft["endpoints"]] == [
        "查询充值流水明细",
        "储值退款-计算可退金额",
        "储值退款-退款申请",
    ]
    assert draft["endpoints"][0]["extra_response_fields"]["canRefund"]["type"] == "boolean"
    assert draft["endpoints"][1]["request_example"] == {"orderNo": "xxxxxxxxxxxxxx"}
    assert draft["endpoints"][1]["response_example"]["data"]["refundableAmount"] == 0.01


def test_draft_to_openapi_builds_importable_spec():
    service = APIDocParserService()

    draft = service.parse_text_to_draft(SAMPLE_API_DOC)
    spec = service.draft_to_openapi(draft)

    assert spec["openapi"] == "3.0.0"
    assert spec["servers"][0]["url"] == "https://imarketing-uat.xysjg.com/yundt-saas-gateway"
    assert len(spec["paths"]) == 3

    page_operation = spec["paths"]["/api/icdp/v1/memberCard/account/financeRecord/page"]["post"]
    assert page_operation["responses"]["200"]["content"]["application/json"]["schema"]["properties"]["canRefund"] == {
        "type": "boolean",
        "description": "true-能退款，false-不能退款",
    }

    calculate_operation = spec["paths"]["/api/icdp/v1/memberCard/storage-free-order/refund/calculate"]["post"]
    request_schema = calculate_operation["requestBody"]["content"]["application/json"]["schema"]
    response_schema = calculate_operation["responses"]["200"]["content"]["application/json"]["schema"]
    assert request_schema["required"] == ["orderNo"]
    assert request_schema["properties"]["orderNo"]["type"] == "string"
    assert response_schema["properties"]["data"]["properties"]["refundableAmount"]["type"] == "number"


def test_extract_text_from_txt_bytes_supports_utf8_sig():
    service = APIDocParserService()

    text = service.extract_text_from_file(
        "api.txt",
        "text/plain",
        ("\ufeff" + SAMPLE_API_DOC).encode("utf-8"),
    )

    assert text.startswith("-- B端-营销云")


def test_parse_curl_document_ignores_auth_header_and_redacts_phone():
    service = APIDocParserService()

    draft = service.parse_text_to_draft(CURL_API_DOC)

    assert draft["base_url"] == "https://uat.example/yundt-saas-gateway"
    assert draft["total_endpoints"] == 1
    endpoint = draft["endpoints"][0]
    assert endpoint["method"] == "POST"
    assert endpoint["path"] == "/api/icdp/v1/recharge/reSetRecharge"
    assert endpoint["request_example"] == {
        "type": 0,
        "phones": ["{{memberPhone}}"],
        "amount": 30,
        "rechargeBizType": "ENTERPRISE",
    }
    assert "should-not-be-imported" not in str(draft)
