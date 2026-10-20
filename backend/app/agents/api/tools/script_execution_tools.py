"""
API 测试脚本执行工具

提供在测试目录中执行 API 测试脚本的功能
"""

import os
import sys
import json
import asyncio
import subprocess
import tempfile
import zipfile
import shutil
from pathlib import Path
from typing import Optional, Dict, Any
from datetime import datetime
from uuid import UUID, uuid4

from langchain_core.tools import tool
from sqlalchemy import select

from app.config import settings
from app.config.database import async_session_factory
from app.models.attachment import Attachment, AttachmentEntityType
from app.models.api_endpoint import APIEndpoint
from app.config.minio_client import MinIOClient

# P1: 统一执行入口 — 所有执行路径必须经过预检
from app.services.unified_execution import execute_with_preflight
from app.test_platform.runtime_context import RunContext
# 复用执行器的环境变量构建：从端点 env_config 注入 base_url/凭证/测试数据，
# 避免脚本 process.env.XXX 落空后回退到 api.example.com 等占位符导致 DNS 失败。
from app.services.api_endpoint_script_executor import _build_subprocess_env

# ============================================================================
# 测试目录配置
# ============================================================================

# 测试服务器根目录
WORKSPACE_TESTS_ROOT = Path(settings.api_workspace_root) / "tests"


def _node_command(command: str, project_root: str) -> str:
    """Return a Windows-safe Node command, preferring the workspace binary."""
    suffix = ".cmd" if os.name == "nt" else ""
    local_command = Path(project_root) / "node_modules" / ".bin" / f"{command}{suffix}"
    if local_command.exists():
        return str(local_command)
    return f"{command}{suffix}"

def get_workspace_tests_dir() -> Path:
    """
    获取 workspace 测试目录路径

    Returns:
        workspace 测试目录的绝对路径
    """
    return WORKSPACE_TESTS_ROOT

def get_project_root() -> Path:
    """
    获取项目根目录（用于在 workspace 测试目录中找到 package.json）

    Returns:
        项目根目录的绝对路径
    """
    return Path(settings.api_workspace_root)

