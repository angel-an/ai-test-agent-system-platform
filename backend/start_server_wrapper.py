"""
Wrapper to start LangGraph API server without the local top-level
`langgraph_api` directory shadowing the installed package.
"""
import os
import sys
import json
from pathlib import Path
import uvicorn

# 计算项目根目录
ROOT = Path(__file__).resolve().parent.parent

# 切换到项目根目录，使 graph.json 中的相对路径能正确解析
os.chdir(ROOT)

# 添加 core/ 目录到 sys.path，加载本地 langgraph 覆盖模块
core_path = ROOT / "core"
if str(core_path) not in sys.path:
    sys.path.insert(0, str(core_path))

# 添加 backend/ 目录到 sys.path，使 agent 模块可以正确导入
backend_path = ROOT / "backend"
if str(backend_path) not in sys.path:
    sys.path.insert(0, str(backend_path))

# Load graphs
config_path = ROOT / "backend" / "graph.json"
graphs = {}
if config_path.exists():
    graphs = json.loads(config_path.read_text(encoding="utf-8")).get("graphs", {})

os.environ.update({
    "DATABASE_URI": ":memory:",
    "REDIS_URI": "fake",
    "MIGRATIONS_PATH": "__inmem",
    "ALLOW_PRIVATE_NETWORK": "true",
    "LANGGRAPH_UI_BUNDLER": "true",
    "LANGGRAPH_RUNTIME_EDITION": "inmem",
    "LANGSMITH_LANGGRAPH_API_VARIANT": "local_dev",
    "LANGGRAPH_DISABLE_FILE_PERSISTENCE": "false",
    "LANGGRAPH_ALLOW_BLOCKING": "true",
    "LANGGRAPH_API_URL": "http://localhost:2025",
    "LANGSERVE_GRAPHS": json.dumps(graphs),
    "N_JOBS_PER_WORKER": "1",
})

env_file = ROOT / ".env"
if env_file.exists():
    try:
        from dotenv import load_dotenv
        load_dotenv(env_file)
        print("Loaded environment from .env")
    except ImportError:
        pass

print("Starting LangGraph API server on http://localhost:2026")
uvicorn.run(
    "langgraph_api.server:app",
    host="0.0.0.0",
    port=2026,
    reload=False,
    access_log=False,
)
