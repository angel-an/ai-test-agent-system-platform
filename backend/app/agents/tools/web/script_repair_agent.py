"""真实 Agent 自愈编排（执行治理层 P0-2 第 2 阶段）。

- build_llm_fix_generator：deepseek-chat LLM 根据失败上下文生成修复脚本
  （fix_generator 的真实实现；只返回脚本内容，不接触 save 工具——版本不可越权）；
- verify_proposed_script：评审内部验证 proposed 脚本（下载内容 → workspace 冒烟
  执行 → self_reflect 判定），**不经过对外授权门**（对外 execute 仍只认 effective，
  proposed 未发布不可由 agent 执行）。
"""

from __future__ import annotations

from pathlib import Path
from uuid import UUID


async def _read_script_content(session_factory, sub_function_id: UUID) -> str:
    """读取子功能当前（effective）脚本附件内容（MinIO）。"""
    from sqlalchemy import select

    from app.agents.tools.web.script_provenance import resolve_current_script_attachment
    from app.config.minio_client import MinIOClient
    from app.models.attachment import Attachment

    async with session_factory() as session:
        att_id = await resolve_current_script_attachment(session, sub_function_id)
        if att_id is None:
            return ""
        att = (
            await session.execute(select(Attachment).where(Attachment.id == att_id))
        ).scalars().first()
        obj = att.object_name if att else None
    if not obj:
        return ""
    try:
        return MinIOClient.download_file(obj).decode("utf-8", "replace")
    except Exception:
        return ""


def build_llm_fix_generator(model=None):
    """构造真实 Agent 修复生成器（LLM 修复；可注入 model 便于测试 mock）。

    Returns:
        async (sub_function_id: UUID, error_summary: str) -> str（修复后脚本内容）
    """
    from langchain.chat_models import init_chat_model

    llm = model or init_chat_model("deepseek:deepseek-chat")

    async def fix(sub_function_id: UUID, error_summary: str) -> str:
        from app.config.database import async_session_factory

        original = await _read_script_content(async_session_factory, sub_function_id)
        prompt = (
            "你是 Web 测试脚本修复专家。以下脚本执行失败，请根据失败摘要生成"
            "**完整可运行的修复版脚本**（Playwright Python，保持原功能与结构，"
            "修正失败点；输出为纯 Python 代码，不含解释）。\n\n"
            f"失败摘要：\n{error_summary[:1200]}\n\n"
            f"原脚本：\n{original[:6000]}\n\n"
            "修复版脚本（仅代码）："
        )
        resp = await llm.ainvoke(prompt)
        content = getattr(resp, "content", None) or str(resp)
        # 剥离可能的代码围栏
        if content.startswith("```"):
            lines = content.splitlines()
            lines = lines[1:-1] if lines[-1].strip().startswith("```") else lines[1:]
            content = "\n".join(lines)
        return content

    return fix


async def verify_proposed_script(
    session_factory,
    sub_function_id: UUID,
    proposed_attachment_id: str | None,
    timeout: int = 120,
) -> tuple[bool, str]:
    """评审内部验证 proposed 脚本（不经过对外授权门）。

    下载 proposed 附件内容 → 写入临时 workspace → run_with_job 执行 →
    读 self_reflect_result.json 判定。

    Returns:
        (ok, detail)
    """
    import asyncio
    import json
    import os
    import tempfile

    from sqlalchemy import select

    from app.agents.tools.web.process_guard import run_with_job
    from app.config.minio_client import MinIOClient
    from app.models.attachment import Attachment

    if not proposed_attachment_id:
        return False, "无 proposed 附件 ID"
    try:
        async with session_factory() as session:
            att = (
                await session.execute(
                    select(Attachment).where(Attachment.id == UUID(proposed_attachment_id))
                )
            ).scalars().first()
            if att is None:
                return False, "proposed 附件不存在"
            # rev44（评审问题 4）+ rev45（P1 修复）：附件绑定校验——属于指定子功能 +
            # 类型为脚本（用 AttachmentEntityType 枚举比较，ORM 返回枚举对象，
            # 其值为 "web_test_script" 而非大写字符串）+ 存在对应 proposed registry 记录
            if str(att.entity_id) != str(sub_function_id):
                return False, f"附件不属于该子功能 (entity_id={att.entity_id})"
            from app.models.attachment import AttachmentEntityType

            if att.entity_type != AttachmentEntityType.WEB_TEST_SCRIPT:
                return False, f"附件类型非脚本: {att.entity_type}"
            from app.models.web_script_registry import WebScriptRegistry

            reg = (
                await session.execute(
                    select(WebScriptRegistry).where(
                        WebScriptRegistry.attachment_id == att.id,
                        WebScriptRegistry.version_status == "proposed",
                    )
                )
            ).scalars().first()
            if reg is None:
                return False, "该附件无对应 proposed registry 记录（非待发布版本）"
            obj = att.object_name
        content = MinIOClient.download_file(obj).decode("utf-8", "replace")

        tmp_dir = Path(tempfile.mkdtemp(prefix="verify_proposed_"))
        script = tmp_dir / "verify_proposed.py"
        script.write_text(content, encoding="utf-8")
        # run_with_job 为同步函数：to_thread 包装避免阻塞事件循环
        completed = await asyncio.to_thread(
            run_with_job,
            [os.sys.executable, str(script)],
            str(tmp_dir),
            dict(os.environ),
            timeout,
            False,
        )
        # rev44（评审问题 2）：退出码非零**无条件拒绝**（无论自评状态）；
        # 自评采用白名单：仅 passed 放行；failed 拒绝；缺失自评仅在退出码 0 时放行
        if completed.returncode != 0:
            return False, (
                f"验证失败: returncode={completed.returncode}（非零无条件拒绝） "
                f"stderr={(completed.stderr or b'')[:200].decode('utf-8', 'replace')}"
            )
        sr = tmp_dir / "self_reflect_result.json"
        status = None
        if sr.exists():
            try:
                status = json.loads(sr.read_text(encoding="utf-8")).get("execution_status")
            except Exception:
                pass
        if status == "failed":
            return False, "验证失败: 脚本自评 failed"
        if status is not None and status != "passed":
            return False, f"验证失败: 自评状态非白名单 ({status})"
        return True, f"验证通过 (returncode={completed.returncode}, self_reflect={status})"
    except Exception as e:
        return False, f"验证异常: {e}"
