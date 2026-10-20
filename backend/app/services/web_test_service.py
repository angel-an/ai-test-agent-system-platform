"""
Web 测试服务

处理 Web 测试相关的业务逻辑
"""

import json
import logging
import os
from pathlib import Path
from datetime import datetime
from typing import Any, Dict, Optional
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
from app.services.execution.runner import resolve_timeout_tiers
from app.services.web_test_compile_manifest import compile_manifest_view

logger = logging.getLogger(__name__)

#: 执行日志落库上限。此前 Web 路径截断到 5000 字符，实测 TR-63 的 stdout 恰好 5000 字符、
#: 末行 COMPILED_ASSERTION_SUMMARY 被切掉，导致"落库 31 条断言 vs 日志只解析出 29 条"，
#: 复核时无法自证。改为与标准执行器（executors.py）一致的上限。
_MAX_LOG_CHARS = 50000


async def _resolve_web_test(repo, web_test_id, *, with_relations: bool = False):
    """按 UUID（内部 id）或对外标识符（如 `WT-1C23A7A2`）解析 Web 测试。

    历史问题：旧代码直接 `UUID(web_test_id)`，非 UUID 标识符会抛 ValueError 未被拦截，
    实测 `GET /web-tests/WT-1C23A7A2` 返回 500 internal_server_error。
    现在：UUID → 按 id 查；其它 → 按 identifier 查；都查不到返回 None（由调用方 404）。
    """
    raw = str(web_test_id)
    try:
        uuid_value: Optional[UUID] = UUID(raw)
    except (ValueError, AttributeError, TypeError):
        uuid_value = None

    if uuid_value is not None:
        if with_relations:
            return await repo.get_by_id_with_relations(uuid_value)
        return await repo.get_by_id(uuid_value)

    # 非 UUID：按对外标识符解析（不存在时返回 None → 404，而不是 500）
    return await repo.get_by_identifier(raw)


