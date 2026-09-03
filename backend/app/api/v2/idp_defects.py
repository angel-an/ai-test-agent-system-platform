"""
IDP 缺陷管理 API

提供 IDP 缺陷记录的查询、操作和项目映射接口
"""

import json
from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Query

from app.api.deps import DbSessionDep
from app.repositories.idp_defect_repo import IDPDefectRecordRepository
from app.schemas.common import SuccessResponse
from app.schemas.enums import IDPDefectStatus
from app.config.settings import settings
from app.services.idp_client import IDPClient
from app.services.idp_project_resolver import IDPProjectResolver

router = APIRouter(prefix="/idp-defects")


# ============================================================================
# 测试运行级缺陷查询
# ============================================================================

@router.get(
    "/test-runs/{source_run_id}",
    response_model=SuccessResponse,
    summary="获取测试运行的 IDP 缺陷记录",
    description="获取指定测试运行的所有 IDP 缺陷登记记录",
)
async def get_defects_by_source_run(
    source_run_id: UUID,
    session: DbSessionDep,
    status: Optional[str] = Query(None, description="按状态过滤"),
):
    """获取测试运行的 IDP 缺陷记录"""
    repo = IDPDefectRecordRepository(session)

    if status:
        records = await repo.get_by_status(source_run_id, status)
    else:
        records = await repo.get_by_source_run(source_run_id)

    items = []
    for record in records:
        items.append(_record_to_dict(record))

    # 统计
    summary = {
        "total": len(items),
        "not_required": sum(1 for r in items if r["create_status"] == IDPDefectStatus.NOT_REQUIRED.value),
        "insufficient_evidence": sum(1 for r in items if r["create_status"] == IDPDefectStatus.INSUFFICIENT_EVIDENCE.value),
        "pending": sum(1 for r in items if r["create_status"] == IDPDefectStatus.PENDING.value),
        "created": sum(1 for r in items if r["create_status"] == IDPDefectStatus.CREATED.value),
        "verified": sum(1 for r in items if r["create_status"] == IDPDefectStatus.VERIFIED.value),
        "written_back": sum(1 for r in items if r["create_status"] == IDPDefectStatus.WRITTEN_BACK.value),
        "sync_failed": sum(1 for r in items if r["create_status"] == IDPDefectStatus.SYNC_FAILED.value),
        "duplicate": sum(1 for r in items if r["create_status"] == IDPDefectStatus.DUPLICATE.value),
        "skipped": sum(1 for r in items if r["create_status"] == IDPDefectStatus.SKIPPED.value),
    }

    return SuccessResponse(data={
        "items": items,
        "summary": summary,
    })


# 向后兼容：保留按 test_run_id 查询的 API
@router.get(
    "/test-runs/{test_run_id}/legacy",
    response_model=SuccessResponse,
    summary="【向后兼容】获取 API 测试运行的 IDP 缺陷记录",
)
async def get_defects_by_test_run_legacy(
    test_run_id: UUID,
    session: DbSessionDep,
    status: Optional[str] = Query(None, description="按状态过滤"),
):
    """获取 API 测试运行的 IDP 缺陷记录（向后兼容）"""
    repo = IDPDefectRecordRepository(session)

    if status:
        records = await repo.get_by_status(test_run_id, status)
    else:
        records = await repo.get_by_test_run(test_run_id)

    items = [_record_to_dict(r) for r in records]

    summary = {
        "total": len(items),
        "created": sum(1 for r in items if r["create_status"] == "created"),
        "duplicate": sum(1 for r in items if r["create_status"] == "duplicate"),
        "skipped": sum(1 for r in items if r["create_status"] == "skipped"),
        "failed": sum(1 for r in items if r["create_status"] == "failed"),
        "pending": sum(1 for r in items if r["create_status"] == "pending"),
    }

    return SuccessResponse(data={
        "items": items,
        "summary": summary,
    })


