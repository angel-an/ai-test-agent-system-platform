"""脚本来源授权（执行治理层 2a 严格模式，rev22 修正）。

检查：
1. resolve_within_workspace：`Path.resolve(strict=True)` + `relative_to(workspace_root)`
   —— 封绝对路径逃逸、符号链接逃逸、`..` 逃逸、跨工作区；
2. 真实项目归属（rev19）：登记侧由子功能 project_id 反查 `Project.identifier`，
   不信任工具参数；执行侧要求调用方项目与注册真实项目一致；
3. 每附件一个当前哈希（rev20）：attachment_id UNIQUE + upsert；
4. **DB 原子 upsert（rev22）**：`INSERT ... ON CONFLICT (attachment_id) DO UPDATE`，
   消除先查后插的并发唯一约束竞争；
5. **严格三要素绑定（rev22）**：`authorize_script_execution` 要求 sub_function_ids
   必填（空 → 拒绝），每个子功能的当前附件都必须解析成功并与 项目+当前哈希 绑定，
   任一失败即终局拒绝（无降级回退）。
"""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Awaitable, Callable
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession


def sha256_hex(content: bytes) -> str:
    """计算内容 SHA-256（十六进制）。"""
    return hashlib.sha256(content).hexdigest()


def sha256_hex_file(path: Path) -> str:
    """计算脚本文件 SHA-256。"""
    return sha256_hex(path.read_bytes())


def resolve_within_workspace(
    script_path: Path, workspace_root: Path
) -> tuple[Path | None, str]:
    """解析脚本并校验工作区包含。返回 (resolved_path, reason)；
    resolved_path 为 None 表示拒绝。"""
    try:
        root_resolved = workspace_root.resolve(strict=True)
    except (OSError, RuntimeError) as e:
        return None, f"工作区根目录解析失败: {e}"
    try:
        resolved = script_path.resolve(strict=True)
    except (OSError, RuntimeError) as e:
        return None, f"脚本解析失败（resolve strict=True）: {e}"
    try:
        resolved.relative_to(root_resolved)
    except ValueError:
        return None, f"脚本路径超出工作区（含符号链接/.. 逃逸）: {script_path}"
    return resolved, "ok"


async def resolve_real_project_identifier(
    session: AsyncSession, project_id: UUID
) -> str | None:
    """由真实 project_id 反查 Project.identifier（rev19 P0：不信任工具参数）。"""
    from app.models.project import Project

    stmt = select(Project.identifier).where(Project.id == project_id)
    value = (await session.execute(stmt)).scalar_one_or_none()
    return value if isinstance(value, str) else None


async def register_script_provenance(
    session: AsyncSession,
    project_identifier: str,
    attachment_id: UUID,
    script_hash: str,
    script_language: str = "",
    script_format: str = "",
    created_by: str = "web-agent",
    version_status: str = "effective",
) -> None:
    """登记脚本来源（rev22：DB 原生 upsert——`ON CONFLICT (attachment_id) DO UPDATE`，
    消除先查后插的并发唯一约束竞争；同一事务执行，由调用方统一 commit）。

    P0-2：version_status 区分 effective（当前生效）/ proposed（待评审发布）——
    proposed 版本 upsert 时保持 proposed（不被 effective 覆盖）；effective 默认。
    """
    from app.models.web_script_registry import WebScriptRegistry

    dialect = session.get_bind().dialect.name
    if dialect == "sqlite":
        from sqlalchemy.dialects.sqlite import insert as _dialect_insert
    else:
        from sqlalchemy.dialects.postgresql import insert as _dialect_insert

    stmt = _dialect_insert(WebScriptRegistry).values(
        id=uuid4(),
        project_identifier=project_identifier,
        attachment_id=attachment_id,
        script_hash=script_hash,
        script_language=script_language,
        script_format=script_format,
        created_by=created_by,
        version_status=version_status,
    )
    stmt = stmt.on_conflict_do_update(
        index_elements=[WebScriptRegistry.attachment_id],
        set_={
            "project_identifier": stmt.excluded.project_identifier,
            "script_hash": stmt.excluded.script_hash,
            "script_language": stmt.excluded.script_language,
            "script_format": stmt.excluded.script_format,
            "created_by": stmt.excluded.created_by,
            "version_status": stmt.excluded.version_status,
        },
    )
    await session.execute(stmt)


