"""测试根 conftest：加载 .env 环境变量。"""

from __future__ import annotations

import os
from pathlib import Path


def _load_env() -> None:
    try:
        from dotenv import load_dotenv
    except ImportError:
        return
    backend_dir = Path(__file__).parent.parent
    env_file = backend_dir / ".env"
    if env_file.exists():
        load_dotenv(env_file)


_load_env()
