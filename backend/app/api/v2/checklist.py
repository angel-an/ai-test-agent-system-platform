"""Checklist 回归路由。

行导入（幂等 upsert）、行查询、执行结果落库与查询、探针证据上传/下载。
前端「上线Checklist」页与 checklist-sync / miniprogram-probe-run 两个 skill 的数据契约。

证据约定（2026-10-28 P0 落地，替代旧的探针侧相对路径）：
- 探针跑完后把每个证据文件 POST 到 /evidence（multipart，字段名 file），
  平台转存对象存储，返回 object_name（形如 checklist-evidence/{项目}/{hash}.png）；
- 探针再把返回的 object_name 填进 run-results 各项的 evidence 字段；
- 前端凭证列对 object_name 走 /evidence-file 代理下载，链接永不过期。
旧 run 里的相对路径（probe-runs\\...）无文件可读，页面只展示不可点。
"""

from pathlib import Path
from typing import Annotated, Any
from uuid import UUID, uuid4

from fastapi import APIRouter, File, HTTPException, Query, UploadFile
from fastapi.responses import Response
from pydantic import BaseModel, Field

from app.api.deps import CurrentUserDep, DbSessionDep
from app.config.minio_client import MinIOClient, MinIOError
from app.schemas.common import SuccessResponse
from app.repositories import checklist_repository as repo


router = APIRouter(
    prefix="/projects/{project_identifier}/checklist",
    tags=["Checklist 回归"],
)

# 证据对象在存储桶内的固定前缀，兼作目录穿越防护的锚
EVIDENCE_PREFIX = "checklist-evidence"

# 单证据大小上限（截图/短视频足够，防止误传大文件把桶打爆）
EVIDENCE_MAX_BYTES = 50 * 1024 * 1024

_EXT_CONTENT_TYPE = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".webp": "image/webp",
    ".gif": "image/gif",
    ".mp4": "video/mp4",
    ".webm": "video/webm",
    ".json": "application/json",
    ".txt": "text/plain; charset=utf-8",
    ".log": "text/plain; charset=utf-8",
    ".zip": "application/zip",
}


def _safe_evidence_name(project_identifier: str, raw: str) -> str:
    """校验证据对象名：必须落在本项目 checklist-evidence/ 前缀下，无目录穿越。"""
    name = raw.replace("\\", "/").lstrip("/")
    expected = f"{EVIDENCE_PREFIX}/{project_identifier}/"
    if not name.startswith(expected) or ".." in name.split("/"):
        raise HTTPException(status_code=400, detail="非法证据对象名")
    return name


class ChecklistRowIn(BaseModel):
    row_id: str = Field(..., min_length=1, max_length=16, description="行锚 CK-<md8>")
    row: int | None = Field(default=None, description="在线表格行号")
    title: str = ""
    main_module: str = ""
    sub_module: str = ""
    feature: str = ""
    expected: str = ""
    priority: str = ""
    probe_kind: str = "reachable"
    data_mode: str = "readonly"
    content_md5: str = ""
    extra: dict[str, Any] | None = None


class ChecklistImportRequest(BaseModel):
    rows: list[ChecklistRowIn] = Field(..., min_length=1)
    dry_run: bool = Field(default=False, description="只算 diff 不落库（同步面板预览）")


class RunResultItem(BaseModel):
    row_id: str
    status: str = ""
    error: str | None = None
    evidence: str | None = None
    consistency: str | None = None
    feature: str | None = None
    main_module: str | None = None


class RunResultRequest(BaseModel):
    profile: str = ""
    run_verdict: str = "empty"
    summary: dict[str, Any] | None = None
    skipped_flow: int = 0
    report_url: str | None = None
    results: list[RunResultItem] = Field(default_factory=list)


def _row_out(r) -> dict[str, Any]:
    return {
        "row_id": r.row_id,
        "source_row": r.source_row,
        "title": r.title,
        "main_module": r.main_module,
        "sub_module": r.sub_module,
        "feature": r.feature,
        "expected": r.expected,
        "priority": r.priority,
        "probe_kind": r.probe_kind,
        "data_mode": r.data_mode,
        "content_md5": r.content_md5,
        "latest_status": r.latest_status,
        "latest_error": r.latest_error,
        "latest_evidence": r.latest_evidence,
        "latest_consistency": r.latest_consistency,
        "latest_run_at": r.latest_run_at.isoformat() if r.latest_run_at else None,
    }


@router.post(
    "/import",
    response_model=SuccessResponse[dict[str, Any]],
    summary="Checklist 行导入（row_id 幂等 upsert，支持 dry-run 预览）",
)
async def import_rows(
    project_identifier: str,
    user: CurrentUserDep,
    request: ChecklistImportRequest,
    db: DbSessionDep,
) -> SuccessResponse[dict[str, Any]]:
    try:
        project = await repo.get_project(db, project_identifier)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    result = await repo.upsert_rows(
        db, project.id,
        [r.model_dump() | {"source_row": r.row} for r in request.rows],
        persist=not request.dry_run,
    )
    return SuccessResponse(
        success=True,
        data={"counts": result["counts"], "total": len(request.rows),
              "details": result["details"]},
    )