@router.get(
    "/test-runs/{source_run_id}/defect-status",
    response_model=SuccessResponse,
    summary="获取运行级缺陷状态汇总",
    description="获取指定测试运行的缺陷登记状态汇总，用于页面展示",
)
async def get_defect_status_summary(
    source_run_id: UUID,
    session: DbSessionDep,
):
    """获取运行级缺陷状态汇总"""
    repo = IDPDefectRecordRepository(session)
    records = await repo.get_by_source_run(source_run_id)

    # 按状态分组
    status_counts = {}
    for status in IDPDefectStatus:
        status_counts[status.value] = 0

    for record in records:
        if record.create_status in status_counts:
            status_counts[record.create_status] += 1

    # 判断整体状态
    total = len(records)
    if total == 0:
        overall_status = "no_defects"
    elif any(r.create_status == IDPDefectStatus.SYNC_FAILED.value for r in records):
        overall_status = "has_failures"
    elif any(r.create_status == IDPDefectStatus.INSUFFICIENT_EVIDENCE.value for r in records):
        overall_status = "needs_confirmation"
    elif all(r.create_status in (IDPDefectStatus.VERIFIED.value, IDPDefectStatus.WRITTEN_BACK.value, IDPDefectStatus.DUPLICATE.value, IDPDefectStatus.SKIPPED.value, IDPDefectStatus.NOT_REQUIRED.value) for r in records):
        overall_status = "all_processed"
    else:
        overall_status = "in_progress"

    return SuccessResponse(data={
        "source_run_id": str(source_run_id),
        "overall_status": overall_status,
        "total_records": total,
        "status_counts": status_counts,
        "records": [_record_to_dict(r) for r in records],
    })


# ============================================================================
# 缺陷记录详情与操作
# ============================================================================

@router.get(
    "/records/{record_id}",
    response_model=SuccessResponse,
    summary="获取 IDP 缺陷记录详情",
)
async def get_defect_record(
    record_id: UUID,
    session: DbSessionDep,
):
    """获取 IDP 缺陷记录详情"""
    repo = IDPDefectRecordRepository(session)
    record = await repo.get_by_id(record_id)

    if not record:
        return SuccessResponse(data=None, message="记录不存在")

    return SuccessResponse(data=_record_to_dict(record))


@router.get(
    "/records/{record_id}/evidence",
    response_model=SuccessResponse,
    summary="查看缺陷证据",
    description="获取缺陷登记的完整证据信息",
)
async def get_defect_evidence(
    record_id: UUID,
    session: DbSessionDep,
):
    """查看缺陷证据"""
    repo = IDPDefectRecordRepository(session)
    record = await repo.get_by_id(record_id)

    if not record:
        return SuccessResponse(data=None, message="记录不存在")

    # 证据信息（从记录中提取）
    evidence = {
        "record_id": str(record.id),
        "source_type": record.source_type,
        "source_run_id": str(record.source_run_id),
        "defect_title": record.defect_title,
        "defect_priority": record.defect_priority,
        "reqid": record.reqid,
        "fingerprint": record.fingerprint,
        "idp_issue_url": record.idp_issue_url,
        "report_url": record.report_url,
        "error_message": record.error_message,
        "verification_status": record.verification_status,
        "verification_error": record.verification_error,
    }

    return SuccessResponse(data=evidence)


