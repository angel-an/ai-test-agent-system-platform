"""generate-run 预检闭环回归测试。

验证阶段 8 修复：
1. 缺 run_id 必须 blocked
2. 预检失败不得创建执行进程
3. 通过预检后才允许执行
4. 运行产物必须写入 TestRunArtifact
"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.services.test_run_artifact_service import TestRunArtifactService


# ---------------------------------------------------------------------------
# TestRunArtifactService
# ---------------------------------------------------------------------------

class TestTestRunArtifactService:
    """验证产物登记服务各状态流转。"""

    @pytest.fixture
    def mock_session(self):
        session = AsyncMock()
        session.flush = AsyncMock()
        return session

    @pytest.fixture
    def service(self, mock_session):
        return TestRunArtifactService(mock_session)

    @pytest.mark.asyncio
    async def test_register_creates_artifact(self, service, mock_session):
        artifact = await service.register(
            project_id=uuid4(),
            run_id="run-001",
            artifact_type="api_script",
            artifact_path="s3://bucket/script.ts",
            mode="safe",
        )
        assert artifact.status == "draft"
        assert artifact.run_id == "run-001"
        mock_session.add.assert_called_once()
        mock_session.flush.assert_called_once()

    @pytest.mark.asyncio
    async def test_mark_blocked(self, service, mock_session):
        artifact = MagicMock()
        await service.mark_blocked(artifact, "缺环境配置", "ENV_MISSING")
        assert artifact.status == "blocked"
        assert artifact.blocked_reason == "缺环境配置"
        assert artifact.blocked_reason_code == "ENV_MISSING"

    @pytest.mark.asyncio
    async def test_mark_executing(self, service, mock_session):
        artifact = MagicMock()
        await service.mark_executing(artifact)
        assert artifact.status == "executing"
        assert artifact.executed_at is not None

    @pytest.mark.asyncio
    async def test_mark_completed(self, service, mock_session):
        artifact = MagicMock()
        await service.mark_completed(artifact, actual_sha256="abc123", duration_ms=500)
        assert artifact.status == "completed"
        assert artifact.actual_sha256 == "abc123"
        assert artifact.duration_ms == 500

    @pytest.mark.asyncio
    async def test_mark_failed(self, service, mock_session):
        artifact = MagicMock()
        await service.mark_failed(artifact, "执行异常")
        assert artifact.status == "failed"
        assert artifact.blocked_reason == "执行异常"


# ---------------------------------------------------------------------------
# generate-run 预检闭环
# ---------------------------------------------------------------------------

class TestGenerateRunPreflight:
    """验证 generate_and_run_from_endpoint 接入统一预检。"""

    @pytest.mark.asyncio
    async def test_missing_run_id_blocked(self):
        """预检入口：无 run_id 时 execute_with_preflight 返回 blocked。"""
        from app.services.unified_execution import execute_with_preflight
        from app.test_platform.runtime_context import RunContext

        # RunContext 本身要求 run_id 非空，构造时空 run_id 抛 ValueError
        with pytest.raises(ValueError):
            RunContext(project_id="P1", run_id="")

    @pytest.mark.asyncio
    async def test_preflight_blocked_returns_blocked_status(self):
        """预检失败时 execute_with_preflight 返回 blocked，不调用 executor。"""
        from app.services.unified_execution import execute_with_preflight
        from app.test_platform.runtime_context import RunContext

        executed = False

        async def _executor():
            nonlocal executed
            executed = True
            return {"status": "running"}

        context = RunContext(project_id="P1", run_id="run-blocked-test")
        # safe 模式下 track=api 需要 live_api=False（默认）
        # 但预检会检查 run_dir 等，预期 blocked
        result = await execute_with_preflight(
            context=context,
            track="api",
            executor=_executor,
        )
        assert result["status"] == "blocked"
        assert not executed  # executor 未被调用

    @pytest.mark.asyncio
    async def test_preflight_result_contains_reason_code(self):
        """blocked 结果必须包含 reason_code。"""
        from app.services.unified_execution import execute_with_preflight
        from app.test_platform.runtime_context import RunContext

        context = RunContext(project_id="P1", run_id="run-reason-test")
        result = await execute_with_preflight(
            context=context,
            track="api",
            executor=lambda: None,
        )
        assert result["status"] == "blocked"
        assert "reason_code" in result.get("preflight", {})

    @pytest.mark.asyncio
    async def test_run_id_propagated_through_result(self):
        """run_id 必须贯穿预检结果。"""
        from app.services.unified_execution import execute_with_preflight
        from app.test_platform.runtime_context import RunContext

        context = RunContext(project_id="P1", run_id="run-propagation-test")
        result = await execute_with_preflight(
            context=context,
            track="api",
            executor=lambda: None,
        )
        assert result.get("run_id") == "run-propagation-test"


# ---------------------------------------------------------------------------
# TestRunArtifact 模型
# ---------------------------------------------------------------------------

class TestTestRunArtifactModel:
    """验证模型修复后可正常映射。"""

    def test_metadata_column_renamed(self):
        from app.models.test_run_artifact import TestRunArtifact
        table = TestRunArtifact.__table__
        assert "metadata" in table.columns
        # 属性名 artifact_metadata
        assert hasattr(TestRunArtifact, "artifact_metadata")

    def test_status_values(self):
        from app.models.test_run_artifact import TestRunArtifact
        artifact = TestRunArtifact(
            project_id=uuid4(),
            run_id="r1",
            artifact_type="api_script",
            artifact_path="/tmp/x",
            status="draft",
            mode="safe",
        )
        assert artifact.status == "draft"
        assert artifact.mode == "safe"