async def resolve_current_script_attachment(
    session: AsyncSession, sub_function_id: UUID
) -> UUID | None:
    """解析子功能当前脚本附件 ID（rev23：以 **WebTest.script_path** 为准，
    而非附件 created_at——避免"先 .ts 后 .py 再更新旧 .ts"时按创建时间取错附件）。"""
    from app.models.attachment import Attachment
    from app.models.web_test import WebTest

    wt_stmt = (
        select(WebTest.script_path)
        .where(WebTest.sub_function_id == sub_function_id)
        .order_by(WebTest.created_at.desc())
        .limit(1)
    )
    script_path = (await session.execute(wt_stmt)).scalar_one_or_none()
    if not script_path:
        return None
    att_stmt = select(Attachment.id).where(Attachment.object_name == script_path)
    value = (await session.execute(att_stmt)).scalar_one_or_none()
    return value if isinstance(value, UUID) else None


async def check_script_registered(
    session: AsyncSession,
    project_identifier: str,
    script_hash: str,
    attachment_id: UUID | None = None,
) -> tuple[bool, str]:
    """校验脚本已在本项目下登记（rev21：可绑定附件——真实项目 + 当前 hash + attachment_id）。

    P0-2：仅认 **effective** 版本——proposed（待评审发布）与 old（历史）版本
    不通过授权（执行入口只允许当前生效版本）。
    """
    from app.models.web_script_registry import WebScriptRegistry

    stmt = select(WebScriptRegistry).where(
        WebScriptRegistry.project_identifier == project_identifier,
        WebScriptRegistry.script_hash == script_hash,
        WebScriptRegistry.version_status == "effective",
    )
    if attachment_id is not None:
        stmt = stmt.where(WebScriptRegistry.attachment_id == attachment_id)
    rows = (await session.execute(stmt)).scalars().all()
    if not rows:
        detail = f"attachment={attachment_id}" if attachment_id is not None else "attachment=*"
        return (
            False,
            f"脚本未经平台登记或项目/附件归属不符（项目 {project_identifier}，{detail}，"
            f"hash={script_hash[:12]}...）：请先调用 save_web_test_script 保存并登记（effective）后再执行",
        )
    return True, "ok"


async def authorize_script_execution(
    project_identifier: str,
    script_path: Path,
    workspace_root: Path,
    session_factory: Callable[[], Awaitable[AsyncSession]],
    sub_function_ids: list[UUID] | None,
) -> tuple[bool, str]:
    """执行前授权门（rev22 严格模式，无降级回退）：
    - sub_function_ids **必填**（空 → 拒绝）；
    - 每个子功能的**当前附件**都必须解析成功，并与 真实项目 + 当前哈希 绑定；
    - 任一子功能附件缺失/解析失败/哈希不符/项目不符 → 终局拒绝。"""
    if not sub_function_ids:
        return (
            False,
            "严格模式要求 sub_function_id/sub_function_ids：脚本执行必须绑定子功能当前附件（三要素：项目+附件+哈希）",
        )
    resolved, reason = resolve_within_workspace(script_path, workspace_root)
    if resolved is None:
        return False, reason
    try:
        script_hash = sha256_hex_file(resolved)
    except OSError as e:
        return False, f"读取脚本内容失败: {e}"
    async with session_factory() as session:
        for sf_id in sub_function_ids:
            try:
                attachment_id = await resolve_current_script_attachment(session, sf_id)
            except Exception as e:
                return False, f"解析子功能 {sf_id} 当前脚本附件失败: {e}"
            if attachment_id is None:
                return (
                    False,
                    f"子功能 {sf_id} 无当前脚本附件，无法建立三要素绑定"
                    "（请先 save_web_test_script 保存并登记后再执行）",
                )
            ok, reason = await check_script_registered(
                session, project_identifier, script_hash, attachment_id
            )
            if not ok:
                return False, f"子功能 {sf_id} 授权失败: {reason}"
    return True, "ok"


__all__ = [
    "authorize_script_execution",
    "check_script_registered",
    "register_script_provenance",
    "resolve_current_script_attachment",
    "resolve_real_project_identifier",
    "resolve_within_workspace",
    "sha256_hex",
    "sha256_hex_file",
]
