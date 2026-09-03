"""
Web 测试成果物管理工具

用于保存和查询 Web 子功能相关的测试成果物：
- 测试计划 (test_plan)
- 测试用例 (test_case)
- 测试脚本 (test_script)
- 测试报告 (test_report)
"""
"""
andan
"""


import json
import io
import os
from uuid import UUID, uuid4
from typing import Optional
from datetime import datetime, timezone
from pathlib import Path

from langchain_core.tools import tool
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.attachment import Attachment, AttachmentEntityType
from app.models.web_function import WebSubFunction
from app.models.web_test import WebTest, WebTestRun
from app.config.minio_client import MinIOClient
from app.config.database import async_session_factory
from app.config.settings import settings


# ============================================================================
# 对象名称工具函数
# ============================================================================

def _build_object_name(*parts: str) -> str:
    """
    构建 MinIO 对象名称，自动过滤空段落并去除多余的 /

    避免 project_identifier 为空时出现双斜杠（如 web-tests//sub-functions/...），
    因为 MinIO SDK 会将其视为包含不支持的字符。
    """
    return "/".join(part.strip("/") for part in parts if part and part.strip("/"))


_RESULT_PATTERNS_FAIL_STRONG = [
    r"执行结果[：:]\s*❌?\s*(失败|不通过)",
    r"执行结果[：:]\s*❌?\s*failed?\b",
    r"结论[：:]\s*[^\n]*失败",
]
_RESULT_PATTERNS_PASS_STRONG = [
    r"执行结果[：:]\s*✅?\s*(通过|全部通过)",
    r"执行结果[：:]\s*✅?\s*passed?\b",
    r"执行结果[：:]\s*✅?\s*success\b",
    r"结论[：:]\s*[^\n]*全部.*通过",
    r"通过率[：:]?\s*100\s*%",
]


def _detect_report_result_tag(text: str) -> str:
    """从报告文本中判断本次执行的结果标签,返回 "通过" / "失败" / "执行"。

    判定顺序:
    1. 强信号:显式的"执行结果"/"结论"/"通过率 100%"
    2. 数字计数:提取 "N failed" 与 "N passed",按数值(而非出现次数)比较
    3. 无法判定时返回 "执行" 表示中性态,不会覆盖已有状态
    """
    if not text:
        return "执行"
    import re

    for pat in _RESULT_PATTERNS_FAIL_STRONG:
        if re.search(pat, text, re.IGNORECASE):
            return "失败"
    for pat in _RESULT_PATTERNS_PASS_STRONG:
        if re.search(pat, text, re.IGNORECASE):
            return "通过"

    # 数字计数: 加总所有 "N failed" / "N passed",按数值比较
    total_failed = 0
    total_passed = 0
    for m in re.finditer(r"(\d+)\s*failed?\b", text, re.IGNORECASE):
        total_failed += int(m.group(1))
    for m in re.finditer(r"failed?\s*[:：]\s*(\d+)", text, re.IGNORECASE):
        total_failed += int(m.group(1))
    for m in re.finditer(r"失败\s*[:：]\s*(\d+)", text):
        total_failed += int(m.group(1))
    for m in re.finditer(r"(\d+)\s*passed?\b", text, re.IGNORECASE):
        total_passed += int(m.group(1))
    for m in re.finditer(r"passed?\s*[:：]\s*(\d+)", text, re.IGNORECASE):
        total_passed += int(m.group(1))
    for m in re.finditer(r"通过\s*[:：]\s*(\d+)", text):
        total_passed += int(m.group(1))

    if total_failed > 0:
        return "失败"
    if total_passed > 0:
        return "通过"
    return "执行"


# ============================================================================
# 本地备份配置
# ============================================================================

def _get_backup_root() -> Path:
    """获取本地备份根目录

    按优先级检测：web_cli -> web_mcp -> webwright
    用于支持不同 web 测试模式
    """
    # 按优先级检测各个 workspace 目录
    for root_attr in ['web_cli_workspace_root', 'web_mcp_workspace_root', 'webwright_workspace_root']:
        root_path = Path(getattr(settings, root_attr, '')).resolve()
        if root_path.exists():
            return root_path / "artifacts_backup"
    # 默认返回 web_cli（保持向后兼容）
    return Path(settings.web_cli_workspace_root).resolve() / "artifacts_backup"


def _backup_to_local(object_name: str, content: bytes) -> Path:
    """
    将文件备份到本地文件系统

    Args:
        object_name: MinIO 对象名称（作为相对路径）
        content: 文件内容

    Returns:
        Path: 本地备份路径
    """
    backup_root = _get_backup_root()
    backup_path = backup_root / object_name

    # 确保目录存在
    backup_path.parent.mkdir(parents=True, exist_ok=True)

    # 写入文件
    with open(backup_path, 'wb') as f:
        f.write(content)

    return backup_path


def _restore_from_local(object_name: str) -> Optional[bytes]:
    """
    从本地文件系统恢复文件

    Args:
        object_name: MinIO 对象名称（作为相对路径）

    Returns:
        Optional[bytes]: 文件内容，如果不存在则返回 None
    """
    backup_root = _get_backup_root()
    backup_path = backup_root / object_name

    if backup_path.exists():
        with open(backup_path, 'rb') as f:
            return f.read()
    return None


def _resolve_workspace_path(file_path: str) -> Path:
    """
    解析文件路径，支持 workspace 中的相对路径

    Args:
        file_path: 文件路径（可以是绝对路径或相对路径）

    Returns:
        解析后的绝对路径
    """
    path = Path(file_path)

    # 获取 Web workspace 根目录（优先 web_cli，回退 web_mcp）
    cli_root = Path(settings.web_cli_workspace_root).resolve()
    workspace_root = cli_root if cli_root.exists() else Path(settings.web_mcp_workspace_root).resolve()

    # 在 Windows 上，以 / 开头的路径不是真正的绝对路径（没有盘符）
    # 应该被当作相对路径处理，避免解析到 C:\
    if os.name == 'nt':  # Windows
        # 将 / 开头的路径当作相对路径
        if file_path.startswith('/') or file_path.startswith('\\'):
            # 去掉开头的 / 或 \
            file_path = file_path.lstrip('/\\')
            path = Path(file_path)

    # 如果是绝对路径，直接返回
    if path.is_absolute():
        return path

    # 检查文件是否在当前工作目录存在
    if path.exists():
        return path.resolve()
