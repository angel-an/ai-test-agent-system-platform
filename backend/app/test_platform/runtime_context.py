# -*- coding: utf-8 -*-
"""P0: 统一运行上下文 — 所有 runner、证据链、报告和编排调用必须接收同一个 RunContext。

目标：禁止自行推断 run_id、工作区或"最近一次运行"。
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any, Optional


# ---------------------------------------------------------------------------
# 统一运行上下文
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class RunContext:
    """统一运行上下文 — 不可变，所有执行模块必须接收同一个实例。

    Attributes:
        project_id: 项目标识符（如 PR-15）
        run_id: 运行标识符（如 run_080）
        artifact_id: 产物/脚本标识符（可选）
        test_case_id: 测试用例 ID（可选）
        scenario_id: 场景 ID（可选）
        artifact_version: 产物版本号（可选）
        workspace_root: 工作区根目录（默认从环境变量读取）
        mode: 执行模式 — "safe"（默认只读）或 "live"
    """

    project_id: str
    run_id: str
    artifact_id: str | None = None
    test_case_id: str | None = None
    scenario_id: str | None = None
    artifact_version: str | None = None
    workspace_root: Path | None = None
    mode: str = "safe"  # "safe" | "live"

    def __post_init__(self):
        # frozen=True 时需要通过 object.__setattr__ 来设置派生属性
        # 但这里我们只需要校验，不需要设置
        if not self.project_id:
            raise ValueError("RunContext.project_id 不能为空")
        if not self.run_id:
            raise ValueError("RunContext.run_id 不能为空")
        if self.mode not in ("safe", "live"):
            raise ValueError(f"RunContext.mode 必须是 'safe' 或 'live'， got '{self.mode}'")

    def to_dict(self) -> dict[str, Any]:
        """序列化为字典（用于 JSON 存储/传输）。"""
        d = {
            "project_id": self.project_id,
            "run_id": self.run_id,
            "mode": self.mode,
        }
        if self.artifact_id:
            d["artifact_id"] = self.artifact_id
        if self.test_case_id:
            d["test_case_id"] = self.test_case_id
        if self.scenario_id:
            d["scenario_id"] = self.scenario_id
        if self.artifact_version:
            d["artifact_version"] = self.artifact_version
        if self.workspace_root:
            d["workspace_root"] = str(self.workspace_root)
        return d

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "RunContext":
        """从字典反序列化。"""
        return cls(
            project_id=d["project_id"],
            run_id=d["run_id"],
            artifact_id=d.get("artifact_id"),
            test_case_id=d.get("test_case_id"),
            scenario_id=d.get("scenario_id"),
            artifact_version=d.get("artifact_version"),
            workspace_root=Path(d["workspace_root"]) if d.get("workspace_root") else None,
            mode=d.get("mode", "safe"),
        )

    def run_dir(self, layout: "DirectoryLayout") -> Path:
        """获取当前运行目录。

        根据上下文自动推断目录类型：
        - 有 test_case_id → api_tests/{test_id}/runs/{run_id}/
        - 有 scenario_id → scenarios/{scenario_id}/runs/{run_id}/
        - 有 artifact_id → web_tests/{artifact_id}/runs/{run_id}/
        - 否则 → orchestration/{run_id}/
        """
        if self.test_case_id:
            return layout.api_run_dir(self.project_id, self.test_case_id, self.run_id)
        elif self.scenario_id:
            return layout.scenario_run_dir(self.project_id, self.scenario_id, self.run_id)
        elif self.artifact_id:
            return layout.web_run_dir(self.project_id, self.artifact_id, self.run_id)
        else:
            return layout.orchestration_run_dir(self.project_id, self.run_id)

    def web_run_dir(self, layout: "DirectoryLayout", web_test_id: str) -> Path:
        """获取 Web 测试运行目录。"""
        return layout.web_run_dir(self.project_id, web_test_id, self.run_id)

    def validate(self) -> tuple[bool, str]:
        """校验上下文完整性。

        Returns:
            (valid, reason): valid=True 表示校验通过。
        """
        if not self.project_id:
            return False, "project_id 不能为空"
        if not self.run_id:
            return False, "run_id 不能为空"
        if self.mode not in ("safe", "live"):
            return False, f"mode 必须是 'safe' 或 'live'， got '{self.mode}'"
        return True, "ok"

    def is_safe_mode(self) -> bool:
        """是否为安全模式。"""
        return self.mode == "safe"

    def is_live_mode(self) -> bool:
        """是否为 live 模式。"""
        return self.mode == "live"

    def with_mode(self, mode: str) -> "RunContext":
        """返回一个 mode 不同的新 RunContext（不可变更新）。"""
        return RunContext(
            project_id=self.project_id,
            run_id=self.run_id,
            artifact_id=self.artifact_id,
            test_case_id=self.test_case_id,
            scenario_id=self.scenario_id,
            artifact_version=self.artifact_version,
            workspace_root=self.workspace_root,
            mode=mode,
        )

    def with_artifact(self, artifact_id: str, version: str | None = None) -> "RunContext":
        """返回绑定产物的新 RunContext。"""
        return RunContext(
            project_id=self.project_id,
            run_id=self.run_id,
            artifact_id=artifact_id,
            test_case_id=self.test_case_id,
            scenario_id=self.scenario_id,
            artifact_version=version or self.artifact_version,
            workspace_root=self.workspace_root,
            mode=self.mode,
        )


# ---------------------------------------------------------------------------
# 便捷函数
# ---------------------------------------------------------------------------

def create_run_context(
    project_id: str,
    run_id: str,
    artifact_id: str | None = None,
    test_case_id: str | None = None,
    scenario_id: str | None = None,
    artifact_version: str | None = None,
    workspace_root: Path | str | None = None,
    mode: str = "safe",
) -> RunContext:
    """便捷函数：创建 RunContext。"""
    if workspace_root and isinstance(workspace_root, str):
        workspace_root = Path(workspace_root)
    return RunContext(
        project_id=project_id,
        run_id=run_id,
        artifact_id=artifact_id,
        test_case_id=test_case_id,
        scenario_id=scenario_id,
        artifact_version=artifact_version,
        workspace_root=workspace_root,
        mode=mode,
    )


__all__ = [
    "RunContext",
    "create_run_context",
]
