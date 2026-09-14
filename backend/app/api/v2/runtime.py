# -*- coding: utf-8 -*-
"""统一运行时 API 路由 — 产物发布 + 执行前预检 + 统一执行入口（真闭环）。

提供：
- POST /projects/{id}/runtime/publish — 发布产物
- POST /projects/{id}/runtime/preflight — 执行前预检
- POST /projects/{id}/runtime/execute — 统一执行入口（真正调用 runner）
- GET /projects/{id}/runtime/status — 获取运行状态
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Body
from pydantic import BaseModel

from app.schemas.common import SuccessResponse
from app.test_platform.runtime_context import RunContext, create_run_context
from app.test_platform.directory_layout import DirectoryLayout
from app.test_platform.execution_gates import GateConfig
from app.services.artifact_publisher import ArtifactPublisher, ArtifactPublishError
from app.services.execution_preflight import ExecutionPreflight, PreflightBlockedError

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/projects/{project_identifier}/runtime",
    tags=["统一运行时"],
)


# ---------------------------------------------------------------------------
# 请求模型
# ---------------------------------------------------------------------------

class PublishRequest(BaseModel):
    """发布产物请求"""
    run_id: str
    artifact_id: str | None = None
    test_case_id: str | None = None
    scenario_id: str | None = None
    source_path: str  # 源文件路径（服务器本地路径）
    artifact_type: str = "web_script"  # api_script / web_script / scenario_manifest
    expected_sha256: str | None = None
    artifact_version: str | None = None
    mode: str = "safe"


class PreflightRequest(BaseModel):
    """执行前预检请求"""
    run_id: str
    artifact_id: str | None = None
    test_case_id: str | None = None
    scenario_id: str | None = None
    artifact_version: str | None = None
    artifact_path: str | None = None
    track: str = "web"  # api / web / scenario
    mode: str = "safe"


class ExecuteRequest(BaseModel):
    """统一执行请求"""
    run_id: str
    artifact_id: str | None = None
    test_case_id: str | None = None
    scenario_id: str | None = None
    artifact_version: str | None = None
    track: str = "web"
    mode: str = "safe"
    live: bool = False
    base_url: str | None = None  # Web 测试基础 URL


# ---------------------------------------------------------------------------
# 统一执行调度器
# ---------------------------------------------------------------------------

class ExecutionDispatcher:
    """统一执行调度器 — 按 track 分发到对应 runner。"""

    @staticmethod
    async def dispatch(
        context: RunContext,
        track: str,
        base_url: str | None = None,
    ) -> dict[str, Any]:
        """根据 track 分发执行。

        Args:
            context: 运行上下文（已预检通过）
            track: 执行轨道 — api / web / scenario
            base_url: Web 测试基础 URL

        Returns:
            执行结果字典
        """
        if track == "web":
            return await ExecutionDispatcher._run_web(context, base_url)
        elif track == "api":
            return await ExecutionDispatcher._run_api(context)
        elif track == "scenario":
            return await ExecutionDispatcher._run_scenario(context)
        else:
            raise ValueError(f"不支持的 track: {track}")

    @staticmethod
    async def _run_web(context: RunContext, base_url: str | None) -> dict[str, Any]:
        """执行 Web 测试。"""
        from app.test_platform.web_test_framework.runner import WebLayerRunner
        from app.test_platform.web_test_framework import WebRegressionLayer

        if not context.artifact_id:
            raise ValueError("Web 执行需要 artifact_id")

        if not base_url:
            raise ValueError("Web 执行需要 base_url")

        runner = WebLayerRunner(
            project=context.project_id,
            base_url=base_url,
            gate_config=GateConfig(live_web=context.is_live_mode()),
            workspace_root=context.workspace_root,
        )

        # 执行 sanity 层
        result = runner.run_layer(WebRegressionLayer.SANITY)

        return {
            "track": "web",
            "status": result.status,
            "layer": result.layer.value,
            "duration_ms": result.duration_ms,
            "stdout": result.stdout,
            "stderr": result.stderr,
            "error_message": result.error_message,
            "screenshot_count": result.screenshot_count,
            "run_id": context.run_id,
        }

    @staticmethod
    async def _run_api(context: RunContext) -> dict[str, Any]:
        """执行 API 测试。"""
        from app.test_platform.api_test_framework.runner import APILayerRunner
        from app.test_platform.api_test_framework.api_layer import ApiLayer

        runner = APILayerRunner(
            project=context.project_id,
            gate_config=GateConfig(live_api=context.is_live_mode()),
            workspace_root=context.workspace_root,
        )

        # 执行 query 层（safe 模式默认只读）
        result = runner.run_layer(ApiLayer.QUERY)

        return {
            "track": "api",
            "status": result.status,
            "layer": result.layer.value,
            "duration_ms": result.duration_ms,
            "stdout": result.stdout,
            "stderr": result.stderr,
            "error_message": result.error_message,
            "test_count": result.test_count,
            "passed_count": result.passed_count,
            "failed_count": result.failed_count,
            "run_id": context.run_id,
        }

    @staticmethod
    async def _run_scenario(context: RunContext) -> dict[str, Any]:
        """执行场景测试。"""
        # 场景执行需要异步数据库会话，这里返回调度信息
        # 实际执行由调用方通过 scenario_execution_engine 完成
        return {
            "track": "scenario",
            "status": "dispatched",
            "message": "场景执行已调度，请调用 ScenarioExecutionEngine.execute()",
            "run_id": context.run_id,
            "scenario_id": context.scenario_id,
        }


# ---------------------------------------------------------------------------
# 路由
# ---------------------------------------------------------------------------

@router.post(
    "/publish",
    response_model=SuccessResponse[dict[str, Any]],
    summary="发布产物",
    description="将脚本/产物发布到指定运行目录，生成 manifest.json 和 provenance.json",
)
async def publish_artifact_route(
    project_identifier: str,
    body: PublishRequest = Body(...),
) -> SuccessResponse[dict[str, Any]]:
    """发布产物到运行目录。"""
    context = create_run_context(
        project_id=project_identifier,
        run_id=body.run_id,
        artifact_id=body.artifact_id,
        test_case_id=body.test_case_id,
        scenario_id=body.scenario_id,
        artifact_version=body.artifact_version,
        mode=body.mode,
    )

    publisher = ArtifactPublisher()
    try:
        published = publisher.publish_artifact(
            context=context,
            source_path=Path(body.source_path),
            artifact_type=body.artifact_type,
            expected_sha256=body.expected_sha256,
            artifact_version=body.artifact_version,
        )
        return SuccessResponse(
            success=True,
            data=published.to_dict(),
        )
    except ArtifactPublishError as e:
        raise HTTPException(
            status_code=400,
            detail=e.to_dict(),
        )


@router.post(
    "/preflight",
    response_model=SuccessResponse[dict[str, Any]],
    summary="执行前预检",
    description="校验运行上下文、产物状态、哈希一致性、执行门控",
)
async def preflight_check_route(
    project_identifier: str,
    body: PreflightRequest = Body(...),
) -> SuccessResponse[dict[str, Any]]:
    """执行前统一预检。"""
    context = create_run_context(
        project_id=project_identifier,
        run_id=body.run_id,
        artifact_id=body.artifact_id,
        test_case_id=body.test_case_id,
        scenario_id=body.scenario_id,
        artifact_version=body.artifact_version,
        mode=body.mode,
    )

    gate_config = GateConfig.from_env()
    if body.mode == "live":
        if body.track == "api":
            gate_config = GateConfig(
                live_api=True,
                live_web=gate_config.live_web,
                run_risky_negative=gate_config.run_risky_negative,
                auth_negative=gate_config.auth_negative,
                fail_closed=gate_config.fail_closed,
            )
        elif body.track == "web":
            gate_config = GateConfig(
                live_api=gate_config.live_api,
                live_web=True,
                run_risky_negative=gate_config.run_risky_negative,
                auth_negative=gate_config.auth_negative,
                fail_closed=gate_config.fail_closed,
            )

    preflight = ExecutionPreflight(gate_config=gate_config)
    result = preflight.check(
        context,
        Path(body.artifact_path) if body.artifact_path else None,
        body.track,
    )

    if result.is_blocked():
        return SuccessResponse(
            success=False,
            data=result.to_dict(),
        )

    return SuccessResponse(
        success=True,
        data=result.to_dict(),
    )


@router.post(
    "/execute",
    response_model=SuccessResponse[dict[str, Any]],
    summary="统一执行入口",
    description="预检通过后真正调用对应 runner 执行测试",
)
async def execute_runtime_route(
    project_identifier: str,
    body: ExecuteRequest = Body(...),
) -> SuccessResponse[dict[str, Any]]:
    """统一执行入口 — 先预检，通过后真正执行。"""
    context = create_run_context(
        project_id=project_identifier,
        run_id=body.run_id,
        artifact_id=body.artifact_id,
        test_case_id=body.test_case_id,
        scenario_id=body.scenario_id,
        artifact_version=body.artifact_version,
        mode="live" if body.live else "safe",
    )

    # 1. 预检（硬阻断，不降级）
    gate_config = GateConfig.from_env()
    if body.live:
        if body.track == "api":
            gate_config = GateConfig(live_api=True)
        elif body.track == "web":
            gate_config = GateConfig(live_web=True)

    preflight = ExecutionPreflight(gate_config=gate_config)
    try:
        preflight_result = preflight.require_passed(context, track=body.track)
    except PreflightBlockedError as e:
        logger.warning(
            "[runtime.execute] 预检阻断: run_id=%s, reason=%s",
            body.run_id,
            e.result.reason_code,
        )
        return SuccessResponse(
            success=False,
            data=e.to_dict(),
        )

    # 2. 真正执行（统一调度）
    try:
        result = await ExecutionDispatcher.dispatch(
            context=context,
            track=body.track,
            base_url=body.base_url,
        )

        logger.info(
            "[runtime.execute] 执行完成: run_id=%s, track=%s, status=%s",
            body.run_id,
            body.track,
            result.get("status"),
        )

        return SuccessResponse(
            success=True,
            data={
                "status": "completed",
                "run_id": body.run_id,
                "project_id": project_identifier,
                "track": body.track,
                "mode": "live" if body.live else "safe",
                "preflight": preflight_result.to_dict() if preflight_result else None,
                "result": result,
            },
        )

    except Exception as e:
        logger.exception(
            "[runtime.execute] 执行异常: run_id=%s, track=%s",
            body.run_id,
            body.track,
        )
        return SuccessResponse(
            success=False,
            data={
                "status": "failed",
                "run_id": body.run_id,
                "track": body.track,
                "error": str(e),
            },
        )


@router.get(
    "/status",
    response_model=SuccessResponse[dict[str, Any]],
    summary="获取运行状态",
    description="获取指定运行的产物发布状态和预检结果",
)
async def get_runtime_status(
    project_identifier: str,
    run_id: str,
    artifact_id: str | None = None,
    test_case_id: str | None = None,
    scenario_id: str | None = None,
) -> SuccessResponse[dict[str, Any]]:
    """获取运行状态。"""
    context = create_run_context(
        project_id=project_identifier,
        run_id=run_id,
        artifact_id=artifact_id,
        test_case_id=test_case_id,
        scenario_id=scenario_id,
    )

    layout = DirectoryLayout()
    publisher = ArtifactPublisher(layout)

    is_published = publisher.is_published(context)
    published_artifact = publisher.get_published_artifact(context)

    # 检查运行目录结构
    run_dir = context.run_dir(layout)
    dir_status = {
        "run_dir_exists": run_dir.exists(),
        "run_dir_path": str(run_dir),
        "manifest_exists": (run_dir / "manifest.json").exists(),
        "provenance_exists": (run_dir / "script_provenance.json").exists(),
    }

    return SuccessResponse(
        success=True,
        data={
            "run_id": run_id,
            "project_id": project_identifier,
            "is_published": is_published,
            "artifact": published_artifact.to_dict() if published_artifact else None,
            "directory_status": dir_status,
        },
    )
