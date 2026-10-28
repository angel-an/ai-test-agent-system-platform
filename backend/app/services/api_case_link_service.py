"""
需求用例与 API 可执行资产的最小闭环服务。

本服务只做一件事：基于已解析入库的 APIEndpoint，生成可追溯的
TestCase + APITest 脚本，并建立三方关联。复杂的 AI 需求理解仍留在
现有 testcase agent 中，后续只需要调用这个服务即可补齐 API 内容。
"""

from __future__ import annotations

import inspect
import json
from datetime import datetime, timezone
from functools import lru_cache
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
from app.services.api_pagination_contracts import (
    load_contract_core,
    resolve_pagination_contract,
)
from app.services.api_response_envelope import resolve_envelope_field
from app.services.api_script_template import (
    API_SCRIPT_TEMPLATE_REV,
    fingerprint_line,
    template_digest,
)
from app.utils.identifier import generate_test_case_identifier


def _unverified_reason(contract: Any) -> str:
    """``provenance=unverified`` 的显式跳过理由。

    参数没确认过的接口，"测出来通过"是没有意义的结论 —— 比不跑更危险。
    因此生成 ``test.skip(true, ...)``：报告里显示 skipped，绝不伪装成通过。
    """
    return (
        f"分页参数未确认（provenance=unverified）：{contract.note}"
        " —— 该接口当前无法给出可信结论，显式跳过（不静默通过）"
    )


#: 组成脚本产物的方法集合：模板本体 + 样本值推导 + 分页契约渲染。
#: digest 覆盖这些源码，任何一处改动都会让历史产物判为 ``digest_mismatch``。
_TEMPLATE_SOURCE_METHODS = (
    "_generate_playwright_script",
    "_render_pagination_block",
    "_sample_path",
    "_sample_payload",
    "_sample_headers",
    "_sample_query_params",
    "_is_mutating_endpoint",
)


