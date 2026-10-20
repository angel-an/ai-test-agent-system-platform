"""Publish the verified coupon-template API test evidence into project PR-2.

The script is deliberately idempotent: it reuses the two endpoint records and
the associated API test, then replaces the evidence attachments with the
latest redacted test artifacts. It never stores access tokens, cookies, or
other authentication material.
"""

from __future__ import annotations

import asyncio
import argparse
import json
import os
import sys
from pathlib import Path
from urllib.parse import unquote, urlparse
from uuid import uuid4

from dotenv import load_dotenv


ROOT = Path(__file__).resolve().parents[1]
BACKEND_ROOT = ROOT / "backend"
PROJECT_IDENTIFIER = "PR-2"
BASE_URL = "https://imarketing-uat.xysjg.com/yundt-saas-gateway"
CREATE_PATH = "/api/imarketing/v1/couponTemplate"
LIST_PATH = "/api/imarketing/v1/couponTemplate/list"
SUITE_NAME = "券模板创建与列表接口自动化测试"


def configure_database() -> str:
    """Translate the repository POSTGRES_URI into the app's DB settings."""
    load_dotenv(ROOT / ".env")
    uri = os.environ.get("POSTGRES_URI") or os.environ.get("DATABASE_URI")
    if not uri:
        raise RuntimeError("未配置 POSTGRES_URI 或 DATABASE_URI")

    parsed = urlparse(uri)
    if not parsed.hostname or not parsed.path.strip("/"):
        raise RuntimeError("POSTGRES_URI 格式无效")

    os.environ["POSTGRES_HOST"] = parsed.hostname
    os.environ["POSTGRES_PORT"] = str(parsed.port or 5432)
    os.environ["POSTGRES_USER"] = unquote(parsed.username or "postgres")
    os.environ["POSTGRES_PASSWORD"] = unquote(parsed.password or "")
    os.environ["POSTGRES_DB"] = parsed.path.strip("/")
    return uri


