# -*- coding: utf-8 -*-
"""P0-2 扩展: 统一目录布局 — 平台级目录布局与文件命名。

目标：让生成物、回写物、证据包、脚本、任务单能稳定归档，
新项目不需重新设计目录。支持 run 级别隔离。

目录结构：
    {PLATFORM_WORKSPACE_ROOT}/projects/{project_id}/
    ├── api_tests/{test_id}/runs/{run_id}/
    │   ├── final_script.py
    │   ├── manifest.json
    │   ├── script_provenance.json
    │   ├── responses/
    │   └── reports/
    ├── web_tests/{test_id}/runs/{run_id}/
    │   ├── final_script.py
    │   ├── manifest.json
    │   ├── script_provenance.json
    │   ├── screenshots/
    │   └── reports/
    ├── scenarios/{scenario_id}/runs/{run_id}/
    │   ├── manifest.json
    │   ├── evidence/
    │   └── reports/
    └── orchestration/{run_id}/
        ├── manifest.json
        ├── evidence/
        └── reports/
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

    新结构（支持 run 级别隔离）：
        workspace/
        └── projects/{project_id}/
            ├── api_tests/{test_id}/runs/{run_id}/
            ├── web_tests/{test_id}/runs/{run_id}/
            ├── scenarios/{scenario_id}/runs/{run_id}/
            └── orchestration/{run_id}/

    旧结构（兼容，标记为 deprecated）：
        workspace/
        ├── generated/          # 生成物
        ├── reports/            # 回写物
        ├── evidence/           # 证据包
        ├── scripts/            # 脚本
        ├── versions/           # 版本留档
        └── tasks/              # 任务单
    """

    def __init__(self, workspace_root: Optional[Path | str] = None):
        self.root = Path(workspace_root) if workspace_root else _workspace_root()

    # --- 项目根目录 ---
    def project_dir(self, project: str) -> Path:
        """项目目录：workspace/projects/{project}/"""
        d = self.root / "projects" / _safe_name(project)
        d.mkdir(parents=True, exist_ok=True)
        return d

    # --- API 测试运行目录 ---
    def api_run_dir(self, project: str, test_id: str, run_id: str) -> Path:
        """API 测试运行目录：projects/{project}/api_tests/{test_id}/runs/{run_id}/"""
        d = self.project_dir(project) / "api_tests" / _safe_name(test_id) / "runs" / _safe_name(run_id)
        d.mkdir(parents=True, exist_ok=True)
        return d

    # --- Web 测试运行目录 ---
    def web_run_dir(self, project: str, test_id: str, run_id: str) -> Path:
        """Web 测试运行目录：projects/{project}/web_tests/{test_id}/runs/{run_id}/"""
        d = self.project_dir(project) / "web_tests" / _safe_name(test_id) / "runs" / _safe_name(run_id)
        d.mkdir(parents=True, exist_ok=True)
        return d

    # --- 场景运行目录 ---
    def scenario_run_dir(self, project: str, scenario_id: str, run_id: str) -> Path:
        """场景运行目录：projects/{project}/scenarios/{scenario_id}/runs/{run_id}/"""
        d = self.project_dir(project) / "scenarios" / _safe_name(scenario_id) / "runs" / _safe_name(run_id)
        d.mkdir(parents=True, exist_ok=True)
        return d

    # --- 编排运行目录 ---
    def orchestration_run_dir(self, project: str, run_id: str) -> Path:
        """编排运行目录：projects/{project}/orchestration/{run_id}/"""
        d = self.project_dir(project) / "orchestration" / _safe_name(run_id)
        d.mkdir(parents=True, exist_ok=True)
        return d

    # --- 子目录快捷方法 ---
    def api_responses_dir(self, project: str, test_id: str, run_id: str) -> Path:
        """API 响应目录"""
        d = self.api_run_dir(project, test_id, run_id) / "responses"
        d.mkdir(parents=True, exist_ok=True)
        return d

    def api_reports_dir(self, project: str, test_id: str, run_id: str) -> Path:
        """API 报告目录"""
        d = self.api_run_dir(project, test_id, run_id) / "reports"
        d.mkdir(parents=True, exist_ok=True)
        return d

    def web_screenshots_dir(self, project: str, test_id: str, run_id: str) -> Path:
        """Web 截图目录"""
        d = self.web_run_dir(project, test_id, run_id) / "screenshots"
        d.mkdir(parents=True, exist_ok=True)
        return d

    def web_reports_dir(self, project: str, test_id: str, run_id: str) -> Path:
        """Web 报告目录"""
        d = self.web_run_dir(project, test_id, run_id) / "reports"
        d.mkdir(parents=True, exist_ok=True)
        return d

    def scenario_evidence_dir(self, project: str, scenario_id: str, run_id: str) -> Path:
        """场景证据目录"""
        d = self.scenario_run_dir(project, scenario_id, run_id) / "evidence"
        d.mkdir(parents=True, exist_ok=True)
        return d

    def scenario_reports_dir(self, project: str, scenario_id: str, run_id: str) -> Path:
        """场景报告目录"""
        d = self.scenario_run_dir(project, scenario_id, run_id) / "reports"
        d.mkdir(parents=True, exist_ok=True)
        return d

    def orchestration_evidence_dir(self, project: str, run_id: str) -> Path:
        """编排证据目录"""
        d = self.orchestration_run_dir(project, run_id) / "evidence"
        d.mkdir(parents=True, exist_ok=True)
        return d

    def orchestration_reports_dir(self, project: str, run_id: str) -> Path:
        """编排报告目录"""
        d = self.orchestration_run_dir(project, run_id) / "reports"
        d.mkdir(parents=True, exist_ok=True)
        return d

    # --- manifest 和 provenance 文件路径 ---
    def manifest_path(self, run_dir: Path) -> Path:
        """manifest.json 路径"""
        return run_dir / "manifest.json"

    def provenance_path(self, run_dir: Path) -> Path:
        """script_provenance.json 路径"""
        return run_dir / "script_provenance.json"

    # ========================================================================
    # 旧方法（兼容，标记为 deprecated）
    # ========================================================================

    def generated_dir(self, project: str, version: str = "latest") -> Path:
        """【DEPRECATED】生成物目录。请使用 api_run_dir / web_run_dir / scenario_run_dir。"""
        d = self.root / "generated" / _safe_name(project) / _safe_name(version)
        d.mkdir(parents=True, exist_ok=True)
        return d

    def reports_dir(self, project: str, timestamp: Optional[str] = None) -> Path:
        """【DEPRECATED】回写物目录。请使用 {api,web,scenario}_reports_dir。"""
        ts = timestamp or _now_str()
        d = self.root / "reports" / _safe_name(project) / ts
        d.mkdir(parents=True, exist_ok=True)
        return d

    def evidence_dir(self, run_id: str, project: Optional[str] = None) -> Path:
        """【DEPRECATED】证据包目录。请使用 scenario_evidence_dir / orchestration_evidence_dir。"""
        if project:
            d = self.root / "evidence" / _safe_name(project) / _safe_name(run_id)
        else:
            d = self.root / "evidence" / _safe_name(run_id)
        d.mkdir(parents=True, exist_ok=True)
        return d

    def scripts_dir(self, project: str, track: Optional[str] = None) -> Path:
        """【DEPRECATED】脚本目录。请使用 api_run_dir / web_run_dir。"""
        d = self.root / "scripts" / _safe_name(project)
        if track in ("api", "web"):
            d = d / track
        d.mkdir(parents=True, exist_ok=True)
        return d

    def api_dir(self, project: str) -> Path:
        """【DEPRECATED】API 轨目录。请使用 api_run_dir。"""
        return self.scripts_dir(project, track="api")

    def web_dir(self, project: str) -> Path:
        """【DEPRECATED】Web 轨目录。请使用 web_run_dir。"""
        return self.scripts_dir(project, track="web")

    def versions_dir(self, project: str) -> Path:
        """版本留档目录：workspace/versions/{project}/"""
        d = self.root / "versions" / _safe_name(project)
        d.mkdir(parents=True, exist_ok=True)
        return d

    def tasks_dir(self, project: str) -> Path:
        """任务单目录：workspace/tasks/{project}/"""
        d = self.root / "tasks" / _safe_name(project)
        d.mkdir(parents=True, exist_ok=True)
        return d

    def generated_track_dir(self, project: str, track: str, version: str = "latest") -> Path:
        """【DEPRECATED】生成物三轨分目录。"""
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
        """测试用例文件命名：{project}_{kind}_{timestamp}.xlsx"""
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

    @staticmethod
    def manifest_filename(run_id: str) -> str:
        """manifest 文件命名"""
        return f"manifest_{_safe_name(run_id)}.json"

    @staticmethod
    def provenance_filename(run_id: str) -> str:
        """provenance 文件命名"""
        return f"provenance_{_safe_name(run_id)}.json"


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
    """通用文件名生成器。"""
    ts = timestamp or _now_str()
    return f"{_safe_name(project)}_{kind}_{ts}.{ext}"


def ensure_platform_dirs(workspace_root: Optional[Path | str] = None) -> DirectoryLayout:
    """确保平台目录结构存在，返回 DirectoryLayout 实例。"""
    layout = DirectoryLayout(workspace_root)
    # 预创建顶层目录
    for sub in ("generated", "reports", "evidence", "scripts", "versions", "tasks", "projects"):
        (layout.root / sub).mkdir(parents=True, exist_ok=True)
    return layout


__all__ = [
    "DirectoryLayout",
    "NamingConvention",
    "generate_filename",
    "ensure_platform_dirs",
]