@router.post(
    "/sync-preview",
    response_model=SuccessResponse[dict[str, Any]],
    summary="上传腾讯文档导出的表格文件，预览同步 diff（不落库）",
)
async def sync_preview(
    project_identifier: str,
    user: CurrentUserDep,
    db: DbSessionDep,
    file: Annotated[UploadFile, File(description="腾讯文档导出的 .json/.csv 表格行")],
) -> SuccessResponse[dict[str, Any]]:
    """同步面板第一步：解析 + 计算逐行 diff，不写库。

    解析与锚定规则与 checklist-sync skill 脚本同源（app/services/checklist_sync.py）。
    """
    from app.services import checklist_sync

    try:
        project = await repo.get_project(db, project_identifier)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    data = await file.read()
    if not data:
        raise HTTPException(status_code=400, detail="空文件")
    try:
        raw_rows = checklist_sync.parse_sheet(data, file.filename or "")
        items, dup = checklist_sync.build_items(raw_rows)
    except (ValueError, UnicodeDecodeError) as exc:
        raise HTTPException(status_code=400, detail=f"表格文件解析失败: {exc}") from exc
    if not items:
        raise HTTPException(status_code=400, detail="未解析到有效行（需要 主模块/功能点 列）")

    result = await repo.upsert_rows(
        db, project.id,
        [i | {"source_row": i.pop("row")} for i in items],
        persist=False,
    )
    return SuccessResponse(
        success=True,
        data={"counts": result["counts"], "total": len(items),
              "dup_row_ids": dup, "details": result["details"],
              "items": items},
    )


@router.get(
    "/rows",
    response_model=SuccessResponse[list[dict[str, Any]]],
    summary="Checklist 行列表（含最近执行状态）",
)
async def list_rows(
    project_identifier: str,
    user: CurrentUserDep,
    db: DbSessionDep,
    main_module: str | None = Query(default=None),
    priority: str | None = Query(default=None),
    probe_kind: str | None = Query(default=None),
) -> SuccessResponse[list[dict[str, Any]]]:
    try:
        project = await repo.get_project(db, project_identifier)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    rows = await repo.list_rows(db, project.id)
    data = [_row_out(r) for r in rows]
    if main_module:
        data = [x for x in data if x["main_module"] == main_module]
    if priority:
        data = [x for x in data if x["priority"] == priority]
    if probe_kind:
        data = [x for x in data if x["probe_kind"] == probe_kind]
    return SuccessResponse(success=True, data=data)


@router.post(
    "/run-results",
    response_model=SuccessResponse[dict[str, Any]],
    summary="写入一次探针执行结果（整包落库 + 行级最新状态回填）",
)
async def save_run_results(
    project_identifier: str,
    user: CurrentUserDep,
    request: RunResultRequest,
    db: DbSessionDep,
) -> SuccessResponse[dict[str, Any]]:
    try:
        project = await repo.get_project(db, project_identifier)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    run = await repo.save_run_result(db, project.id, request.model_dump())
    return SuccessResponse(
        success=True,
        data={
            "run_id": str(run.id),
            "run_verdict": run.run_verdict,
            "rows_touched": getattr(run, "touch_count", 0),
        },
    )


@router.get(
    "/runs",
    response_model=SuccessResponse[list[dict[str, Any]]],
    summary="执行历史（最新在前）",
)
async def list_runs(
    project_identifier: str,
    user: CurrentUserDep,
    db: DbSessionDep,
    limit: int = Query(default=20, ge=1, le=100),
) -> SuccessResponse[list[dict[str, Any]]]:
    try:
        project = await repo.get_project(db, project_identifier)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    runs = await repo.list_runs(db, project.id, limit)
    return SuccessResponse(
        success=True,
        data=[
            {
                "run_id": str(r.id),
                "run_verdict": r.run_verdict,
                "profile_name": r.profile_name,
                "summary": r.summary,
                "skipped_flow": r.skipped_flow,
                "report_url": r.report_url,
                "created_at": r.created_at.isoformat() if r.created_at else None,
            }
            for r in runs
        ],
    )


@router.get(
    "/runs/{run_id}",
    response_model=SuccessResponse[dict[str, Any]],
    summary="单次执行整包结果",
)
async def get_run(
    project_identifier: str,
    user: CurrentUserDep,
    run_id: UUID,
    db: DbSessionDep,
) -> SuccessResponse[dict[str, Any]]:
    try:
        project = await repo.get_project(db, project_identifier)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    run = await repo.get_run(db, project.id, run_id)
    if not run:
        raise HTTPException(status_code=404, detail="执行记录不存在")
    return SuccessResponse(
        success=True,
        data={
            "run_id": str(run.id),
            "run_verdict": run.run_verdict,
            "profile_name": run.profile_name,
            "summary": run.summary,
            "skipped_flow": run.skipped_flow,
            "report_url": run.report_url,
            "results": (run.results or {}).get("results", []),
        },
    )