# fmt: off  MC80OmFIVnBZMlhscm9ua3VMazZOSEpKVlE9PTo1MDM5ZWY1Yw==

    # 尝试在 workspace 目录中查找
    workspace_path = workspace_root / path
    if workspace_path.exists():
        return workspace_path

    # 尝试在 CLI 输出目录中查找
    cli_output_root = settings.web_cli_workspace_root
    if cli_output_root:
        cli_path = Path(cli_output_root) / path
        if cli_path.exists():
            return cli_path

    # 尝试在 MCP 输出目录中查找（MCP 工具可能使用环境变量指定的目录）
    mcp_output_root = settings.web_mcp_root
    if mcp_output_root:
        mcp_path = Path(mcp_output_root) / path
        if mcp_path.exists():
            return mcp_path

    # 如果都找不到，返回 workspace 路径（让调用方处理错误）
    return workspace_root / path


@tool
async def save_web_test_plan(
    sub_function_id: str,
    plan_path: Optional[str] = None,
    test_plan: Optional[dict] = None,
    plan_content: Optional[str] = None,
    plan_format: str = "markdown",
    project_identifier: str = ""
) -> dict:
    """
    保存 Web 子功能的测试计划到 MinIO

    支持三种方式提供测试计划内容：
    1. 通过 plan_path 指定由 web_planner 生成的测试计划文件路径
    2. 通过 test_plan 直接提供测试计划字典（JSON 格式）
    3. 通过 plan_content 直接提供测试计划内容（Markdown/字符串）

    Args:
        sub_function_id: Web 子功能 ID
        plan_path: 测试计划文件路径（由 web_planner 生成），如 "./web-test-plan.md"
        test_plan: 测试计划内容（字典格式），包含：
            - test_scenarios: 测试场景列表
            - coverage: 覆盖率分析
            - priority: 优先级评估
            - estimated_time: 预估测试时间
        plan_content: 测试计划内容（Markdown/字符串格式），可选
        plan_format: 计划格式（markdown, json），默认为 markdown
        project_identifier: 项目标识符

    Returns:
        dict: 包含 attachment_id 和 file_path 的字典
    """
    # 验证 sub_function_id 是否为有效的 UUID
    try:
        sub_function_uuid = UUID(sub_function_id)
    except (ValueError, AttributeError):
        return {"error": f"Invalid sub_function_id format: {sub_function_id}. Must be a valid UUID."}

    # 获取测试计划内容
    plan_bytes = None
    content_type = None
    file_extension = None

    if plan_path:
        # 从 web_planner 生成的文件读取
        try:
            # 使用智能路径解析
            plan_file = _resolve_workspace_path(plan_path)
            if not plan_file.exists():
                return {
                    "error": f"Test plan file not found: {plan_path}",
                    "hint": f"Resolved path: {plan_file}",
                    "tried_paths": [
                        f"Current: {Path(plan_path).resolve()}",
                        f"Workspace: {Path(settings.web_workspace_root).resolve() / plan_path}",
                        f"MCP: {os.environ.get('WEB_WORKSPACE_ROOT', 'Not set')}"
                    ]
                }
            plan_content = plan_file.read_text(encoding='utf-8')
            plan_bytes = plan_content.encode('utf-8')

            # 根据文件扩展名确定格式
            if plan_file.suffix in ['.md', '.markdown']:
                plan_format = "markdown"
                content_type = "text/markdown"
                file_extension = "md"
            elif plan_file.suffix == '.json':
                plan_format = "json"
                content_type = "application/json"
                file_extension = "json"
            else:
                # 默认使用 markdown
                content_type = "text/markdown"
                file_extension = "md"
        except Exception as e:
            return {"error": f"Failed to read test plan file: {str(e)}"}
    elif test_plan:
        # 从字典生成 JSON
        plan_json = json.dumps(test_plan, ensure_ascii=False, indent=2)
        plan_bytes = plan_json.encode('utf-8')
        content_type = "application/json"
        file_extension = "json"
        plan_format = "json"
    elif plan_content:
        # 直接使用提供的内容
        plan_bytes = plan_content.encode('utf-8')
        if plan_format == "json":
            content_type = "application/json"
            file_extension = "json"
        else:
            content_type = "text/markdown"
            file_extension = "md"
    else:
        return {"error": "Either plan_path, test_plan, or plan_content must be provided"}

    async with async_session_factory() as session:
        # 查询 sub_function
        sub_function_stmt = select(WebSubFunction).where(
            WebSubFunction.id == sub_function_uuid
        )
        sub_function_result = await session.execute(sub_function_stmt)
        sub_function = sub_function_result.scalar_one_or_none()

        if not sub_function:
            return {"error": f"Sub-function {sub_function_id} not found"}

        # 生成 MinIO 对象名称
        object_name = _build_object_name(
            "web-tests", project_identifier, "sub-functions", sub_function_id, f"test-plan.{file_extension}"
        )

        # 上传到 MinIO
        MinIOClient.upload_bytes(
            object_name=object_name,
            data=plan_bytes,
            content_type=content_type
        )

        # 备份到本地文件系统
        try:
            backup_path = _backup_to_local(object_name, plan_bytes)
            print(f"[Backup] 测试计划已备份到本地: {backup_path}")
        except Exception as e:
            print(f"[Backup Warning] 本地备份失败: {e}")

        # 检查是否已存在相同的附件
        existing_stmt = select(Attachment).where(
            Attachment.object_name == object_name
        )
        existing_result = await session.execute(existing_stmt)
        existing_attachment = existing_result.scalar_one_or_none()

        # 生成文件名和描述
        file_name = f"test-plan-{sub_function.display_name}.{file_extension}"
        format_desc = "Markdown" if plan_format == "markdown" else "JSON"
        description = f"Web 子功能 {sub_function.display_name} 的测试计划 ({format_desc})"
