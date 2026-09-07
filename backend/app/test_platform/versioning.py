# -*- coding: utf-8 -*-
"""P1-3: 固化场景版本 — 生成结果按版本号留档，版本变更保留差异说明。

目标：避免 prompt 一改就失去对比基线。
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from app.test_platform.directory_layout import DirectoryLayout, NamingConvention


# ---------------------------------------------------------------------------
# 版本记录
# ---------------------------------------------------------------------------

class VersionRecord:
    """单版本记录。"""

    def __init__(
        self,
        version_no: int,
        timestamp: str,
        cases_hash: str,
        cases_count: int,
        diff_from_prev: Optional[list[str]] = None,
        metadata: Optional[dict[str, Any]] = None,
    ):
        self.version_no = version_no
        self.timestamp = timestamp
        self.cases_hash = cases_hash
        self.cases_count = cases_count
        self.diff_from_prev = diff_from_prev or []
        self.metadata = metadata or {}

    def to_dict(self) -> dict[str, Any]:
        return {
            "version_no": self.version_no,
            "timestamp": self.timestamp,
            "cases_hash": self.cases_hash,
            "cases_count": self.cases_count,
            "diff_from_prev": self.diff_from_prev,
            "metadata": self.metadata,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "VersionRecord":
        return cls(
            version_no=d["version_no"],
            timestamp=d["timestamp"],
            cases_hash=d["cases_hash"],
            cases_count=d["cases_count"],
            diff_from_prev=d.get("diff_from_prev", []),
            metadata=d.get("metadata", {}),
        )


# ---------------------------------------------------------------------------
# 版本管理
# ---------------------------------------------------------------------------

class VersionManager:
    """场景版本管理器。"""

    def __init__(self, project: str, workspace_root: Optional[Path | str] = None):
        self.project = project
        self.layout = DirectoryLayout(workspace_root)
        self.versions_dir = self.layout.versions_dir(project)

    def _cases_hash(self, cases: list[dict[str, Any]]) -> str:
        """计算用例列表的哈希（用于快速比对）"""
        canonical = json.dumps(cases, ensure_ascii=False, sort_keys=True)
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:16]

    def _version_file(self, version_no: int) -> Path:
        """版本文件路径"""
        name = NamingConvention.version_filename(self.project, version_no)
        return self.versions_dir / name

    def _manifest_file(self) -> Path:
        """版本清单文件"""
        return self.versions_dir / "manifest.json"

    def _load_manifest(self) -> dict[str, Any]:
        """加载版本清单"""
        mf = self._manifest_file()
        if mf.exists():
            return json.loads(mf.read_text(encoding="utf-8"))
        return {"versions": [], "latest": 0}

    def _save_manifest(self, manifest: dict[str, Any]) -> None:
        """保存版本清单"""
        self._manifest_file().write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def list_versions(self) -> list[VersionRecord]:
        """列出所有版本记录"""
        manifest = self._load_manifest()
        records = []
        for v in manifest.get("versions", []):
            records.append(VersionRecord.from_dict(v))
        return records

    def get_latest_version(self) -> Optional[VersionRecord]:
        """获取最新版本"""
        manifest = self._load_manifest()
        latest_no = manifest.get("latest", 0)
        if latest_no == 0:
            return None
        for v in manifest.get("versions", []):
            if v["version_no"] == latest_no:
                return VersionRecord.from_dict(v)
        return None

    def save_version(
        self,
        cases: list[dict[str, Any]],
        metadata: Optional[dict[str, Any]] = None,
    ) -> VersionRecord:
        """保存新版本。

        Args:
            cases: 用例列表
            metadata: 可选元数据（如 prompt 版本、生成参数等）

        Returns:
            VersionRecord: 保存的版本记录
        """
        manifest = self._load_manifest()
        latest_no = manifest.get("latest", 0)
        new_no = latest_no + 1

        cases_hash = self._cases_hash(cases)
        now = datetime.now(timezone.utc).isoformat()

        # 计算与上一版本的差异
        diff: list[str] = []
        if latest_no > 0:
            prev = self.get_latest_version()
            if prev and prev.cases_hash != cases_hash:
                diff = self._diff_against_prev(cases, latest_no)
            elif prev and prev.cases_hash == cases_hash:
                diff = ["用例内容与上一版本完全一致"]

        record = VersionRecord(
            version_no=new_no,
            timestamp=now,
            cases_hash=cases_hash,
            cases_count=len(cases),
            diff_from_prev=diff,
            metadata=metadata or {},
        )

        # 保存版本文件
        version_file = self._version_file(new_no)
        version_file.write_text(
            json.dumps({
                "record": record.to_dict(),
                "cases": cases,
            }, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

        # 更新清单
        manifest["versions"].append(record.to_dict())
        manifest["latest"] = new_no
        self._save_manifest(manifest)

        return record

    def _diff_against_prev(self, cases: list[dict[str, Any]], prev_version_no: int) -> list[str]:
        """计算与上一版本的差异"""
        prev_file = self._version_file(prev_version_no)
        if not prev_file.exists():
            return ["无法读取上一版本文件"]

        prev_data = json.loads(prev_file.read_text(encoding="utf-8"))
        prev_cases = prev_data.get("cases", [])

        return diff_versions(prev_cases, cases)

    def load_version(self, version_no: int) -> Optional[list[dict[str, Any]]]:
        """加载指定版本的用例"""
        version_file = self._version_file(version_no)
        if not version_file.exists():
            return None
        data = json.loads(version_file.read_text(encoding="utf-8"))
        return data.get("cases", [])


# ---------------------------------------------------------------------------
# 版本差异对比
# ---------------------------------------------------------------------------

def diff_versions(
    old_cases: list[dict[str, Any]],
    new_cases: list[dict[str, Any]],
) -> list[str]:
    """对比两个版本用例的差异。

    Returns:
        差异说明列表（人类可读）
    """
    diffs: list[str] = []

    old_ids = {c.get("id"): c for c in old_cases if c.get("id")}
    new_ids = {c.get("id"): c for c in new_cases if c.get("id")}

    # 新增
    added = [nid for nid in new_ids if nid not in old_ids]
    if added:
        diffs.append(f"新增 {len(added)} 条用例: {', '.join(added[:5])}" + ("..." if len(added) > 5 else ""))

    # 删除
    removed = [oid for oid in old_ids if oid not in new_ids]
    if removed:
        diffs.append(f"删除 {len(removed)} 条用例: {', '.join(removed[:5])}" + ("..." if len(removed) > 5 else ""))

    # 修改
    modified = []
    for nid, new_case in new_ids.items():
        if nid in old_ids:
            old_case = old_ids[nid]
            old_title = old_case.get("title", "")
            new_title = new_case.get("title", "")
            if old_title != new_title:
                modified.append(f"{nid}: 标题从 '{old_title}' 改为 '{new_title}'")
            old_steps = len(old_case.get("steps", []))
            new_steps = len(new_case.get("steps", []))
            if old_steps != new_steps:
                modified.append(f"{nid}: 步骤数从 {old_steps} 改为 {new_steps}")

    if modified:
        diffs.append(f"修改 {len(modified)} 处: {'; '.join(modified[:3])}" + ("..." if len(modified) > 3 else ""))

    # 数量变化
    if len(new_cases) != len(old_cases):
        diffs.append(f"总数从 {len(old_cases)} 变为 {len(new_cases)}")

    if not diffs:
        diffs.append("无结构性差异（内容哈希可能不同）")

    return diffs


# ---------------------------------------------------------------------------
# 便捷函数
# ---------------------------------------------------------------------------

def save_version(
    project: str,
    cases: list[dict[str, Any]],
    workspace_root: Optional[Path | str] = None,
    metadata: Optional[dict[str, Any]] = None,
) -> VersionRecord:
    """便捷函数：保存版本"""
    manager = VersionManager(project, workspace_root)
    return manager.save_version(cases, metadata)


def list_versions(project: str, workspace_root: Optional[Path | str] = None) -> list[VersionRecord]:
    """便捷函数：列出版本"""
    manager = VersionManager(project, workspace_root)
    return manager.list_versions()
