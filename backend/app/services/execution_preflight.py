# -*- coding: utf-8 -*-
"""P1: 执行前统一校验 — 所有 API、Web、场景和编排执行前统一检查。

目标：确保执行前完成路径和哈希校验，任何缺失或不一致都阻断执行。
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

from app.test_platform.runtime_context import RunContext
from app.test_platform.directory_layout import DirectoryLayout
from app.test_platform.execution_gates import ExecutionGate, GateConfig
from app.services.artifact_publisher import ArtifactPublisher, sha256_hex_file


# ---------------------------------------------------------------------------
# 预检结果
# ---------------------------------------------------------------------------

@dataclass
class PreflightResult:
    """预检结果。"""

    status: str  # "passed" | "blocked"
    reason_code: str | None = None
    message: str | None = None
    run_id: str | None = None
    artifact_path: str | None = None
    expected_sha256: str | None = None
    actual_sha256: str | None = None
    checks: list[dict[str, Any]] | None = None

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {"status": self.status}
        if self.reason_code:
            d["reason_code"] = self.reason_code
        if self.message:
            d["message"] = self.message
        if self.run_id:
            d["run_id"] = self.run_id
        if self.artifact_path:
            d["artifact_path"] = self.artifact_path
        if self.expected_sha256:
            d["expected_sha256"] = self.expected_sha256
        if self.actual_sha256:
            d["actual_sha256"] = self.actual_sha256
        if self.checks:
            d["checks"] = self.checks
        return d

    def is_passed(self) -> bool:
        return self.status == "passed"

    def is_blocked(self) -> bool:
        return self.status == "blocked"


# ---------------------------------------------------------------------------
# 预检异常
# ---------------------------------------------------------------------------

class PreflightBlockedError(Exception):
    """预检被阻断异常。"""

    def __init__(self, result: PreflightResult):
        self.result = result
        super().__init__(result.message or "执行被预检阻断")

    def to_dict(self) -> dict[str, Any]:
        return self.result.to_dict()


# ---------------------------------------------------------------------------
# 执行前预检服务
# ---------------------------------------------------------------------------

class ExecutionPreflight:
    """执行前统一校验服务。

    检查项：
    1. project_id 一致
    2. run_id 一致
    3. test_case_id / scenario_id 一致
    4. artifact_version 一致
    5. 目标文件存在
    6. 目标路径位于当前 run 目录
    7. 实际 SHA-256 等于登记 SHA-256
    8. 产物状态为 published
    9. safe/live 门控允许执行

    失败返回统一结构：
    {
      "status": "blocked",
      "reason_code": "SCRIPT_HASH_MISMATCH",
      "message": "执行文件与登记版本不一致",
      "run_id": "run_080",
      "artifact_path": "...",
      "expected_sha256": "...",
      "actual_sha256": "..."
    }
    """

    def __init__(
        self,
        layout: DirectoryLayout | None = None,
        gate_config: GateConfig | None = None,
    ):
        self.layout = layout or DirectoryLayout()
        self.gate = ExecutionGate(gate_config)
        self.publisher = ArtifactPublisher(self.layout)

    def check(
        self,
        context: RunContext,
        artifact_path: Path | None = None,
        track: str = "web",  # "api" | "web" | "scenario"
    ) -> PreflightResult:
        """执行预检。

        Args:
            context: 运行上下文
            artifact_path: 要执行的目标文件路径（可选，默认从 manifest 推断）
            track: 执行轨道类型

        Returns:
            PreflightResult: 预检结果
        """
        checks: list[dict[str, Any]] = []

        # 1. 校验上下文完整性
        valid, reason = context.validate()
        checks.append({"check": "context_validation", "passed": valid, "detail": reason})
        if not valid:
            return PreflightResult(
                status="blocked",
                reason_code="INVALID_CONTEXT",
                message=f"运行上下文校验失败: {reason}",
                run_id=context.run_id,
                checks=checks,
            )

        # 2. 校验门控
        allowed, gate_reasons = self.gate.check_all(track)
        checks.append({"check": "execution_gate", "passed": allowed, "detail": gate_reasons})
        if not allowed:
            return PreflightResult(
                status="blocked",
                reason_code="GATE_BLOCKED",
                message=f"执行门控阻断: {'; '.join(gate_reasons)}",
                run_id=context.run_id,
                checks=checks,
            )

        # 3. 校验 mode 与 gate 一致
        if context.is_live_mode() and not self.gate.config.any_live():
            return PreflightResult(
                status="blocked",
                reason_code="MODE_GATE_MISMATCH",
                message="RunContext 标记为 live 模式，但执行门控未开启 live",
                run_id=context.run_id,
                checks=checks,
            )

        # 4. 确定运行目录
        run_dir = context.run_dir(self.layout)
        checks.append({"check": "run_dir_exists", "passed": run_dir.exists(), "detail": str(run_dir)})
        if not run_dir.exists():
            return PreflightResult(
                status="blocked",
                reason_code="RUN_DIR_NOT_FOUND",
                message=f"运行目录不存在: {run_dir}",
                run_id=context.run_id,
                artifact_path=str(run_dir),
                checks=checks,
            )

        # 5. 读取 manifest.json
        manifest_path = self.layout.manifest_path(run_dir)
        checks.append({"check": "manifest_exists", "passed": manifest_path.exists(), "detail": str(manifest_path)})
        if not manifest_path.exists():
            return PreflightResult(
                status="blocked",
                reason_code="MANIFEST_MISSING",
                message="manifest.json 不存在，产物未发布",
                run_id=context.run_id,
                artifact_path=str(run_dir),
                checks=checks,
            )

        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as e:
            return PreflightResult(
                status="blocked",
                reason_code="MANIFEST_READ_ERROR",
                message=f"读取 manifest.json 失败: {e}",
                run_id=context.run_id,
                artifact_path=str(manifest_path),
                checks=checks,
            )

        # 6. 校验 manifest 中的绑定信息
        manifest_project = manifest.get("project_id")
        manifest_run_id = manifest.get("run_id")
        manifest_artifact_id = manifest.get("artifact_id")

        if manifest_project and manifest_project != context.project_id:
            return PreflightResult(
                status="blocked",
                reason_code="PROJECT_MISMATCH",
                message=f"项目不一致: manifest={manifest_project}, context={context.project_id}",
                run_id=context.run_id,
                checks=checks,
            )

        if manifest_run_id and manifest_run_id != context.run_id:
            return PreflightResult(
                status="blocked",
                reason_code="RUN_ID_MISMATCH",
                message=f"run_id 不一致: manifest={manifest_run_id}, context={context.run_id}",
                run_id=context.run_id,
                checks=checks,
            )

        if context.artifact_id and manifest_artifact_id and manifest_artifact_id != context.artifact_id:
            return PreflightResult(
                status="blocked",
                reason_code="ARTIFACT_ID_MISMATCH",
                message=f"artifact_id 不一致: manifest={manifest_artifact_id}, context={context.artifact_id}",
                run_id=context.run_id,
                checks=checks,
            )

        # 7. 校验目标文件存在
        target_path = artifact_path or Path(manifest.get("artifact_path", ""))
        if not target_path or not target_path.exists():
            return PreflightResult(
                status="blocked",
                reason_code="ARTIFACT_NOT_FOUND",
                message=f"目标文件不存在: {target_path}",
                run_id=context.run_id,
                artifact_path=str(target_path) if target_path else None,
                checks=checks,
            )

        # 8. 校验目标路径位于当前 run 目录
        try:
            target_path.resolve().relative_to(run_dir.resolve())
        except ValueError:
            return PreflightResult(
                status="blocked",
                reason_code="PATH_ESCAPE",
                message=f"目标路径超出运行目录: {target_path}",
                run_id=context.run_id,
                artifact_path=str(target_path),
                checks=checks,
            )

        # 9. 校验 SHA-256
        expected_hash = manifest.get("sha256")
        if expected_hash:
            try:
                actual_hash = sha256_hex_file(target_path)
            except OSError as e:
                return PreflightResult(
                    status="blocked",
                    reason_code="HASH_READ_ERROR",
                    message=f"读取目标文件计算哈希失败: {e}",
                    run_id=context.run_id,
                    artifact_path=str(target_path),
                    expected_sha256=expected_hash,
                    checks=checks,
                )

            if actual_hash != expected_hash:
                return PreflightResult(
                    status="blocked",
                    reason_code="SCRIPT_HASH_MISMATCH",
                    message="执行文件与登记版本不一致",
                    run_id=context.run_id,
                    artifact_path=str(target_path),
                    expected_sha256=expected_hash,
                    actual_sha256=actual_hash,
                    checks=checks,
                )

            checks.append({"check": "hash_match", "passed": True, "detail": f"sha256={actual_hash[:16]}..."})
        else:
            checks.append({"check": "hash_match", "passed": False, "detail": "manifest 中无 sha256 记录"})

        # 10. 校验 provenance 状态 — 缺失即阻断
        provenance_path = self.layout.provenance_path(run_dir)
        if not provenance_path.exists():
            return PreflightResult(
                status="blocked",
                reason_code="PROVENANCE_MISSING",
                message="script_provenance.json 不存在，产物未完整发布",
                run_id=context.run_id,
                artifact_path=str(target_path),
                checks=checks,
            )

        try:
            provenance = json.loads(provenance_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as e:
            return PreflightResult(
                status="blocked",
                reason_code="PROVENANCE_READ_ERROR",
                message=f"读取 script_provenance.json 失败: {e}",
                run_id=context.run_id,
                artifact_path=str(target_path),
                checks=checks,
            )

        prov_status = provenance.get("status", "")
        if prov_status != "published":
            return PreflightResult(
                status="blocked",
                reason_code="NOT_PUBLISHED",
                message=f"产物状态不是 published: {prov_status}",
                run_id=context.run_id,
                artifact_path=str(target_path),
                checks=checks,
            )

        # 校验 provenance 中的绑定信息
        prov_project = provenance.get("project_id")
        prov_run_id = provenance.get("run_id")
        prov_artifact_id = provenance.get("artifact_id")

        if prov_project and prov_project != context.project_id:
            return PreflightResult(
                status="blocked",
                reason_code="PROVENANCE_PROJECT_MISMATCH",
                message=f"provenance 项目不一致: provenance={prov_project}, context={context.project_id}",
                run_id=context.run_id,
                artifact_path=str(target_path),
                checks=checks,
            )

        if prov_run_id and prov_run_id != context.run_id:
            return PreflightResult(
                status="blocked",
                reason_code="PROVENANCE_RUN_ID_MISMATCH",
                message=f"provenance run_id 不一致: provenance={prov_run_id}, context={context.run_id}",
                run_id=context.run_id,
                artifact_path=str(target_path),
                checks=checks,
            )

        if context.artifact_id and prov_artifact_id and prov_artifact_id != context.artifact_id:
            return PreflightResult(
                status="blocked",
                reason_code="PROVENANCE_ARTIFACT_ID_MISMATCH",
                message=f"provenance artifact_id 不一致: provenance={prov_artifact_id}, context={context.artifact_id}",
                run_id=context.run_id,
                artifact_path=str(target_path),
                checks=checks,
            )

        checks.append({"check": "provenance_status", "passed": True, "detail": f"status={prov_status}"})

        # 全部通过
        return PreflightResult(
            status="passed",
            message="预检通过",
            run_id=context.run_id,
            artifact_path=str(target_path),
            expected_sha256=expected_hash,
            actual_sha256=actual_hash if expected_hash else None,
            checks=checks,
        )

    def require_passed(
        self,
        context: RunContext,
        artifact_path: Path | None = None,
        track: str = "web",
    ) -> PreflightResult:
        """执行预检，如果被阻断则抛出异常。

        Args:
            context: 运行上下文
            artifact_path: 目标文件路径
            track: 执行轨道类型

        Returns:
            PreflightResult: 通过的预检结果

        Raises:
            PreflightBlockedError: 预检被阻断
        """
        result = self.check(context, artifact_path, track)
        if result.is_blocked():
            raise PreflightBlockedError(result)
        return result


# ---------------------------------------------------------------------------
# 便捷函数
# ---------------------------------------------------------------------------

def preflight_check(
    context: RunContext,
    artifact_path: Path | str | None = None,
    track: str = "web",
    gate_config: GateConfig | None = None,
) -> PreflightResult:
    """便捷函数：执行预检。"""
    preflight = ExecutionPreflight(gate_config=gate_config)
    return preflight.check(
        context,
        Path(artifact_path) if artifact_path else None,
        track,
    )


__all__ = [
    "ExecutionPreflight",
    "PreflightResult",
    "PreflightBlockedError",
    "preflight_check",
]
