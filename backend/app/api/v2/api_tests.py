"""
API 测试管理 API

提供 API 测试的 CRUD 操作接口
"""

from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Query, UploadFile, File, Form
from fastapi.responses import Response, StreamingResponse

from app.api.deps import (
    APITestServiceDep,
    PaginationDep,
)
from app.utils.exceptions import BadRequestException
from app.schemas.common import SuccessResponse

router = APIRouter(prefix="/projects/{project_identifier}/api-tests")

# Static intent routes must precede /{api_test_id} routes.
from pydantic import BaseModel, Field, field_validator
from app.api.deps import CurrentUserDep, DbSessionDep
from app.schemas.intent import ApiIntent


class CompileIntentRequest(BaseModel):
    natural_language: str = Field(min_length=1, max_length=10000)
    endpoint_id: UUID | None = None
    base_url: str | None = None

    @field_validator('natural_language')
    @classmethod
    def nonblank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError('请输入自然语言描述')
        return value.strip()


class PreviewIntentRequest(BaseModel):
    intent: ApiIntent


async def _check_intent_project(db, project_identifier, endpoint_id=None):
    from fastapi import HTTPException
    from sqlalchemy import select
    from app.models.project import Project
    from app.models.api_endpoint import APIEndpoint
    project = (await db.execute(select(Project).where(Project.identifier == project_identifier))).scalar_one_or_none()
    if project is None:
        raise HTTPException(404, '项目不存在')
    if endpoint_id:
        endpoint = (await db.execute(select(APIEndpoint).where(
            APIEndpoint.id == endpoint_id, APIEndpoint.project_id == project.id,
        ))).scalar_one_or_none()
        if endpoint is None:
            raise HTTPException(404, '接口不属于当前项目或不存在')


@router.post('/compile-intent', response_model=SuccessResponse)
async def compile_intent(project_identifier: str, request: CompileIntentRequest,
                         user: CurrentUserDep, db: DbSessionDep):
    import json
    from app.agents.api.tools.intent_tools import compile_api_intent
    await _check_intent_project(db, project_identifier, request.endpoint_id)
    result = compile_api_intent.invoke({
        'natural_language': request.natural_language,
        'endpoint_id': str(request.endpoint_id) if request.endpoint_id else None,
        'base_url': request.base_url,
        'project_identifier': project_identifier,
    })
    return SuccessResponse(data=json.loads(result))


@router.post('/preview-config', response_model=SuccessResponse)
async def preview_intent(project_identifier: str, request: PreviewIntentRequest,
                         user: CurrentUserDep, db: DbSessionDep):
    import json
    from app.agents.api.tools.intent_tools import preview_intent_config
    await _check_intent_project(db, project_identifier, request.intent.endpoint_id)
    result = preview_intent_config.invoke({
        'intent_json': request.intent.model_dump_json(exclude_none=True),
        'intent_type': 'api',
    })
    return SuccessResponse(data=json.loads(result))

# ============ API 测试管理接口 ============

class RefreshScriptsRequest(BaseModel):
    """脚本重建请求体。默认 ``dry_run=True``：批量改资产要显式要求执行。"""

    dry_run: bool = Field(default=True, description="只报告不改动")
    api_test_ids: list[str] | None = Field(default=None, description="只处理指定资产")


@router.post(
    "/scripts/refresh",
    response_model=SuccessResponse,
    summary="重建跟随模板版本的脚本资产",
    description=(
        "比对已生成脚本的模板指纹与当前模板：无指纹/版本不同/源码不同即视为过期。"
        "dry_run=true 只报告，dry_run=false 就地覆盖重建（script_path 不变）。"
    ),
)
async def refresh_script_assets(
    project_identifier: str,
    db: DbSessionDep,
    payload: RefreshScriptsRequest | None = None,
):
    """让历史脚本跟上当前模板。

    动机：脚本是生成期一次性产物，模板改进不会回流到已生成的资产上。
    没有这个入口，"模板已修好"和"库里还是旧脚本"会长期并存，
    而且从界面上完全看不出来 —— 跑出来的结论取决于资产生成日期。
    """
    from app.services.api_script_refresh_service import APIScriptRefreshService

    request = payload or RefreshScriptsRequest()
    report = await APIScriptRefreshService(db).refresh(
        project_identifier,
        dry_run=request.dry_run,
        api_test_ids=request.api_test_ids,
    )
    message = (
        f"待重建 {report['stale']} 条（dry_run，未改动）"
        if report["dry_run"]
        else f"已重建 {report['regenerated']} 条，失败 {report['failed']} 条"
    )
    return SuccessResponse(data=report, message=message)


