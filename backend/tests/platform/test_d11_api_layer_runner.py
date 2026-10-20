# -*- coding: utf-8 -*-
"""D11 测试：API 分层运行器测试。"""

import pytest
import os
from unittest.mock import patch, MagicMock

from app.test_platform.api_test_framework.api_layer import ApiLayer
from app.test_platform.api_test_framework.runner import (
    APILayerRunner,
    LayerExecutionResult,
    run_api_layers,
    get_api_layer_config,
)
from app.test_platform.execution_gates import GateConfig


class TestAPILayerRunner:
    """测试 APILayerRunner"""

    def test_init_default(self):
        runner = APILayerRunner("PROJ-1")
        assert runner.project == "PROJ-1"
        # 默认 QUERY 启用，WRITE/COMPOSITE/NEGATIVE 禁用
        assert runner.layer_configs[ApiLayer.QUERY].enabled is True
        assert runner.layer_configs[ApiLayer.WRITE].enabled is False
        assert runner.layer_configs[ApiLayer.NEGATIVE].enabled is False

    def test_set_layer_enabled(self):
        runner = APILayerRunner("PROJ-1")
        runner.set_layer_enabled(ApiLayer.WRITE, True)
        assert runner.layer_configs[ApiLayer.WRITE].enabled is True

    def test_should_skip_layer_disabled(self):
        runner = APILayerRunner("PROJ-1")
        should_skip, reason = runner._should_skip_layer(ApiLayer.WRITE)
        assert should_skip is True
        assert "未启用" in reason

    def test_should_skip_layer_safe_mode(self):
        """安全模式下，AUTH 层默认 enabled=True + requires_live=True，会被跳过"""
        # 显式传入 safe mode 配置（避免 .env 中 LIVE_API=1 的影响）
        config = GateConfig(live_api=False, live_web=False)
        runner = APILayerRunner("PROJ-1", gate_config=config)
        assert runner.layer_configs[ApiLayer.AUTH].enabled is True
        assert runner.layer_configs[ApiLayer.AUTH].requires_live is True
        should_skip, reason = runner._should_skip_layer(ApiLayer.AUTH)
        assert should_skip is True, f"Expected skip but got: {reason}"

    def test_should_skip_layer_query_safe_mode(self):
        """安全模式下，QUERY 层也会被跳过（requires_live=True）"""
        config = GateConfig(live_api=False, live_web=False)
        runner = APILayerRunner("PROJ-1", gate_config=config)
        assert runner.layer_configs[ApiLayer.QUERY].enabled is True
        assert runner.layer_configs[ApiLayer.QUERY].requires_live is True
        should_skip, reason = runner._should_skip_layer(ApiLayer.QUERY)
        assert should_skip is True, f"Expected skip but got: {reason}"

    def test_should_skip_layer_live_mode(self):
        """live 模式下，QUERY 应该可以执行"""
        config = GateConfig(live_api=True)
        runner = APILayerRunner("PROJ-1", gate_config=config)
        should_skip, reason = runner._should_skip_layer(ApiLayer.QUERY)
        assert should_skip is False

    def test_should_skip_layer_risky_denied(self):
        """风险层需要显式开启 RUN_RISKY_NEG"""
        config = GateConfig(live_api=True, run_risky_negative=False)
        runner = APILayerRunner("PROJ-1", gate_config=config)
        # 先启用 NEGATIVE
        runner.set_layer_enabled(ApiLayer.NEGATIVE, True)
        should_skip, reason = runner._should_skip_layer(ApiLayer.NEGATIVE)
        assert should_skip is True
        # 检查 reason 中是否包含 RUN_RISKY_NEG 或 "反向" 关键词提示
        assert any(k in str(reason) for k in ["RUN_RISKY_NEG", "反向", "边界"])

    def test_should_skip_layer_risky_allowed(self):
        """风险层显式开启后应该可以执行"""
        config = GateConfig(live_api=True, run_risky_negative=True)
        runner = APILayerRunner("PROJ-1", gate_config=config)
        runner.set_layer_enabled(ApiLayer.NEGATIVE, True)
        should_skip, reason = runner._should_skip_layer(ApiLayer.NEGATIVE)
        assert should_skip is False

    def test_get_layer_status(self):
        runner = APILayerRunner("PROJ-1")
        status = runner.get_layer_status()
        assert len(status) == 6
        assert status[0]["layer"] == "p0_auth"
        assert status[1]["layer"] == "p1_query"

    def test_layer_execution_result_to_dict(self):
        result = LayerExecutionResult(
            layer=ApiLayer.QUERY,
            status="passed",
            duration_ms=1000,
            test_count=5,
            passed_count=5,
        )
        d = result.to_dict()
        assert d["layer"] == "p1_query"
        assert d["status"] == "passed"
        assert d["test_count"] == 5


class TestRunAPILayers:
    """测试便捷函数"""

    def test_get_api_layer_config(self):
        config = get_api_layer_config("PROJ-1")
        assert len(config) == 6
        assert config[0]["layer"] == "p0_auth"

    @patch("app.test_platform.api_test_framework.runner.subprocess.run")
    def test_run_api_layers_safe_mode(self, mock_run):
        """安全模式下只跑 QUERY，其他层 skip"""
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout="test_module.py::test_example PASSED \n",
            stderr="",
        )

        results = run_api_layers("PROJ-1")

        # QUERY 被执行（mock）
        assert results[1]["status"] == "passed"
        # WRITE 被跳过
        assert results[2]["status"] == "skipped"

    @patch("app.test_platform.api_test_framework.runner.subprocess.run")
    def test_run_api_layers_with_live(self, mock_run):
        """live 模式下可以执行更多层"""
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout="test_module.py::test_example PASSED \n",
            stderr="",
        )

        config = GateConfig(live_api=True)
        results = run_api_layers("PROJ-1", gate_config=config)

        # AUTH 和 QUERY 应该被执行
        assert results[0]["status"] == "passed"
        assert results[1]["status"] == "passed"


class TestLayerExecutionResultParsing:
    """测试结果解析"""

    def test_passed_parsing(self):
        stdout = """
test_auth.py::test_login PASSED
test_auth.py::test_token PASSED
2 passed in 0.5s
"""
        result = LayerExecutionResult(
            layer=ApiLayer.AUTH,
            status="passed",
            stdout=stdout,
            test_count=2,
            passed_count=2,
        )
        assert result.passed_count == 2
        assert result.failed_count == 0

    def test_failed_parsing(self):
        result = LayerExecutionResult(
            layer=ApiLayer.QUERY,
            status="failed",
            error_message="Connection refused",
            test_count=1,
            failed_count=1,
        )
        assert result.status == "failed"
        assert result.error_message == "Connection refused"
