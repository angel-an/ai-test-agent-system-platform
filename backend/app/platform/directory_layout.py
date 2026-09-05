# -*- coding: utf-8 -*-
"""P0-2: 统一目录和命名规范 — 平台级目录布局与文件命名。

目标：让生成物、回写物、证据包、脚本、任务单能稳定归档，
新项目不需重新设计目录。
"""

from __future__ import annotations

import os
from datetime import datetime
from pathlib import Path
from typing import Optional


# ---------------------------------------------------------------------------
# 默认根目录（可通过环境变量覆盖）
# ---------------------------------------------------------------------------

DEFAULT_WORKSPACE_ROOT = Path(__file__).resolve().parent.parent.parent.parent / "workspace"


def _workspace_root() -> Path:
    return Path(os.getenv("PLATFORM_WORKSPACE_ROOT", DEFAULT_WORKSPACE_ROOT))


# ---------------------------------------------------------------------------
# 目录层级定义
# ---------------------------------------------------------------------------

class DirectoryLayout:
    """平台统一目录布局。

    结构：
        workspace/
        ├── generated/          # 生成物（AI 生成的测试用例、脚本等）
        │   └── {project}/
        │       └── {version}/
        ├── reports/            # 回写物（执行报告、质量报告）
        │   └── {project}/
        │       └── {timestamp}/
        ├── evidence/           # 证据包（截图、日志、返回体）
        │   └── {run_id}/
        ├── scripts/            # 脚本（Playwright、pytest 等）
        │   └── {project}/
        │       ├── api/        # API 测试脚本
        │       └── web/        # Web 测试脚本
        ├── versions/           # 版本留档
        │   └── {project}/
        │       └── v{N}.json
        └── tasks/              # 任务单
            └── {project}/
                └── {timestamp}.json
    """

    def __init__(self, workspace_root: Optional[Path | str] = None):
        self.root = Path(workspace_root) if workspace_root else _workspace_root()

    # --- 生成物 ---
    def generated_dir(self, project: str, version: str = "latest") -> Path:
        """生成物目录：workspace/generated/{project}/{version}/"""
        d = self.root / "generated" / _safe_name(project) / _safe_name(version)
        d.mkdir(parents=True, exist_ok=True)
        return d

    # --- 回写物 ---
    def reports_dir(self, project: str, timestamp: Optional[str] = None) -> Path:
        """回写物目录：workspace/reports/{project}/{timestamp}/"""
        ts = timestamp or _now_str()
        d = self.root / "reports" / _safe_name(project) / ts
        d.mkdir(parents=True, exist_ok=True)
        return d

    # --- 证据包 ---
    def evidence_dir(self, run_id: str) -> Path:
        """证据包目录：workspace/evidence/{run_id}/"""
        d = self.root / "evidence" / _safe_name(run_id)
        d.mkdir(parents=True, exist_ok=True)
        return d

    # --- 脚本 ---
    def scripts_dir(self, project: str, track: Optional[str] = None) -> Path:
        """脚本目录：workspace/scripts/{project}/[api|web]/"""
        d = self.root / "scripts" / _safe_name(project)
        if track in ("api", "web"):
            d = d / track
        d.mkdir(parents=True, exist_ok=True)
        return d

    # --- 版本留档 ---
    def versions_dir(self, project: str) -> Path:
        """版本留档目录：workspace/versions/{project}/"""
        d = self.root / "versions" / _safe_name(project)
        d.mkdir(parents=True, exist_ok=True)
        return d

    # --- 任务单 ---
    def tasks_dir(self, project: str) -> Path:
        """任务单目录：workspace/tasks/{project}/"""
        d = self.root / "tasks" / _safe_name(project)
        d.mkdir(parents=True, exist_ok=True)
        return d

    # --- 三轨分目录快捷方法 ---
    def api_dir(self, project: str) -> Path:
        """API 轨目录"""
        return self.scripts_dir(project, track="api")

    def web_dir(self, project: str) -> Path:
        """Web 轨目录"""
        return self.scripts_dir(project, track="web")

    def generated_track_dir(self, project: str, track: str, version: str = "latest") -> Path:
        """生成物三轨分目录：workspace/generated/{project}/{track}/{version}/"""
        d = self.root / "generated" / _safe_name(project) / track / _safe_name(version)
        d.mkdir(parents=True, exist_ok=True)
        return d


# ---------------------------------------------------------------------------
# 命名规则
# ---------------------------------------------------------------------------

class NamingConvention:
    """平台统一文件命名规则。"""

    @staticmethod
    def testcase_filename(project: str, kind: str, timestamp: Optional[str] = None) -> str:
        """测试用例文件命名：{project}_{kind}_{timestamp}.xlsx

        kind: SIT测试用例 / 冒烟测试 / UAT验收用例
        """
        ts = timestamp or _now_str()
        return f"{_safe_name(project)}_{kind}_{ts}.xlsx"

    @staticmethod
    def report_filename(project: str, report_type: str, timestamp: Optional[str] = None) -> str:
        """报告文件命名：{project}_{report_type}_报告_{timestamp}.json/md/html"""
        ts = timestamp or _now_str()
        return f"{_safe_name(project)}_{report_type}_报告_{ts}"

    @staticmethod
    def evidence_package_filename(run_id: str, timestamp: Optional[str] = None) -> str:
        """证据包命名：evidence_{run_id}_{timestamp}.zip"""
        ts = timestamp or _now_str()
        return f"evidence_{_safe_name(run_id)}_{ts}.zip"

    @staticmethod
    def version_filename(project: str, version_no: int) -> str:
        """版本留档命名：{project}_v{version_no}.json"""
        return f"{_safe_name(project)}_v{version_no}.json"

    @staticmethod
    def task_filename(project: str, task_type: str, timestamp: Optional[str] = None) -> str:
        """任务单命名：{project}_{task_type}_{timestamp}.json"""
        ts = timestamp or _now_str()
        return f"{_safe_name(project)}_{task_type}_{ts}.json"

    @staticmethod
    def script_filename(name: str, ext: str = "py") -> str:
        """脚本文件命名（确保合法文件名）"""
        return f"{_safe_name(name)}.{ext}"


# ---------------------------------------------------------------------------
# 工具函数
# ---------------------------------------------------------------------------

def _safe_name(name: str) -> str:
    """将名称转为安全的目录/文件名（去除非法字符）"""
    import re
    s = re.sub(r'[\\/:*?"<>|]', "_", str(name))
    s = re.sub(r"_+", "_", s).strip("_")
    return s or "untitled"


def _now_str() -> str:
    """当前时间戳：YYYYMMDD_HHMMSS"""
    return datetime.now().strftime("%Y%m%d_%H%M%S")


def generate_filename(
    project: str,
    kind: str,
    timestamp: Optional[str] = None,
    ext: str = "xlsx",
) -> str:
    """通用文件名生成器。

    Args:
        project: 项目标识符
        kind: 文件种类（如 SIT测试用例、冒烟测试、执行报告）
        timestamp: 时间戳（默认当前时间）
        ext: 扩展名
    """
    ts = timestamp or _now_str()
    return f"{_safe_name(project)}_{kind}_{ts}.{ext}"


def ensure_platform_dirs(workspace_root: Optional[Path | str] = None) -> DirectoryLayout:
    """确保平台目录结构存在，返回 DirectoryLayout 实例。"""
    layout = DirectoryLayout(workspace_root)
    # 预创建顶层目录
    for sub in ("generated", "reports", "evidence", "scripts", "versions", "tasks"):
        (layout.root / sub).mkdir(parents=True, exist_ok=True)
    return layout