# fmt: off  MS80OmFIVnBZMlhscm9ua3VMazZOSEpKVlE9PTo1MDM5ZWY1Yw==

        if existing_attachment:
            # 更新现有附件
            existing_attachment.file_size = len(plan_bytes)
            existing_attachment.content_type = content_type
            existing_attachment.file_name = file_name
            existing_attachment.description = description
            existing_attachment.updated_at = datetime.now()
            attachment = existing_attachment
        else:
            # 创建新附件记录
            attachment = Attachment(
                entity_type=AttachmentEntityType.WEB_TEST_PLAN,
                entity_id=sub_function_uuid,
                project_id=sub_function.project_id,
                file_name=file_name,
                file_size=len(plan_bytes),
                content_type=content_type,
                object_name=object_name,
                description=description,
                created_by="web-agent"
            )
            session.add(attachment)

        await session.commit()
        await session.refresh(attachment)

        return {
            "success": True,
            "attachment_id": str(attachment.id),
            "file_path": object_name,
            "format": plan_format,
            "file_extension": file_extension,
            "message": f"测试计划已保存 ({format_desc})"
        }


@tool
async def save_web_test_cases(
    sub_function_id: str,
    test_cases: list[dict],
    project_identifier: str
) -> dict:
    """
    保存 Web 子功能的测试用例到 MinIO

    Args:
        sub_function_id: Web 子功能 ID
        test_cases: 测试用例列表，每个用例包含：
            - name: 用例名称
            - description: 用例描述
            - steps: 测试步骤
            - expected_result: 预期结果
            - priority: 优先级
            - page_elements: 涉及的页面元素
        project_identifier: 项目标识符

    Returns:
        dict: 包含 attachment_id 和 file_path 的字典
    """
    # 验证 sub_function_id 是否为有效的 UUID
    try:
        sub_function_uuid = UUID(sub_function_id)
    except (ValueError, AttributeError):
        return {"error": f"Invalid sub_function_id format: {sub_function_id}. Must be a valid UUID."}

    async with async_session_factory() as session:
        # 查询 sub_function
        sub_function_stmt = select(WebSubFunction).where(
            WebSubFunction.id == sub_function_uuid
        )
        sub_function_result = await session.execute(sub_function_stmt)
        sub_function = sub_function_result.scalar_one_or_none()

        if not sub_function:
            return {"error": f"Sub-function {sub_function_id} not found"}

        # 序列化测试用例
        cases_json = json.dumps(test_cases, ensure_ascii=False, indent=2)
        cases_bytes = cases_json.encode('utf-8')

        # 生成 MinIO 对象名称
        object_name = _build_object_name(
            "web-tests", project_identifier, "sub-functions", sub_function_id, "test-cases.json"
        )

        # 上传到 MinIO
        MinIOClient.upload_bytes(
            object_name=object_name,
            data=cases_bytes,
            content_type="application/json"
        )

        # 备份到本地文件系统
        try:
            backup_path = _backup_to_local(object_name, cases_bytes)
            print(f"[Backup] 测试用例已备份到本地: {backup_path}")
        except Exception as e:
            print(f"[Backup Warning] 本地备份失败: {e}")

        # 检查是否已存在相同的附件
        existing_stmt = select(Attachment).where(
            Attachment.object_name == object_name
        )
        existing_result = await session.execute(existing_stmt)
        existing_attachment = existing_result.scalar_one_or_none()

        if existing_attachment:
            # 更新现有附件
            existing_attachment.file_size = len(cases_bytes)
            existing_attachment.description = f"Web 子功能 {sub_function.display_name} 的测试用例（共 {len(test_cases)} 个）"
            existing_attachment.updated_at = datetime.now()
            attachment = existing_attachment
        else:
            # 创建新附件记录
            attachment = Attachment(
                entity_type=AttachmentEntityType.WEB_TEST_CASE,
                entity_id=sub_function_uuid,
                project_id=sub_function.project_id,
                file_name=f"test-cases-{sub_function.display_name}.json",
                file_size=len(cases_bytes),
                content_type="application/json",
                object_name=object_name,
                description=f"Web 子功能 {sub_function.display_name} 的测试用例（共 {len(test_cases)} 个）",
                created_by="web-agent"
            )
            session.add(attachment)

        # 更新子功能的测试用例统计
        # 该工具以固定 object_name 全量覆盖 JSON,所以计数也必须与之保持一致:
        # 用当前批次长度赋值,而不是累加(之前累加会把重复生成的批次算多次,前端出现 49 这种脱离实际的数字)。
        sub_function.total_test_cases = len(test_cases)
        sub_function.updated_at = datetime.now(timezone.utc)

        await session.commit()
        await session.refresh(attachment)

        return {
            "success": True,
            "attachment_id": str(attachment.id),
            "file_path": object_name,
            "test_cases_count": len(test_cases),
            "message": f"已保存 {len(test_cases)} 个测试用例"
        }
# pylint: disable  Mi80OmFIVnBZMlhscm9ua3VMazZOSEpKVlE9PTo1MDM5ZWY1Yw==