@router.post(
    "/records/{record_id}/retry-sync",
    response_model=SuccessResponse,
    summary="重试同步",
    description="对同步失败的记录重新尝试 IDP 同步",
)
async def retry_defect_sync(
    record_id: UUID,
    session: DbSessionDep,
):
    """重试同步缺陷到 IDP"""
    from app.services.idp_defect_service import IDPDefectService
    from app.services.defect_fingerprint import DefectFingerprintService

    repo = IDPDefectRecordRepository(session)
    record = await repo.get_by_id(record_id)

    if not record:
        return SuccessResponse(data=None, message="记录不存在")

    if record.create_status not in (IDPDefectStatus.SYNC_FAILED.value, IDPDefectStatus.PENDING.value):
        return SuccessResponse(data=None, message=f"当前状态 {record.create_status} 不支持重试同步")

    # 解析项目映射
    mapping = IDPProjectResolver.resolve(record.source_project_key)
    if not mapping:
        return SuccessResponse(data=None, message="项目映射不存在，无法重试")

    # 重新调用 IDP 创建
    idp_service = IDPDefectService(session)
    try:
        # 优先级映射：本地优先级值 -> IDP priorityCode + priorityId
        priority_map = {
            "high": ("priority-1", 1),
            "medium": ("priority-2", 2),
            "low": ("priority-3", 3),
        }
        priority_code, priority_id = priority_map.get(
            record.defect_priority or "medium",
            ("priority-2", 2)
        )

        # IDP 富文本字段需要 Quill Delta 操作数组的 JSON 字符串。
        delta_description = [
            {"insert": "问题概述\n", "attributes": {"bold": True}},
            {"insert": f"{record.defect_title or '缺陷重试同步'}\n\n"},
            {"insert": "追踪信息\n", "attributes": {"bold": True}},
            {"insert": f"reqid: {record.reqid or '未获取'}\n"},
            {"insert": f"原始记录: {record_id}\n"},
            {"insert": "【本 Issue 由重试同步创建，原描述请参考关联记录】\n"},
        ]

        issue_data = {
            "summary": record.defect_title or "【自动化测试】缺陷",
            "description": json.dumps(delta_description, ensure_ascii=False),
            "typeCode": mapping.type_code,
            "issueTypeId": mapping.issue_type_id,
            "priorityCode": priority_code,
            "priorityId": priority_id,
        }

        if mapping.default_sprint_id:
            issue_data["sprintId"] = mapping.default_sprint_id
        if mapping.default_epic_id:
            issue_data["epicId"] = mapping.default_epic_id
        if mapping.default_assignee_id:
            issue_data["assigneeId"] = mapping.default_assignee_id

        idp_client = IDPClient()
        if idp_service.dry_run:
            result = await idp_client.create_issue_dry_run(mapping.idp_project_id, issue_data)
        else:
            result = await idp_client.create_issue(mapping.idp_project_id, issue_data)

        issue_id = result.get("issueId")
        issue_key = result.get("issueNum")
        web_base = getattr(settings, 'idp_web_base_url', settings.idp_base_url).rstrip('/')
        issue_url = f"{web_base}/agile/issues/{issue_id}" if issue_id else None

        updated = await repo.update(
            record,
            create_status=IDPDefectStatus.CREATED.value,
            idp_issue_id=issue_id,
            idp_issue_key=issue_key,
            idp_issue_url=issue_url,
            error_message=None,
        )

        return SuccessResponse(
            data={
                "record_id": str(record_id),
                "idp_issue_id": issue_id,
                "idp_issue_key": issue_key,
                "idp_issue_url": issue_url,
                "status": updated.create_status,
            },
            message=f"重试同步成功: {issue_key}"
        )

    except Exception as e:
        await repo.update(
            record,
            create_status=IDPDefectStatus.SYNC_FAILED.value,
            error_message=f"重试失败: {str(e)[:500]}",
        )
        return SuccessResponse(
            data={"record_id": str(record_id)},
            message=f"重试同步失败: {str(e)}"
        )


@router.post(
    "/records/{record_id}/mark-insufficient",
    response_model=SuccessResponse,
    summary="标记证据不足",
    description="将记录标记为证据不足状态",
)
async def mark_insufficient_evidence(
    record_id: UUID,
    reason: str,
    session: DbSessionDep,
):
    """标记证据不足"""
    repo = IDPDefectRecordRepository(session)
    record = await repo.get_by_id(record_id)

    if not record:
        return SuccessResponse(data=None, message="记录不存在")

    updated = await repo.update(
        record,
        create_status=IDPDefectStatus.INSUFFICIENT_EVIDENCE.value,
        error_message=reason,
    )

    return SuccessResponse(
        data={"id": str(updated.id), "create_status": updated.create_status},
        message="已标记为证据不足"
    )


