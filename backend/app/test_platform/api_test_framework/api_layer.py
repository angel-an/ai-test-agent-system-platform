# -*- coding: utf-8 -*-
"""P2-1: 固定 API 分层 — 准入/查询/写入/复合/负向/收口六层。

目标：让接口测试按同一框架展开，新项目可直接套用。
通用断言和客户端基类从实际项目骨架中抽象而来。
"""

from __future__ import annotations

from enum import Enum
from typing import Any, Optional


# ---------------------------------------------------------------------------
# API 分层枚举
# ---------------------------------------------------------------------------

class ApiLayer(str, Enum):
    """API 测试六层"""
    AUTH = "p0_auth"           # 准入：登录、鉴权
    QUERY = "p1_query"         # 查询：只读、列表、详情
    WRITE = "p1_write"         # 写入：增删改
    COMPOSITE = "p2_composite" # 复合：多步骤闭环
    NEGATIVE = "p2_negative"   # 负向：边界、异常
    CLEANUP = "p3_cleanup"     # 收口：对账、清理


# 分层顺序（执行顺序）
LAYER_ORDER = [
    ApiLayer.AUTH,
    ApiLayer.QUERY,
    ApiLayer.WRITE,
    ApiLayer.COMPOSITE,
    ApiLayer.NEGATIVE,
    ApiLayer.CLEANUP,
]


# 分层说明
LAYER_DESCRIPTIONS: dict[ApiLayer, str] = {
    ApiLayer.AUTH: "准入层：验证登录、token 获取、鉴权有效性",
    ApiLayer.QUERY: "查询层：只读接口验证，确认数据可见性和字段完整性",
    ApiLayer.WRITE: "写入层：增删改操作，验证数据变更正确性",
    ApiLayer.COMPOSITE: "复合层：多步骤业务流程闭环验证",
    ApiLayer.NEGATIVE: "负向层：边界条件、异常输入、权限不足场景",
    ApiLayer.CLEANUP: "收口层：对账、数据清理、状态恢复",
}


# ---------------------------------------------------------------------------
# 分层配置
# ---------------------------------------------------------------------------

class LayerConfig:
    """单分层配置"""

    def __init__(
        self,
        layer: ApiLayer,
        enabled: bool = True,
        requires_live: bool = False,
        is_risky: bool = False,
        test_files: Optional[list[str]] = None,
    ):
        self.layer = layer
        self.enabled = enabled
        self.requires_live = requires_live
        self.is_risky = is_risky
        self.test_files = test_files or []

    def to_dict(self) -> dict[str, Any]:
        return {
            "layer": self.layer.value,
            "enabled": self.enabled,
            "requires_live": self.requires_live,
            "is_risky": self.is_risky,
            "test_files": self.test_files,
            "description": LAYER_DESCRIPTIONS.get(self.layer, ""),
        }


# 默认分层配置（安全模式：只读层默认启用，写入层需 live）
DEFAULT_LAYER_CONFIGS: dict[ApiLayer, LayerConfig] = {
    ApiLayer.AUTH: LayerConfig(ApiLayer.AUTH, enabled=True, requires_live=True),
    ApiLayer.QUERY: LayerConfig(ApiLayer.QUERY, enabled=True, requires_live=True),
    ApiLayer.WRITE: LayerConfig(ApiLayer.WRITE, enabled=False, requires_live=True),
    ApiLayer.COMPOSITE: LayerConfig(ApiLayer.COMPOSITE, enabled=False, requires_live=True),
    ApiLayer.NEGATIVE: LayerConfig(ApiLayer.NEGATIVE, enabled=False, requires_live=True, is_risky=True),
    ApiLayer.CLEANUP: LayerConfig(ApiLayer.CLEANUP, enabled=False, requires_live=False),
}


# ---------------------------------------------------------------------------
# 骨架模板
# ---------------------------------------------------------------------------

PYTEST_SKELETON = '''# -*- coding: utf-8 -*-
"""{project} API 测试骨架 — {layer_desc}。

运行：
    # 默认（安全模式，无 live 时 skip）
    python -m pytest {test_path} -q

    # Live 执行
    LIVE_API=1 python -m pytest {test_path} -v
"""

from __future__ import annotations

import os

import pytest

pytestmark = pytest.mark.skipif(
    os.getenv("LIVE_API", "0") != "1",
    reason="需 LIVE_API=1 才执行 live 测试",
)


# 复用平台通用客户端和断言
# from app.test_platform.api_test_framework.assertion_helpers import ...


def test_{layer_name}_example():
    """示例测试 — 请替换为实际接口测试。"""
    pass
'''


