"""统一接口资产包路由 — 三文件联合导入（parse-bundle / confirm）。

链路：

    POST /projects/{pid}/api-bundles/parse-bundle   上传三文件 → 解析并落库（不建端点）
    GET  /projects/{pid}/api-bundles/{bundle_id}    查询 bundle 解析结果
    POST /projects/{pid}/api-bundles/{bundle_id}/conflicts/confirm  确认/豁免冲突
    POST /projects/{pid}/api-bundles/{bundle_id}/confirm            确认导入（单事务建端点）
    GET  /projects/{pid}/api-bundles/{bundle_id}/variables          查询变量（secret 脱敏）

幂等：
- 同项目同名 bundle 复用；
- 来源按 (bundle, source_type) upsert；
- 契约按 (bundle, method, normalized_path) upsert；
- 端点按 (项目, method, normalized_path) 复用，重复 confirm 返回 skipped。
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from fastapi import APIRouter, File, Form, HTTPException, UploadFile, status
from pydantic import BaseModel, Field
from sqlalchemy import select

from app.api.deps import CurrentUserIdDep, DbSessionDep
from app.models.api_asset import ApiDocumentBundle
from app.models.project import Project
from app.schemas.common import SuccessResponse
from app.services.api_bundle_service import (
    SOURCE_TYPES,
    ApiBundleService,
    BundleStateError,
    ConflictNotConfirmedError,
    SchemaNotReadyError,
    SourceInput,
)

router = APIRouter(
    prefix="/projects/{project_identifier}/api-bundles",
    tags=["统一接口资产包"],
)

# 单个来源文件上限（Excel 契约通常 < 5MB）
MAX_SOURCE_SIZE = 20 * 1024 * 1024

__all__ = ["router", "ConflictResolutionItem", "BundleConfirmRequest"]


# ---------------------------------------------------------------------------
# 请求模型
# ---------------------------------------------------------------------------

class ConflictResolutionItem(BaseModel):
    """单条冲突处理。"""
    conflict_id: UUID = Field(..., description="冲突 ID")
    resolution: str = Field(..., description="accept（接受并继续）/ waive（豁免）")
    note: str | None = Field(default=None, description="处理备注")


class ConflictsConfirmRequest(BaseModel):
    """批量确认冲突。"""
    resolutions: list[ConflictResolutionItem] = Field(..., min_length=1)


class BundleConfirmRequest(BaseModel):
    """确认导入请求。"""
    contract_ids: list[UUID] | None = Field(
        default=None, description="指定要导入的契约；为空则导入全部"
    )
    parent_folder_id: UUID | None = Field(default=None, description="父文件夹 ID")
    folder_name: str | None = Field(default=None, description="目标文件夹名（默认取 bundle 名）")


# ---------------------------------------------------------------------------
# 辅助
# ---------------------------------------------------------------------------

async def _get_project(project_identifier: str, db) -> Project:
    stmt = select(Project).where(Project.identifier == project_identifier)
    project = (await db.execute(stmt)).scalar_one_or_none()
    if project is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"项目 {project_identifier} 不存在",
        )
    return project


async def _get_bundle(bundle_id: UUID, project: Project, db) -> ApiDocumentBundle:
    stmt = select(ApiDocumentBundle).where(
        ApiDocumentBundle.id == bundle_id,
        ApiDocumentBundle.project_id == project.id,
    )
    bundle = (await db.execute(stmt)).scalar_one_or_none()
    if bundle is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"资产包 {bundle_id} 不存在或不属于项目 {project.identifier}",
        )
    return bundle


def _outcome_payload(outcome) -> dict[str, Any]:
    return {
        "bundle_id": str(outcome.bundle_id),
        "status": outcome.status,
        "stats": outcome.stats,
        "sources": outcome.sources,
        "contracts": outcome.contracts,
        "conflicts": outcome.conflicts,
        "warnings": outcome.warnings,
        "errors": outcome.errors,
    }


# ---------------------------------------------------------------------------
# 路由
# ---------------------------------------------------------------------------

@router.post(
    "/parse-bundle",
    response_model=SuccessResponse[dict[str, Any]],
    summary="解析三文件并落库（Bundle/Source/契约/变量/冲突）",
    description=(
        "上传 Excel 契约、Postman Collection、Postman Environment（至少一个）。"
        "只解析与落库，不创建 APIEndpoint；创建端点请调用 confirm。"
    ),
)
async def parse_bundle(
    project_identifier: str,
    current_user_id: CurrentUserIdDep,
    db: DbSessionDep,
    excel_file: UploadFile | None = File(default=None, description="Excel 接口契约（美心模板）"),
    collection_file: UploadFile | None = File(default=None, description="Postman Collection"),
    environment_file: UploadFile | None = File(default=None, description="Postman Environment"),
    bundle_name: str = Form(..., description="资产包名称（同项目同名复用）"),
    system_type: str | None = Form(default=None, description="系统类型: crm_non_pos / pos / marketing_gateway"),
) -> SuccessResponse[dict[str, Any]]:
    project = await _get_project(project_identifier, db)

    uploads = [
        ("excel_mx", excel_file),
        ("postman_collection", collection_file),
        ("postman_environment", environment_file),
    ]
    sources: list[SourceInput] = []
    for source_type, upload in uploads:
        if upload is None:
            continue
        content = await upload.read()
        if not content:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"{source_type} 上传文件为空",
            )
        if len(content) > MAX_SOURCE_SIZE:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"{source_type} 文件超过 {MAX_SOURCE_SIZE // 1024 // 1024}MB 限制",
            )
        sources.append(SourceInput(
            source_type=source_type,
            file_name=upload.filename or f"{source_type}.bin",
            content=content,
        ))

    if not sources:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="至少需要上传一个来源文件（excel_file / collection_file / environment_file）",
        )

    service = ApiBundleService(db)
    try:
        bundle = await service.create_bundle(
            project_id=project.id,
            name=bundle_name,
            system_type=system_type,
            created_by=str(current_user_id),
        )
        outcome = await service.parse_bundle(bundle, sources)
        await db.commit()
    except HTTPException:
        await db.rollback()
        raise
    except Exception as exc:  # noqa: BLE001
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"解析失败（已回滚）: {type(exc).__name__}: {exc}",
        ) from exc

    return SuccessResponse(success=True, data=_outcome_payload(outcome))


@router.get(
    "/{bundle_id}",
    response_model=SuccessResponse[dict[str, Any]],
    summary="查询资产包解析结果",
)
async def get_bundle(
    project_identifier: str,
    bundle_id: UUID,
    db: DbSessionDep,
) -> SuccessResponse[dict[str, Any]]:
    project = await _get_project(project_identifier, db)
    bundle = await _get_bundle(bundle_id, project, db)
    service = ApiBundleService(db)
    contracts = await service._load_contracts(bundle)  # noqa: SLF001 — 只读查询

    return SuccessResponse(success=True, data={
        "bundle": {
            "id": str(bundle.id),
            "name": bundle.name,
            "system_type": bundle.system_type,
            "status": bundle.status,
            "current_version": bundle.current_version,
            "parse_result": bundle.parse_result,
        },
        "contracts": [
            {
                "id": str(c.id),
                "endpoint_key": c.endpoint_key,
                "method": c.method,
                "path": c.path,
                "normalized_path": c.normalized_path,
                "name": c.name,
                "status": c.status,
                "source_types": sorted((c.request or {}).get("_source_types", []) or []),
                "imported_endpoint_id": str(c.imported_endpoint_id) if c.imported_endpoint_id else None,
                "pending_confirmations": c.pending_confirmations or [],
            }
            for c in contracts
        ],
        "conflicts": await service._load_conflicts(bundle),  # noqa: SLF001
    })


@router.get(
    "/{bundle_id}/variables",
    response_model=SuccessResponse[list[dict[str, Any]]],
    summary="查询资产包变量（secret 脱敏，仅返回 env:NAME 引用）",
)
async def get_bundle_variables(
    project_identifier: str,
    bundle_id: UUID,
    db: DbSessionDep,
) -> SuccessResponse[list[dict[str, Any]]]:
    project = await _get_project(project_identifier, db)
    bundle = await _get_bundle(bundle_id, project, db)
    service = ApiBundleService(db)
    return SuccessResponse(success=True, data=await service.list_variables(bundle))


@router.post(
    "/{bundle_id}/conflicts/confirm",
    response_model=SuccessResponse[dict[str, Any]],
    summary="确认或豁免冲突",
)
async def confirm_conflicts(
    project_identifier: str,
    bundle_id: UUID,
    body: ConflictsConfirmRequest,
    db: DbSessionDep,
) -> SuccessResponse[dict[str, Any]]:
    project = await _get_project(project_identifier, db)
    bundle = await _get_bundle(bundle_id, project, db)
    service = ApiBundleService(db)
    try:
        results = await service.confirm_conflicts(
            bundle, [item.model_dump() for item in body.resolutions]
        )
        await db.commit()
    except BundleStateError as exc:
        await db.rollback()
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    except Exception as exc:  # noqa: BLE001
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"冲突确认失败（已回滚）: {type(exc).__name__}: {exc}",
        ) from exc

    return SuccessResponse(success=True, data={
        "bundle_id": str(bundle.id),
        "resolved": results,
        "remaining_open": [
            c for c in await service._load_conflicts(bundle) if c["status"] == "open"  # noqa: SLF001
        ],
    })


@router.post(
    "/{bundle_id}/confirm",
    response_model=SuccessResponse[dict[str, Any]],
    summary="确认导入（单事务创建 Folder/APIEndpoint/模板/断言/依赖）",
    description=(
        "幂等键为 项目 + method + normalized_path：已存在的端点会被复用并回填契约关联，"
        "不会重复创建。存在未确认的高等级冲突时拒绝导入。任一步失败整体回滚。"
    ),
)
async def confirm_bundle(
    project_identifier: str,
    bundle_id: UUID,
    body: BundleConfirmRequest,
    db: DbSessionDep,
) -> SuccessResponse[dict[str, Any]]:
    project = await _get_project(project_identifier, db)
    bundle = await _get_bundle(bundle_id, project, db)
    service = ApiBundleService(db)

    conflict_payload: dict[str, Any] | None = None
    try:
        outcome = await service.confirm(
            bundle,
            contract_ids=body.contract_ids,
            parent_folder_id=body.parent_folder_id,
            folder_name=body.folder_name,
        )
        await db.commit()
    except ConflictNotConfirmedError as exc:
        # 关键：必须在 rollback 前把冲突清单固化成纯 dict。
        # rollback 会使 ORM 实例过期，之后再访问属性会触发
        # sqlalchemy MissingGreenlet（在同步上下文中做异步 IO）。
        conflict_payload = {
            "bundle_id": str(bundle.id),
            "unconfirmed_count": len(exc.conflicts),
            "unconfirmed_conflicts": [
                {
                    "id": str(c.get("id")),
                    "conflict_type": c.get("conflict_type"),
                    "severity": c.get("severity"),
                    "message": c.get("message"),
                }
                for c in (exc.conflicts or [])
            ],
        }
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=conflict_payload,
        ) from exc
    except SchemaNotReadyError as exc:
        # 服务端结构未就绪（迁移未到 head）→ 503 而非 400：
        # 请求本身合法，是部署环境不满足前提。
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "error": "schema_not_ready",
                "message": str(exc),
                "missing": exc.missing,
                "remedy": "alembic upgrade head",
            },
        ) from exc
    except BundleStateError as exc:
        await db.rollback()
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    except Exception as exc:  # noqa: BLE001
        # 失败回滚：不留下半成品端点/文件夹
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"导入失败（已整体回滚）: {type(exc).__name__}: {exc}",
        ) from exc

    return SuccessResponse(success=True, data={
        "bundle_id": str(outcome.bundle_id),
        "status": outcome.status,
        "imported": outcome.imported,
        "skipped": outcome.skipped,
        "failed": outcome.failed,
        "folder_ids": outcome.folder_ids,
        "imported_count": len(outcome.imported),
        "skipped_count": len(outcome.skipped),
        # 结构就绪校验已通过（幂等键唯一约束存在），供部署取证
        "schema_check": "passed",
    })


@router.get(
    "/{bundle_id}/assets",
    response_model=SuccessResponse[dict[str, Any]],
    summary="查询资产包已落库的导入产物统计（模板/断言/依赖/端点）",
)
async def get_bundle_assets(
    project_identifier: str,
    bundle_id: UUID,
    db: DbSessionDep,
) -> SuccessResponse[dict[str, Any]]:
    project = await _get_project(project_identifier, db)
    bundle = await _get_bundle(bundle_id, project, db)

    from sqlalchemy import func

    from app.models.api_asset import (
        ApiAssertion,
        ApiDependency,
        ApiInterfaceContract,
        ApiRequestTemplate,
    )

    template_count = (await db.execute(
        select(func.count(ApiRequestTemplate.id))
        .join(ApiInterfaceContract, ApiInterfaceContract.id == ApiRequestTemplate.contract_id)
        .where(ApiInterfaceContract.bundle_id == bundle.id)
    )).scalar() or 0

    assertion_count = (await db.execute(
        select(func.count(ApiAssertion.id))
        .join(ApiInterfaceContract, ApiInterfaceContract.id == ApiAssertion.contract_id)
        .where(ApiInterfaceContract.bundle_id == bundle.id)
    )).scalar() or 0

    dependency_count = (await db.execute(
        select(func.count(ApiDependency.id)).where(ApiDependency.bundle_id == bundle.id)
    )).scalar() or 0

    contract_count = (await db.execute(
        select(func.count(ApiInterfaceContract.id))
        .where(ApiInterfaceContract.bundle_id == bundle.id)
    )).scalar() or 0

    imported_contract_count = (await db.execute(
        select(func.count(ApiInterfaceContract.id)).where(
            ApiInterfaceContract.bundle_id == bundle.id,
            ApiInterfaceContract.imported_endpoint_id.is_not(None),
        )
    )).scalar() or 0

    # 断言来源细分：用于区分「真实 Excel 返回码断言」与「Collection 脚本断言」，
    # 避免验收时无法判断断言是否真的来自文档（P0-2）。
    assertion_by_source = (await db.execute(
        select(ApiAssertion.source_type, func.count(ApiAssertion.id))
        .join(ApiInterfaceContract, ApiInterfaceContract.id == ApiAssertion.contract_id)
        .where(ApiInterfaceContract.bundle_id == bundle.id)
        .group_by(ApiAssertion.source_type)
    )).all()

    return SuccessResponse(success=True, data={
        "bundle_id": str(bundle.id),
        "status": bundle.status,
        "contracts": contract_count,
        "contracts_with_endpoint": imported_contract_count,
        "request_templates": template_count,
        "assertions": assertion_count,
        "dependencies": dependency_count,
        "assertions_by_source": {
            str(source): int(count) for source, count in assertion_by_source
        },
    })


@router.get(
    "",
    response_model=SuccessResponse[list[dict[str, Any]]],
    summary="列出项目下的资产包",
)
async def list_bundles(
    project_identifier: str,
    db: DbSessionDep,
) -> SuccessResponse[list[dict[str, Any]]]:
    project = await _get_project(project_identifier, db)
    stmt = (
        select(ApiDocumentBundle)
        .where(ApiDocumentBundle.project_id == project.id)
        .order_by(ApiDocumentBundle.created_at.desc())
    )
    bundles = (await db.execute(stmt)).scalars().all()
    return SuccessResponse(success=True, data=[
        {
            "id": str(b.id),
            "name": b.name,
            "system_type": b.system_type,
            "status": b.status,
            "parse_result": b.parse_result,
            "created_at": b.created_at.isoformat() if b.created_at else None,
        }
        for b in bundles
    ])
