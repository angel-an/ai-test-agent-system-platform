"""Web 用例最小闭环路由。"""

from typing import Any
from uuid import UUID

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app.api.deps import DbSessionDep
from app.schemas.common import SuccessResponse
from app.services.web_case_link_service import WebCaseLinkService


router = APIRouter(
    prefix="/projects/{project_identifier}/web-case-links",
    tags=["Web 用例闭环"],
)


class MinimalWebLoopRequest(BaseModel):
    """基于功能 TestCase 创建最小 Web 闭环资产。"""

    test_case_ids: list[UUID] = Field(..., min_length=1, description="功能测试用例 ID 列表")
    folder_id: UUID | None = Field(default=None, description="测试用例文件夹 ID，可选")
    base_url: str | None = Field(default=None, description="B端登录页 URL")
    test_run_identifier: str | None = Field(default=None, description="追加到已有 TestRun")
    create_test_run: bool = Field(default=False, description="是否同步创建 TestRun")
    execution_config: dict[str, Any] | None = Field(default=None, description="执行配置")


@router.post(
    "/minimal-loop",
    response_model=SuccessResponse[dict[str, Any]],
    summary="从功能用例生成可执行 Web 冒烟测试",
)
async def create_minimal_web_loop(
    project_identifier: str,
    request: MinimalWebLoopRequest,
    db: DbSessionDep,
) -> SuccessResponse[dict[str, Any]]:
    """最小闭环：TestCase -> WebTest -> 可选 TestRun job。"""
    service = WebCaseLinkService(db)
    try:
        result = await service.create_minimal_loop(
            project_identifier=project_identifier,
            test_case_ids=request.test_case_ids,
            folder_id=request.folder_id,
            base_url=request.base_url,
            test_run_identifier=request.test_run_identifier,
            create_test_run=request.create_test_run,
            execution_config=request.execution_config,
        )
        return SuccessResponse(success=True, data=result)
    except ValueError as exc:
        await db.rollback()
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        await db.rollback()
        raise HTTPException(status_code=500, detail=f"创建 Web 闭环失败: {exc}") from exc
