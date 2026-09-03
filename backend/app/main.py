"""
测试管理系统主入口

FastAPI 应用程序入口点
"""

from contextlib import asynccontextmanager
from uuid import UUID

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import select

from app.api import api_router
from app.config.settings import settings
from app.config.database import engine, MongoDB, async_session_factory
from app.middleware.rate_limiter import RateLimiterMiddleware
from app.middleware.error_handler import setup_exception_handlers
from app.models.base import Base
from app.models.user import User

# Import all models DIRECTLY from their modules (not through __init__.py)
# This ensures correct initialization order for SQLAlchemy foreign key resolution
# IMPORTANT: Models referenced by foreign keys must be imported FIRST

from app.models.project import Project
from app.models.web_path_template import ProjectWebPathTemplate
from app.models.folder import Folder
from app.models.team import Team
from app.models.test_case import TestCase, TestStep, Tag, TestCaseTag
from app.models.scheduled_run import ScheduledRun, ScheduledRunExecution
from app.models.test_run import TestRun, TestRunTestCase
from app.models.test_result import TestResult, TestStepResult
from app.models.attachment import Attachment
from app.models.configuration import Configuration
from app.models.test_plan import TestPlan
from app.models.api_test import APITest, APITestRun, APITestResult
from app.models.api_endpoint import APIEndpoint
from app.models.security_test import SecurityTest, SecurityVulnerability, SecurityReport
from app.models.web_script_registry import WebScriptRegistry
# pylint: disable  MC80OmFIVnBZMlhscm9ua3VMazZPRVF6YkE9PToyM2NmMTExNQ==

# Import scenario models LAST (they depend on projects, folders, users, api_endpoints)
from app.models.test_scenario import (
    TestScenario,
    ScenarioStep,
    StepDataMapping,
    ScenarioVariable,
    ScenarioRun,
    ScenarioStepResult,
)


async def ensure_default_user():
    """
    确保默认测试用户存在（开发环境使用）

    rev48（P1 修复）：默认用户**创建/提升为超管**仅在显式开发模式
    （ENABLE_DEV_DEFAULT_SUPERUSER=1）下发生。生产默认 0：
    - 不创建默认用户（若不存在）；
    - 已存在的默认用户**不会**被提升为超管（维持迁移 0011 的
      "存量用户默认非超管"安全基线）；
    - 生产授予超管走受控命令：`python -m app.cli grant-superuser <username>`。
    """
    dev_bootstrap = settings.enable_dev_default_superuser
    async with async_session_factory() as session:
        # 检查默认用户是否存在
        user_id = UUID(settings.default_user_id)
        result = await session.execute(
            select(User).where(User.id == user_id)
        )
        user = result.scalar_one_or_none()

        if not user:
            if not dev_bootstrap:
                print(
                    "[WARN] 默认用户不存在且非开发模式：跳过创建。"
                    "超管请用 python -m app.cli grant-superuser <username> 授予"
                )
                return
            # 创建默认用户（仅开发模式）
            user = User(
                id=user_id,
                email=settings.default_user_email,
                username=settings.default_user_name,
                password_hash="not_used_for_dev",  # 开发环境不需要真实密码
                is_active=True,
                is_superuser=True,  # rev47：默认开发管理员具备超管权限（开发模式）
            )
            session.add(user)
            await session.commit()
            print(f"[OK] Created default test user: {settings.default_user_email}")
            return

        if dev_bootstrap and not user.is_superuser:
            # 仅开发模式：存量默认用户幂等提升为超管
            user.is_superuser = True
            await session.commit()
            print(f"[OK] Default test user exists: {settings.default_user_email} (dev superuser)")
        else:
            if user.is_superuser:
                print(f"[OK] Default test user exists: {settings.default_user_email}")
            else:
                print(
                    f"[WARN] 默认用户存在但非超管（ENABLE_DEV_DEFAULT_SUPERUSER=0，符合生产基线）。"
                    f"如需超管：python -m app.cli grant-superuser {settings.default_user_name}"
                )
# pylint: disable  MS80OmFIVnBZMlhscm9ua3VMazZPRVF6YkE9PToyM2NmMTExNQ==

async def ensure_enum_values():
    """
    确保 PostgreSQL 枚举类型包含所有必要的值

    在应用启动时检查并添加缺失的枚举值，避免数据库迁移
    """
    from sqlalchemy import text
    from app.models.attachment import AttachmentEntityType

    async with engine.begin() as conn:
        # 获取所有枚举值
        # SQLAlchemy's existing attachmententitytype enum stores enum member
        # names (for example API_TEST_PLAN), not their lowercase values.
        enum_values = [e.name for e in AttachmentEntityType]

        # 检查枚举类型是否存在
        result = await conn.execute(
            text("SELECT 1 FROM pg_type WHERE typname = 'attachmententitytype'")
        )
        if not result.scalar_one_or_none():
            print("[Enum Fix] attachmententitytype 枚举类型不存在，跳过修复")
            return

        # 获取当前枚举值
        result = await conn.execute(
            text("""
                SELECT enumlabel FROM pg_enum
                WHERE enumtypid = (SELECT oid FROM pg_type WHERE typname = 'attachmententitytype')
            """)
        )
        existing_values = {row[0] for row in result.all()}

        # 添加缺失的枚举值
        for value in enum_values:
            if value not in existing_values:
                await conn.execute(
                    text(f"ALTER TYPE attachmententitytype ADD VALUE IF NOT EXISTS '{value}'")
                )
                print(f"[Enum Fix] 添加枚举值: {value}")

        print(f"[Enum Fix] 枚举值检查完成，共 {len(enum_values)} 个值")


