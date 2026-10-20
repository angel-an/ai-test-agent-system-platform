"""Run the FastAPI service with the repository's local PostgreSQL settings."""

from __future__ import annotations

import os
import sys
from pathlib import Path
from urllib.parse import unquote, urlparse

from dotenv import load_dotenv


ROOT = Path(__file__).resolve().parents[1]
load_dotenv(ROOT / ".env")

uri = os.environ.get("POSTGRES_URI") or os.environ.get("DATABASE_URI")
if not uri:
    raise RuntimeError("未配置 POSTGRES_URI 或 DATABASE_URI")

parsed = urlparse(uri)
if not parsed.hostname or not parsed.path.strip("/"):
    raise RuntimeError("POSTGRES_URI 格式无效")

os.environ["POSTGRES_HOST"] = parsed.hostname
os.environ["POSTGRES_PORT"] = str(parsed.port or 5432)
os.environ["POSTGRES_USER"] = unquote(parsed.username or "postgres")
os.environ["POSTGRES_PASSWORD"] = unquote(parsed.password or "")
os.environ["POSTGRES_DB"] = parsed.path.strip("/")

sys.path.insert(0, str(ROOT / "backend"))

import uvicorn

uvicorn.run("app.main:app", host="127.0.0.1", port=8000, reload=False)
