"""需求功能用例与 Web 可执行资产的最小闭环服务。"""

from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config.minio_client import MinIOClient
from app.config.settings import settings
from app.models.attachment import Attachment, AttachmentEntityType
from app.models.project import Project
from app.models.test_case import TestCase
from app.models.test_run import TestRun, TestRunScriptJob, TestRunTestCase
from app.models.web_path_template import ProjectWebPathTemplate
from app.models.web_test import WebTest
from app.repositories.test_case_repo import TestCaseRepository
from app.repositories.test_run_repo import TestRunRepository
from app.schemas.enums import (
    AutomationStatus,
    ExecutionMode,
    JobStatus,
    ScriptType,
    TestResultStatus,
    TestRunState,
)


class WebCaseLinkService:
    """把已生成的功能测试用例转换为一条可执行 Web 冒烟脚本。"""

    DEFAULT_PLACEHOLDER_BASE_URL = "https://example.invalid/console/login"
    DEFAULT_LOGIN_PROFILE = "WEB_TEST_DEFAULT"
    DEFAULT_WEB_VALIDATION_PATH = [
        "登录系统入口（由项目路径配置或 WEB_TEST_BASE_URL 指定）",
        "进入目标业务入口",
        "打开目标功能菜单",
        "执行用例描述中的验证操作",
    ]

    def __init__(self, session: AsyncSession):
        self.session = session
        self.test_case_repo = TestCaseRepository(session)
        self.test_run_repo = TestRunRepository(session)

    async def create_minimal_loop(
        self,
        project_identifier: str,
        test_case_ids: list[UUID],
        folder_id: UUID | None = None,
        base_url: str | None = None,
        test_run_identifier: str | None = None,
        create_test_run: bool = False,
        execution_config: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """生成/复用 WebTest，并可追加到 TestRun。"""
        project = await self._get_project(project_identifier)
        test_case = await self._select_test_case(project.id, test_case_ids)
        if not test_case:
            raise ValueError("未找到可关联的功能测试用例")

        path_config = await self._resolve_path_config(project, test_case, base_url)
        resolved_base_url = path_config["base_url"]
        navigation_path = path_config["navigation_path"]

        web_test = await self._get_existing_web_test(test_case)
        if not web_test:
            web_test = await self._create_web_test_asset(
                project_identifier=project_identifier,
                project=project,
                test_case=test_case,
                folder_id=folder_id,
                base_url=resolved_base_url,
                navigation_path=navigation_path,
                path_config=path_config,
            )
        else:
            self._refresh_web_test_asset(
                web_test=web_test,
                test_case=test_case,
                base_url=resolved_base_url,
                navigation_path=navigation_path,
                path_config=path_config,
            )

        test_case.automation_status = AutomationStatus.AUTOMATED
        existing_custom_fields = test_case.custom_fields or {}
        test_case.custom_fields = {
            **existing_custom_fields,
            "channel": "web",
            "executable_type": "web_test",
            "generated_from": existing_custom_fields.get("generated_from") or "document_ai",
            "web_test_id": str(web_test.id),
            "web_validation_path": navigation_path,
            "web_path_template_id": path_config.get("template_id"),
            "web_path_match_status": "matched" if path_config["matched"] else "pending_confirmation",
            "web_path_match_score": path_config.get("score", 0),
            "web_path_base_url_source": path_config.get("base_url_source"),
            "login_profile": path_config.get("login_profile"),
        }
        test_case.description = self._merge_web_path_description(
            test_case.description,
            navigation_path,
        )
        await self._ensure_test_case_tags(project.id, test_case)

        test_run_info = None
        if test_run_identifier:
            test_run_info = await self._append_to_test_run(
                project=project,
                test_run_identifier=test_run_identifier,
                test_case=test_case,
                web_test=web_test,
                execution_config=execution_config or {},
            )
        elif create_test_run:
            test_run_info = await self._create_test_run(
                project=project,
                test_case=test_case,
                web_test=web_test,
                execution_config=execution_config or {},
            )

        await self.session.commit()
        return {
            "project_identifier": project_identifier,
            "test_cases": [{
                "id": str(test_case.id),
                "identifier": test_case.identifier,
                "name": test_case.name,
            }],
            "web_tests": [{
                "id": str(web_test.id),
                "identifier": web_test.identifier,
                "name": web_test.name,
                "script_path": web_test.script_path,
                "test_case_id": str(test_case.id),
                "web_path_match_status": "matched" if path_config["matched"] else "pending_confirmation",
                "web_path_template_id": path_config.get("template_id"),
            }],
            "test_run": test_run_info,
            "message": "已创建 1 条 Web 冒烟可执行资产",
        }

    async def _get_project(self, project_identifier: str) -> Project:
        result = await self.session.execute(
            select(Project).where(Project.identifier == project_identifier)
        )
        project = result.scalar_one_or_none()
        if not project:
            raise ValueError(f"项目不存在: {project_identifier}")
        return project

    async def _select_test_case(
        self,
        project_id: UUID,
        test_case_ids: list[UUID],
    ) -> TestCase | None:
        result = await self.session.execute(
            select(TestCase)
            .where(TestCase.project_id == project_id)
            .where(TestCase.id.in_(test_case_ids))
        )
        cases = list(result.scalars().all())
        if not cases:
            return None

        def score(case: TestCase) -> int:
            text = f"{case.name} {case.module or ''} {case.description or ''}"
            keywords = ["储值免单", "新建活动", "营销活动", "活动管理", "B端"]
            return sum(1 for keyword in keywords if keyword in text)

        return sorted(cases, key=lambda item: (-score(item), item.created_at))[0]

    async def _get_existing_web_test(self, test_case: TestCase) -> WebTest | None:
        result = await self.session.execute(
            select(WebTest)
            .where(WebTest.project_id == test_case.project_id)
            .where(WebTest.test_case_id == test_case.id)
            .where(WebTest.generated_by_agent == "web_case_link_service")
            .order_by(WebTest.created_at.asc())
        )
        return result.scalars().first()

    async def _resolve_path_config(
        self,
        project: Project,
        test_case: TestCase,
        requested_base_url: str | None,
    ) -> dict[str, Any]:
        template, score = await self._match_path_template(project.id, test_case)
        matched = template is not None and score > 0
        navigation_path = (
            list(template.navigation_path or [])
            if matched and template and template.navigation_path
            else list(self.DEFAULT_WEB_VALIDATION_PATH)
        )
        if requested_base_url:
            base_url = requested_base_url
            base_url_source = "request"
        elif matched and template and template.base_url:
            base_url = template.base_url
            base_url_source = "template"
        else:
            base_url = self.DEFAULT_PLACEHOLDER_BASE_URL
            base_url_source = "placeholder"

        return {
            "matched": matched,
            "score": score,
            "template_id": str(template.id) if matched and template else None,
            "template_name": template.name if matched and template else None,
            "base_url": base_url,
            "base_url_source": base_url_source,
            "login_profile": (
                template.login_profile
                if matched and template and template.login_profile
                else self.DEFAULT_LOGIN_PROFILE
            ),
            "navigation_path": navigation_path,
        }

    async def _match_path_template(
        self,
        project_id: UUID,
        test_case: TestCase,
    ) -> tuple[ProjectWebPathTemplate | None, int]:
        result = await self.session.execute(
            select(ProjectWebPathTemplate)
            .where(ProjectWebPathTemplate.project_id == project_id)
            .where(ProjectWebPathTemplate.status == "active")
        )
        candidates = list(result.scalars().all())
        if not candidates:
            return None, 0

        text = self._build_case_match_text(test_case)
        scored = sorted(
            [(self._score_path_template(template, test_case, text), template) for template in candidates],
            key=lambda pair: pair[0],
            reverse=True,
        )
        score, template = scored[0]
        if score <= 0:
            return None, 0
        return template, score

    def _build_case_match_text(self, test_case: TestCase) -> str:
        custom_fields = test_case.custom_fields or {}
        custom_text = json.dumps(custom_fields, ensure_ascii=False)
        return " ".join([
            test_case.identifier or "",
            test_case.name or "",
            test_case.module or "",
            test_case.case_kind or "",
            test_case.description or "",
            test_case.preconditions or "",
            custom_text,
        ])

    def _score_path_template(
        self,
        template: ProjectWebPathTemplate,
        test_case: TestCase,
        text: str,
    ) -> int:
        score = 0
        field_values = {
            "side": "",
            "module": test_case.module or "",
            "business_type": "",
            "action": "",
        }
        custom_fields = test_case.custom_fields or {}
        for key in ["side", "business_type", "action"]:
            value = custom_fields.get(key) or custom_fields.get(f"web_{key}") or ""
            field_values[key] = str(value)

        for attr, weight in [
            ("side", 3),
            ("module", 4),
            ("business_type", 5),
            ("action", 3),
        ]:
            expected = getattr(template, attr) or ""
            actual = field_values.get(attr) or ""
            if expected and actual and expected in actual:
                score += weight
            elif expected and expected in text:
                score += max(1, weight - 1)

        for keyword in template.match_keywords or []:
            if keyword and keyword in text:
                score += 2
        return score

    def _merge_web_path_description(
        self,
        description: str | None,
        navigation_path: list[str],
    ) -> str:
        base_description = re.sub(
            r"(\r?\n){0,2}Web验证路径：.*?(?=(\r?\n){2,}|\Z)",
            "",
            description or "",
            flags=re.S,
        ).strip()
        path_description = "Web验证路径：" + " -> ".join(navigation_path)
        return f"{base_description}\n\n{path_description}" if base_description else path_description

    async def _create_web_test_asset(
        self,
        project_identifier: str,
        project: Project,
        test_case: TestCase,
        folder_id: UUID | None,
        base_url: str,
        navigation_path: list[str],
        path_config: dict[str, Any],
    ) -> WebTest:
        web_label = self._web_test_label(test_case)
        object_name = (
            f"web-tests/{project_identifier}/minimal-loop/{test_case.id}/"
            f"{uuid4().hex}/test-script.spec.ts"
        )
        script_content = self._generate_playwright_script(test_case, base_url, navigation_path)
        storage_info = self._store_script(object_name, script_content)

        web_test = WebTest(
            project_id=project.id,
            folder_id=folder_id or test_case.folder_id,
            test_case_id=test_case.id,
            identifier=f"WT-{uuid4().hex[:8].upper()}",
            name=f"{test_case.identifier} - {web_label}冒烟",
            description=f"由文档生成用例自动创建的 {web_label} 最小闭环冒烟脚本",
            base_url=base_url,
            script_path=object_name,
            script_format="playwright",
            script_language="typescript",
            test_config={
                "base_url": base_url,
                "storage": storage_info,
                "credential_env": ["WEB_TEST_USERNAME", "WEB_TEST_PASSWORD"],
                "web_path_template_id": path_config.get("template_id"),
                "web_path_match_status": "matched" if path_config["matched"] else "pending_confirmation",
                "web_path_base_url_source": path_config.get("base_url_source"),
                "login_profile": path_config.get("login_profile"),
            },
            target_pages=[{"name": f"{web_label}入口", "url": base_url}],
            test_flows=[{
                "name": f"登录并进入{web_label}入口",
                "steps": navigation_path,
            }],
            generated_by_agent="web_case_link_service",
            generation_params={
                "mode": "minimal_loop",
                "web_path_template_id": path_config.get("template_id"),
                "web_path_match_status": "matched" if path_config["matched"] else "pending_confirmation",
            },
            total_pages=1,
            total_flows=1,
        )
        self.session.add(web_test)
        await self.session.flush()

        self.session.add(Attachment(
            entity_type=AttachmentEntityType.WEB_TEST_SCRIPT,
            entity_id=web_test.id,
            project_id=project.id,
            file_name="test-script.spec.ts",
            file_size=len(script_content.encode("utf-8")),
            content_type="text/plain",
            object_name=object_name,
            description=f"{web_label}最小闭环 Web 冒烟脚本",
            created_by="web_case_link_service",
        ))
        await self.session.flush()
        await self.session.refresh(web_test)
        return web_test

    def _refresh_web_test_asset(
        self,
        web_test: WebTest,
        test_case: TestCase,
        base_url: str,
        navigation_path: list[str],
        path_config: dict[str, Any],
    ) -> None:
        web_label = self._web_test_label(test_case)
        script_content = self._generate_playwright_script(test_case, base_url, navigation_path)
        storage_info = self._store_script(web_test.script_path, script_content)
        existing_config = web_test.test_config or {}
        web_test.base_url = base_url
        web_test.test_config = {
            **existing_config,
            "base_url": base_url,
            "storage": storage_info,
            "credential_env": ["WEB_TEST_USERNAME", "WEB_TEST_PASSWORD"],
            "web_path_template_id": path_config.get("template_id"),
            "web_path_match_status": "matched" if path_config["matched"] else "pending_confirmation",
            "web_path_base_url_source": path_config.get("base_url_source"),
            "login_profile": path_config.get("login_profile"),
        }
        web_test.name = f"{test_case.identifier} - {web_label}冒烟"
        web_test.description = f"由文档生成用例自动创建的 {web_label} 最小闭环冒烟脚本"
        web_test.target_pages = [{"name": f"{web_label}入口", "url": base_url}]
        web_test.test_flows = [{
            "name": f"登录并进入{web_label}入口",
            "steps": navigation_path,
        }]
        existing_generation_params = web_test.generation_params or {}
        web_test.generation_params = {
            **existing_generation_params,
            "mode": "minimal_loop",
            "web_path_template_id": path_config.get("template_id"),
            "web_path_match_status": "matched" if path_config["matched"] else "pending_confirmation",
        }

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
        workspace_root = Path(settings.web_cli_workspace_root).resolve()
        backup_path = workspace_root / "artifacts_backup" / object_name
        backup_path.parent.mkdir(parents=True, exist_ok=True)
        backup_path.write_bytes(data)
        return backup_path

    async def _ensure_test_case_tags(self, project_id: UUID, test_case: TestCase) -> None:
        for tag_name in ["AI生成", "最小闭环", "Web冒烟"]:
            tag = await self.test_case_repo.get_or_create_tag(project_id, tag_name)
            await self.test_case_repo.add_tag_to_test_case(test_case.id, tag)

    async def _append_to_test_run(
        self,
        project: Project,
        test_run_identifier: str,
        test_case: TestCase,
        web_test: WebTest,
        execution_config: dict[str, Any],
    ) -> dict[str, Any]:
        result = await self.session.execute(
            select(TestRun)
            .where(TestRun.project_id == project.id)
            .where(TestRun.identifier == test_run_identifier)
        )
        test_run = result.scalar_one_or_none()
        if not test_run:
            raise ValueError(f"测试运行不存在: {test_run_identifier}")

        await self._ensure_run_case(test_run, test_case.id)
        job = await self._ensure_web_job(
            test_run=test_run,
            web_test=web_test,
            execution_order=await self._next_execution_order(test_run.id),
            execution_config=execution_config,
        )
        await self.session.flush()
        return self._test_run_info(test_run, [job])

    async def _create_test_run(
        self,
        project: Project,
        test_case: TestCase,
        web_test: WebTest,
        execution_config: dict[str, Any],
    ) -> dict[str, Any]:
        test_run = TestRun(
            project_id=project.id,
            identifier=await self.test_run_repo.generate_identifier(project.id),
            name=f"Web 最小闭环验证 {datetime.now().strftime('%Y%m%d-%H%M%S')}",
            description="由 Web 最小闭环接口自动创建",
            run_state=TestRunState.NEW_RUN,
            test_cases_count=0,
            execution_mode=ExecutionMode.SEQUENTIAL,
            max_concurrency=1,
        )
        self.session.add(test_run)
        await self.session.flush()
        await self._ensure_run_case(test_run, test_case.id)
        job = await self._ensure_web_job(
            test_run=test_run,
            web_test=web_test,
            execution_order=1,
            execution_config=execution_config,
        )
        await self.session.flush()
        return self._test_run_info(test_run, [job])

    async def _ensure_run_case(self, test_run: TestRun, test_case_id: UUID) -> None:
        result = await self.session.execute(
            select(TestRunTestCase)
            .where(TestRunTestCase.test_run_id == test_run.id)
            .where(TestRunTestCase.test_case_id == test_case_id)
        )
        if result.scalar_one_or_none():
            return
        self.session.add(TestRunTestCase(
            test_run_id=test_run.id,
            test_case_id=test_case_id,
            latest_status=TestResultStatus.NOT_EXECUTED,
        ))
        test_run.test_cases_count = (test_run.test_cases_count or 0) + 1

    async def _ensure_web_job(
        self,
        test_run: TestRun,
        web_test: WebTest,
        execution_order: int,
        execution_config: dict[str, Any],
    ) -> TestRunScriptJob:
        result = await self.session.execute(
            select(TestRunScriptJob)
            .where(TestRunScriptJob.test_run_id == test_run.id)
            .where(TestRunScriptJob.script_type == ScriptType.WEB_TEST)
            .where(TestRunScriptJob.script_id == web_test.id)
        )
        existing = result.scalar_one_or_none()
        if existing:
            existing.execution_config = {
                **(existing.execution_config or {}),
                **(execution_config or {}),
            }
            return existing

        job = TestRunScriptJob(
            test_run_id=test_run.id,
            script_type=ScriptType.WEB_TEST,
            script_id=web_test.id,
            script_identifier=web_test.identifier,
            script_name=web_test.name,
            execution_order=execution_order,
            execution_mode=ExecutionMode.SEQUENTIAL,
            status=JobStatus.PENDING,
            max_retries=0,
            execution_config=execution_config,
        )
        self.session.add(job)
        return job

    async def _next_execution_order(self, test_run_id: UUID) -> int:
        result = await self.session.execute(
            select(TestRunScriptJob.execution_order)
            .where(TestRunScriptJob.test_run_id == test_run_id)
            .order_by(TestRunScriptJob.execution_order.desc())
        )
        current = result.scalars().first()
        return (current or 0) + 1

    def _test_run_info(
        self,
        test_run: TestRun,
        jobs: list[TestRunScriptJob],
    ) -> dict[str, Any]:
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

    def _generate_playwright_script(
        self,
        test_case: TestCase,
        base_url: str,
        navigation_path: list[str],
    ) -> str:
        if self._is_enterprise_storage_recharge_case(test_case):
            return self._generate_enterprise_storage_recharge_script(
                test_case,
                base_url,
                navigation_path,
            )

        path_steps = json.dumps(navigation_path, ensure_ascii=False, indent=2)
        return f"""import {{ test, expect, type BrowserContext, type FrameLocator, type Page }} from '@playwright/test';

const LOGIN_URL = process.env.WEB_TEST_BASE_URL || {json.dumps(base_url, ensure_ascii=False)};
const USERNAME = process.env.WEB_TEST_USERNAME;
const PASSWORD = process.env.WEB_TEST_PASSWORD;
const WEB_VALIDATION_PATH = {path_steps};

type ClickScope = Page | FrameLocator;

async function clickByText(scope: ClickScope, candidates: string[]) {{
  for (const text of candidates) {{
    const locators = [
      scope.getByRole('button', {{ name: new RegExp(text) }}).first(),
      scope.getByRole('link', {{ name: new RegExp(text) }}).first(),
      scope.getByText(text, {{ exact: false }}).first(),
    ];
    for (const locator of locators) {{
      if (!(await locator.count())) continue;
      try {{
        await locator.click({{ timeout: 5000 }});
        return true;
      }} catch (error) {{
        // Try next candidate.
      }}
    }}
  }}
  return false;
}}

async function requiredClick(scope: ClickScope, stepName: string, candidates: string[]) {{
  const clicked = await clickByText(scope, candidates);
  expect(clicked, `未找到或无法点击：${{stepName}}`).toBeTruthy();
}}

async function clickClickableAncestorByText(page: Page, candidates: string[]) {{
  return await page.evaluate((texts) => {{
    const normalize = (value: string) => value.replace(/\\s+/g, '').trim();
    const isVisible = (element: Element) => {{
      const rect = element.getBoundingClientRect();
      return rect.width > 0 && rect.height > 0;
    }};
    const elements = Array.from(document.querySelectorAll('body *'))
      .filter(isVisible)
      .filter((element) => texts.some((text) => normalize(element.textContent || '').includes(normalize(text))))
      .sort((a, b) => (a.textContent || '').length - (b.textContent || '').length);

    for (const element of elements) {{
      let current: HTMLElement | null = element as HTMLElement;
      for (let depth = 0; current && depth < 6; depth += 1, current = current.parentElement) {{
        const style = window.getComputedStyle(current);
        if (style.cursor === 'pointer' || current.tagName === 'BUTTON' || current.tagName === 'A') {{
          current.click();
          return true;
        }}
      }}
      (element as HTMLElement).click();
      return true;
    }}
    return false;
  }}, candidates);
}}

async function openMarketingCloud(page: Page, context: BrowserContext) {{
  const marketingPagePromise = context.waitForEvent('page', {{ timeout: 15000 }}).catch(() => null);
  let clicked = await clickByText(page, ['数盈·营销云', '数盈营销云', '营销云', 'Marketing cloud']);
  if (!clicked) {{
    clicked = await clickClickableAncestorByText(page, ['数盈·营销云', '数盈营销云', '营销云', 'Marketing cloud']);
  }}
  expect(clicked, '未找到或无法点击：数盈·营销云卡片').toBeTruthy();

  const newPage = await marketingPagePromise;
  const appPage = newPage || page;
  await appPage.waitForLoadState('domcontentloaded', {{ timeout: 30000 }}).catch(() => undefined);
  await appPage.waitForLoadState('networkidle', {{ timeout: 30000 }}).catch(() => undefined);
  await appPage.waitForTimeout(3000);
  return appPage;
}}

async function getMarketingScope(page: Page): Promise<ClickScope> {{
  const iframe = page.locator('iframe#systemIframe').first();
  if (await iframe.count()) {{
    await iframe.waitFor({{ state: 'attached', timeout: 30000 }}).catch(() => undefined);
    return page.frameLocator('#systemIframe').first();
  }}
  return page;
}}

async function chooseActivityType(scope: ClickScope) {{
  if (await clickByText(scope, ['储值免单活动', '储值免单'])) {{
    return true;
  }}

  const triggerCandidates = [
    scope.getByLabel(/活动类型/).first(),
    scope.getByPlaceholder(/请选择.*活动类型|活动类型/).first(),
    scope.locator('.ant-select:has-text("活动类型"), .ant-select-selector').first(),
    scope.locator('xpath=//*[contains(normalize-space(.), "活动类型")]/following::*[contains(@class, "ant-select-selector")][1]').first(),
    scope.getByText('活动类型', {{ exact: false }}).locator('..').first(),
  ];
  for (const trigger of triggerCandidates) {{
    if (!(await trigger.count())) continue;
    try {{
      await trigger.click({{ timeout: 5000 }});
      if (await clickByText(scope, ['储值免单活动', '储值免单'])) {{
        return true;
      }}
    }} catch (error) {{
      // Try next trigger.
    }}
  }}
  return false;
}}

test('B端储值免单活动新建入口冒烟 - {test_case.identifier}', async ({{ page, context }}) => {{
  test.skip(!USERNAME || !PASSWORD, '缺少 WEB_TEST_USERNAME/WEB_TEST_PASSWORD，跳过外部 B端 Web 冒烟执行');

  await page.goto(LOGIN_URL, {{ waitUntil: 'domcontentloaded', timeout: 60000 }});
  await page.locator('input[placeholder*="用户名"], input#login_username').first().fill(USERNAME);
  await page.locator('input[type="password"], input#login_password').first().fill(PASSWORD);
  await page.locator('button:has-text("登 录"), button[type="submit"]').first().click();
  await page.waitForLoadState('networkidle', {{ timeout: 30000 }}).catch(() => undefined);
  await page.waitForFunction(() => /数盈[·\\s]*营销云|Marketing cloud/.test(document.body.innerText), null, {{ timeout: 45000 }});

  const appPage = await openMarketingCloud(page, context);
  const appScope = await getMarketingScope(appPage);

  await requiredClick(appScope, '活动菜单', ['活动']);
  await requiredClick(appScope, '营销活动管理', ['营销活动管理', '营销活动']);
  await requiredClick(appScope, '创建', ['创建', '新建活动', '新增活动']);
  const selected = await chooseActivityType(appScope);
  expect(selected).toBeTruthy();

  await expect(appScope.getByText(/储值免单/).first()).toBeVisible({{ timeout: 15000 }});
}});
"""

    def _is_enterprise_storage_recharge_case(self, test_case: TestCase) -> bool:
        text = " ".join([
            test_case.name or "",
            test_case.module or "",
            test_case.description or "",
        ])
        return any(keyword in text for keyword in ["企业储值", "储值充值", "储值账户列表"])

    def _web_test_label(self, test_case: TestCase) -> str:
        if self._is_enterprise_storage_recharge_case(test_case):
            return "B端企业储值充值"
        return "B端储值免单活动"

    def _generate_enterprise_storage_recharge_script(
        self,
        test_case: TestCase,
        base_url: str,
        navigation_path: list[str],
    ) -> str:
        """Generate a read-only entry smoke test for the enterprise recharge form."""
        path_steps = json.dumps(navigation_path, ensure_ascii=False, indent=2)
        return f"""import {{ test, expect, type BrowserContext, type FrameLocator, type Page }} from '@playwright/test';

const LOGIN_URL = process.env.WEB_TEST_BASE_URL || {json.dumps(base_url, ensure_ascii=False)};
const USERNAME = process.env.WEB_TEST_USERNAME;
const PASSWORD = process.env.WEB_TEST_PASSWORD;
const WEB_VALIDATION_PATH = {path_steps};

type ClickScope = Page | FrameLocator;

async function clickByText(scope: ClickScope, candidates: string[]) {{
  for (const text of candidates) {{
    const locators = [
      scope.getByRole('button', {{ name: new RegExp(text) }}).first(),
      scope.getByRole('link', {{ name: new RegExp(text) }}).first(),
      scope.getByText(text, {{ exact: false }}).first(),
    ];
    for (const locator of locators) {{
      if (!(await locator.count())) continue;
      try {{
        await locator.click({{ timeout: 5000 }});
        return true;
      }} catch {{
        // Try the next locator.
      }}
    }}
  }}
  return false;
}}

async function requiredClick(scope: ClickScope, stepName: string, candidates: string[]) {{
  expect(await clickByText(scope, candidates), `未找到或无法点击：${{stepName}}`).toBeTruthy();
}}

async function openMarketingCloud(page: Page, context: BrowserContext) {{
  const newPagePromise = context.waitForEvent('page', {{ timeout: 15000 }}).catch(() => null);
  await requiredClick(page, '数盈营销云卡片', ['数盈·营销云', '数盈营销云', '营销云', 'Marketing cloud']);
  const appPage = (await newPagePromise) || page;
  await appPage.waitForLoadState('domcontentloaded', {{ timeout: 30000 }}).catch(() => undefined);
  await appPage.waitForTimeout(2500);
  return appPage;
}}

async function getMarketingScope(page: Page): Promise<ClickScope> {{
  const iframe = page.locator('iframe#systemIframe').first();
  if (await iframe.count()) {{
    await iframe.waitFor({{ state: 'attached', timeout: 30000 }}).catch(() => undefined);
    return page.frameLocator('#systemIframe').first();
  }}
  return page;
}}

test('企业储值充值入口与表单冒烟 - {test_case.identifier}', async ({{ page, context }}) => {{
  test.skip(!USERNAME || !PASSWORD, '缺少 WEB_TEST_USERNAME/WEB_TEST_PASSWORD，跳过外部 B端 Web 冒烟执行');

  await page.goto(LOGIN_URL, {{ waitUntil: 'domcontentloaded', timeout: 60000 }});
  await page.locator('input[placeholder*="用户名"], input#login_username').first().fill(USERNAME);
  await page.locator('input[type="password"], input#login_password').first().fill(PASSWORD);
  await page.locator('button:has-text("登 录"), button[type="submit"]').first().click();
  // The portal card can render after the page load event. The click below is the actual assertion.
  await page.waitForTimeout(2000);

  const appPage = await openMarketingCloud(page, context);
  const appScope = await getMarketingScope(appPage);
  await requiredClick(appScope, '忠诚度菜单', ['忠诚度']);
  await requiredClick(appScope, '储值管理菜单', ['储值管理']);
  await requiredClick(appScope, '储值账户列表', ['储值账户列表']);

  await expect(appScope.getByText('手机号码', {{ exact: true }}).first()).toBeVisible({{ timeout: 15000 }});
  await expect(appScope.getByText('会员卡号', {{ exact: true }}).first()).toBeVisible({{ timeout: 15000 }});
  await expect(appScope.getByRole('button', {{ name: /企业充值[/]退款|创建储值充值/ }}).first()).toBeVisible({{ timeout: 15000 }});

  // UAT currently exposes the creation entry as "企业充值/退款". Keep smoke validation read-only.
  expect(WEB_VALIDATION_PATH).toContain('创建储值充值');
}});
"""
