"""
Checklist 回归仓库

行级 upsert（row_id 锚）、执行结果写入与查询。
判据纪律：写后必须可回读逐字比对；行内容以 content_md5 判变更，走 update 不走 delete+insert。
"""

from datetime import datetime, timezone
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.checklist_row import ChecklistRow, ChecklistRun
from app.models.project import Project


async def get_project(session: AsyncSession, project_identifier: str) -> Project:
    result = await session.execute(select(Project).where(Project.identifier == project_identifier))
    project = result.scalar_one_or_none()
    if not project:
        raise ValueError(f"项目不存在: {project_identifier}")
    return project


async def upsert_rows(session: AsyncSession, project_id: UUID, rows: list[dict]) -> dict:
    """全量 upsert：存在且 content_md5 一致 → skipped；存在但变更 → updated；不存在 → created。"""
    result = await session.execute(
        select(ChecklistRow).where(ChecklistRow.project_id == project_id)
    )
    existing = {r.row_id: r for r in result.scalars()}
    counts = {"created": 0, "updated": 0, "skipped": 0}
    now = datetime.now(timezone.utc)
    for row in rows:
        rid = row["row_id"]
        cur = existing.get(rid)
        if cur is None:
            session.add(
                ChecklistRow(
                    id=uuid4(),
                    project_id=project_id,
                    row_id=rid,
                    source_row=row.get("source_row"),
                    title=row.get("title", ""),
                    main_module=row.get("main_module", ""),
                    sub_module=row.get("sub_module", ""),
                    feature=row.get("feature", ""),
                    expected=row.get("expected", ""),
                    priority=row.get("priority", ""),
                    probe_kind=row.get("probe_kind", "reachable"),
                    data_mode=row.get("data_mode", "readonly"),
                    content_md5=row.get("content_md5", ""),
                    extra=row.get("extra"),
                )
            )
            counts["created"] += 1
        else:
            if cur.content_md5 == row.get("content_md5", ""):
                counts["skipped"] += 1
                # 行号快照仍需刷新（表格行可能移动）
                cur.source_row = row.get("source_row", cur.source_row)
            else:
                cur.title = row.get("title", cur.title)
                cur.main_module = row.get("main_module", cur.main_module)
                cur.sub_module = row.get("sub_module", cur.sub_module)
                cur.feature = row.get("feature", cur.feature)
                cur.expected = row.get("expected", cur.expected)
                cur.priority = row.get("priority", cur.priority)
                cur.probe_kind = row.get("probe_kind", cur.probe_kind)
                cur.data_mode = row.get("data_mode", cur.data_mode)
                cur.content_md5 = row.get("content_md5", cur.content_md5)
                cur.extra = row.get("extra", cur.extra)
                cur.updated_at = now
                counts["updated"] += 1
    await session.commit()
    return counts


async def list_rows(session: AsyncSession, project_id: UUID) -> list[ChecklistRow]:
    result = await session.execute(
        select(ChecklistRow)
        .where(ChecklistRow.project_id == project_id)
        .order_by(ChecklistRow.source_row.nulls_first(), ChecklistRow.row_id)
    )
    return list(result.scalars())


async def save_run_result(
    session: AsyncSession,
    project_id: UUID,
    payload: dict,
) -> ChecklistRun:
    """落一次执行的整包结果，并回填行级最新状态。results.results 元素须含 row_id。"""
    run = ChecklistRun(
        id=uuid4(),
        project_id=project_id,
        run_verdict=payload.get("run_verdict", "empty"),
        profile_name=payload.get("profile", ""),
        summary=payload.get("summary"),
        skipped_flow=payload.get("skipped_flow", 0),
        report_url=payload.get("report_url"),
        results=payload,
    )
    session.add(run)
    await session.flush()

    result = await session.execute(
        select(ChecklistRow).where(ChecklistRow.project_id == project_id)
    )
    by_rid = {r.row_id: r for r in result.scalars()}
    now = datetime.now(timezone.utc)
    touched = 0
    for item in payload.get("results", []):
        rid = item.get("row_id")
        row = by_rid.get(rid)
        if not row:
            continue
        row.latest_status = item.get("status")
        row.latest_error = (item.get("error") or None)
        row.latest_evidence = item.get("evidence") or None
        row.latest_consistency = item.get("consistency") or None
        row.latest_run_id = run.id
        row.latest_run_at = now
        touched += 1
    await session.commit()
    run.touch_count = touched  # 非持久字段，仅供响应
    return run


async def list_runs(session: AsyncSession, project_id: UUID, limit: int = 20) -> list[ChecklistRun]:
    result = await session.execute(
        select(ChecklistRun)
        .where(ChecklistRun.project_id == project_id)
        .order_by(ChecklistRun.created_at.desc())
        .limit(limit)
    )
    return list(result.scalars())


async def get_run(session: AsyncSession, project_id: UUID, run_id: UUID) -> ChecklistRun | None:
    result = await session.execute(
        select(ChecklistRun).where(
            ChecklistRun.project_id == project_id, ChecklistRun.id == run_id
        )
    )
    return result.scalar_one_or_none()
