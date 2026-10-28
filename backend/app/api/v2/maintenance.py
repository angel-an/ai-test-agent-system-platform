"""平台维护接口：对象存储保留期策略与清理。

为什么要有这个入口：证据（trace/截图）、Allure 报告、脚本重建归档三处产物
只增不减，桶会随执行次数线性膨胀；而清理是**破坏性**动作，必须能先看计划
（默认 dry-run）再执行，并且有按组保底与单次上限兜底。
"""

from __future__ import annotations

import asyncio
from typing import Optional

from fastapi import APIRouter
from pydantic import BaseModel, Field

from app.schemas.common import SuccessResponse
from app.services.retention_service import (
    DEFAULT_MAX_DELETE,
    TARGET_CONFIG,
    current_policy,
    execute_cleanup,
)

router = APIRouter(prefix="/maintenance", tags=["平台维护"])


class CleanupRequest(BaseModel):
    """清理请求。``dry_run`` 默认 true —— 没显式传 false 就不会删任何东西。"""

    targets: Optional[list[str]] = Field(
        default=None,
        description=f"清理目标，可选 {sorted(TARGET_CONFIG)}；不传表示全部",
    )
    retain_days: Optional[dict[str, int]] = Field(
        default=None,
        description="按目标覆盖保留期（天），如 {\"evidence\": 14}",
    )
    keep_recent: Optional[dict[str, int]] = Field(
        default=None,
        description="按目标覆盖「每组至少保留最新 N 个」，防止整组证据被清空",
    )
    max_delete: int = Field(
        default=DEFAULT_MAX_DELETE,
        ge=1,
        le=5000,
        description="单次最多删除的对象数，超出则只删最旧的到上限并标 truncated",
    )
    dry_run: bool = Field(default=True, description="true 只出计划；false 才真正删除")


@router.get(
    "/storage/retention",
    response_model=SuccessResponse,
    summary="查看对象存储保留策略",
)
async def get_retention_policy():
    """回显默认保留期、按组保底数量与单次删除上限。"""
    return SuccessResponse(data=current_policy())


@router.post(
    "/storage/cleanup",
    response_model=SuccessResponse,
    summary="按保留期清理对象存储（默认 dry-run）",
    description=(
        "清理失败证据、Allure 报告、脚本重建归档。默认 dry_run=true 只返回计划；"
        "确认无误后再传 dry_run=false 执行。按组保底（keep_recent）与单次上限"
        "（max_delete）双重兜底，不会一次性清空某次运行的全部证据。"
    ),
)
async def cleanup_storage(body: CleanupRequest):
    # 列举/删除都是阻塞 IO，放线程里跑，别卡事件循环（清理可能涉及上千个对象）
    result = await asyncio.to_thread(
        execute_cleanup,
        targets=body.targets,
        retain_days=body.retain_days,
        keep_recent=body.keep_recent,
        max_delete=body.max_delete,
        dry_run=body.dry_run,
    )
    return SuccessResponse(data=result)