@tool
async def save_web_test_script(
    sub_function_id: str,
    script_path: Optional[str] = None,
    script_content: Optional[str] = None,
    script_language: str = "typescript",
    script_format: str = "playwright",
    project_identifier: str = "",
    version_mode: str = "effective",
) -> dict:
    """
    保存 Web 子功能的测试脚本到 MinIO

    支持两种方式提供脚本内容：
    1. 通过 script_path 指定由 web_generator 生成的脚本文件路径
    2. 通过 script_content 直接提供脚本内容

    P0-2 版本化：
    - version_mode="effective"（默认，向后兼容）：现状行为——复用当前附件并登记
      为 effective（覆盖当前哈希，旧内容失效）；
    - version_mode="proposed"：创建**新版本附件**（对象名带时间戳后缀）并登记为
      proposed（待评审发布），**不更新** WebTest.script_path（当前生效脚本不变）；
      由 publish_script_version 发布为 effective。

    Args:
        sub_function_id: Web 子功能 ID
        script_path: 脚本文件路径（由 web_generator 生成）
        script_content: 脚本内容（代码），可选
        script_language: 脚本语言（如: typescript, javascript, python）
        script_format: 脚本格式（如: playwright, cypress, selenium）
        project_identifier: 项目标识符
        version_mode: "effective"（默认）| "proposed"

    Returns:
        dict: 包含 attachment_id 和 file_path 的字典
    """
    # 验证 sub_function_id 是否为有效的 UUID
    try:
        sub_function_uuid = UUID(sub_function_id)
    except (ValueError, AttributeError):
        return {"error": f"Invalid sub_function_id format: {sub_function_id}. Must be a valid UUID."}

    # 获取脚本内容
    if script_path:
        # 从 web_generator 生成的文件读取
        try:
            # 使用智能路径解析
            script_file = _resolve_workspace_path(script_path)
            if not script_file.exists():
                return {
                    "error": f"Script file not found: {script_path}",
                    "hint": f"Resolved path: {script_file}",
                    "tried_paths": [
                        f"Current: {Path(script_path).resolve()}",
                        f"Workspace: {Path(settings.web_workspace_root).resolve() / script_path}",
                        f"MCP: {os.environ.get('WEB_WORKSPACE_ROOT', 'Not set')}"
                    ]
                }
            script_content = script_file.read_text(encoding='utf-8')
        except Exception as e:
            return {"error": f"Failed to read script file: {str(e)}"}
    elif not script_content:
        return {"error": "Either script_path or script_content must be provided"}

    # =========================================================================
    # P0: 自动检测脚本语言，确保扩展名与实际内容匹配
    # 防止 LLM 生成 Python 代码但保存为 .ts 文件，导致 Playwright 解析失败
    # =========================================================================
    def _detect_script_language(content: str) -> str:
        """根据内容特征自动检测脚本语言"""
        content_stripped = content.strip()
        first_lines = '\n'.join(content_stripped.split('\n')[:20]).lower()

        # Python 特征检测（优先级高）
        python_indicators = [
            'import asyncio', 'import json', 'import os', 'import sys',
            'from playwright', 'from datetime import', 'import pytest',
            'def test_', 'async def ', 'if __name__ == ',
            'import requests', 'from typing import',
        ]
        # TypeScript/Playwright 特征检测
        ts_indicators = [
            'import { test', 'import { expect }', 'import test from',
            'test.describe(', 'test.beforeeach(', 'test.aftereach(',
            'import { chromium', 'import { page', 'const { test',
            'playwright.config', 'page.goto(', 'page.click(',
            'page.fill(', 'page.locator(', 'expect(page',
        ]

        python_score = sum(1 for ind in python_indicators if ind in first_lines)
        ts_score = sum(1 for ind in ts_indicators if ind in first_lines)

        # 如果有明显的 Python 特征（score >= 2），且比 TS 特征强，判定为 Python
        if python_score >= 2 and python_score > ts_score:
            return "python"
        # 如果有明显的 TS 特征
        if ts_score >= 2 and ts_score > python_score:
            return "typescript"
        # 默认保持传入的 script_language
        return script_language

    detected_language = _detect_script_language(script_content)

    # 如果检测到的语言与传入的参数不一致，自动纠正并记录警告
    if detected_language != script_language:
        print(f"[save_web_test_script] WARNING: 脚本语言不匹配! "
              f"传入参数={script_language}, 实际检测={detected_language}. "
              f"自动纠正为 {detected_language}")
        script_language = detected_language
        # 同时更新 script_format（如果原来是 Python 内容）
        if detected_language == "python" and script_format == "playwright":
            script_format = "webwright"

    async with async_session_factory() as session:
        # 查询 sub_function
        sub_function_stmt = select(WebSubFunction).where(
            WebSubFunction.id == sub_function_uuid
        )
        sub_function_result = await session.execute(sub_function_stmt)
        sub_function = sub_function_result.scalar_one_or_none()

        if not sub_function:
            return {"error": f"Sub-function {sub_function_id} not found"}

        # rev22：先解析真实项目标识（不信任调用方参数），再生成对象名/上传——
        # MinIO 对象存储的项目命名空间同样必须使用真实项目
        from app.agents.tools.web.script_provenance import (
            register_script_provenance,
            resolve_real_project_identifier,
            sha256_hex,
        )
        real_project_identifier = await resolve_real_project_identifier(
            session, sub_function.project_id
        )
        if not real_project_identifier:
            return {
                "success": False,
                "error": f"无法解析子功能真实项目标识（project_id={sub_function.project_id}），脚本未保存",
            }

        # 确定文件扩展名
        extension = {
            "typescript": "ts",
            "javascript": "js",
            "python": "py",
            "java": "java",
        }.get(script_language, "txt")

        # 生成 MinIO 对象名称（使用真实项目标识；proposed 版本带时间戳后缀避免
        # 覆盖当前生效附件）
        _is_proposed = version_mode == "proposed"
        _script_file = (
            f"test-script-{datetime.now().strftime('%Y%m%d%H%M%S')}.{extension}"
            if _is_proposed else f"test-script.{extension}"
        )
        object_name = _build_object_name(
            "web-tests", real_project_identifier, "sub-functions", sub_function_id, _script_file
        )

        # 上传到 MinIO
        script_bytes = script_content.encode('utf-8')
        MinIOClient.upload_bytes(
            object_name=object_name,
            data=script_bytes,
            content_type="text/plain"
        )

        # 备份到本地文件系统
        try:
            backup_path = _backup_to_local(object_name, script_bytes)
            print(f"[Backup] 测试脚本已备份到本地: {backup_path}")
        except Exception as e:
            print(f"[Backup Warning] 本地备份失败: {e}")

        # 检查是否已存在相同的附件（proposed 版本强制新建，不复用）
        existing_attachment = None
        if not _is_proposed:
            existing_stmt = select(Attachment).where(
                Attachment.object_name == object_name
            )
            existing_result = await session.execute(existing_stmt)
            existing_attachment = existing_result.scalar_one_or_none()

        if existing_attachment:
            # 更新现有附件
            existing_attachment.file_size = len(script_bytes)
            existing_attachment.description = f"Web 子功能 {sub_function.display_name} 的测试脚本 ({script_format} - {script_language})"
            existing_attachment.updated_at = datetime.now()
            attachment = existing_attachment
        else:
            # 创建新附件记录（proposed 版本同样新建）
            attachment = Attachment(
                entity_type=AttachmentEntityType.WEB_TEST_SCRIPT,
                entity_id=sub_function_uuid,
                project_id=sub_function.project_id,
                file_name=f"test-script.{extension}",
                file_size=len(script_bytes),
                content_type="text/plain",
                object_name=object_name,
                description=f"Web 子功能 {sub_function.display_name} 的测试脚本 ({script_format} - {script_language})",
                created_by="web-agent"
            )
            session.add(attachment)

        # =========================================================================
        # 创建或更新 WebTest 记录（proposed 版本不更新——当前生效脚本不变；
        # 仅 effective 保存时更新 script_path 指向当前附件）
        # =========================================================================
        web_test = None
        if not _is_proposed:
            wt_stmt = select(WebTest).where(
                WebTest.sub_function_id == sub_function_uuid
            ).order_by(WebTest.created_at.desc())
            wt_result = await session.execute(wt_stmt)
            existing_web_test = wt_result.scalar_one_or_none()

            if existing_web_test:
                # 更新现有 WebTest 的脚本路径
                existing_web_test.script_path = object_name
                existing_web_test.script_format = script_format
                existing_web_test.script_language = script_language
                existing_web_test.updated_at = datetime.now(timezone.utc)
                web_test = existing_web_test
                print(f"[save_web_test_script] 更新现有 WebTest: {web_test.id} (identifier={web_test.identifier})")
            else:
                # 生成唯一标识符
                identifier = f"WT-{uuid4().hex[:8].upper()}"

                web_test = WebTest(
                    project_id=sub_function.project_id,
                    sub_function_id=sub_function.id,
                    function_id=sub_function.function_id,
                    folder_id=sub_function.folder_id,  # 关联到子功能所在的文件夹
                    identifier=identifier,
                    name=sub_function.display_name or "未命名功能",
                    description=f"Auto-created for sub-function {sub_function_id}",
                    script_path=object_name,
                    script_format=script_format,
                    script_language=script_language,
                    generated_by_agent="web-agent"
                )
                session.add(web_test)
                print(f"[save_web_test_script] 创建新 WebTest: {identifier}")

        # =====================================================================
        # 执行治理层 2a（严格来源授权）+ P0-2 版本化：
        #   - 真实项目标识已在对象名生成前解析（rev22，不信任调用方参数）；
        #   - DB 原子 upsert 登记（rev22：ON CONFLICT (attachment_id) DO UPDATE）
        #   - proposed 版本登记为 proposed（待评审发布，不覆盖 effective）
        #   - 与附件/WebTest 同一事务：登记失败整体回滚，返回失败
        # =====================================================================
        await session.flush()  # 生成 attachment.id（UUID default=uuid4）
        await register_script_provenance(
            session,
            real_project_identifier,
            attachment.id,
            sha256_hex(script_bytes),
            script_language,
            script_format,
            version_status="proposed" if _is_proposed else "effective",
        )
        await session.commit()
        await session.refresh(attachment)
        if web_test is not None:
            await session.refresh(web_test)
        print(
            f"[save_web_test_script] 已登记脚本来源: project={real_project_identifier} "
            f"hash={sha256_hex(script_bytes)[:12]}... "
            f"version_status={'proposed' if _is_proposed else 'effective'}"
        )

        result = {
            "success": True,
            "attachment_id": str(attachment.id),
            "file_path": object_name,
            "language": script_language,
            "format": script_format,
            "version_status": "proposed" if _is_proposed else "effective",
            "message": "测试脚本已保存并登记为 proposed（待发布）" if _is_proposed else "测试脚本已保存 (并关联 WebTest 记录)",
        }
        if web_test is not None:
            result["web_test_id"] = str(web_test.id)
            result["web_test_identifier"] = web_test.identifier
        return result


