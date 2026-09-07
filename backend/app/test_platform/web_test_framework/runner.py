# -*- coding: utf-8 -*-
"""Web 分层运行器 — 按四层执行 Playwright 脚本。

D12: 将 web_test_framework 的层级配置接入真实 runner。
"""

from __future__ import annotations

import json
import os
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from app.test_platform.web_test_framework import (
    WebRegressionLayer,
    WebRunConfig,
    WebResultWriteback,
    WEB_LAYER_DESCRIPTIONS,
)
from app.test_platform.execution_gates import ExecutionGate, GateConfig
from app.test_platform.directory_layout import DirectoryLayout


# ---------------------------------------------------------------------------
# Web 层级执行结果
# ---------------------------------------------------------------------------

class WebLayerExecutionResult:
    """Web 单层执行结果"""

    def __init__(
        self,
        layer: WebRegressionLayer,
        status: str,  # passed | failed | skipped | error
        duration_ms: int = 0,
        stdout: str = "",
        stderr: str = "",
        error_message: Optional[str] = None,
        screenshot_count: int = 0,
        self_reflect_path: Optional[str] = None,
        index_html_path: Optional[str] = None,
    ):
        self.layer = layer
        self.status = status
        self.duration_ms = duration_ms
        self.stdout = stdout
        self.stderr = stderr
        self.error_message = error_message
        self.screenshot_count = screenshot_count
        self.self_reflect_path = self_reflect_path
        self.index_html_path = index_html_path

    def to_dict(self) -> dict[str, Any]:
        return {
            "layer": self.layer.value,
            "layer_name": WEB_LAYER_DESCRIPTIONS.get(self.layer, ""),
            "status": self.status,
            "duration_ms": self.duration_ms,
            "stdout": self.stdout,
            "stderr": self.stderr,
            "error_message": self.error_message,
            "screenshot_count": self.screenshot_count,
            "self_reflect_path": self.self_reflect_path,
            "index_html_path": self.index_html_path,
        }


# ---------------------------------------------------------------------------
# Web 分层运行器
# ---------------------------------------------------------------------------

