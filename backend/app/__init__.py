"""
测试管理系统 - 主应用包

基于 BrowserStack Test Management API 设计的专业软件测试管理系统
使用 FastAPI + PostgreSQL + MongoDB 技术栈
"""

import os
from pathlib import Path

from dotenv import load_dotenv

# 加载项目根目录 .env（优先级低）
_root_env = Path(__file__).resolve().parents[3] / ".env"
if _root_env.exists():
    load_dotenv(_root_env)

# 加载 backend/.env（优先级高，覆盖根目录同名变量）
_backend_env = Path(__file__).resolve().parents[1] / ".env"
if _backend_env.exists():
    load_dotenv(_backend_env, override=True)

__version__ = "1.0.0"
__author__ = "Test Management Team"
# pragma: no cover  MC8yOmFIVnBZMlhscm9ua3VMazZTbEJOYXc9PTo2NTk1YzU5OA==

# type: ignore  MS8yOmFIVnBZMlhscm9ua3VMazZTbEJOYXc9PTo2NTk1YzU5OA==