def artifact_contents(
    idp_issue_id: int | None = None,
    idp_issue_key: str | None = None,
) -> dict[str, tuple[str, str, str]]:
    """Return name, MIME type and redacted evidence for each artifact type."""
    plan = f"""# {SUITE_NAME}

## 范围

- POST `{CREATE_PATH}`：创建商品券模板。
- GET `{LIST_PATH}`：查询券模板列表与分页边界。

## 环境与口径

- 环境：小杨生煎 UAT。
- 认证：测试执行时已使用有效授权；本成果物不保存 token、cookie 或请求头敏感值。
- 缺陷判定：分页参数 `pageSize` 应为有效的正整数；`pageSize=0` 不应绕过分页并返回全部数据。
- 极端参数（如 `pageNum=9999`）按低优先级口径验证，不作为高优先级故障。
"""

    cases = {
        "test_cases": [
            {"name": "创建券模板", "endpoint": CREATE_PATH, "method": "POST", "expected": "HTTP 200 且返回券模板 ID", "status": "passed"},
            {"name": "创建结果列表回读", "endpoint": LIST_PATH, "method": "GET", "expected": "新建券模板可在第一页查询到", "status": "passed"},
            {"name": "正常翻页", "endpoint": LIST_PATH, "method": "GET", "expected": "pageNum=2&pageSize=1 返回第二页数据", "status": "passed"},
            {"name": "超大页码", "endpoint": LIST_PATH, "method": "GET", "expected": "pageNum=9999 安全返回空列表，不发生 5xx", "status": "passed"},
            {"name": "创建必填项校验", "endpoint": CREATE_PATH, "method": "POST", "expected": "空请求体返回 HTTP 400 与字段校验信息", "status": "passed"},
            {"name": "零页大小校验", "endpoint": LIST_PATH, "method": "GET", "expected": "pageSize=0 被拒绝或返回受控分页结果", "status": "failed"},
        ]
    }

    script = f'''import {{ expect, test }} from "@playwright/test";

const baseURL = process.env.IMARKETING_BASE_URL ?? "{BASE_URL}";
const token = process.env.IMARKETING_ACCESS_TOKEN;

test.describe("券模板创建与列表", () => {{
  test.use({{ extraHTTPHeaders: {{ "access-token": token ?? "" }} }});

  test("pageSize=0 不应返回全量数据", async ({{ request }}) => {{
    const response = await request.get(`${{baseURL}}{LIST_PATH}`, {{
      params: {{ pageNum: 1, pageSize: 0, filter: JSON.stringify({{ orgIds: [process.env.IMARKETING_ORG_ID], queryInvalidExpired: false }}) }},
    }});
    expect(response.status()).toBeGreaterThanOrEqual(400);
  }});
}});
'''

    idp_url = f"https://idp.dtyunxi.cn/agile/issues/{idp_issue_id}" if idp_issue_id else None
    idp_status = "created" if idp_issue_id else "pending_valid_token"
    idp_message = (
        f"已创建 IDP 缺陷 {idp_issue_key or idp_issue_id}。"
        if idp_issue_id
        else "允许真实创建；当前可用历史 IDP Token 已过期，尚未生成 IDP Issue。"
    )
    result = {
        "suite": SUITE_NAME,
        "environment": "小杨生煎 UAT",
        "summary": {"total": 6, "passed": 5, "failed": 1, "skipped": 0},
        "created_coupon_template_id": "1449615636934208611",
        "defects": [
            {
                "title": "【自动化测试】【API】【券模板】pageSize=0 返回全部172条记录，分页失效",
                "priority": "低",
                "request_id": "0A000607-MT2B4XUV-27373",
                "endpoint": LIST_PATH,
                "request": "GET pageNum=1&pageSize=0",
                "actual": "HTTP 200, resultCode=0, total=172, size=172，返回全部 172 条记录",
                "expected": "拒绝 pageSize=0 或保持受控的分页语义，不返回全量数据",
                "idp": {
                    "project_id": 1169,
                    "project_name": "小杨生煎",
                    "status": idp_status,
                    "issue_id": idp_issue_id,
                    "issue_key": idp_issue_key,
                    "issue_url": idp_url,
                    "message": idp_message,
                },
            }
        ],
    }

    report = f"""<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8"><title>{SUITE_NAME}</title>
<style>body{{font:14px/1.6 system-ui,sans-serif;margin:32px;color:#1f2937}}h1{{margin:0 0 8px}}table{{border-collapse:collapse;width:100%;margin:16px 0}}th,td{{border:1px solid #d1d5db;padding:8px;text-align:left}}th{{background:#f3f4f6}}.pass{{color:#166534;font-weight:600}}.fail{{color:#b91c1c;font-weight:600}}.note{{background:#fff7ed;border-left:4px solid #f97316;padding:12px}}</style>
</head><body>
<h1>{SUITE_NAME}</h1><p>项目：PR-2（小杨生煎） | 环境：UAT | 自动化测试成果</p>
<table><thead><tr><th>总数</th><th>通过</th><th>失败</th><th>跳过</th></tr></thead><tbody><tr><td>6</td><td class="pass">5</td><td class="fail">1</td><td>0</td></tr></tbody></table>
<h2>验证结果</h2><ul><li>创建券模板：通过，返回模板 ID `1449615636934208611`。</li><li>列表回读、正常翻页、超大页码、空请求体校验：通过。</li><li class="fail">pageSize=0：失败，HTTP 200 返回组织下全部 172 条券模板记录。</li></ul>
<div class="note"><strong>缺陷登记</strong><br>【自动化测试】【API】【券模板】pageSize=0 返回全部172条记录，分页失效<br>优先级：低 | reqid：0A000607-MT2B4XUV-27373<br>IDP 项目：1169（小杨生煎） | 状态：{idp_message}{f' <a href="{idp_url}">查看 IDP 缺陷</a>' if idp_url else ''}</div>
</body></html>"""

    return {
        "API_TEST_PLAN": ("coupon-template-test-plan.md", "text/markdown", plan),
        "API_TEST_CASE": ("coupon-template-test-cases.json", "application/json", json.dumps(cases, ensure_ascii=False, indent=2)),
        "API_TEST_SCRIPT": ("coupon-template-pagination.spec.ts", "text/plain", script),
        "API_TEST_RESULT": ("coupon-template-execution-result.json", "application/json", json.dumps(result, ensure_ascii=False, indent=2)),
        "API_TEST_REPORT": ("coupon-template-automation-report.html", "text/html", report),
    }