@tool
async def publish_script_version(sub_function_id: str, verified_attachment_id: str = "") -> dict:
    """发布子功能的 proposed 脚本版本为 effective（P0-2）。

    流程：
    1. 取该子功能**已验证**的 proposed 版本（verified_attachment_id 指定；
       空则取最新 proposed）；
    2. 当前 effective 行 → old（历史版本，执行授权不再认）；
    3. proposed 行 → effective（新当前生效版本）；
    4. WebTest.script_path 更新为新版本附件对象名。

    rev44：verified_attachment_id 锁定已验证版本，避免并发下未验证版本被发布。

    Args:
        sub_function_id: Web 子功能 ID
        verified_attachment_id: 已验证的附件 ID（评审流程传入；空 = 取最新 proposed）

    Returns:
        dict: 发布结果（新 effective attachment/object_name/旧版本降级信息）
    """
    try:
        sub_function_uuid = UUID(sub_function_id)
    except (ValueError, AttributeError):
        return {"error": f"Invalid sub_function_id format: {sub_function_id}"}

    verified_uuid = None
    if verified_attachment_id:
        try:
            verified_uuid = UUID(verified_attachment_id)
        except (ValueError, AttributeError):
            return {"error": "verified_attachment_id 非法"}

    async with async_session_factory() as session:
        return await _publish_version_transition(session, sub_function_uuid, verified_uuid)


