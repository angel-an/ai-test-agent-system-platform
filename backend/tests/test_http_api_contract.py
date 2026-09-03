"""执行治理层 rev32 P2 补测：HTTP API 契约（TestClient 覆盖路由层）。

评审意见：新增 HTTP 测试直接调用 service/route 函数并使用 mock，未通过 TestClient
覆盖 HTTP 状态码、SuccessResponse 序列化及运行接口外层的 final 呈现。本文件补齐：
1. POST /api/v2/mcp/call（save_web_test_script 缺 sub_function_id）
   → HTTP 200 + McpCallResponse 序列化 {result, error, final:true}；
2. POST /api/v2/projects/{id}/web-tests/{wid}/run（授权终局拒绝）
   → HTTP 200 + SuccessResponse {success:true, data:{...final:true, guard:...}}
   ——记录运行接口外层 final 呈现契约（终局拒绝内嵌于 data）。
"""

from types import SimpleNamespace
from unittest import mock

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.v2.mcp_proxy import router as mcp_router
from app.api.v2.web_tests import get_web_test_service, router as web_tests_router
from app.config.database import get_db


def _mock_db():
    async def _gen():
        yield None

    return _gen


def _build_app(service):
    """构造最小测试 app：mcp 路由 + web-tests 路由，依赖覆盖。"""
    app = FastAPI()
    app.include_router(mcp_router, prefix="/api/v2")
    app.include_router(web_tests_router, prefix="/api/v2")
    app.dependency_overrides[get_db] = _mock_db()

    async def _override_service():
        yield service

    app.dependency_overrides[get_web_test_service] = _override_service
    return app


class TestMcpCallHttpContract:
    def test_save_web_test_script_missing_sub_function_id_http_final(self):
        app = _build_app(service=None)
        client = TestClient(app)
        resp = client.post("/api/v2/mcp/call", json={
            "server": {"id": "internal-test-tools", "name": "internal", "enabled": True},
            "tool_name": "save_web_test_script",
            "args": {"script_content": "print(1)", "language": "python"},
        })
        # HTTP 状态码与 McpCallResponse 序列化
        assert resp.status_code == 200
        body = resp.json()
        assert body["error"] is None
        assert body["final"] is True  # 外层 final 呈现
        assert "sub_function_id" in body["result"]


class TestRunWebTestHttpContract:
    WT = "00000000-0000-0000-0000-0000000000b1"

    def test_run_authorization_denial_http_contract(self):
        """授权终局拒绝：HTTP 200 + SuccessResponse，final 内嵌于 data。"""
        service = SimpleNamespace(
            run_web_test=mock.AsyncMock(return_value={
                "run_id": "run-1",
                "identifier": "WTR-1",
                "status": "failed",
                "guard": "script_provenance",
                "final": True,
                "error_message": "脚本来源授权拒绝（三要素绑定不成立）",
            })
        )
        app = _build_app(service=service)
        client = TestClient(app)
        resp = client.post(
            f"/api/v2/projects/PR-1/web-tests/{self.WT}/run",
            json={"timeout": 60},
        )
        # SuccessResponse 序列化：HTTP 200 + success:true + data 内嵌 final/guard
        assert resp.status_code == 200
        body = resp.json()
        assert body["success"] is True
        data = body["data"]
        assert data["guard"] == "script_provenance"
        assert data["final"] is True
        assert data["status"] == "failed"
        assert "授权" in data["error_message"]
        service.run_web_test.assert_awaited_once()

    def test_run_success_http_contract(self):
        """放行场景：HTTP 200 + data 正常字段（无 guard/final 残留）。"""
        service = SimpleNamespace(
            run_web_test=mock.AsyncMock(return_value={
                "run_id": "run-2",
                "identifier": "WTR-2",
                "status": "completed",
                "total_tests": 1,
                "passed_tests": 1,
                "stdout": "OK",
            })
        )
        app = _build_app(service=service)
        client = TestClient(app)
        resp = client.post(
            f"/api/v2/projects/PR-1/web-tests/{self.WT}/run",
            json={"timeout": 60},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["success"] is True
        data = body["data"]
        assert data["status"] == "completed"
        assert data.get("guard") is None  # 成功路径不残留 guard/final
        assert data.get("final") is None
