"""
LLM 配置 API

提供 GitNexus 全栈分析模块 LLM 配置的获取与保存接口。
"""

from typing import Annotated, Any

from fastapi import APIRouter, Depends

from app.schemas.common import SuccessResponse
from app.schemas.llm_settings import LLMSettingsPayload
from app.services.llm_settings_service import (
    LLMSettingsService,
    get_llm_settings_service,
)

router = APIRouter(prefix="/llm-settings", tags=["LLM 配置"])

LLMSettingsServiceDep = Annotated[
    LLMSettingsService, Depends(get_llm_settings_service)
]


@router.get(
    "",
    response_model=SuccessResponse[dict[str, Any]],
    summary="获取 LLM 配置",
    description="读取 GitNexus 全栈分析模块当前的 LLM 提供商配置。",
)
async def get_llm_settings(
    service: LLMSettingsServiceDep,
) -> SuccessResponse[dict[str, Any]]:
    return SuccessResponse(data=service.load())


@router.put(
    "",
    response_model=SuccessResponse[dict[str, Any]],
    summary="保存 LLM 配置",
    description="整体覆盖保存 GitNexus 全栈分析模块的 LLM 提供商配置。",
)
async def update_llm_settings(
    payload: LLMSettingsPayload,
    service: LLMSettingsServiceDep,
) -> SuccessResponse[dict[str, Any]]:
    saved = service.save(payload.model_dump(exclude_none=False))
    return SuccessResponse(data=saved)
