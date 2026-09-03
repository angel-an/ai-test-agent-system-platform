"""
需求用例与 API 可执行资产的最小闭环服务。

本服务只做一件事：基于已解析入库的 APIEndpoint，生成可追溯的
TestCase + APITest 脚本，并建立三方关联。复杂的 AI 需求理解仍留在
现有 testcase agent 中，后续只需要调用这个服务即可补齐 API 内容。
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config.minio_client import MinIOClient
from app.config.settings import settings
from app.models.api_endpoint import APIEndpoint
from app.models.api_test import APITest
from app.models.attachment import Attachment, AttachmentEntityType
from app.models.project import Project
from app.models.test_case import TestCase, TestStep
from app.models.test_run import TestRun, TestRunScriptJob, TestRunTestCase
from app.repositories.api_test_repo import APITestRepository
from app.repositories.test_case_repo import TestCaseRepository
from app.repositories.test_run_repo import TestRunRepository
from app.schemas.enums import (
    AutomationStatus,
    ExecutionMode,
    JobStatus,
    Priority,
    ScriptType,
    TestCaseState,
    TestCaseTemplate,
    TestCaseType,
    TestResultStatus,
    TestRunState,
)
from app.utils.identifier import generate_test_case_identifier


class APICaseLinkService:
    """把 APIEndpoint 转换为可执行测试资产。"""

    def __init__(self, session: AsyncSession):
        self.session = session
        self.test_case_repo = TestCaseRepository(session)
        self.api_test_repo = APITestRepository(session)
        self.test_run_repo = TestRunRepository(session)

    async def create_minimal_loop(
        self,
        project_identifier: str,
        endpoint_ids: list[UUID],
        folder_id: UUID | None = None,
        case_kind: str = "sit",
        base_url: str | None = None,
        create_test_run: bool = False,
        execution_config: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """生成 TestCase、APITest，并可选创建 TestRun。"""
        project = await self._get_project(project_identifier)
        endpoints = await self._load_endpoints(project.id, endpoint_ids)
        if not endpoints:
            raise ValueError("未找到可关联的 API 端点")

        created_cases: list[dict[str, Any]] = []
        created_api_tests: list[dict[str, Any]] = []

        for endpoint in endpoints:
            test_case = await self._get_existing_test_case(
                project=project,
                endpoint=endpoint,
            )
            if test_case:
                await self._ensure_test_case_tags(project.id, test_case, endpoint)
            else:
                test_case = await self._create_test_case(
                    project=project,
                    endpoint=endpoint,
                    folder_id=folder_id,
                    case_kind=case_kind,
                )

            api_test = await self._get_existing_api_test(endpoint, test_case)
            if not api_test:
                api_test = await self._create_api_test_asset(
                    project_identifier=project_identifier,
                    project=project,
                    endpoint=endpoint,
                    test_case=test_case,
                    base_url=base_url,
                )

            await self._link_endpoint(endpoint, test_case, api_test)

            created_cases.append({
                "id": str(test_case.id),
                "identifier": test_case.identifier,
                "name": test_case.name,
                "endpoint_id": str(endpoint.id),
            })
            created_api_tests.append({
                "id": str(api_test.id),
                "identifier": api_test.identifier,
                "name": api_test.name,
                "script_path": api_test.script_path,
                "endpoint_id": str(endpoint.id),
                "test_case_id": str(test_case.id),
            })

        test_run_info = None
        if create_test_run:
            test_run_info = await self._create_test_run(
                project=project,
                api_tests=[await self.session.get(APITest, UUID(item["id"])) for item in created_api_tests],
                test_case_ids=[UUID(item["id"]) for item in created_cases],
                execution_config={
                    **(execution_config or {}),
                    **({"base_url": base_url} if base_url else {}),
                },
            )

        await self.session.commit()

        return {
            "project_identifier": project_identifier,
            "test_cases": created_cases,
            "api_tests": created_api_tests,
            "test_run": test_run_info,
            "message": (
                f"已生成 {len(created_cases)} 条 API 关联测试用例和 "
                f"{len(created_api_tests)} 个可执行 API 测试资产"
            ),
        }

    async def _get_project(self, project_identifier: str) -> Project:
        result = await self.session.execute(
            select(Project).where(Project.identifier == project_identifier)
        )
        project = result.scalar_one_or_none()
        if not project:
            raise ValueError(f"项目不存在: {project_identifier}")
        return project

    async def _load_endpoints(
        self,
        project_id: UUID,
        endpoint_ids: list[UUID],
    ) -> list[APIEndpoint]:
        result = await self.session.execute(
            select(APIEndpoint)
            .where(APIEndpoint.project_id == project_id)
            .where(APIEndpoint.id.in_(endpoint_ids))
            .order_by(APIEndpoint.tag_group, APIEndpoint.sort_order, APIEndpoint.path)
        )
        return list(result.scalars().all())

    async def _get_existing_test_case(
        self,
        project: Project,
        endpoint: APIEndpoint,
    ) -> TestCase | None:
        endpoint_case_ids = []
        for item in endpoint.test_case_ids or []:
            try:
                endpoint_case_ids.append(UUID(str(item)))
            except ValueError:
                continue

        candidates: list[TestCase] = []
        if endpoint_case_ids:
            result = await self.session.execute(
                select(TestCase)
                .where(TestCase.project_id == project.id)
                .where(TestCase.id.in_(endpoint_case_ids))
            )
            candidates.extend(result.scalars().all())

        if not candidates:
            method = endpoint.method.upper()
            result = await self.session.execute(
                select(TestCase)
                .where(TestCase.project_id == project.id)
                .where(TestCase.name == f"API {method} {endpoint.path} 基础契约验证")
                .order_by(TestCase.created_at.asc())
            )
            candidates.extend(result.scalars().all())

        for candidate in candidates:
            custom_fields = candidate.custom_fields or {}
            api_refs = custom_fields.get("api_refs") or []
            api_ref = api_refs[0] if api_refs else {}
            if (
                custom_fields.get("generated_from") == "api_case_minimal_loop"
                and api_ref
                and (
                    api_ref.get("endpoint_id") == str(endpoint.id)
                    or (
                        api_ref.get("method") == endpoint.method.upper()
                        and api_ref.get("path") == endpoint.path
                    )
                )
            ):
                return candidate
        return None

    async def _get_existing_api_test(
        self,
        endpoint: APIEndpoint,
        test_case: TestCase,
    ) -> APITest | None:
        endpoint_api_test_ids = []
        for item in endpoint.api_test_ids or []:
            try:
                endpoint_api_test_ids.append(UUID(str(item)))
            except ValueError:
                continue

        query = (
            select(APITest)
            .where(APITest.project_id == test_case.project_id)
            .where(APITest.test_case_id == test_case.id)
            .where(APITest.generated_by_agent == "api_case_link_service")
            .order_by(APITest.created_at.asc())
        )
        if endpoint_api_test_ids:
            query = query.where(APITest.id.in_(endpoint_api_test_ids))

        result = await self.session.execute(query)
        api_test = result.scalars().first()
        if api_test:
            return api_test

        result = await self.session.execute(
            select(APITest)
            .where(APITest.project_id == test_case.project_id)
            .where(APITest.test_case_id == test_case.id)
            .where(APITest.generated_by_agent == "api_case_link_service")
            .order_by(APITest.created_at.asc())
        )
        return result.scalars().first()

    async def _create_test_case(
        self,
        project: Project,
        endpoint: APIEndpoint,
        folder_id: UUID | None,
        case_kind: str,
    ) -> TestCase:
        identifier = await self._next_test_case_identifier()
        api_ref = self._build_api_ref(endpoint)
        method = endpoint.method.upper()
        name = f"API {method} {endpoint.path} 基础契约验证"

        custom_fields = {
            "channel": "api",
            "api_coverage_status": "linked",
            "executable_type": "api_test",
            "api_refs": [api_ref],
            "generated_from": "api_case_minimal_loop",
        }

        test_case = TestCase(
            project_id=project.id,
            folder_id=folder_id,
            identifier=identifier,
            name=name,
            description=endpoint.summary or endpoint.description or name,
            preconditions="接口文档已解析入库，执行环境已配置 base_url 和必要鉴权信息。",
            priority=Priority.MEDIUM if method == "GET" else Priority.HIGH,
            state=TestCaseState.NEW,
            test_case_type=TestCaseType.FUNCTIONAL,
            template=TestCaseTemplate.TEST_CASE,
            automation_status=AutomationStatus.AUTOMATED,
            custom_fields=custom_fields,
            issues=[],
            created_by=project.created_by,
            module=endpoint.tag_group,
            keyword="正向",
            risk_level="medium" if method == "GET" else "high",
            case_kind=case_kind,
        )
        self.session.add(test_case)
        await self.session.flush()
        await self._ensure_test_case_tags(project.id, test_case, endpoint)

        steps = [
            TestStep(
                test_case_id=test_case.id,
                step_number=1,
                action=f"使用 {method} 请求接口 {endpoint.path}",
                expected_result="接口响应状态码小于 500，并返回可解析响应。",
            ),
            TestStep(
                test_case_id=test_case.id,
                step_number=2,
                action="校验响应状态和基础响应结构",
                expected_result="响应符合接口契约；服务端未发生 5xx 错误。",
            ),
        ]
        self.session.add_all(steps)
        await self.session.flush()
        await self.session.refresh(test_case)
        return test_case

    async def _ensure_test_case_tags(
        self,
        project_id: UUID,
        test_case: TestCase,
        endpoint: APIEndpoint,
    ) -> None:
        tag_names = ["AI生成", "最小闭环", "API契约"]
        if endpoint.tag_group:
            tag_names.append(endpoint.tag_group)
        for tag_name in tag_names:
            tag = await self.test_case_repo.get_or_create_tag(project_id, tag_name)
            await self.test_case_repo.add_tag_to_test_case(test_case.id, tag)

    async def _next_test_case_identifier(self) -> str:
        for _ in range(10):
            identifier = generate_test_case_identifier()
            if not await self.test_case_repo.identifier_exists(identifier):
                return identifier
        raise ValueError("无法生成唯一的测试用例标识符")

    async def _create_api_test_asset(
        self,
        project_identifier: str,
        project: Project,
        endpoint: APIEndpoint,
        test_case: TestCase,
        base_url: str | None,
    ) -> APITest:
        identifier = await self.api_test_repo.get_next_identifier(project.id)
        object_name = (
            f"api-tests/{project_identifier}/endpoints/{endpoint.id}/"
            f"linked/{uuid4().hex}/test-script.ts"
        )
        script_content = self._generate_playwright_script(endpoint)
        storage_info = self._store_script(object_name, script_content)

        api_test = APITest(
            project_id=project.id,
            folder_id=endpoint.folder_id,
            test_case_id=test_case.id,
            identifier=identifier,
            name=f"{test_case.identifier} - {endpoint.display_name}",
            description=f"由测试用例 {test_case.identifier} 自动生成的 API 执行脚本",
            schema_path=None,
            schema_type="openapi",
            script_path=object_name,
            script_format="playwright",
            script_language="typescript",
            test_config={
                "base_url": base_url,
                "data_profile": "DEFAULT",
                "data_variables": [],
                "endpoint_id": str(endpoint.id),
                "test_case_id": str(test_case.id),
                "storage": storage_info,
            },
            generated_by_agent="api_case_link_service",
            generation_params={"mode": "minimal_loop"},
            total_endpoints=1,
            total_scenarios=1,
        )
        self.session.add(api_test)
        await self.session.flush()

        self.session.add(Attachment(
            entity_type=AttachmentEntityType.API_TEST_SCRIPT,
            entity_id=endpoint.id,
            project_id=project.id,
            file_name="test-script.ts",
            file_size=len(script_content.encode("utf-8")),
            content_type="text/plain",
            object_name=object_name,
            description=f"API 端点 {endpoint.display_name} 的最小闭环测试脚本",
            created_by="api_case_link_service",
        ))
        await self.session.flush()
        await self.session.refresh(api_test)
        return api_test

    def _store_script(self, object_name: str, content: str) -> dict[str, Any]:
        data = content.encode("utf-8")
        backup_path = self._write_local_backup(object_name, data)
        try:
            MinIOClient.upload_bytes(
                object_name=object_name,
                data=data,
                content_type="text/plain",
            )
            return {"minio": True, "local_backup": str(backup_path)}
        except Exception as exc:
            return {
                "minio": False,
                "local_backup": str(backup_path),
                "warning": f"MinIO 上传失败，已使用本地备份兜底: {exc}",
            }

    def _write_local_backup(self, object_name: str, data: bytes) -> Path:
        workspace_root = Path(settings.api_workspace_root).resolve()
        backup_path = workspace_root / "artifacts_backup" / object_name
        backup_path.parent.mkdir(parents=True, exist_ok=True)
        backup_path.write_bytes(data)
        return backup_path

    async def _link_endpoint(
        self,
        endpoint: APIEndpoint,
        test_case: TestCase,
        api_test: APITest,
    ) -> None:
        case_ids = {str(x) for x in (endpoint.test_case_ids or [])}
        api_test_ids = {str(x) for x in (endpoint.api_test_ids or [])}
        case_ids.add(str(test_case.id))
        api_test_ids.add(str(api_test.id))
        endpoint.test_case_ids = sorted(case_ids)
        endpoint.api_test_ids = sorted(api_test_ids)
        endpoint.total_test_cases = len(endpoint.test_case_ids)
        endpoint.updated_at = datetime.utcnow()
        await self.session.flush()

    async def _create_test_run(
        self,
        project: Project,
        api_tests: list[APITest | None],
        test_case_ids: list[UUID],
        execution_config: dict[str, Any],
    ) -> dict[str, Any]:
        test_run = TestRun(
            project_id=project.id,
            identifier=await self.test_run_repo.generate_identifier(project.id),
            name=f"API 最小闭环验证 {datetime.now().strftime('%Y%m%d-%H%M%S')}",
            description="由 API 最小闭环接口自动创建",
            run_state=TestRunState.NEW_RUN,
            test_cases_count=len(test_case_ids),
            execution_mode=ExecutionMode.SEQUENTIAL,
            max_concurrency=1,
        )
        self.session.add(test_run)
        await self.session.flush()

        for test_case_id in test_case_ids:
            self.session.add(TestRunTestCase(
                test_run_id=test_run.id,
                test_case_id=test_case_id,
                latest_status=TestResultStatus.NOT_EXECUTED,
            ))

        jobs = []
        for idx, api_test in enumerate([item for item in api_tests if item], start=1):
            job = TestRunScriptJob(
                test_run_id=test_run.id,
                script_type=ScriptType.API_TEST,
                script_id=api_test.id,
                script_identifier=api_test.identifier,
                script_name=api_test.name,
                execution_order=idx,
                execution_mode=ExecutionMode.SEQUENTIAL,
                status=JobStatus.PENDING,
                max_retries=0,
                execution_config=execution_config,
            )
            self.session.add(job)
            jobs.append(job)

        await self.session.flush()
        return {
            "id": str(test_run.id),
            "identifier": test_run.identifier,
            "script_jobs": [
                {
                    "id": str(job.id),
                    "script_type": job.script_type.value,
                    "script_id": str(job.script_id),
                    "script_identifier": job.script_identifier,
                }
                for job in jobs
            ],
        }

    def _build_api_ref(self, endpoint: APIEndpoint) -> dict[str, Any]:
        return {
            "endpoint_id": str(endpoint.id),
            "method": endpoint.method.upper(),
            "path": endpoint.path,
            "display_name": endpoint.display_name,
            "operation_id": (endpoint.custom_config or {}).get("operation_id"),
            "tag_group": endpoint.tag_group,
            "request_fields": self._extract_request_fields(endpoint.request_body),
            "assertions": [
                {"type": "status_range", "min": 200, "max": 499},
                {"type": "not_status_range", "min": 500, "max": 599},
            ],
        }

    def _extract_request_fields(self, request_body: Any) -> list[str]:
        schema = self._extract_schema(request_body)
        properties = schema.get("properties") if isinstance(schema, dict) else None
        if isinstance(properties, dict):
            return sorted(properties.keys())
        return []

    def _generate_playwright_script(self, endpoint: APIEndpoint) -> str:
        method = endpoint.method.upper()
        path = self._sample_path(endpoint.path)
        payload = self._sample_payload(endpoint.request_body)
        headers = self._sample_headers(endpoint.parameters)
        should_skip_mutation = self._is_mutating_endpoint(method, endpoint.path)
        skip_guard = (
            "test.skip(process.env.ALLOW_MUTATING_API_TESTS !== '1', "
            "'Mutating API test is generated but disabled by default. Set ALLOW_MUTATING_API_TESTS=1 to run.');"
            if should_skip_mutation
            else ""
        )

        options: dict[str, Any] = {}
        if headers:
            options["headers"] = headers
        if method in {"POST", "PUT", "PATCH"}:
            options["data"] = payload

        return f"""import {{ test, expect }} from '@playwright/test';

