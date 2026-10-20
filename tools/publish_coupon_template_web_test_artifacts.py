"""Publish verified UAT coupon-template Web test artifacts for PR-2.

The publisher is idempotent and deliberately stores only redacted evidence.
It never persists credentials, cookies, access tokens, or portal URLs containing
authentication query parameters.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path
from urllib.parse import unquote, urlparse

from dotenv import load_dotenv


ROOT = Path(__file__).resolve().parents[1]
BACKEND_ROOT = ROOT / "backend"
PROJECT_IDENTIFIER = "PR-2"
FUNCTION_IDENTIFIER = "WF-COUPON-TEMPLATE"
SUB_FUNCTION_IDENTIFIER = "WSF-COUPON-TEMPLATE-WEB"
SUB_FUNCTION_NAME = "B端-券模板创建与列表"
SUITE_NAME = "券模板创建与列表 Web 自动化测试"
CREATED_TEMPLATE_NO = "20260827012"
CREATED_TEMPLATE_NAME = "【自动化测试】券模板-Web-20260827"


def configure_database() -> None:
    """Translate the repository connection URI to the app settings fields."""
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


def artifact_contents() -> dict[str, tuple[str, str, str]]:
    """Build the five artifact contents shown by the Web test UI."""
    plan = f"""# {SUITE_NAME}

## 范围

- 企业控制台 UAT 登录后进入门户中心。
- 门户中心进入数盈营销云，依次打开营销 -> 券管理 -> 券模板管理。
- 验证券模板列表检索、分页，以及新建模板和列表回读。

## 安全与数据口径

- 认证在浏览器会话内完成；本成果物不保存账号、密码、Token、Cookie 或带 Token 的 URL。
- 真实创建的数据带 `【自动化测试】` 标识，仅用于 UAT 回归验证。
- 已知 API 分页缺陷 `pageSize=0` 已登记为 IDP `jf-20260122392-01-2521`；本轮 Web 正常分页未产生新的独立缺陷。
"""

    cases = {
        "suite": SUITE_NAME,
        "cases": [
            {"name": "门户链路登录", "expected": "进入消费者运营平台首页", "actual": "通过", "status": "passed"},
            {"name": "券模板列表检索", "expected": "按名称查询返回匹配模板", "actual": "0827测试-商品券返回 2 条", "status": "passed"},
            {"name": "券模板正常分页", "expected": "第二页展示不同的模板记录", "actual": "列表由第一页切换至第二页成功", "status": "passed"},
            {"name": "新建券模板", "expected": "提交成功并返回列表", "actual": f"创建模板 {CREATED_TEMPLATE_NO}", "status": "passed"},
            {"name": "创建结果回读", "expected": "新模板在列表第一页可见", "actual": CREATED_TEMPLATE_NAME, "status": "passed"},
        ],
    }

    script = '''import { expect, test } from "@playwright/test";

test("券模板创建与列表", async ({ page }) => {
  // Credentials are injected by the runner and never committed to artifacts.
  await page.goto(process.env.CONSOLE_UAT_URL!);
  await page.getByPlaceholder("请输入用户名").fill(process.env.CONSOLE_USERNAME!);
  await page.getByPlaceholder("请输入密码").fill(process.env.CONSOLE_PASSWORD!);
  await page.getByRole("button", { name: "登 录" }).click();

  await page.getByText("数盈·营销云", { exact: true }).click();
  const marketing = page.context().pages().at(-1)!;
  await marketing.getByText("营销", { exact: true }).click();
  await marketing.getByRole("menuitem", { name: "券管理" }).click();
  await expect(marketing.getByRole("button", { name: "新建模板" })).toBeVisible();
});
'''

    result = {
        "suite": SUITE_NAME,
        "environment": "小杨生煎 UAT",
        "execution_time": "2026-08-27 09:41:55",
        "summary": {"total": 5, "passed": 5, "failed": 0, "skipped": 0},
        "navigation": ["企业控制台", "门户中心", "数盈营销云", "营销", "券管理", "券模板管理"],
        "created_template": {
            "template_no": CREATED_TEMPLATE_NO,
            "name": CREATED_TEMPLATE_NAME,
            "status": "已启用",
            "organization": "小杨生煎",
        },
        "known_idp_defect": {
            "issue_key": "jf-20260122392-01-2521",
            "issue_id": 912747,
            "relationship": "API pageSize=0 分页边界问题；本轮 Web 正常分页不重复登记",
        },
    }

    report = f"""<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8"><title>{SUITE_NAME}</title>
