"""
Web 测试服务

处理 Web 测试相关的业务逻辑
"""

import json
from pathlib import Path
from datetime import datetime
from typing import Optional
from uuid import UUID, uuid4

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.web_test import WebTest, WebTestRun, WebTestResult
from app.models.web_function import WebFunction, WebSubFunction
from app.repositories.web_test_repo import (
    WebTestRepository,
    WebTestRunRepository,
    WebTestResultRepository,
)
from app.repositories.project_repo import ProjectRepository
from app.schemas.enums import TestResultStatus
from app.utils.exceptions import NotFoundException
from app.config.minio_client import MinIOClient
from app.config.settings import settings
from app.services.defect_registration_service import DefectRegistrationService


def _detect_script_language(content: str) -> str:
    """根据脚本内容特征检测语言类型

    用于防止 Python 代码被保存为 .spec.ts 导致 Playwright 解析失败。
    """
    content_stripped = content.strip()
    first_lines = '\n'.join(content_stripped.split('\n')[:20]).lower()

    python_indicators = [
        'import asyncio', 'import json', 'import os', 'import sys',
        'from playwright', 'from datetime import', 'import pytest',
        'def test_', 'async def ', 'if __name__ == ',
        'import requests', 'from typing import',
    ]
    ts_indicators = [
        'import { test', 'import { expect }', 'import test from',
        'test.describe(', 'test.beforeeach(', 'test.aftereach(',
        'import { chromium', 'import { page', 'const { test',
        'playwright.config', 'page.goto(', 'page.click(',
        'page.fill(', 'page.locator(', 'expect(page',
    ]

    python_score = sum(1 for ind in python_indicators if ind in first_lines)
    ts_score = sum(1 for ind in ts_indicators if ind in first_lines)

    if python_score >= 2 and python_score > ts_score:
        return "python"
    if ts_score >= 2 and ts_score > python_score:
        return "typescript"
    return "typescript"  # 默认


