# -*- coding: utf-8 -*-
"""P0-P2 验收测试：统一运行时 — RunContext + ArtifactPublisher + ExecutionPreflight。

必测项：
- 不同 run_id 互相隔离
- 跨项目访问返回 404/阻断
- 登记哈希和实际执行哈希一致
- 文件被修改后执行被阻断
- 未发布产物不能执行
- 并发运行不互相覆盖
"""

import tempfile
from pathlib import Path

import pytest

from app.test_platform.runtime_context import RunContext, create_run_context
from app.test_platform.directory_layout import DirectoryLayout
from app.test_platform.execution_gates import GateConfig
from app.services.artifact_publisher import (
    ArtifactPublisher,
    ArtifactPublishError,
    publish_script,
)
from app.services.execution_preflight import (
    ExecutionPreflight,
    PreflightBlockedError,
    preflight_check,
)


# ---------------------------------------------------------------------------
# RunContext 测试
# ---------------------------------------------------------------------------

class TestRunContext:
    def test_basic_creation(self):
        ctx = create_run_context(
            project_id="PR-15",
            run_id="run_080",
            artifact_id="artifact-123",
            mode="safe",
        )
        assert ctx.project_id == "PR-15"
        assert ctx.run_id == "run_080"
        assert ctx.artifact_id == "artifact-123"
        assert ctx.mode == "safe"

    def test_frozen_immutable(self):
        ctx = create_run_context(project_id="PR-15", run_id="run_080")
        with pytest.raises(AttributeError):
            ctx.project_id = "PR-16"

    def test_validation_empty_project(self):
        with pytest.raises(ValueError, match="project_id"):
            create_run_context(project_id="", run_id="run_080")

    def test_validation_empty_run_id(self):
        with pytest.raises(ValueError, match="run_id"):
            create_run_context(project_id="PR-15", run_id="")

    def test_validation_invalid_mode(self):
        with pytest.raises(ValueError, match="mode"):
            create_run_context(project_id="PR-15", run_id="run_080", mode="invalid")

    def test_serialization(self):
        ctx = create_run_context(
            project_id="PR-15",
            run_id="run_080",
            artifact_id="artifact-123",
            artifact_version="v1.0",
            mode="live",
        )
        d = ctx.to_dict()
        assert d["project_id"] == "PR-15"
        assert d["run_id"] == "run_080"
        assert d["artifact_id"] == "artifact-123"
        assert d["artifact_version"] == "v1.0"
        assert d["mode"] == "live"

        restored = RunContext.from_dict(d)
        assert restored.project_id == ctx.project_id
        assert restored.run_id == ctx.run_id
        assert restored.artifact_version == ctx.artifact_version

    def test_with_mode(self):
        ctx = create_run_context(project_id="PR-15", run_id="run_080", mode="safe")
        live_ctx = ctx.with_mode("live")
        assert live_ctx.mode == "live"
        assert ctx.mode == "safe"  # 原对象不变

    def test_with_artifact(self):
        ctx = create_run_context(project_id="PR-15", run_id="run_080")
        new_ctx = ctx.with_artifact("artifact-123", "v1.0")
        assert new_ctx.artifact_id == "artifact-123"
        assert new_ctx.artifact_version == "v1.0"
        assert ctx.artifact_id is None  # 原对象不变


# ---------------------------------------------------------------------------
# DirectoryLayout 新结构测试
# ---------------------------------------------------------------------------

