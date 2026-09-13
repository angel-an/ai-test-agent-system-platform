# -*- coding: utf-8 -*-
"""存量端点 custom_config 回填：补齐 P1 生成器需要的 auth / source_location。

背景（P0→P1 桥接缝隙的存量部分）
--------------------------------
统一导入创建 APIEndpoint 时，早期版本只写 ``import_source / bundle_id / contract_id``：

1. 缺 ``auth`` → 契约生成的 ``_endpoint_to_spec`` 得到 ``auth=None``，
   生成器直接抛"当前脚本生成仅支持 mx_md5 鉴权"；
2. ``source_location`` 写成了契约侧 ``source_refs`` 的 **list** 形状，
   而生成器按 **dict** 消费，抛 ``AttributeError: 'list' object has no attribute 'get'``。

新建端点已修复，但 ``confirm`` 的幂等键是 (项目, method, normalized_path)：
**既有端点会被复用、不会重写 custom_config**，因此存量数据必须单独回填。

回填来源：``api_interface_contracts``（按 ``imported_endpoint_id`` 精确关联，
不依赖路径二次匹配，避免误改）。

用法::

    cd backend
    ..\\.venv\\Scripts\\python.exe -X utf8 scripts\\backfill_endpoint_contract_config.py            # 仅报告
    ..\\.venv\\Scripts\\python.exe -X utf8 scripts\\backfill_endpoint_contract_config.py --apply    # 执行

回滚（--apply 时自动快照）：::

    UPDATE api_endpoints e
       SET custom_config = b.custom_config
      FROM _endpoint_custom_config_backup b
     WHERE e.id = b.endpoint_id;
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import asyncpg

BACKEND = Path(__file__).resolve().parents[1]
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from app.config.settings import settings  # noqa: E402
from app.services.api_bundle_service import _contract_source_location  # noqa: E402


def _dsn() -> str:
    return settings.postgres_sync_url.replace("postgresql+asyncpg://", "postgresql://")


@dataclass
class BackfillStats:
    scanned: int = 0
    need_auth: int = 0
    need_source_location: int = 0
    need_api_name: int = 0
    updated: int = 0
    collection_only: int = 0
    skipped_no_contract: int = 0
    examples: list[str] = field(default_factory=list)


class _ContractView:
    """供 `_contract_source_location` 消费的最小契约视图。"""

    def __init__(self, source_refs: Any) -> None:
        self.source_refs = source_refs


def _normalize_source_location(contract: _ContractView) -> dict[str, Any] | None:
    return _contract_source_location(contract)


async def main() -> int:
    parser = argparse.ArgumentParser(description="回填存量端点的 custom_config")
    parser.add_argument("--apply", action="store_true", help="实际写入（默认仅报告）")
    args = parser.parse_args()

    conn = await asyncpg.connect(_dsn())
    stats = BackfillStats()
    try:
        rows = await conn.fetch(
            """
            SELECT e.id            AS endpoint_id,
                   e.method        AS method,
                   e.path          AS path,
                   e.custom_config AS custom_config,
                   c.id            AS contract_id,
                   c.auth          AS auth,
                   c.name          AS contract_name,
                   c.endpoint_key  AS endpoint_key,
                   c.source_refs   AS source_refs,
                   c.pending_confirmations AS pending
            FROM api_endpoints e
            LEFT JOIN api_interface_contracts c ON c.imported_endpoint_id = e.id
            WHERE e.custom_config ->> 'import_source' = 'unified_import'
            ORDER BY e.method, e.path
            """
        )
        print(f"统一导入端点总数: {len(rows)}")

        updates: list[tuple[dict[str, Any], Any]] = []

        for row in rows:
            stats.scanned += 1
            raw_cc = row["custom_config"]
            if isinstance(raw_cc, str):
                raw_cc = json.loads(raw_cc) if raw_cc else {}
            cc = dict(raw_cc or {})
            contract_id = row["contract_id"]

            if contract_id is None:
                stats.skipped_no_contract += 1
                continue

            auth = row["auth"]
            if isinstance(auth, str):
                auth = json.loads(auth) if auth else None
            raw_refs = row["source_refs"]
            if isinstance(raw_refs, str):
                raw_refs = json.loads(raw_refs) if raw_refs else None
            raw_pending = row["pending"]
            if isinstance(raw_pending, str):
                raw_pending = json.loads(raw_pending) if raw_pending else None
            if not auth:
                # Collection-only 契约（无 Excel 导读）本来就没有鉴权配置，
                # 这类端点无法驱动 mx_md5 脚本生成，属预期而非缺陷。
                stats.collection_only += 1
                continue

            changed = False

            if not cc.get("auth"):
                cc["auth"] = auth
                stats.need_auth += 1
                changed = True

            if not cc.get("api_name"):
                cc["api_name"] = row["contract_name"] or row["endpoint_key"]
                stats.need_api_name += 1
                changed = True

            if not isinstance(cc.get("api_name"), str) or not cc.get("api_name"):
                cc["api_name"] = row["contract_name"] or row["endpoint_key"]
                changed = True

            if not cc.get("method_name"):
                cc["method_name"] = row["endpoint_key"]
                changed = True

            source_location = _normalize_source_location(_ContractView(raw_refs))
            if cc.get("source_location") != source_location:
                # 旧数据是 list 形状（或缺失）→ 归一化为生成器可消费的 dict
                if not isinstance(cc.get("source_location"), dict):
                    stats.need_source_location += 1
                cc["source_location"] = source_location
                changed = True

            if cc.get("pending_confirmations") != (raw_pending or []):
                cc["pending_confirmations"] = raw_pending or []
                changed = True

            if changed:
                if len(stats.examples) < 5:
                    stats.examples.append(f"{row['method']} {row['path'][:48]}")
                updates.append((cc, row["endpoint_id"]))

        print(f"  Collection-only（无鉴权，跳过）: {stats.collection_only}")
        print(f"  无关联契约（跳过）: {stats.skipped_no_contract}")
        print(f"  缺少 auth: {stats.need_auth}")
        print(f"  缺少 api_name: {stats.need_api_name}")
        print(f"  source_location 形状不正确: {stats.need_source_location}")
        print(f"  需要回填的端点数: {len(updates)}")
        for example in stats.examples:
            print(f"    - {example}")

        if not updates:
            print("无需回填。")
            return 0
        if not args.apply:
            print("（未加 --apply，未执行写入）")
            return 0

        async with conn.transaction():
            # 可回滚备份：写前快照 (id, custom_config)，便于一键还原
            await conn.execute(
                "CREATE TABLE IF NOT EXISTS _endpoint_custom_config_backup ("
                "  endpoint_id uuid PRIMARY KEY,"
                "  custom_config jsonb,"
                "  backed_up_at timestamptz NOT NULL DEFAULT now()"
                ")"
            )
            await conn.execute(
                "INSERT INTO _endpoint_custom_config_backup (endpoint_id, custom_config) "
                "SELECT id, custom_config FROM api_endpoints WHERE id = ANY($1::uuid[]) "
                "ON CONFLICT (endpoint_id) DO UPDATE SET "
                "  custom_config = EXCLUDED.custom_config, backed_up_at = now()",
                [endpoint_id for _, endpoint_id in updates],
            )
            for cc, endpoint_id in updates:
                await conn.execute(
                    "UPDATE api_endpoints SET custom_config = $1::jsonb WHERE id = $2",
                    json.dumps(cc, ensure_ascii=False),
                    endpoint_id,
                )
        stats.updated = len(updates)
        print(f"已回填: {stats.updated} 个端点")
        print("备份已写入 _endpoint_custom_config_backup（回滚：见脚本尾部 SQL 注释）")
        return 0
    finally:
        await conn.close()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