@lru_cache(maxsize=1)
def script_template_digest() -> str:
    """当前模板源码摘要（12 位）。进程内缓存：模板随代码发布变化，不需要运行期热更。"""
    chunks = [
        inspect.getsource(getattr(APICaseLinkService, name))
        for name in _TEMPLATE_SOURCE_METHODS
    ]
    return template_digest(chunks)


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
        script_content = self.render_script(endpoint)
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
            generation_params={
                "mode": "minimal_loop",
                # 版本戳：P2 的判据来源。没有它，"这条脚本按哪版模板生成"无从判断，
                # 模板改进后也无法识别哪些资产需要重建（历史上正是这样静默腐化的）。
                "script_template": self.script_stamp(),
            },
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
        endpoint.updated_at = datetime.now(timezone.utc)
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

    @staticmethod
    def script_stamp() -> dict[str, Any]:
        """当前模板的版本戳，写进 ``generation_params.script_template``。"""
        return {"rev": API_SCRIPT_TEMPLATE_REV, "digest": script_template_digest()}

    def render_script(self, endpoint: APIEndpoint) -> str:
        """渲染可执行脚本（首行带版本指纹）。

        指纹行是**纯注释**：执行器把这段文本当 TS 直接跑，不能带任何执行语义。
        生成期与重建期都走这一个入口，避免两处各拼一遍正文。
        """
        body = self._generate_playwright_script(endpoint)
        return fingerprint_line(digest=script_template_digest()) + "\n" + body

    def _generate_playwright_script(self, endpoint: APIEndpoint) -> str:
        method = endpoint.method.upper()
        path = self._sample_path(endpoint.path, endpoint.parameters)
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
        # 必填 query 参数（一期）：此前完全丢弃，带参接口实际打的是残缺 URL
        # （404 / 200+data:null 都可能，后者在信封断言下是假通过）。
        query_params = self._sample_query_params(endpoint.parameters)
        if query_params:
            options["params"] = query_params
        if method in {"POST", "PUT", "PATCH"}:
            options["data"] = payload

        # ---- 分页契约（P5 / DEFECT-002）----
        pagination = resolve_pagination_contract(endpoint.path)
        # ---- 响应信封字段（F1 复核修复）----
        # 不再写死 resultCode：按接口数据解析（custom_config 显式声明 → 已导入 responses 推断），
        # 拿不到就**不硬判**（降级为只判 HTTP + 可解析，并输出机读标记）。
        # 依据：1892 个 APIEndpoint 分布在 14 个项目，非 PR-2 项目声明的是 `Code`，
        # 写死 resultCode 会让这些 SUT 的**成功响应**被判失败（已复现）。
        envelope_field = resolve_envelope_field(endpoint)
        if pagination is not None and getattr(pagination, "envelope_field", ""):
            # 契约表已按接口声明了该 SUT 的信封字段名 —— 它比接口推断更具体，优先采用。
            envelope_field = pagination.envelope_field
        wants_contract = pagination is not None and pagination.provenance != "unverified"
        core = load_contract_core() if wants_contract else None
        # 拿不到核心段 → 降级为"仅信封断言"。**不静默**：脚本里留注解，日志里有 error。
        # 不做成硬失败，因为降级只是"少断言一层"，并非"给出错误结论"。
        contract_degraded = wants_contract and core is None
        contract_active = wants_contract and core is not None
        unverified_guard = (
            f"test.skip(true, {json.dumps(_unverified_reason(pagination), ensure_ascii=False)});"
            if pagination is not None and pagination.provenance == "unverified"
            else ""
        )
        if contract_active and pagination is not None:
            target_url_expr = f"buildPageUrl(buildUrl({json.dumps(path, ensure_ascii=False)}), 1)"
        else:
            target_url_expr = f"buildUrl({json.dumps(path, ensure_ascii=False)})"
        # testInfo 必须**无条件**注入：信封降级分支（ENVELOPE_FIELD 为 null 时 push
        # `envelope_check_skipped` 注解）与 `if (ENVELOPE_FIELD) {{...}} else {{...}}` 一样
        # 是**静态文本**，任何产物里都引用了 testInfo。漏注入会让 envelope_field 已声明的
        # 端点编译出 TS2304（实测被 test_rendered_scripts_compile 捕获）。
        test_params = "{ request }, testInfo"
        pagination_helper = (
            f"""
const PAGE_NUM_PARAM = {json.dumps(pagination.page_num_param)};
const PAGE_SIZE_PARAM = {json.dumps(pagination.page_size_param)};
const PAGE_SIZE = {pagination.page_size};
const OUT_OF_RANGE_PAGE = {pagination.out_of_range_page};

function buildPageUrl(base: string, page: number): string {{
  const url = new URL(base);
  url.searchParams.set(PAGE_NUM_PARAM, String(page));
  url.searchParams.set(PAGE_SIZE_PARAM, String(PAGE_SIZE));
  return url.toString();
}}
"""
            if contract_active and pagination is not None
            else ""
        )
        pagination_block = (
            self._render_pagination_block(
                method=method, path=path, contract=pagination, core=core or "",
            )
            if contract_active and pagination is not None
            else ""
        )
        if contract_degraded and pagination is not None:
            pagination_block = (
                "\n  // ⚠️ 该接口声明了分页契约（provenance="
                f"{pagination.provenance}），但契约核心段未能加载 —— "
                "本次**只**执行信封断言，分页结构未被验证。\n"
                "  testInfo.annotations.push({ type: 'pagination_contract_skipped', "
                "description: '契约核心段未加载：分页结构未被验证（不是通过）' });\n"
            )

        envelope_field_json = json.dumps(envelope_field, ensure_ascii=False)

        sample_note = ""
        if query_params or "{" in (endpoint.path or ""):
            sample_note = (
                "\n// 样本值说明：路径参数优先按接口声明取样本值"
                "（default → 枚举首值 → 类型样本值，声明缺失用 1）；"
                "query 只传必填项 —— 均不代表业务真实数据，"
                "本用例验证的是接口契约与参数传递，不是业务结果正确性。"
                "{{var}} 形态的路径段可经 API_TEST_DATA_JSON 运行期覆盖。"
            )

        return f"""import {{ test, expect }} from '@playwright/test';{sample_note}

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
  // {{{{var}}}} 形态的路径段走 TEST_DATA 运行期覆盖（与 rawOptions 同一套解析）；
  // 单大括号 {{id}} 在生成期已被样本值替换，不会进入这里。
  // String()：resolveTemplate 的返回类型是 unknown，直接 .replace 会 TS2339。
  return new URL(String(resolveTemplate(path)).replace(/^\\/+/, ''), normalizedBase).toString();
}}
{pagination_helper}
test('{method} {endpoint.path} basic contract', async ({test_params}) => {{
  {unverified_guard}
  {skip_guard}
  const rawOptions = {json.dumps(options, ensure_ascii=False, indent=4)};
  // 路径里的 {{{{var}}}} 与 rawOptions 共用同一套缺失检查：缺 TEST_DATA 时跳过
  // 并给出变量名，而不是把字面 {{{{var}}}} 打进 URL 制造一个必然 404。
  const missingVariables = missingTemplateVariables(rawOptions)
    .concat(missingTemplateVariables({json.dumps(path, ensure_ascii=False)}))
    .filter((key) => TEST_DATA[key] === undefined || TEST_DATA[key] === null);
  test.skip(missingVariables.length > 0, `Missing API test data: ${{missingVariables.join(', ')}}`);
  const resolvedOptions: any = resolveTemplate(rawOptions);
  if (process.env.API_TEST_AUTHORIZATION) {{
    resolvedOptions.headers = {{
      ...(resolvedOptions.headers || {{}}),
      [AUTH_HEADER]: process.env.API_TEST_AUTHORIZATION,
    }};
  }}
  // 额外请求头（JSON，由 data_profile 注入，只进子进程、不落库）。
  // 真实动机：小杨生煎网关按 referer 识别实例，不带 referer 会返回
  // "SaasUserInfoInterceptor 应用识别失败"（HTTP 200）—— 看着像接口问题，
  // 实为平台没带够头，归因错向。配置写坏时**报错**而不是静默少带头。
  if (process.env.API_TEST_EXTRA_HEADERS) {{
    let extraHeaders: Record<string, string>;
    try {{
      extraHeaders = JSON.parse(process.env.API_TEST_EXTRA_HEADERS);
    }} catch {{
      throw new Error('PLATFORM_CONFIG_ERROR API_TEST_EXTRA_HEADERS 不是合法 JSON'
        + `（这是平台侧配置问题，不是被测接口问题）：${{process.env.API_TEST_EXTRA_HEADERS}}`);
    }}
    resolvedOptions.headers = {{ ...(resolvedOptions.headers || {{}}), ...extraHeaders }};
  }}
  const response = await request.{method.lower()}(
    {target_url_expr},
    resolvedOptions
  );
  const status = response.status();
  console.log('{method} {endpoint.path} status=', status);
  await test.step(`断言：HTTP 响应状态为 2xx（实际：${{status}}）`, async () => {{
    expect(status).toBeGreaterThanOrEqual(200);
    expect(status).toBeLessThan(300);
  }});

  // ===== 信封断言（ENVELOPE）=====
  // 动机（2026-09-30 实测）：小杨生煎的 SUT 是**信封式响应**——业务成功/失败 HTTP 恒为 200，
  // 靠响应体业务码区分（实测：过期令牌也返回 {{"resultCode":401,"resultMsg":"no login"}} 且 HTTP 200）。
  // 因此**只看 HTTP 2xx 等于没有断言**：认证/业务失败会被误判为"通过"。
  // 信封字段名**不写死**（复核 F1）：按接口数据解析（custom_config.response_envelope_field →
  // 已导入 responses 推断）。写死会把一个业务的响应结构套到别的业务上，实测会让 MX 系
  // SUT 的**成功响应**被判失败。
  const ENVELOPE_FIELD: string | null = {envelope_field_json};
  const rawBody = await response.text().catch(() => '');
  let envelope: any = null;
  try {{
    envelope = rawBody ? JSON.parse(rawBody) : null;
  }} catch {{
    envelope = null;
  }}
  await test.step(`断言：响应信封可解析（实际：${{envelope === null ? '非 JSON' : 'JSON'}}）`, async () => {{
    expect(
      envelope,
      `响应体不是合法 JSON（前 300 字符）：${{rawBody.slice(0, 300)}}`,
    ).not.toBeNull();
  }});
  const envelopeCode =
    ENVELOPE_FIELD && envelope && envelope[ENVELOPE_FIELD] !== undefined && envelope[ENVELOPE_FIELD] !== null
      ? String(envelope[ENVELOPE_FIELD])
      : '';
  if (ENVELOPE_FIELD) {{
    await test.step(`断言：信封含 ${{ENVELOPE_FIELD}} 字段（实际：${{envelopeCode || '<缺失>'}}）`, async () => {{
      expect(
        envelopeCode,
        `响应信封缺少 ${{ENVELOPE_FIELD}} 字段，无法判定业务结果（响应体前 300 字符）：${{rawBody.slice(0, 300)}}`,
      ).not.toBe('');
    }});
    await test.step(`断言：业务结果为成功（${{ENVELOPE_FIELD}}=0，实际：${{envelopeCode}}）`, async () => {{
      expect(
        envelopeCode,
        `AUTH_OR_BUSINESS_ENVELOPE ${{ENVELOPE_FIELD}}=${{envelopeCode}} resultMsg=${{envelope && (envelope.resultMsg || envelope.Message)}}`
          + '（凭据/权限或业务失败，不是 HTTP 层问题；修复凭据或业务流程后应自然通过）',
      ).toBe('0');
    }});
  }} else {{
    // 未声明信封形状 → 只判"可解析"就够，**不硬判业务结果**。
    // 但必须**不静默**：与 P5 分页契约降级**同一口径** —— 在报告里留注解（黄灯），
    // 否则"少判一层"会表现为绿灯假通过（报告里看不出这轮根本没判业务结果）。
    console.log('{endpoint.path} ENVELOPE_CHECK_SKIPPED ' + JSON.stringify({{
      reason: 'envelope_field_not_declared',
      hint: '在 APIEndpoint.custom_config.response_envelope_field 声明该 SUT 的信封字段名后即可启用业务结果断言',
      raw: rawBody.slice(0, 300),
    }}));
    testInfo.annotations.push({{
      type: 'envelope_check_skipped',
      description: '未声明信封字段（custom_config.response_envelope_field）：'
        + '本轮**只校验 HTTP 与响应可解析，未判定业务结果**（不是通过）。'
        + '在 APIEndpoint.custom_config.response_envelope_field 声明该 SUT 的信封字段名后可启用业务结果断言。',
    }});
  }}
{pagination_block}}});
"""

    def _render_pagination_block(
        self,
        *,
        method: str,
        path: str,
        contract: Any,
        core: str,
    ) -> str:
        """渲染分页契约断言段（第 2 层正常页 + 第 3 层越界页）。

        验收要求"平台跑出的结论与手跑矩阵
        （``p5_pagination_contract_matrix.spec.ts``）一致"，因此这里的判定调用
        与矩阵**逐字对齐**：同样的 ``checkPaginationContract``、
        同样的 ``reportContractResult``，且核心段是**抽取**来的同源代码。

        比矩阵更严的一处（有意为之）：越界页也先判 ``resultCode``。
        否则"越界页 + 凭据失效"会得到一个 ``data:null``，被 known_issue 例外
        吸收成"已知问题"—— 那等于用 DEFECT-002 掩盖另一个问题，比不测更危险。
        """
        known_expr = "null"
        if contract.expect_out_of_range == "known_issue":
            known_expr = json.dumps(
                {"id": contract.known_issue_id, "until": contract.known_issue_until,
                 "note": contract.note},
                ensure_ascii=False,
            )
        label = f"{method} {path}"
        core_indented = "\n".join(
            ("  " + line) if line.strip() else line for line in core.splitlines()
        )
        return f"""
  // ===== 分页契约断言（P5 / DEFECT-002）=====
  // 判定逻辑按标记抽取自 workspace/api/_lib/response_contract.ts（与手跑矩阵同源，
  // 不另写一份实现——两份实现迟早漂移，且漂移时表现为"某边悄悄变宽"，正是漏检藏身处）。
  // 配置：provenance={contract.provenance} 越界页期望={contract.expect_out_of_range}
  // 备注：{contract.note}
{core_indented}

  // 第 2 层：正常页结构（data 为对象 / list 为数组 / total·pages 为数字）
  const normalContract = checkPaginationContract(envelope, {{ pageNum: 1 }});
  if (!normalContract.ok) {{
    console.log('{label} P5_CONTRACT ' + JSON.stringify({{
      stage: 'normal_page', ok: normalContract.ok, failures: normalContract.failures,
      summary: normalContract.summary,
    }}));
    testInfo.annotations.push({{ type: 'contract_failure', description: `正常页 ${{normalContract.summary}}` }});
    throw new Error(`CONTRACT_FAILURE 正常页 {label}：${{normalContract.summary}}｜raw=${{normalContract.raw}}`);
  }}

  // 第 3 层：越界页行为（越界仍须是完整空结构，否则要么是缺陷、要么是已声明的 known_issue）
  const oorResponse = await request.{method.lower()}(
    buildPageUrl(buildUrl({json.dumps(path, ensure_ascii=False)}), OUT_OF_RANGE_PAGE),
    resolvedOptions
  );
  const oorRaw = await oorResponse.text().catch(() => '');
  let oorEnvelope: any = null;
  try {{
    oorEnvelope = oorRaw ? JSON.parse(oorRaw) : null;
  }} catch {{
    oorEnvelope = null;
  }}
  const oorCode =
    ENVELOPE_FIELD && oorEnvelope
      && oorEnvelope[ENVELOPE_FIELD] !== undefined && oorEnvelope[ENVELOPE_FIELD] !== null
      ? String(oorEnvelope[ENVELOPE_FIELD])
      : '';
  // 越界页同样先判信封：**必须** 业务码=0 才继续。
  // 只判"字段存在"是不够的（实测漏过）：越界页返回 401 时它也是 data:null，
  // 会被 known_issue 例外吸收成"已知问题" —— 那等于用 DEFECT-002 掩盖另一个问题。
  await test.step(`断言：越界页信封有效（实际：http=${{oorResponse.status()}} ${{ENVELOPE_FIELD || '未声明'}}=${{oorCode || '<缺失>'}}）`, async () => {{
    expect(
      oorEnvelope,
      `越界页响应不是合法 JSON（前 300 字符）：${{oorRaw.slice(0, 300)}}`,
    ).not.toBeNull();
    if (ENVELOPE_FIELD) {{
      expect(
        oorCode,
        `AUTH_OR_BUSINESS_ENVELOPE 越界页 ${{ENVELOPE_FIELD}}=${{oorCode || '<缺失>'}}`
          + `（凭据/权限问题，不能算作分页契约结论；响应体前 300 字符）：${{oorRaw.slice(0, 300)}}`,
      ).toBe('0');
    }}
  }});
  const oorContract = checkPaginationContract(
    oorEnvelope,
    {{ pageNum: OUT_OF_RANGE_PAGE }},
    {known_expr},
  );
  console.log('{label} P5_CONTRACT ' + JSON.stringify({{
    stage: 'out_of_range',
    provenance: {json.dumps(contract.provenance)},
    expect_out_of_range: {json.dumps(contract.expect_out_of_range)},
    ok: oorContract.ok, known_issue: oorContract.knownIssue,
    known_issue_id: oorContract.knownIssueId, failures: oorContract.failures,
    summary: oorContract.summary, raw: oorContract.raw,
  }}));
  reportContractResult(oorContract, {{
    annotate: (type, description) => testInfo.annotations.push({{ type, description }}),
    attach: (name, body) =>
      testInfo.attach(`{label}-${{name}}`, {{ body, contentType: 'application/json' }}),
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

    def _sample_path(self, path: str, parameters: Any = None) -> str:
        """路径参数取样本值：优先用 endpoint.parameters 里 ``in: path`` 的声明
        （default → 枚举首值 → 类型样本值），声明缺失时回落 "1"。

        此前无条件 ``re.sub(r"\\{[^{}]+\\}", "1", path)``：分页类接口声明了
        page/pageSize 语义的路径段（如 ``/page/{pageNo}``）也被打成 1，
        和 query 参数同一套样本逻辑却不共用。

        ``{{var}}``（双大括号）是运行期变量，保留原样交给脚本里的
        ``buildUrl``/``resolveTemplate`` 按 ``API_TEST_DATA_JSON`` 解析，不参与采样。
        """
        import re

        declared: dict[str, str] = {}
        if isinstance(parameters, list):
            for param in parameters:
                if (
                    isinstance(param, dict)
                    and param.get("in") == "path"
                    and param.get("name")
                ):
                    declared[str(param["name"])] = self._sample_param_value(param)

        def _replace(match: "re.Match[str]") -> str:
            name = match.group(1).strip()
            return declared.get(name, "1")

        # 先把 {{var}} 摘出来保护（sentinel 不含大括号，采样正则碰不到它），
        # 单大括号段采样完再原样放回。
        tokens: dict[str, str] = {}

        def _protect(match: "re.Match[str]") -> str:
            key = f"\x00{len(tokens)}\x00"
            tokens[key] = match.group(0)
            return key

        protected = re.sub(r"\{\{[^{}]+\}\}", _protect, path)
        protected = re.sub(r"\{([^{}]+)\}", _replace, protected)
        for key, original in tokens.items():
            protected = protected.replace(key, original)
        return protected

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

    def _sample_query_params(self, parameters: Any) -> dict[str, str]:
        """必填 query 参数取样本值：**只传 required**。

        可选参数刻意不传 —— 样本值撞上业务校验时，多传一个参数会凭空制造失败，
        而漏传可选参数最多让返回值范围变宽，不影响契约判定。
        """
        params: dict[str, str] = {}
        if not isinstance(parameters, list):
            return params
        for param in parameters:
            if not isinstance(param, dict) or param.get("in") != "query":
                continue
            name = param.get("name")
            if not name or not param.get("required"):
                continue
            params[str(name)] = self._sample_param_value(param)
        return params

    @staticmethod
    def _sample_param_value(param: dict[str, Any]) -> str:
        """样本值优先级：default → 枚举首值 → 类型样本值（数字 1 / 布尔 false / 文本 test）。"""
        default = param.get("default")
        if default not in (None, ""):
            return str(default)
        enum = param.get("enum")
        if isinstance(enum, list) and enum:
            return str(enum[0])
        schema = param.get("schema") if isinstance(param.get("schema"), dict) else {}
        ptype = str(param.get("type") or schema.get("type") or "").lower()
        if ptype in {"integer", "number"}:
            return "1"
        if ptype == "boolean":
            return "false"
        return "test"

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
