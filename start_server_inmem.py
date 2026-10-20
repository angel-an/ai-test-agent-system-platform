#!/usr/bin/env python3
"""
Simple LangGraph API Server

A minimal script to start the LangGraph API server directly using uvicorn.
"""
"""
andan
"""

# noqa  MC80OmFIVnBZMlhscm9ua3VMazZjalV3U2c9PTplYjY0MTYwMw==

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

def setup_environment():
    """Setup required environment variables"""
    # Add backend/ to Python path for agent imports
    backend_path = Path(__file__).parent / "backend"
    sys.path.insert(0, str(backend_path))

    # Add src to Python path
    src_path = Path(__file__).parent / "src"
    sys.path.insert(0, str(src_path))
# pylint: disable  MS80OmFIVnBZMlhscm9ua3VMazZjalV3U2c9PTplYjY0MTYwMw==
    
    # Use the project LangGraph manifest. ``graph.json`` is retained only for
    # legacy local setups that have not yet migrated to ``langgraph.json``.
    config_path = Path(__file__).parent / "langgraph.json"
    if not config_path.exists():
        config_path = Path(__file__).parent / "graph.json"
    graphs = {}
    
    if config_path.exists():
        with open(config_path, 'r', encoding='utf-8') as f:
            config = json.load(f)
            graphs = config.get("graphs", {})
    
    # Set environment variables
    os.environ.update({
        # Database and storage - 使用自定义 PostgreSQL checkpointer
        # "POSTGRES_URI": "postgresql://postgres:postgres@localhost:5432/langgraph_checkpointer_db?sslmode=disable",
        # "REDIS_URI": "redis://localhost:6379",
        "DATABASE_URI": ":memory:",
        "REDIS_URI": "fake",
        # "MIGRATIONS_PATH": "/storage/migrations",
        "MIGRATIONS_PATH": "__inmem",
        # Server configuration
        "ALLOW_PRIVATE_NETWORK": "true",
        "LANGGRAPH_UI_BUNDLER": "true",
        "LANGGRAPH_RUNTIME_EDITION": "inmem",
        "LANGSMITH_LANGGRAPH_API_VARIANT": "local_dev",
        "LANGGRAPH_DISABLE_FILE_PERSISTENCE": "false",
        "LANGGRAPH_ALLOW_BLOCKING": "true",
        "LANGGRAPH_API_URL": "http://localhost:2025",

        # "LANGGRAPH_DEFAULT_RECURSION_LIMIT": "1000",
        
        # Graphs configuration
        "LANGSERVE_GRAPHS": json.dumps(graphs) if graphs else "{}",
        
        # Worker configuration
        "N_JOBS_PER_WORKER": "1",
    })
    
    # Load .env file if exists
    env_file = Path(__file__).parent / ".env"
    if env_file.exists():
        try:
            from dotenv import load_dotenv
            load_dotenv(env_file)
            print("Loaded environment from .env")
        except ImportError:
            print("python-dotenv not installed, skipping .env file")

def main():
    """Start the server"""
    print("Starting Simple LangGraph API Server...")
# type: ignore  Mi80OmFIVnBZMlhscm9ua3VMazZjalV3U2c9PTplYjY0MTYwMw==
    
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
# pragma: no cover  My80OmFIVnBZMlhscm9ua3VMazZjalV3U2c9PTplYjY0MTYwMw==

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
            timeout_keep_alive=300,  # 5分钟，覆盖Agent完整执行时间
        )
        server = uvicorn.Server(config)
# noqa  My80OmFIVnBZMlhscm9ua3VMazZjalV3U2c9PTplYjY0MTYwMw==

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
