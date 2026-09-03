"""
应用配置管理

使用 Pydantic Settings 管理应用配置，支持环境变量和 .env 文件
"""

from functools import lru_cache
from typing import Optional

from pydantic_settings import BaseSettings, SettingsConfigDict
# pragma: no cover

class Settings(BaseSettings):
    """应用配置类"""
    
    model_config = SettingsConfigDict(
        env_file="app/.env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )
    
    # 应用基础配置
    app_name: str = "测试管理系统"
    app_version: str = "1.0.0"
    debug: bool = False
    api_prefix: str = "/api/v2"
    
    # PostgreSQL 数据库配置
    postgres_host: str = "localhost"
    postgres_port: int = 5432
    postgres_user: str = "postgres"
    postgres_password: str = "postgres"
    postgres_db: str = "langgraph_platform_db"
    
    # Redis 配置
    redis_uri: str = "redis://localhost:6379/0"
    
    @property
    def postgres_url(self) -> str:
        """获取 PostgreSQL 连接 URL"""
        return (
            f"postgresql+asyncpg://{self.postgres_user}:{self.postgres_password}"
            f"@{self.postgres_host}:{self.postgres_port}/{self.postgres_db}"
        )
    
    @property
    def postgres_sync_url(self) -> str:
        """获取 PostgreSQL 同步连接 URL（用于 Alembic）"""
        return (
            f"postgresql://{self.postgres_user}:{self.postgres_password}"
            f"@{self.postgres_host}:{self.postgres_port}/{self.postgres_db}"
        )
    
    # MongoDB 配置
    mongodb_host: str = "121.40.159.60"
    mongodb_port: int = 27017
    mongodb_user: Optional[str] = None
    mongodb_password: Optional[str] = None
    mongodb_db: str = "ai_test_management"

    # pylint: disable
    
    @property
    def mongodb_url(self) -> str:
        """获取 MongoDB 连接 URL"""
        if self.mongodb_user and self.mongodb_password:
            return (
                f"mongodb://{self.mongodb_user}:{self.mongodb_password}"
                f"@{self.mongodb_host}:{self.mongodb_port}"
            )
        return f"mongodb://{self.mongodb_host}:{self.mongodb_port}"
    
    # 速率限制配置
    rate_limit_requests: int = 300  # 每分钟最大请求数
    rate_limit_window: int = 60  # 时间窗口（秒）
    
    # 分页配置
    pagination_default_size: int = 30
    pagination_max_size: int = 300

    @property
    def default_page_size(self) -> int:
        """获取默认分页大小（别名）"""
        return self.pagination_default_size

    @property
    def max_page_size(self) -> int:
        """获取最大分页大小（别名）"""
        return self.pagination_max_size
    
    # CORS 配置
    cors_origins: list[str] = ["http://localhost:3000", "http://localhost:8080"]

    # JWT 配置（用于认证）
    secret_key: str = "your-secret-key-change-in-production"
    algorithm: str = "HS256"
    access_token_expire_minutes: int = 30

    # 默认测试用户配置（开发环境使用）
    default_user_id: str = "00000000-0000-0000-0000-000000000001"
    default_user_email: str = "admin@test.com"
    default_user_name: str = "管理员"
    # rev47/48（P1 修复）：仅显式开发模式才在启动时把默认用户创建/提升为超管。
    # 生产必须保持 0（默认）：超管只能通过受控命令授予
    # （python -m app.cli grant-superuser <username>）或人工 DB 操作。
    enable_dev_default_superuser: bool = False

    # MinIO 对象存储配置
    minio_endpoint: str = "114.55.110.60:9000"
    minio_access_key: str = "minioadmin"
    minio_secret_key: str = "minioadmin"
    minio_bucket: str = "test-management"
    minio_secure: bool = False  # 是否使用 HTTPS
    minio_region: Optional[str] = None

    # 附件配置
    attachment_max_size: int = 50 * 1024 * 1024  # 50 MB
    attachment_allowed_types: list[str] = [
        "image/jpeg", "image/png", "image/gif", "image/webp",
        "application/pdf", "application/zip", "application/x-rar-compressed",
        "text/plain", "text/csv",
        "application/msword", "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        "application/vnd.ms-excel", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    ]

    # PDF 解析配置
    enable_pdf_multimodal: bool = False  # 是否启用 PDF 多模态图片解析（需要配置 DOUBAO_API_KEY）

    # 大模型配置
    deepseek_api_key: Optional[str] = None
    # 备用模型配置（当主模型 503 过载时自动切换）
    fallback_llm_model: Optional[str] = None  # 如 "openai:gpt-4o-mini"

    # 图片/多模态模型配置（豆包/火山引擎）
    image_parser_api_key: Optional[str] = None  # 豆包 API Key
    image_parser_api_base: str = "https://ark.cn-beijing.volces.com/api/v3"  # 火山引擎 API 地址
    image_parser_model: str = "doubao-seed-1-6-vision-250815"  # 豆包多模态模型

    # 性能测试工作目录配置
    perf_workspace_root: str = "backend/app/agents/perf/workspace"
    perf_mcp_root: str = "backend/mcp/perf"
    perf_yaml_tests: str = "backend/app/agents/perf/yaml-tests"
    perf_skills_root: str = "backend/app/agents/perf/agent_skills"

    # 接口测试工作目录配置
    api_workspace_root: str = "backend/workspace/api"
    api_mcp_root: str = "backend/mcp/api"
    api_skills_root: str = "backend/workspace/api"

    # Web 测试工作目录配置
    web_mcp_workspace_root: str = "backend/workspace/web_mcp"
    web_mcp_root: str = "backend/mcp/web_mcp"
    web_mcp_skills_root: str = "backend/workspace/web_mcp"

    # Web CLI 测试工作目录配置
    web_cli_workspace_root: str = "backend/workspace/web_cli"
    web_cli_skills_root: str = ".claude/skills"

    # Webwright 测试工作目录配置
    webwright_workspace_root: str = "backend/workspace/webwright"
    webwright_skills_root: str = ".claude/skills"

    # Web Chrome 测试工作目录配置
    web_chrome_workspace_root: str = "backend/workspace/web_chrome"
    web_chrome_mcp_root: str = "backend/mcp/web_chrome"
    web_chrome_skills_root: str = "backend/workspace/web_chrome"

    # 测试用例工作目录配置
    testcase_workspace_root: str = "backend/workspace/testcase"
    testcase_skills_root: str = "backend/workspace/testcase"

    # 安全测试工作目录配置
    security_workspace_root: str = "backend/workspace/security"
    security_skills_root: str = ".claude/skills/security"

    # Android 测试工作目录配置
    android_workspace_root: str = "backend/workspace/android"
    android_skills_root: str = ".claude/skills/android"

    # iOS 测试工作目录配置
    ios_workspace_root: str = "backend/workspace/ios"
    ios_skills_root: str = ".claude/skills/ios"

    # 应用端口（用于内部 API 调用）
    app_port: int = 8000

    # IDP 配置
    idp_base_url: str = "https://idp-api.example.com"
    idp_web_base_url: str = "https://idp.example.com"  # IDP 前端地址，用于生成可点击链接
    idp_username: str = ""
    idp_password: str = ""
    idp_token: str = ""  # 直接注入 Token（MVP 阶段替代自动登录）
    idp_organization_id: int = 1
    idp_auto_create_enabled: bool = False
    idp_dry_run: bool = True

    # IDP 项目映射配置（YAML 文件路径）
    idp_project_mapping_path: str = "backend/config/idp_projects.yaml"

    # RAG / 知识库配置
    # 参照 anything-chat-rag 框架的 Embedding 配置：
    #   EMBEDDING_BINDING=openai
    #   EMBEDDING_MODEL=Qwen/Qwen3-Embedding-0.6B
    #   EMBEDDING_DIM=1024
    #   EMBEDDING_BINDING_HOST=https://llmapi.dtyunxi.cn/v1/
    #   EMBEDDING_BINDING_API_KEY=sk-6cwnCNPVwFMwPgZPk0ejyQ
    rag_server_url: str = "http://127.0.0.1:8002"
    rag_default_space_strategy: str = "project"
    rag_degradation_enabled: bool = True
    rag_chunk_size: int = 1000
    rag_chunk_overlap: int = 200
    rag_top_k: int = 5
    rag_similarity_threshold: float = 0.7

    # Embedding API 配置（与 anything-chat-rag 框架保持一致）
    # 默认使用已测试过的内部模型：Qwen/Qwen3-Embedding-0.6B
    embedding_binding: str = "openai"
    embedding_model: str = "Qwen/Qwen3-Embedding-0.6B"
    embedding_dim: int = 1024
    embedding_token_limit: int = 8192
    embedding_binding_host: str = "https://llmapi.dtyunxi.cn/v1"
    embedding_binding_api_key: Optional[str] = None

    @property
    def idp_token_available(self) -> bool:
        """检查是否有可用的 IDP Token"""
        return bool(self.idp_token and len(self.idp_token) > 10)

@lru_cache
def get_settings() -> Settings:
    """获取配置单例"""
    return Settings()  # noqa

settings = get_settings()