const BASE_URL = process.env.API_BASE_URL || 'http://localhost:8000';
const AUTH_HEADER = process.env.API_TEST_AUTH_HEADER || 'Authorization';
const TEST_DATA = parseTestData();

function parseTestData(): Record<string, unknown> {{
  const raw = process.env.API_TEST_DATA_JSON;
  if (!raw) return {{}};
  try {{
    const value = JSON.parse(raw);
    return value && typeof value === 'object' ? value : {{}};
  }} catch {{
    throw new Error('API_TEST_DATA_JSON is not valid JSON');
  }}
}}

function resolveTemplate(value: unknown): unknown {{
  if (typeof value === 'string') {{
    return value.replace(/{{{{\\s*([\\w.-]+)\\s*}}}}/g, (_match, key) => {{
      const replacement = TEST_DATA[key];
      return replacement === undefined || replacement === null
        ? '{{{{' + key + '}}}}'
        : String(replacement);
    }});
  }}
  if (Array.isArray(value)) return value.map(resolveTemplate);
  if (value && typeof value === 'object') {{
    return Object.fromEntries(Object.entries(value).map(([key, item]) => [key, resolveTemplate(item)]));
  }}
  return value;
}}

function missingTemplateVariables(value: unknown): string[] {{
  const text = JSON.stringify(value);
  return [...text.matchAll(/{{{{\\s*([\\w.-]+)\\s*}}}}/g)].map((match) => match[1]);
}}

