"""API 用例最小闭环路由。"""

from typing import Any
from uuid import UUID

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app.api.deps import DbSessionDep
from app.schemas.common import SuccessResponse
from app.services.api_case_link_service import APICaseLinkService


router = APIRouter(
    prefix="/projects/{project_identifier}/api-case-links",
    tags=["API 用例闭环"],
)


class MinimalAPILoopRequest(BaseModel):
    """基于 APIEndpoint 创建最小闭环资产。"""

    endpoint_ids: list[UUID] = Field(..., min_length=1, description="API 端点 ID 列表")
    folder_id: UUID | None = Field(default=None, description="测试用例文件夹 ID，可选")
    case_kind: str = Field(default="sit", description="用例类型：sit/smoke/uat/api")
    base_url: str | None = Field(default=None, description="执行时使用的 API Base URL")
    create_test_run: bool = Field(default=False, description="是否同步创建 TestRun")
    execution_config: dict[str, Any] | None = Field(default=None, description="执行配置")


@router.post(
    "/minimal-loop",
    response_model=SuccessResponse[dict[str, Any]],
    summary="从 API 端点生成测试用例和可执行 API 测试",
)
async def create_minimal_api_loop(
    project_identifier: str,
    request: MinimalAPILoopRequest,
    db: DbSessionDep,
) -> SuccessResponse[dict[str, Any]]:
    """最小闭环：APIEndpoint -> TestCase -> APITest -> 可选 TestRun。"""
    service = APICaseLinkService(db)
    try:
        result = await service.create_minimal_loop(
            project_identifier=project_identifier,
            endpoint_ids=request.endpoint_ids,
            folder_id=request.folder_id,
            case_kind=request.case_kind,
            base_url=request.base_url,
            create_test_run=request.create_test_run,
            execution_config=request.execution_config,
        )
        return SuccessResponse(success=True, data=result)
    except ValueError as exc:
        await db.rollback()
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        await db.rollback()
        raise HTTPException(status_code=500, detail=f"创建 API 闭环失败: {exc}") from exc
