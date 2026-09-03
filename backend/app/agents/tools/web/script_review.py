"""脚本自愈评审队列（执行治理层 P0-2）。

- enqueue_script_review：执行失败（脚本自评 failed 等）入队（幂等——同子功能
  已有 pending/repairing 任务不重复）；
- bump_review_retry：重试次数 +1，达上限（MAX_REVIEW_RETRIES）→ blocked（人工介入）。
"""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import UUID, uuid4

from sqlalchemy import select, text

from app.models.web_script_review import WebScriptReview

# 自愈重试上限（与执行层失败刹车保持一致的量级）
MAX_REVIEW_RETRIES = 3


async def enqueue_script_review(
    session_factory,
    sub_function_id: UUID,
    attachment_id: UUID | None,
    error_summary: str | None,
) -> WebScriptReview | None:
    """入队评审任务（幂等：同子功能已有 pending/repairing 任务则更新摘要不新建）。

    rev51（评审问题 2）：attachment_id 为空时**强制解析当前 effective 脚本附件**
    绑定（失败评审必须精确追踪到失败版本，不能 NULL）——解析失败才允许
    attachment_id=None 并记录原因。

    Returns:
        新任务或现有任务；异常时返回 None（不阻断执行流程）。
    """
    try:
        async with session_factory() as session:
            existing = (
                await session.execute(
                    select(WebScriptReview).where(
                        WebScriptReview.sub_function_id == sub_function_id,
                        WebScriptReview.status.in_(["pending", "repairing"]),
                    ).order_by(WebScriptReview.created_at.desc()).limit(1)
                )
            ).scalars().first()
            if existing:
                if error_summary:
                    existing.error_summary = (error_summary or "")[:2000]
                if existing.attachment_id is None and attachment_id is not None:
                    existing.attachment_id = attachment_id
                await session.commit()
                return existing
            # rev51：未显式传入失败附件时，解析当前 effective 附件作为失败版本锚点
            bound_att_id = attachment_id
            if bound_att_id is None:
                try:
                    from app.agents.tools.web.script_provenance import (
                        resolve_current_script_attachment,
                    )

                    bound_att_id = await resolve_current_script_attachment(
                        session, sub_function_id
                    )
                    if bound_att_id is not None:
                        print(
                            f"[script_review] rev51：已自动绑定当前 effective 失败附件 "
                            f"{bound_att_id}"
                        )
                except Exception as _att_e:
                    print(f"[script_review] 解析当前附件失败（attachment 保持 NULL）: {_att_e}")
            task = WebScriptReview(
                id=uuid4(),
                sub_function_id=sub_function_id,
                attachment_id=bound_att_id,
                status="pending",
                error_summary=(error_summary or "")[:2000],
                retry_count=0,
            )
            session.add(task)
            await session.commit()
            print(f"[script_review] 已入队评审任务: sub_function={sub_function_id} status=pending"
                  f" attachment={bound_att_id}")
            return task
    except Exception as e:
        print(f"[script_review] 入队失败（不阻断执行）: {e}")
        return None


async def enqueue_reviews_for_subfunctions(
    session_factory,
    sub_function_ids: list[UUID],
    error_summary: str | None,
) -> None:
    """为**全部**子功能入队评审任务（rev40 修复：不再只入队第一个）。

    每个子功能尝试绑定当前 effective 脚本附件（resolve_current_script_attachment）
    以便准确追踪失败版本；解析失败/无附件 → attachment_id=None（仍入队）。
    """
    for sf_id in sub_function_ids:
        att_id = None
        try:
            from app.agents.tools.web.script_provenance import (
                resolve_current_script_attachment,
            )

            async with session_factory() as s:
                att_id = await resolve_current_script_attachment(s, sf_id)
        except Exception as _att_e:
            print(f"[script_review] 解析子功能 {sf_id} 当前附件失败（仍入队）: {_att_e}")
        await enqueue_script_review(session_factory, sf_id, att_id, error_summary)