@tool
async def execute_api_script(
    local_script_path: str,
    framework: str = "playwright",
    reporter: str = "html",
    project_identifier: str = "PR-1",
    endpoint_id: Optional[str] = None,
    # P1: 新增 run_id 和 mode 参数，用于预检
    run_id: Optional[str] = None,
    artifact_id: Optional[str] = None,
    artifact_version: Optional[str] = None,
    mode: str = "safe",
) -> str:
    """
    执行已下载到测试目录的 API 测试脚本

    此工具会：
    1. 验证脚本文件存在于 workspace 测试目录
    2. 执行测试（Playwright/Jest/Pytest）
    3. 生成测试报告（HTML/JSON）
    4. 将测试报告保存到 MinIO
    5. 在数据库中创建测试报告附件记录
    6. 更新端点的测试运行次数
    7. 清理临时报告文件

    Args:
        local_script_path: 本地脚本文件的完整路径（相对或绝对路径）
        framework: 测试框架 (playwright, jest, pytest)
        reporter: 报告格式 (html, json, list)
        project_identifier: 项目标识符，用于保存测试报告
        endpoint_id: 端点 ID（可选，用于更新测试统计）
        run_id: 运行标识符（可选，不传时自动生成格式为 run_YYYYMMDD_HHMMSS）
        artifact_id: 产物 ID（可选）
        artifact_version: 产物版本（可选）
        mode: 执行模式 (safe/live)

    Returns:
        JSON 格式的执行结果，包含：
        - success: 是否成功
        - script_path: 执行的脚本路径
        - execution_result: 执行结果（stdout, stderr, duration, return_code）
        - report_attachment_id: 测试报告附件 ID（如果生成了报告）
        - error: 错误信息（如果有）

    Example:
        >>> result = await execute_api_script(
        ...     local_script_path="backend/workspace/api/tests/login_test.spec.ts",
        ...     framework="playwright",
        ...     reporter="html",
        ...     project_identifier="PR-3",
        ...     endpoint_id="5ea81a5f-c97b-4a36-a680-13637f1b9eed"
        ... )
    """
    try:
        # P0: API 覆盖度门禁（设计后/执行前）
        # 结论由 review_api_coverage 写入端点 custom_config["coverage_gate"]。
        # 默认 warn（只告警不阻断，避免影响既有流水线）；
        # 置 API_COVERAGE_GATE_STRICT=strict 后为硬阻断。
        from app.agents.api.tools.coverage_review_tools import enforce_coverage_gate

        gate_block = await enforce_coverage_gate(endpoint_id)
        if gate_block:
            return json.dumps(gate_block, ensure_ascii=False, indent=2)

        # P1: 统一预检门禁 — 所有执行必须经过预检
        # 如果没有 run_id，自动生成一个（Agent 场景下允许自动生成，
        # 因为这里的 run_id 仅用于追踪，不涉及 ArtifactPublisher 发布流程）
        if not run_id:
            from datetime import datetime as _dt
            run_id = f"run_{_dt.now().strftime('%Y%m%d_%H%M%S')}"
            print(f"[API Script Execution] 自动生成 run_id: {run_id}, project={project_identifier}, script={local_script_path}")

        # 构建 RunContext 并执行预检
        context = RunContext(
            project_id=project_identifier,
            run_id=run_id,
            artifact_id=artifact_id,
            artifact_version=artifact_version,
            mode=mode if mode in ("safe", "live") else "safe",
        )

        # 调用统一执行入口（预检 + 执行）
        # Agent Tool 场景：跳过 manifest/provenance 检查（脚本通过 download_api_script 下载，
        # 不走 ArtifactPublisher 发布流程，因此没有 manifest.json 和 script_provenance.json）
        result = await execute_with_preflight(
            context=context,
            track="api",
            skip_manifest_check=True,
            executor=lambda: _execute_api_script_impl(
                local_script_path=local_script_path,
                framework=framework,
                reporter=reporter,
                project_identifier=project_identifier,
                endpoint_id=endpoint_id,
                run_id=run_id,
                artifact_id=artifact_id,
                artifact_version=artifact_version,
                mode=mode,
            ),
        )

        # 如果预检阻断，直接返回阻断信息
        if result.get("status") == "blocked":
            return json.dumps(result, ensure_ascii=False, indent=2)

        # 正常执行完成，返回结果
        return json.dumps(result, ensure_ascii=False, indent=2)

    except Exception as e:
        import traceback
        traceback.print_exc()
        return json.dumps({
            "success": False,
            "error": f"执行脚本时发生错误: {str(e)}"
        }, ensure_ascii=False, indent=2)


async def _execute_api_script_impl(
    local_script_path: str,
    framework: str = "playwright",
    reporter: str = "html",
    project_identifier: str = "PR-1",
    endpoint_id: Optional[str] = None,
    run_id: Optional[str] = None,
    artifact_id: Optional[str] = None,
    artifact_version: Optional[str] = None,
    mode: str = "safe",
) -> dict[str, Any]:
    """API 脚本执行的实际实现（预检通过后调用）。"""
    try:
        # 1. 解析脚本路径
        # 清理路径：去除开头的斜杠或反斜杠，标准化分隔符
        cleaned_path = local_script_path.strip().strip('/').strip('\\')
        script_path = Path(cleaned_path)
        project_root = Path(settings.api_workspace_root).resolve()
        workspace_tests_dir = get_workspace_tests_dir()

        # 2. 标准化路径：如果是绝对路径，直接使用；如果是相对路径，在 workspace_tests_dir 中查找
        if script_path.is_absolute():
            # 绝对路径：直接使用
            pass
        else:
            # 相对路径：尝试多种解析方式
            # 方式1: 路径已经是相对于 project_root 的格式 (如: "tests/test.spec.ts")
            test_path_full = project_root / script_path
            if test_path_full.exists():
                script_path = test_path_full
            # 方式2: 只在 workspace_tests_dir 中查找文件名
            else:
                test_path = workspace_tests_dir / script_path.name
                if test_path.exists():
                    script_path = test_path
                # 方式3: 尝试添加 .spec.ts 扩展名
                elif not script_path.suffix:
                    test_path_with_ext = workspace_tests_dir / f"{script_path.name}.spec.ts"
                    if test_path_with_ext.exists():
                        script_path = test_path_with_ext

        # 3. 验证脚本文件存在
        if not script_path.exists():
            return {
                "success": False,
                "error": f"脚本文件不存在: {script_path}"
            }
