"""清理历史遗留的空路径契约（幂等键不可用）与其关联端点。

背景：首次 confirm 冒烟时（守卫上线前）写入了一个 normalized_path='' 的契约，
并创建了对应端点。这类记录无法参与 (project, method, normalized_path) 幂等匹配，
会持续触发唯一约束冲突，需要清理。

用法：
    python scripts/cleanup_empty_path_contracts.py           # 仅预览
    python scripts/cleanup_empty_path_contracts.py --apply   # 执行删除
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))


async def main(apply: bool) -> int:
    from sqlalchemy import text

    from app.config.database import async_session_factory
    from app.services.api_bundle_service import normalize_import_path

    async with async_session_factory() as session:
        # 1) 既有端点：normalized_path 为空但 path 有效 → 回填（参与幂等匹配）
        backfill_rows = (await session.execute(text(
            "SELECT id, project_id, method, path FROM api_endpoints "
            "WHERE (normalized_path IS NULL OR normalized_path = '') AND path <> ''"
        ))).fetchall()

        # 2) 真正无路径的端点 → 删除
        dead_rows = (await session.execute(text(
            "SELECT id, method, path, display_name FROM api_endpoints "
            "WHERE (normalized_path IS NULL OR normalized_path = '') AND (path IS NULL OR path = '')"
        ))).fetchall()

        # 3) 空路径契约 → 删除（无法构成幂等键）
        contract_rows = (await session.execute(text(
            "SELECT id, method, path, name FROM api_interface_contracts "
            "WHERE normalized_path IS NULL OR normalized_path = ''"
        ))).fetchall()

        print(f"可回填端点数: {len(backfill_rows)}")
        for r in backfill_rows:
            print(f"  {r[0]} | {r[2]} {r[3]!r} -> {normalize_import_path(r[3])!r}")
        print(f"无路径端点（待删除）: {len(dead_rows)}")
        for e in dead_rows:
            print(f"  {e[0]} | {e[1]} {e[2]!r} | {e[3]}")
        print(f"空路径契约（待删除）: {len(contract_rows)}")
        for c in contract_rows:
            print(f"  {c[0]} | {c[1]} {c[2]!r} | {c[3]}")

        if not apply:
            print("\n（预览模式，未做修改；加 --apply 执行）")
            return 0

        # ---- 回填 ----
        for row in backfill_rows:
            normalized = normalize_import_path(row[3])
            if not normalized:
                continue
            # 冲突保护：同项目+method+normalized 已存在则跳过并提示
            dup = (await session.execute(text(
                "SELECT id FROM api_endpoints WHERE project_id = :p AND method = :m "
                "AND normalized_path = :n AND id <> :i LIMIT 1"
            ), {"p": row[1], "m": row[2], "n": normalized, "i": row[0]})).fetchone()
            if dup:
                print(f"  跳过回填（已存在同键端点 {dup[0]}）: {row[0]}")
                continue
            await session.execute(text(
                "UPDATE api_endpoints SET normalized_path = :n WHERE id = :i"
            ), {"n": normalized, "i": row[0]})

        # ---- 删除无路径端点 ----
        dead_ids = [e[0] for e in dead_rows]
        if dead_ids:
            await session.execute(text(
                "DELETE FROM api_endpoints WHERE id = ANY(:ids)"
            ), {"ids": dead_ids})

        # ---- 删除空路径契约及其挂接对象 ----
        if contract_rows:
            await session.execute(text(
                "DELETE FROM api_dependencies WHERE from_contract_id IN "
                "(SELECT id FROM api_interface_contracts WHERE normalized_path IS NULL OR normalized_path = '') "
                "OR to_contract_id IN "
                "(SELECT id FROM api_interface_contracts WHERE normalized_path IS NULL OR normalized_path = '')"
            ))
            await session.execute(text(
                "DELETE FROM api_assertions WHERE contract_id IN "
                "(SELECT id FROM api_interface_contracts WHERE normalized_path IS NULL OR normalized_path = '')"
            ))
            await session.execute(text(
                "DELETE FROM api_request_templates WHERE contract_id IN "
                "(SELECT id FROM api_interface_contracts WHERE normalized_path IS NULL OR normalized_path = '')"
            ))
            await session.execute(text(
                "DELETE FROM api_conflicts WHERE contract_id IN "
                "(SELECT id FROM api_interface_contracts WHERE normalized_path IS NULL OR normalized_path = '')"
            ))
            await session.execute(text(
                "DELETE FROM api_interface_contracts WHERE normalized_path IS NULL OR normalized_path = ''"
            ))

        await session.commit()
        print(
            f"\n完成：回填 {len(backfill_rows)} 个端点，"
            f"删除 {len(dead_ids)} 个无路径端点、{len(contract_rows)} 个空路径契约"
        )
        return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    raise SystemExit(asyncio.run(main(args.apply)))
