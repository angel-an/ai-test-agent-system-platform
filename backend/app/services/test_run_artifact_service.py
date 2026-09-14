"""运行产物注册表 — TestRunArtifact 的写入与查询。

阶段 8：为 generate-run 等执行入口提供产物登记、预检阻断记录和审计追踪。
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.test_run_artifact import TestRunArtifact


def _utcnow_naive() -> datetime:
    """当前 UTC 时间（**naive**）。

    ``test_run_artifacts.executed_at`` / ``completed_at`` 由 SQLAlchemy 映射为
    ``TIMESTAMP WITHOUT TIME ZONE``，asyncpg 不接受带 tzinfo 的 datetime
    （``can't subtract offset-naive and offset-aware datetimes``），
    会把整条执行链路变成 500。
    """
    return datetime.now(timezone.utc).replace(tzinfo=None)


class TestRunArtifactService:
    """TestRunArtifact 读写服务。"""

    def __init__(self, session: AsyncSession):
        self.session = session

    async def register(
        self,
        *,
        project_id: UUID,
        run_id: str,
        artifact_type: str,
        artifact_path: str,
        test_case_id: UUID | None = None,
        scenario_id: UUID | None = None,
        artifact_version: str | None = None,
        expected_sha256: str | None = None,
        mode: str = "safe",
        status: str = "draft",
        metadata: dict[str, Any] | None = None,
    ) -> TestRunArtifact:
        """登记一次运行的产物（执行前调用）。"""
        artifact = TestRunArtifact(
            project_id=project_id,
            run_id=run_id,
            artifact_type=artifact_type,
            artifact_path=artifact_path,
            test_case_id=test_case_id,
            scenario_id=scenario_id,
            artifact_version=artifact_version,
            expected_sha256=expected_sha256,
            mode=mode,
            status=status,
            artifact_metadata=metadata or {},
        )
        self.session.add(artifact)
        await self.session.flush()
        return artifact

    async def mark_blocked(
        self,
        artifact: TestRunArtifact,
        reason: str,
        reason_code: str,
    ) -> None:
        """预检阻断：登记产物但不执行。"""
        artifact.status = "blocked"
        artifact.blocked_reason = reason
        artifact.blocked_reason_code = reason_code
        await self.session.flush()

    async def mark_executing(self, artifact: TestRunArtifact) -> None:
        """预检通过，开始执行。"""
        artifact.status = "executing"
        artifact.executed_at = _utcnow_naive()
        await self.session.flush()

    async def mark_completed(
        self,
        artifact: TestRunArtifact,
        actual_sha256: str | None = None,
        duration_ms: int | None = None,
    ) -> None:
        """执行完成。"""
        artifact.status = "completed"
        artifact.actual_sha256 = actual_sha256
        artifact.completed_at = _utcnow_naive()
        artifact.duration_ms = duration_ms
        await self.session.flush()

    async def mark_failed(
        self,
        artifact: TestRunArtifact,
        reason: str,
    ) -> None:
        """执行失败。"""
        artifact.status = "failed"
        artifact.blocked_reason = reason
        artifact.completed_at = _utcnow_naive()
        await self.session.flush()

    async def get_by_run_id(self, project_id: UUID, run_id: str) -> list[TestRunArtifact]:
        """查询某次运行的所有产物。"""
        stmt = select(TestRunArtifact).where(
            TestRunArtifact.project_id == project_id,
            TestRunArtifact.run_id == run_id,
        )
        result = await self.session.execute(stmt)
        return list(result.scalars().all())
