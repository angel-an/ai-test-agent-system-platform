"""从 Playwright trace 附件里提取真实的 HTTP 请求/响应。

为什么需要它：Playwright 的 **JSON 报告里没有 request/response 结构**
（2026-10-27 实测 ``results[]`` 只有 workerIndex/status/error/stdout/attachments…），
历史代码却按 ``result["request"]["url"]`` 取值 —— 永远取不到，于是「有完整证据」
恒为 False，失败自动建单从未发生。

trace 附件（``application/zip``）里的 ``*-trace.network`` 是 **HAR 风格**的
网络快照流（每行一个 JSON，``type: resource-snapshot``），含完整的
``request.method/url/headers/postData`` 与 ``response.status/headers/content``。
这是不修改脚本就能拿到真实证据的唯一途径。

开启方式：Playwright 配置里 ``use.trace = 'retain-on-failure'``。
失败用例才落盘，通过用例零额外开销；对纯 API（``request`` fixture）用例同样生效
（已实测：config 无 browser 项目时也能录到网络快照）。
"""

from __future__ import annotations

import json
import logging
import zipfile
from dataclasses import dataclass, field
from typing import Any, Optional
from urllib.parse import urlsplit

logger = logging.getLogger(__name__)

#: 响应体入库上限。缺陷描述要的是"能看出是什么错"，不是完整报文；
#: 大响应（列表查询）原样塞进去只会把描述撑爆。
MAX_BODY_BYTES = 8192

#: 静态资源后缀 —— trace 在带浏览器的项目里会录到页面资源，它们不是被测接口。
_STATIC_SUFFIXES = (
    ".js", ".mjs", ".css", ".map", ".png", ".jpg", ".jpeg", ".gif",
    ".svg", ".ico", ".woff", ".woff2", ".ttf", ".html", ".htm",
)


@dataclass
class HttpExchange:
    """一次真实的 HTTP 往返。"""

    method: str
    url: str
    status_code: Optional[int] = None
    request_headers: dict[str, str] = field(default_factory=dict)
    request_body: Any = None
    response_headers: dict[str, str] = field(default_factory=dict)
    response_body: Any = None
    duration_ms: Optional[float] = None
    started_at: str = ""

    @property
    def path(self) -> str:
        """URL 的 path 部分（defect 描述里的「接口」字段）。"""
        try:
            return urlsplit(self.url).path or self.url
        except ValueError:
            return self.url

    @property
    def query(self) -> str:
        try:
            return urlsplit(self.url).query
        except ValueError:
            return ""


def _headers_to_dict(raw: Any) -> dict[str, str]:
    """HAR 的 headers 是 ``[{name, value}]`` 数组，转成小写键的 dict。"""
    result: dict[str, str] = {}
    if isinstance(raw, list):
        for item in raw:
            if not isinstance(item, dict):
                continue
            name = str(item.get("name") or "").strip()
            if name:
                result[name.lower()] = str(item.get("value") or "")
    elif isinstance(raw, dict):
        result = {str(k).lower(): str(v) for k, v in raw.items()}
    return result


def _decode_body(raw: bytes) -> Any:
    """字节 → JSON（能解则解）/ 文本。"""
    try:
        text = raw.decode("utf-8", errors="replace")
    except Exception:  # pragma: no cover - decode with replace 不会抛
        return None
    try:
        return json.loads(text)
    except (ValueError, TypeError):
        return text


def _resolve_resource_name(zf: zipfile.ZipFile, content: dict[str, Any]) -> Optional[str]:
    """定位 ``response.content`` 对应的 zip 内资源文件。

    Playwright 不同版本写 trace 的字段名不一样，实测到的两种形态：
    - ``_file``：值就是完整相对路径（``resources/<sha>.json``）；
    - ``_sha1``：值只是资源哈希，文件在 ``resources/`` 下（有的版本带扩展名，
      有的不带）。**平台执行的版本走的是 ``_sha1``** —— 只认 ``_file`` 会让
      响应体永远读成空（2026-10-27 实测）。
    """
    file_name = content.get("_file")
    if isinstance(file_name, str) and file_name:
        try:
            zf.getinfo(file_name)
            return file_name
        except KeyError:
            pass
    sha = content.get("_sha1")
    if isinstance(sha, str) and sha:
        for candidate in (f"resources/{sha}", sha, f"resources/{sha}.json"):
            try:
                zf.getinfo(candidate)
                return candidate
            except KeyError:
                continue
    return None