async def _publish_version_transition(
    session, sub_function_uuid: UUID, verified_attachment_id: UUID | None = None
) -> dict:
    """发布状态转换核心逻辑（可注入 session，便于单测）：
    proposed → effective；旧 effective → old；WebTest.script_path 更新。

    rev44（评审问题 3）：verified_attachment_id 锁定**已验证的** proposed 版本——
    发布指定附件对应的 proposed，不重新选择"最新 proposed"（避免并发下未验证版本
    被发布）。None 时保持旧行为（取最新 proposed）。
    """
    from app.models.web_script_registry import WebScriptRegistry
    from app.models.web_test import WebTest
    from app.models.attachment import Attachment

    # 1. 定位待发布 proposed 行（rev44：锁定已验证 attachment 或取最新）
    proposed_stmt = (
        select(WebScriptRegistry, Attachment.object_name)
        .join(Attachment, Attachment.id == WebScriptRegistry.attachment_id)
        .where(
            Attachment.entity_id == sub_function_uuid,
            WebScriptRegistry.version_status == "proposed",
        )
        .order_by(WebScriptRegistry.created_at.desc(), WebScriptRegistry.id.desc())
        .limit(1)
        .with_for_update()
    )
    if verified_attachment_id is not None:
        proposed_stmt = proposed_stmt.where(
            WebScriptRegistry.attachment_id == verified_attachment_id
        )
    row = (await session.execute(proposed_stmt)).first()
    if row is None:
        return {"error": "该子功能无 proposed 版本，无需发布"}
    new_reg, new_object_name = row

    # 2. 当前 effective 行 → old
    eff_stmt = (
        select(WebScriptRegistry)
        .join(Attachment, Attachment.id == WebScriptRegistry.attachment_id)
        .where(
            Attachment.entity_id == sub_function_uuid,
            WebScriptRegistry.version_status == "effective",
        )
    )
    eff_rows = (await session.execute(eff_stmt)).scalars().all()
    old_hashes = []
    for reg in eff_rows:
        if reg.id != new_reg.id:
            reg.version_status = "old"
            old_hashes.append(reg.script_hash[:12])

    # 3. proposed → effective
    new_reg.version_status = "effective"
    new_hash = new_reg.script_hash
    # commit 前缓存属性值（commit 后 expire 触发同步 lazy load → async MissingGreenlet）
    new_att_id = str(new_reg.attachment_id)

    # 4. WebTest.script_path 更新为新版本附件
    wt_stmt = select(WebTest).where(
        WebTest.sub_function_id == sub_function_uuid
    ).order_by(WebTest.created_at.desc())
    web_test = (await session.execute(wt_stmt)).scalars().first()
    if web_test:
        web_test.script_path = new_object_name
        web_test.updated_at = datetime.now(timezone.utc)

    await session.commit()
    print(
        f"[publish_script_version] 子功能 {sub_function_uuid}: "
        f"proposed -> effective (hash={new_hash[:12]}...)，旧版本 {len(old_hashes)} 个降级 old"
    )
    return {
        "success": True,
        "sub_function_id": str(sub_function_uuid),
        "attachment_id": new_att_id,
        "object_name": new_object_name,
        "script_hash": new_hash,
        "old_versions_downgraded": old_hashes,
        "web_test_updated": web_test is not None,
    }


@tool
async def get_web_sub_function_artifacts(
    sub_function_id: str,
    artifact_type: Optional[str] = None
) -> dict:
    """
    获取 Web 子功能的测试成果物列表

    Args:
        sub_function_id: Web 子功能 ID
        artifact_type: 成果物类型过滤（可选）:
            - WEB_TEST_PLAN: 测试计划
            - WEB_TEST_CASE: 测试用例
            - WEB_TEST_SCRIPT: 测试脚本
            - WEB_TEST_REPORT: 测试报告

    Returns:
        dict: 成果物列表，包含类型、文件名、描述、创建时间等信息
    """
    # 验证 sub_function_id 是否为有效的 UUID
    try:
        sub_function_uuid = UUID(sub_function_id)
    except (ValueError, AttributeError) as e:
        return {"error": f"Invalid sub_function_id format: {sub_function_id}. Must be a valid UUID."}
# pylint: disable  My80OmFIVnBZMlhscm9ua3VMazZOSEpKVlE9PTo1MDM5ZWY1Yw==

    async with async_session_factory() as session:
        # 构建查询
        stmt = select(Attachment).where(
            Attachment.entity_id == sub_function_uuid
        )

        # 按类型过滤
        if artifact_type:
            try:
                entity_type = AttachmentEntityType[artifact_type]
                stmt = stmt.where(Attachment.entity_type == entity_type)
            except KeyError:
                return {"error": f"Invalid artifact_type: {artifact_type}"}

        # 执行查询
        result = await session.execute(stmt)
        attachments = result.scalars().all()

        # 格式化返回
        artifacts = []
        for attachment in attachments:
            artifacts.append({
                "id": str(attachment.id),
                "type": attachment.entity_type.value,
                "file_name": attachment.file_name,
                "description": attachment.description,
                "file_size": attachment.file_size,
                "content_type": attachment.content_type,
                "object_name": attachment.object_name,
                "created_at": attachment.created_at.isoformat() if attachment.created_at else None,
            })

        return {
            "success": True,
            "sub_function_id": sub_function_id,
            "artifacts": artifacts,
            "total": len(artifacts)
        }