async def run_repair_cycle(
    session_factory,
    sub_function_id: UUID,
    fix_generator,
    execute_verifier,
) -> dict:
    """修复循环编排（rev40，闭环脚手架；fix_generator 由 agent 层提供）。

    流程：
    1. 领取 pending/repairing 任务（无 → 返回 no_task）；
    2. 标记 repairing（重试计数不变）；
    3. fix_generator(sub_function_id, error_summary) → 新脚本内容（agent 修复）；
    4. save 为 proposed 版本（version_mode="proposed"，不覆盖当前生效）；
    5. execute_verifier() 验证修复后脚本（返回 (ok, error)）；
    6. 通过 → publish_script_version（proposed → effective，旧版本降 old）+
       任务 passed；失败 → bump_review_retry（达上限 blocked，人工介入）。

    Args:
        session_factory: DB session 工厂
        sub_function_id: 子功能 ID
        fix_generator: async (sf_id, error_summary) -> str（新脚本内容）
        execute_verifier: async (sf_id, proposed_attachment_id, project_identifier)
            -> tuple[bool, str]（验证修复结果）

    Returns:
        dict: {status: no_task|repairing|passed|blocked|error, detail}
    """
    # rev42：原子领取（并发下仅一个 worker 成功）
    task = await claim_review(session_factory, sub_function_id)
    if task is None:
        return {"status": "no_task", "detail": "无待修复任务或并发竞争失败（已被其他 worker 领取）"}
    error_summary = task.error_summary or ""

    try:
        # 3. agent 修复生成新脚本内容
        content = await fix_generator(sub_function_id, error_summary)

        # 4. 保存为 proposed 版本
        from app.agents.tools.web.artifacts_tools import save_web_test_script

        save_result = await save_web_test_script.coroutine(
            sub_function_id=str(sub_function_id),
            script_content=content,
            script_language="python",
            script_format="playwright",
            project_identifier="",
            version_mode="proposed",
        )
        if isinstance(save_result, dict) and save_result.get("error"):
            raise RuntimeError(f"proposed 保存失败: {save_result.get('error')}")
        prop_att_id = save_result.get("attachment_id")

        # 5. 执行验证
        ok, err = await execute_verifier(str(sub_function_id), prop_att_id)

        if ok:
            # rev55（审批流）：修复验证通过后**不自动发布**——记录 proposed
            # 附件与版本号，任务转 pending_approval，由人工审批决定是否生效
            # （approve_script_review 发布 / reject_script_review 拒绝）。
            async with session_factory() as session:
                task = (
                    await session.execute(
                        select(WebScriptReview).where(
                            WebScriptReview.sub_function_id == sub_function_id,
                            WebScriptReview.status == "repairing",
                        ).order_by(WebScriptReview.created_at.desc()).limit(1)
                    )
                ).scalars().first()
                if task:
                    task.status = "pending_approval"
                    if prop_att_id:
                        task.proposed_attachment_id = UUID(str(prop_att_id))
                    task.version_no = (task.version_no or 0) + 1
                    await session.commit()
            return {
                "status": "pending_approval",
                "detail": f"修复验证通过，待审批发布（proposed 附件 {prop_att_id}，"
                          f"version_no={task.version_no if task else '?'}）",
            }
        else:
            # 6b. 失败 → 重试计数（rev42：未达上限置回 pending 可重试，达上限 blocked）
            task = await bump_review_retry(session_factory, sub_function_id, err or "修复验证失败")
            return {
                "status": "blocked" if task and task.status == "blocked" else "pending",
                "detail": f"修复验证失败: {(err or '')[:200]}（重试 {task.retry_count if task else '?'}/{MAX_REVIEW_RETRIES}）",
            }
    except Exception as e:
        # rev41：异常（修复生成/proposed 保存/发布失败等）计入重试计数，
        # 达上限 blocked——不静默返回 error 导致任务悬挂
        task = await bump_review_retry(session_factory, sub_function_id, f"修复循环异常: {e}")
        return {
            "status": "blocked" if task and task.status == "blocked" else "pending",
            "detail": f"修复循环异常（已计入重试）: {str(e)[:200]}（重试 {task.retry_count if task else '?'}/{MAX_REVIEW_RETRIES}）",
        }


