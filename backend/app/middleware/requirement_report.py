"""需求分析报告自动落盘中间件。

监听 model 节点完成后的最新 AIMessage，若其中包含 ``## Step 1: 需求理解``
段落，就把该段切片保存为 Markdown 文件并登记到数据库，供前端列表展示与下载。

设计要点：
- 用 ``after_model`` 钩子，不消耗 LLM token、不依赖 LLM 主动调工具。
- 通过 ``thread_id`` 级别去重：同一会话内 Step 1 内容若被反复输出（例如用户
  说"重新分析需求"），按内容哈希判断是否变化，未变化则跳过。
- 落盘失败、入库失败均不阻断主流程，只记 warning 日志。
"""

from __future__ import annotations

import hashlib
import logging
import re
import threading
from datetime import datetime
from pathlib import Path
from typing import Any

from langchain.agents.middleware import AgentMiddleware
from langchain_core.messages import AIMessage, HumanMessage

logger = logging.getLogger(__name__)

# 需求分析报告保存目录 — 与 API 扫描路径保持一致
# API 在 backend/app/api/v2/test_cases.py 中扫描以下路径：
#   {api_workspace_root}/reports/requirement-analysis/{project_identifier}/
# 这里使用相同的目录结构，确保文件能被 API 正确读取
_EXPORTS_DIR = Path(__file__).resolve().parent.parent.parent / "workspace" / "api" / "reports" / "requirement-analysis"
_EXPORTS_DIR.mkdir(parents=True, exist_ok=True)

# Step 1 段落起止锚点 — 与 agent.py SYSTEM_PROMPT 中的标题保持一致
_STEP1_HEADING = re.compile(r"##\s*Step\s*1[:\s]*需求理解", re.IGNORECASE)
_NEXT_STEP_HEADING = re.compile(r"##\s*Step\s*2", re.IGNORECASE)

# Step 1 强制小节正则字典 — 与 agent.py SYSTEM_PROMPT 中的子步骤保持一致
_STEP1_SUBSECTIONS = {
    "Step 1a": re.compile(r"###\s*Step\s*1a[:\s]*文档结构确认", re.IGNORECASE),
    "Step 1b": re.compile(r"###\s*Step\s*1b[:\s]*功能点提取", re.IGNORECASE),
    "Step 1c": re.compile(r"###\s*Step\s*1c[:\s]*需求识别", re.IGNORECASE),
    "Step 1d": re.compile(r"###\s*Step\s*1d[:\s]*范围声明", re.IGNORECASE),
    "Step 1e": re.compile(r"###\s*Step\s*1e[:\s]*用例预估", re.IGNORECASE),
    "Step 1.5": re.compile(r"###\s*Step\s*1\.5[:\s]*自检", re.IGNORECASE),
    "待澄清问题清单": re.compile(r"###\s*待澄清问题清单", re.IGNORECASE),
    "功能测试矩阵": re.compile(r"###\s*功能测试矩阵|###\s*📋\s*功能测试矩阵", re.IGNORECASE),
}

# 文件名安全字符过滤（与 Excel 导出风格一致，保留中英文/数字/常见分隔符）
_INVALID_FILENAME_CHARS = re.compile(r'[\\/:*?"<>|\r\n\t]+')

# 进程级去重缓存：{thread_id: last_step1_hash} (简单 dict，maxsize 200 个键)
_lock = threading.Lock()
_seen: dict[str, str] = {}
_MAX_CACHE_SIZE = 200


def _extract_step1_section(text: str) -> tuple[str | None, list[str]]:
    """从一段文本中切出 ``## Step 1: 需求理解`` 到 ``## Step 2`` 之间的内容。

    若文本不包含 Step 1 标题，返回 (None, [])。若 Step 1 后没有 Step 2，则切到文末。
    同时校验是否包含所有强制小节，返回缺失的小节列表。
    """
    if not text:
        return None, []
    m1 = _STEP1_HEADING.search(text)
    if not m1:
        return None, []
    start = m1.start()
    m2 = _NEXT_STEP_HEADING.search(text, m1.end())
    end = m2.start() if m2 else len(text)
    section = text[start:end].rstrip()
    # 太短的不当作有效报告（防止只输出标题就触发保存）
    if len(section) < 200:
        return None, []

    # 校验强制小节
    missing = [name for name, pat in _STEP1_SUBSECTIONS.items() if not pat.search(section)]

    return section, missing


def _ai_message_text(msg: AIMessage) -> str:
    """把 AIMessage.content 拍平为字符串，兼容 list[block] 形式。"""
    content = msg.content
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for block in content:
            if isinstance(block, str):
                parts.append(block)
            elif isinstance(block, dict):
                t = block.get("text")
                if isinstance(t, str):
                    parts.append(t)
        return "\n".join(parts)
    return ""


