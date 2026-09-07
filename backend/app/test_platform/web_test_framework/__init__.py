# -*- coding: utf-8 -*-
"""P2-2: Web 稳定层 — 主链路回归、分层回归、负向隔离、结果回写。

目标：让 Web 测试能长期复跑，页面流程、明细、结果弹窗一致。
"""

from __future__ import annotations

from enum import Enum
from typing import Any, Optional


# ---------------------------------------------------------------------------
# Web 回归层级
# ---------------------------------------------------------------------------

class WebRegressionLayer(str, Enum):
    """Web 回归分层"""
    MAIN_FLOW = "main_flow"       # 主链路：核心业务流程
    LAYERED = "layered"           # 分层回归：按模块/页面分层
    NEGATIVE = "negative"         # 负向隔离：异常场景独立执行
    SANITY = "sanity"             # 快速冒烟：关键路径验证


# 层级说明
WEB_LAYER_DESCRIPTIONS: dict[WebRegressionLayer, str] = {
    WebRegressionLayer.MAIN_FLOW: "主链路：端到端核心业务流程",
    WebRegressionLayer.LAYERED: "分层回归：按功能模块逐层验证",
    WebRegressionLayer.NEGATIVE: "负向隔离：异常输入、错误路径独立执行",
    WebRegressionLayer.SANITY: "快速冒烟：关键路径快速验证",
}


# ---------------------------------------------------------------------------
# Web 运行配置
# ---------------------------------------------------------------------------

class WebRunConfig:
    """Web 测试运行配置"""

    def __init__(
        self,
        base_url: str,
        layers: Optional[list[WebRegressionLayer]] = None,
        headless: bool = True,
        viewport: Optional[dict[str, int]] = None,
        timeout_ms: int = 60000,
        screenshot_on_failure: bool = True,
        screenshot_on_success: bool = False,
    ):
        self.base_url = base_url
        self.layers = layers or [WebRegressionLayer.SANITY]
        self.headless = headless
        self.viewport = viewport or {"width": 1680, "height": 950}
        self.timeout_ms = timeout_ms
        self.screenshot_on_failure = screenshot_on_failure
        self.screenshot_on_success = screenshot_on_success

    def to_dict(self) -> dict[str, Any]:
        return {
            "base_url": self.base_url,
            "layers": [l.value for l in self.layers],
            "headless": self.headless,
            "viewport": self.viewport,
            "timeout_ms": self.timeout_ms,
            "screenshot_on_failure": self.screenshot_on_failure,
            "screenshot_on_success": self.screenshot_on_success,
        }


# ---------------------------------------------------------------------------
# 结果回写
# ---------------------------------------------------------------------------

class WebResultWriteback:
    """Web 测试结果回写 — 截图、self_reflect、index.html"""

    def __init__(self, run_id: str, output_dir: str):
        self.run_id = run_id
        self.output_dir = output_dir

    def write_result(
        self,
        status: str,  # passed | failed | skipped
        steps: list[dict[str, Any]],
        screenshots: list[str],
        error_message: Optional[str] = None,
    ) -> dict[str, Any]:
        """写入 self_reflect_result.json 格式的结果"""
        import json
        from pathlib import Path

        result = {
            "execution_status": status,
            "run_name": self.run_id,
            "steps": steps,
            "screenshots": screenshots,
        }
        if error_message:
            result["error_message"] = error_message

        output_path = Path(self.output_dir) / "self_reflect_result.json"
        output_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")

        return result

    def build_index_html(self) -> Optional[str]:
        """构建 index.html（复用 WebTestService 的逻辑）"""
        from pathlib import Path
        try:
            from app.services.web_test_service import _build_webwright_index_html
            path = _build_webwright_index_html(Path(self.output_dir))
            return str(path) if path else None
        except ImportError:
            return None


# ---------------------------------------------------------------------------
# 分层回归运行器
# ---------------------------------------------------------------------------

class WebRegressionRunner:
    """Web 分层回归运行器"""

    def __init__(self, config: WebRunConfig):
        self.config = config

    def run_layer(self, layer: WebRegressionLayer) -> dict[str, Any]:
        """运行指定回归层级"""
        return {
            "layer": layer.value,
            "status": "skipped",
            "reason": "WebRegressionRunner.run_layer 为抽象接口，需子类实现",
        }

    def run_all(self) -> list[dict[str, Any]]:
        """运行所有配置的层级"""
        results = []
        for layer in self.config.layers:
            results.append(self.run_layer(layer))
        return results

    def should_run_negative_separately(self) -> bool:
        """负向场景是否应独立执行（默认 True，避免污染主链路）"""
        return WebRegressionLayer.NEGATIVE in self.config.layers