async def _evaluate_run_compiled_assertions(report_dir, web_test, test_run) -> None:
    """rev58（执行证据闭环）：读取本次 self_reflect_result.json，对子功能
    compiled_assertions 做执行后评估，结果写入 report_dir/assertion_result.json。

    - 子功能无 compiled_assertions → 跳过；
    - 只校验编译产物，不现场发明断言（human_oracle 部分由人工判定）。
    """
    import json as _json

    from app.agents.tools.web.assertion_compiler import evaluate_run_assertions

    sr_path = report_dir / "self_reflect_result.json"
    if not sr_path.exists():
        return
    try:
        sr_data = _json.loads(sr_path.read_text(encoding="utf-8"))
    except Exception:
        sr_data = None
    if not isinstance(sr_data, dict):
        return
    sf_id = web_test.sub_function_id
    if not sf_id:
        return
    from sqlalchemy import select as _select

    from app.config.database import async_session_factory
    from app.models.web_function import WebSubFunction

    async with async_session_factory() as session:
        sf = (
            await session.execute(
                _select(WebSubFunction).where(WebSubFunction.id == sf_id)
            )
        ).scalars().first()
        if sf is None or not sf.compiled_assertions:
            return
        result = evaluate_run_assertions(sf.compiled_assertions, sr_data)
        out = {
            "run_id": str(test_run.id),
            "sub_function_id": str(sf.id),
            "assertion_mode": sf.assertion_mode,
            "compiled_count": len(sf.compiled_assertions),
            **result,
        }
        (report_dir / "assertion_result.json").write_text(
            _json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        print(f"[WebTestService] rev58：断言评估 {out['passed']}/{out['total']} 通过 "
              f"（{sf.assertion_mode}，human_oracle 由人工判定）")


def _build_webwright_index_html(report_dir: Path) -> Optional[Path]:
    """rev36：webwright/Python 报告目录生成 index.html 入口页。

    读取 self_reflect_result.json（脚本自评）与截图，生成可渲染的报告首页——
    供 ZIP 报告查看器（report-files）以 index.html 为入口，避免"无入口页"。

    Returns:
        生成的 index.html 路径；无 self_reflect_result.json 时返回 None。
    """
    import html as _html

    sr = report_dir / "self_reflect_result.json"
    if not sr.exists():
        return None
    try:
        data = json.loads(sr.read_text(encoding="utf-8"))
    except Exception:
        return None
    status = data.get("execution_status", "unknown")
    steps = data.get("steps", [])
    shots = data.get("screenshots", [])
    ok_n = sum(1 for s in steps if s.get("ok"))
    fail_n = len(steps) - ok_n

    steps_html = "".join(
        "<tr>"
        f"<td>{i + 1}</td>"
        f"<td>{_html.escape(str(s.get('name', '')))}</td>"
        f"<td class='{'ok' if s.get('ok') else 'fail'}'>{'✅ 通过' if s.get('ok') else '❌ 失败'}</td>"
        f"<td>{_html.escape(str(s.get('detail', ''))[:100])}</td>"
        "</tr>"
        for i, s in enumerate(steps)
    )
    shots_html = ""
    for s in shots:
        # rev37（P1-1）：截图优先位于 screenshots/ 子目录（webwright 产物结构），
        # 兜底报告根目录；<img src> 使用相对 ZIP 根的路径
        img_src = None
        for cand, prefix in (
            (report_dir / "screenshots" / f"{s}.png", "screenshots/"),
            (report_dir / f"{s}.png", ""),
        ):
            if cand.exists():
                img_src = f"{prefix}{_html.escape(str(s))}.png"
                break
        if img_src:
            shots_html += (
                f"<div class='shot'><img src='{img_src}' loading='lazy'/>"
                f"<div class='shot-name'>{_html.escape(str(s))}</div></div>"
            )

    html = f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>{_html.escape(str(data.get('run_name', 'Web 测试报告')))} - 执行报告</title>
<style>
body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Arial, sans-serif;
       background: #f5f7fa; color: #333; margin: 0; padding: 24px; }}
.container {{ max-width: 1100px; margin: 0 auto; background: #fff; border-radius: 12px;
             padding: 32px; box-shadow: 0 2px 12px rgba(0,0,0,0.06); }}
h1 {{ font-size: 22px; color: #1a1a2e; margin-bottom: 8px; }}
.meta {{ color: #888; font-size: 13px; margin-bottom: 20px; }}
.badge {{ display: inline-block; padding: 6px 16px; border-radius: 16px; font-size: 13px;
         font-weight: 600; color: #fff; margin-bottom: 16px;
         background: {'#52c41a' if status == 'passed' else '#ff4d4f'}; }}
.summary {{ display: flex; gap: 24px; margin-bottom: 24px; }}
.summary-item {{ background: #f8f9fa; border-radius: 8px; padding: 12px 20px; }}
.summary-item b {{ font-size: 24px; display: block; }}
table {{ width: 100%; border-collapse: collapse; margin-bottom: 24px; }}
th, td {{ border: 1px solid #e8e8e8; padding: 8px 12px; text-align: left; font-size: 13px; }}
th {{ background: #fafafa; }}
.ok {{ color: #52c41a; font-weight: 600; }}
.fail {{ color: #ff4d4f; font-weight: 600; }}
.shots {{ display: grid; grid-template-columns: repeat(auto-fill, minmax(280px, 1fr)); gap: 16px; }}
.shot {{ background: #f8f9fa; border-radius: 8px; padding: 8px; }}
.shot img {{ width: 100%; border-radius: 4px; }}
.shot-name {{ font-size: 12px; color: #888; margin-top: 6px; text-align: center; }}
.footer {{ margin-top: 24px; text-align: center; color: #aaa; font-size: 12px; }}
</style>
</head>
<body>
<div class="container">
  <h1>📊 {_html.escape(str(data.get('run_name', 'Web 测试报告')))}</h1>
  <div class="meta">{_html.escape(str(data.get('sub_function', '')))}</div>
  <span class="badge">执行状态: {_html.escape(str(status))}</span>
  <div class="summary">
    <div class="summary-item"><b>{len(steps)}</b>总步骤</div>
    <div class="summary-item"><b style="color:#52c41a">{ok_n}</b>通过</div>
    <div class="summary-item"><b style="color:#ff4d4f">{fail_n}</b>失败</div>
    <div class="summary-item"><b>{len(shots)}</b>截图</div>
  </div>
  <h2>执行步骤</h2>
  <table>
    <tr><th>#</th><th>步骤</th><th>结果</th><th>详情</th></tr>
    {steps_html}
  </table>
  <h2>截图</h2>
  <div class="shots">{shots_html}</div>
  <div class="footer">AI Test Agent System Platform · Webwright 执行报告</div>
</div>
</body>
</html>"""
    idx = report_dir / "index.html"
    idx.write_text(html, encoding="utf-8")
    return idx

class WebTestService:
    """Web 测试服务类"""

    def __init__(self, session: AsyncSession, mongodb=None):
        self.session = session
        self.mongodb = mongodb
        self.web_test_repo = WebTestRepository(session)
        self.web_test_run_repo = WebTestRunRepository(session)
        self.web_test_result_repo = WebTestResultRepository(session)
        self.project_repo = ProjectRepository(session)

    async def _get_project_by_identifier(self, identifier: str):
        """获取项目，不存在则抛出异常"""
        project = await self.project_repo.get_by_identifier(identifier)
        if not project:
            raise NotFoundException(resource_type="项目", resource_id=identifier)
        return project

    # ==================== Web 测试管理 ====================

    async def create_web_test(
        self,
        project_identifier: str,
        name: str,
        base_url: str,
        script_path: str,
        script_format: str = "playwright",
        script_language: str = "typescript",
        description: Optional[str] = None,
        test_config: Optional[dict] = None,
        folder_id: Optional[str] = None,
        target_pages: Optional[list] = None,
        test_flows: Optional[list] = None,
    ) -> dict:
        """创建 Web 测试"""
        project = await self._get_project_by_identifier(project_identifier)
# type: ignore  MC80OmFIVnBZMlhscm9ua3VMazZZMWxyYVE9PTo5YjE2ZTg0MA==

        # 生成标识符 (简化版本，实际应该用序列)
        identifier = f"WT-{uuid4().hex[:8].upper()}"

        web_test = await self.web_test_repo.create(
            project_id=project.id,
            folder_id=UUID(folder_id) if folder_id else None,
            identifier=identifier,
            name=name,
            base_url=base_url,
            script_path=script_path,
            script_format=script_format,
            script_language=script_language,
            description=description,
            test_config=test_config or {},
            target_pages=target_pages,
            test_flows=test_flows,
            generated_by_agent="web_agent",
            total_pages=len(target_pages) if target_pages else 0,
            total_flows=len(test_flows) if test_flows else 0,
        )

        return {
            "id": str(web_test.id),
            "identifier": web_test.identifier,
            "name": web_test.name,
            "base_url": web_test.base_url,
            "description": web_test.description,
            "script_format": web_test.script_format,
            "script_language": web_test.script_language,
            "total_pages": web_test.total_pages,
            "total_flows": web_test.total_flows,
            "created_at": web_test.created_at.isoformat(),
        }

    async def get_web_test(
        self,
        project_identifier: str,
        web_test_id: str,
    ) -> dict:
        """获取 Web 测试详情"""
        project = await self._get_project_by_identifier(project_identifier)
        web_test = await self.web_test_repo.get_by_id_with_relations(UUID(web_test_id))

        if not web_test or web_test.project_id != project.id:
            raise NotFoundException(resource_type="Web 测试", resource_id=web_test_id)

        return {
            "id": str(web_test.id),
            "identifier": web_test.identifier,
            "name": web_test.name,
            "base_url": web_test.base_url,
            "description": web_test.description,
            "script_path": web_test.script_path,
            "script_format": web_test.script_format,
            "script_language": web_test.script_language,
            "test_config": web_test.test_config,
            "target_pages": web_test.target_pages,
            "test_flows": web_test.test_flows,
            "total_pages": web_test.total_pages,
            "total_flows": web_test.total_flows,
            "created_at": web_test.created_at.isoformat(),
            "updated_at": web_test.updated_at.isoformat() if web_test.updated_at else None,
        }

    async def list_web_tests(
        self,
        project_identifier: str,
        page: int = 1,
        page_size: int = 20,
        search: Optional[str] = None,
        script_format: Optional[str] = None,
    ) -> dict:
        """获取 Web 测试列表"""
        project = await self._get_project_by_identifier(project_identifier)

        offset = (page - 1) * page_size
        items, total = await self.web_test_repo.get_by_project(
            project.id,
            offset=offset,
            limit=page_size,
            search=search,
            script_format=script_format,
        )
# noqa  MS80OmFIVnBZMlhscm9ua3VMazZZMWxyYVE9PTo5YjE2ZTg0MA==

        return {
            "items": [
                {
                    "id": str(item.id),
                    "identifier": item.identifier,
                    "name": item.name,
                    "base_url": item.base_url,
                    "description": item.description,
                    "script_format": item.script_format,
                    "script_language": item.script_language,
                    "total_pages": item.total_pages,
                    "total_flows": item.total_flows,
                    "created_at": item.created_at.isoformat(),
                    "updated_at": item.updated_at.isoformat() if item.updated_at else None,
                }
                for item in items
            ],
            "total": total,
            "page": page,
            "page_size": page_size,
        }

    async def update_web_test(
        self,
        project_identifier: str,
        web_test_id: str,
        name: Optional[str] = None,
        description: Optional[str] = None,
        test_config: Optional[dict] = None,
    ) -> dict:
        """更新 Web 测试"""
        project = await self._get_project_by_identifier(project_identifier)
        web_test = await self.web_test_repo.get_by_id(UUID(web_test_id))

        if not web_test or web_test.project_id != project.id:
            raise NotFoundException(resource_type="Web 测试", resource_id=web_test_id)

        update_data = {}
        if name is not None:
            update_data["name"] = name
        if description is not None:
            update_data["description"] = description
        if test_config is not None:
            update_data["test_config"] = test_config

        updated = await self.web_test_repo.update(web_test, **update_data)

        return {
            "id": str(updated.id),
            "identifier": updated.identifier,
            "name": updated.name,
            "description": updated.description,
            "test_config": updated.test_config,
            "updated_at": updated.updated_at.isoformat() if updated.updated_at else None,
        }

    async def delete_web_test(
        self,
        project_identifier: str,
        web_test_id: str,
    ) -> None:
        """删除 Web 测试"""
        project = await self._get_project_by_identifier(project_identifier)
        web_test = await self.web_test_repo.get_by_id(UUID(web_test_id))

        if not web_test or web_test.project_id != project.id:
            raise NotFoundException(resource_type="Web 测试", resource_id=web_test_id)

        await self.web_test_repo.delete(web_test)

    async def get_test_script(
        self,
        project_identifier: str,
        web_test_id: str,
    ) -> str:
        """获取测试脚本内容"""
        project = await self._get_project_by_identifier(project_identifier)
        web_test = await self.web_test_repo.get_by_id(UUID(web_test_id))

        if not web_test or web_test.project_id != project.id:
            raise NotFoundException(resource_type="Web 测试", resource_id=web_test_id)

        # 从 MinIO 下载脚本
        content_bytes = MinIOClient.download_file(web_test.script_path)
        return content_bytes.decode('utf-8')
# pragma: no cover  Mi80OmFIVnBZMlhscm9ua3VMazZZMWxyYVE9PTo5YjE2ZTg0MA==

    async def update_test_script(
        self,
        project_identifier: str,
        web_test_id: str,
        script_content: str,
    ) -> None:
        """更新测试脚本内容"""
        project = await self._get_project_by_identifier(project_identifier)
        web_test = await self.web_test_repo.get_by_id(UUID(web_test_id))

        if not web_test or web_test.project_id != project.id:
            raise NotFoundException(resource_type="Web 测试", resource_id=web_test_id)

        # 上传到 MinIO
        script_bytes = script_content.encode('utf-8')
        MinIOClient.upload_bytes(
            object_name=web_test.script_path,
            data=script_bytes,
            content_type="text/plain"
        )

    async def run_web_test(
        self,
        project_identifier: str,
        web_test_id: str,
        execution_config: Optional[dict] = None,
    ) -> dict:
        """
        执行 Web 测试

        流程：
        1. 创建测试运行记录（状态: pending）
        2. 从 MinIO 下载测试脚本到 workspace
        3. 使用 PlaywrightRunner 执行测试
        4. 更新运行记录状态为 completed/failed
        5. 将测试报告上传到 MinIO
        """
        import asyncio
        import zipfile
        from pathlib import Path

        from app.services.execution.runner import PlaywrightRunner
        from app.config.settings import settings

        project = await self._get_project_by_identifier(project_identifier)
        web_test = await self.web_test_repo.get_by_id(UUID(web_test_id))

        if not web_test or web_test.project_id != project.id:
            raise NotFoundException(resource_type="Web 测试", resource_id=web_test_id)

        # 创建测试运行记录
        identifier = f"WTR-{datetime.now().strftime('%Y%m%d')}-{uuid4().hex[:6]}"
        test_run = await self.web_test_run_repo.create(
            project_id=project.id,
            web_test_id=web_test.id,
            identifier=identifier,
            status="pending",
            execution_config=execution_config or {},
        )

        # 确定 workspace 目录
        workspace_root = Path(settings.web_cli_workspace_root)
        if not workspace_root.exists():
            workspace_root = Path(settings.web_mcp_workspace_root)

        runner = PlaywrightRunner(workspace_root)
        script_file_path = None
        report_attachment_id = None

        try:
            # 1. 更新状态为 running
            await self.web_test_run_repo.update(test_run, status="running")
            await self.session.commit()

            # 检查 script_path 是否为空
            if not web_test.script_path:
                raise ValueError(f"Web 测试 {web_test.identifier} 的 script_path 为空，无法执行")

            # 2. 从 MinIO 下载脚本到 workspace/tests/
            script_content_bytes = MinIOClient.download_file(web_test.script_path)
            script_content = script_content_bytes.decode('utf-8')

            # 自动检测脚本语言，确保文件扩展名与实际内容匹配
            detected_lang = _detect_script_language(script_content)
            if detected_lang == "python":
                file_extension = ".py"
                print(f"[WebTestService] WARNING: 检测到脚本实际为 Python 代码，"
                      f"web_test.script_language={web_test.script_language}. "
                      f"将使用 .py 扩展名执行")
            else:
                file_extension = ".spec.ts"

            # 使用唯一文件名避免冲突，扩展名根据实际内容确定
            # rev53（报告目录隔离）：每次 run 使用**专属子目录**
            # tests/run_<test_run_id>_<rand8>/run<ext>——python 脚本执行 cwd 为该
            # 子目录，self_reflect/截图等产物天然隔离；报告打包只含本次产物，
            # 避免把 workspace 根历史残留打包（曾致报告 284MB）。
            safe_name = f"run_{test_run.id}_{uuid4().hex[:8]}{file_extension}"
            run_dir = workspace_root / "tests" / f"run_{test_run.id}_{uuid4().hex[:8]}"
            run_dir.mkdir(parents=True, exist_ok=True)
            script_file_path = run_dir / f"run{file_extension}"
            # rev31：write_bytes 保持字节原样（write_text 在 Windows 文本模式会把
            # \n 转 \r\n，导致脚本哈希与注册侧 MinIO 内容不一致 → 2a 授权门误拒）
            script_file_path.write_bytes(script_content_bytes)

            print(f"[WebTestService] 脚本已下载: {script_file_path} (检测语言: {detected_lang})")

            # =================================================================
            # 执行治理层 2a（HTTP 执行面，rev31）：脚本来源授权门
            #  - 三要素绑定：真实项目 + 子功能当前附件 + 内容哈希（web_script_registry）
            #  - 未登记 / 项目不符 / 无子功能绑定 → 终局拒绝（不降级执行）
            # =================================================================
            from uuid import UUID as _UUID

            from app.agents.tools.web.script_provenance import (
                authorize_script_execution,
            )
            from app.config.database import async_session_factory

            sf_id = web_test.sub_function_id
            if not sf_id:
                err = ("脚本来源授权拒绝：Web 测试未绑定子功能（sub_function_id 为空），"
                       "无法建立三要素绑定（项目+附件+哈希），拒绝执行。")
                print(f"[WebTestService] {err}")
                await self.web_test_run_repo.update(
                    test_run, status="failed", error_message=err
                )
                await self.session.commit()
                return {
                    "run_id": str(test_run.id),
                    "identifier": test_run.identifier,
                    "status": "failed",
                    "guard": "script_provenance",
                    "final": True,
                    "error_message": err,
                }
            auth_ok, auth_reason = await authorize_script_execution(
                project.identifier,
                script_file_path,
                workspace_root,
                async_session_factory,
                [_UUID(str(sf_id))],
            )
            if not auth_ok:
                err = f"脚本来源授权拒绝（三要素绑定不成立）: {auth_reason}"
                print(f"[WebTestService] {err}")
                await self.web_test_run_repo.update(
                    test_run, status="failed", error_message=err
                )
                await self.session.commit()
                return {
                    "run_id": str(test_run.id),
                    "identifier": test_run.identifier,
                    "status": "failed",
                    "guard": "script_provenance",
                    "final": True,
                    "error_message": err,
                }
            print("[WebTestService] 脚本来源授权通过（HTTP 执行面已纳入 2a）")

            # 3. 执行测试（同时生成 list + html 报告）
            config_with_reporter = {**(execution_config or {}), "reporter": "list,html"}
            runner_result = await runner.run(
                script_path=script_file_path,
                config=config_with_reporter,
                timeout=execution_config.get("timeout", 600) if execution_config else 600,
            )

            # 4. 将 HTML 报告打包上传到 MinIO
            report_path = runner_result.report_path
            if report_path and Path(report_path).exists():
                try:
                    report_dir = Path(report_path)
                    # rev58（执行证据闭环）：若子功能有 compiled_assertions（rev56 编译），
                    # 用本次 self_reflect_result.json 构建证据评估，结果写入
                    # assertion_result.json（随报告打包），执行只校验不现场发明。
                    try:
                        await _evaluate_run_compiled_assertions(
                            report_dir, web_test, test_run
                        )
                    except Exception as ae:
                        print(f"[WebTestService] 编译断言评估失败（不影响打包）: {ae}")
                    # rev36：webwright/Python 报告生成 index.html 入口页（self_reflect + 截图）
                    try:
                        _build_webwright_index_html(report_dir)
                    except Exception as ie:
                        print(f"[WebTestService] 生成报告 index.html 失败（不影响打包）: {ie}")
                    zip_path = report_dir.parent / f"report-{test_run.id}.zip"
                    with zipfile.ZipFile(zip_path, 'w', zipfile.ZIP_DEFLATED) as zipf:
                        for file in report_dir.rglob('*'):
                            if file.is_file():
                                zipf.write(file, file.relative_to(report_dir))

                    minio_path = f"web-test-reports/{project_identifier}/{web_test_id}/{test_run.id}/report.zip"
                    zip_bytes = zip_path.read_bytes()
                    MinIOClient.upload_bytes(
                        object_name=minio_path,
                        data=zip_bytes,
                        content_type="application/zip",
                    )
                    # rev36：创建 WEB_TEST_REPORT 附件（report-html / report-files 以附件 ID 为入口）
                    from app.models.attachment import Attachment, AttachmentEntityType

                    report_att = Attachment(
                        entity_type=AttachmentEntityType.WEB_TEST_REPORT,
                        entity_id=test_run.id,
                        project_id=project.id,
                        file_name="report.zip",
                        file_size=len(zip_bytes),
                        content_type="application/zip",
                        object_name=minio_path,
                        description=f"Web 测试 {web_test.identifier} 执行报告（{test_run.identifier}，"
                                    f"自评 {getattr(runner_result, 'success', False)}）",
                        created_by="system",
                    )
                    self.session.add(report_att)
                    await self.session.flush()
                    report_attachment_id = str(report_att.id)
                    try:
                        zip_path.unlink()
                    except Exception:
                        pass
                except Exception as e:
                    print(f"[WebTestService] 上传报告失败: {e}")

            # 5. 更新运行记录为完成状态
            duration_ms = runner_result.duration_ms
            summary = runner_result.result_summary or {}
            total_count = int(summary.get("total", 0) or 0)
            passed_count = int(summary.get("passed", 0) or 0)
            failed_count = int(summary.get("failed", 0) or 0)
            skipped_count = int(summary.get("skipped", 0) or 0)
            if runner_result.success:
                await self.web_test_run_repo.update(
                    test_run,
                    status="completed",
                    duration_ms=duration_ms,
                    total_tests=total_count,
                    passed_tests=passed_count,
                    failed_tests=failed_count,
                    skipped_tests=skipped_count,
                    report_path=report_attachment_id,
                )
                await self.session.commit()

                # 更新关联子功能的执行统计
                if web_test.sub_function_id:
                    sub_function = await self.session.get(
                        WebSubFunction, web_test.sub_function_id
                    )
                    if sub_function:
                        sub_function.total_test_runs = (
                            sub_function.total_test_runs or 0
                        ) + 1
                        sub_function.last_run_status = (
                            "passed" if failed_count == 0 else "failed"
                        )
                        await self.session.commit()

                return {
                    "run_id": str(test_run.id),
                    "identifier": test_run.identifier,
                    "status": "completed",
                    "duration_ms": duration_ms,
                    "total_tests": total_count,
                    "passed_tests": passed_count,
                    "failed_tests": failed_count,
                    "skipped_tests": skipped_count,
                    "report_path": report_attachment_id,
                    "stdout": runner_result.stdout[:5000] if runner_result.stdout else "",
                    "stderr": runner_result.stderr[:2000] if runner_result.stderr else "",
                }
            else:
                # 执行失败 - 登记 IDP 缺陷
                error_msg = runner_result.error_message or "未知错误"

                # IDP 缺陷登记（异步，不阻塞返回）
                try:
                    project = await self._get_project_by_identifier(project_identifier)
                    registration_service = DefectRegistrationService(self.session)
                    # WebTest 模型使用 base_url 而非 target_url
                    page_url = web_test.base_url or ""
                    await registration_service.register_from_web_failure(
                        test_run_id=test_run.id,
                        test_case_id=None,
                        source_project_key=project.identifier if project else "UNKNOWN",
                        scenario_name=web_test.name or "Web测试",
                        page_url=page_url,
                        action="EXECUTE",
                        request_summary={"url": page_url},
                        response_summary={"status_code": 500},
                        error_message=error_msg,
                        report_url=report_attachment_id,
                    )
                except Exception as idp_err:
                    # IDP 登记失败不影响测试结果返回
                    import logging
                    logging.getLogger(__name__).warning(
                        "[WebTest] IDP 缺陷登记失败（不影响测试结果）: %s", idp_err
                    )

                await self.web_test_run_repo.update(
                    test_run,
                    status="failed",
                    duration_ms=duration_ms,
                    error_message=error_msg,
                    total_tests=total_count,
                    passed_tests=passed_count,
                    failed_tests=failed_count,
                    skipped_tests=skipped_count,
                    report_path=report_attachment_id,
                )
                await self.session.commit()

                # 更新关联子功能的执行统计
                if web_test.sub_function_id:
                    sub_function = await self.session.get(
                        WebSubFunction, web_test.sub_function_id
                    )
                    if sub_function:
                        sub_function.total_test_runs = (
                            sub_function.total_test_runs or 0
                        ) + 1
                        sub_function.last_run_status = "failed"
                        await self.session.commit()

                return {
                    "run_id": str(test_run.id),
                    "identifier": test_run.identifier,
                    "status": "failed",
                    "duration_ms": duration_ms,
                    "error_message": error_msg,
                    "total_tests": total_count,
                    "passed_tests": passed_count,
                    "failed_tests": failed_count,
                    "skipped_tests": skipped_count,
                    "report_path": report_attachment_id,
                    "stdout": runner_result.stdout[:5000] if runner_result.stdout else "",
                    "stderr": runner_result.stderr[:2000] if runner_result.stderr else "",
                }

        except Exception as e:
            # 执行过程中发生异常
            import traceback
            error_msg = f"执行 Web 测试时发生错误: {str(e)}"
            print(f"[WebTestService] {error_msg}")
            traceback.print_exc()

            await self.web_test_run_repo.update(
                test_run,
                status="failed",
                error_message=error_msg,
                report_path=report_attachment_id,
            )
            await self.session.commit()

            # 更新关联子功能的执行统计
            if web_test.sub_function_id:
                sub_function = await self.session.get(
                    WebSubFunction, web_test.sub_function_id
                )
                if sub_function:
                    sub_function.total_test_runs = (
                        sub_function.total_test_runs or 0
                    ) + 1
                    sub_function.last_run_status = "failed"
                    await self.session.commit()

            return {
                "run_id": str(test_run.id),
                "identifier": test_run.identifier,
                "status": "failed",
                "error_message": error_msg,
                "report_path": report_attachment_id,
            }

        finally:
            # 清理临时脚本文件
            if script_file_path and script_file_path.exists():
                try:
                    script_file_path.unlink()
                    print(f"[WebTestService] 临时脚本已清理: {script_file_path}")
                except Exception as e:
                    print(f"[WebTestService] 清理临时脚本失败: {e}")

            # 报告目录清理已移至报告保存完成后执行
            # 避免在报告还未保存时就被删除

    async def get_test_runs(
        self,
        project_identifier: str,
        web_test_id: str,
        page: int = 1,
        page_size: int = 20,
    ) -> dict:
        """获取测试运行历史"""
        project = await self._get_project_by_identifier(project_identifier)
        web_test = await self.web_test_repo.get_by_id(UUID(web_test_id))

        if not web_test or web_test.project_id != project.id:
            raise NotFoundException(resource_type="Web 测试", resource_id=web_test_id)

        offset = (page - 1) * page_size
        items, total = await self.web_test_run_repo.get_by_web_test(
            web_test.id,
            offset=offset,
            limit=page_size,
        )

        return {
            "items": [
                {
                    "id": str(item.id),
                    "identifier": item.identifier,
                    "status": item.status,
                    "total_tests": item.total_tests,
                    "passed_tests": item.passed_tests,
                    "failed_tests": item.failed_tests,
                    "skipped_tests": item.skipped_tests,
                    "duration_ms": item.duration_ms,
                    "error_message": item.error_message,
                    "created_at": item.created_at.isoformat(),
                }
                for item in items
            ],
            "total": total,
            "page": page,
            "page_size": page_size,
        }

    async def get_folder_web_tests(
        self,
        project_identifier: str,
        folder_id: str,
        page: int = 1,
        page_size: int = 20,
    ) -> dict:
        """获取文件夹下的 Web 测试列表"""
        from sqlalchemy import select, func

        project = await self._get_project_by_identifier(project_identifier)

        query = select(WebTest).where(
            WebTest.project_id == project.id,
            WebTest.folder_id == UUID(folder_id)
        )

        count_result = await self.session.execute(
            select(func.count()).select_from(query.subquery())
        )
        total = count_result.scalar_one()
# noqa  My80OmFIVnBZMlhscm9ua3VMazZZMWxyYVE9PTo5YjE2ZTg0MA==

        offset = (page - 1) * page_size
        query = query.order_by(WebTest.created_at.desc())
        query = query.offset(offset).limit(page_size)

        result = await self.session.execute(query)
        items = list(result.scalars().all())

        return {
            "items": [
                {
                    "id": str(item.id),
                    "identifier": item.identifier,
                    "name": item.name,
                    "base_url": item.base_url,
                    "description": item.description,
                    "script_format": item.script_format,
                    "total_pages": item.total_pages,
                    "total_flows": item.total_flows,
                    "created_at": item.created_at.isoformat(),
                }
                for item in items
            ],
            "total": total,
            "page": page,
            "page_size": page_size,
        }