@router.get(
    "/writeback-preview",
    response_model=SuccessResponse[dict[str, Any]],
    summary="生成回写腾讯文档的行载荷（平台只算载荷，写文档由外部执行）",
)
async def writeback_preview(
    project_identifier: str,
    user: CurrentUserDep,
    db: DbSessionDep,
    run_id: Annotated[UUID | None, Query(description="缺省取最近一次执行")] = None,
    label: str = Query(default="自动化执行", description="执行方式列文案"),
) -> SuccessResponse[dict[str, Any]]:
    """回写面板数据源：把一次执行整包转成插入腾讯文档的一行载荷。

    平台不落库、不碰腾讯文档；拿到载荷后由 WorkBuddy 用腾讯文档授权插入
    《上线Checklist》的「自动化执行」区。列契约与 checklist-writeback skill 一致。
    """
    from app.services import checklist_sync

    try:
        project = await repo.get_project(db, project_identifier)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    run = None
    if run_id is not None:
        run = await repo.get_run(db, project.id, run_id)
        if not run:
            raise HTTPException(status_code=404, detail="执行记录不存在")
    else:
        latest = await repo.list_runs(db, project.id, limit=1)
        run = latest[0] if latest else None
    if not run:
        raise HTTPException(status_code=404, detail="暂无执行记录，无可回写内容")

    results = (run.results or {}).get("results", [])
    run_dict = {
        "run_id": str(run.id),
        "run_verdict": run.run_verdict,
        "profile_name": run.profile_name,
        "summary": run.summary,
        "skipped_flow": run.skipped_flow,
        "report_url": run.report_url,
        "created_at": run.created_at.isoformat() if run.created_at else "",
    }
    return SuccessResponse(
        success=True,
        data={
            "run_id": str(run.id),
            "columns": list(checklist_sync.WRITEBACK_COLUMNS),
            "payload": checklist_sync.build_writeback_payload(run_dict, results, label),
        },
    )


@router.post(
    "/evidence",
    response_model=SuccessResponse[dict[str, Any]],
    summary="探针证据上传（转存对象存储，返回 object_name）",
)
async def upload_evidence(
    project_identifier: str,
    user: CurrentUserDep,
    db: DbSessionDep,
    file: Annotated[UploadFile, File(description="证据文件（截图/视频/日志）")],
) -> SuccessResponse[dict[str, Any]]:
    """探针证据上传。

    探针跑完后对每个证据文件调用本端点（multipart，字段名 file），
    平台转存对象存储并返回 object_name；探针把它填进 run-results
    各项的 evidence 字段，前端即可通过 /evidence-file 永不过期地访问。
    """
    try:
        project = await repo.get_project(db, project_identifier)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    data = await file.read()
    if not data:
        raise HTTPException(status_code=400, detail="空文件")
    if len(data) > EVIDENCE_MAX_BYTES:
        raise HTTPException(
            status_code=413,
            detail=f"证据超过大小上限 {EVIDENCE_MAX_BYTES // (1024 * 1024)}MB",
        )

    ext = Path(file.filename or "").suffix.lower() or ".bin"
    object_name = f"{EVIDENCE_PREFIX}/{project.identifier}/{uuid4().hex[:12]}{ext}"
    content_type = file.content_type or _EXT_CONTENT_TYPE.get(ext, "application/octet-stream")
    try:
        MinIOClient.upload_bytes(object_name, data, content_type=content_type)
    except MinIOError as exc:
        raise HTTPException(status_code=502, detail=f"证据上传对象存储失败: {exc}") from exc

    return SuccessResponse(
        success=True,
        data={"object_name": object_name, "size": len(data), "content_type": content_type},
    )


@router.get(
    "/evidence-file",
    summary="证据文件下载（对象存储代理，链接永不过期）",
)
async def get_evidence_file(
    project_identifier: str,
    db: DbSessionDep,
    object_name: Annotated[str, Query(description="upload_evidence 返回的 object_name")],
) -> Response:
    """按 object_name 从对象存储取回证据文件。

    本端点**有意不鉴权**：前端凭证列是裸 ``<a href>`` 不带 Authorization，
    且对象名不可枚举（写入端已鉴权、UUID 命名）——等价分享链接语义。
    防护依赖 _safe_evidence_name 的项目前缀限定。其余端点均要求登录。
    """
    try:
        project = await repo.get_project(db, project_identifier)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    name = _safe_evidence_name(project.identifier, object_name)
    try:
        content = MinIOClient.download_file(name)
    except MinIOError as exc:
        if getattr(exc.original_error, "code", None) == "NoSuchKey":
            raise HTTPException(status_code=404, detail="证据文件不存在") from exc
        raise HTTPException(status_code=502, detail=f"证据读取失败: {exc}") from exc

    ext = Path(name).suffix.lower()
    media_type = _EXT_CONTENT_TYPE.get(ext, "application/octet-stream")
    return Response(content=content, media_type=media_type)