@router.post(
    "/records/{record_id}/update-description",
    response_model=SuccessResponse,
    summary="补齐 IDP 描述",
    description="更新 IDP Issue 的描述内容（Delta JSON 格式）",
)
async def update_defect_description(
    record_id: UUID,
    description: str,
    session: DbSessionDep,
):
    """补齐 IDP 描述

    调用 IDP 更新接口，将 description（Delta JSON 字符串）
    写入已创建的 Issue。
    """
    repo = IDPDefectRecordRepository(session)
    record = await repo.get_by_id(record_id)

    if not record:
        return SuccessResponse(data=None, message="记录不存在")

    if not record.idp_issue_id:
        return SuccessResponse(data=None, message="记录尚未关联 IDP Issue，无法更新描述")

    # 调用 IDP 客户端更新 Issue 描述
    try:
        mapping = IDPProjectResolver.resolve(record.source_project_key)
        if not mapping:
            return SuccessResponse(data=None, message="项目映射不存在")

        idp_client = IDPClient()

        # IDP 描述字段使用 Quill Delta 操作数组的 JSON 字符串。
        try:
            delta_ops = json.loads(description)
        except json.JSONDecodeError:
            return SuccessResponse(data=None, message="描述必须是合法的 Delta JSON 数组")
        if not isinstance(delta_ops, list):
            return SuccessResponse(data=None, message="描述必须是 Delta 操作数组，不支持 {\"ops\": [...]} 包装对象")

        # 更新接口要求乐观锁版本号，先回读当前 Issue 获取 objectVersionNumber。
        issue_detail = await idp_client.get_issue(
            mapping.idp_project_id, record.idp_issue_id
        )
        issue_data = issue_detail.get("data", issue_detail)
        object_version_number = issue_data.get("objectVersionNumber")
        if object_version_number is None:
            return SuccessResponse(
                data=None,
                message="IDP 返回中缺少 objectVersionNumber，无法安全更新描述",
            )

        update_data = {
            "issueId": record.idp_issue_id,
            "objectVersionNumber": object_version_number,
            "description": json.dumps(delta_ops, ensure_ascii=False),
        }
        result = await idp_client.update_issue(mapping.idp_project_id, update_data)

        # 更新本地记录
        updated = await repo.update(
            record,
            verification_status="pending",
            verification_error=None,
        )

        return SuccessResponse(
            data={
                "record_id": str(record_id),
                "idp_issue_id": record.idp_issue_id,
                "update_result": result,
            },
            message="描述更新已提交"
        )

    except Exception as e:
        return SuccessResponse(
            data={"record_id": str(record_id)},
            message=f"描述更新失败: {str(e)}"
        )


@router.post(
    "/records/{record_id}/mark-duplicate",
    response_model=SuccessResponse,
    summary="标记缺陷为重复",
)
async def mark_defect_duplicate(
    record_id: UUID,
    original_issue_id: int,
    original_issue_key: str,
    session: DbSessionDep,
    original_issue_url: Optional[str] = None,
):
    """标记缺陷为重复"""
    repo = IDPDefectRecordRepository(session)
    record = await repo.get_by_id(record_id)

    if not record:
        return SuccessResponse(data=None, message="记录不存在")

    updated = await repo.update(
        record,
        create_status=IDPDefectStatus.DUPLICATE.value,
        idp_issue_id=original_issue_id,
        idp_issue_key=original_issue_key,
        idp_issue_url=original_issue_url,
    )

    return SuccessResponse(
        data={
            "id": str(updated.id),
            "create_status": updated.create_status,
            "original_issue_key": updated.idp_issue_key,
            "original_issue_url": updated.idp_issue_url,
        },
        message=f"已标记为重复，关联到 {original_issue_key}"
    )


@router.delete(
    "/records/{record_id}",
    response_model=SuccessResponse,
    summary="删除 IDP 缺陷记录",
)
async def delete_defect_record(
    record_id: UUID,
    session: DbSessionDep,
):
    """删除 IDP 缺陷记录"""
    repo = IDPDefectRecordRepository(session)
    record = await repo.get_by_id(record_id)

    if not record:
        return SuccessResponse(data=None, message="记录不存在")

    await repo.delete(record)

    return SuccessResponse(
        data={"deleted_id": str(record_id)},
        message=f"记录 {record_id} 已删除（IDP 系统中的问题未被删除）"
    )


# ============================================================================
# 缺陷记录列表查询
# ============================================================================

