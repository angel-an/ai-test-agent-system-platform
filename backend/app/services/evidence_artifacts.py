"""
失败证据归档：把执行目录里的 trace / 截图上传对象存储，产出**可直接点开**的链接。

背景（2026-10-28）：缺陷描述里的"测试报告地址"写的是
``/api/v2/test-runs/{run_id}/report`` —— 相对路径 + 需要平台登录态，
拿到工单的人点不开；Playwright trace 更是随执行目录一起被删（``TemporaryDirectory``
退出即清空），等于证据本来就没留下。

两条硬约束（实测得出，别轻易改）：
1. 上传必须发生在 ``with TemporaryDirectory()`` **之内** —— 目录一销毁 trace 就没了；
2. 链接必须走**预签名**：桶是私有的，裸对象地址匿名访问是 403。
   实测预签名 URL 匿名 GET 返回 200（``http://<minio_endpoint>/<bucket>/<key>?X-Amz-…``），
   但 S3 协议上限 **7 天**，所以描述里必须写明有效期，不能让人半个月后点开是 403。
"""

from __future__ import annotations

import logging
from datetime import timedelta
from pathlib import Path
from typing import Optional
from uuid import UUID

from app.config.minio_client import MinIOClient

logger = logging.getLogger(__name__)

#: 对象名前缀。与报告（``api-test-reports/``）分开，便于按类型设生命周期。
EVIDENCE_PREFIX = "api-test-evidence"
#: 预签名有效期（S3 协议硬上限 7 天）。
PRESIGN_DAYS = 7
#: 单次运行最多归档多少个证据文件（trace 可能按用例各一个，防止把目录整个搬上云）。
MAX_EVIDENCE_FILES = 10
#: 单个文件上限：trace 含截图/网络体时容易上到几十 MB，超限直接跳过并记录。
MAX_EVIDENCE_BYTES = 50 * 1024 * 1024

#: 收集规则：Playwright 失败时把 ``trace.zip`` 落到**产物目录**；浏览器用例还会带 ``*.png``。
#: ⚠️ 平台执行时用的是 ``--output=<work_dir>/allure-results``（见
#: ``APITestExecutor._run_playwright_process``），trace 因此落在 **allure-results/**，
#: 不是默认的 test-results/（2026-10-28 实测：只扫 test-results 会一个证据都收不到）。
#: 两个目录都扫，兼容将来去掉 ``--output`` 的情况。
_EVIDENCE_ROOTS = ("allure-results", "test-results")
_EVIDENCE_SUFFIXES = (".zip", ".png")


def collect_evidence_files(work_dir: Path | str) -> list[Path]:
    """收集执行目录里值得归档的证据文件（按路径排序，保证链接顺序稳定）。"""
    root_dir = Path(work_dir)
    found: list[Path] = []
    for sub in _EVIDENCE_ROOTS:
        root = root_dir / sub
        if not root.exists():
            continue
        for path in sorted(root.rglob("*")):
            if not path.is_file():
                continue
            if path.suffix.lower() not in _EVIDENCE_SUFFIXES:
                continue
            # 只认 trace.zip，不把 allure 的其它 zip 附件混进来
            if path.suffix.lower() == ".zip" and path.name != "trace.zip":
                continue
            found.append(path)
            if len(found) >= MAX_EVIDENCE_FILES:
                return found
    return found


def _kind_of(path: Path) -> str:
    return "trace" if path.suffix.lower() == ".zip" else "screenshot"


def _object_name(run_id: UUID | str, path: Path) -> str:
    """对象名带 run_id 前缀，同名不同用例靠父目录区分。"""
    parent = path.parent.name or "root"
    return f"{EVIDENCE_PREFIX}/{run_id}/{parent}/{path.name}"


def presigned_url_for(object_name: str, days: int = PRESIGN_DAYS) -> Optional[str]:
    """给已有对象生成预签名下载地址；失败返回 None（**不返回坏链接**）。"""
    if not object_name:
        return None
    try:
        return MinIOClient.get_presigned_url(object_name, expires=timedelta(days=days))
    except Exception as exc:  # noqa: BLE001 —— 预签名失败不该影响执行主链路
        logger.warning("[Evidence] 生成预签名链接失败 %s: %s", object_name, exc)
        return None


def upload_run_evidence(
    run_id: UUID | str,
    work_dir: Path | str,
    *,
    days: int = PRESIGN_DAYS,
) -> dict:
    """上传本次运行的失败证据并返回链接。

    返回::

        {
          "items": [{"kind": "trace", "name": "...", "object_name": "...",
                     "url": "http://...", "size": 1234}],
          "trace_url": "..." | None,
          "screenshot_url": "..." | None,
          "expires_days": 7,
        }

    任何一步失败都只记日志、返回已拿到的部分（**不抛异常**）——证据归档是诊断增强，
    不能因为它把执行结果/缺陷登记整体拖挂。
    """
    items: list[dict] = []
    try:
        for path in collect_evidence_files(work_dir):
            try:
                size = path.stat().st_size
                if size <= 0 or size > MAX_EVIDENCE_BYTES:
                    logger.warning(
                        "[Evidence] 跳过证据文件（大小 %s 字节，上限 %s）: %s",
                        size, MAX_EVIDENCE_BYTES, path,
                    )
                    continue
                object_name = _object_name(run_id, path)
                MinIOClient.upload_bytes(
                    object_name=object_name,
                    data=path.read_bytes(),
                    content_type=(
                        "application/zip" if path.suffix.lower() == ".zip" else "image/png"
                    ),
                )
                url = presigned_url_for(object_name, days=days)
                items.append(
                    {
                        "kind": _kind_of(path),
                        "name": path.name,
                        "object_name": object_name,
                        "url": url,
                        "size": size,
                    }
                )
            except Exception as exc:  # noqa: BLE001 —— 单个文件失败不影响其他文件
                logger.warning("[Evidence] 上传证据文件失败 %s: %s", path, exc)
    except Exception as exc:  # noqa: BLE001
        logger.warning("[Evidence] 证据归档整体失败 run_id=%s: %s", run_id, exc)

    def _first(kind: str) -> Optional[str]:
        for item in items:
            if item["kind"] == kind and item.get("url"):
                return item["url"]
        return None

    return {
        "items": items,
        "trace_url": _first("trace"),
        "screenshot_url": _first("screenshot"),
        "expires_days": days,
    }


def evidence_url_map(evidence: dict | None) -> dict[str, str]:
    """把归档结果压成描述渲染用的 ``{类别: 链接}``（多张截图合并成一条）。

    trace 优先取 ``trace_url``，缺失时回落到 items（调用方自己拼 dict 时常见）。
    """
    if not evidence:
        return {}
    items = evidence.get("items", [])
    out: dict[str, str] = {}
    trace_url = evidence.get("trace_url") or next(
        (i["url"] for i in items if i.get("kind") == "trace" and i.get("url")), None
    )
    if trace_url:
        out["trace"] = trace_url
    shot_urls = [
        item["url"] for item in items
        if item["kind"] == "screenshot" and item.get("url")
    ]
    if shot_urls:
        out["screenshot"] = shot_urls[0]
        for idx, url in enumerate(shot_urls[1:], start=2):
            out[f"screenshot_{idx}"] = url
    return out