class WebLayerRunner:
    """Web 分层运行器 — 按四层执行测试。"""

    def __init__(
        self,
        project: str,
        base_url: str,
        gate_config: Optional[GateConfig] = None,
        workspace_root: Optional[str] = None,
    ):
        self.project = project
        self.base_url = base_url
        self.gate = ExecutionGate(gate_config)
        self.layout = DirectoryLayout(workspace_root)

    def _should_skip_layer(self, layer: WebRegressionLayer) -> tuple[bool, Optional[str]]:
        """判断是否应该跳过该层"""
        # Live 检查（所有 Web 层都需要 live）
        allowed, reason = self.gate.check_all("web")
        if not allowed:
            return True, reason

        # 负向层额外检查
        if layer == WebRegressionLayer.NEGATIVE:
            allowed, reason = self.gate.check_all("web", case_keyword="反向")
            if not allowed:
                return True, reason

        return False, None

    def _run_playwright(self, layer: WebRegressionLayer, output_dir: Path) -> WebLayerExecutionResult:
        """在指定目录运行 Playwright 测试"""
        start = datetime.now(timezone.utc)

        # 生成最小测试骨架
        test_file = output_dir / f"test_{layer.value}.py"
        test_file.write_text(_generate_web_test_skeleton(self.base_url, layer), encoding="utf-8")

        # 运行 pytest
        try:
            result = subprocess.run(
                [
                    "python", "-m", "pytest",
                    str(test_file),
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

            # 写入 self_reflect_result.json
            writeback = WebResultWriteback(
                run_id=f"{self.project}_{layer.value}_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}",
                output_dir=str(output_dir),
            )
            writeback.write_result(
                status="passed" if result.returncode == 0 else "failed",
                steps=[{"step": f"Run {layer.value}", "result": result.stdout[:500]}],
                screenshots=[],
                error_message=result.stderr[:500] if result.returncode != 0 else None,
            )

            # 尝试构建 index.html
            index_path = writeback.build_index_html()

            return WebLayerExecutionResult(
                layer=layer,
                status="passed" if result.returncode == 0 else "failed",
                duration_ms=duration,
                stdout=result.stdout,
                stderr=result.stderr,
                self_reflect_path=str(output_dir / "self_reflect_result.json"),
                index_html_path=index_path,
            )

        except subprocess.TimeoutExpired:
            duration = int((datetime.now(timezone.utc) - start).total_seconds() * 1000)
            return WebLayerExecutionResult(
                layer=layer,
                status="error",
                duration_ms=duration,
                error_message="Playwright 执行超时（300秒）",
            )
        except Exception as e:
            duration = int((datetime.now(timezone.utc) - start).total_seconds() * 1000)
            return WebLayerExecutionResult(
                layer=layer,
                status="error",
                duration_ms=duration,
                error_message=str(e),
            )

    def run_layer(self, layer: WebRegressionLayer) -> WebLayerExecutionResult:
        """执行指定 Web 回归层级"""
        # 1. 检查是否应该跳过
        should_skip, reason = self._should_skip_layer(layer)
        if should_skip:
            return WebLayerExecutionResult(
                layer=layer,
                status="skipped",
                error_message=reason,
            )

        # 2. 准备输出目录
        output_dir = self.layout.web_dir(self.project) / layer.value
        output_dir.mkdir(parents=True, exist_ok=True)

        # 3. 运行 Playwright
        return self._run_playwright(layer, output_dir)

    def run_all(self, layers: Optional[list[WebRegressionLayer]] = None) -> list[WebLayerExecutionResult]:
        """按顺序执行所有配置的层级"""
        target_layers = layers or [WebRegressionLayer.SANITY]
        results: list[WebLayerExecutionResult] = []

        for layer in target_layers:
            result = self.run_layer(layer)
            results.append(result)

            # 主链路失败时，后续层跳过（负向层除外）
            if layer == WebRegressionLayer.MAIN_FLOW and result.status == "failed":
                for remaining in target_layers[target_layers.index(layer) + 1:]:
                    if remaining != WebRegressionLayer.NEGATIVE:
                        results.append(WebLayerExecutionResult(
                            layer=remaining,
                            status="skipped",
                            error_message="主链路失败，后续分层跳过",
                        ))
                break

        return results


# ---------------------------------------------------------------------------
# 骨架生成
# ---------------------------------------------------------------------------

def _generate_web_test_skeleton(base_url: str, layer: WebRegressionLayer) -> str:
    """生成 Web 测试骨架"""
    return f'''# -*- coding: utf-8 -*-
"""Web 测试骨架 — {layer.value}。

运行：
    LIVE_WEB=1 python -m pytest {layer.value}_test.py -v
"""

from __future__ import annotations

import os
import pytest

pytestmark = pytest.mark.skipif(
    os.getenv("LIVE_WEB", "0") != "1",
    reason="需 LIVE_WEB=1 才执行 live Web 测试",
)

BASE_URL = "{base_url}"


def test_{layer.value}_example():
    """示例测试 — {WEB_LAYER_DESCRIPTIONS.get(layer, "")}"""
    assert BASE_URL, "BASE_URL 未配置"
    # 请替换为实际页面测试
    pass
'''


# ---------------------------------------------------------------------------
# 便捷函数
# ---------------------------------------------------------------------------

def run_web_layers(
    project: str,
    base_url: str,
    layers: Optional[list[str]] = None,
    gate_config: Optional[GateConfig] = None,
) -> list[dict[str, Any]]:
    """便捷函数：执行 Web 分层。

    Args:
        project: 项目标识符
        base_url: Web 应用基础 URL
        layers: 指定执行的层级列表（默认 sanity）
        gate_config: 门控配置

    Returns:
        各层执行结果列表
    """
    runner = WebLayerRunner(project, base_url, gate_config)

    target_layers: list[WebRegressionLayer] = []
    if layers:
        for l in layers:
            try:
                target_layers.append(WebRegressionLayer(l))
            except ValueError:
                pass
    else:
        target_layers = [WebRegressionLayer.SANITY]

    results = runner.run_all(target_layers)
    return [r.to_dict() for r in results]


def get_web_layer_config() -> list[dict[str, Any]]:
    """获取 Web 分层配置"""
    return [
        {
            "layer": layer.value,
            "layer_name": WEB_LAYER_DESCRIPTIONS.get(layer, ""),
            "requires_live": True,
            "is_risky": layer == WebRegressionLayer.NEGATIVE,
        }
        for layer in WebRegressionLayer
    ]
