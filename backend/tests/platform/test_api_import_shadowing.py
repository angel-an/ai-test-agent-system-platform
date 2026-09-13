# -*- coding: utf-8 -*-
"""回归测试：局部 import 不得遮蔽模块级 import（UnboundLocalError 防护）。

背景（真实缺陷）：
``backend/app/api/v2/ai.py`` 的 ``generate_from_document`` 曾在门控分支中调用
``record_generated``，而函数内部又写了 ``from ... import record_generated``。
Python 在编译期就会把该名字视为**函数局部变量**，于是同一函数中出现在
局部 import **之前**的调用会抛：

    UnboundLocalError: cannot access local variable 'record_generated'
    where it is not associated with a value

前端表现为「上传需求 → 点击生成测试用例 → 500」。

本测试对 API 层做 AST 静态扫描，防止同类问题再次引入。
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

API_DIR = Path(__file__).resolve().parents[2] / "app" / "api"


def _module_level_imported_names(tree: ast.Module) -> set[str]:
    """收集模块级 import 引入的名字。"""
    names: set[str] = set()
    for node in tree.body:
        if isinstance(node, ast.Import):
            for alias in node.names:
                names.add((alias.asname or alias.name).split(".")[0])
        elif isinstance(node, ast.ImportFrom):
            for alias in node.names:
                if alias.name != "*":
                    names.add(alias.asname or alias.name)
    return names


def _function_local_imports(func: ast.AST) -> list[tuple[str, int]]:
    """收集函数体内（不含嵌套函数/类）的 import 语句。"""
    found: list[tuple[str, int]] = []
    for node in ast.walk(func):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) and node is not func:
            continue
        if isinstance(node, ast.Import):
            for alias in node.names:
                found.append(((alias.asname or alias.name).split(".")[0], node.lineno))
        elif isinstance(node, ast.ImportFrom):
            for alias in node.names:
                if alias.name != "*":
                    found.append((alias.asname or alias.name, node.lineno))
    return found


def _scan_file(path: Path) -> list[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    module_names = _module_level_imported_names(tree)

    problems: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        local = [(name, lineno) for name, lineno in _function_local_imports(node) if name in module_names]
        if not local:
            continue

        # 该名字在本函数中最早被使用的位置（排除 import 语句自身）
        for name, import_line in local:
            uses = [
                n.lineno
                for n in ast.walk(node)
                if isinstance(n, ast.Name) and n.id == name and n.lineno < import_line
            ]
            also_called_earlier = [
                n.lineno
                for n in ast.walk(node)
                if isinstance(n, ast.Call)
                and isinstance(n.func, ast.Name)
                and n.func.id == name
                and n.lineno < import_line
            ]
            if uses or also_called_earlier:
                first = min(uses + also_called_earlier)
                problems.append(
                    f"{path.name}:{node.name}() 第 {first} 行使用了 '{name}'，"
                    f"但第 {import_line} 行又局部 import 了同名符号"
                    f"（会抛 UnboundLocalError）"
                )
    return problems


def _api_files() -> list[Path]:
    return sorted(API_DIR.rglob("*.py"))


class TestNoShadowingLocalImport:
    """API 层不得出现「局部 import 遮蔽模块级 import」"""

    def test_scan_is_not_empty(self):
        files = _api_files()
        assert files, f"未找到待扫描文件: {API_DIR}"

    def test_no_shadowing_local_import(self):
        problems: list[str] = []
        for path in _api_files():
            problems.extend(_scan_file(path))
        assert not problems, "发现局部 import 遮蔽模块级 import：\n  - " + "\n  - ".join(problems)


class TestScanDetectsKnownPattern:
    """确保扫描器本身有效：对已知缺陷形态必须报错"""

    @staticmethod
    def _write_sample(name: str, content: str) -> Path:
        """沙箱下 pytest tmp_path 可能不可用，改用自建可写临时目录。"""
        import tempfile

        directory = Path(tempfile.mkdtemp(prefix="import_shadow_"))
        path = directory / name
        path.write_text(content, encoding="utf-8")
        return path

    def test_detects_shadowing_pattern(self):
        sample = self._write_sample(
            "sample.py",
            "from app.gate import record_generated\n"
            "\n"
            "def handler(session_key, ok):\n"
            "    if not ok:\n"
            "        record_generated(session_key, [])\n"
            "        return 'blocked'\n"
            "    from app.gate import record_generated\n"
            "    record_generated(session_key, ['TC-1'])\n"
            "    return 'generated'\n",
        )
        problems = _scan_file(sample)
        assert problems, "扫描器未能识别遮蔽模式"
        assert "UnboundLocalError" in problems[0]

    def test_accepts_safe_pattern(self):
        sample = self._write_sample(
            "safe.py",
            "from app.gate import record_generated\n"
            "\n"
            "def handler(session_key, ok):\n"
            "    record_generated(session_key, [])\n"
            "    return 'ok'\n",
        )
        assert _scan_file(sample) == []


class TestGenerateFromDocumentGateBranch:
    """针对出错分支的定向回归：门控拦截路径必须能正常返回，不得抛异常"""

    @pytest.fixture()
    def client(self):
        import httpx
        from fastapi import FastAPI

        from app.api.v2.requirement_quality import router

        app = FastAPI()
        app.include_router(router, prefix="/api/v2")
        transport = httpx.ASGITransport(app=app)
        return httpx.AsyncClient(transport=transport, base_url="http://test")

    async def test_blocked_branch_returns_quality_report(self, client):
        """被门控拦截时返回质量报告（对应 ai.py 中调用 record_generated 的分支）。"""
        async with client:
            resp = await client.post(
                "/api/v2/projects/PR-1/requirements/generate-testcases",
                json={"requirement_text": "只有一句话的需求，既没有目标也没有流程。"},
            )
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["testcase_generation"]["status"] == "blocked"

    def test_ai_module_imports_are_available_at_module_scope(self):
        """ai.py 中门控分支用到的名字必须在模块级可用（防止再次被局部 import 遮蔽）。"""
        from app.api.v2 import ai as ai_mod

        for name in ("record_generated", "start_session", "update_session", "GateOverride"):
            assert hasattr(ai_mod, name), f"ai.py 缺少模块级导入: {name}"


class TestGenerateFromDocumentEndpoint:
    """真实调用 /ai/generate-from-document（就是报错的那个入口）

    覆盖两类分支：
    - 需求不完整 → 被门控拦截（报错发生在这里，session_key 已设置时访问 record_generated）；
    - 需求完整 → 通过门控并写入会话记录（验证 record_generated 在正常路径可用）。
    """

    BLOCKED_PRD = "只有一句话的需求，既没有目标也没有流程说明。"
    COMPLETE_PRD = """# 会员储值充值需求