class TestDirectoryLayoutNewStructure:
    def test_api_run_dir(self):
        with tempfile.TemporaryDirectory() as tmp:
            layout = DirectoryLayout(tmp)
            d = layout.api_run_dir("PR-15", "test-001", "run_080")
            assert d.exists()
            assert "projects" in str(d)
            assert "PR-15" in str(d)
            assert "api_tests" in str(d)
            assert "test-001" in str(d)
            assert "runs" in str(d)
            assert "run_080" in str(d)

    def test_web_run_dir(self):
        with tempfile.TemporaryDirectory() as tmp:
            layout = DirectoryLayout(tmp)
            d = layout.web_run_dir("PR-15", "test-001", "run_080")
            assert d.exists()
            assert "web_tests" in str(d)
            assert "run_080" in str(d)

    def test_scenario_run_dir(self):
        with tempfile.TemporaryDirectory() as tmp:
            layout = DirectoryLayout(tmp)
            d = layout.scenario_run_dir("PR-15", "scenario-001", "run_080")
            assert d.exists()
            assert "scenarios" in str(d)
            assert "scenario-001" in str(d)

    def test_orchestration_run_dir(self):
        with tempfile.TemporaryDirectory() as tmp:
            layout = DirectoryLayout(tmp)
            d = layout.orchestration_run_dir("PR-15", "run_080")
            assert d.exists()
            assert "orchestration" in str(d)

    def test_project_isolation(self):
        """不同项目的相同 run_id 互相隔离"""
        with tempfile.TemporaryDirectory() as tmp:
            layout = DirectoryLayout(tmp)
            d1 = layout.web_run_dir("PR-15", "test-001", "run_080")
            d2 = layout.web_run_dir("PR-16", "test-001", "run_080")
            assert d1 != d2
            assert "PR-15" in str(d1)
            assert "PR-16" in str(d2)

    def test_run_isolation(self):
        """同一项目的不同 run_id 互相隔离"""
        with tempfile.TemporaryDirectory() as tmp:
            layout = DirectoryLayout(tmp)
            d1 = layout.web_run_dir("PR-15", "test-001", "run_079")
            d2 = layout.web_run_dir("PR-15", "test-001", "run_080")
            assert d1 != d2
            # run_080 不能读取 run_079 的数据
            assert not str(d2).startswith(str(d1))


# ---------------------------------------------------------------------------
# ArtifactPublisher 测试
# ---------------------------------------------------------------------------

class TestArtifactPublisher:
    def test_publish_success(self):
        with tempfile.TemporaryDirectory() as tmp:
            layout = DirectoryLayout(tmp)
            publisher = ArtifactPublisher(layout)

            # 创建源文件
            source = Path(tmp) / "test_script.py"
            source.write_text("print('hello')", encoding="utf-8")

            ctx = create_run_context(
                project_id="PR-15",
                run_id="run_080",
                artifact_id="web-test-001",
            )
            published = publisher.publish_artifact(
                context=ctx,
                source_path=source,
                artifact_type="web_script",
            )

            assert published.artifact_path.exists()
            assert published.manifest_path.exists()
            assert published.provenance_path.exists()
            assert published.actual_sha256 is not None
            assert published.artifact_version is not None

    def test_publish_hash_mismatch(self):
        with tempfile.TemporaryDirectory() as tmp:
            layout = DirectoryLayout(tmp)
            publisher = ArtifactPublisher(layout)

            source = Path(tmp) / "test_script.py"
            source.write_text("print('hello')", encoding="utf-8")

            ctx = create_run_context(project_id="PR-15", run_id="run_080")
            with pytest.raises(ArtifactPublishError) as exc_info:
                publisher.publish_artifact(
                    context=ctx,
                    source_path=source,
                    artifact_type="web_script",
                    expected_sha256="0000000000000000000000000000000000000000000000000000000000000000",
                )
            assert exc_info.value.reason_code == "HASH_MISMATCH"
            assert exc_info.value.expected_sha256 is not None

    def test_publish_source_not_found(self):
        with tempfile.TemporaryDirectory() as tmp:
            layout = DirectoryLayout(tmp)
            publisher = ArtifactPublisher(layout)

            ctx = create_run_context(project_id="PR-15", run_id="run_080")
            with pytest.raises(ArtifactPublishError) as exc_info:
                publisher.publish_artifact(
                    context=ctx,
                    source_path=Path(tmp) / "nonexistent.py",
                    artifact_type="web_script",
                )
            assert exc_info.value.reason_code == "SOURCE_NOT_FOUND"

    def test_publish_manifest_content(self):
        with tempfile.TemporaryDirectory() as tmp:
            layout = DirectoryLayout(tmp)
            publisher = ArtifactPublisher(layout)

            source = Path(tmp) / "test_script.py"
            source.write_text("print('hello')", encoding="utf-8")

            ctx = create_run_context(
                project_id="PR-15",
                run_id="run_080",
                artifact_id="artifact-123",
                artifact_version="v1.0",
            )
            publisher.publish_artifact(
                context=ctx,
                source_path=source,
                artifact_type="web_script",
                artifact_version="v1.0",
            )

            # 验证 manifest.json
            run_dir = ctx.run_dir(layout)
            manifest_path = layout.manifest_path(run_dir)
            import json
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            assert manifest["project_id"] == "PR-15"
            assert manifest["run_id"] == "run_080"
            assert manifest["artifact_id"] == "artifact-123"
            assert manifest["artifact_version"] == "v1.0"
            assert manifest["artifact_type"] == "web_script"

    def test_is_published(self):
        with tempfile.TemporaryDirectory() as tmp:
            layout = DirectoryLayout(tmp)
            publisher = ArtifactPublisher(layout)

            ctx = create_run_context(project_id="PR-15", run_id="run_080", artifact_id="web-test-001")
            assert not publisher.is_published(ctx)

            source = Path(tmp) / "test_script.py"
            source.write_text("print('hello')", encoding="utf-8")
            publisher.publish_artifact(ctx, source, "web_script")

            assert publisher.is_published(ctx)