@tool
async def save_web_test_report(
    test_run_id: Optional[str] = None,
    sub_function_id: Optional[str] = None,
    report_path: Optional[str] = None,
    report_content: Optional[str] = None,
    screenshots: Optional[list[str]] = None,
    project_identifier: str = ""
) -> dict:
    """
    保存 Web 测试执行报告到 MinIO

    支持两种方式来指定报告归属：
    1. 通过 test_run_id: 直接关联到已有的测试运行记录（需要 UUID 格式）
    2. 通过 sub_function_id: 自动查找或创建关联的测试运行记录（更灵活）

    Args:
        test_run_id: 测试运行 ID（UUID 格式，可选，与 sub_function_id 二选一）
        sub_function_id: 子功能 ID（可选，与 test_run_id 二选一，会自动查找关联的 test run）
        report_path: 报告文件路径（可选）
        report_content: 报告内容（HTML/JSON），可选
        screenshots: 截图文件路径列表，可选
        project_identifier: 项目标识符

    Returns:
        dict: 包含 attachment_id 和 file_path 的字典
    """
    # 参数校验：必须提供 test_run_id 或 sub_function_id 之一
    if not test_run_id and not sub_function_id:
        return {"error": "必须提供 test_run_id 或 sub_function_id 之一"}

    run_uuid = None
    test_run = None
    sf_uuid = None  # 当通过 sub_function_id 调用时，用于将报告关联到子功能

    async with async_session_factory() as session:
        # 方式1：通过 test_run_id 直接查找
        if test_run_id:
            try:
                run_uuid = UUID(test_run_id)
            except (ValueError, AttributeError):
                return {"error": f"Invalid test_run_id format: {test_run_id}. Must be a valid UUID."}

            run_stmt = select(WebTestRun).where(WebTestRun.id == run_uuid)
            run_result = await session.execute(run_stmt)
            test_run = run_result.scalar_one_or_none()

            if not test_run:
                return {"error": f"Test run {test_run_id} not found"}

        # 方式2：通过 sub_function_id 查找或创建关联的 test run
        elif sub_function_id:
            try:
                sf_uuid = UUID(sub_function_id)
            except (ValueError, AttributeError):
                return {"error": f"Invalid sub_function_id format: {sub_function_id}. Must be a valid UUID."}

            # 查询子功能是否存在
            sf_stmt = select(WebSubFunction).where(WebSubFunction.id == sf_uuid)
            sf_result = await session.execute(sf_stmt)
            sub_function = sf_result.scalar_one_or_none()

            if not sub_function:
                return {"error": f"Sub function {sub_function_id} not found"}

            # 查找该子功能关联的 WebTest
            wt_stmt = select(WebTest).where(WebTest.sub_function_id == sf_uuid).order_by(WebTest.created_at.desc())
            wt_result = await session.execute(wt_stmt)
            web_test = wt_result.scalar_one_or_none()

            if not web_test:
                # 如果没有关联的 WebTest，创建一个基本的 WebTest 记录
                from app.models.project import Project
                proj_stmt = select(Project).where(Project.identifier == project_identifier)
                proj_result = await session.execute(proj_stmt)
                project = proj_result.scalar_one_or_none()

                if not project:
                    return {"error": f"Project {project_identifier} not found, cannot create test run"}

                # 生成唯一标识符（使用 uuid4 避免重试冲突）
                identifier = f"WT-{uuid4().hex[:8]}"

                web_test = WebTest(
                    project_id=project.id,
                    sub_function_id=sub_function.id,
                    function_id=sub_function.function_id,
                    folder_id=sub_function.folder_id,  # 关联到子功能所在的文件夹
                    identifier=identifier,
                    name=sub_function.display_name or "未命名功能",
                    description=f"Auto-created for sub-function {sub_function_id}",
                    script_path="",
                    script_format="playwright",
                    script_language="python",
                    generated_by_agent="web-agent"
                )
                session.add(web_test)
                await session.flush()
                print(f"[save_web_test_report] 自动创建 WebTest: {web_test.id}")

            # 查找该 WebTest 最新的 test run
            run_stmt = select(WebTestRun).where(
                WebTestRun.web_test_id == web_test.id
            ).order_by(WebTestRun.created_at.desc())
            run_result = await session.execute(run_stmt)
            test_run = run_result.scalar_one_or_none()

            if not test_run:
                # 如果没有 test run，创建一个新的
                run_identifier = f"WTR-{uuid4().hex[:8]}"

                test_run = WebTestRun(
                    project_id=web_test.project_id,
                    web_test_id=web_test.id,
                    identifier=run_identifier,
                    status="completed",
                    total_tests=0,
                    passed_tests=0,
                    failed_tests=0
                )
                session.add(test_run)
                await session.flush()
                print(f"[save_web_test_report] 自动创建 WebTestRun: {test_run.id}")

            run_uuid = test_run.id

        # 报告关联实体：优先使用子功能 ID，方便在子功能成果物面板展示
        report_entity_id = sf_uuid if sf_uuid is not None else run_uuid

        report_object_name = None
        screenshot_dir = None
        attachment_id = None

        # 保存报告
        if report_path or report_content:
            if report_path:
                try:
                    report_file = _resolve_workspace_path(report_path)
                    if not report_file.exists():
                        return {"error": f"Report file not found: {report_path}"}
                    report_content = report_file.read_text(encoding='utf-8')
                except Exception as e:
                    return {"error": f"Failed to read report file: {str(e)}"}

            report_bytes = report_content.encode('utf-8')
            report_object_name = _build_object_name(
                "web-tests", project_identifier, "runs", str(run_uuid), "report.html"
            )

            MinIOClient.upload_bytes(
                object_name=report_object_name,
                data=report_bytes,
                content_type="text/html"
            )

            # 检查是否已存在相同 object_name 的附件（避免重复插入导致唯一约束冲突）
            existing_stmt = select(Attachment).where(
                Attachment.object_name == report_object_name
            )
            existing_result = await session.execute(existing_stmt)
            existing_attachment = existing_result.scalar_one_or_none()

            # 获取子功能名称（如果通过 sub_function_id 关联）
            sub_function_name = None
            if sf_uuid:
                sf_result = await session.execute(
                    select(WebSubFunction).where(WebSubFunction.id == sf_uuid)
                )
                sf_obj = sf_result.scalar_one_or_none()
                if sf_obj:
                    sub_function_name = sf_obj.display_name

            # 生成前端期望的文件名格式: 功能名-测试报告-结果-YYYYMMDD_HHMMSS.html
            # 解析执行结果状态
            # 之前用宽泛的关键字包含判断,报告里常见"失败: 0" / "0 failed" 都会误判为失败;
            # 现改为优先看结构化标记(通过率 / 明确的"全部通过"总结),再回落到 pass/fail 计数比较。
            result_tag = _detect_report_result_tag(report_content or "")

            # 生成时间戳
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")

            # 安全化子功能名称用于文件名
            if sub_function_name:
                safe_name = sub_function_name.replace(" ", "-").replace("/", "-").replace("\\", "-")
            else:
                safe_name = test_run.identifier or "未命名"

            # 生成符合前端解析规则的文件名: 功能名-测试报告-结果-时间戳.html
            report_file_name = f"{safe_name}-测试报告-{result_tag}-{timestamp}.html"

            if existing_attachment:
                # 更新现有附件记录
                existing_attachment.file_size = len(report_bytes)
                existing_attachment.file_name = report_file_name
                existing_attachment.description = f"Web 测试运行 {test_run.identifier} 的报告"
                existing_attachment.updated_at = datetime.now(timezone.utc)
                session.add(existing_attachment)
                await session.flush()
                attachment_id = str(existing_attachment.id)
                print(f"[save_web_test_report] 更新已存在的附件记录: {attachment_id}")
            else:
                # 创建新的附件记录
                attachment = Attachment(
                    entity_type=AttachmentEntityType.WEB_TEST_REPORT,
                    entity_id=report_entity_id,
                    project_id=test_run.project_id,
                    file_name=report_file_name,
                    file_size=len(report_bytes),
                    content_type="text/html",
                    object_name=report_object_name,
                    description=f"Web 测试运行 {test_run.identifier} 的报告",
                    created_by="web-agent"
                )
                session.add(attachment)
                await session.flush()
                attachment_id = str(attachment.id)

        # 保存截图
        if screenshots:
            screenshot_dir = _build_object_name(
                "web-tests", project_identifier, "runs", str(run_uuid), "screenshots"
            )
            for idx, screenshot_path in enumerate(screenshots):
                try:
                    screenshot_file = _resolve_workspace_path(screenshot_path)
                    if not screenshot_file.exists():
                        continue

                    screenshot_bytes = screenshot_file.read_bytes()
                    screenshot_name = f"screenshot-{idx + 1}{screenshot_file.suffix}"
                    screenshot_object_name = f"{screenshot_dir}/{screenshot_name}"

                    MinIOClient.upload_bytes(
                        object_name=screenshot_object_name,
                        data=screenshot_bytes,
                        content_type="image/png"
                    )
                except Exception as e:
                    # 记录错误但继续处理其他截图
                    print(f"Warning: Failed to save screenshot {screenshot_path}: {e}")

        # 更新 test run 记录
        if report_object_name:
            test_run.report_path = report_object_name
        if screenshot_dir:
            test_run.screenshots_path = screenshot_dir
        test_run.updated_at = datetime.now(timezone.utc)

        # 同步子功能的执行统计
        # save_web_test_report 是 "AI 声明本次执行结束" 的信号,应视为权威结果:
        # - total_test_runs +1
        # - last_run_status 用报告内容判断的 result_tag 反推
        #   (通过 → passed, 失败 → failed, 执行 → 根据 test_run 状态判断)
        # FIX: 2026-08-17 修复 "执行" 时 last_run_status 为 null 的问题
        if sf_uuid and report_object_name:
            sf_result = await session.execute(
                select(WebSubFunction).where(WebSubFunction.id == sf_uuid)
            )
            sf_obj_for_stats = sf_result.scalar_one_or_none()
            if sf_obj_for_stats:
                sf_obj_for_stats.total_test_runs = (sf_obj_for_stats.total_test_runs or 0) + 1
                if result_tag == "通过":
                    sf_obj_for_stats.last_run_status = "passed"
                elif result_tag == "失败":
                    sf_obj_for_stats.last_run_status = "failed"
                else:
                    # "执行" 时根据 test_run 状态判断，不再保留旧值（避免 null 问题）
                    # 如果 test_run 状态是 completed，则视为 passed
                    if test_run and test_run.status == "completed":
                        sf_obj_for_stats.last_run_status = "passed"
                    elif test_run and test_run.status == "failed":
                        sf_obj_for_stats.last_run_status = "failed"
                    else:
                        # 兜底：如果已有报告且执行成功，设为 passed
                        sf_obj_for_stats.last_run_status = "passed"
                sf_obj_for_stats.updated_at = datetime.now(timezone.utc)
                print(f"[save_web_test_report] 更新子功能状态: {sf_obj_for_stats.identifier} "
                      f"total_test_runs={sf_obj_for_stats.total_test_runs}, "
                      f"last_run_status={sf_obj_for_stats.last_run_status}")

        await session.commit()

        return {
            "success": True,
            "attachment_id": attachment_id,
            "report_path": report_object_name,
            "screenshots_path": screenshot_dir,
            "test_run_id": str(run_uuid),
            "message": "测试报告已保存"
        }