优先级：P1

## 功能点清单

| 编号 | 名称 |
| --- | --- |
| FP-001 | 企业储值充值 |

### FP-001 企业储值充值

业务目标：企业客户可在后台为企业账户充值余额。
角色权限：企业管理员可操作；无权限用户访问时提示权限不足。
前置条件：已登录企业控制台并开通储值账户。
操作流程：进入储值账户列表，点击创建储值充值，输入手机号与充值金额后保存。
输入与数据规则：手机号必填；金额必填，金额精度两位小数。
业务规则：单笔充值金额上限 50000 元，超出时提示金额超限。
正常结果：保存后生成储值流水。
验收标准：充值成功后余额增加对应金额，流水可查询。
计算公式：充值后余额 = 原余额 + 充值金额，精度保留两位小数。
"""

    @pytest.fixture()
    def client(self, monkeypatch):
        import httpx
        from fastapi import FastAPI

        from app.api.deps import get_current_user_id, get_db
        from app.api.v2 import ai as ai_mod
        from app.test_platform.requirements import gate
        from app.test_platform.requirements.gate import (
            OVERRIDE_TOKEN_ENV,
            clear_session,
        )

        monkeypatch.delenv(OVERRIDE_TOKEN_ENV, raising=False)
        clear_session("it-session")

        # 桩掉落库：本测试只关心门控分支与 session 状态，不触碰数据库
        created_calls: list[dict] = []

        class _FakeService:
            def __init__(self, db):  # noqa: ANN001
                self.db = db

            async def create_test_case(self, **kwargs):  # noqa: ANN003
                created_calls.append(kwargs)

                class _Item:
                    identifier = "TC-9001"

                    def model_dump(self, **kw):  # noqa: ANN003
                        return {"identifier": self.identifier, "name": kwargs["data"].name}

                return _Item()

        monkeypatch.setattr(ai_mod, "TestCaseService", _FakeService)

        app = FastAPI()
        app.include_router(ai_mod.router, prefix="/api/v2")
        app.dependency_overrides[get_current_user_id] = lambda: "user-1"

        class _FakeDb:
            async def commit(self):
                return None

        app.dependency_overrides[get_db] = lambda: _FakeDb()

        transport = httpx.ASGITransport(app=app)
        client = httpx.AsyncClient(transport=transport, base_url="http://test")
        client._created_calls = created_calls  # type: ignore[attr-defined]
        client._gate = gate  # type: ignore[attr-defined]
        return client

    def _file(self, text: str, name: str = "req.md"):
        return {"file": (name, text.encode("utf-8"), "text/markdown")}

    async def test_blocked_requirement_does_not_500(self, client):
        """回归：需求不完整 + session_key → 必须返回 200 质量报告，而不是 500。"""
        async with client:
            resp = await client.post(
                "/api/v2/projects/PR-1/ai/generate-from-document",
                files=self._file(self.BLOCKED_PRD),
                data={"session_key": "it-session"},
            )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["success"] is False
        assert body["test_cases"] == []
        assert body["prd_quality"]["can_generate_testcases"] is False
        assert body["testcase_generation"]["status"] == "blocked"

        # 会话状态必须是 blocked（不得被误标为 generated）
        session = client._gate.get_session("it-session")  # type: ignore[attr-defined]
        assert session is not None
        assert session.status.value == "blocked"

    async def test_legacy_force_generate_still_does_not_bypass(self, client):
        """回归：传 force_generate=true 仍被门控拦截，且给出废弃提示。"""
        async with client:
            resp = await client.post(
                "/api/v2/projects/PR-1/ai/generate-from-document",
                files=self._file(self.BLOCKED_PRD),
                data={"session_key": "it-session", "force_generate": "true"},
            )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["success"] is False
        assert body["testcase_generation"]["status"] == "blocked"
        assert "warning" in body and "force_generate" in body["warning"]

    async def test_complete_requirement_reaches_record_generated(self, client):
        """回归：需求完整 → 通过门控并记录生成结果（验证 record_generated 正常路径可用）。"""
        async with client:
            resp = await client.post(
                "/api/v2/projects/PR-1/ai/generate-from-document",
                files=self._file(self.COMPLETE_PRD),
                data={"session_key": "it-session", "requirement_id": "REQ-1"},
            )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["success"] is True, body
        assert len(body["test_cases"]) > 0
        assert body["testcase_generation"]["status"] == "generated"

        session = client._gate.get_session("it-session")  # type: ignore[attr-defined]
        assert session is not None
        assert session.status.value == "generated"
        assert session.created_case_ids  # record_generated 确实被调用
