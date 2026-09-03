"""
需求分析报告工具

提供需求分析报告（Markdown）的本地保存能力。
报告默认保存到 workspace/api/reports/requirement-analysis/ 目录。
"""

import re
from datetime import datetime
from pathlib import Path
from typing import Optional

from langchain_core.tools import tool

from app.config.settings import settings


REPORT_SUBDIR = "reports/requirement-analysis"


def _safe_filename(name: str) -> str:
    """将任意字符串转换为安全的文件名片段。"""
    name = name.strip().replace(" ", "_")
    name = re.sub(r"[^\w\-一-鿿.]", "", name)
    return name[:80] or "requirement"


def _resolve_report_dir() -> Path:
    workspace_root = Path(settings.api_workspace_root).resolve()
    report_dir = workspace_root / REPORT_SUBDIR
    report_dir.mkdir(parents=True, exist_ok=True)
    return report_dir


@tool
async def save_requirement_analysis(
    report_content: str,
    title: Optional[str] = None,
    project_identifier: str = "",
    source_file_name: Optional[str] = None,
) -> dict:
    """
    保存需求分析报告（Markdown）到本地工作目录。

    使用场景：当用户上传需求文档并要求"输出需求分析报告 / 分析报告 / md 格式报告"时调用本工具。
    生成测试用例的流程不应调用本工具。

    Args:
        report_content: 完整的 Markdown 报告内容（必须包含报告主体，不要再嵌套代码块）。
        title: 报告标题（用于生成文件名），可选。
        project_identifier: 当前项目标识，可选（用于子目录区分）。
        source_file_name: 原始需求文档的文件名，可选（写入报告头部以便溯源）。

    Returns:
        包含保存路径与摘要信息的字典。
    """
    if not report_content or not report_content.strip():
        return {"success": False, "error": "report_content 不能为空"}

    report_dir = _resolve_report_dir()
    if project_identifier:
        report_dir = report_dir / _safe_filename(project_identifier)
        report_dir.mkdir(parents=True, exist_ok=True)

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    title_part = _safe_filename(title) if title else "requirement_analysis"
    filename = f"{timestamp}_{title_part}.md"
    file_path = report_dir / filename

    header_lines = [
        f"<!-- generated_at: {datetime.now().isoformat(timespec='seconds')} -->",
    ]
    if source_file_name:
        header_lines.append(f"<!-- source: {source_file_name} -->")
    if project_identifier:
        header_lines.append(f"<!-- project: {project_identifier} -->")

    final_content = "\n".join(header_lines) + "\n\n" + report_content.strip() + "\n"

    file_path.write_text(final_content, encoding="utf-8")

    return {
        "success": True,
        "file_path": str(file_path),
        "file_name": filename,
        "directory": str(report_dir),
        "size_bytes": len(final_content.encode("utf-8")),
        "message": f"需求分析报告已保存到 {file_path}",
    }
