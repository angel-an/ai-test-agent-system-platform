# -*- coding: utf-8 -*-
"""API 分层运行器 — 按六层执行 pytest 骨架。

D11: 将 api_layer.py 的分层配置接到真实 runner。
"""

from __future__ import annotations

import os
import subprocess
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from app.test_platform.api_test_framework.api_layer import (
    ApiLayer,
    LAYER_ORDER,
    LAYER_DESCRIPTIONS,
    DEFAULT_LAYER_CONFIGS,
    LayerConfig,
    generate_test_skeleton,
)
from app.test_platform.execution_gates import ExecutionGate, GateConfig
from app.test_platform.directory_layout import DirectoryLayout
from app.test_platform.report_templates import TestResult, WriteBackRecord


# ---------------------------------------------------------------------------
# 分层执行结果
# ---------------------------------------------------------------------------

class LayerExecutionResult:
    """单层执行结果"""

    def __init__(
        self,
        layer: ApiLayer,
        status: str,  # passed | failed | skipped | error
        duration_ms: int = 0,
        stdout: str = "",
        stderr: str = "",
        error_message: Optional[str] = None,
        test_count: int = 0,
        passed_count: int = 0,
        failed_count: int = 0,
        skipped_count: int = 0,
    ):
        self.layer = layer
        self.status = status
        self.duration_ms = duration_ms
        self.stdout = stdout
        self.stderr = stderr
        self.error_message = error_message
        self.test_count = test_count
        self.passed_count = passed_count
        self.failed_count = failed_count
        self.skipped_count = skipped_count

    def to_dict(self) -> dict[str, Any]:
        return {
            "layer": self.layer.value,
            "layer_name": LAYER_DESCRIPTIONS.get(self.layer, ""),
            "status": self.status,
            "duration_ms": self.duration_ms,
            "stdout": self.stdout,
            "stderr": self.stderr,
            "error_message": self.error_message,
            "test_count": self.test_count,
            "passed_count": self.passed_count,
            "failed_count": self.failed_count,
            "skipped_count": self.skipped_count,
        }


# ---------------------------------------------------------------------------
# 分层运行器
# ---------------------------------------------------------------------------

