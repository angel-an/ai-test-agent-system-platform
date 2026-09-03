"""
API 依赖注入

提供 API 路由所需的依赖项
"""

import logging
from typing import Annotated, Optional
from uuid import UUID

from fastapi import Depends, HTTPException, Query, Request, status
from fastapi.security import OAuth2PasswordBearer
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from app.config.database import get_db
from app.config.settings import settings
from app.models.user import User
from app.auth.auth import decode_access_token
from app.services.project_service import ProjectService
from app.services.folder_service import FolderService
from app.services.test_case_service import TestCaseService
from app.services.export_service import ExportService
from app.services.test_run_service import TestRunService
from app.services.test_result_service import TestResultService
from app.services.test_plan_service import TestPlanService
from app.services.api_test_service import APITestService
from app.services.web_function_service import WebFunctionService
from app.services.android_app_service import AndroidAppService
from app.services.ios_app_service import IOSAppService
from app.services.knowledge_base_service import KnowledgeBaseService
from app.schemas.pagination import PaginationParams
from app.config.database import get_mongodb

logger = logging.getLogger(__name__)

# OAuth2 密码流认证方案
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/v2/auth/login", auto_error=False)

# pylint: disable  MC80OmFIVnBZMlhscm9ua3VMazZTMkkxZGc9PTplNWYwYWI0Ng==

async def get_project_service(
    db: AsyncSession = Depends(get_db),
) -> ProjectService:
    """获取项目服务实例"""
    return ProjectService(db)

async def get_folder_service(
    db: AsyncSession = Depends(get_db),
) -> FolderService:
    """获取文件夹服务实例"""
    return FolderService(db)

async def get_test_case_service(
    db: AsyncSession = Depends(get_db),
    mongodb = Depends(get_mongodb),
) -> TestCaseService:
    """获取测试用例服务实例"""
    return TestCaseService(db, mongodb)

async def get_export_service(
    db: AsyncSession = Depends(get_db),
    mongodb = Depends(get_mongodb),
) -> ExportService:
    """获取导出服务实例"""
    return ExportService(db, mongodb)

# pylint: disable  MS80OmFIVnBZMlhscm9ua3VMazZTMkkxZGc9PTplNWYwYWI0Ng==

async def get_test_run_service(
    db: AsyncSession = Depends(get_db),
) -> TestRunService:
    """获取测试运行服务实例"""
    return TestRunService(db)

async def get_test_result_service(
    db: AsyncSession = Depends(get_db),
) -> TestResultService:
    """获取测试结果服务实例"""
    return TestResultService(db)
# noqa  Mi80OmFIVnBZMlhscm9ua3VMazZTMkkxZGc9PTplNWYwYWI0Ng==

async def get_test_plan_service(
    db: AsyncSession = Depends(get_db),
) -> TestPlanService:
    """获取测试计划服务实例"""
    return TestPlanService(db)

async def get_api_test_service(
    db: AsyncSession = Depends(get_db),
    mongodb = Depends(get_mongodb),
) -> APITestService:
    """获取 API 测试服务实例"""
    return APITestService(db, mongodb)

async def get_web_function_service(
    db: AsyncSession = Depends(get_db),
) -> WebFunctionService:
    """获取 Web 功能服务实例"""
    return WebFunctionService(db)

async def get_android_app_service(
    db: AsyncSession = Depends(get_db),
) -> AndroidAppService:
    """获取 Android App 服务实例"""
    return AndroidAppService(db)

async def get_ios_app_service(
    db: AsyncSession = Depends(get_db),
) -> IOSAppService:
    """获取 iOS App 服务实例"""
    return IOSAppService(db)

async def get_security_test_service(
    db: AsyncSession = Depends(get_db),
) -> "SecurityTestService":
    """获取安全测试服务实例"""
    from app.services.security_test_service import SecurityTestService
    return SecurityTestService(db)

async def get_knowledge_base_service(
    db: AsyncSession = Depends(get_db),
) -> KnowledgeBaseService:
    """获取知识库服务实例"""
    return KnowledgeBaseService(db)

def get_pagination_params(
    p: int = Query(
        default=1,
        ge=1,
        description="页码，从 1 开始",
    ),
    page_size: int = Query(
        default=settings.pagination_default_size,
        ge=1,
        le=settings.pagination_max_size,
        description=f"每页数量，默认 {settings.pagination_default_size}，最大 {settings.pagination_max_size}",
    ),
) -> PaginationParams:
    """
    获取分页参数

    参考: https://www.browserstack.com/docs/test-management/api-reference/pagination
    """
    return PaginationParams(
        p=p,
        page_size=page_size,
    )