def _read_content(zf: zipfile.ZipFile, content: Any) -> Any:
    """读取 HAR ``response.content``。

    大响应写在 zip 内的资源文件里（``_file`` 或 ``_sha1`` 指向），
    小响应可能内联在 ``content.text``。
    """
    if not isinstance(content, dict):
        return None
    try:
        size = int(content.get("size") or 0)
    except (TypeError, ValueError):
        size = 0

    resource = _resolve_resource_name(zf, content)
    if resource:
        try:
            if size > MAX_BODY_BYTES:
                with zf.open(resource) as fh:
                    return _truncate(_decode_body(fh.read(MAX_BODY_BYTES)))
            with zf.open(resource) as fh:
                return _decode_body(fh.read())
        except KeyError:
            return None
        except Exception:  # pragma: no cover
            return None

    text = content.get("text")
    if isinstance(text, str):
        if size > MAX_BODY_BYTES:
            return _truncate(text)
        try:
            return json.loads(text)
        except (ValueError, TypeError):
            return text
    return None


def _truncate(value: Any) -> Any:
    if isinstance(value, str) and len(value) > MAX_BODY_BYTES:
        return value[:MAX_BODY_BYTES] + "...(truncated)"
    return value


def _is_static(url: str) -> bool:
    path = urlsplit(url).path.lower() if url else ""
    return path.endswith(_STATIC_SUFFIXES)


def _snapshot_to_exchange(snapshot: dict[str, Any]) -> Optional[HttpExchange]:
    request = snapshot.get("request") or {}
    response = snapshot.get("response") or {}
    if not isinstance(request, dict):
        return None
    url = str(request.get("url") or "")
    method = str(request.get("method") or "").upper()
    if not url or not method:
        return None
    if not url.startswith(("http://", "https://")):
        return None

    status: Optional[int]
    try:
        status = int(response.get("status")) if response.get("status") is not None else None
    except (TypeError, ValueError):
        status = None

    post_data = request.get("postData")
    request_body: Any = post_data
    if isinstance(post_data, dict):
        request_body = post_data.get("text", post_data)
    if isinstance(request_body, str) and request_body:
        try:
            request_body = json.loads(request_body)
        except (ValueError, TypeError):
            pass

    duration: Optional[float]
    try:
        duration = float(snapshot.get("time")) if snapshot.get("time") is not None else None
    except (TypeError, ValueError):
        duration = None

    return HttpExchange(
        method=method,
        url=url,
        status_code=status,
        request_headers=_headers_to_dict(request.get("headers")),
        request_body=request_body,
        response_headers=_headers_to_dict(response.get("headers")),
        duration_ms=duration,
        started_at=str(snapshot.get("startedDateTime") or ""),
        # response_body 由调用方带 zipfile 读取（需要打开 zip）
    )


def extract_last_exchange(trace_path: str) -> Optional[HttpExchange]:
    """从 trace.zip 里取出**最后一次**非静态资源的 HTTP 往返。

    一个用例可能打多个接口（登录 + 目标接口），trace 按时间顺序排列，
    取最后一条最接近「出问题的那个请求」。解析失败一律返回 None，
    由调用方降级 —— 证据提取绝不能拖挂整次执行。
    """
    if not trace_path:
        return None
    try:
        with zipfile.ZipFile(trace_path) as zf:
            names = [n for n in zf.namelist() if n.endswith("-trace.network")]
            if not names:
                return None
            entries: list[tuple[str, dict[str, Any]]] = []
            for name in names:
                try:
                    raw = zf.read(name).decode("utf-8", errors="replace")
                except KeyError:  # pragma: no cover
                    continue
                for line in raw.splitlines():
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        event = json.loads(line)
                    except (ValueError, TypeError):
                        continue
                    if not isinstance(event, dict):
                        continue
                    if event.get("type") != "resource-snapshot":
                        continue
                    snapshot = event.get("snapshot")
                    if isinstance(snapshot, dict):
                        entries.append((str(snapshot.get("startedDateTime") or ""), snapshot))

            for _, snapshot in reversed(entries):
                exchange = _snapshot_to_exchange(snapshot)
                if exchange is None or _is_static(exchange.url):
                    continue
                exchange.response_body = _read_content(zf, snapshot.get("response", {}).get("content"))
                return exchange
            return None
    except (zipfile.BadZipFile, OSError) as exc:
        logger.warning("[TraceEvidence] trace 附件不可读（%s）: %s", trace_path, exc)
        return None
    except Exception as exc:  # pragma: no cover - 兜底，绝不影响执行
        logger.warning("[TraceEvidence] 解析 trace 失败: %s", exc, exc_info=True)
        return None


def find_trace_attachment(attachments: Any) -> Optional[str]:
    """在 Playwright ``result["attachments"]`` 里找 trace 附件的本地路径。"""
    if not isinstance(attachments, list):
        return None
    for item in attachments:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or "").lower()
        content_type = str(item.get("contentType") or "").lower()
        path = item.get("path")
        if not isinstance(path, str) or not path:
            continue
        if name == "trace" or "zip" in content_type:
            return path
    return None


def extract_from_result(result: dict[str, Any]) -> Optional[HttpExchange]:
    """从一条 Playwright test result 里提证据（内部找 trace 附件）。"""
    if not isinstance(result, dict):
        return None
    trace_path = find_trace_attachment(result.get("attachments"))
    if not trace_path:
        return None
    return extract_last_exchange(trace_path)
