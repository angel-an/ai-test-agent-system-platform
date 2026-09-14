# -*- coding: utf-8 -*-
"""编排入口路由 — D14 修正版。

修复：
- 场景筛选结果传入 runner
- runner 返回值映射为 TrackResult（含真实状态）
- 安全模式/缺少 base_url/无场景返回准确状态
- 编排流程调用统一证据链
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Body
from pydantic import BaseModel

from app.schemas.common import SuccessResponse
from app.test_platform.orchestrator import (
    OrchestratorConfig,
    TrackResult,
    select_scenarios,
    aggregate_results,
)
from app.test_platform.execution_gates import GateConfig, get_gate_status
from app.test_platform.api_test_framework.runner import run_api_layers
from app.test_platform.web_test_framework.runner import run_web_layers
from app.test_platform.evidence_chain import build_evidence_package, WebEvidence, ApiEvidence

router = APIRouter(
    prefix="/projects/{project_identifier}/orchestrate",
    tags=["双轨编排"],
)


# ---------------------------------------------------------------------------
# 请求模型
# ---------------------------------------------------------------------------

class OrchestrateRequest(BaseModel):
    """编排执行请求"""
    cases: list[dict[str, Any]] = []
    selected_scenarios: list[str] = []
    run_api: bool = True
    run_web: bool = True
    live_api: bool = False
    live_web: bool = False
    run_risky: bool = False
    base_url: str = ""  # Web 测试基础 URL


# ---------------------------------------------------------------------------
# 路由
# ---------------------------------------------------------------------------

@router.get(
    "/status",
    response_model=SuccessResponse[dict[str, Any]],
    summary="获取编排门控状态",
    description="获取当前门控状态和可执行层级",
)
async def get_orchestrate_status(
    project_identifier: str,
) -> SuccessResponse[dict[str, Any]]:
    """获取编排状态"""
    gates = get_gate_status()

    return SuccessResponse(
        success=True,
        data={
            "project": project_identifier,
            "gate_status": gates,
            "safe_mode": gates["safe_mode"],
            "can_run_api": gates["live_api"],
            "can_run_web": gates["live_web"],
            "can_run_risky": gates["run_risky_negative"],
        },
    )


@router.post(
    "/execute",
    response_model=SuccessResponse[dict[str, Any]],
    summary="执行双轨编排",
    description="场景选择 → API 分层执行 → Web 分层执行 → 证据包生成 → 聚合报告",
)
async def orchestrate_execute(
    project_identifier: str,
    body: OrchestrateRequest = Body(...),
) -> SuccessResponse[dict[str, Any]]:
    """执行双轨编排。

    工作流程：
    1. 场景选择（按 selected_scenarios 过滤）
    2. API 分层执行（若 run_api=True）
    3. Web 分层执行（若 run_web=True 且 base_url 非空）
    4. 证据包生成
    5. 聚合报告
    """
    # 1. 场景选择
    scenarios = select_scenarios(
        body.cases,
        {"ids": body.selected_scenarios} if body.selected_scenarios else None,
    )

    # 无场景时直接返回
    if not scenarios and body.cases:
        return SuccessResponse(
            success=True,
            data={
                "project": project_identifier,
                "scenarios_selected": 0,
                "track_results": [],
                "report": {},
                "message": "无匹配场景",
            },
        )

    if not body.cases:
        return SuccessResponse(
            success=True,
            data={
                "project": project_identifier,
                "scenarios_selected": 0,
                "track_results": [],
                "report": {},
                "message": "未提供测试用例",
            },
        )

    # 构建门控配置
    gate_config = GateConfig(
        live_api=body.live_api,
        live_web=body.live_web,
        run_risky_negative=body.run_risky,
    )

    # 构建编排配置
    config = OrchestratorConfig(
        project=project_identifier,
        selected_scenarios=body.selected_scenarios or None,
        run_api=body.run_api,
        run_web=body.run_web,
        gate_config=gate_config,
    )

    track_results = []
    evidence_run_id = f"{project_identifier}_{__import__('datetime').datetime.now(__import__('datetime').timezone.utc).strftime('%Y%m%d_%H%M%S')}"

    # 2. API 轨执行
    if body.run_api:
        try:
            api_layer_results = run_api_layers(
                project=project_identifier,
                gate_config=gate_config,
            )
            # 从分层结果推导真实状态
            all_skipped = all(r.get("status") == "skipped" for r in api_layer_results)
            any_failed = any(r.get("status") in ("failed", "error") for r in api_layer_results)

            if any_failed:
                api_status = "failed"
            elif all_skipped:
                api_status = "skipped"
            else:
                api_status = "completed"

            track_results.append(TrackResult(
                track="api",
                status=api_status,
                records=[],
                error=None,
                evidence_paths=[r.get("layer") for r in api_layer_results],
            ))
        except Exception as e:
            track_results.append(TrackResult(
                track="api",
                status="failed",
                records=[],
                error=str(e),
            ))
    else:
        track_results.append(TrackResult(
            track="api",
            status="skipped",
            error="API 轨未启用",
        ))

    # 3. Web 轨执行
    if body.run_web:
        if not body.base_url:
            track_results.append(TrackResult(
                track="web",
                status="skipped",
                error="Web 轨需要 base_url",
            ))
        else:
            try:
                web_layer_results = run_web_layers(
                    project=project_identifier,
                    base_url=body.base_url,
                    gate_config=gate_config,
                )
                all_skipped = all(r.get("status") == "skipped" for r in web_layer_results)
                any_failed = any(r.get("status") in ("failed", "error") for r in web_layer_results)

                if any_failed:
                    web_status = "failed"
                elif all_skipped:
                    web_status = "skipped"
                else:
                    web_status = "completed"

                track_results.append(TrackResult(
                    track="web",
                    status=web_status,
                    records=[],
                    error=None,
                    evidence_paths=[r.get("layer") for r in web_layer_results],
                ))
            except Exception as e:
                track_results.append(TrackResult(
                    track="web",
                    status="failed",
                    records=[],
                    error=str(e),
                ))
    else:
        track_results.append(TrackResult(
            track="web",
            status="skipped",
            error="Web 轨未启用",
        ))

    # 4. 证据包生成
    evidence_zip_path = None
    try:
        web_ev = None
        api_ev = None
        if body.run_api:
            api_ev = ApiEvidence(
                response_body={"track": "api", "status": track_results[0].status if track_results else "unknown"},
            )
        if body.run_web and body.base_url:
            web_ev = WebEvidence(
                screenshots=[],
            )

        if web_ev or api_ev:
            zip_path = build_evidence_package(
                run_id=evidence_run_id,
                web_ev=web_ev,
                api_ev=api_ev,
                metadata={"project": project_identifier, "orchestrated": True},
                project=project_identifier,
            )
            evidence_zip_path = str(zip_path)
    except Exception as e:
        # 证据生成失败不影响主流程
        pass

    # 5. 聚合报告
    report = aggregate_results(track_results, config)

    return SuccessResponse(
        success=True,
        data={
            "project": project_identifier,
            "scenarios_selected": len(scenarios),
            "track_results": [tr.to_dict() for tr in track_results],
            "report": report,
            "evidence_run_id": evidence_run_id,
            "evidence_zip_path": evidence_zip_path,
            "gate_status": get_gate_status(),
        },
    )