async def recover_stuck_executions():
    """
    启动时恢复被中断的执行。

    服务器重启时，可能有一些作业处于 RUNNING 状态但永远不会完成。
    将这些作业标记为 FAILED，并更新关联的 TestRun 状态。
    """
    from app.models.test_run import TestRunScriptJob
    from app.schemas.enums import JobStatus, TestRunState

    async with async_session_factory() as session:
        # 查找所有 RUNNING 状态的作业
        result = await session.execute(
            select(TestRunScriptJob).where(TestRunScriptJob.status == JobStatus.RUNNING)
        )
        stuck_jobs = result.scalars().all()

        if not stuck_jobs:
            return

        print(f"[Recover] 发现 {len(stuck_jobs)} 个被中断的执行，正在恢复...")

        affected_test_run_ids = set()

        for job in stuck_jobs:
            job.status = JobStatus.FAILED
            job.error_message = job.error_message or "执行被中断（服务器重启或进程终止）"
            if not job.completed_at:
                from datetime import datetime, timezone
                job.completed_at = datetime.now(timezone.utc).replace(tzinfo=None)
            affected_test_run_ids.add(job.test_run_id)
            print(f"[Recover] 作业 {job.id} -> FAILED")

        # 更新关联的 TestRun 状态（从 IN_PROGRESS 改为 REJECTED）
        if affected_test_run_ids:
            result = await session.execute(
                select(TestRun).where(
                    TestRun.id.in_(affected_test_run_ids),
                    TestRun.run_state == TestRunState.IN_PROGRESS
                )
            )
            stuck_runs = result.scalars().all()
            for run in stuck_runs:
                run.run_state = TestRunState.REJECTED
                print(f"[Recover] 测试运行 {run.identifier} -> REJECTED")

        await session.commit()
        print(f"[Recover] 恢复完成")


@asynccontextmanager
async def lifespan(app: FastAPI):
    """应用生命周期管理"""
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    # 修复枚举值（不迁移历史数据，新值自动生效）
    await ensure_enum_values()

    await ensure_default_user()

    # 恢复被中断的执行
    await recover_stuck_executions()

    # 启动定时任务调度器
    from app.services.scheduler import scheduler, load_all_jobs, register_script_repair_job
    await load_all_jobs()
    # P0-2：Web 脚本评审修复循环（WEB_SCRIPT_REPAIR_INTERVAL_MINUTES>0 时注册）
    register_script_repair_job()
    scheduler.start()

    # 连接 MongoDB
    await MongoDB.connect()

    yield

    scheduler.shutdown(wait=False)
    await MongoDB.disconnect()
    await engine.dispose()
# pragma: no cover  Mi80OmFIVnBZMlhscm9ua3VMazZPRVF6YkE9PToyM2NmMTExNQ==

def create_app() -> FastAPI:
    """
    创建 FastAPI 应用实例
    
    Returns:
        FastAPI: 应用实例
    """
    app = FastAPI(
        title=settings.app_name,
        description="""
# 测试管理系统 API

专业的软件测试管理系统，提供完整的测试用例管理功能。

## 功能特性

- **项目管理**: 创建、查看、删除项目
- **文件夹管理**: 层级文件夹结构，支持移动操作
- **测试用例管理**: 完整的测试用例 CRUD，支持步骤、标签、版本管理
- **分页支持**: 所有列表接口支持分页
- **速率限制**: 每分钟最多 300 个请求

## API 版本

当前版本: v2

## 认证

所有 API 需要认证（待实现）
        """,
        version=settings.app_version,
        docs_url="/docs",
        redoc_url="/redoc",
        openapi_url="/openapi.json",
        lifespan=lifespan,
    )
    
    # 添加 CORS 中间件
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    
    # 添加速率限制中间件
    app.add_middleware(RateLimiterMiddleware)
    
    # 设置异常处理器
    setup_exception_handlers(app)
    
    # 注册 API 路由
    app.include_router(api_router)
    
    # 健康检查端点
    @app.get("/health", tags=["系统"])
    async def health_check():
        """健康检查"""
        return {
            "status": "healthy",
            "app_name": settings.app_name,
            "version": settings.app_version,
        }
    
    # 根路径
    @app.get("/", tags=["系统"])
    async def root():
        """API 根路径"""
        return {
            "message": "欢迎使用测试管理系统 API",
            "docs": "/docs",
            "version": settings.app_version,
        }
    
    return app

# pragma: no cover  My80OmFIVnBZMlhscm9ua3VMazZPRVF6YkE9PToyM2NmMTExNQ==

# 创建应用实例
app = create_app()

if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8000)
