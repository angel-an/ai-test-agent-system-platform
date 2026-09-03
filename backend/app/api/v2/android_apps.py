"""
Android App 管理 API

提供 Android App 和子功能的 CRUD 操作接口
使用 AndroidAppService 处理业务逻辑
"""

from typing import Optional

from fastapi import APIRouter, Query
from pydantic import BaseModel

from app.api.deps import (
    AndroidAppServiceDep,
    PaginationDep,
)
from app.schemas.common import SuccessResponse

# ============ 请求模型 ============

class CreateAndroidAppRequest(BaseModel):
    """创建 Android App 请求"""
    display_name: str
    name: str
    folder_id: Optional[str] = None
    description: Optional[str] = None
    app_package: str = ""
    main_activity: Optional[str] = None
    version: Optional[str] = None
    device_udid: Optional[str] = None
    business_module: Optional[str] = None
    navigation: Optional[dict] = None
    screens: Optional[list] = None
    tags: Optional[list] = None
    custom_config: Optional[dict] = None
    sort_order: int = 0


class UpdateAndroidAppRequest(BaseModel):
    """更新 Android App 请求"""
    display_name: Optional[str] = None
    name: Optional[str] = None
    description: Optional[str] = None
    app_package: Optional[str] = None
    main_activity: Optional[str] = None
    version: Optional[str] = None
    device_udid: Optional[str] = None
    business_module: Optional[str] = None
    navigation: Optional[dict] = None
    screens: Optional[list] = None
    tags: Optional[list] = None
    custom_config: Optional[dict] = None
    sort_order: Optional[int] = None


class CreateAndroidSubFunctionRequest(BaseModel):
    """创建 Android 子功能请求"""
    function_id: str
    display_name: str
    name: str
    folder_id: Optional[str] = None
    description: Optional[str] = None
    test_type: str = "functional"
    target_screens: Optional[list] = None
    test_scenario: Optional[str] = None
    test_data: Optional[dict] = None
    expected_results: Optional[list] = None
    priority: str = "medium"
    tags: Optional[list] = None
    custom_config: Optional[dict] = None
    sort_order: int = 0


class UpdateAndroidSubFunctionRequest(BaseModel):
    """更新 Android 子功能请求"""
    display_name: Optional[str] = None
    name: Optional[str] = None
    description: Optional[str] = None
    test_type: Optional[str] = None
    target_screens: Optional[list] = None
    test_scenario: Optional[str] = None
    test_data: Optional[dict] = None
    expected_results: Optional[list] = None
    priority: Optional[str] = None
    tags: Optional[list] = None
    custom_config: Optional[dict] = None
    sort_order: Optional[int] = None


# ============ 路由定义 ============

router = APIRouter(prefix="/projects/{project_identifier}/android-apps")


# ============ Android App 管理接口 ============

@router.post(
    "",
    response_model=SuccessResponse,
    summary="创建 Android App",
    description="创建新的 Android App 定义",
)
async def create_android_app(
    project_identifier: str,
    request: CreateAndroidAppRequest,
    service: AndroidAppServiceDep,
):
    """创建 Android App"""
    import logging
    logger = logging.getLogger(__name__)
    logger.info(f"[create_android_app] 收到请求: project={project_identifier}, display_name={request.display_name}, name={request.name}")

    try:
        result = await service.create_app(
            project_identifier=project_identifier,
            display_name=request.display_name,
            name=request.name,
            folder_id=request.folder_id,
            description=request.description,
            app_package=request.app_package,
            main_activity=request.main_activity,
            version=request.version,
            device_udid=request.device_udid,
            business_module=request.business_module,
            navigation=request.navigation,
            screens=request.screens,
            tags=request.tags,
            custom_config=request.custom_config,
            sort_order=request.sort_order,
        )
        logger.info(f"[create_android_app] 创建成功: {result}")
        return SuccessResponse(data=result, message="Android App 创建成功")
    except Exception as e:
        logger.error(f"[create_android_app] 创建失败: {e}", exc_info=True)
        raise


@router.get(
    "",
    response_model=SuccessResponse,
    summary="获取 Android App 列表",
    description="获取项目下的所有 Android App 列表，支持搜索和过滤",
)
async def list_android_apps(
    project_identifier: str,
    service: AndroidAppServiceDep,
    pagination: PaginationDep,
    folder_id: Optional[str] = Query(None, description="文件夹 ID 过滤"),
    search: Optional[str] = Query(None, description="搜索关键词"),
):
    """获取 Android App 列表"""
    result = await service.list_apps(
        project_identifier=project_identifier,
        folder_id=folder_id,
        offset=(pagination.p - 1) * pagination.page_size,
        limit=pagination.page_size,
        search=search,
    )

    return SuccessResponse(data=result)


