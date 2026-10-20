# -*- coding: utf-8 -*-
"""检测「孤儿附件」：数据库有记录、但对象存储中已无对应对象。

背景
----
报告查看接口曾对这种情况返回 500（`处理报告失败: ... The specified key does not exist`），
前端无法区分"报告缺失"与"服务故障"。接口侧已改为返回可读的 404/502，
但**数据本身**仍需可检测——否则这类行会长期静默存在。

用法
----
    # 默认 dry-run：只检测并列出，不做任何修改
    python -m app.scripts.check_orphan_attachments

    # 只看报告类
    python -m app.scripts.check_orphan_attachments --types API_TEST_REPORT,WEB_TEST_REPORT

    # 确认后清理（软删除：仅从数据库移除记录，不动对象存储）
    python -m app.scripts.check_orphan_attachments --delete

注意
----
- 本工具**只读检测 + 显式删除**，默认不会改动任何数据；
- `--delete` 会删除数据库记录，且**不可撤销**；对象存储侧本就没有对象，
  因此删除记录不会丢失内容，但会丢失"曾经生成过该报告"的痕迹，请先确认。
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from collections import Counter

from sqlalchemy import select

from app.config.minio_client import MinIOClient
from app.config.database import async_session_factory
from app.models.attachment import Attachment, AttachmentEntityType


def _type_name(attachment: Attachment) -> str:
    entity_type = attachment.entity_type
    return entity_type.value if hasattr(entity_type, "value") else str(entity_type)


def _classify(object_name: str) -> tuple[str, str]:
    """判定附件对象的可读状态。

    返回 (状态, 说明)，状态取值：

    - ``"ok"``：对象存储中存在；
    - ``"local_only"``：对象存储中缺失，但 ``download_file`` 能从本地
      ``artifacts_backup`` 备份读回（``MinIOClient._try_local_backup``）——**不算孤儿**，
      仍可服务，但已失去对象存储副本；
    - ``"orphan"``：对象存储缺失且本地也无备份，记录已不可用。

    注意：``MinIOClient.file_exists`` 只查对象存储，不查本地备份，
    因此不能单独用它判定孤儿，否则会把"本地可读"的行误判为失效。
    """
    try:
        if MinIOClient.file_exists(object_name):
            return "ok", "对象存储中存在"
    except Exception as exc:  # noqa: BLE001
        # stat 异常（非 NoSuchKey）无法判定，按存在处理，避免误删
        return "ok", f"探测异常按存在处理: {str(exc)[:60]}"

    try:
        data = MinIOClient.download_file(object_name)
    except Exception as exc:  # noqa: BLE001
        return "orphan", f"对象存储与本地备份均不可读: {str(exc)[:60]}"
    return "local_only", f"仅本地备份可读（{len(data)} bytes）"


async def scan(type_filter: list[str] | None, delete: bool) -> int:
    async with async_session_factory() as session:
        stmt = select(Attachment).order_by(Attachment.created_at.desc())
        if type_filter:
            stmt = stmt.where(
                Attachment.entity_type.in_(
                    [AttachmentEntityType[t] for t in type_filter]
                )
            )
        attachments = (await session.execute(stmt)).scalars().all()
        total = len(attachments)
        print(f"待检测附件 = {total} 条" + (f"（类型过滤: {type_filter}）" if type_filter else ""))

        orphans: list[Attachment] = []
        local_only: list[Attachment] = []
        checked = Counter()

        for attachment in attachments:
            entity = _type_name(attachment)
            checked[entity] += 1
            state, detail = await asyncio.to_thread(_classify, attachment.object_name)
            if state == "ok":
                continue
            target = orphans if state == "orphan" else local_only
            target.append(attachment)
            label = "[孤儿]" if state == "orphan" else "[仅本地]"
            print(f"  {label} {entity:20s} {attachment.file_name}")
            print(f"         object={attachment.object_name}")
            print(f"         id={attachment.id}  size={attachment.file_size}  ({detail})")

        print("\n=== 汇总 ===")
        for entity, count in checked.most_common():
            print(f"  {entity:24s} 检测 {count} 条")
        print(f"  对象存储缺失 = {len(orphans) + len(local_only)} / {total}")
        print(f"    其中真孤儿（本地亦无备份，不可用）= {len(orphans)}")
        print(f"    仅本地备份可读（仍可服务）       = {len(local_only)}")

        if not orphans:
            print("\n未发现不可用的孤儿附件。")
            return 0

        if not delete:
            print(
                "\n以上为 dry-run 结果，未做任何修改。"
                "\n确认后可用 --delete 从数据库移除**真孤儿**记录"
                "（对象存储与本地备份均无内容，删除记录不会丢失可读内容）。"
            )
            return 0

        print(f"\n开始删除 {len(orphans)} 条失效记录 …")
        for attachment in orphans:
            await session.delete(attachment)
        await session.commit()
        print(f"已删除 {len(orphans)} 条记录。")
        return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="检测对象存储中已缺失的附件记录（孤儿行）")
    parser.add_argument(
        "--types",
        default="",
        help="逗号分隔的附件类型（如 API_TEST_REPORT,WEB_TEST_REPORT）；留空表示全部",
    )
    parser.add_argument(
        "--delete",
        action="store_true",
        help="确认删除失效记录（默认只检测，不修改数据）",
    )
    args = parser.parse_args(argv)

    type_filter = [t.strip() for t in args.types.split(",") if t.strip()]
    for name in type_filter:
        if name not in AttachmentEntityType.__members__:
            print(f"未知附件类型: {name}", file=sys.stderr)
            return 2

    return asyncio.run(scan(type_filter or None, args.delete))


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