@router.post(
    "",
    response_model=SuccessResponse,
    summary="创建 API 测试",
    description="创建新的 API 测试",
)
async def create_api_test(
    project_identifier: str,
    service: APITestServiceDep,
    name: str = Form(..., description="API 测试名称"),
    schema_path: str = Form(..., description="Schema 文件路径 (MinIO)"),
    script_path: str = Form(..., description="脚本文件路径 (MinIO)"),
    script_format: str = Form(default="playwright", description="脚本格式"),
    script_language: str = Form(default="typescript", description="脚本语言"),
    schema_url: Optional[str] = Form(None, description="Schema URL"),
    description: Optional[str] = Form(None, description="描述"),
    test_config: Optional[str] = Form(None, description="测试配置 (JSON)"),
    total_endpoints: int = Form(default=0, description="端点总数"),
    total_scenarios: int = Form(default=0, description="场景总数"),
    generation_params: Optional[str] = Form(None, description="生成参数 (JSON)"),
):
    """创建 API 测试"""
    import json
    try:
        config = json.loads(test_config) if test_config else None
    except json.JSONDecodeError as e:
        raise BadRequestException(f"测试配置 JSON 格式无效: {str(e)}")

    try:
        gen_params = json.loads(generation_params) if generation_params else None
    except json.JSONDecodeError as e:
        raise BadRequestException(f"生成参数 JSON 格式无效: {str(e)}")

    result = await service.create_api_test(
        project_identifier=project_identifier,
        name=name,
        schema_path=schema_path,
        script_path=script_path,
        script_format=script_format,
        script_language=script_language,
        schema_url=schema_url,
        description=description,
        test_config=config,
        total_endpoints=total_endpoints,
        total_scenarios=total_scenarios,
        generation_params=gen_params,
    )

    return SuccessResponse(data=result, message="API 测试创建成功")
# pylint: disable  MC80OmFIVnBZMlhscm9ua3VMazZVbUpEUkE9PTo0NDY3MDY1YQ==

@router.get(
    "",
    response_model=SuccessResponse,
    summary="获取 API 测试列表",
    description="获取项目下的所有 API 测试列表，支持搜索和过滤",
)
async def list_api_tests(
    project_identifier: str,
    service: APITestServiceDep,
    pagination: PaginationDep,
    search: Optional[str] = Query(None, description="搜索关键词"),
    script_format: Optional[str] = Query(None, description="脚本格式过滤"),
):
    """获取 API 测试列表"""
    result = await service.list_api_tests(
        project_identifier=project_identifier,
        page=pagination.p,
        page_size=pagination.page_size,
        search=search,
        script_format=script_format,
    )

    return SuccessResponse(data=result)

@router.get(
    "/{api_test_id}",
    response_model=SuccessResponse,
    summary="获取 API 测试详情",
    description="获取指定 API 测试的详细信息",
)
async def get_api_test(
    project_identifier: str,
    api_test_id: str,
    service: APITestServiceDep,
):
    """获取 API 测试详情"""
    result = await service.get_api_test(
        project_identifier=project_identifier,
        api_test_id=api_test_id,
    )

    return SuccessResponse(data=result)

@router.patch(
    "/{api_test_id}",
    response_model=SuccessResponse,
    summary="更新 API 测试",
    description="更新 API 测试配置",
)
async def update_api_test(
    project_identifier: str,
    api_test_id: str,
    service: APITestServiceDep,
    name: Optional[str] = None,
    description: Optional[str] = None,
    test_config: Optional[dict] = None,
):
    """更新 API 测试"""
    result = await service.update_api_test(
        project_identifier=project_identifier,
        api_test_id=api_test_id,
        name=name,
        description=description,
        test_config=test_config,
    )

    return SuccessResponse(data=result, message="API 测试更新成功")