@router.get(
    "/{app_id}",
    response_model=SuccessResponse,
    summary="获取 Android App 详情",
    description="获取指定 Android App 的详细信息",
)
async def get_android_app(
    app_id: str,
    service: AndroidAppServiceDep,
):
    """获取 Android App 详情"""
    result = await service.get_app(app_id=app_id)
    return SuccessResponse(data=result)


@router.patch(
    "/{app_id}",
    response_model=SuccessResponse,
    summary="更新 Android App",
    description="更新指定 Android App 的信息",
)
async def update_android_app(
    app_id: str,
    request: UpdateAndroidAppRequest,
    service: AndroidAppServiceDep,
):
    """更新 Android App"""
    result = await service.update_app(
        app_id=app_id,
        **request.model_dump(exclude_none=True),
    )

    return SuccessResponse(data=result, message="Android App 更新成功")


@router.delete(
    "/{app_id}",
    response_model=SuccessResponse,
    summary="删除 Android App",
    description="删除指定的 Android App",
)
async def delete_android_app(
    app_id: str,
    service: AndroidAppServiceDep,
):
    """删除 Android App"""
    result = await service.delete_app(app_id=app_id)
    return SuccessResponse(data=result)


# ============ Android 子功能管理接口 ============

@router.get(
    "/sub-functions",
    response_model=SuccessResponse,
    summary="获取 Android 子功能列表",
    description="获取项目下的所有 Android 子功能列表，支持搜索和过滤",
)
async def list_android_sub_functions(
    project_identifier: str,
    service: AndroidAppServiceDep,
    pagination: PaginationDep,
    function_id: Optional[str] = Query(None, description="功能 ID 过滤"),
    folder_id: Optional[str] = Query(None, description="文件夹 ID 过滤"),
    search: Optional[str] = Query(None, description="搜索关键词"),
):
    """获取 Android 子功能列表"""
    result = await service.list_sub_functions(
        project_identifier=project_identifier,
        function_id=function_id,
        folder_id=folder_id,
        offset=(pagination.p - 1) * pagination.page_size,
        limit=pagination.page_size,
        search=search,
    )

    return SuccessResponse(data=result)


@router.post(
    "/sub-functions",
    response_model=SuccessResponse,
    summary="创建 Android 子功能",
    description="创建新的 Android 子功能定义",
)
async def create_android_sub_function(
    project_identifier: str,
    request: CreateAndroidSubFunctionRequest,
    service: AndroidAppServiceDep,
):
    """创建 Android 子功能"""
    result = await service.create_sub_function(
        project_identifier=project_identifier,
        **request.model_dump(),
    )

    return SuccessResponse(data=result, message="Android 子功能创建成功")


@router.get(
    "/sub-functions/{sub_function_id}",
    response_model=SuccessResponse,
    summary="获取 Android 子功能详情",
    description="获取指定 Android 子功能的详细信息",
)
async def get_android_sub_function(
    sub_function_id: str,
    service: AndroidAppServiceDep,
):
    """获取 Android 子功能详情"""
    result = await service.get_sub_function(sub_function_id=sub_function_id)
    return SuccessResponse(data=result)


@router.patch(
    "/sub-functions/{sub_function_id}",
    response_model=SuccessResponse,
    summary="更新 Android 子功能",
    description="更新指定 Android 子功能的信息",
)
async def update_android_sub_function(
    sub_function_id: str,
    request: UpdateAndroidSubFunctionRequest,
    service: AndroidAppServiceDep,
):
    """更新 Android 子功能"""
    result = await service.update_sub_function(
        sub_function_id=sub_function_id,
        **request.model_dump(exclude_none=True),
    )

    return SuccessResponse(data=result, message="Android 子功能更新成功")


@router.delete(
    "/sub-functions/{sub_function_id}",
    response_model=SuccessResponse,
    summary="删除 Android 子功能",
    description="删除指定的 Android 子功能",
)
async def delete_android_sub_function(
    sub_function_id: str,
    service: AndroidAppServiceDep,
):
    """删除 Android 子功能"""
    result = await service.delete_sub_function(sub_function_id=sub_function_id)
    return SuccessResponse(data=result)


@router.get(
    "/sub-functions/{sub_function_id}/artifacts",
    response_model=SuccessResponse,
    summary="获取 Android 子功能测试成果物",
    description="获取指定 Android 子功能的所有测试成果物（测试计划、测试用例、测试脚本、测试报告）",
)
async def get_android_sub_function_artifacts(
    sub_function_id: str,
    service: AndroidAppServiceDep,
):
    """获取 Android 子功能测试成果物"""
    result = await service.get_sub_function_artifacts(sub_function_id=sub_function_id)
    return SuccessResponse(data=result)