@router.get(
    "/records",
    response_model=SuccessResponse,
    summary="获取所有 IDP 缺陷记录",
)
async def list_defect_records(
    session: DbSessionDep,
    status: Optional[str] = Query(None, description="按状态过滤"),
    source_type: Optional[str] = Query(None, description="按来源类型过滤: api, web, security"),
    source_project_key: Optional[str] = Query(None, description="按项目标识符过滤"),
    page: int = Query(1, ge=1, description="页码"),
    page_size: int = Query(20, ge=1, le=100, description="每页数量"),
):
    """获取所有 IDP 缺陷记录"""
    repo = IDPDefectRecordRepository(session)

    records = await repo.get_all_records(
        status=status,
        source_type=source_type,
        source_project_key=source_project_key,
        offset=(page - 1) * page_size,
        limit=page_size,
    )

    items = [_record_to_dict(r) for r in records]

    return SuccessResponse(data={
        "items": items,
        "page": page,
        "page_size": page_size,
        "total": len(items),
    })


# ============================================================================
# 项目映射接口
# ============================================================================

@router.get(
    "/project-mappings",
    response_model=SuccessResponse,
    summary="获取所有项目映射",
    description="返回所有 IDP 项目映射配置及状态",
)
async def list_project_mappings():
    """获取所有项目映射"""
    mappings = IDPProjectResolver.get_all_mappings()

    items = []
    for key, mapping in mappings.items():
        items.append({
            "source_project_key": mapping.source_project_key,
            "source_project_name": mapping.source_project_name,
            "idp_project_id": mapping.idp_project_id,
            "idp_project_name": mapping.idp_project_name,
            "type_code": mapping.type_code,
            "issue_type_id": mapping.issue_type_id,
            "default_priority_id": mapping.default_priority_id,
            "default_priority_code": mapping.default_priority_code,
            "default_sprint_id": mapping.default_sprint_id,
            "default_epic_id": mapping.default_epic_id,
            "default_assignee_id": mapping.default_assignee_id,
            "enabled": mapping.enabled,
        })

    return SuccessResponse(data={
        "items": items,
        "total": len(items),
    })


@router.get(
    "/project-mappings/{source_project_key}",
    response_model=SuccessResponse,
    summary="获取指定项目映射",
)
async def get_project_mapping(
    source_project_key: str,
):
    """获取指定项目映射"""
    mapping = IDPProjectResolver.resolve(source_project_key)

    if not mapping:
        return SuccessResponse(
            data=None,
            message=f"未找到项目映射: {source_project_key}"
        )

    return SuccessResponse(data={
        "source_project_key": mapping.source_project_key,
        "source_project_name": mapping.source_project_name,
        "idp_project_id": mapping.idp_project_id,
        "idp_project_name": mapping.idp_project_name,
        "type_code": mapping.type_code,
        "issue_type_id": mapping.issue_type_id,
        "default_priority_id": mapping.default_priority_id,
        "default_priority_code": mapping.default_priority_code,
        "default_sprint_id": mapping.default_sprint_id,
        "default_epic_id": mapping.default_epic_id,
        "default_assignee_id": mapping.default_assignee_id,
        "enabled": mapping.enabled,
    })


# ============================================================================
# 内部辅助函数
# ============================================================================

def _record_to_dict(record) -> dict:
    """将记录转换为字典"""
    return {
        "id": str(record.id),
        "source_type": record.source_type,
        "source_run_id": str(record.source_run_id),
        "source_case_id": str(record.source_case_id) if record.source_case_id else None,
        "test_run_id": str(record.test_run_id) if record.test_run_id else None,
        "test_case_id": str(record.test_case_id) if record.test_case_id else None,
        "source_project_key": record.source_project_key,
        "idp_project_id": record.idp_project_id,
        "fingerprint": record.fingerprint,
        "idp_issue_id": record.idp_issue_id,
        "idp_issue_key": record.idp_issue_key,
        "idp_issue_url": record.idp_issue_url,
        "create_status": record.create_status,
        "verification_status": record.verification_status,
        "verification_error": record.verification_error,
        "reqid": record.reqid,
        "report_url": record.report_url,
        "written_back_at": record.written_back_at.isoformat() if record.written_back_at else None,
        "error_message": record.error_message,
        "defect_title": record.defect_title,
        "defect_priority": record.defect_priority,
        "created_at": record.created_at.isoformat() if record.created_at else None,
        "updated_at": record.updated_at.isoformat() if record.updated_at else None,
    }
