"""
一次性修复 total_test_cases 累加膨胀的历史数据。

背景:
- save_web_test_cases / save_test_cases 之前用 `+= len(test_cases)` 累加计数,
  但底层 JSON 是按固定 object_name 全量覆盖的,重复生成会让计数无限膨胀。
- 例如: 前端出现 WF-9B15 显示 49 条,但真实 JSON 里只有几条。

本脚本:
- 遍历所有 WebSubFunction / APIEndpoint,
- 读取其对应的 WEB_TEST_CASE / API_TEST_CASE 附件 JSON,
- 用 JSON 中的真实数组长度回填 total_test_cases。
- 找不到附件 / 解析失败的记录会打印警告并跳过。

用法(在 backend/ 目录下):
    python recount_test_cases.py            # 干跑,只打印将要修改的记录
    python recount_test_cases.py --apply    # 真正写库
"""

import asyncio
import json
import sys
from uuid import UUID

from sqlalchemy import select

from app.config.database import async_session_factory
from app.config.minio_client import MinIOClient
from app.models.attachment import Attachment, AttachmentEntityType
from app.models.web_function import WebSubFunction
from app.models.api_endpoint import APIEndpoint


async def _read_case_count(object_name: str) -> int | None:
    """从 MinIO/本地备份读取 JSON 并返回数组长度。失败返回 None。"""
    try:
        data = MinIOClient.download_file(object_name)
        payload = json.loads(data.decode("utf-8"))
        if isinstance(payload, list):
            return len(payload)
        if isinstance(payload, dict):
            for key in ("test_cases", "cases", "items"):
                if isinstance(payload.get(key), list):
                    return len(payload[key])
        return None
    except Exception as e:
        print(f"  ! 读取失败 object_name={object_name}: {e}")
        return None


async def _recount(session, model, entity_type: AttachmentEntityType, label: str, apply: bool):
    stmt = select(model)
    result = await session.execute(stmt)
    entities = list(result.scalars().all())
    print(f"\n== {label}: 共 {len(entities)} 条 ==")

    changed = 0
    skipped = 0
    for ent in entities:
        att_stmt = (
            select(Attachment)
            .where(Attachment.entity_id == ent.id, Attachment.entity_type == entity_type)
            .order_by(Attachment.updated_at.desc())
            .limit(1)
        )
        att = (await session.execute(att_stmt)).scalar_one_or_none()

        old = ent.total_test_cases or 0
        if not att:
            if old != 0:
                print(f"  [{ent.identifier}] 无附件,当前计数={old} → 建议置 0")
                if apply:
                    ent.total_test_cases = 0
                    changed += 1
            else:
                skipped += 1
            continue

        real = await _read_case_count(att.object_name)
        if real is None:
            print(f"  [{ent.identifier}] 附件存在但解析失败({att.object_name}),保留原值 {old}")
            skipped += 1
            continue

        if real == old:
            skipped += 1
            continue

        print(f"  [{ent.identifier}] {old} → {real}")
        if apply:
            ent.total_test_cases = real
            changed += 1

    if apply:
        await session.commit()
        print(f"  已提交 {changed} 条,跳过 {skipped} 条")
    else:
        print(f"  [DRY-RUN] 待修改 {sum(1 for _ in entities) - skipped} 条,跳过 {skipped} 条")


async def main(apply: bool):
    async with async_session_factory() as session:
        await _recount(session, WebSubFunction, AttachmentEntityType.WEB_TEST_CASE, "WebSubFunction", apply)
        await _recount(session, APIEndpoint, AttachmentEntityType.API_TEST_CASE, "APIEndpoint", apply)
    print("\n完成。")


if __name__ == "__main__":
    apply = "--apply" in sys.argv
    if not apply:
        print("[DRY-RUN] 未加 --apply,只打印差异。加 --apply 才会真正写库。")
    asyncio.run(main(apply))
