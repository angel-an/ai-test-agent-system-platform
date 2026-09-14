# -*- coding: utf-8 -*-
"""P1: 统一产物发布服务 — 脚本先发布再执行，发布时校验源文件、哈希、绑定关系。

目标：确保执行文件与登记版本一致，任何缺失或不一致都阻断执行。
"""

from __future__ import annotations

import hashlib
import json
import shutil
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from app.test_platform.runtime_context import RunContext
from app.test_platform.directory_layout import DirectoryLayout


# ---------------------------------------------------------------------------
# 发布产物数据类
# ---------------------------------------------------------------------------

@dataclass
class PublishedArtifact:
    """已发布产物信息。"""

    artifact_path: Path
    artifact_type: str  # "api_script" | "web_script" | "scenario_manifest" | ...
    expected_sha256: str | None
    actual_sha256: str
    artifact_version: str
    run_context: RunContext
    published_at: str
    manifest_path: Path
    provenance_path: Path

    def to_dict(self) -> dict[str, Any]:
        return {
            "artifact_path": str(self.artifact_path),
            "artifact_type": self.artifact_type,
            "expected_sha256": self.expected_sha256,
            "actual_sha256": self.actual_sha256,
            "artifact_version": self.artifact_version,
            "run_context": self.run_context.to_dict(),
            "published_at": self.published_at,
            "manifest_path": str(self.manifest_path),
            "provenance_path": str(self.provenance_path),
        }


# ---------------------------------------------------------------------------
# 产物发布异常
# ---------------------------------------------------------------------------

class ArtifactPublishError(Exception):
    """产物发布失败异常。"""

    def __init__(
        self,
        message: str,
        reason_code: str,
        context: RunContext | None = None,
        source_path: Path | None = None,
        expected_sha256: str | None = None,
        actual_sha256: str | None = None,
    ):
        super().__init__(message)
        self.reason_code = reason_code
        self.context = context
        self.source_path = source_path
        self.expected_sha256 = expected_sha256
        self.actual_sha256 = actual_sha256

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {
            "status": "blocked",
            "reason_code": self.reason_code,
            "message": str(self),
        }
        if self.context:
            d["run_id"] = self.context.run_id
        if self.source_path:
            d["artifact_path"] = str(self.source_path)
        if self.expected_sha256:
            d["expected_sha256"] = self.expected_sha256
        if self.actual_sha256:
            d["actual_sha256"] = self.actual_sha256
        return d


# ---------------------------------------------------------------------------
# SHA-256 工具
# ---------------------------------------------------------------------------

def sha256_hex(content: bytes) -> str:
    """计算内容 SHA-256（十六进制）。"""
    return hashlib.sha256(content).hexdigest()


def sha256_hex_file(path: Path) -> str:
    """计算文件 SHA-256。"""
    return sha256_hex(path.read_bytes())


# ---------------------------------------------------------------------------
# 产物发布服务
# ---------------------------------------------------------------------------

