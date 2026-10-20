import pytest
import sys
from pathlib import Path
from uuid import uuid4

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from app.models.api_endpoint import APIEndpoint
from app.services.api_case_link_service import APICaseLinkService


def test_generate_get_script_executes_basic_contract():
    service = APICaseLinkService(session=None)
    endpoint = APIEndpoint(
        id=uuid4(),
        project_id=uuid4(),
        display_name="GET /api/v1/brands/{id}",
        path="/api/v1/brands/{id}",
        method="GET",
        parameters=[],
    )

    script = service._generate_playwright_script(endpoint)

    assert "request.get" in script
    assert '"/api/v1/brands/1"' in script
    assert "path.replace(/^\\/+/, '')" in script
    assert "expect(status).toBeGreaterThanOrEqual(200)" in script
    assert "expect(status).toBeLessThan(300)" in script
    assert "断言：HTTP 响应状态为 2xx" in script
    assert "ALLOW_MUTATING_API_TESTS" not in script
    assert "resolveTemplate" in script
    assert "API_TEST_AUTHORIZATION" in script
    assert "API_TEST_AUTH_HEADER" in script


def test_generate_post_script_is_guarded_by_default():
    service = APICaseLinkService(session=None)
    endpoint = APIEndpoint(
        id=uuid4(),
        project_id=uuid4(),
        display_name="POST /api/v1/brands",
        path="/api/v1/brands",
        method="POST",
        request_body={
            "content": {
                "application/json": {
                    "schema": {
                        "type": "object",
                        "required": ["brandName"],
                        "properties": {
                            "brandName": {"type": "string"},
                            "sort": {"type": "integer"},
                        },
                    }
                }
            }
        },
    )

    script = service._generate_playwright_script(endpoint)

    assert "request.post" in script
    assert "ALLOW_MUTATING_API_TESTS" in script
    assert '"brandName": "test"' in script
    assert '"sort"' not in script


def test_generate_readonly_post_script_is_not_guarded_and_supports_template_data():
    service = APICaseLinkService(session=None)
    endpoint = APIEndpoint(
        id=uuid4(),
        project_id=uuid4(),
        display_name="POST /api/v1/account/page",
        path="/api/v1/account/page",
        method="POST",
        request_body={
            "content": {
                "application/json": {
                    "example": {"memberPhone": "{{memberPhone}}"},
                    "schema": {"type": "object"},
                }
            }
        },
    )

    script = service._generate_playwright_script(endpoint)

    assert "ALLOW_MUTATING_API_TESTS" not in script
    assert '"memberPhone": "{{memberPhone}}"' in script
    assert "Missing API test data" in script


def test_mutating_endpoint_classification_is_conservative():
    service = APICaseLinkService(session=None)

    assert service._is_mutating_endpoint("POST", "/api/v1/account/recharge")
    assert not service._is_mutating_endpoint("POST", "/api/v1/account/calculate")


@pytest.mark.xfail(
    strict=True,
    reason="过时断言（2026-10-08 判性质为假红，非生成器缺陷）：本用例要求脚本里出现**硬编码** "
           "resultCode 字面量，而现行生成器已把字段名模板化为 ${ENVELOPE_FIELD}（三层信封断言仍在，"
           "见 :702/:717），且 backend/tests/test_api_response_envelope.py 明确断言 "
           "'envelope.resultCode' not in script —— 新旧主张相反。"
           "处置：只杀该条假红，本文件其余 5 个函数维持类别 C，待生成器提交后逐条处置。"
           "依据见 顶层tests遗留目录-对照清单-20261008.md 与 "
           "API环境阻断闸-接线与验证-复核说明-20261008.md §5。",
)
def test_generate_script_asserts_response_envelope_not_only_http_status():
    """P5 阶段 A：信封式 SUT 的业务成败靠 resultCode 区分，HTTP 恒 200。

    只断言 HTTP 2xx 等于没有断言——认证失败（resultCode=401）与业务失败
    （resultCode!=0）都会被误判为"通过"。此测试锁定三层信封断言的存在。
    """
    service = APICaseLinkService(session=None)
    endpoint = APIEndpoint(
        id=uuid4(),
        project_id=uuid4(),
        display_name="GET /v1/purchaseLimit/list",
        path="/v1/purchaseLimit/list",
        method="GET",
        parameters=[],
    )

    script = service._generate_playwright_script(endpoint)

    # 1) 必须读取响应体原文并尝试解析为 JSON
    assert "response.text()" in script
    assert "JSON.parse(rawBody)" in script
    # 2) 三层断言：可解析 → 含 resultCode → resultCode == '0'
    assert "响应信封可解析" in script
    assert "信封含 resultCode 字段" in script
    assert "业务结果为成功（resultCode=0" in script
    # 3) 失败时给出可检索的分类标记（便于下游归类为凭据/业务问题）
    assert "AUTH_OR_BUSINESS_ENVELOPE" in script
    # 4) 不伪造：resultCode 缺失时显式暴露，而不是静默通过
    assert "缺少 resultCode 字段" in script


def test_store_script_uses_local_backup_when_minio_fails(tmp_path, monkeypatch):
    service = APICaseLinkService(session=None)
    monkeypatch.setattr(
        "app.services.api_case_link_service.settings.api_workspace_root",
        str(tmp_path),
    )

    def fail_upload(**_kwargs):
        raise RuntimeError("minio down")

    monkeypatch.setattr(
        "app.services.api_case_link_service.MinIOClient.upload_bytes",
        fail_upload,
    )

    result = service._store_script(
        "api-tests/PR-1/endpoints/e-1/test-script.ts",
        "console.log('ok');",
    )

    assert result["minio"] is False
    backup_path = tmp_path / "artifacts_backup" / "api-tests/PR-1/endpoints/e-1/test-script.ts"
    assert backup_path.read_text(encoding="utf-8") == "console.log('ok');"
