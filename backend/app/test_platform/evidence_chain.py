# -*- coding: utf-8 -*-
"""P2-3: 统一证据链 — Web 截图/API 返回体/DB 流水合并为同一份结论。

目标：让 Web 和 API 的证据能合并成同一份结论，证据项完整，结论可追溯。
"""

from __future__ import annotations

import json
import zipfile
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional


# ---------------------------------------------------------------------------
# Web 证据
# ---------------------------------------------------------------------------

@dataclass
class WebEvidence:
    """Web 测试证据"""

    screenshots: list[str] = field(default_factory=list)
    self_reflect_path: Optional[str] = None
    page_details: Optional[dict[str, Any]] = None
    console_logs: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "type": "web",
            "screenshots": self.screenshots,
            "self_reflect_path": self.self_reflect_path,
            "page_details": self.page_details,
            "console_logs": self.console_logs,
        }


# ---------------------------------------------------------------------------
# API 证据
# ---------------------------------------------------------------------------

@dataclass
class ApiEvidence:
    """API 测试证据"""

    request_summary: Optional[dict[str, Any]] = None
    response_body: Optional[dict[str, Any]] = None
    status_code: Optional[int] = None
    db_records: list[dict[str, Any]] = field(default_factory=list)
    transaction_logs: list[str] = field(default_factory=list)
    pytest_report_path: Optional[str] = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "type": "api",
            "request_summary": self.request_summary,
            "response_body": self.response_body,
            "status_code": self.status_code,
            "db_records": self.db_records,
            "transaction_logs": self.transaction_logs,
            "pytest_report_path": self.pytest_report_path,
        }


# ---------------------------------------------------------------------------
# 统一证据
# ---------------------------------------------------------------------------

@dataclass
class UnifiedEvidence:
    """统一证据 — 合并 Web + API 证据"""

    run_id: str
    web: Optional[WebEvidence] = None
    api: Optional[ApiEvidence] = None
    timestamp: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "timestamp": self.timestamp,
            "web": self.web.to_dict() if self.web else None,
            "api": self.api.to_dict() if self.api else None,
            "metadata": self.metadata,
        }

    def has_web_evidence(self) -> bool:
        return self.web is not None and bool(self.web.screenshots or self.web.self_reflect_path)

    def has_api_evidence(self) -> bool:
        return self.api is not None and bool(self.api.response_body is not None or self.api.db_records)

    def summary(self) -> dict[str, Any]:
        """证据摘要"""
        return {
            "run_id": self.run_id,
            "has_web": self.has_web_evidence(),
            "has_api": self.has_api_evidence(),
            "screenshot_count": len(self.web.screenshots) if self.web else 0,
            "db_record_count": len(self.api.db_records) if self.api else 0,
            "timestamp": self.timestamp,
        }


# ---------------------------------------------------------------------------
# 证据构建
# ---------------------------------------------------------------------------

def build_web_evidence(
    output_dir: str,
    self_reflect_result: Optional[dict[str, Any]] = None,
) -> WebEvidence:
    """从输出目录构建 Web 证据"""
    from pathlib import Path

    path = Path(output_dir)
    screenshots = []

    # 收集截图
    screenshots_dir = path / "screenshots"
    if screenshots_dir.exists():
        screenshots = sorted(str(p.relative_to(path)) for p in screenshots_dir.glob("*.png"))
    else:
        # 根目录兜底
        screenshots = sorted(str(p.relative_to(path)) for p in path.glob("*.png"))

    self_reflect_path = None
    sr_file = path / "self_reflect_result.json"
    if sr_file.exists():
        self_reflect_path = str(sr_file)

    return WebEvidence(
        screenshots=screenshots,
        self_reflect_path=self_reflect_path,
    )


def build_api_evidence(
    response_body: Optional[dict[str, Any]] = None,
    status_code: Optional[int] = None,
    db_records: Optional[list[dict[str, Any]]] = None,
    pytest_report_path: Optional[str] = None,
) -> ApiEvidence:
    """构建 API 证据"""
    return ApiEvidence(
        response_body=response_body,
        status_code=status_code,
        db_records=db_records or [],
        pytest_report_path=pytest_report_path,
    )


# ---------------------------------------------------------------------------
# 证据打包
# ---------------------------------------------------------------------------

def build_evidence_package(
    run_id: str,
    web_ev: Optional[WebEvidence] = None,
    api_ev: Optional[ApiEvidence] = None,
    output_dir: Optional[str] = None,
    metadata: Optional[dict[str, Any]] = None,
    project: Optional[str] = None,
) -> Path:
    """打包统一证据为 ZIP 文件。

    Args:
        run_id: 运行标识
        web_ev: Web 证据
        api_ev: API 证据
        output_dir: 输出目录（默认使用 DirectoryLayout）
        metadata: 元数据
        project: 项目标识符（用于项目隔离目录）

    Returns:
        ZIP 文件路径
    """
    from app.test_platform.directory_layout import DirectoryLayout

    if output_dir:
        evidence_dir = Path(output_dir)
        evidence_dir.mkdir(parents=True, exist_ok=True)
    else:
        layout = DirectoryLayout()
        evidence_dir = layout.evidence_dir(run_id, project=project)

    # 创建统一证据
    unified = UnifiedEvidence(
        run_id=run_id,
        web=web_ev,
        api=api_ev,
        metadata=metadata or {},
    )

    # 写入 evidence.json
    evidence_json = evidence_dir / "evidence.json"
    evidence_json.write_text(
        json.dumps(unified.to_dict(), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    # 打包 ZIP
    zip_path = evidence_dir / f"{run_id}_evidence.zip"
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.write(evidence_json, "evidence.json")

        if web_ev:
            # 截图路径相对于 evidence_dir 解析
            for ss in web_ev.screenshots:
                ss_path = evidence_dir / ss
                if ss_path.exists():
                    zf.write(ss_path, f"screenshots/{ss_path.name}")
                else:
                    # 尝试绝对路径或相对于工作目录
                    alt_path = Path(ss)
                    if alt_path.exists():
                        zf.write(alt_path, f"screenshots/{alt_path.name}")
            if web_ev.self_reflect_path:
                sr_path = Path(web_ev.self_reflect_path)
                if sr_path.exists():
                    zf.write(sr_path, "self_reflect_result.json")

        if api_ev:
            if api_ev.pytest_report_path:
                pr_path = Path(api_ev.pytest_report_path)
                if pr_path.exists():
                    zf.write(pr_path, f"reports/{pr_path.name}")
            if api_ev.db_records:
                db_json = evidence_dir / "db_records.json"
                db_json.write_text(
                    json.dumps(api_ev.db_records, ensure_ascii=False, indent=2),
                    encoding="utf-8",
                )
                zf.write(db_json, "db_records.json")

    return zip_path