# pylint: disable  MC80OmFIVnBZMlhscm9ua3VMazZXamRsT0E9PToyMjk3MDJhZA==

        script_filename = script_path.name

        print(f"[API Script Execution] 准备执行脚本: {script_path}")

        # 4. 计算相对路径（相对于 project_root）
        try:
            relative_path = script_path.resolve().relative_to(project_root)
        except ValueError:
            # 如果无法计算相对路径，使用文件名
            relative_path = script_path.name

        print(f"[API Script Execution] 项目根目录: {project_root}")
        print(f"[API Script Execution] 相对脚本路径: {relative_path}")

        # 4.5 读取端点环境配置（base_url/凭证/测试数据），执行时注入子进程环境变量。
        # 来源：端点 custom_config.env_config（由"环境配置并执行"流程持久化）。
        # 脚本里 process.env.API_BASE_URL / MX_APPID / MX_APPSECRET / TEST_* 依赖这些值，
        # 缺省会回退到 api.example.com 等占位符，导致 getaddrinfo ENOTFOUND 全军覆没。
        env_config: Dict[str, Any] = {}
        if endpoint_id:
            async with async_session_factory() as db:
                ep_res = await db.execute(
                    select(APIEndpoint).where(APIEndpoint.id == UUID(endpoint_id))
                )
                _ep = ep_res.scalar_one_or_none()
            if _ep and _ep.custom_config:
                env_config = (_ep.custom_config.get("env_config") or {})

        # 5. 执行脚本
        execution_result = await _execute_script_internal(
            script_path=str(relative_path),
            script_filename=script_filename,
            framework=framework,
            reporter=reporter,
            project_root=str(project_root),
            env_config=env_config,
        )

        # 6. 保存测试报告到 MinIO（如果生成了 HTML 报告）
        report_attachment_id = None
        if endpoint_id and reporter == "html" and execution_result.get("report_path"):
            # 获取端点信息
            async with async_session_factory() as db:
                endpoint_result = await db.execute(
                    select(APIEndpoint).where(APIEndpoint.id == UUID(endpoint_id))
                )
                endpoint = endpoint_result.scalar_one_or_none()

            if endpoint:
                report_attachment_id = await _save_test_report(
                    endpoint_id=endpoint_id,
                    project_identifier=project_identifier,
                    endpoint=endpoint,
                    report_path=execution_result["report_path"],
                    execution_result=execution_result,
                    project_root=str(project_root)
                )

                # 7. 更新端点的测试运行次数
                try:
                    async with async_session_factory() as db:
                        endpoint_result = await db.execute(
                            select(APIEndpoint).where(APIEndpoint.id == UUID(endpoint_id))
                        )
                        endpoint = endpoint_result.scalar_one_or_none()
# type: ignore  MS80OmFIVnBZMlhscm9ua3VMazZXamRsT0E9PToyMjk3MDJhZA==

                        if endpoint:
                            # 递增测试运行次数
                            endpoint.total_test_runs = (endpoint.total_test_runs or 0) + 1

                            # 更新最后运行状态
                            if execution_result.get("success"):
                                endpoint.last_run_status = "success"
                            else:
                                endpoint.last_run_status = "failed"

                            await db.commit()
                            print(f"[API Script Execution] 已更新端点 {endpoint_id} 的测试运行次数")
                except Exception as e:
                    print(f"[API Script Execution] 更新端点测试运行次数失败: {e}")

        # 8. 返回结果
        result = {
            "success": True,
            "script_path": str(script_path),
            "script_filename": script_filename,
            "execution_result": execution_result
        }

        if report_attachment_id:
            result["report_attachment_id"] = report_attachment_id
            result["message"] = "脚本执行完成，测试报告已保存"

        if endpoint_id:
            result["endpoint_id"] = endpoint_id

        # P1: 返回 dict（外层 execute_with_preflight 会统一处理 JSON 序列化）
        return result

    except Exception as e:
        import traceback
        traceback.print_exc()
        return {
            "success": False,
            "error": f"执行脚本时发生错误: {str(e)}"
        }

