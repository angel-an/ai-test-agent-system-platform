# -*- coding: utf-8 -*-
"""把 api_endpoints.security 的 dict 形状归一化为 list。

背景：统一导入曾把 ``{"type": "mx_md5", ...}`` 整体写入 security 字段，
而 ``APIEndpointResponse.security`` 是 ``list[dict] | None``，导致
``GET /projects/{id}/api-endpoints`` 整表响应校验失败 → 500。

schema 侧已加归一化校验器保证可读；本脚本把存量数据改成正确形状，
避免同一接口返回两种形状（读出来是 list，库里是 dict）。

用法::

    cd backend
    ..\\.venv\\Scripts\\python.exe -X utf8 scripts\\normalize_endpoint_security.py            # 仅统计
    ..\\.venv\\Scripts\\python.exe -X utf8 scripts\\normalize_endpoint_security.py --apply    # 执行
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

import asyncpg

BACKEND = Path(__file__).resolve().parents[1]
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from app.config.settings import settings  # noqa: E402


def _dsn() -> str:
    url = settings.postgres_sync_url
    return url.replace("postgresql+asyncpg://", "postgresql://")


async def main() -> int:
    parser = argparse.ArgumentParser(description="归一化 api_endpoints.security 形状")
    parser.add_argument("--apply", action="store_true", help="实际执行更新（默认只统计）")
    args = parser.parse_args()

    conn = await asyncpg.connect(_dsn())
    try:
        total = await conn.fetchval("SELECT count(*) FROM api_endpoints")
        # 注意：JSONB 里存的是 JSON null（jsonb_typeof = 'null'）而非 SQL NULL，
        # 它们不会触发响应校验失败，不应被改写。只处理真正的 object。
        dict_rows = await conn.fetch(
            "SELECT id, method, path, jsonb_typeof(security) AS kind "
            "FROM api_endpoints "
            "WHERE security IS NOT NULL AND jsonb_typeof(security) = 'object'"
        )
        json_null = await conn.fetchval(
            "SELECT count(*) FROM api_endpoints "
            "WHERE security IS NOT NULL AND jsonb_typeof(security) = 'null'"
        )
        print(f"端点总数: {total}")
        print(f"security 为 JSON null 的行数（无需处理）: {json_null}")
        print(f"security 为 object 的行数（需归一化）: {len(dict_rows)}")
        for row in dict_rows[:10]:
            print(f"  - {row['method']} {row['path']} ({row['kind']})")

        if not dict_rows:
            print("无需处理。")
            return 0
        if not args.apply:
            print("（未加 --apply，未执行更新）")
            return 0

        result = await conn.execute(
            "UPDATE api_endpoints SET security = jsonb_build_array(security) "
            "WHERE security IS NOT NULL AND jsonb_typeof(security) = 'object'"
        )
        print(f"已更新: {result}")

        remaining = await conn.fetchval(
            "SELECT count(*) FROM api_endpoints "
            "WHERE security IS NOT NULL AND jsonb_typeof(security) = 'object'"
        )
        print(f"剩余 object 行数: {remaining}")
        return 0 if remaining == 0 else 1
    finally:
        await conn.close()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