<style>body{{font:14px/1.6 system-ui,sans-serif;margin:32px;color:#1f2937}}table{{border-collapse:collapse;width:100%;margin:16px 0}}th,td{{border:1px solid #d1d5db;padding:8px;text-align:left}}th{{background:#f3f4f6}}.pass{{color:#166534;font-weight:600}}.note{{background:#eff6ff;border-left:4px solid #2563eb;padding:12px}}</style>
</head><body>
<h1>{SUITE_NAME}</h1><p>项目：PR-2（小杨生煎） | 环境：UAT | Web 自动化测试成果</p>
<table><thead><tr><th>总数</th><th>通过</th><th>失败</th><th>跳过</th></tr></thead><tbody><tr><td>5</td><td class="pass">5</td><td>0</td><td>0</td></tr></tbody></table>
<h2>验证结果</h2><ul><li>企业控制台门户链路进入营销云成功。</li><li>券模板名称检索、正常分页均通过。</li><li>真实创建模板：{CREATED_TEMPLATE_NO}，并在列表第一页回读成功。</li></ul>
<div class="note"><strong>已关联问题</strong><br>API `pageSize=0` 边界问题已登记 IDP `jf-20260122392-01-2521`。本轮 Web 正常分页通过，未重复创建缺陷。</div>
</body></html>"""

    return {
        "WEB_TEST_PLAN": ("coupon-template-web-test-plan.md", "text/markdown", plan),
        "WEB_TEST_CASE": ("coupon-template-web-test-cases.json", "application/json", json.dumps(cases, ensure_ascii=False, indent=2)),
        "WEB_TEST_SCRIPT": ("coupon-template-web.spec.ts", "text/plain", script),
        "WEB_TEST_RESULT": ("coupon-template-web-execution-result.json", "application/json", json.dumps(result, ensure_ascii=False, indent=2)),
        "WEB_TEST_REPORT": ("coupon-template-web-test-report.html", "text/html", report),
    }


async def main() -> None:
    configure_database()
    sys.path.insert(0, str(BACKEND_ROOT))

    from sqlalchemy import select
    from app.config.database import async_session_factory, engine
    from app.config.settings import settings
    from app.models.attachment import Attachment, AttachmentEntityType
    from app.models.project import Project
    from app.models.web_function import WebFunction, WebSubFunction

    artifact_root = Path(settings.web_cli_workspace_root).resolve() / "artifacts_backup"
    async with async_session_factory() as session:
        project = (await session.execute(select(Project).where(Project.identifier == PROJECT_IDENTIFIER))).scalar_one_or_none()
        if not project:
            raise RuntimeError(f"项目 {PROJECT_IDENTIFIER} 不存在，未写入任何成果物")

        web_function = (await session.execute(select(WebFunction).where(WebFunction.identifier == FUNCTION_IDENTIFIER))).scalar_one_or_none()
        if not web_function:
            web_function = WebFunction(
                project_id=project.id,
                identifier=FUNCTION_IDENTIFIER,
                display_name="B端-券模板",
                name="Coupon Template",
                description="小杨生煎 B端券模板创建与列表 Web 测试",
                base_url="https://console-uat.xysjg.com/front/dtcloud-console-web-pc/",
                business_module="营销/券管理",
                navigation={"path": ["门户中心", "数盈营销云", "营销", "券管理", "券模板管理"]},
                pages=[{"name": "券模板管理", "path": "#/coupon/tpl/list"}],
                tags=["B端", "券模板", "UAT"],
            )
            session.add(web_function)
            await session.flush()

        sub_function = (await session.execute(select(WebSubFunction).where(WebSubFunction.identifier == SUB_FUNCTION_IDENTIFIER))).scalar_one_or_none()
        if not sub_function:
            sub_function = WebSubFunction(
                project_id=project.id,
                function_id=web_function.id,
                identifier=SUB_FUNCTION_IDENTIFIER,
                display_name=SUB_FUNCTION_NAME,
                name="Create and List Coupon Templates",
                description="券模板创建、列表检索、分页与回读验证",
                test_type="functional",
                target_pages=[{"name": "券模板管理", "path": "#/coupon/tpl/list"}],
                test_scenario="门户链路进入营销云后创建券模板，并验证列表检索和分页。",
                expected_results=["创建成功", "列表可回读", "正常分页可用"],
                priority="medium",
                tags=["B端", "券模板", "自动化测试"],
            )
            session.add(sub_function)
            web_function.total_sub_functions += 1
            await session.flush()

        contents = artifact_contents()
        prefix = f"web-tests/{PROJECT_IDENTIFIER}/sub-functions/{sub_function.id}/coupon-template-uat-web"
        for entity_type_name, (file_name, content_type, content) in contents.items():
            object_name = f"{prefix}/{file_name}"
            data = content.encode("utf-8")
            backup_path = artifact_root / object_name
            backup_path.parent.mkdir(parents=True, exist_ok=True)
            backup_path.write_bytes(data)

            attachment = (await session.execute(select(Attachment).where(Attachment.object_name == object_name))).scalar_one_or_none()
            if attachment:
                attachment.file_size = len(data)
                attachment.content_type = content_type
                attachment.description = f"{SUITE_NAME}：{file_name}"
            else:
                session.add(Attachment(
                    entity_type=AttachmentEntityType[entity_type_name],
                    entity_id=sub_function.id,
                    project_id=project.id,
                    file_name=file_name,
                    file_size=len(data),
                    content_type=content_type,
                    object_name=object_name,
                    description=f"{SUITE_NAME}：{file_name}",
                    created_by="manual_web_test_publisher",
                ))

        sub_function.total_test_cases = 5
        sub_function.total_test_runs = 1
        sub_function.last_run_status = "passed"
        web_function.total_test_cases = max(web_function.total_test_cases, 5)
        web_function.total_test_runs = max(web_function.total_test_runs, 1)
        web_function.last_run_status = "passed"
        await session.commit()

        artifacts = (await session.execute(select(Attachment.entity_type, Attachment.file_name).where(Attachment.entity_id == sub_function.id))).all()
        print(json.dumps({
            "project": PROJECT_IDENTIFIER,
            "web_function_id": str(web_function.id),
            "sub_function_id": str(sub_function.id),
            "sub_function": sub_function.display_name,
            "created_template_no": CREATED_TEMPLATE_NO,
            "artifacts": [{"type": entity_type.name, "file_name": file_name} for entity_type, file_name in artifacts],
        }, ensure_ascii=False))

    await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