class ArtifactPublisher:
    """统一产物发布服务。

    发布流程：
    1. 校验源文件存在
    2. 计算源文件 SHA-256
    3. 校验登记哈希（如提供）
    4. 复制到当前运行目录
    5. 重新计算目标哈希
    6. 写入 manifest.json
    7. 写入 script_provenance.json
    8. 返回 PublishedArtifact

    失败条件（直接抛出 ArtifactPublishError，禁止执行）：
    - 源文件不存在
    - 哈希不一致（如提供 expected）
    - 目标路径不在当前 run 目录下
    - 绑定信息不一致
    """

    def __init__(self, layout: DirectoryLayout | None = None):
        self.layout = layout or DirectoryLayout()

    def publish_artifact(
        self,
        context: RunContext,
        source_path: Path,
        artifact_type: str,
        expected_sha256: str | None = None,
        artifact_version: str | None = None,
    ) -> PublishedArtifact:
        """发布产物到运行目录。

        Args:
            context: 运行上下文
            source_path: 源文件路径
            artifact_type: 产物类型
            expected_sha256: 期望的 SHA-256（可选，提供时校验）
            artifact_version: 产物版本（默认使用时间戳）

        Returns:
            PublishedArtifact: 发布后的产物信息

        Raises:
            ArtifactPublishError: 发布失败
        """
        # 1. 校验上下文
        valid, reason = context.validate()
        if not valid:
            raise ArtifactPublishError(
                message=f"运行上下文校验失败: {reason}",
                reason_code="INVALID_CONTEXT",
                context=context,
            )

        # 2. 校验源文件存在
        if not source_path.exists():
            raise ArtifactPublishError(
                message=f"源文件不存在: {source_path}",
                reason_code="SOURCE_NOT_FOUND",
                context=context,
                source_path=source_path,
            )

        # 3. 计算源文件 SHA-256
        try:
            source_hash = sha256_hex_file(source_path)
        except OSError as e:
            raise ArtifactPublishError(
                message=f"读取源文件失败: {e}",
                reason_code="SOURCE_READ_ERROR",
                context=context,
                source_path=source_path,
            )

        # 4. 校验登记哈希
        if expected_sha256 is not None and source_hash != expected_sha256:
            raise ArtifactPublishError(
                message="源文件哈希与登记哈希不一致",
                reason_code="HASH_MISMATCH",
                context=context,
                source_path=source_path,
                expected_sha256=expected_sha256,
                actual_sha256=source_hash,
            )

        # 5. 确定目标运行目录
        run_dir = self._resolve_run_dir(context, artifact_type)

        # 6. 确定目标文件路径
        target_filename = source_path.name
        target_path = run_dir / target_filename

        # 确保目标路径在 run_dir 内（防止路径遍历）
        try:
            target_path.resolve().relative_to(run_dir.resolve())
        except ValueError:
            raise ArtifactPublishError(
                message=f"目标路径超出运行目录: {target_path}",
                reason_code="PATH_ESCAPE",
                context=context,
                source_path=source_path,
            )

        # 7. 复制文件到运行目录
        try:
            shutil.copy2(source_path, target_path)
        except OSError as e:
            raise ArtifactPublishError(
                message=f"复制文件失败: {e}",
                reason_code="COPY_FAILED",
                context=context,
                source_path=source_path,
            )

        # 8. 重新计算目标哈希
        try:
            target_hash = sha256_hex_file(target_path)
        except OSError as e:
            raise ArtifactPublishError(
                message=f"读取目标文件失败: {e}",
                reason_code="TARGET_READ_ERROR",
                context=context,
                source_path=source_path,
            )

        # 9. 写入 manifest.json
        version = artifact_version or datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        manifest = {
            "project_id": context.project_id,
            "run_id": context.run_id,
            "artifact_id": context.artifact_id,
            "artifact_type": artifact_type,
            "artifact_version": version,
            "artifact_path": str(target_path),
            "artifact_filename": target_filename,
            "source_path": str(source_path),
            "sha256": target_hash,
            "published_at": datetime.now(timezone.utc).isoformat(),
            "mode": context.mode,
        }
        if context.test_case_id:
            manifest["test_case_id"] = context.test_case_id
        if context.scenario_id:
            manifest["scenario_id"] = context.scenario_id

        manifest_path = self.layout.manifest_path(run_dir)
        manifest_path.write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

        # 10. 写入 script_provenance.json
        provenance = {
            "project_id": context.project_id,
            "run_id": context.run_id,
            "artifact_id": context.artifact_id,
            "sha256": target_hash,
            "version": version,
            "published_at": manifest["published_at"],
            "source_sha256": source_hash,
            "target_sha256": target_hash,
            "status": "published",
        }

        provenance_path = self.layout.provenance_path(run_dir)
        provenance_path.write_text(
            json.dumps(provenance, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

        return PublishedArtifact(
            artifact_path=target_path,
            artifact_type=artifact_type,
            expected_sha256=expected_sha256,
            actual_sha256=target_hash,
            artifact_version=version,
            run_context=context,
            published_at=manifest["published_at"],
            manifest_path=manifest_path,
            provenance_path=provenance_path,
        )

    def _resolve_run_dir(self, context: RunContext, artifact_type: str) -> Path:
        """根据上下文和产物类型解析运行目录。

        严格绑定：缺少对应 ID 时直接失败，不允许回退到编排目录。
        """
        # Web 测试 — 必须有 artifact_id
        if artifact_type.startswith("web_"):
            if not context.artifact_id:
                raise ArtifactPublishError(
                    message="Web 产物发布需要 artifact_id（子功能/测试ID）",
                    reason_code="MISSING_ARTIFACT_ID",
                    context=context,
                )
            return context.web_run_dir(self.layout, context.artifact_id)

        # API 测试 — 必须有 test_case_id
        if artifact_type.startswith("api_"):
            if not context.test_case_id:
                raise ArtifactPublishError(
                    message="API 产物发布需要 test_case_id",
                    reason_code="MISSING_TEST_CASE_ID",
                    context=context,
                )
            return self.layout.api_run_dir(
                context.project_id, context.test_case_id, context.run_id
            )

        # 场景测试 — 必须有 scenario_id
        if artifact_type.startswith("scenario_"):
            if not context.scenario_id:
                raise ArtifactPublishError(
                    message="场景产物发布需要 scenario_id",
                    reason_code="MISSING_SCENARIO_ID",
                    context=context,
                )
            return self.layout.scenario_run_dir(
                context.project_id, context.scenario_id, context.run_id
            )

        # 编排产物 — 允许无额外 ID，使用编排目录
        if artifact_type.startswith("orchestration_"):
            return context.run_dir(self.layout)

        # 未知类型 — 阻断
        raise ArtifactPublishError(
            message=f"未知产物类型: {artifact_type}，无法确定运行目录",
            reason_code="UNKNOWN_ARTIFACT_TYPE",
            context=context,
        )

    def get_published_artifact(self, context: RunContext) -> PublishedArtifact | None:
        """获取已发布的产物信息（从 manifest 读取）。"""
        run_dir = context.run_dir(self.layout)
        manifest_path = self.layout.manifest_path(run_dir)

        if not manifest_path.exists():
            return None

        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return None

        artifact_path = Path(manifest.get("artifact_path", ""))
        if not artifact_path.exists():
            return None

        try:
            actual_hash = sha256_hex_file(artifact_path)
        except OSError:
            return None

        return PublishedArtifact(
            artifact_path=artifact_path,
            artifact_type=manifest.get("artifact_type", "unknown"),
            expected_sha256=manifest.get("sha256"),
            actual_sha256=actual_hash,
            artifact_version=manifest.get("artifact_version", ""),
            run_context=context,
            published_at=manifest.get("published_at", ""),
            manifest_path=manifest_path,
            provenance_path=self.layout.provenance_path(run_dir),
        )

    def is_published(self, context: RunContext) -> bool:
        """检查指定运行上下文是否已有发布产物。"""
        run_dir = context.run_dir(self.layout)
        manifest_path = self.layout.manifest_path(run_dir)
        provenance_path = self.layout.provenance_path(run_dir)
        return manifest_path.exists() and provenance_path.exists()


# ---------------------------------------------------------------------------
# 便捷函数
# ---------------------------------------------------------------------------

def publish_script(
    context: RunContext,
    source_path: Path | str,
    script_type: str = "web_script",
    expected_sha256: str | None = None,
    version: str | None = None,
) -> PublishedArtifact:
    """便捷函数：发布脚本。"""
    publisher = ArtifactPublisher()
    return publisher.publish_artifact(
        context=context,
        source_path=Path(source_path),
        artifact_type=script_type,
        expected_sha256=expected_sha256,
        artifact_version=version,
    )


__all__ = [
    "ArtifactPublisher",
    "PublishedArtifact",
    "ArtifactPublishError",
    "publish_script",
    "sha256_hex",
    "sha256_hex_file",
]