async def claim_review(session_factory, sub_function_id: UUID) -> WebScriptReview | None:
    """**原子领取**评审任务（rev42 事务级并发治理）。

    状态机：pending --领取--> repairing --验证失败--> bump --未达上限--> pending（可重试）
    --达上限--> blocked；验证通过 --> passed。
    用单条 UPDATE（`WHERE status='pending'`）原子翻转 pending→repairing，
    affected==1 才算领到——跨 PG/SQLite 生效，双 worker 并发下只有一个成功。
    """
    from datetime import datetime, timezone

    try:
        async with session_factory() as session:
            task = (
                await session.execute(
                    select(WebScriptReview).where(
                        WebScriptReview.sub_function_id == sub_function_id,
                        WebScriptReview.status == "pending",
                    ).order_by(WebScriptReview.created_at.desc(), WebScriptReview.id.desc()).limit(1)
                )
            ).scalars().first()
            if task is None:
                return None
            result = await session.execute(
                text(
                    "UPDATE web_script_reviews SET status='repairing', updated_at=:ts "
                    "WHERE id=:id AND status='pending'"
                ),
                {"id": task.id.hex, "ts": datetime.now(timezone.utc)},
            )
            if result.rowcount == 1:
                await session.commit()
                return task
            return None  # 并发竞争失败（他人已领取/状态已变）
    except Exception as e:
        print(f"[script_review] 领取任务失败: {e}")
        return None


async def run_pending_repairs(
    session_factory,
    fix_generator=None,
    execute_verifier=None,
    limit: int = 5,
) -> list[dict]:
    """调度入口（rev44）：扫描 pending 评审任务并逐个执行修复循环。

    供后台任务/API 调用（生产接入点）；fix_generator/execute_verifier 缺省时
    使用真实实现（LLM 修复 + proposed 验证）。

    Returns:
        list[dict]: 每个任务的修复结果 {sub_function_id, status, detail}
    """
    results: list[dict] = []
    async with session_factory() as session:
        rows = (
            await session.execute(
                select(WebScriptReview).where(WebScriptReview.status == "pending")
                .order_by(WebScriptReview.created_at.asc()).limit(limit)
            )
        ).scalars().all()
        pending = [(r.sub_function_id, r.error_summary) for r in rows]
    if not pending:
        return results
    if fix_generator is None:
        from app.agents.tools.web.script_repair_agent import build_llm_fix_generator

        fix_generator = build_llm_fix_generator()
    if execute_verifier is None:
        from app.agents.tools.web.script_repair_agent import verify_proposed_script

        async def _verify(sf_id: UUID, att_id: str | None) -> tuple[bool, str]:
            return await verify_proposed_script(session_factory, sf_id, att_id)

        execute_verifier = _verify
    for sf_id, err_summary in pending:
        r = await run_repair_cycle(session_factory, sf_id, fix_generator, execute_verifier)
        results.append({"sub_function_id": str(sf_id), "error_summary": (err_summary or "")[:80], **r})
    return results


async def bump_review_retry(
    session_factory,
    sub_function_id: UUID,
    error_summary: str | None = None,
) -> WebScriptReview | None:
    """重试计数 +1；达上限 → blocked（人工介入）。

    由评审修复循环（重新保存 proposed → 执行 → 失败）每次调用。
    """
    try:
        async with session_factory() as session:
            task = (
                await session.execute(
                    select(WebScriptReview).where(
                        WebScriptReview.sub_function_id == sub_function_id,
                        WebScriptReview.status.in_(["pending", "repairing"]),
                    ).order_by(WebScriptReview.created_at.desc()).limit(1)
                )
            ).scalars().first()
            if task is None:
                return None
            task.retry_count = (task.retry_count or 0) + 1
            if error_summary:
                task.error_summary = (error_summary or "")[:2000]
            if task.retry_count >= MAX_REVIEW_RETRIES:
                task.status = "blocked"
                print(f"[script_review] 子功能 {sub_function_id} 重试达上限 "
                      f"({MAX_REVIEW_RETRIES})，评审任务 blocked（人工介入）")
            else:
                # rev42：未达上限 → 置回 pending（供下次原子领取重试；非 repairing）
                task.status = "pending"
            await session.commit()
            return task
    except Exception as e:
        print(f"[script_review] 重试计数失败: {e}")
        return None


