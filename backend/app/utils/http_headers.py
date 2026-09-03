"""
HTTP 响应头构造工具

提供文件下载相关的辅助函数，统一处理中文文件名编码（RFC 5987/6266）。
"""

from __future__ import annotations

import re
import urllib.parse


_TOKEN_SAFE_RE = re.compile(r'[\\/:*?"<>|\r\n\t]')


def _ascii_fallback(filename: str) -> str:
    """生成 ASCII 兼容的文件名回退值。

    对于无法用 ASCII 表示的字符（如中文），替换为下划线，
    避免老旧客户端解析 Content-Disposition 时报错。
    """
    cleaned = _TOKEN_SAFE_RE.sub("_", filename)
    encoded = cleaned.encode("ascii", errors="replace").decode("ascii")
    encoded = encoded.replace("?", "_").replace('"', "_")
    return encoded or "download"


def build_content_disposition(filename: str, disposition: str = "attachment") -> str:
    """构造 RFC 6266 兼容的 Content-Disposition 头。

    同时提供 ``filename`` (ASCII 回退) 与 ``filename*`` (UTF-8 编码)
    两种参数，确保中文文件名在主流浏览器中均可正确显示。

    Args:
        filename: 原始文件名，可包含中文等非 ASCII 字符。
        disposition: 处置类型，默认为 ``attachment``。

    Returns:
        组装好的 Content-Disposition 响应头字符串。
    """
    if not filename:
        filename = "download"

    fallback = _ascii_fallback(filename)
    quoted = urllib.parse.quote(filename, safe="")

    return (
        f'{disposition}; filename="{fallback}"; '
        f"filename*=UTF-8''{quoted}"
    )