class APILayerRunner:
    """API 分层运行器 — 按六层执行测试。"""

    def __init__(
        self,
        project: str,
        gate_config: Optional[GateConfig] = None,
        workspace_root: Optional[str] = None,
    ):
        self.project = project
        self.gate = ExecutionGate(gate_config)
        self.layout = DirectoryLayout(workspace_root)
        self.layer_configs: dict[ApiLayer, LayerConfig] = {}
        for layer in LAYER_ORDER:
            self.layer_configs[layer] = LayerConfig(
                layer=layer,
                enabled=DEFAULT_LAYER_CONFIGS[layer].enabled,
                requires_live=DEFAULT_LAYER_CONFIGS[layer].requires_live,
                is_risky=DEFAULT_LAYER_CONFIGS[layer].is_risky,
            )

    def set_layer_enabled(self, layer: ApiLayer, enabled: bool) -> None:
        """设置分层启用状态"""
        if layer in self.layer_configs:
            self.layer_configs[layer].enabled = enabled

    def _should_skip_layer(self, layer: ApiLayer) -> tuple[bool, Optional[str]]:
        """判断是否应该跳过该层"""
        config = self.layer_configs.get(layer)
        if not config:
            return True, "分层配置不存在"

        if not config.enabled:
            return True, f"{layer.value} 未启用"

        # Live 检查
        if config.requires_live:
            allowed, reason = self.gate.check_all("api")
            if not allowed:
                return True, reason

        # 风险检查
        if config.is_risky:
            allowed, reason = self.gate.check_all("api", case_keyword="反向")
            if not allowed:
                return True, reason

        return False, None

    def _generate_skeleton(self, layer: ApiLayer) -> Path:
        """生成 pytest 骨架到磁盘"""
        scripts_dir = self.layout.api_dir(self.project)
        layer_dir = scripts_dir / layer.value
        layer_dir.mkdir(parents=True, exist_ok=True)

        # 生成骨架
        skeleton = generate_test_skeleton(self.project, layer, output_dir=str(layer_dir))

        # 写入文件
        test_file = layer_dir / f"test_{layer.value}.py"
        test_file.write_text(skeleton["test_file"], encoding="utf-8")

        # 写入 conftest（如果不存在）
        conftest_file = layer_dir / "conftest.py"
        if not conftest_file.exists():
            conftest_file.write_text(skeleton["conftest"], encoding="utf-8")

        return layer_dir

    def _run_pytest(self, layer_dir: Path) -> LayerExecutionResult:
        """在指定目录运行 pytest"""
        layer_name = layer_dir.name
        try:
            # 尝试匹配 layer 名称
            layer = ApiLayer(layer_name)
        except ValueError:
            layer = ApiLayer.QUERY  # 默认

        start = datetime.now(timezone.utc)

        try:
            result = subprocess.run(
                [
                    "python", "-m", "pytest",
                    str(layer_dir),
                    "-v",
                    "--tb=short",
                    "-q",
                ],
                capture_output=True,
                text=True,
                timeout=300,
                env={**os.environ, "PYTHONPATH": os.getcwd()},
            )

            duration = int((datetime.now(timezone.utc) - start).total_seconds() * 1000)

            # 解析结果
            stdout = result.stdout
            stderr = result.stderr

            # 简单解析通过/失败数
            passed_count = stdout.count(" PASSED ")
            failed_count = stdout.count(" FAILED ")
            skipped_count = stdout.count(" SKIPPED ")
            error_count = stdout.count(" ERROR ")

            total = passed_count + failed_count + skipped_count + error_count

            if result.returncode == 0 or (total > 0 and failed_count == 0 and error_count == 0):
                status = "passed"
            elif failed_count > 0 or error_count > 0:
                status = "failed"
            else:
                status = "skipped"

            return LayerExecutionResult(
                layer=layer,
                status=status,
                duration_ms=duration,
                stdout=stdout,
                stderr=stderr,
                test_count=total,
                passed_count=passed_count,
                failed_count=failed_count + error_count,
                skipped_count=skipped_count,
            )

        except subprocess.TimeoutExpired:
            duration = int((datetime.now(timezone.utc) - start).total_seconds() * 1000)
            return LayerExecutionResult(
                layer=layer,
                status="error",
                duration_ms=duration,
                error_message="pytest 执行超时（300秒）",
            )
        except Exception as e:
            duration = int((datetime.now(timezone.utc) - start).total_seconds() * 1000)
            return LayerExecutionResult(
                layer=layer,
                status="error",
                duration_ms=duration,
                error_message=str(e),
            )

    def run_layer(self, layer: ApiLayer) -> LayerExecutionResult:
        """执行指定分层"""
        # 1. 检查是否应该跳过
        should_skip, reason = self._should_skip_layer(layer)
        if should_skip:
            return LayerExecutionResult(
                layer=layer,
                status="skipped",
                error_message=reason,
            )

        # 2. 生成骨架
        layer_dir = self._generate_skeleton(layer)

        # 3. 运行 pytest
        return self._run_pytest(layer_dir)

    def run_all(self) -> list[LayerExecutionResult]:
        """按顺序执行所有启用的分层"""
        results: list[LayerExecutionResult] = []

        for layer in LAYER_ORDER:
            result = self.run_layer(layer)
            results.append(result)

            # 如果准入层失败，停止执行
            if layer == ApiLayer.AUTH and result.status == "failed":
                # 跳过剩余层
                for remaining in LAYER_ORDER[LAYER_ORDER.index(layer) + 1:]:
                    results.append(LayerExecutionResult(
                        layer=remaining,
                        status="skipped",
                        error_message="准入层失败，后续分层跳过",
                    ))
                break

        return results

    def get_layer_status(self) -> list[dict[str, Any]]:
        """获取各层配置状态"""
        status_list = []
        for layer in LAYER_ORDER:
            config = self.layer_configs.get(layer)
            if config:
                status_list.append({
                    "layer": layer.value,
                    "layer_name": LAYER_DESCRIPTIONS.get(layer, ""),
                    "enabled": config.enabled,
                    "requires_live": config.requires_live,
                    "is_risky": config.is_risky,
                })
        return status_list


# ---------------------------------------------------------------------------
# 便捷函数
# ---------------------------------------------------------------------------

def run_api_layers(
    project: str,
    layers: Optional[list[str]] = None,
    gate_config: Optional[GateConfig] = None,
) -> list[dict[str, Any]]:
    """便捷函数：执行 API 分层。

    Args:
        project: 项目标识符
        layers: 指定执行的分层列表（默认执行所有启用的）
        gate_config: 门控配置（默认从环境变量读取）

    Returns:
        各层执行结果列表
    """
    runner = APILayerRunner(project, gate_config)

    # 如果指定了分层，只启用这些分层
    if layers:
        for layer in LAYER_ORDER:
            runner.set_layer_enabled(layer, layer.value in layers)

    results = runner.run_all()
    return [r.to_dict() for r in results]


def get_api_layer_config(project: str) -> list[dict[str, Any]]:
    """获取 API 分层配置"""
    runner = APILayerRunner(project)
    return runner.get_layer_status()