@router.delete(
    "/{api_test_id}",
    response_model=SuccessResponse,
    summary="删除 API 测试",
    description="删除指定的 API 测试",
)
async def delete_api_test(
    project_identifier: str,
    api_test_id: str,
    service: APITestServiceDep,
):
    """删除 API 测试"""
    await service.delete_api_test(
        project_identifier=project_identifier,
        api_test_id=api_test_id,
    )

    return SuccessResponse(message="API 测试删除成功")

# ============ Schema 上传接口 ============

@router.post(
    "/upload-schema",
    response_model=SuccessResponse,
    summary="上传 Schema 文件",
    description="上传 OpenAPI/Swagger/GraphQL Schema 文件到 MinIO",
)
async def upload_schema(
    project_identifier: str,
    service: APITestServiceDep,
    file: UploadFile = File(..., description="Schema 文件"),
):
    """上传 Schema 文件"""
    content = await file.read()
    content_type = file.content_type or "application/json"

    result = await service.upload_schema(
        project_identifier=project_identifier,
        filename=file.filename,
        content=content,
        content_type=content_type,
    )
# pragma: no cover  MS80OmFIVnBZMlhscm9ua3VMazZVbUpEUkE9PTo0NDY3MDY1YQ==

    return SuccessResponse(data=result, message="Schema 文件上传成功")

@router.post(
    "/generate-from-schema",
    response_model=SuccessResponse,
    summary="从 Schema 生成 API 测试",
    description="使用 AI 智能体从 OpenAPI Schema 生成测试计划和脚本",
)
async def generate_from_schema(
    project_identifier: str,
    service: APITestServiceDep,
    schema_url: Optional[str] = Form(None, description="Schema URL（远程）"),
    schema_path: Optional[str] = Form(None, description="Schema 文件路径（已上传）"),
    script_format: str = Form(default="playwright", description="脚本格式"),
    script_language: str = Form(default="typescript", description="脚本语言"),
    include_auth: bool = Form(default=True, description="包含认证场景"),
    include_security: bool = Form(default=False, description="包含安全测试"),
    include_error_handling: bool = Form(default=True, description="包含错误处理"),
):
    """
    从 Schema 生成 API 测试（调用 api_agent）

    工作流程:
    1. 调用 api_agent.planner 生成测试计划
    2. 调用 api_agent.generator 生成测试脚本
    3. 返回生成的计划和脚本
    """
    import asyncio
    from app.agents.api.agent import agent

    # 准备参数
    params = {
        "schemaUrl": schema_url,
        "schemaPath": schema_path,
        "outputFormat": script_format,
        "language": script_language,
        "includeAuth": include_auth,
        "includeSecurity": include_security,
        "includeErrorHandling": include_error_handling,
        "outputPath": f"./api-test-plan-{project_identifier}.md",
    }

    # 构造用户消息
    if schema_url:
        user_message = f"为这个 API 生成测试计划: {schema_url}"
    elif schema_path:
        user_message = f"为这个 API Schema 文件生成测试计划: {schema_path}"
    else:
        return SuccessResponse(
            data=None,
            message="请提供 schema_url 或 schema_path"
        )

    try:
        # 调用 agent 生成测试计划
        planner_result = await agent.ainvoke({
            "messages": [user_message]
        })

        # 提取生成的测试计划内容
        # 注意: 实际实现需要解析 agent 的返回结果
        test_plan_content = planner_result.get("messages", [])[-1].content if planner_result else ""

        # TODO: 这里还需要调用 generator 生成实际的测试脚本
        # 暂时返回测试计划和占位符脚本
        result = {
            "test_plan": test_plan_content,
            "test_script": f"// Test script will be generated by api_agent.generator\n// Format: {script_format}\n// Language: {script_language}",
            "statistics": {
                "total_endpoints": 0,  # 从测试计划中提取
                "total_scenarios": 0,   # 从测试计划中提取
            }
        }
# fmt: off  Mi80OmFIVnBZMlhscm9ua3VMazZVbUpEUkE9PTo0NDY3MDY1YQ==

        return SuccessResponse(
            data=result,
            message="AI 生成成功"
        )

    except Exception as e:
        import logging
        logger = logging.getLogger(__name__)
        logger.error(f"AI 生成失败: {str(e)}", exc_info=True)
        raise BadRequestException(f"AI 生成失败: {str(e)}")

# ============ 测试脚本管理接口 ============

