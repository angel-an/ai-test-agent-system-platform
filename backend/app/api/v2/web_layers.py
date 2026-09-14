# -*- coding: utf-8 -*-
"""Web 分层执行路由 — D12 后端入口。"""

from __future__ import annotations

from typing import Any, Optional

from fastapi import APIRouter, HTTPException, Body
from pydantic import BaseModel

from app.schemas.common import SuccessResponse
from app.test_platform.web_test_framework.runner import (
    WebLayerRunner,
    run_web_layers,
    get_web_layer_config,
)
from app.test_platform.execution_gates import GateConfig, get_gate_status

router = APIRouter(
    prefix="/projects/{project_identifier}/web-layers",
    tags=["Web 分层执行"],
)


# ---------------------------------------------------------------------------
# 请求模型
# ---------------------------------------------------------------------------

class WebLayerExecuteRequest(BaseModel):
    """Web 分层执行请求"""
    base_url: str
    layers: Optional[list[str]] = None
    live_web: Optional[bool] = None
    run_risky: Optional[bool] = None


class WebSingleLayerExecuteRequest(BaseModel):
    """Web 单层执行请求"""
    base_url: str
    live_web: Optional[bool] = None
    run_risky: Optional[bool] = None


# ---------------------------------------------------------------------------
# 路由
# ---------------------------------------------------------------------------

@router.get(
    "",
    response_model=SuccessResponse[dict[str, Any]],
    summary="获取 Web 分层配置状态",
    description="获取四层回归的配置状态和门控信息",
)
async def get_web_layers(
    project_identifier: str,
) -> SuccessResponse[dict[str, Any]]:
    """获取 Web 四层配置状态"""
    layers = get_web_layer_config()
    gates = get_gate_status()

    return SuccessResponse(
        success=True,
        data={
            "layers": layers,
            "gate_status": gates,
            "safe_mode": gates["safe_mode"],
        },
    )


@router.post(
    "/execute",
    response_model=SuccessResponse[list[dict[str, Any]]],
    summary="执行 Web 分层测试",
    description="按四层回归执行 Web 测试",
)
async def execute_web_layers(
    project_identifier: str,
    body: WebLayerExecuteRequest = Body(...),
) -> SuccessResponse[list[dict[str, Any]]]:
    """执行 Web 分层测试。"""
    # 构建门控配置
    gate_config = GateConfig.from_env()
    if body.live_web is not None:
        gate_config = GateConfig(
            live_api=gate_config.live_api,
            live_web=body.live_web,
            run_risky_negative=body.run_risky if body.run_risky is not None else gate_config.run_risky_negative,
            auth_negative=gate_config.auth_negative,
            fail_closed=gate_config.fail_closed,
        )

    try:
        results = run_web_layers(
            project=project_identifier,
            base_url=body.base_url,
            layers=body.layers,
            gate_config=gate_config,
        )

        return SuccessResponse(
            success=True,
            data=results,
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Web 分层执行失败: {str(e)}")


@router.post(
    "/{layer}/execute",
    response_model=SuccessResponse[dict[str, Any]],
    summary="执行单层 Web 测试",
    description="执行指定回归层级的 Web 测试",
)
async def execute_single_web_layer(
    project_identifier: str,
    layer: str,
    body: WebSingleLayerExecuteRequest = Body(...),
) -> SuccessResponse[dict[str, Any]]:
    """执行单层 Web 测试"""
    from app.test_platform.web_test_framework import WebRegressionLayer

    try:
        layer_enum = WebRegressionLayer(layer)
    except ValueError:
        valid = [l.value for l in WebRegressionLayer]
        raise HTTPException(
            status_code=400,
            detail=f"无效的层级名称: {layer}，有效值: {valid}",
        )

    # 构建门控配置
    gate_config = GateConfig.from_env()
    if body.live_web is not None:
        gate_config = GateConfig(
            live_api=gate_config.live_api,
            live_web=body.live_web,
            run_risky_negative=body.run_risky if body.run_risky is not None else gate_config.run_risky_negative,
            auth_negative=gate_config.auth_negative,
            fail_closed=gate_config.fail_closed,
        )

    runner = WebLayerRunner(project_identifier, body.base_url, gate_config)
    result = runner.run_layer(layer_enum)

    return SuccessResponse(
        success=True,
        data=result.to_dict(),
    )
