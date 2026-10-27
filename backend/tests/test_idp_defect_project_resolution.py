"""API 失败 → IDP 登记：项目标识符必须能在后台会话里解析出来。

背景（2026-10-27 实测）
----------------------
``_process_idp_defect`` 原来写 ``if api_test.project and hasattr(...)``。
后台执行会话里的 api_test 来自 ``session.merge(api_test, load=False)``，
``project`` 关系未加载（默认 lazy="select"），在 async 上下文里访问直接抛：

    MissingGreenlet: greenlet_spawn has not been called; can't call await_only() here.

异常被外层「不影响测试结果」的 except 吞掉 —— 外部完全看不见，现象只是
"失败的单子一张都没建出来"。探针实测：修复前 ``register_from_api_failure``
调用次数 = **0**，即失败→缺陷登记整条链路静默空转。

本文件锁三件事：
1. 关系未加载时，按 project_id 单独查 identifier，**绝不访问 ``.project``**；
2. 关系已加载时直接取，不额外查库；
3. ``_process_idp_defect`` 端到端能把项目标识符交给登记服务（不是 UNKNOWN）。
"""

from __future__ import annotations

import inspect
from types import SimpleNamespace
from uuid import uuid4

import pytest

import app.services.api_test_executor as executor_mod
from app.services.api_test_executor import APITestExecutor


class _FakeResult:
    def __init__(self, value):
        self._value = value

    def scalar_one_or_none(self):
        return self._value


class _FakeSession:
    """只记录 execute 调用，不连库。"""

    def __init__(self, identifier="PR-9"):
        self.identifier = identifier
        self.statements: list = []

    async def execute(self, statement):
        self.statements.append(statement)
        return _FakeResult(self.identifier)


class _LazyTrap:
    """复刻后台会话里的 merge 产物：关系未加载，一访问就抛 MissingGreenlet。"""

    def __init__(self, project_id=None, base_url="https://uat.example.com"):
        self.__dict__["project_id"] = project_id
        self.__dict__["test_config"] = {"base_url": base_url}
        self.__dict__["test_case_id"] = uuid4()
        self.project_touched = False

    @property
    def project(self):  # pragma: no cover - 被访问即为缺陷
        self.project_touched = True
        raise RuntimeError(
            "MissingGreenlet: greenlet_spawn has not been called; can't call await_only() here."
        )


def _failed_results() -> list[dict]:
    return [{
        "error": {"message": "expect(received).toBe(expected) // 500 !== 200"},
        "request": {"url": "https://uat.example.com/api/v1/orders", "method": "POST",
                    "headers": {}, "postData": ""},
        "response": {"status": 500, "headers": {}, "body": ""},
    }]


@pytest.mark.asyncio
async def test_unloaded_relation_queries_identifier_without_touching_project():
    """未加载关系 → 按 project_id 查一列，且全程不碰 .project。"""
    api_test = _LazyTrap(project_id=uuid4())
    session = _FakeSession(identifier="PR-9")
    executor = APITestExecutor(session=session)

    key = await executor._resolve_source_project_key(api_test)

    assert key == "PR-9"
    assert api_test.project_touched is False, "不得访问未加载的 project 关系"
    assert len(session.statements) == 1


@pytest.mark.asyncio
async def test_loaded_relation_is_used_directly():
    """关系已加载 → 直接读，不产生额外查询。"""
    api_test = SimpleNamespace(
        project=SimpleNamespace(identifier="PR-2"),
        project_id=uuid4(),
    )
    session = _FakeSession()
    executor = APITestExecutor(session=session)

    key = await executor._resolve_source_project_key(api_test)

    assert key == "PR-2"
    assert session.statements == []


@pytest.mark.asyncio
async def test_missing_project_id_falls_back_to_none():
    """既没加载关系也没 project_id → 返回 None（交上层按项目未映射处理），不抛错。"""
    session = _FakeSession()
    executor = APITestExecutor(session=session)

    assert await executor._resolve_source_project_key(_LazyTrap(project_id=None)) is None
    assert session.statements == []