async def main(idp_issue_id: int | None = None, idp_issue_key: str | None = None) -> None:
    configure_database()
    sys.path.insert(0, str(BACKEND_ROOT))

    from sqlalchemy import select, text
    from app.config.database import async_session_factory, engine
    from app.config.settings import settings
    from app.models.api_endpoint import APIEndpoint
    from app.models.api_test import APITest, APITestResult, APITestRun
    from app.models.attachment import Attachment, AttachmentEntityType
    from app.models.idp_defect_record import IDPDefectRecord
    from app.models.project import Project
    from app.repositories.api_test_repo import APITestRepository, APITestRunRepository
    from app.schemas.enums import TestResultStatus

    artifact_root = Path(settings.api_workspace_root).resolve() / "artifacts_backup"

    async with engine.begin() as connection:
        await connection.execute(text("ALTER TYPE attachmententitytype ADD VALUE IF NOT EXISTS 'API_TEST_RESULT'"))

    async with async_session_factory() as session:
        project = (await session.execute(select(Project).where(Project.identifier == PROJECT_IDENTIFIER))).scalar_one_or_none()
        if not project:
            raise RuntimeError(f"项目 {PROJECT_IDENTIFIER} 不存在，未写入任何成果物")

        endpoints: dict[str, APIEndpoint] = {}
        endpoint_specs = [
            ("POST", CREATE_PATH, "创建券模板", "B端券模板创建接口"),
            ("GET", LIST_PATH, "查询券模板列表", "B端券模板列表与分页接口"),
        ]
        for method, path, summary, description in endpoint_specs:
            endpoint = (await session.execute(
                select(APIEndpoint).where(
                    APIEndpoint.project_id == project.id,
                    APIEndpoint.method == method,
                    APIEndpoint.path == path,
                )
            )).scalar_one_or_none()
            if not endpoint:
                endpoint = APIEndpoint(
                    project_id=project.id,
                    folder_id=None,
                    display_name=f"{method} {path}",
                    path=path,
                    method=method,
                    summary=summary,
                    description=description,
                    tag_group="B端-券模板",
                    parameters=[],
                    request_body=None,
                    responses={"200": {"description": "业务响应"}, "400": {"description": "参数校验失败"}},
                    security=[],
                    tags=["小杨生煎", "B端", "券模板", "自动化测试"],
                    test_case_ids=[],
                    api_test_ids=[],
                    custom_config={"base_url": BASE_URL},
                    total_test_cases=0,
                    total_test_runs=0,
                    last_run_status=None,
                    sort_order=0,
                )
                session.add(endpoint)
                await session.flush()
            endpoints[method] = endpoint

        create_endpoint = endpoints["POST"]
        api_test = (await session.execute(
            select(APITest).where(APITest.project_id == project.id, APITest.name == SUITE_NAME)
        )).scalar_one_or_none()
        object_prefix = f"api-tests/{PROJECT_IDENTIFIER}/endpoints/{create_endpoint.id}/manual-coupon-template"
        if not api_test:
            api_test = APITest(
                project_id=project.id,
                folder_id=None,
                test_case_id=None,
                identifier=await APITestRepository(session).get_next_identifier(project.id),
                name=SUITE_NAME,
                description="人工触发的真实 UAT API 回归结果，证据已脱敏。",
                schema_url=None,
                schema_path=None,
                schema_type="openapi",
                script_path=f"{object_prefix}/coupon-template-pagination.spec.ts",
                script_format="playwright",
                script_language="typescript",
                test_config={"base_url": BASE_URL, "credential_source": "runtime_environment_only"},
                generated_by_agent="manual_api_test_publisher",
                generation_params={"source": "verified_curl_execution"},
                total_endpoints=2,
                total_scenarios=6,
            )
            session.add(api_test)
            await session.flush()

        for endpoint in endpoints.values():
            linked_ids = set(endpoint.api_test_ids or [])
            linked_ids.add(str(api_test.id))
            endpoint.api_test_ids = sorted(linked_ids)
            endpoint.total_test_cases = 2 if endpoint.method == "POST" else 4
            endpoint.total_test_runs = 1
            endpoint.last_run_status = "completed"

        run = (await session.execute(
            select(APITestRun).where(APITestRun.api_test_id == api_test.id, APITestRun.identifier == "ATR-COUPON-TEMPLATE-UAT")
        )).scalar_one_or_none()
        if not run:
            run = APITestRun(
                project_id=project.id,
                api_test_id=api_test.id,
                identifier="ATR-COUPON-TEMPLATE-UAT",
                status="completed",
                execution_config={"environment": "UAT", "source": "verified_curl_execution"},
                total_tests=6,
                passed_tests=5,
                failed_tests=1,
                skipped_tests=0,
                error_message="发现低优先级分页缺陷：pageSize=0 返回全量记录。",
            )
            session.add(run)
            await session.flush()

            results = [
                ("创建券模板", "POST", CREATE_PATH, TestResultStatus.PASSED, 200, None),
                ("创建结果列表回读", "GET", LIST_PATH, TestResultStatus.PASSED, 200, None),
                ("正常翻页", "GET", LIST_PATH, TestResultStatus.PASSED, 200, None),
                ("超大页码 pageNum=9999", "GET", LIST_PATH, TestResultStatus.PASSED, 200, None),
                ("空请求体字段校验", "POST", CREATE_PATH, TestResultStatus.PASSED, 400, None),
                ("pageSize=0 分页边界", "GET", LIST_PATH, TestResultStatus.FAILED, 200, "返回全部 172 条记录，分页失效；reqid=0A000607-MT2B4XUV-27373"),
            ]
            for name, method, path, status, status_code, error in results:
                session.add(APITestResult(
                    test_run_id=run.id,
                    api_test_id=api_test.id,
                    scenario_name=name,
                    endpoint=path,
                    method=method,
                    status=status,
                    request_summary={"url": f"{BASE_URL}{path}", "method": method, "headers": "redacted"},
                    response_summary={"status_code": status_code},
                    error_message=error,
                ))

        if idp_issue_id:
            record = (await session.execute(
                select(IDPDefectRecord).where(
                    IDPDefectRecord.test_run_id == run.id,
                    IDPDefectRecord.fingerprint == "GET:/api/imarketing/v1/couponTemplate/list:pageSize=0",
                )
            )).scalar_one_or_none()
            record_fields = {
                "source_project_key": PROJECT_IDENTIFIER,
                "idp_project_id": 1169,
                "reqid": "0A000607-MT2B4XUV-27373",
                "defect_title": "【自动化测试】【API】【券模板】pageSize=0 返回全部172条记录，分页失效",
                "defect_priority": "low",
                "create_status": "created",
                "idp_issue_id": idp_issue_id,
                "idp_issue_key": idp_issue_key,
                "idp_issue_url": f"https://idp.dtyunxi.cn/agile/issues/{idp_issue_id}",
                "error_message": None,
            }
            if record:
                for field, value in record_fields.items():
                    setattr(record, field, value)
            else:
                session.add(IDPDefectRecord(
                    test_run_id=run.id,
                    test_case_id=None,
                    fingerprint="GET:/api/imarketing/v1/couponTemplate/list:pageSize=0",
                    **record_fields,
                ))

        for endpoint in endpoints.values():
            endpoint_prefix = f"api-tests/{PROJECT_IDENTIFIER}/endpoints/{endpoint.id}/manual-coupon-template"
            for entity_type_name, (file_name, content_type, content) in artifact_contents(idp_issue_id, idp_issue_key).items():
                entity_type = AttachmentEntityType[entity_type_name]
                object_name = f"{endpoint_prefix}/{file_name}"
                data = content.encode("utf-8")
                backup_path = artifact_root / object_name
                backup_path.parent.mkdir(parents=True, exist_ok=True)
                backup_path.write_bytes(data)

                attachment = (await session.execute(
                    select(Attachment).where(Attachment.object_name == object_name)
                )).scalar_one_or_none()
                if attachment:
                    attachment.file_size = len(data)
                    attachment.content_type = content_type
                    attachment.description = f"{SUITE_NAME}：{file_name}"
                else:
                    session.add(Attachment(
                        entity_type=entity_type,
                        entity_id=endpoint.id,
                        project_id=project.id,
                        file_name=file_name,
                        file_size=len(data),
                        content_type=content_type,
                        object_name=object_name,
                        description=f"{SUITE_NAME}：{file_name}",
                        created_by="manual_api_test_publisher",
                    ))

        await session.commit()
        stored_artifacts = (await session.execute(
            select(Attachment.entity_type, Attachment.file_name).where(Attachment.entity_id == create_endpoint.id)
        )).all()
        print(json.dumps({
            "project": PROJECT_IDENTIFIER,
            "create_endpoint_id": str(create_endpoint.id),
            "list_endpoint_id": str(endpoints["GET"].id),
            "api_test_id": str(api_test.id),
            "run_id": str(run.id),
            "artifacts": list(artifact_contents(idp_issue_id, idp_issue_key).keys()),
            "stored_artifacts": [
                {"type": entity_type.name, "file_name": file_name}
                for entity_type, file_name in stored_artifacts
            ],
        }, ensure_ascii=False))

    await engine.dispose()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--idp-issue-id", type=int)
    parser.add_argument("--idp-issue-key")
    args = parser.parse_args()
    asyncio.run(main(args.idp_issue_id, args.idp_issue_key))