CONFTEXT_SKELETON = '''# -*- coding: utf-8 -*-
"""pytest 共享配置 — {project} API 测试。"""

from __future__ import annotations

import os
from typing import Any

import pytest


# 环境变量读取
API_BASE_URL = os.getenv("API_BASE_URL", "")
ACCESS_TOKEN = os.getenv("ACCESS_TOKEN", "").strip()
LIVE = os.getenv("LIVE_API", "0") == "1"


class Resp:
    """极简响应包装"""

    def __init__(self, status_code: int, body: Any):
        self.status_code = status_code
        self._body = body

    def json(self) -> Any:
        return self._body


@pytest.fixture(scope="session")
def api_base_url() -> str:
    return API_BASE_URL


@pytest.fixture(scope="session")
def access_token() -> str:
    return ACCESS_TOKEN
'''


def generate_test_skeleton(
    project: str,
    layer: ApiLayer,
    output_dir: Optional[str] = None,
) -> dict[str, str]:
    """生成指定分层的 pytest 骨架文件内容。

    Returns:
        {"test_file": 内容, "conftest": 内容}
    """
    layer_desc = LAYER_DESCRIPTIONS.get(layer, "")
    test_path = f"tests/api/{project}/test_{layer.value}.py"

    test_content = PYTEST_SKELETON.format(
        project=project,
        layer_desc=layer_desc,
        test_path=test_path,
        layer_name=layer.value,
    )

    conftest_content = CONFTEXT_SKELETON.format(project=project)

    return {
        "test_file": test_content,
        "conftest": conftest_content,
        "test_path": test_path,
    }


# ---------------------------------------------------------------------------
# 通用断言助手（从业务骨架抽象）
# ---------------------------------------------------------------------------

def assert_envelope_ok(body: dict[str, Any] | None, result_code_key: str = "resultCode") -> None:
    """断言统一信封：HTTP 200 + resultCode=0"""
    assert body is not None, "返回体为空"
    assert str(body.get(result_code_key)) == "0", f"业务失败: {body}"


def assert_delta_within(actual: float, expected: float, tolerance: float = 0.011) -> None:
    """断言金额差异在容差内（默认 ±0.011，支持 0.01 级操作 + 舍入误差）"""
    assert abs(actual - expected) <= tolerance, f"差异 {actual - expected} 超出容差 {tolerance}"


def assert_field_exists(body: dict[str, Any], path: str) -> Any:
    """断言嵌套字段存在，支持点号路径（如 "data.list.0.phone"）"""
    parts = path.split(".")
    current = body
    for part in parts:
        if isinstance(current, dict):
            assert part in current, f"字段 '{path}' 缺失"
            current = current[part]
        elif isinstance(current, list):
            idx = int(part)
            assert idx < len(current), f"索引 {idx} 越界"
            current = current[idx]
        else:
            raise AssertionError(f"无法访问路径 '{path}'")
    return current


# ---------------------------------------------------------------------------
# 通用 API 客户端基类（从业务骨架抽象）
# ---------------------------------------------------------------------------

class BaseApiClient:
    """API 客户端基类 — 提供通用请求方法。"""

    def __init__(self, base_url: str, timeout_ms: int = 60000):
        self.base_url = base_url.rstrip("/")
        self.timeout_ms = timeout_ms

    def _full_url(self, path: str) -> str:
        return f"{self.base_url}{path}"

    def is_configured(self) -> bool:
        """检查客户端是否已配置（base_url 非空）"""
        return bool(self.base_url) and self.base_url != "http://"


class HttpApiClient(BaseApiClient):
    """HTTP API 客户端（httpx 实现）。"""

    def __init__(
        self,
        base_url: str,
        access_token: Optional[str] = None,
        headers: Optional[dict[str, str]] = None,
        timeout_ms: int = 60000,
    ):
        super().__init__(base_url, timeout_ms)
        self.access_token = access_token
        self._headers = headers or {}

    def _build_headers(self) -> dict[str, str]:
        h = dict(self._headers)
        if self.access_token:
            h["access-token"] = self.access_token
        return h

    def post(self, path: str, json: Any = None, **kwargs) -> dict[str, Any]:
        import httpx
        with httpx.Client(base_url=self.base_url, timeout=self.timeout_ms / 1000.0) as client:
            resp = client.post(path, json=json, headers=self._build_headers(), **kwargs)
            return {"status_code": resp.status_code, "body": _safe_json(resp)}

    def get(self, path: str, params: Optional[dict] = None, **kwargs) -> dict[str, Any]:
        import httpx
        with httpx.Client(base_url=self.base_url, timeout=self.timeout_ms / 1000.0) as client:
            resp = client.get(path, params=params, headers=self._build_headers(), **kwargs)
            return {"status_code": resp.status_code, "body": _safe_json(resp)}


def _safe_json(resp) -> Any:
    try:
        return resp.json()
    except Exception:
        return {"raw": resp.text[:500]}
