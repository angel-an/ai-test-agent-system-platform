# -*- coding: utf-8 -*-
"""统一证据链路由 — D13 修正版。

修复：
- 项目隔离目录 evidence/{project}/{run_id}/
- 使用 EvidenceBuildRequest Body 模型
- 增加单张截图下载接口
"""

from __future__ import annotations

import io
import zipfile
from typing import Any

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from app.schemas.common import SuccessResponse
from app.test_platform.evidence_chain import (
    UnifiedEvidence,
    WebEvidence,
    ApiEvidence,
    build_evidence_package,
)
from app.test_platform.directory_layout import DirectoryLayout

router = APIRouter(
    prefix="/projects/{project_identifier}/evidence",
    tags=["统一证据链"],
)


# ---------------------------------------------------------------------------
# 请求模型
# ---------------------------------------------------------------------------

class EvidenceBuildRequest(BaseModel):
    """构建证据包请求"""
    web_screenshots: list[str] = []
    self_reflect_path: str = ""
    api_response_body: dict[str, Any] = {}
    api_status_code: int = 0
    db_records: list[dict[str, Any]] = []
    metadata: dict[str, Any] = {}


# ---------------------------------------------------------------------------
# 路由
# ---------------------------------------------------------------------------

def _get_evidence_dir(project_identifier: str, run_id: str) -> Any:
    """获取项目隔离的证据目录"""
    layout = DirectoryLayout()
    return layout.evidence_dir(run_id, project=project_identifier)


@router.get(
    "/{run_id}",
    response_model=SuccessResponse[dict[str, Any]],
    summary="获取证据摘要",
    description="获取指定运行 ID 的证据摘要信息",
)
async def get_evidence_summary(
    project_identifier: str,
    run_id: str,
) -> SuccessResponse[dict[str, Any]]:
    """获取证据摘要"""
    evidence_dir = _get_evidence_dir(project_identifier, run_id)

    # 读取 evidence.json
    evidence_json = evidence_dir / "evidence.json"
    if not evidence_json.exists():
        raise HTTPException(status_code=404, detail=f"证据不存在: {run_id}")

    import json
    data = json.loads(evidence_json.read_text(encoding="utf-8"))

    # 检查 ZIP 包
    zip_path = evidence_dir / f"{run_id}_evidence.zip"
    data["has_zip"] = zip_path.exists()
    data["zip_path"] = str(zip_path) if zip_path.exists() else None

    # 截图列表
    screenshots_dir = evidence_dir / "screenshots"
    if screenshots_dir.exists():
        data["screenshots"] = sorted(
            str(p.name) for p in screenshots_dir.glob("*.png")
        )
    else:
        data["screenshots"] = []

    return SuccessResponse(success=True, data=data)


@router.get(
    "/{run_id}/download",
    summary="下载证据 ZIP 包",
    description="下载指定运行 ID 的证据 ZIP 包",
)
async def download_evidence(
    project_identifier: str,
    run_id: str,
):
    """下载证据 ZIP 包"""
    evidence_dir = _get_evidence_dir(project_identifier, run_id)
    zip_path = evidence_dir / f"{run_id}_evidence.zip"

    if not zip_path.exists():
        raise HTTPException(status_code=404, detail=f"证据包不存在: {run_id}")

    data = zip_path.read_bytes()

    return StreamingResponse(
        io.BytesIO(data),
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{run_id}_evidence.zip"'},
    )


@router.get(
    "/{run_id}/screenshots",
    response_model=SuccessResponse[list[str]],
    summary="获取截图列表",
    description="获取指定运行 ID 的所有截图文件名",
)
async def list_screenshots(
    project_identifier: str,
    run_id: str,
) -> SuccessResponse[list[str]]:
    """获取截图列表"""
    evidence_dir = _get_evidence_dir(project_identifier, run_id)
    screenshots_dir = evidence_dir / "screenshots"

    if not screenshots_dir.exists():
        return SuccessResponse(success=True, data=[])

    screenshots = sorted(str(p.name) for p in screenshots_dir.glob("*.png"))
    return SuccessResponse(success=True, data=screenshots)


@router.get(
    "/{run_id}/screenshots/{filename}",
    summary="下载单张截图",
    description="下载指定运行 ID 的单张截图",
)
async def download_screenshot(
    project_identifier: str,
    run_id: str,
    filename: str,
):
    """下载单张截图"""
    # 路径安全校验：禁止路径遍历
    import re
    if not re.match(r"^[\w\-. ]+\.png$", filename):
        raise HTTPException(status_code=400, detail="非法文件名")

    evidence_dir = _get_evidence_dir(project_identifier, run_id)
    screenshot_path = evidence_dir / "screenshots" / filename

    # 确保路径在 evidence_dir 内
    try:
        screenshot_path.relative_to(evidence_dir)
    except ValueError:
        raise HTTPException(status_code=400, detail="非法文件路径")

    if not screenshot_path.exists():
        raise HTTPException(status_code=404, detail=f"截图不存在: {filename}")

    data = screenshot_path.read_bytes()

    return StreamingResponse(
        io.BytesIO(data),
        media_type="image/png",
        headers={"Content-Disposition": f'inline; filename="{filename}"'},
    )


@router.post(
    "/{run_id}/build",
    response_model=SuccessResponse[dict[str, Any]],
    summary="构建证据包",
    description="从 Web/API 证据构建统一证据包",
)
async def build_evidence(
    project_identifier: str,
    run_id: str,
    body: EvidenceBuildRequest,
) -> SuccessResponse[dict[str, Any]]:
    """构建统一证据包"""
    try:
        web_ev = None
        if body.web_screenshots or body.self_reflect_path:
            web_ev = WebEvidence(
                screenshots=body.web_screenshots,
                self_reflect_path=body.self_reflect_path or None,
            )

        api_ev = None
        if body.api_response_body or body.db_records:
            api_ev = ApiEvidence(
                response_body=body.api_response_body or None,
                status_code=body.api_status_code or None,
                db_records=body.db_records,
            )

        zip_path = build_evidence_package(
            run_id=run_id,
            web_ev=web_ev,
            api_ev=api_ev,
            metadata={"project": project_identifier, **body.metadata},
            project=project_identifier,
        )

        return SuccessResponse(
            success=True,
            data={
                "run_id": run_id,
                "zip_path": str(zip_path),
                "has_web": web_ev is not None,
                "has_api": api_ev is not None,
            },
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"构建证据包失败: {str(e)}")