async def _execute_script_internal(
    script_path: str,
    script_filename: str,
    framework: str,
    reporter: str,
    project_root: str,
    env_config: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """
    内部执行脚本函数

    Args:
        script_path: 脚本文件相对路径（相对于 project_root）
        script_filename: 脚本文件名
        framework: 测试框架
        reporter: 报告格式
        project_root: 项目根目录
        env_config: 端点环境配置（base_url/appid/appsecret/env 自定义键值），
            注入子进程环境变量供脚本 process.env.XXX 引用；缺省则脚本用占位符。

    Returns:
        执行结果字典
    """
    try:
        start_time = datetime.now()

        # 确定测试命令
        if framework == "playwright":
            playwright = _node_command("playwright", project_root)
            if reporter == "html":
                # HTML 报告需要指定输出目录
                script_target = Path(script_filename).as_posix()
                cmd = [playwright, "test", script_target, "--reporter=html"]
            else:
                script_target = Path(script_filename).as_posix()
                cmd = [playwright, "test", script_target, f"--reporter={reporter}"]
        elif framework == "jest":
            npm = _node_command("npm", project_root)
            if reporter == "html":
                script_target = Path(script_filename).as_posix()
                cmd = [npm, "test", "--", script_target, "--reporter=html"]
            else:
                script_target = Path(script_filename).as_posix()
                cmd = [npm, "test", "--", script_target, f"--reporter={reporter}"]
        elif framework == "pytest":
            script_target = Path(script_filename).as_posix()
            cmd = ["pytest", script_target, f"--reporter={reporter}"]
        else:
            return {
                "success": False,
                "error": f"不支持的测试框架: {framework}"
            }

        print(f"[API Script Execution] 执行命令: {' '.join(cmd)}")
        print(f"[API Script Execution] 工作目录: {project_root}")

        # 准备环境变量：复用执行器的构建逻辑，从端点 env_config 注入
        # base_url/appid/appsecret/自定义键值，供脚本 process.env.XXX 引用。
        # 再设 CI=1 禁用 Playwright HTML reporter 自动打开浏览器。
        env = _build_subprocess_env(
            env_config or {},
            base_env=os.environ.copy(),
            node_bin=str(Path(project_root) / "node_modules" / ".bin"),
        )
        if reporter == "html":
            env['CI'] = '1'

        # 把 base_url 显式映射为 API_BASE_URL（脚本里 process.env.API_BASE_URL 引用），
        # 避免脚本回退到 api.example.com 占位符。base_url 不含密钥，打印便于排查。
        base_url = (env_config or {}).get("base_url")
        if base_url:
            env["API_BASE_URL"] = str(base_url).rstrip("/")
            print(f"[API Script Execution] 注入 API_BASE_URL={env['API_BASE_URL']}")

        # Execute without a shell so test paths remain safe and predictable.
        result = subprocess.run(
            cmd,
            cwd=project_root,
            capture_output=True,
            text=True,
            encoding='utf-8',
            errors='replace',
            timeout=300,  # 5分钟超时
            shell=False,
            env=env
        )

        end_time = datetime.now()
        duration = (end_time - start_time).total_seconds()

        # 解析输出
        stdout = result.stdout
        stderr = result.stderr
        return_code = result.returncode

        print(f"[API Script Execution] 执行完成，返回码: {return_code}")
        print(f"[API Script Execution] 执行时间: {duration:.2f}s")

        # 检查是否生成了 HTML 报告
        report_path = None
        if reporter == "html":
            report_dir = Path(project_root) / "playwright-report"
            index_html = report_dir / "index.html"
            if index_html.exists():
                report_path = str(report_dir)
                print(f"[API Script Execution] HTML 报告已生成: {report_path}")

        return {
            "success": return_code == 0,
            "return_code": return_code,
            "duration": duration,
            "stdout": stdout,
            "stderr": stderr,
            "report_path": report_path,
            "start_time": start_time.isoformat(),
            "end_time": end_time.isoformat()
        }
# noqa  Mi80OmFIVnBZMlhscm9ua3VMazZXamRsT0E9PToyMjk3MDJhZA==

    except subprocess.TimeoutExpired:
        return {
            "success": False,
            "error": "脚本执行超时（超过5分钟）"
        }
    except Exception as e:
        return {
            "success": False,
            "error": f"执行脚本时发生错误: {str(e)}"
        }

async def _save_test_report(
    endpoint_id: str,
    project_identifier: str,
    endpoint: APIEndpoint,
    report_path: str,
    execution_result: Dict[str, Any],
    project_root: str
) -> Optional[str]:
    """
    保存测试报告到 MinIO 并创建附件记录

    Args:
        endpoint_id: 端点 ID
        project_identifier: 项目标识符
        endpoint: 端点对象
        report_path: 报告目录路径
        execution_result: 执行结果
        project_root: 项目根目录

    Returns:
        附件 ID，如果保存失败则返回 None
    """
    try:
        # 1. 将报告目录打包成 ZIP
        # object_name 带短 UUID：attachments.object_name 是全局唯一约束，秒级时间戳在
        # 并发/快速重试时会冲突导致落库失败，加后缀保证唯一。
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        unique_suffix = uuid4().hex[:8]
        zip_filename = f"api_test_report_{timestamp}_{unique_suffix}.zip"
        zip_path = Path(project_root) / zip_filename

        print(f"[API Report] 打包测试报告: {report_path} -> {zip_path}")

        with zipfile.ZipFile(zip_path, 'w', zipfile.ZIP_DEFLATED) as zipf:
            report_dir = Path(report_path)
            for file_path in report_dir.rglob('*'):
                if file_path.is_file():
                    arcname = file_path.relative_to(report_dir)
                    zipf.write(file_path, arcname)

        # 2. 读取 ZIP 文件内容
        with open(zip_path, 'rb') as f:
            zip_bytes = f.read()

        # 3. 上传到 MinIO
        object_name = f"api-tests/{project_identifier}/endpoints/{endpoint_id}/test-report-{timestamp}-{unique_suffix}.zip"
        MinIOClient.upload_bytes(
            object_name=object_name,
            data=zip_bytes,
            content_type="application/zip"
        )

        print(f"[API Report] 报告已上传到 MinIO: {object_name}")

        # 4. 创建附件记录
        # _persist_report_attachment 用 ON CONFLICT 幂等落库，cancel 时尽力完成。
        # 描述只写整体状态+耗时：stdout 里 ✓/passed 双计数会重复，逐条统计不可靠，
        # 精确计数由离线 report-summary 接口解析 result.json 提供。
        description = f"API 测试报告 - {endpoint.display_name}\n"
        description += f"执行时间: {execution_result.get('duration', 0):.2f}秒\n"
        description += "结果: " + ("通过" if execution_result.get("success") else "存在失败")

        attachment_id = await _persist_report_attachment(
            endpoint_id=endpoint_id,
            endpoint=endpoint,
            object_name=object_name,
            zip_bytes=zip_bytes,
            description=description,
            file_name=zip_filename,
        )

        print(f"[API Report] 附件记录已创建: {attachment_id}")

        # 5. 清理临时 ZIP 文件
        try:
            zip_path.unlink()
            print(f"[API Report] 临时 ZIP 文件已清理: {zip_path}")
        except Exception as e:
            print(f"[API Report] 清理临时 ZIP 文件失败: {e}")

        # 6. 清理报告目录
        try:
            shutil.rmtree(report_path)
            print(f"[API Report] 报告目录已清理: {report_path}")
        except Exception as e:
            print(f"[API Report] 清理报告目录失败: {e}")

        return attachment_id

    except Exception as e:
        print(f"[API Report] 保存测试报告失败: {e}")
        import traceback
        traceback.print_exc()
        return None


async def _persist_report_attachment(
    endpoint_id: str,
    endpoint: "APIEndpoint",
    object_name: str,
    zip_bytes: bytes,
    description: str,
    file_name: str,
) -> str:
    """把报告附件记录写入 DB，返回附件 ID。

    幂等：用 PostgreSQL ON CONFLICT (object_name) DO NOTHING，并发/重试冲突不报错，
    随后回查拿已有记录 ID，避免"先查再插"的竞态。

    cancel：脚本执行耗时长，客户端断开/langgraph interrupt 会注入 cancel。INSERT 的
    commit 被 cancel 时尽力完成（ON CONFLICT 保证不重复插入）再向上抛；调用处回查 ID
    时也吞 cancel 完成。注意：这是尽最大努力，重复取消/服务关闭/DB 故障仍可能落库失败。
    """
    try:
        row_id = await _insert_report_attachment(
            endpoint_id, endpoint, object_name, zip_bytes, description, file_name
        )
    except asyncio.CancelledError:
        # INSERT 的 commit 被 cancel：记录可能已落库，回查拿 ID 后向上响应取消
        attachment_id = await _fetch_attachment_id_by_object_name(object_name)
        raise asyncio.CancelledError() from None
    if row_id is not None:
        return row_id
    # INSERT 因冲突未插入：记录已存在，回查拿 ID
    try:
        return await _fetch_attachment_id_by_object_name(object_name)
    except asyncio.CancelledError:
        return await _fetch_attachment_id_by_object_name(object_name)


async def _insert_report_attachment(
    endpoint_id: str,
    endpoint: "APIEndpoint",
    object_name: str,
    zip_bytes: bytes,
    description: str,
    file_name: str,
) -> Optional[str]:
    """INSERT ... ON CONFLICT (object_name) DO NOTHING。返回新记录 ID；冲突未插入返回 None。
    commit 被 cancel 时吞掉并完成（ON CONFLICT 保证不重复插入），随后向上抛 CancelledError。"""
    from sqlalchemy.dialects.postgresql import insert as pg_insert
    stmt = pg_insert(Attachment).values(
        entity_type=AttachmentEntityType.API_TEST_REPORT,
        entity_id=UUID(endpoint_id),
        project_id=endpoint.project_id,
        file_name=file_name,
        file_size=len(zip_bytes),
        content_type="application/zip",
        object_name=object_name,
        description=description,
        created_by="api-agent",
    ).on_conflict_do_nothing(index_elements=["object_name"]).returning(Attachment.id)

    async with async_session_factory() as session:
        try:
            result = await session.execute(stmt)
            await session.commit()
        except asyncio.CancelledError:
            # 尽力完成 commit（幂等，不会重复插入），再向上响应取消
            await session.commit()
            raise
        row = result.first()
        return str(row[0]) if row else None


async def _fetch_attachment_id_by_object_name(object_name: str) -> str:
    async with async_session_factory() as session:
        result = await session.execute(
            select(Attachment.id).where(Attachment.object_name == object_name)
        )
        row = result.first()
        if row is None:
            raise RuntimeError(f"report attachment not found after insert: {object_name}")
        return str(row[0])

@tool
async def get_test_execution_status(
    execution_id: str
) -> str:
    """
    获取测试执行状态（占位符，未来可扩展为异步执行查询）

    Args:
        execution_id: 执行 ID

    Returns:
        JSON 格式的执行状态
    """
    return json.dumps({
        "success": True,
        "execution_id": execution_id,
        "status": "completed",
        "message": "当前版本仅支持同步执行，不支持异步状态查询"
    }, ensure_ascii=False, indent=2)