async def approve_script_review(
    session_factory,
    sub_function_id: UUID,
    actor: str = "admin",
) -> dict:
    """审批通过：将 pending_approval 任务的 proposed 附件发布为 effective（rev55）。

    修复后脚本**不直接覆盖生效**——本动作（人工/管理 API 审批）才触发发布：
    proposed → effective，旧 effective 降 old，WebTest.script_path 更新；
    任务 passed（保留 proposed_attachment_id/version_no 审计）。

    Returns:
        dict: {status: passed|no_task|error, detail, version_no, object_name?}
    """
    try:
        from app.agents.tools.web.artifacts_tools import publish_script_version

        async with session_factory() as session:
            task = (
                await session.execute(
                    select(WebScriptReview).where(
                        WebScriptReview.sub_function_id == sub_function_id,
                        WebScriptReview.status == "pending_approval",
                    ).order_by(WebScriptReview.created_at.desc()).limit(1)
                )
            ).scalars().first()
            if task is None:
                return {"status": "no_task", "detail": "无待审批任务（pending_approval）"}
            prop_att_id = task.proposed_attachment_id
            version_no = task.version_no or 0
        if not prop_att_id:
            return {"status": "error", "detail": "待审批任务缺少 proposed_attachment_id，无法发布"}
        pub = await publish_script_version.coroutine(
            sub_function_id=str(sub_function_id),
            verified_attachment_id=str(prop_att_id),
        )
        if not (isinstance(pub, dict) and pub.get("success")):
            return {"status": "error", "detail": f"发布 effective 失败: {pub}"}
        async with session_factory() as session:
            task = (
                await session.execute(
                    select(WebScriptReview).where(
                        WebScriptReview.sub_function_id == sub_function_id,
                        WebScriptReview.status == "pending_approval",
                    ).order_by(WebScriptReview.created_at.desc()).limit(1)
                )
            ).scalars().first()
            if task:
                task.status = "passed"
                await session.commit()
        print(f"[script_review] 审批通过（actor={actor}）: {sub_function_id} version_no={version_no} "
              f"proposed={prop_att_id} -> effective")
        return {"status": "passed", "detail": f"审批通过并发布 effective: {pub.get('object_name', '')}",
                "version_no": version_no}
    except Exception as e:
        print(f"[script_review] 审批通过失败: {e}")
        return {"status": "error", "detail": f"审批通过异常: {e}"}


async def reject_script_review(
    session_factory,
    sub_function_id: UUID,
    reason: str | None = None,
    actor: str = "admin",
) -> dict:
    """审批拒绝（rev55）：pending_approval 任务 → rejected。

    proposed 版本**不发布**（保留 registry proposed + 附件作审计）；
    任务 rejected（人工介入，不会自动重试）。

    Returns:
        dict: {status: rejected|no_task|error, detail, version_no}
    """
    try:
        async with session_factory() as session:
            task = (
                await session.execute(
                    select(WebScriptReview).where(
                        WebScriptReview.sub_function_id == sub_function_id,
                        WebScriptReview.status == "pending_approval",
                    ).order_by(WebScriptReview.created_at.desc()).limit(1)
                )
            ).scalars().first()
            if task is None:
                return {"status": "no_task", "detail": "无待审批任务（pending_approval）"}
            task.status = "rejected"
            if reason:
                task.error_summary = (reason or "")[:2000]
            version_no = task.version_no or 0
            await session.commit()
        print(f"[script_review] 审批拒绝（actor={actor}）: {sub_function_id} version_no={version_no} reason={reason}")
        return {"status": "rejected", "detail": f"审批拒绝（proposed 保留审计）: {reason or ''}",
                "version_no": version_no}
    except Exception as e:
        print(f"[script_review] 审批拒绝失败: {e}")
        return {"status": "error", "detail": f"审批拒绝异常: {e}"}