@router.get(
    "/{api_test_id}/script",
    response_class=Response,
    summary="获取测试脚本",
    description="下载 API 测试的脚本文件",
)
async def get_test_script(
    project_identifier: str,
    api_test_id: str,
    service: APITestServiceDep,
):
    """获取测试脚本内容"""
    script_content = await service.get_test_script(
        project_identifier=project_identifier,
        api_test_id=api_test_id,
    )

    return Response(
        content=script_content,
        media_type="text/plain",
        headers={
            "Content-Disposition": f"attachment; filename=api-test-{api_test_id}.ts"
        }
    )

@router.put(
    "/{api_test_id}/script",
    response_model=SuccessResponse,
    summary="更新测试脚本",
    description="更新 API 测试的脚本内容",
)
async def update_test_script(
    project_identifier: str,
    api_test_id: str,
    service: APITestServiceDep,
    script_content: str = Form(..., description="脚本内容"),
):
    """更新测试脚本内容"""
    result = await service.update_test_script(
        project_identifier=project_identifier,
        api_test_id=api_test_id,
        script_content=script_content,
    )

    return SuccessResponse(data=result)
# pylint: disable  My80OmFIVnBZMlhscm9ua3VMazZVbUpEUkE9PTo0NDY3MDY1YQ==

# ============ 测试执行接口 ============

@router.post(
    "/{api_test_id}/run",
    response_model=SuccessResponse,
    summary="执行 API 测试",
    description="异步执行 API 测试",
)
async def run_api_test(
    project_identifier: str,
    api_test_id: str,
    service: APITestServiceDep,
    execution_config: Optional[dict] = None,
):
    """执行 API 测试"""
    result = await service.run_api_test(
        project_identifier=project_identifier,
        api_test_id=api_test_id,
        execution_config=execution_config,
    )

    return SuccessResponse(data=result, message="API 测试已加入执行队列")

@router.get(
    "/{api_test_id}/runs",
    response_model=SuccessResponse,
    summary="获取测试运行历史",
    description="获取 API 测试的所有运行记录",
)
async def list_test_runs(
    project_identifier: str,
    api_test_id: str,
    service: APITestServiceDep,
    pagination: PaginationDep,
):
    """获取测试运行历史"""
    result = await service.get_test_runs(
        project_identifier=project_identifier,
        api_test_id=api_test_id,
        page=pagination.p,
        page_size=pagination.page_size,
    )

    return SuccessResponse(data=result)

@router.get(
    "/{api_test_id}/runs/{run_id}",
    response_model=SuccessResponse,
    summary="获取运行详情",
    description="获取指定测试运行的详细信息",
)
async def get_test_run(
    project_identifier: str,
    api_test_id: str,
    run_id: str,
    service: APITestServiceDep,
):
    """获取测试运行详情"""
    result = await service.get_test_run(
        project_identifier=project_identifier,
        api_test_id=api_test_id,
        run_id=run_id,
    )

    return SuccessResponse(data=result)

@router.get(
    "/{api_test_id}/runs/{run_id}/artifacts",
    response_model=SuccessResponse,
    summary="重新签发报告与证据链接",
    description=(
        "预签名 URL 有 7 天硬上限，缺陷描述里写入的链接过期后会 403。"
        "该接口按对象名现场重新签发报告与证据（trace/截图）的下载地址。"
    ),
)
async def get_run_artifacts(
    project_identifier: str,
    api_test_id: str,
    run_id: str,
    service: APITestServiceDep,
):
    result = await service.get_run_artifacts(
        project_identifier=project_identifier,
        api_test_id=api_test_id,
        run_id=run_id,
    )
    return SuccessResponse(data=result)


@router.get(
    "/{api_test_id}/runs/{run_id}/results",
    response_model=SuccessResponse,
    summary="获取测试结果",
    description="获取测试运行的所有结果",
)
async def get_test_results(
    project_identifier: str,
    api_test_id: str,
    run_id: str,
    service: APITestServiceDep,
    pagination: PaginationDep,
):
    """获取测试结果列表"""
    result = await service.get_test_results(
        project_identifier=project_identifier,
        api_test_id=api_test_id,
        run_id=run_id,
        page=pagination.p,
        page_size=pagination.page_size,
    )

    return SuccessResponse(data=result)
