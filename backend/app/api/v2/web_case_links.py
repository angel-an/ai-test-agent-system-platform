"""Web 用例最小闭环路由。"""

from datetime import datetime
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


class WebLoopPrecheckRequest(BaseModel):
    """只读预检请求：cohort 筛选（稳定字段）+ dry-run 唯一键判定。"""

    module: str | None = Field(default=None, description="test_cases.module 实列精确匹配")
    module_in: list[str] | None = Field(default=None, description="module 多值精确匹配")
    module_source: str | None = Field(
        default=None, description="custom_fields.module_source（如 prd_section）"
    )
    original_module: str | None = Field(
        default=None, description="custom_fields.original_module"
    )
    has_requirement_rule: bool | None = Field(
        default=None, description="custom_fields.requirement_rule_id 是否非空；None=不过滤"
    )
    created_from: datetime | None = Field(default=None, description="created_at 起始（含）")
    created_to: datetime | None = Field(default=None, description="created_at 截止（含）")
    test_case_ids: list[UUID] | None = Field(
        default=None, description="显式用例 ID 集合；提供时与其他过滤条件取交集"
    )
    limit: int = Field(default=200, ge=1, le=1000, description="明细返回条数上限")


class MinimalWebLoopRequest(BaseModel):
    """基于功能 TestCase 创建最小 Web 闭环资产。"""

    test_case_ids: list[UUID] = Field(..., min_length=1, description="功能测试用例 ID 列表")
    folder_id: UUID | None = Field(default=None, description="测试用例文件夹 ID，可选")
    base_url: str | None = Field(default=None, description="B端登录页 URL")
    test_run_identifier: str | None = Field(default=None, description="追加到已有 TestRun")
    create_test_run: bool = Field(default=False, description="是否同步创建 TestRun")
    execution_config: dict[str, Any] | None = Field(default=None, description="执行配置")
    sub_function_id: UUID | None = Field(
        default=None,
        description=(
            "Web 子功能 ID（执行三要素绑定目标）。不传时按用例文本自动匹配；"
            "无法匹配且需要创建运行时将拒绝创建（不会产生必然失败的运行）"
        ),
    )
    primary_test_case_id: UUID | None = Field(
        default=None,
        description=(
            "本次生成的主用例 ID（WebTest↔TestCase 一对一绑定的锚点，须属于 test_case_ids）。"
            "不传时按：显式子功能名称反查 → 批次内既有 WebTest 关联 → 创建时间确定性排序 依次判定，"
            "选择依据写入 WebTest.generation_params.case_selection 供审计"
        ),
    )
    allow_upload_checks: bool = Field(
        default=False,
        description=(
            "是否执行「上传文件」类断言（默认关闭）。附文件可能触发向 UAT 的上传请求，"
            "属服务端副作用；开启后执行器仍会拦截任何上传请求，只验证客户端格式/大小规则。"
        ),
    )


@router.post(
    "/minimal-loop/precheck",
    response_model=SuccessResponse[dict[str, Any]],
    summary="只读预检：归集前 cohort 筛选与唯一键判定（零写入）",
)
async def precheck_minimal_web_loop(
    project_identifier: str,
    request: WebLoopPrecheckRequest,
    db: DbSessionDep,
) -> SuccessResponse[dict[str, Any]]:
    """dry-run 归集预检。

    只执行 SELECT，不创建/修改任何 WebTest、不回写 custom_fields；
    cohort 筛选仅使用稳定字段（module 实列 / custom_fields.module_source /
    original_module / requirement_rule_id 有无 / created_at 窗口），
    不使用显示名，避免两套 module 标签家族混计。
    """
    service = WebCaseLinkService(db)
    try:
        result = await service.precheck_minimal_loop(
            project_identifier=project_identifier,
            module=request.module,
            module_in=request.module_in,
            module_source=request.module_source,
            original_module=request.original_module,
            has_requirement_rule=request.has_requirement_rule,
            created_from=request.created_from,
            created_to=request.created_to,
            test_case_ids=request.test_case_ids,
            limit=request.limit,
        )
        return SuccessResponse(success=True, data=result)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=f"预检失败: {exc}") from exc


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
            sub_function_id=request.sub_function_id,
            allow_upload_checks=request.allow_upload_checks,
        )
        return SuccessResponse(success=True, data=result)
    except ValueError as exc:
        await db.rollback()
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        await db.rollback()
        # 只返回 str(exc) 会把栈丢掉：实测 2026-10-20 出现
        # `('EOF in multi-line string', (23, 5))`，那是 SyntaxError 的 args，
        # 但没有任何栈信息，只能靠猜是哪个文件/函数在编译字符串。
        import traceback

        traceback.print_exc()
        raise HTTPException(status_code=500, detail=f"创建 Web 闭环失败: {exc}") from exc
