"""
导入 PR-2 Web 验证成果物到系统

将本地文件夹中的验证成果物导入数据库和 MinIO：
- WEB_VERIFICATION_REPORT.md -> WEB_TEST_REPORT
- web_verification_result.json -> WEB_TEST_RESULT
- final_script.py -> WEB_TEST_SCRIPT
"""

import asyncio
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from app.config.database import async_session_factory
from app.config.minio_client import MinIOClient
from app.config.settings import settings
from app.models.attachment import Attachment, AttachmentEntityType
from app.models.folder import Folder
from app.models.project import Project
from app.models.web_function import WebFunction, WebSubFunction
from app.models.web_test import WebTest, WebTestRun
from sqlalchemy import select


# ============================================================================
# 配置
# ============================================================================

PROJECT_IDENTIFIER = "PR-2"
FOLDER_NAME = "营销云-营销活动-储值免单活动-真实验证"
FOLDER_DESCRIPTION = "PR-2 储值免单活动真实环境 Web 验证成果物（2026-09-06）"

FUNCTION_NAME = "storage_free_activity_verification"
FUNCTION_DISPLAY_NAME = "储值免单活动-真实验证"
FUNCTION_DESCRIPTION = "储值免单活动创建流程的真实环境 Web 验证"

SUB_FUNCTION_NAME = "storage_free_activity_flow"
SUB_FUNCTION_DISPLAY_NAME = "储值免单活动创建流程验证"
SUB_FUNCTION_DESCRIPTION = "从控制台登录到储值免单活动表单展示的完整路径验证"

# 本地成果物文件路径
LOCAL_ARTIFACTS_DIR = Path("d:/code/Pyproject/ai-test-agent-system-platform/pr2-储值充值交付-20260904/真实验证-20260906-131320/web验证")

# 验证环境信息
VERIFICATION_ENV = "UAT"
VERIFICATION_URL = "https://console-uat.xysjg.com"
VERIFICATION_ACCOUNT = "andan / andan123"


def _build_object_name(*parts: str) -> str:
    """构建 MinIO 对象名称"""
    return "/".join(part.strip("/") for part in parts if part and part.strip("/"))