function buildUrl(path: string): string {{
  const normalizedBase = BASE_URL.endsWith('/') ? BASE_URL : BASE_URL + '/';
  return new URL(path.replace(/^\\/+/, ''), normalizedBase).toString();
}}

test('{method} {endpoint.path} basic contract', async ({{ request }}) => {{
  {skip_guard}
  const rawOptions = {json.dumps(options, ensure_ascii=False, indent=4)};
  const missingVariables = missingTemplateVariables(rawOptions)
    .filter((key) => TEST_DATA[key] === undefined || TEST_DATA[key] === null);
  test.skip(missingVariables.length > 0, `Missing API test data: ${{missingVariables.join(', ')}}`);
  const resolvedOptions: any = resolveTemplate(rawOptions);
  if (process.env.API_TEST_AUTHORIZATION) {{
    resolvedOptions.headers = {{
      ...(resolvedOptions.headers || {{}}),
      [AUTH_HEADER]: process.env.API_TEST_AUTHORIZATION,
    }};
  }}
  const response = await request.{method.lower()}(
    buildUrl({json.dumps(path, ensure_ascii=False)}),
    resolvedOptions
  );
  const status = response.status();
  console.log('{method} {endpoint.path} status=', status);
  await test.step(`断言：HTTP 响应状态为 2xx（实际：${{status}}）`, async () => {{
    expect(status).toBeGreaterThanOrEqual(200);
    expect(status).toBeLessThan(300);
  }});
}});
"""

    @staticmethod
    def _is_mutating_endpoint(method: str, path: str) -> bool:
        if method not in {"POST", "PUT", "PATCH", "DELETE"}:
            return False
        normalized_path = path.lower()
        readonly_markers = (
            "/page", "/list", "/query", "/search", "/detail", "/calculate",
            "/check", "/validate",
        )
        return not any(marker in normalized_path for marker in readonly_markers)

    def _sample_path(self, path: str) -> str:
        import re

        return re.sub(r"\{[^{}]+\}", "1", path)

    def _sample_headers(self, parameters: Any) -> dict[str, str]:
        headers: dict[str, str] = {}
        if not isinstance(parameters, list):
            return headers
        for param in parameters:
            if not isinstance(param, dict) or param.get("in") != "header":
                continue
            name = param.get("name")
            if name and param.get("required"):
                headers[name] = str(param.get("default") or "test")
        return headers

    def _sample_payload(self, request_body: Any) -> dict[str, Any]:
        example = self._extract_request_example(request_body)
        if isinstance(example, dict):
            return example
        schema = self._extract_schema(request_body)
        if not schema:
            return {}
        return self._sample_from_schema(schema)

    def _extract_request_example(self, request_body: Any) -> Any:
        if not isinstance(request_body, dict):
            return None
        content = request_body.get("content")
        if not isinstance(content, dict):
            return None
        for media in content.values():
            if isinstance(media, dict) and "example" in media:
                return media["example"]
        return None

    def _extract_schema(self, request_body: Any) -> dict[str, Any]:
        if not isinstance(request_body, dict):
            return {}
        content = request_body.get("content")
        if isinstance(content, dict):
            for media_type in ("application/json", "application/*+json"):
                media = content.get(media_type)
                if isinstance(media, dict) and isinstance(media.get("schema"), dict):
                    return media["schema"]
            for media in content.values():
                if isinstance(media, dict) and isinstance(media.get("schema"), dict):
                    return media["schema"]
        if isinstance(request_body.get("schema"), dict):
            return request_body["schema"]
        return {}

    def _sample_from_schema(self, schema: dict[str, Any]) -> Any:
        if "example" in schema:
            return schema["example"]
        if "default" in schema:
            return schema["default"]
        schema_type = schema.get("type")
        if schema_type == "object" or "properties" in schema:
            result: dict[str, Any] = {}
            properties = schema.get("properties") or {}
            required = set(schema.get("required") or properties.keys())
            for name, prop_schema in properties.items():
                if name in required:
                    result[name] = self._sample_from_schema(prop_schema or {})
            return result
        if schema_type == "array":
            return [self._sample_from_schema(schema.get("items") or {})]
        if schema_type in {"integer", "number"}:
            return 1
        if schema_type == "boolean":
            return True
        return "test"
