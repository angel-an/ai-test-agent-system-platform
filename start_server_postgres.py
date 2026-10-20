#!/usr/bin/env python3
"""
Simple LangGraph API Server

A minimal script to start the LangGraph API server directly using uvicorn.
modified according to cli.py under LangGraph API
"""
"""
andan
"""

# pylint: disable  MC80OmFIVnBZMlhscm9ua3VMazZhbEF3VVE9PTo2YmM2ZGYxZA==

# ========== FIX: Windows 事件循环兼容 — 必须在任何其他导入之前执行 ==========
# Windows 默认的 ProactorEventLoop 与 psycopg 异步模式、Redis Pub/Sub 及 SSE
# 流式传输存在兼容性缺陷，长时间运行后会导致 [Errno 22] Invalid argument。
# 必须在导入任何可能创建事件循环的模块（如 asyncio、uvicorn、psycopg）之前
# 强制设置全局策略，确保主线程和子线程都使用 SelectorEventLoop。
import sys
if sys.platform == "win32":
    import asyncio
    import selectors
    # 先设置策略，防止后续代码创建新循环时回退到 ProactorEventLoop
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    # 再显式创建并设置当前循环，确保 uvicorn 启动前循环已就绪
    selector = selectors.SelectSelector()
    loop = asyncio.SelectorEventLoop(selector)
    asyncio.set_event_loop(loop)
# =============================================================================

import os
import json
from pathlib import Path

# ========== 必须在任何 langgraph 模块导入前加载 .env ==========
# 先加载 .env，让后续所有模块（包括 langgraph_api.config）能读到环境变量
env_file = Path(__file__).parent / ".env"
if env_file.exists():
    try:
        from dotenv import load_dotenv
        load_dotenv(env_file)
    except ImportError:
        pass

# 从环境变量读取数据库 / Redis 连接信息
postgres_uri = os.environ.get("POSTGRES_URI") or os.environ.get("DATABASE_URI")
redis_uri = os.environ.get("REDIS_URI")
migrations_path = os.environ.get("MIGRATIONS_PATH", "./storage/migrations")

# 提前设置到 os.environ，确保 langgraph_api.config 导入时能读到
if postgres_uri:
    os.environ["DATABASE_URI"] = postgres_uri
if redis_uri:
    os.environ["REDIS_URI"] = redis_uri
if migrations_path:
    os.environ["MIGRATIONS_PATH"] = migrations_path
# =============================================================

def setup_environment():
    """Setup required environment variables"""
    # Add core/ to Python path for local langgraph overrides
    core_path = Path(__file__).parent / "core"
    sys.path.insert(0, str(core_path))

    # Add backend/ to Python path for agent imports
    backend_path = Path(__file__).parent / "backend"
    sys.path.insert(0, str(backend_path))

    # Add src to Python path
    src_path = Path(__file__).parent / "src"
    sys.path.insert(0, str(src_path))

    # .env 已在文件顶部加载，这里只需确认关键变量存在
    # 从环境变量读取数据库 / Redis 连接信息
    # 兼容两种命名：POSTGRES_URI / DATABASE_URI
    postgres_uri = os.environ.get("POSTGRES_URI") or os.environ.get("DATABASE_URI")
    redis_uri = os.environ.get("REDIS_URI")
    migrations_path = os.environ.get("MIGRATIONS_PATH", "./storage/migrations")