# ---------------------------------------------------------------------------
# ExecutionPreflight 测试
# ---------------------------------------------------------------------------

class TestExecutionPreflight:
    def test_preflight_passed(self):
        with tempfile.TemporaryDirectory() as tmp:
            layout = DirectoryLayout(tmp)

            # 先发布产物
            source = Path(tmp) / "test_script.py"
            source.write_text("print('hello')", encoding="utf-8")

            ctx = create_run_context(project_id="PR-15", run_id="run_080", artifact_id="web-test-001")
            publisher = ArtifactPublisher(layout)
            publisher.publish_artifact(ctx, source, "web_script")

            # 预检 — safe 模式不需要 LIVE_WEB
            gate_config = GateConfig(live_web=True)  # 显式开启以通过门控
            preflight = ExecutionPreflight(layout, gate_config)
            result = preflight.check(ctx, track="web")

            assert result.is_passed()
            assert result.run_id == "run_080"

    def test_preflight_manifest_missing(self):
        with tempfile.TemporaryDirectory() as tmp:
            layout = DirectoryLayout(tmp)

            # 不发布产物，直接预检
            ctx = create_run_context(project_id="PR-15", run_id="run_080")
            gate_config = GateConfig(live_web=True)
            preflight = ExecutionPreflight(layout, gate_config)
            result = preflight.check(ctx, track="web")

            assert result.is_blocked()
            assert result.reason_code == "MANIFEST_MISSING"

    def test_preflight_hash_mismatch_after_modify(self):
        with tempfile.TemporaryDirectory() as tmp:
            layout = DirectoryLayout(tmp)

            # 发布产物
            source = Path(tmp) / "test_script.py"
            source.write_text("print('hello')", encoding="utf-8")

            ctx = create_run_context(project_id="PR-15", run_id="run_080", artifact_id="web-test-001")
            publisher = ArtifactPublisher(layout)
            publisher.publish_artifact(ctx, source, "web_script")

            # 修改文件
            run_dir = ctx.run_dir(layout)
            target = run_dir / "test_script.py"
            target.write_text("print('modified')", encoding="utf-8")

            # 预检应该失败
            gate_config = GateConfig(live_web=True)
            preflight = ExecutionPreflight(layout, gate_config)
            result = preflight.check(ctx, track="web")

            assert result.is_blocked()
            assert result.reason_code == "SCRIPT_HASH_MISMATCH"

    def test_preflight_project_mismatch(self):
        with tempfile.TemporaryDirectory() as tmp:
            layout = DirectoryLayout(tmp)

            source = Path(tmp) / "test_script.py"
            source.write_text("print('hello')", encoding="utf-8")

            ctx = create_run_context(project_id="PR-15", run_id="run_080", artifact_id="web-test-001")
            publisher = ArtifactPublisher(layout)
            publisher.publish_artifact(ctx, source, "web_script")

            # 用错误的 project_id 预检 — 但 run_id 相同，所以会找到 manifest 然后发现 project 不匹配
            # 实际上由于目录结构是 projects/{project_id}/...，PR-16 的 run_080 是一个全新空目录
            # 所以应该报 MANIFEST_MISSING。要测试 PROJECT_MISMATCH，需要让两个项目共享同一个 run_dir
            # 这里改为验证：PR-16 的 run_080 确实找不到 manifest
            wrong_ctx = create_run_context(project_id="PR-16", run_id="run_080")
            gate_config = GateConfig(live_web=True)
            preflight = ExecutionPreflight(layout, gate_config)
            result = preflight.check(wrong_ctx, track="web")

            assert result.is_blocked()
            # PR-16 的 run_080 目录不存在 manifest
            assert result.reason_code == "MANIFEST_MISSING"

    def test_preflight_run_id_mismatch(self):
        with tempfile.TemporaryDirectory() as tmp:
            layout = DirectoryLayout(tmp)

            source = Path(tmp) / "test_script.py"
            source.write_text("print('hello')", encoding="utf-8")

            ctx = create_run_context(project_id="PR-15", run_id="run_079", artifact_id="web-test-001")
            publisher = ArtifactPublisher(layout)
            publisher.publish_artifact(ctx, source, "web_script")

            # run_080 不能读取 run_079 — 不同 run_id = 不同目录
            wrong_ctx = create_run_context(project_id="PR-15", run_id="run_080")
            gate_config = GateConfig(live_web=True)
            preflight = ExecutionPreflight(layout, gate_config)
            result = preflight.check(wrong_ctx, track="web")

            assert result.is_blocked()
            assert result.reason_code == "MANIFEST_MISSING"

    def test_preflight_safe_mode_blocks_live(self):
        with tempfile.TemporaryDirectory() as tmp:
            layout = DirectoryLayout(tmp)

            source = Path(tmp) / "test_script.py"
            source.write_text("print('hello')", encoding="utf-8")

            ctx = create_run_context(project_id="PR-15", run_id="run_080", artifact_id="web-test-001", mode="live")
            publisher = ArtifactPublisher(layout)
            publisher.publish_artifact(ctx, source, "web_script")

            # 安全模式门控（默认 LIVE_WEB=0）
            preflight = ExecutionPreflight(layout)
            result = preflight.check(ctx, track="web")

            assert result.is_blocked()
            assert result.reason_code == "GATE_BLOCKED"

    def test_preflight_live_mode_allowed(self):
        with tempfile.TemporaryDirectory() as tmp:
            layout = DirectoryLayout(tmp)

            source = Path(tmp) / "test_script.py"
            source.write_text("print('hello')", encoding="utf-8")

            ctx = create_run_context(project_id="PR-15", run_id="run_080", artifact_id="web-test-001", mode="live")
            publisher = ArtifactPublisher(layout)
            publisher.publish_artifact(ctx, source, "web_script")

            # 显式开启 live
            gate_config = GateConfig(live_web=True)
            preflight = ExecutionPreflight(layout, gate_config)
            result = preflight.check(ctx, track="web")

            assert result.is_passed()

    def test_require_passed_raises(self):
        with tempfile.TemporaryDirectory() as tmp:
            layout = DirectoryLayout(tmp)

            ctx = create_run_context(project_id="PR-15", run_id="run_080")
            gate_config = GateConfig(live_web=True)
            preflight = ExecutionPreflight(layout, gate_config)

            with pytest.raises(PreflightBlockedError) as exc_info:
                preflight.require_passed(ctx, track="web")

            assert exc_info.value.result.reason_code == "MANIFEST_MISSING"