def _infer_requirement_name(section: str, messages: list[Any]) -> str:
    """提取需求名称作为文件名前缀。

    规则（按优先级）：
    1. 用户消息附件的文件名（去后缀）—— 最能代表本次需求的标识；
      从 ``msg.additional_kwargs['attachments'][*]['metadata']['filename']`` 读取。
    2. Step 1 报告段落中显式字段（``需求名称：``、``项目名称：`` 等）。
    3. 兜底使用占位符 ``需求名称``。

    不做用户消息正文的 NLP 模糊抽取。
    """
    # 1) 从 HumanMessage 的 attachments 取文件名（最近一次上传优先）
    for msg in reversed(messages):
        if not isinstance(msg, HumanMessage):
            continue
        atts = getattr(msg, "additional_kwargs", {}) or {}
        atts = atts.get("attachments") if isinstance(atts, dict) else None
        if not isinstance(atts, list):
            continue
        for att in atts:
            if not isinstance(att, dict):
                continue
            metadata = att.get("metadata") if isinstance(att.get("metadata"), dict) else {}
            fname = (metadata.get("filename") or metadata.get("name") or "").strip()
            if not fname:
                continue
            # 去掉后缀，保留主名
            stem = Path(fname).stem.strip()
            if stem and stem.lower() != "unknown":
                return stem[:30]

    # 2) 从 Step 1 段落抽显式字段
    if section:
        m = re.search(
            r"\*{0,2}\s*(?:需求名称|需求标题|项目名称|功能名称|模块名称)\s*\*{0,2}\s*[：:]\s*\*{0,2}\s*([^\n*：:]+)",
            section,
        )
        if m:
            cand = m.group(1).strip().strip("*").strip()
            if cand:
                return cand[:30]

    return "需求名称"


def _safe_filename(name: str) -> str:
    """剔除文件系统不允许的字符。"""
    cleaned = _INVALID_FILENAME_CHARS.sub("_", name).strip()
    return cleaned or "需求分析"


def _persist_report(
    thread_id: str,
    section: str,
    requirement_name: str,
    missing_subsections: list[str],
    project_identifier: str = "",
) -> str | None:
    """把 Step 1 段落写到 .md 文件并登记数据库。返回文件名，失败返回 None。"""
    section_hash = hashlib.md5(section.encode("utf-8")).hexdigest()
    with _lock:
        if _seen.get(thread_id) == section_hash:
            return None
        # 简单 LRU：超过大小时清空一半
        if len(_seen) >= _MAX_CACHE_SIZE:
            # 保留最近的一半
            keys = list(_seen.keys())
            for k in keys[: len(keys) // 2]:
                del _seen[k]
        _seen[thread_id] = section_hash

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    safe_req = _safe_filename(requirement_name)
    filename = f"{safe_req}_需求分析报告_{timestamp}.md"
    output_path = _EXPORTS_DIR / filename

    # 构建文件内容：元数据头部 + 警告（如有）+ 原始内容
    content_to_write = section

    # 在文件顶部注入元数据（帮助 API 识别项目归属）
    metadata_header = f"""---
project: {project_identifier or "unknown"}
requirement_name: {safe_req}
generated_at: {datetime.now().isoformat()}
thread_id: {thread_id}
---

"""
    content_to_write = metadata_header + content_to_write

    # 若存在缺失小节，在保存内容顶部注入警告标记（不阻断落盘）
    if missing_subsections:
        warn_header = (
            f"\n\n> ⚠️ [格式校验警告] LLM 未完整输出以下强制小节，"
            f"报告可能存在结构性缺陷：{', '.join(missing_subsections)}\n\n"
        )
        content_to_write = warn_header + content_to_write
        logger.warning(
            "[RequirementReport] 报告 %s 缺失强制小节: %s",
            filename,
            missing_subsections,
        )

    try:
        output_path.write_text(content_to_write, encoding="utf-8")
    except Exception:
        logger.exception("[RequirementReport] 写入 %s 失败", filename)
        return None

    # 尝试登记到数据库（如果存在该功能）
    try:
        from app.core.database import save_requirement_report

        save_requirement_report(filename, safe_req)
    except ImportError:
        # 数据库模块不存在，仅落盘文件即可
        pass
    except Exception as e:
        logger.warning("[RequirementReport] 报告已落盘但入库失败: %s", e)

    logger.info("[RequirementReport] 已保存需求分析报告 %s", filename)
    return filename


class RequirementReportMiddleware(AgentMiddleware):
    """在 model 节点完成后扫描最新 AIMessage，命中 Step 1 段落则落盘。"""

    def __init__(self) -> None:
        super().__init__()
        logger.info("[RequirementReport] middleware loaded")

    def _handle(self, state: Any, runtime: Any) -> None:
        messages = state.get("messages") if isinstance(state, dict) else getattr(state, "messages", None)
        if not messages:
            return

        # 取最近一条 AIMessage
        latest_ai: AIMessage | None = None
        for msg in reversed(messages):
            if isinstance(msg, AIMessage):
                latest_ai = msg
                break
        if latest_ai is None:
            return

        text = _ai_message_text(latest_ai)
        section, missing = _extract_step1_section(text)
        if not section:
            return

        # thread_id 用于会话级去重，取不到则用 messages 长度作弱回退
        thread_id = ""
        project_identifier = ""
        try:
            cfg = getattr(runtime, "config", None) or {}
            configurable = cfg.get("configurable") if isinstance(cfg, dict) else None
            if isinstance(configurable, dict):
                thread_id = configurable.get("thread_id") or ""
                project_identifier = configurable.get("project_identifier") or ""
        except Exception:
            thread_id = ""
        if not thread_id:
            thread_id = f"_anon_{len(messages)}"

        requirement_name = _infer_requirement_name(section, messages)
        _persist_report(thread_id, section, requirement_name, missing, project_identifier)

    def after_model(self, state, runtime) -> Any:  # noqa: ARG002, ANN001
        try:
            self._handle(state, runtime)
        except Exception:
            logger.exception("[RequirementReport] after_model 异常（已忽略，不影响主流程）")
        return None

    async def aafter_model(self, state, runtime) -> Any:  # noqa: ARG002, ANN001
        return self.after_model(state, runtime)

    def wrap_model_call(self, request, handler):
        return handler(request)

    async def awrap_model_call(self, request, handler):
        return await handler(request)