# 类型别名，用于依赖注入
ProjectServiceDep = Annotated[ProjectService, Depends(get_project_service)]
FolderServiceDep = Annotated[FolderService, Depends(get_folder_service)]
TestCaseServiceDep = Annotated[TestCaseService, Depends(get_test_case_service)]
ExportServiceDep = Annotated[ExportService, Depends(get_export_service)]
TestRunServiceDep = Annotated[TestRunService, Depends(get_test_run_service)]
TestResultServiceDep = Annotated[TestResultService, Depends(get_test_result_service)]
TestPlanServiceDep = Annotated[TestPlanService, Depends(get_test_plan_service)]
APITestServiceDep = Annotated[APITestService, Depends(get_api_test_service)]
WebFunctionServiceDep = Annotated[WebFunctionService, Depends(get_web_function_service)]
AndroidAppServiceDep = Annotated[AndroidAppService, Depends(get_android_app_service)]
IOSAppServiceDep = Annotated[IOSAppService, Depends(get_ios_app_service)]
SecurityTestServiceDep = Annotated["SecurityTestService", Depends(get_security_test_service)]
KnowledgeBaseServiceDep = Annotated[KnowledgeBaseService, Depends(get_knowledge_base_service)]
PaginationDep = Annotated[PaginationParams, Depends(get_pagination_params)]
DbSessionDep = Annotated[AsyncSession, Depends(get_db)]

async def get_current_user(
    token: Annotated[Optional[str], Depends(oauth2_scheme)],
    db: AsyncSession = Depends(get_db),
) -> Optional[User]:
    """
    获取当前认证用户

    从 JWT Token 中解析用户身份，并从数据库查询用户信息。
    如果未提供 Token 或 Token 无效，返回 None。
    """
    if not token:
        return None
    
    payload = decode_access_token(token)
    if payload is None:
        return None
    
    user_id_str: Optional[str] = payload.get("sub")
    if user_id_str is None:
        return None
    
    try:
        user_id = UUID(user_id_str)
    except ValueError:
        return None
    
    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()
    return user


async def get_current_active_user(
    token: Annotated[Optional[str], Depends(oauth2_scheme)],
    db: AsyncSession = Depends(get_db),
) -> User:
    """
    获取当前已激活的认证用户

    如果未认证或用户不存在/未激活，抛出 401 异常。
    """
    user = await get_current_user(token, db)
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="未认证或认证已过期",
            headers={"WWW-Authenticate": "Bearer"},
        )
    if not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="用户已被禁用",
        )
    return user


async def get_current_superuser(
    request: Request,
    token: Annotated[Optional[str], Depends(oauth2_scheme)],
    db: AsyncSession = Depends(get_db),
) -> User:
    """
    获取当前超级管理员（管理操作鉴权，rev47 + rev48/P2 审计）

    未认证 → 401（get_current_active_user 抛出）；已认证但非超管 → 403。
    rev48（P2 修复）：**允许与拒绝都记录服务端审计**——401/403 在依赖层
    提前抛出不经过端点日志，必须在依赖内记录 allow/deny
    （actor / path / decision / reason）。

    Returns:
        User: 当前超管用户
    """
    path = getattr(request, "url", None)
    path = str(path.path) if path is not None else "?"
    try:
        user = await get_current_active_user(token, db)
    except HTTPException as exc:
        logger.warning(
            "[audit] action=access.decision path=%s decision=deny reason=unauthorized "
            "status=%s detail=%s",
            path, exc.status_code, exc.detail,
        )
        raise
    if not user.is_superuser:
        logger.warning(
            "[audit] action=access.decision path=%s decision=deny reason=not_superuser "
            "status=403 actor=%s(%s)",
            path, user.username, user.id,
        )
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="需要管理员权限",
        )
    logger.info(
        "[audit] action=access.decision path=%s decision=allow actor=%s(%s)",
        path, user.username, user.id,
    )
    return user


def get_current_user_id() -> UUID:
    """
    获取当前用户 ID（简化实现，用于非认证场景）

    注意: 这是一个向后兼容的简化实现，新代码应使用 get_current_active_user
    """
    return UUID(settings.default_user_id)


CurrentUserDep = Annotated[User, Depends(get_current_active_user)]
CurrentSuperuserDep = Annotated[User, Depends(get_current_superuser)]
CurrentUserIdDep = Annotated[UUID, Depends(get_current_user_id)]