# fmt: off  MS80OmFIVnBZMlhscm9ua3VMazZhbEF3VVE9PTo2YmM2ZGYxZA==

    missing = []
    if not postgres_uri:
        missing.append("POSTGRES_URI (或 DATABASE_URI)")
    if not redis_uri:
        missing.append("REDIS_URI")
    if missing:
        print(f"❌ 缺少必要的环境变量，请在 .env 中配置: {', '.join(missing)}")
        sys.exit(1)

    # Load config from langgraph.json
    config_path = Path(__file__).parent / "langgraph.json"
    graphs = {}
    auth = None

    if config_path.exists():
        with open(config_path, 'r', encoding='utf-8') as f:
            config = json.load(f)
            graphs = config.get("graphs", {})
            auth = config.get("auth")  # 读取 auth 配置

    # Build environment variables dict
    env_vars = {
        # Database and storage - 从 .env 读取
        "POSTGRES_URI": postgres_uri,
        "DATABASE_URI": postgres_uri,
        "REDIS_URI": redis_uri,
        "MIGRATIONS_PATH": migrations_path,
        # 本地 in-memory 无数据库持久化时
        #"DATABASE_URI": ":memory:",
        #"REDIS_URI": "fake",
        #"MIGRATIONS_PATH": "__inmem",

        # Server configuration
        "ALLOW_PRIVATE_NETWORK": "true",
        "LANGGRAPH_UI_BUNDLER": "true",
        "LANGGRAPH_RUNTIME_EDITION": "postgres",
        "LANGSMITH_LANGGRAPH_API_VARIANT": "local_dev",
        "LANGGRAPH_DISABLE_FILE_PERSISTENCE": "false",
        "LANGGRAPH_ALLOW_BLOCKING": "true",
        "LANGGRAPH_API_URL": "http://localhost:2026",

        "LANGGRAPH_DEFAULT_RECURSION_LIMIT": "2000",

        # Graphs configuration
        "LANGSERVE_GRAPHS": json.dumps(graphs) if graphs else "{}",

        # Auth configuration - 从 langgraph.json 读取
        "LANGGRAPH_AUTH": json.dumps(auth) if auth else None,

        # Worker configuration
        "N_JOBS_PER_WORKER": "10",
    }

    # 过滤掉 None 值，然后设置环境变量
    os.environ.update({k: v for k, v in env_vars.items() if v is not None})

def main():
    """Start the server"""
    print("Starting Simple LangGraph API Server...")
    
    # Setup environment
    setup_environment()
    
    # Print server information
    print("\n" + "="*60)
    print("Server URL: http://localhost:2026")
    print("API Documentation: http://localhost:2026/docs")
    print("Studio UI: http://localhost:2026/ui")
    print("Health Check: http://localhost:2026/ok")
    print("="*60)
    
    try:
        # Import uvicorn after environment setup
        import uvicorn
# type: ignore  Mi80OmFIVnBZMlhscm9ua3VMazZhbEF3VVE9PTo2YmM2ZGYxZA==

        log_config = {
            "version": 1,
            "disable_existing_loggers": False,
            "formatters": {
                "default": {
                    "format": "%(asctime)s - %(name)s - %(levelname)s - %(message)s",
                }
            },
            "handlers": {
                "default": {
                    "formatter": "default",
                    "class": "logging.StreamHandler",
                    "stream": "ext://sys.stdout",
                }
            },
            "root": {
                "level": "INFO",
                "handlers": ["default"],
            },
            "loggers": {
                "uvicorn": {"level": "INFO"},
                "uvicorn.error": {"level": "INFO"},
                "uvicorn.access": {"level": "WARNING"},
            }
        }

        # 用 Config + Server + asyncio.run(loop_factory=...) 显式控制事件循环，
        # 避免 uvicorn.run 在 Windows 上把循环重置回 ProactorEventLoop
        config = uvicorn.Config(
"langgraph_api.server:app",
            host="0.0.0.0",
            port=2026,
            reload=False,
            access_log=False,
            loop="asyncio",
            log_config=log_config,
            # SSE 流式连接需要长超时，禁用 HTTP keep-alive 超时限制
            timeout_keep_alive=300,  # 5分钟，覆盖Agent完整执行时间（生成计划+用例+脚本+执行）
        )
        server = uvicorn.Server(config)
# noqa  My80OmFIVnBZMlhscm9ua3VMazZhbEF3VVE9PTo2YmM2ZGYxZA==

        if sys.platform == "win32":
            import selectors

            def _selector_loop_factory():
                return asyncio.SelectorEventLoop(selectors.SelectSelector())

            asyncio.run(server.serve(), loop_factory=_selector_loop_factory)
        else:
            asyncio.run(server.serve())
    except KeyboardInterrupt:
        print("\nServer stopped by user")
    except Exception as e:
        print(f"❌ Server failed to start: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)

if __name__ == "__main__":
    main()
