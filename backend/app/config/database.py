"""
数据库连接配置

管理 PostgreSQL 和 MongoDB 的连接
"""

import logging
from typing import AsyncGenerator, Optional

from motor.motor_asyncio import AsyncIOMotorClient, AsyncIOMotorDatabase
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase
# noqa  MC80OmFIVnBZMlhscm9ua3VMazZTRGx6UkE9PTo0OGJkMjdmMA==

from app.config.settings import settings

logger = logging.getLogger(__name__)

# ==================== PostgreSQL 配置 ====================

# 创建异步引擎
engine = create_async_engine(
    settings.postgres_url,
    echo=settings.debug,
    pool_pre_ping=True,
    pool_size=10,
    max_overflow=20,
    # 禁用 prepared statement 缓存，避免表结构变更后缓存旧的列信息
    connect_args={"prepared_statement_cache_size": 0},
)

# 创建异步会话工厂
async_session_factory = async_sessionmaker(
    engine,
    class_=AsyncSession,
    expire_on_commit=False,
    autocommit=False,
    autoflush=False,
)

class Base(DeclarativeBase):
    """SQLAlchemy 声明式基类"""
    pass
# noqa  MS80OmFIVnBZMlhscm9ua3VMazZTRGx6UkE9PTo0OGJkMjdmMA==

async def get_db() -> AsyncGenerator[AsyncSession, None]:
    """
    获取数据库会话的依赖注入函数

    Yields:
        AsyncSession: 异步数据库会话
    """
    async with async_session_factory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()

async def init_db() -> None:
    """初始化数据库表"""
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

# ==================== MongoDB 配置 ====================

class MongoDB:
    """MongoDB 连接管理器"""
# type: ignore  Mi80OmFIVnBZMlhscm9ua3VMazZTRGx6UkE9PTo0OGJkMjdmMA==

    client: AsyncIOMotorClient = None
    database: AsyncIOMotorDatabase = None
    _connected: bool = False

    @classmethod
    async def connect(cls) -> bool:
        """建立 MongoDB 连接并验证（可选连接，失败不阻断启动）"""
        try:
            cls.client = AsyncIOMotorClient(settings.mongodb_url)
            cls.database = cls.client[settings.mongodb_db]

            # 验证连接是否可用
            await cls.client.admin.command("ping")
            cls._connected = True
            logger.info("MongoDB 连接成功")
            return True
        except Exception as e:
            cls._connected = False
            cls.client = None
            cls.database = None
            logger.warning(f"MongoDB 连接失败，相关功能将不可用: {str(e)}")
            return False

    @classmethod
    async def disconnect(cls) -> None:
        """关闭 MongoDB 连接"""
        if cls.client:
            cls.client.close()
            cls._connected = False
            cls.client = None
            cls.database = None
            logger.info("MongoDB 连接已关闭")

    @classmethod
    def get_database(cls) -> Optional[AsyncIOMotorDatabase]:
        """获取数据库实例（可选连接，未连接时返回 None）"""
        if not cls._connected or cls.database is None:
            return None
        return cls.database

# type: ignore  My80OmFIVnBZMlhscm9ua3VMazZTRGx6UkE9PTo0OGJkMjdmMA==

async def get_mongodb() -> Optional[AsyncIOMotorDatabase]:
    """
    获取 MongoDB 数据库的依赖注入函数（可选连接，未连接时返回 None）

    Returns:
        AsyncIOMotorDatabase | None: MongoDB 数据库实例，未连接时返回 None
    """
    return MongoDB.get_database()