@pytest.mark.asyncio
async def test_process_idp_defect_passes_resolved_project_key(monkeypatch):
    """端到端：登记服务必须被调用，且拿到解析出来的项目标识符。"""
    calls: list[dict] = []

    class _SpyRegistrationService:
        def __init__(self, _session):
            pass

        async def register_from_api_failure(self, **kwargs):
            calls.append(kwargs)
            return None

    monkeypatch.setattr(executor_mod, "DefectRegistrationService", _SpyRegistrationService)

    session = _FakeSession(identifier="PR-9")
    executor = APITestExecutor(session=session)
    run_id = uuid4()

    await executor._process_idp_defect(
        run_id=run_id,
        api_test=_LazyTrap(project_id=uuid4()),
        test_name="POST /api/v1/orders",
        results=_failed_results(),
    )

    assert len(calls) == 1, "失败必须进入登记服务（历史 bug 是 0 次）"
    assert calls[0]["source_project_key"] == "PR-9"
    assert calls[0]["allow_create"] is True
    assert calls[0]["test_run_id"] == run_id


def test_swallowed_defect_error_logs_stacktrace():
    """被吞掉的 IDP 异常必须留堆栈，否则只看到"单子没建出来"。"""
    source = inspect.getsource(APITestExecutor._process_idp_defect)
    # 只看代码，不看注释：避免注释里写 exc_info=True 也能过
    code_lines = [
        line for line in source.splitlines()
        if line.strip() and not line.strip().startswith("#")
    ]
    code = "\n".join(code_lines)
    assert "exc_info=True" in code


# ==========================================================================
# 第二层：状态码语义 —— Playwright 的"用例状态"不是 HTTP 状态码
# ==========================================================================

@pytest.mark.asyncio
async def test_degraded_branch_reports_unknown_status_not_case_status(monkeypatch):
    """没有 request/response 证据时，status_code 必须是 None（未知）。

    历史实现取 ``results[-1]["status"]``，那是 Playwright 的用例状态字符串
    （"failed"/"timedOut"），当状态码传下去会让决策层 ``>= 500`` 抛 TypeError。
    """
    calls: list[dict] = []

    class _SpyRegistrationService:
        def __init__(self, _session):
            pass

        async def register_from_api_failure(self, **kwargs):
            calls.append(kwargs)
            return None

    monkeypatch.setattr(executor_mod, "DefectRegistrationService", _SpyRegistrationService)

    executor = APITestExecutor(session=_FakeSession(identifier="PR-2"))
    await executor._process_idp_defect(
        run_id=uuid4(),
        api_test=_LazyTrap(project_id=uuid4()),
        test_name="GET /health basic contract",
        results=[{
            "status": "failed",  # ← 用例状态，不是 HTTP 状态码
            "error": {"message": "expect(received).toBeLessThan(expected)"},
        }],
    )

    assert len(calls) == 1
    assert calls[0]["response_summary"]["status_code"] is None


def test_decision_normalizes_status_code_types():
    """决策层对状态码做类型收敛：非数字按"未观测到"，数字字符串按数值。"""
    from app.services.defect_decision_service import DefectDecisionService

    assert DefectDecisionService._as_status_code("failed") is None
    assert DefectDecisionService._as_status_code("timedOut") is None
    assert DefectDecisionService._as_status_code(True) is None
    assert DefectDecisionService._as_status_code(None) is None
    assert DefectDecisionService._as_status_code(503) == 503
    assert DefectDecisionService._as_status_code(" 503 ") == 503

    # 端到端不抛错：脏值不得让整个决策链崩掉
    result = DefectDecisionService.decide(
        test_status="failed",
        error_message="expect(received).toBe(expected)",
        response_status_code="failed",  # type: ignore[arg-type]
        expected_status_code=None,
    )
    assert result is not None

    # 数字字符串按真实状态码参与判定（503 属 SUT 证据）
    result = DefectDecisionService.decide(
        test_status="failed",
        error_message="request failed",
        response_status_code="503",  # type: ignore[arg-type]
        expected_status_code=None,
    )
    assert result.decision.value != "skip" or "自动化" not in result.reason