async def import_web_verification_artifacts():
    """导入 Web 验证成果物"""
    async with async_session_factory() as session:
        # =====================================================================
        # 1. 查找项目
        # =====================================================================
        stmt = select(Project).where(Project.identifier == PROJECT_IDENTIFIER)
        result = await session.execute(stmt)
        project = result.scalar_one_or_none()

        if not project:
            print(f"[ERROR] 项目 {PROJECT_IDENTIFIER} 不存在")
            return

        print(f"[OK] 找到项目: {project.name} (id={project.id})")

        # =====================================================================
        # 2. 创建文件夹（如果不存在）
        # =====================================================================
        stmt = select(Folder).where(
            Folder.project_id == project.id,
            Folder.name == FOLDER_NAME,
            Folder.folder_type == "web_test"
        )
        result = await session.execute(stmt)
        folder = result.scalar_one_or_none()

        if folder:
            print(f"[OK] 文件夹已存在: {folder.name} (id={folder.id})")
        else:
            folder = Folder(
                project_id=project.id,
                name=FOLDER_NAME,
                description=FOLDER_DESCRIPTION,
                folder_type="web_test"
            )
            session.add(folder)
            await session.flush()
            print(f"[OK] 创建文件夹: {folder.name} (id={folder.id})")

        # =====================================================================
        # 3. 创建 Web 功能（如果不存在）
        # =====================================================================
        stmt = select(WebFunction).where(
            WebFunction.project_id == project.id,
            WebFunction.name == FUNCTION_NAME
        )
        result = await session.execute(stmt)
        web_function = result.scalar_one_or_none()

        if web_function:
            print(f"[OK] Web 功能已存在: {web_function.display_name} (id={web_function.id})")
        else:
            func_identifier = f"WF-{uuid4().hex[:8].upper()}"
            web_function = WebFunction(
                project_id=project.id,
                folder_id=folder.id,
                identifier=func_identifier,
                name=FUNCTION_NAME,
                display_name=FUNCTION_DISPLAY_NAME,
                description=FUNCTION_DESCRIPTION,
                base_url=VERIFICATION_URL,
                business_module="营销云"
            )
            session.add(web_function)
            await session.flush()
            print(f"[OK] 创建 Web 功能: {web_function.display_name} (id={web_function.id}, identifier={func_identifier})")

        # =====================================================================
        # 4. 创建 Web 子功能（如果不存在）
        # =====================================================================
        stmt = select(WebSubFunction).where(
            WebSubFunction.function_id == web_function.id,
            WebSubFunction.name == SUB_FUNCTION_NAME
        )
        result = await session.execute(stmt)
        sub_function = result.scalar_one_or_none()

        if sub_function:
            print(f"[OK] Web 子功能已存在: {sub_function.display_name} (id={sub_function.id})")
        else:
            sf_identifier = f"WSF-{uuid4().hex[:8].upper()}"
            sub_function = WebSubFunction(
                project_id=project.id,
                function_id=web_function.id,
                folder_id=folder.id,
                identifier=sf_identifier,
                name=SUB_FUNCTION_NAME,
                display_name=SUB_FUNCTION_DISPLAY_NAME,
                description=SUB_FUNCTION_DESCRIPTION,
                test_type="validation",
                priority="high"
            )
            session.add(sub_function)
            await session.flush()
            print(f"[OK] 创建 Web 子功能: {sub_function.display_name} (id={sub_function.id}, identifier={sf_identifier})")

        # =====================================================================
        # 5. 创建 WebTest 记录（用于关联报告）
        # =====================================================================
        stmt = select(WebTest).where(WebTest.sub_function_id == sub_function.id)
        result = await session.execute(stmt)
        web_test = result.scalar_one_or_none()

        if not web_test:
            identifier = f"WT-{uuid4().hex[:8].upper()}"
            web_test = WebTest(
                project_id=project.id,
                sub_function_id=sub_function.id,
                function_id=web_function.id,
                folder_id=folder.id,
                identifier=identifier,
                name=sub_function.display_name,
                description=f"Auto-created for sub-function {sub_function.id}",
                script_path="",
                script_format="playwright",
                script_language="python",
                generated_by_agent="web-verification-import"
            )
            session.add(web_test)
            await session.flush()
            print(f"[OK] 创建 WebTest: {identifier} (id={web_test.id})")

        # =====================================================================
        # 6. 创建 WebTestRun 记录
        # =====================================================================
        run_identifier = f"WTR-{uuid4().hex[:8].upper()}"
        test_run = WebTestRun(
            project_id=project.id,
            web_test_id=web_test.id,
            identifier=run_identifier,
            status="completed",
            total_tests=10,
            passed_tests=10,
            failed_tests=0
        )
        session.add(test_run)
        await session.flush()
        print(f"[OK] 创建 WebTestRun: {run_identifier} (id={test_run.id})")

        # =====================================================================
        # 7. 导入成果物文件
        # =====================================================================
        artifacts_to_import = [
            {
                "file_name": "WEB_VERIFICATION_REPORT.md",
                "entity_type": AttachmentEntityType.WEB_TEST_REPORT,
                "content_type": "text/markdown",
                "description": f"Web 验证报告 - 储值免单活动创建流程 ({VERIFICATION_ENV})",
                "file_suffix": "md"
            },
            {
                "file_name": "web_verification_result.json",
                "entity_type": AttachmentEntityType.WEB_TEST_RESULT,
                "content_type": "application/json",
                "description": f"Web 验证结构化结果 - 10/10 通过 ({VERIFICATION_ENV})",
                "file_suffix": "json"
            },
            {
                "file_name": "final_script.py",
                "entity_type": AttachmentEntityType.WEB_TEST_SCRIPT,
                "content_type": "text/plain",
                "description": f"Playwright 自动化验证脚本 ({VERIFICATION_ENV})",
                "file_suffix": "py"
            }
        ]

        imported_artifacts = []

        for artifact_config in artifacts_to_import:
            local_file = LOCAL_ARTIFACTS_DIR / artifact_config["file_name"]

            if not local_file.exists():
                print(f"[WARN] 文件不存在: {local_file}")
                continue

            # 读取文件内容
            content = local_file.read_text(encoding="utf-8")
            content_bytes = content.encode("utf-8")

            # 生成 MinIO 对象名称
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            safe_name = SUB_FUNCTION_DISPLAY_NAME.replace(" ", "-").replace("/", "-").replace("\\", "-")

            if artifact_config["entity_type"] == AttachmentEntityType.WEB_TEST_REPORT:
                object_name = _build_object_name(
                    "web-tests", PROJECT_IDENTIFIER, "runs", str(test_run.id),
                    f"{safe_name}-测试报告-通过-{timestamp}.html"
                )
                # 报告转换为 HTML 格式（Markdown 转 HTML）
                report_html = f"""<!DOCTYPE html>
<html>
<head>
    <meta charset="UTF-8">
    <title>Web 验证报告 - {SUB_FUNCTION_DISPLAY_NAME}</title>
    <style>
        body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; max-width: 1200px; margin: 0 auto; padding: 20px; line-height: 1.6; }}
        h1 {{ color: #1a1a1a; border-bottom: 2px solid #4CAF50; padding-bottom: 10px; }}
        h2 {{ color: #333; margin-top: 30px; }}
        table {{ border-collapse: collapse; width: 100%; margin: 20px 0; }}
        th, td {{ border: 1px solid #ddd; padding: 12px; text-align: left; }}
        th {{ background-color: #4CAF50; color: white; }}
        tr:nth-child(even) {{ background-color: #f2f2f2; }}
        .pass {{ color: #4CAF50; font-weight: bold; }}
        .info {{ background: #e3f2fd; padding: 15px; border-radius: 8px; margin: 20px 0; }}
        pre {{ background: #f5f5f5; padding: 15px; border-radius: 8px; overflow-x: auto; }}
    </style>
</head>
<body>
    <h1>🌐 Web 验证报告</h1>
    <div class="info">
        <strong>验证功能：</strong>{SUB_FUNCTION_DISPLAY_NAME}<br>
        <strong>验证环境：</strong>{VERIFICATION_ENV} ({VERIFICATION_URL})<br>
        <strong>验证账号：</strong>{VERIFICATION_ACCOUNT}<br>
        <strong>执行结果：</strong><span class="pass">10/10 通过 (100%)</span><br>
        <strong>验证时间：</strong>2026-09-06 14:48:20 ~ 14:49:18
    </div>
    <h2>📋 验证路径</h2>
    <pre>CP-001: 打开控制台登录页
    ↓
CP-002: 输入账号密码 ({VERIFICATION_ACCOUNT})
    ↓
CP-003: 点击登录，进入门户中心
    ↓
CP-004: 点击「数盈·营销云」卡片 → 新窗口打开营销云
    ↓
CP-005: 营销云首页加载完成
    ↓
CP-006: 点击左侧「活动」菜单
    ↓
CP-007: 点击「营销活动管理」
    ↓
CP-008: 点击「创建」按钮 → 打开活动创建表单
    ↓
CP-009: 活动类型下拉选择「储值免单活动」
    ↓
CP-010: 验证储值免单活动表单展示</pre>
    <h2>📊 检查点详情</h2>
    <table>
        <tr><th>编号</th><th>检查项</th><th>状态</th><th>详情</th></tr>
        <tr><td>CP-001</td><td>打开控制台登录页</td><td class="pass">✅ PASS</td><td>页面标题: 企业控制台</td></tr>
        <tr><td>CP-002</td><td>输入账号密码</td><td class="pass">✅ PASS</td><td>用户名: andan</td></tr>
        <tr><td>CP-003</td><td>登录成功，进入门户中心</td><td class="pass">✅ PASS</td><td>跳转至门户首页，获取 accessToken</td></tr>
        <tr><td>CP-004</td><td>点击数盈·营销云卡片</td><td class="pass">✅ PASS</td><td>新窗口打开营销云</td></tr>
        <tr><td>CP-005</td><td>营销云首页加载完成</td><td class="pass">✅ PASS</td><td>营销云页面加载成功</td></tr>
        <tr><td>CP-006</td><td>点击左侧活动菜单</td><td class="pass">✅ PASS</td><td>活动菜单展开</td></tr>
        <tr><td>CP-007</td><td>进入营销活动管理</td><td class="pass">✅ PASS</td><td>营销活动管理页面加载，显示活动列表</td></tr>
        <tr><td>CP-008</td><td>点击创建按钮</td><td class="pass">✅ PASS</td><td>打开活动创建表单</td></tr>
        <tr><td>CP-009</td><td>选择储值免单活动类型</td><td class="pass">✅ PASS</td><td>下拉框选择成功</td></tr>
        <tr><td>CP-010</td><td>储值免单活动表单展示验证</td><td class="pass">✅ PASS</td><td>发现字段: 活动类型、活动名称、活动时间、活动门槛、业务类型、适用店铺</td></tr>
    </table>
    <h2>📝 原始 Markdown 报告</h2>
    <pre>{content.replace('<', '&lt;').replace('>', '&gt;')}</pre>
</body>
</html>"""
                content_bytes = report_html.encode("utf-8")
                display_file_name = f"{safe_name}-测试报告-通过-{timestamp}.html"
                mime_type = "text/html"
            else:
                object_name = _build_object_name(
                    "web-tests", PROJECT_IDENTIFIER, "sub-functions", str(sub_function.id),
                    f"{artifact_config['file_name']}"
                )
                display_file_name = artifact_config["file_name"]
                mime_type = artifact_config["content_type"]

            # 上传到 MinIO
            MinIOClient.upload_bytes(
                object_name=object_name,
                data=content_bytes,
                content_type=mime_type
            )
            print(f"[OK] 上传到 MinIO: {object_name}")

            # 创建 Attachment 记录
            attachment = Attachment(
                entity_type=artifact_config["entity_type"],
                entity_id=sub_function.id,
                project_id=project.id,
                file_name=display_file_name,
                file_size=len(content_bytes),
                content_type=mime_type,
                object_name=object_name,
                description=artifact_config["description"],
                created_by="web-verification-import"
            )
            session.add(attachment)
            await session.flush()

            imported_artifacts.append({
                "type": artifact_config["entity_type"].value,
                "file_name": display_file_name,
                "attachment_id": str(attachment.id),
                "size": len(content_bytes)
            })
            print(f"[OK] 创建 Attachment: {display_file_name} (id={attachment.id})")

        # =====================================================================
        # 8. 更新子功能统计
        # =====================================================================
        sub_function.total_test_runs = (sub_function.total_test_runs or 0) + 1
        sub_function.last_run_status = "passed"
        sub_function.updated_at = datetime.now(timezone.utc)

        # 更新 test_run 报告路径
        report_attachment = [a for a in imported_artifacts if a["type"] == "WEB_TEST_REPORT"]
        if report_attachment:
            test_run.report_path = object_name

        await session.commit()

        # =====================================================================
        # 9. 输出导入结果
        # =====================================================================
        print("\n" + "=" * 60)
        print("Web 验证成果物导入完成")
        print("=" * 60)
        print(f"项目: {project.name} ({PROJECT_IDENTIFIER})")
        print(f"文件夹: {folder.name} (id={folder.id})")
        print(f"Web 功能: {web_function.display_name} (id={web_function.id})")
        print(f"子功能: {sub_function.display_name} (id={sub_function.id})")
        print(f"测试运行: {test_run.identifier} (id={test_run.id})")
        print(f"\n导入成果物 ({len(imported_artifacts)} 个):")
        for art in imported_artifacts:
            print(f"  - [{art['type']}] {art['file_name']} ({art['size']} bytes, id={art['attachment_id']})")
        print("=" * 60)

        return {
            "success": True,
            "project_id": str(project.id),
            "folder_id": str(folder.id),
            "function_id": str(web_function.id),
            "sub_function_id": str(sub_function.id),
            "test_run_id": str(test_run.id),
            "artifacts": imported_artifacts
        }


if __name__ == "__main__":
    result = asyncio.run(import_web_verification_artifacts())
    if result:
        print(f"\n导入成功！子功能 ID: {result['sub_function_id']}")