@tool
async def get_artifact_content(
    attachment_id: str
) -> dict:
    """
    获取附件内容

    Args:
        attachment_id: 附件 ID

    Returns:
        dict: 包含文件内容和元数据的字典
    """
    async with async_session_factory() as session:
        # 查询附件
        stmt = select(Attachment).where(
            Attachment.id == UUID(attachment_id)
        )
        result = await session.execute(stmt)
        attachment = result.scalar_one_or_none()

        if not attachment:
            return {"error": f"Attachment {attachment_id} not found"}

        # 从 MinIO 下载文件，如果失败则尝试从本地恢复
        try:
            content_bytes = MinIOClient.download_file(attachment.object_name)
            content = content_bytes.decode('utf-8')
        except Exception as e:
            # MinIO 下载失败，尝试从本地恢复
            print(f"[Restore] MinIO 下载失败，尝试从本地恢复: {attachment.object_name}")
            local_content = _restore_from_local(attachment.object_name)
            if local_content:
                content = local_content.decode('utf-8')
                print(f"[Restore] 从本地恢复成功")
            else:
                return {"error": f"Failed to download file from MinIO and no local backup found: {str(e)}"}

        return {
            "success": True,
            "attachment_id": str(attachment.id),
            "type": attachment.entity_type.value,
            "file_name": attachment.file_name,
            "content": content,
            "content_type": attachment.content_type,
            "created_at": attachment.created_at.isoformat() if attachment.created_at else None,
        }
