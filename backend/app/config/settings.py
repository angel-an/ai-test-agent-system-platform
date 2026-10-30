"""
应用配置管理

使用 Pydantic Settings 管理应用配置，支持环境变量和 .env 文件
"""

from functools import lru_cache
from pathlib import Path
from typing import Optional

from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
# pragma: no cover

# 仓库根目录：本文件位于 <repo>/backend/app/config/settings.py
_REPO_ROOT = Path(__file__).resolve().parents[3]

# ---------------------------------------------------------------------------
# Skill 维护目录（唯一约定，改动前请先读这里）
# ---------------------------------------------------------------------------
# 1. **唯一维护目录**：`<repo>/.claude/skills/`。
#    每个 agent 一个子目录：`<repo>/.claude/skills/<agent>/<skill>/SKILL.md`。
#    不要在 `backend/workspace/**` 下另存一份 skill —— 双份副本必然漂移
#    （历史上 testcase 曾在 backend/workspace/testcase/skills/ 另存一份，
#     其中 uat-scenario-design 更新到 194 行而 .claude 侧仍是 117 行）。
#
# 2. **接线约定**：所有 `*_skills_root` 一律等于 `.claude/skills`，
#    agent 侧 `SkillsMiddleware(sources=["/skills/<agent目录>/"])`。
#    `/skills/` 是 CompositeBackend 的挂载前缀（见各 agent.py 的 routes），
#    SkillsMiddleware 会把该前缀去掉后拼到 skills_root 上。
#    因此 `sources=["/skills/"]` + root=`.claude/skills/<agent>` 这种写法
#    等价但易与上一条混淆，新代码请统一用本约定的写法。
#
# 3. **自检**：`app/config/skill_assembly_check.py` 在启动时校验每个 source
#    真能装配到 SKILL.md，装配失败记 ERROR。新增 agent 需同步其 default_specs。
# ---------------------------------------------------------------------------


class Settings(BaseSettings):
    """应用配置类"""
    
    model_config = SettingsConfigDict(
        # 绝对路径：``env_file`` 默认按进程 CWD 解析，而本项目存在两种启动方式
        # （``cd backend && uvicorn app.main:app`` 与仓库根 ``start_server_*.py``）。
        # 用绝对路径可保证两种方式读到同一份本地配置；外部环境变量仍优先于它。
        env_file=str(_REPO_ROOT / "backend" / "app" / ".env"),
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    @model_validator(mode="after")
    def _anchor_relative_roots(self):
        """把所有相对路径的 ``*_root(s)`` 锚定到仓库根目录。

        这些配置按"仓库根相对路径"书写（如 ``backend/workspace/api``），
        但 ``env_file`` 是相对 **进程 CWD** 解析的，而实际存在两种启动方式
        （``cd backend && uvicorn app.main:app`` 与仓库根 ``start_server_*.py``）。

        不锚定的后果是同一份配置在两种启动方式下解析到不同目录，且失败是
        静默的：实测 ``cd backend`` 启动时 ``testcase_skills_root`` 被解析成
        ``<repo>/backend/backend/workspace/testcase``（不存在），
        导致 testcase Agent 加载 0 个 skill（quality-review 等全部失效）。
        """
        for name in type(self).model_fields:
            if not (name.endswith("_root") or name.endswith("_roots")):
                continue
            value = getattr(self, name, None)
            if not isinstance(value, str) or not value.strip():
                continue
            path = Path(value)
            if not path.is_absolute():
                setattr(self, name, str((_REPO_ROOT / path).resolve()))
        # idp_project_mapping_path 不以 _root 结尾但被同一 CWD 问题击中：
        # 相对路径书写、两种启动方式解析结果不同，且失败静默
        #（实测 cd backend 启动时映射文件"不存在"，自动提单 20/20 因项目未匹配被跳过）。
        mapping_path = Path(self.idp_project_mapping_path)
        if not mapping_path.is_absolute():
            self.idp_project_mapping_path = str((_REPO_ROOT / mapping_path).resolve())
        return self
    
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

    # 迁移预检（统一导入的幂等键依赖 api_endpoints 唯一约束，迁移未到 head 时必须可见）
    # 默认只告警不阻断，避免既有部署因新检查无法启动；生产可用环境变量设为 true。
    migration_preflight_strict: bool = False
    migration_preflight_enabled: bool = True

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
    # 2026-10-30：30min → 480min（一个工作日）。配合前端 401 统一跳登录，
    # 内部测试平台重登频率与工作流匹配；验签方案落地后另行收紧评估。
    access_token_expire_minutes: int = 480
    #: 作业总超时（秒）：整个脚本作业（下载/预检/运行器）的上限，防无总超时脚本挂起
    job_timeout_seconds: int = 1800

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
    # 对外可分享域名（团队成员/手机访问用）；留空则退化用 minio_endpoint。
    # 例：MINIO_PUBLIC_ENDPOINT=minio.example.com:9000（有反代时写域名:443 不带 scheme）
    minio_public_endpoint: Optional[str] = None
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
    image_parser_model: str = "doubao-seed-2-1-turbo-260628"  # 豆包多模态模型

    # 性能测试工作目录配置
    perf_workspace_root: str = "backend/app/agents/perf/workspace"
    perf_mcp_root: str = "backend/mcp/perf"
    perf_yaml_tests: str = "backend/app/agents/perf/yaml-tests"
    perf_skills_root: str = "backend/app/agents/perf/agent_skills"

    # 接口测试工作目录配置
    api_workspace_root: str = "backend/workspace/api"
    api_mcp_root: str = "backend/mcp/api"
    # 必须与 agent 侧 sources=["/skills/api/"] 匹配，即 <root>/api/<skill>/SKILL.md
    api_skills_root: str = ".claude/skills"

    # Web 测试工作目录配置
    web_mcp_workspace_root: str = "backend/workspace/web_mcp"
    web_mcp_root: str = "backend/mcp/web_mcp"
    # 同上：sources=["/skills/web_mcp/"]
    web_mcp_skills_root: str = ".claude/skills"

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
    # 统一约定（见下方"Skill 维护目录"说明）：根目录一律 .claude/skills，
    # agent 侧以 sources=["/skills/<agent目录>/"] 取自己的 skill。
    testcase_skills_root: str = ".claude/skills"

    # 安全测试工作目录配置
    security_workspace_root: str = "backend/workspace/security"
    security_skills_root: str = ".claude/skills"

    # Android 测试工作目录配置
    android_workspace_root: str = "backend/workspace/android"
    android_skills_root: str = ".claude/skills"

    # iOS 测试工作目录配置
    ios_workspace_root: str = "backend/workspace/ios"
    ios_skills_root: str = ".claude/skills"

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

