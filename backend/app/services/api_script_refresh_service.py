"""脚本资产跟随模板版本（P2）：识别并就地重建过期脚本。

**问题**

``APITest.script_path`` 指向对象存储里的**一次性产物**：生成时按当时那版模板渲染，
之后不管模板怎么改，执行器都按路径原样下载。于是"模板已经补了必填 query 参数"和
"库里那条脚本还是残缺 URL 版"可以同时存在，而且从 API 表面完全看不出来 ——
跑出来的结论对不对，取决于这条资产是哪天生成的。

**做法**

1. 生成期在脚本首行写指纹（``// API_SCRIPT_TEMPLATE_REV: ... DIGEST: ...``），
   并把同一份 ``{rev, digest}`` 写进 ``generation_params.script_template``。
2. 本服务按当前模板指纹逐条比对，给出过期原因（无指纹 / rev 不同 / digest 不同）。
3. ``dry_run=False`` 时**就地重建**：用同一个 ``script_path`` 覆盖上传，
   同步附件体积与版本戳。路径不变 ⇒ 执行器、附件、历史 run 的引用都不用动。

**边界（有意为之）**

- 只重建 ``generated_by_agent == api_case_link_service`` 的资产。人工编写/导入的脚本
  不是模板产物，重写等于删掉别人的工作。
- 端点已删除的资产重建不了，如实计入 ``skipped_missing_endpoint``，不静默跳过。
- 单条失败不影响其他条目（对象存储偶发失败不该让整批回滚），失败原因逐条带回。
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Iterable, Optional
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config.minio_client import MinIOClient
from app.models.api_endpoint import APIEndpoint
from app.models.api_test import APITest
from app.models.attachment import Attachment
from app.models.project import Project
from app.services.api_case_link_service import APICaseLinkService, script_template_digest
from app.services.api_script_template import (
    API_SCRIPT_TEMPLATE_REV,
    stale_reason,
    stale_reason_text,
)

logger = logging.getLogger(__name__)

#: 只有这个 agent 生成的脚本才认定为"模板产物"，可被本服务重建。
TEMPLATE_GENERATED_BY = "api_case_link_service"

#: 条目动作码
ACTION_WOULD_REGENERATE = "would_regenerate"
ACTION_REGENERATED = "regenerated"
ACTION_FAILED = "failed"
ACTION_SKIPPED_NO_ENDPOINT = "skipped_missing_endpoint"
ACTION_CURRENT = "current"
ACTION_NOT_TEMPLATE = "not_template"


class APIScriptRefreshService:
    """把"脚本是否还跟着模板"变成可查、可执行的动作。"""

    def __init__(self, session: AsyncSession):
        self.session = session
        self.link_service = APICaseLinkService(session)

    async def refresh(
        self,
        project_identifier: str,
        *,
        dry_run: bool = True,
        api_test_ids: Optional[Iterable[str]] = None,
    ) -> dict[str, Any]:
        """比对并（可选）重建项目下的模板脚本。

        Args:
            project_identifier: 项目短码（如 ``PR-2``）
            dry_run: 只报告不改动（默认 True —— 批量改资产的风险不该由调用方默认承担）
            api_test_ids: 只处理指定资产（排障用），缺省处理项目内全部模板产物

        Returns:
            结构化报告：总数、各原因计数、逐条动作与失败原因
        """
        project = await self._get_project(project_identifier)
        current_digest = script_template_digest()

        stmt = (
            select(APITest)
            .where(APITest.project_id == project.id)
            .where(APITest.generated_by_agent == TEMPLATE_GENERATED_BY)
            .order_by(APITest.identifier.asc())
        )
        if api_test_ids:
            wanted = [UUID(str(item)) for item in api_test_ids]
            stmt = stmt.where(APITest.id.in_(wanted))

        rows = (await self.session.execute(stmt)).scalars().all()

        by_reason: dict[str, int] = {}
        items: list[dict[str, Any]] = []
        regenerated = failed = skipped = 0

        for api_test in rows:
            stamp = (api_test.generation_params or {}).get("script_template")
            reason = stale_reason(stamp, current_digest=current_digest)
            api_test_id = str(api_test.id)
            if reason is None:
                continue

            by_reason[reason] = by_reason.get(reason, 0) + 1
            entry: dict[str, Any] = {
                "api_test_id": api_test_id,
                "identifier": api_test.identifier,
                "reason": reason,
                "reason_text": stale_reason_text(reason),
                "stamp": stamp,
            }

            if dry_run:
                entry["action"] = ACTION_WOULD_REGENERATE
                items.append(entry)
                continue

            action, detail, _ = await self._regenerate(api_test, previous_stamp=stamp)
            entry["action"] = action
            if action == ACTION_REGENERATED:
                regenerated += 1
                entry["script_path"] = api_test.script_path
            elif action == ACTION_SKIPPED_NO_ENDPOINT:
                skipped += 1
                entry["detail"] = detail
            else:
                failed += 1
                entry["error"] = detail
            items.append(entry)

        if not dry_run and (regenerated or failed):
            await self.session.commit()
            logger.info(
                "[ScriptRefresh] %s 重建完成：regenerated=%s failed=%s skipped=%s",
                project.identifier, regenerated, failed, skipped,
            )

        return {
            "project": project.identifier,
            "dry_run": dry_run,
            "current_rev": API_SCRIPT_TEMPLATE_REV,
            "current_digest": current_digest,
            "checked": len(rows),
            "stale": len(items),
            "regenerated": regenerated,
            "failed": failed,
            "skipped": skipped,
            "by_reason": {
                code: {"count": count, "text": stale_reason_text(code)}
                for code, count in sorted(by_reason.items())
            },
            "items": items,
        }

    async def ensure_current(
        self,
        api_test: APITest,
        *,
        current_content: Optional[str] = None,
    ) -> tuple[str, Optional[str], str]:
        """执行前确保这条资产跟得上当前模板（``/run`` 链路专用）。

        与 ``refresh()`` 的区别：这里只针对**正要执行的那一条**，且默认动作是重建
        ——执行已经开始，用户不会有机会再手动调一次 refresh。

        Returns:
            ``(action, rendered_content, reason)``

            - ``not_template``：非模板产物（人工/导入脚本），不比对也不重建
            - ``current``：已是最新，调用方继续用手上的内容
            - ``regenerated``：已重建，**必须**用返回的 ``rendered_content`` 执行
            - ``failed`` / ``skipped_missing_endpoint``：没重建成功，调用方用旧内容继续，
              但这次执行的结论要当作"基于过期模板"看待
        """
        if api_test.generated_by_agent != TEMPLATE_GENERATED_BY:
            return ACTION_NOT_TEMPLATE, None, ""

        stamp = (api_test.generation_params or {}).get("script_template")
        reason = stale_reason(stamp, current_digest=script_template_digest())
        if reason is None:
            return ACTION_CURRENT, None, ""

        previous_bytes = (
            current_content.encode("utf-8") if current_content is not None else None
        )
        action, detail, content = await self._regenerate(
            api_test, previous_stamp=stamp, previous_bytes=previous_bytes
        )
        if action == ACTION_REGENERATED:
            # 后台会话没有请求级依赖注入代为提交：不 commit 会被回滚，
            # 而对象存储那边已经覆盖了 —— 版本戳与脚本内容就此对不上。
            await self.session.commit()
            logger.info(
                "[ScriptRefresh] 执行前重建 %s（%s）", api_test.identifier, reason
            )
            return ACTION_REGENERATED, content, reason

        logger.warning(
            "[ScriptRefresh] 执行前重建失败 %s（%s）：%s —— 本次仍按旧脚本执行",
            api_test.identifier, reason, detail,
        )
        return action, None, detail or reason

    async def _get_project(self, project_identifier: str) -> Project:
        project = (
            await self.session.execute(
                select(Project).where(Project.identifier == project_identifier)
            )
        ).scalar_one_or_none()
        if project is None:
            raise ValueError(f"项目不存在: {project_identifier}")
        return project

    async def _regenerate(
        self,
        api_test: APITest,
        *,
        previous_stamp: Optional[dict[str, Any]] = None,
        previous_bytes: Optional[bytes] = None,
    ) -> tuple[str, str, Optional[str]]:
        """就地重建单条脚本，返回 ``(action, detail, rendered_content)``。"""
        endpoint_id_raw = (api_test.test_config or {}).get("endpoint_id")
        if not endpoint_id_raw:
            return ACTION_SKIPPED_NO_ENDPOINT, "test_config 里没有 endpoint_id", None
        try:
            endpoint_id = UUID(str(endpoint_id_raw))
        except ValueError:
            return ACTION_SKIPPED_NO_ENDPOINT, f"endpoint_id 非法: {endpoint_id_raw}", None

        endpoint = await self.session.get(APIEndpoint, endpoint_id)
        if endpoint is None:
            return ACTION_SKIPPED_NO_ENDPOINT, f"端点已不存在: {endpoint_id}", None

        try:
            content = self.link_service.render_script(endpoint)
            payload = content.encode("utf-8")
            archived = self._archive_previous(
                api_test.script_path, previous_bytes=previous_bytes
            )
            MinIOClient.upload_bytes(
                object_name=api_test.script_path,
                data=payload,
                content_type="text/plain",
            )
        except Exception as exc:  # 单条失败不拖累整批
            logger.warning(
                "[ScriptRefresh] 重建失败 %s: %s", api_test.identifier, exc
            )
            return ACTION_FAILED, str(exc), None

        await self._sync_attachment_size(api_test.script_path, len(payload))

        now = datetime.now(timezone.utc).isoformat()
        generation_params = dict(api_test.generation_params or {})
        generation_params["script_template"] = {
            **self.link_service.script_stamp(),
            "generated_at": now,
            "regenerated_at": now,
            # 留旧戳 + 旧内容归档位置：出问题时要能回答"这版是从哪版换过来的"
            "previous": {**(previous_stamp or {}), "archived_object": archived},
        }
        api_test.generation_params = generation_params
        return ACTION_REGENERATED, "", content

    def _archive_previous(
        self, object_name: str, *, previous_bytes: Optional[bytes] = None
    ) -> Optional[str]:
        """覆盖前把旧脚本另存一份，返回归档对象名。

        重建是**破坏性**的（同一路径覆盖），没有归档就没有回退路径：
        一旦新模板对某个接口产生更松/更严的判定，事后想复现旧结论就没材料了。
        归档失败直接抛 —— 宁可这条不重建，也不要在无备份的情况下覆盖。

        ``previous_bytes``：调用方已经下载过内容时直接传入，省一次对象存储往返
        （执行前自动重建的热路径上，内容本来就在手上）。
        """
        if previous_bytes is None:
            try:
                previous_bytes = MinIOClient.download_file(object_name)
            except Exception:
                # 对象本来就不存在（如从未成功上传）→ 没有可丢的内容
                return None
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%f")
        directory, _, filename = object_name.rpartition("/")
        archived_name = f"{directory}/history/{stamp}-{filename}"
        MinIOClient.upload_bytes(
            object_name=archived_name,
            data=previous_bytes,
            content_type="text/plain",
        )
        return archived_name

    async def _sync_attachment_size(self, object_name: str, size: int) -> None:
        """附件表里记的体积跟着脚本一起更新（否则列表页显示的是历史大小）。"""
        attachments = (
            await self.session.execute(
                select(Attachment).where(Attachment.object_name == object_name)
            )
        ).scalars().all()
        for attachment in attachments:
            attachment.file_size = size