# ---------------------------------------------------------------------------
# 端到端流程测试
# ---------------------------------------------------------------------------

class TestEndToEndFlow:
    def test_full_publish_preflight_execute_flow(self):
        """完整流程：发布 -> 预检 -> 执行令牌"""
        with tempfile.TemporaryDirectory() as tmp:
            layout = DirectoryLayout(tmp)

            # 1. 创建脚本
            source = Path(tmp) / "final_script.py"
            source.write_text("# test script\nprint('ok')", encoding="utf-8")

            # 2. 创建运行上下文
            ctx = create_run_context(
                project_id="PR-15",
                run_id="run_080",
                artifact_id="web-test-001",
                mode="safe",
            )

            # 3. 发布产物
            publisher = ArtifactPublisher(layout)
            published = publisher.publish_artifact(ctx, source, "web_script")
            assert publisher.is_published(ctx)

            # 4. 预检
            gate_config = GateConfig(live_web=True)
            preflight = ExecutionPreflight(layout, gate_config)
            result = preflight.require_passed(ctx, track="web")
            assert result.is_passed()

            # 5. 验证目录结构
            run_dir = ctx.run_dir(layout)
            assert (run_dir / "final_script.py").exists()
            assert (run_dir / "manifest.json").exists()
            assert (run_dir / "script_provenance.json").exists()

    def test_concurrent_runs_no_overlap(self):
        """并发运行不会互相覆盖"""
        with tempfile.TemporaryDirectory() as tmp:
            layout = DirectoryLayout(tmp)
            publisher = ArtifactPublisher(layout)

            # run_079
            source1 = Path(tmp) / "script1.py"
            source1.write_text("print('run_079')", encoding="utf-8")
            ctx1 = create_run_context(project_id="PR-15", run_id="run_079", artifact_id="web-test-001")
            pub1 = publisher.publish_artifact(ctx1, source1, "web_script")

            # run_080
            source2 = Path(tmp) / "script2.py"
            source2.write_text("print('run_080')", encoding="utf-8")
            ctx2 = create_run_context(project_id="PR-15", run_id="run_080", artifact_id="web-test-002")
            pub2 = publisher.publish_artifact(ctx2, source2, "web_script")

            # 验证两个产物独立存在
            assert pub1.artifact_path.exists()
            assert pub2.artifact_path.exists()
            assert pub1.artifact_path != pub2.artifact_path

            # 验证各自预检通过
            gate_config = GateConfig(live_web=True)
            preflight = ExecutionPreflight(layout, gate_config)
            assert preflight.check(ctx1, track="web").is_passed()
            assert preflight.check(ctx2, track="web").is_passed()

    def test_unpublished_blocks_execution(self):
        """未发布产物不能执行"""
        with tempfile.TemporaryDirectory() as tmp:
            layout = DirectoryLayout(tmp)

            ctx = create_run_context(project_id="PR-15", run_id="run_080")
            gate_config = GateConfig(live_web=True)
            preflight = ExecutionPreflight(layout, gate_config)
            result = preflight.check(ctx, track="web")

            assert result.is_blocked()
            assert result.reason_code == "MANIFEST_MISSING"
            assert "未发布" in result.message


# ---------------------------------------------------------------------------
# 便捷函数测试
# ---------------------------------------------------------------------------

class TestConvenienceFunctions:
    def test_publish_script(self):
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "test.py"
            source.write_text("print('ok')", encoding="utf-8")

            ctx = create_run_context(project_id="PR-15", run_id="run_080", artifact_id="web-test-001")
            published = publish_script(ctx, source, "web_script")

            assert published.artifact_path.exists()

    def test_preflight_check(self):
        with tempfile.TemporaryDirectory() as tmp:
            layout = DirectoryLayout(tmp)
            source = Path(tmp) / "test.py"
            source.write_text("print('ok')", encoding="utf-8")

            ctx = create_run_context(project_id="PR-15", run_id="run_080", artifact_id="web-test-001")
            publisher = ArtifactPublisher(layout)
            publisher.publish_artifact(ctx, source, "web_script")

            gate_config = GateConfig(live_web=True)
            result = preflight_check(ctx, track="web", gate_config=gate_config)
            assert result.is_passed()