def resolve_runner_timeout(execution_config: Optional[Dict[str, Any]]) -> int:
    """解析本次 Playwright **进程**超时（秒）。

    rev116（2026-10-19，实测三例假失败）：三层超时必须严格递减——
    引擎作业超时(job_timeout_seconds，默认 1800s) > 本函数返回的进程超时
    > 脚本侧 ``test.setTimeout``（由 runner 经 ``WEB_TEST_BUDGET_MS`` 下发）。

    历史实现这里写死 600s，而脚本模板里写着 ``test.setTimeout(1800000)``：
    600s 一到进程就被硬杀，Playwright 来不及把结论写进报告 ——
    日志里断言都跑完了、job 状态却是 failed（按状态统计即假失败）。

    rev116-fix（2026-10-19 复核）：本函数不再自己算，改为委托
    :func:`app.services.execution.runner.resolve_timeout_tiers` ——
    三层必须**一次性解算**，否则会出现「显式 7200s 反超 1800s 作业预算」
    「进程 30s 而脚本预算 60s」一类倒挂（实测过）。

    - 显式配置了 ``execution_config.timeout`` → 用它，但受作业预算上限约束；
    - 未配置 → 跟随引擎作业预算，留 60s 给引擎侧取消与终态回写；
    - 配置小到三层装不下 → 抛 ``TimeoutBudgetError``（宁可报配置错，不静默降级）。
    """
    tiers = resolve_timeout_tiers(
        job_budget_s=getattr(settings, "job_timeout_seconds", 1800),
        explicit_runner_s=(execution_config or {}).get("timeout"),
    )
    if tiers.clamped:
        logger.warning(
            "[WebTestService] execution_config.timeout 超过作业预算上限，已裁剪：%s",
            tiers.as_log(),
        )
    return tiers.runner_s


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
        web_test = await _resolve_web_test(self.web_test_repo, web_test_id, with_relations=True)

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
            # 三要素绑定：执行治理要求"真实项目 + 子功能当前脚本附件 + 内容哈希"，
            # 详情接口此前不返回这两项，导致绑定结果无处可查（也没法在 UI 上修复绑定）。
            "test_case_id": str(web_test.test_case_id) if web_test.test_case_id else None,
            "function_id": str(web_test.function_id) if web_test.function_id else None,
            "sub_function_id": str(web_test.sub_function_id) if web_test.sub_function_id else None,
            # N7：编译清单（选执行范围前必须先看清"能生成多少条可执行检查"）
            **compile_manifest_view(web_test),
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
                    # N7：列表页直接展示"可生成检查数 / 编译状态"，无需再打开资产翻 JSONB。
                    # 注意 compiled_check_count 为 null ≠ 0：前者是"不知道"，后者是"确实零检查"。
                    **compile_manifest_view(item),
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
        web_test = await _resolve_web_test(self.web_test_repo, web_test_id)

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
        web_test = await _resolve_web_test(self.web_test_repo, web_test_id)

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
        web_test = await _resolve_web_test(self.web_test_repo, web_test_id)

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
        web_test = await _resolve_web_test(self.web_test_repo, web_test_id)

        if not web_test or web_test.project_id != project.id:
            raise NotFoundException(resource_type="Web 测试", resource_id=web_test_id)

        # 上传到 MinIO
        script_bytes = script_content.encode('utf-8')
        MinIOClient.upload_bytes(
            object_name=web_test.script_path,
            data=script_bytes,
            content_type="text/plain"
        )

    async def persist_case_level_results(
        self,
        *,
        web_test,
        stdout: str,
        parent_test_run_id: Optional[str] = None,
        job_context: Optional[dict] = None,
    ) -> dict[str, int]:
        """把脚本里的逐条断言归属到**具体用例**并写入 test_run_test_cases。

        现状问题（实测）：一个批次 43/53 条用例只编译成一个脚本执行，运行记录里只有
        "锚点用例"一行，其余用例在页面上永远显示"新建"——执行结论只存在于脚本 stdout。
        本方法把 `COMPILED_ASSERTION` 按 `rule_id` 归属到用例；
        没有可执行断言的用例显式写 `not_executed`（不冒充通过、也不留空）。

        N2（2026-10-17）：本方法是**编译断言脚本的唯一写入者**（writer=mapper）。
        写入前经 ``case_result_write_guard`` 判定——旧作业（更小的
        ``execution_order``/``retry_count``）不得覆盖新轮次结论；被拒绝时连明细一起
        跳过，避免"状态没变、latest_result_id 却换了"的二次不一致。

        失败绝不影响运行结论（整体 try/except）。
        """
        if not parent_test_run_id or not (stdout or "").strip():
            return {}
        try:
            from uuid import UUID as _UUID

            from sqlalchemy import select

            from app.models.test_case import TestCase
            from app.models.test_run import TestRunTestCase
            from app.repositories.test_run_repo import TestRunTestCaseRepository
            from app.services.web_case_result_mapper import (
                build_identifier_to_case,
                build_rule_to_case,
                map_case_results,
            )

            anchor_id = getattr(web_test, "test_case_id", None)
            if not anchor_id:
                return {}
            anchor = (await self.session.execute(
                select(TestCase).where(TestCase.id == anchor_id)
            )).scalar_one_or_none()
            if anchor is None:
                return {}

            # 批次范围（rev71 修复）：锚点用例的 requirement_id 可能指向**历史批次**
            # （实测 CS：锚点 TC-824119 挂旧需求 req-mueukebw-25k1e4，而 9/24 重建的
            # 96 条用例挂 req-cs-spec-2.0-20260924——按 requirement_id 圈批次会让
            # 断言归属全部落空，TR-109 实测 36 条全 NOT_EXECUTED）。
            # 改为：锚点所在 folder 的用例 ∪ stdout 断言显式引用的 case_id（同项目内）。
            from app.services.web_case_result_mapper import parse_compiled_assertions

            referenced_ids = {
                a["case_id"] for a in parse_compiled_assertions(stdout) if a["case_id"]
            }
            stmt = select(TestCase).where(TestCase.project_id == anchor.project_id)
            if anchor.folder_id:
                stmt = stmt.where(TestCase.folder_id == anchor.folder_id)
            elif anchor.requirement_id:
                stmt = stmt.where(TestCase.requirement_id == anchor.requirement_id)
            else:
                stmt = stmt.where(TestCase.id == anchor.id)
            cases = list((await self.session.execute(stmt)).scalars().all())
            have_ids = {c.identifier for c in cases if c.identifier}
            missing_refs = referenced_ids - have_ids
            if missing_refs:
                extra = list((await self.session.execute(
                    select(TestCase).where(
                        TestCase.project_id == anchor.project_id,
                        TestCase.identifier.in_(missing_refs),
                    )
                )).scalars().all())
                known = {c.id for c in cases}
                cases.extend(c for c in extra if c.id not in known)
            if not cases:
                return {}

            # deprecated=true 的废弃用例不得接收**新的**执行结论（2026-09-29 复核：
            # "废弃禁止后续执行，但保留历史结果"）。历史 TestResult/TestRunTestCase
            # 行不动（不改写历史覆盖率）；这里只把它们从本次归属中剔除——即使旧资产
            # 的脚本/清单里仍编译着它们，也不会再被写入新结果。
            from app.services.deprecation import filter_active, is_deprecated
            dropped = [c.identifier for c in cases if is_deprecated(c)]
            if dropped:
                logger.warning(
                    "[web_test] 执行结论归属跳过已废弃用例 %s（deprecated=true）",
                    dropped,
                )
            cases = filter_active(cases)
            if not cases:
                return {}

            rule_to_case = build_rule_to_case(cases)
            results = map_case_results(
                stdout, rule_to_case, case_ids=[str(c.id) for c in cases],
                identifier_to_case=build_identifier_to_case(cases),
            )

            repo = TestRunTestCaseRepository(self.session)
            parent_uuid = _UUID(str(parent_test_run_id))
            # N2：本轮写入的轮次上下文（作业层传入；手工触发时为空 → 轮次未知）
            from app.services.case_result_write_guard import (
                CaseWriteState,
                build_attempt,
                decide_write,
            )

            import logging

            _ctx = job_context or {}
            attempt = build_attempt(
                "mapper",
                job_id=_ctx.get("job_id"),
                execution_order=_ctx.get("execution_order"),
                retry_count=_ctx.get("retry_count"),
            )
            _logger = logging.getLogger(__name__)
            # rev87：NOT_EXECUTED 默认行只允许写入"本次运行 payload 里的用例"
            # （TestRunTestCase 行在组 run 时只为 payload 用例创建）。多 job 同 run
            # （多表单分发）时，每个 job 以文件夹全量做归属——不限制会把兄弟 job 已
            # 写入的结论重置（实测 TR-117/119：后执行 job 把先执行 job 的 PASSED
            # 覆盖成 NOT_EXECUTED），还会给根本没进本次运行的用例造幻影未执行行。
            from sqlalchemy import select as _sa_select2
            _rc = await self.session.execute(
                _sa_select2(TestRunTestCase.test_case_id).where(
                    TestRunTestCase.test_run_id == parent_uuid)
            )
            payload_ids = {str(x) for x in _rc.scalars().all()}
            written: dict[str, int] = {}
            rejected: dict[str, int] = {}
            for case_id, res in results.items():
                status = TestResultStatus(str(res["status"]).lower())
                # 无断言证据且不在本次运行 payload → 不属于本 job 的结论，跳过
                if not res.get("assertions") and case_id not in payload_ids:
                    continue
                # 无断言证据但 payload 里已有兄弟 job 写的明细 → 不重置
                if not res.get("assertions"):
                    from sqlalchemy import select as _sa_select
                    from app.models.test_result import TestResult as _TR
                    _existing = await self.session.execute(
                        _sa_select(_TR.id).where(
                            _TR.test_run_id == parent_uuid,
                            _TR.test_case_id == _UUID(case_id),
                        )
                    )
                    if _existing.scalar_one_or_none() is not None:
                        continue
                row = await repo.get_by_test_run_and_case(parent_uuid, _UUID(case_id))
                # N2：先判定这次写入是否该生效（轮次 + 写入者优先级）。
                # 被拒绝时**连明细一起跳过**——只拦住状态、却把 latest_result_id 换掉，
                # 会让"可解释明细"与"结论"互相矛盾。
                decision = decide_write(
                    CaseWriteState.from_row(row) if row is not None else CaseWriteState(),
                    attempt,
                )
                if not decision.allowed:
                    rejected[decision.reason] = rejected.get(decision.reason, 0) + 1
                    _logger.info(
                        "[web_test] 用例 %s 结论写入被拒绝（%s）：%s",
                        case_id, decision.reason, decision.detail,
                    )
                    continue

                row, _decision = await repo.apply_case_result(
                    test_run_id=parent_uuid,
                    test_case_id=_UUID(case_id),
                    status=status,
                    writer="mapper",
                    job_id=attempt.job_id,
                    execution_order=attempt.execution_order,
                    retry_count=attempt.retry_count,
                    create_if_missing=True,
                )
                if row is None:
                    continue

                # 明细落库：说明"为什么是这个状态"。
                # **每条用例都写**（含无断言者）——既让原因可查，也让"是否已有细粒度结果"可判定，
                # 从而让引擎的粗粒度同步可以安全让位（见 engine._should_skip_coarse_case_sync）。
                summary = str(res.get("summary") or "") or "（无明细）"
                try:
                    from app.models.test_result import TestResult

                    # 重执行场景先清掉同 run+case 的旧明细，避免每次重跑累积重复行
                    #（latest_status 有 get_or_create 去重，明细此前是无条件 insert）。
                    from sqlalchemy import delete as _sa_delete

                    await self.session.execute(_sa_delete(TestResult).where(
                        TestResult.test_run_id == parent_uuid,
                        TestResult.test_case_id == _UUID(case_id),
                    ))

                    result_row = TestResult(
                        test_run_id=parent_uuid,
                        test_case_id=_UUID(case_id),
                        test_run_test_case_id=row.id,
                        status=status,
                        description=summary,
                        custom_fields={
                            "assertions": res.get("assertions", 0),
                            "unverified": res.get("unverified_count", 0),
                            "passed": res.get("pass_count", 0),
                            "source": "compiled_assertions",
                        },
                    )
                    self.session.add(result_row)
                    await self.session.flush()
                    await repo.apply_case_result(
                        test_run_id=parent_uuid,
                        test_case_id=_UUID(case_id),
                        status=status,
                        writer="mapper",
                        result_id=result_row.id,
                        job_id=attempt.job_id,
                        execution_order=attempt.execution_order,
                        retry_count=attempt.retry_count,
                        create_if_missing=True,
                    )
                except Exception as exc:  # noqa: BLE001 —— 明细失败不影响状态落库
                    print("[WebTestService] 用例明细写入失败（忽略）: %s" % exc)
                written[status.value] = written.get(status.value, 0) + 1

            # rev70c：回写用例自动化状态——凡拿到可执行结论（非 not_executed）的用例
            # 标记 AUTOMATED，避免"已自动执行多轮但仍显示 NOT_AUTOMATED"的标记滞后
            #（not_executed = 无可执行断言，保持 NOT_AUTOMATED 便于筛选补断言）。
            try:
                from sqlalchemy import update as _sa_update

                from app.schemas.enums import AutomationStatus as _AutoStatus

                automated_ids = [
                    _UUID(cid) for cid, res in results.items()
                    if str(res.get("status", "")).lower() != "not_executed"
                ]
                if automated_ids:
                    await self.session.execute(
                        _sa_update(TestCase)
                        .where(TestCase.id.in_(automated_ids),
                               TestCase.automation_status != _AutoStatus.AUTOMATED)
                        .values(automation_status=_AutoStatus.AUTOMATED)
                    )
            except Exception as exc:  # noqa: BLE001 —— 标记回写失败不影响结果落库
                print("[WebTestService] automation_status 回写失败（忽略）: %s" % exc)

            await self.session.commit()
            print("[WebTestService] 用例级执行结果已落库: %s（共 %d 条用例，写入被拒 %s）" % (
                written, len(results), rejected or "无"))
            if rejected:
                print("[WebTestService] 写入被拒明细（N2 守卫）: %s" % rejected)
            return written
        except Exception as exc:  # noqa: BLE001 —— 落库失败不得影响运行结论
            import logging
            logging.getLogger(__name__).warning(
                "[WebTestService] 用例级结果落库失败（不影响运行结论）: %s", exc
            )
            return {}

    async def register_failure_defect(
        self,
        *,
        project_identifier: str,
        web_test,
        test_run_id,
        error_msg: str,
        report_attachment_id: Optional[str] = None,
        parent_test_run_id: Optional[str] = None,
    ) -> None:
        """为一次**已失败**的运行登记 IDP 缺陷。

        重试场景由执行器在"确定不再重试"之后调用，保证：
        - 非最终尝试不建单（不产生重复单）；
        - 最终失败**一定**建单（不因"本打算重试"而把失败吞掉）。
        登记失败不影响测试结论。
        """
        import logging

        try:
            project = await self._get_project_by_identifier(project_identifier)
            registration_service = DefectRegistrationService(self.session)
            # WebTest 模型使用 base_url 而非 target_url
            page_url = web_test.base_url or ""
            await registration_service.register_from_web_failure(
                test_run_id=test_run_id,
                test_case_id=None,
                source_project_key=project.identifier if project else "UNKNOWN",
                scenario_name=web_test.name or "Web测试",
                page_url=page_url,
                action="EXECUTE",
                request_summary={"url": page_url},
                # 不伪造 HTTP 状态码：UI 测试失败（浏览器关闭、定位器超时等）
                # 并没有观测到任何 HTTP 响应，硬编码 500 会被缺陷判定误判为
                # "状态码不匹配"，产出与真实错误无关的缺陷标题。
                response_summary={},
                error_message=error_msg,
                report_url=report_attachment_id,
                parent_test_run_id=(
                    UUID(str(parent_test_run_id)) if parent_test_run_id else None
                ),
            )
            # 必须提交：本方法在 run 的 update/commit **之后**被调用，
            # 若只 flush 不 commit，会话关闭时这条缺陷记录会被回滚掉（实测踩过）。
            await self.session.commit()
        except Exception as idp_err:  # noqa: BLE001
            logging.getLogger(__name__).warning(
                "[WebTest] IDP 缺陷登记失败（不影响测试结果）: %s", idp_err
            )

    async def run_web_test(
        self,
        project_identifier: str,
        web_test_id: str,
        execution_config: Optional[dict] = None,
        register_defect: bool = True,
        parent_test_run_id: Optional[str] = None,
    ) -> dict:
        """
        执行 Web 测试

        流程：
        1. 创建测试运行记录（状态: pending）
        2. 从 MinIO 下载测试脚本到 workspace
        3. 使用 PlaywrightRunner 执行测试
        4. 更新运行记录状态为 completed/failed
        5. 将测试报告上传到 MinIO

        Args:
            register_defect: 失败时是否登记 IDP 缺陷。重试场景下非最终尝试传 False，
                避免为同一次 flake 重复建单。
            parent_test_run_id: 本次执行所属的 TestRun（作业层传入）。
                用于把逐条断言的执行结果按**用例**落库（否则页面只有锚点用例有状态）。
        """
        import asyncio
        import zipfile
        from pathlib import Path

        from app.services.execution.runner import PlaywrightRunner
        from app.config.settings import settings

        project = await self._get_project_by_identifier(project_identifier)
        web_test = await _resolve_web_test(self.web_test_repo, web_test_id)

        if not web_test or web_test.project_id != project.id:
            raise NotFoundException(resource_type="Web 测试", resource_id=web_test_id)

        # 创建测试运行记录
        identifier = f"WTR-{datetime.now().strftime('%Y%m%d')}-{uuid4().hex[:6]}"
        # N2：作业层把本轮的执行顺序/重试次数带进 config，用于"旧作业不得覆盖新轮次"
        job_context = {
            "job_id": (execution_config or {}).get("job_id"),
            "execution_order": (execution_config or {}).get("job_execution_order"),
            "retry_count": (execution_config or {}).get("job_retry_count"),
        }
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

            # 1.5 预检（在下载脚本/启动浏览器之前）：绑定、占位地址、凭据
            from app.services.web_preflight import check_web_preflight

            effective_base_url = (
                (execution_config or {}).get("env", {}).get("WEB_TEST_BASE_URL")
                or web_test.base_url
            )
            preflight = check_web_preflight(
                sub_function_id=web_test.sub_function_id,
                base_url=effective_base_url,
                execution_config=execution_config or {},
                test_config=getattr(web_test, "test_config", None),
            )
            if preflight:
                err = f"[{preflight['code']}] {preflight['message']}"
                print(f"[WebTestService] 预检未通过: {err}")
                await self.web_test_run_repo.update(
                    test_run, status="failed", error_message=err
                )
                await self.session.commit()
                return {
                    "run_id": str(test_run.id),
                    "identifier": test_run.identifier,
                    "status": "failed",
                    "guard": "preflight",
                    "preflight": preflight,
                    "final": True,
                    "error_message": err,
                }

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
            from app.services.web_preflight import preflight_provenance_reason

            auth_ok, auth_reason = await authorize_script_execution(
                project.identifier,
                script_file_path,
                workspace_root,
                async_session_factory,
                [_UUID(str(sf_id))],
                # rev62（方案 A）：按**正在执行的 WebTest** 解析脚本附件，
                # 避免同一子功能下多个 WebTest 互相顶掉"当前脚本"。
                script_object_name=web_test.script_path,
            )
            if not auth_ok:
                preflight = preflight_provenance_reason(auth_reason)
                err = f"[{preflight['code']}] {preflight['message']}"
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
                    "preflight": preflight,
                    "final": True,
                    "error_message": err,
                }
            print("[WebTestService] 脚本来源授权通过（HTTP 执行面已纳入 2a）")

            # 3. 执行测试（同时生成 list + html 报告）
            # rev116（2026-10-19，实测三例假失败）：三层超时必须**严格递减**——
            #   引擎作业超时(job_timeout_seconds, 默认 1800s)
            #     > runner 进程超时（本处）
            #       > 脚本 test.setTimeout（由 runner 经 WEB_TEST_BUDGET_MS 下发）
            # 历史实现这里硬编码 600s，而模板里写着 test.setTimeout(1800000)：
            # 600s 一到进程就被杀，Playwright 来不及把结论写进报告，
            # 日志里断言明明跑完了、job 状态却是 failed（按状态统计即假失败）。
            _runner_timeout_s = resolve_runner_timeout(execution_config)
            config_with_reporter = {**(execution_config or {}), "reporter": "list,html"}
            runner_result = await runner.run(
                script_path=script_file_path,
                config=config_with_reporter,
                timeout=_runner_timeout_s,
            )

            # 4. 将报告打包上传到 MinIO
            # rev63（执行证据闭环）：把 runner 的 stdout/stderr 落盘为
            # execution_stdout.log / execution_stderr.log，随报告 ZIP 持久化——
            # 脚本 console.log 的 COMPILED_ASSERTION 逐断言行此前只存在于瞬时
            # stdout（API 响应截断返回），复核时不可回查（实测 TR-88 的 46 条
            # 表单检查执行结果无处可查）。Playwright HTML 报告缺失时兜底用
            # run_dir 打包执行日志，保证本次断言证据不丢。
            report_path = runner_result.report_path
            report_dir: Optional[Path] = None
            if report_path and Path(report_path).exists():
                report_dir = Path(report_path)
            elif (runner_result.stdout or "").strip():
                report_dir = run_dir
            if report_dir is not None:
                try:
                    if runner_result.stdout:
                        (report_dir / "execution_stdout.log").write_text(
                            runner_result.stdout, encoding="utf-8"
                        )
                    if runner_result.stderr:
                        (report_dir / "execution_stderr.log").write_text(
                            runner_result.stderr, encoding="utf-8"
                        )
                except Exception as le:
                    print(f"[WebTestService] 执行日志落盘失败（不影响打包）: {le}")
                try:
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
                    # rev90：entity_id 关联到**父 TestRun**（作业层传入的 parent_test_run_id）——
                    # 此前挂 WTR（web_test_run）导致 TestRun 详情/附件查询按运行 ID 查不到报告。
                    from app.models.attachment import Attachment, AttachmentEntityType

                    report_att = Attachment(
                        entity_type=AttachmentEntityType.WEB_TEST_REPORT,
                        entity_id=_UUID(str(parent_test_run_id)) if parent_test_run_id else test_run.id,
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

                # 用例级执行结果落库（只写状态，不影响运行结论）
                await self.persist_case_level_results(
                    web_test=web_test,
                    stdout=runner_result.stdout or "",
                    parent_test_run_id=parent_test_run_id,
                    job_context=job_context,
                )

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
                    "stdout": runner_result.stdout[:_MAX_LOG_CHARS] if runner_result.stdout else "",
                    "stderr": runner_result.stderr[:_MAX_LOG_CHARS] if runner_result.stderr else "",
                }
            else:
                # 执行失败 - 登记 IDP 缺陷（重试场景下由调用方决定是否登记）
                error_msg = runner_result.error_message or "未知错误"

                if register_defect:
                    # IDP 缺陷登记（异步，不阻塞返回）
                    await self.register_failure_defect(
                        project_identifier=project_identifier,
                        web_test=web_test,
                        test_run_id=test_run.id,
                        error_msg=error_msg,
                        report_attachment_id=report_attachment_id,
                    )
                else:
                    # 非最终尝试：等重试结果，避免为同一次 flake 重复建单
                    import logging
                    logging.getLogger(__name__).info(
                        "[WebTest] 非最终尝试失败，跳过缺陷登记（等待重试）: %s",
                        str(error_msg)[:120],
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

                # 用例级执行结果落库（失败运行同样需要：失败时更该分清哪条用例没过）
                await self.persist_case_level_results(
                    web_test=web_test,
                    stdout=runner_result.stdout or "",
                    parent_test_run_id=parent_test_run_id,
                    job_context=job_context,
                )

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
                    "stdout": runner_result.stdout[:_MAX_LOG_CHARS] if runner_result.stdout else "",
                    "stderr": runner_result.stderr[:_MAX_LOG_CHARS] if runner_result.stderr else "",
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
        web_test = await _resolve_web_test(self.web_test_repo, web_test_id)

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
                    # N7：文件夹视图同样是"列表页"，口径必须与主列表一致
                    **compile_manifest_view(item),
                    "created_at": item.created_at.isoformat(),
                }
                for item in items
            ],
            "total": total,
            "page": page,
            "page_size": page_size,
        }
