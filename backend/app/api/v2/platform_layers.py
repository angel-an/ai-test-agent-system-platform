# -*- coding: utf-8 -*-
"""API 分层执行路由 — D11 后端入口（修正版）。

提供：
- GET /projects/{id}/api-layers — 获取六层配置状态
- POST /projects/{id}/api-layers/execute — 分层执行入口
- POST /projects/{id}/api-layers/{layer}/execute — 单层执行
"""

from __future__ import annotations

from typing import Any, Optional

from fastapi import APIRouter, HTTPException, Body
from pydantic import BaseModel

from app.schemas.common import SuccessResponse
from app.test_platform.api_test_framework.runner import (
    APILayerRunner,
    run_api_layers,
    get_api_layer_config,
)
from app.test_platform.execution_gates import GateConfig, get_gate_status

router = APIRouter(
    prefix="/projects/{project_identifier}/api-layers",
    tags=["API 分层执行"],
)


# ---------------------------------------------------------------------------
# 请求模型
# ---------------------------------------------------------------------------

class LayerExecuteRequest(BaseModel):
    """分层执行请求"""
    layers: Optional[list[str]] = None
    live_api: Optional[bool] = None
    run_risky: Optional[bool] = None


class SingleLayerExecuteRequest(BaseModel):
    """单层执行请求"""
    live_api: Optional[bool] = None
    run_risky: Optional[bool] = None


# ---------------------------------------------------------------------------
# 路由
# ---------------------------------------------------------------------------

@router.get(
    "",
    response_model=SuccessResponse[dict[str, Any]],
    summary="获取 API 分层配置状态",
    description="获取六层分层的配置状态和门控信息",
)
async def get_api_layers(
    project_identifier: str,
) -> SuccessResponse[dict[str, Any]]:
    """获取 API 六层配置状态"""
    layers = get_api_layer_config(project_identifier)
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
    summary="执行 API 分层测试",
    description="按六层分层执行 API 测试，支持指定分层和自定义门控",
)
async def execute_api_layers(
    project_identifier: str,
    body: LayerExecuteRequest = Body(...),
) -> SuccessResponse[list[dict[str, Any]]]:
    """执行 API 分层测试。

    Args:
        body: 执行参数，包含 layers（指定分层列表）、live_api、run_risky
    """
    # 构建门控配置
    gate_config = GateConfig.from_env()
    if body.live_api is not None:
        gate_config = GateConfig(
            live_api=body.live_api,
            live_web=gate_config.live_web,
            run_risky_negative=body.run_risky if body.run_risky is not None else gate_config.run_risky_negative,
            auth_negative=gate_config.auth_negative,
            fail_closed=gate_config.fail_closed,
        )

    try:
        results = run_api_layers(
            project=project_identifier,
            layers=body.layers,
            gate_config=gate_config,
        )

        return SuccessResponse(
            success=True,
            data=results,
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"分层执行失败: {str(e)}")


@router.post(
    "/{layer}/execute",
    response_model=SuccessResponse[dict[str, Any]],
    summary="执行单层 API 测试",
    description="执行指定分层的 API 测试",
)
async def execute_single_layer(
    project_identifier: str,
    layer: str,
    body: SingleLayerExecuteRequest = Body(...),
) -> SuccessResponse[dict[str, Any]]:
    """执行单层 API 测试"""
    from app.test_platform.api_test_framework.api_layer import ApiLayer

    try:
        layer_enum = ApiLayer(layer)
    except ValueError:
        valid = [l.value for l in ApiLayer]
        raise HTTPException(
            status_code=400,
            detail=f"无效的分层名称: {layer}，有效值: {valid}",
        )

    # 构建门控配置
    gate_config = GateConfig.from_env()
    if body.live_api is not None:
        gate_config = GateConfig(
            live_api=body.live_api,
            live_web=gate_config.live_web,
            run_risky_negative=body.run_risky if body.run_risky is not None else gate_config.run_risky_negative,
            auth_negative=gate_config.auth_negative,
            fail_closed=gate_config.fail_closed,
        )

    runner = APILayerRunner(project_identifier, gate_config)
    result = runner.run_layer(layer_enum)

    return SuccessResponse(
        success=True,
        data=result.to_dict(),
    )
