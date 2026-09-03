"""Web 脚本评审管理 API（P0-2 生产接入，rev45/46 接线 + rev47 鉴权加固）。

- POST /web-script-reviews/run-pending：立即执行一次待评审修复循环
  （真实 LLM 修复 + proposed 验证 + 发布/重试/阻塞状态机）；
- GET /web-script-reviews/pending：运维可见的待处理/处理中评审任务列表。

rev47 鉴权：两个端点均要求超级管理员（get_current_superuser）——
- 未认证 → 401；
- 已认证非超管 → 403；
- 每次管理操作记录结构化审计日志（actor / action / result）。
"""

import logging

from fastapi import APIRouter, Body, Depends
from pydantic import BaseModel

from app.api.deps import get_current_superuser
from app.config.database import async_session_factory
from app.models.user import User

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/web-script-reviews", tags=["Web 脚本评审"])


class RunPendingRequest(BaseModel):
    limit: int = 5


@router.post("/run-pending")
async def run_pending(
    body: RunPendingRequest = Body(default=RunPendingRequest()),
    current_user: User = Depends(get_current_superuser),
):
    """立即执行一次待评审修复循环（真实 Agent：LLM 修复 + proposed 验证）。

    仅超级管理员可调用；返回每个处理任务的结果：
    {sub_function_id, error_summary, status, detail, ...}。
    limit 上限 20，防止单次触发消耗过多 API 额度。
    """
    from app.agents.tools.web.script_review import run_pending_repairs

    results = await run_pending_repairs(
        async_session_factory, limit=max(1, min(body.limit, 20))
    )
    # rev47 审计：谁在何时触发了修复循环、处理了多少任务
    logger.info(
        "[audit] actor=%s(%s) action=web_script_reviews.run_pending limit=%s ran=%s",
        current_user.username, current_user.id, body.limit, len(results),
    )
    return {"ran": len(results), "results": results}


@router.get("/pending")
async def list_pending(
    current_user: User = Depends(get_current_superuser),
):
    """列出待评审/处理中任务（运维可见，便于确认修复循环状态）。

    仅超级管理员可调用；返回任务含错误摘要（敏感失败信息）。
    """
    from sqlalchemy import select

    from app.models.web_script_review import WebScriptReview

    async with async_session_factory() as session:
        rows = (
            await session.execute(
                select(WebScriptReview)
                .where(WebScriptReview.status.in_(
                    ["pending", "repairing", "pending_approval"]))
                .order_by(WebScriptReview.created_at.desc())
                .limit(50)
            )
        ).scalars().all()
    logger.info(
        "[audit] actor=%s(%s) action=web_script_reviews.list_pending count=%s",
        current_user.username, current_user.id, len(rows),
    )
    return {
        "count": len(rows),
        "items": [
            {
                "id": str(r.id),
                "sub_function_id": str(r.sub_function_id),
                "status": r.status,
                "retry_count": r.retry_count,
                "version_no": r.version_no,
                "proposed_attachment_id": str(r.proposed_attachment_id) if r.proposed_attachment_id else None,
                "error_summary": (r.error_summary or "")[:200],
                "created_at": r.created_at.isoformat() if r.created_at else None,
            }
            for r in rows
        ],
    }


class ApproveReviewRequest(BaseModel):
    sub_function_id: str
    reason: str | None = None


@router.post("/approve")
async def approve_review(
    body: ApproveReviewRequest,
    current_user: User = Depends(get_current_superuser),
):
    """审批通过（rev55）：将 pending_approval 任务的 proposed 修复版本发布为 effective。

    修复后脚本**不自动覆盖生效**——必须经本审批动作才发布（proposed → effective，
    旧版本降 old）。仅超级管理员可调用；返回发布结果与 version_no（审计）。
    """
    from uuid import UUID

    from app.agents.tools.web.script_review import approve_script_review

    result = await approve_script_review(
        async_session_factory,
        UUID(body.sub_function_id),
        actor=f"{current_user.username}({current_user.id})",
    )
    logger.info(
        "[audit] actor=%s(%s) action=web_script_reviews.approve sub_function=%s result=%s",
        current_user.username, current_user.id, body.sub_function_id, result.get("status"),
    )
    return result


@router.post("/reject")
async def reject_review(
    body: ApproveReviewRequest,
    current_user: User = Depends(get_current_superuser),
):
    """审批拒绝（rev55）：pending_approval → rejected（proposed 保留审计，不发布）。

    仅超级管理员可调用。
    """
    from uuid import UUID

    from app.agents.tools.web.script_review import reject_script_review

    result = await reject_script_review(
        async_session_factory,
        UUID(body.sub_function_id),
        reason=body.reason,
        actor=f"{current_user.username}({current_user.id})",
    )
    logger.info(
        "[audit] actor=%s(%s) action=web_script_reviews.reject sub_function=%s result=%s",
        current_user.username, current_user.id, body.sub_function_id, result.get("status"),
    )
    return result
