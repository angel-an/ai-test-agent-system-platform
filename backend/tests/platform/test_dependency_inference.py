# -*- coding: utf-8 -*-
"""P0-3 依赖推断测试：只落库能证明的依赖，无证据保持为空。

两类断言，缺一不可：
1. **有证据时必须推断出依赖** —— 用合成 Collection（A 写变量、同 folder 内 B 引用）验证；
   否则"依赖为 0"可能只是功能没接上。
2. **无证据时必须为 0** —— 用真实美心 Collection 固化边界：
   该样本 47 个请求全部没有 test script，因此不存在变量写入，
   依赖数为 0 是**证据决定的正确结果**，而非缺陷。

证据链定义（与 `scripts/dependency_evidence_report.py` 一致）：
可证明依赖 = 变量被 A 写入（test script 中 `pm.*.set`），且被**同 folder 内次序更晚**的 B 引用。
跨 folder 不假设顺序（避免推测）。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

REAL_COLLECTION = Path(
    r"D:\测试管理\项目管理\美心项目\美心POS\API\美心.postman_collection.json"
)


def _collection(items: list[dict]) -> bytes:
    return json.dumps({
        "info": {
            "name": "依赖样本",
            "schema": "https://schema.getpostman.com/json/collection/v2.1.0/collection.json",
        },
        "item": items,
    }, ensure_ascii=False).encode("utf-8")


def _request(name: str, path: str, *, body: dict | None = None, test_script: list[str] | None = None) -> dict:
    item: dict = {
        "name": name,
        "request": {
            "method": "POST",
            "url": {"raw": f"https://uat.example.com{path}", "path": path.strip("/").split("/")},
        },
    }
    if body is not None:
        item["request"]["body"] = {"mode": "raw", "raw": json.dumps(body)}
    if test_script is not None:
        item["event"] = [{"listen": "test", "script": {"exec": test_script}}]
    return item


def _load_report_module(alias: str):
    """加载诊断脚本模块。

    必须先把模块登记进 ``sys.modules``：脚本内用了 ``@dataclass``，
    而 dataclass 处理注解时会通过 ``sys.modules[cls.__module__]`` 反查命名空间，
    未登记时抛 ``AttributeError: 'NoneType' object has no attribute '__dict__'``。
    """
    import importlib.util
    import sys

    script = Path(__file__).resolve().parents[2] / "scripts" / "dependency_evidence_report.py"
    spec = importlib.util.spec_from_file_location(alias, script)
    module = importlib.util.module_from_spec(spec)
    sys.modules[alias] = module
    spec.loader.exec_module(module)
    return module


class TestParserVariableWrites:
    """解析器必须从 test script 提取变量写入（依赖推断的唯一"产出"证据）。"""

    def test_extracts_pm_environment_set(self):
        from app.services.postman_collection_parser import PostmanCollectionParser

        coll = PostmanCollectionParser().parse(_collection([
            _request("write", "/api/login", test_script=[
                "const jsonData = pm.response.json();",
                "pm.environment.set('sessionKey', jsonData.Data.SessionKey);",
            ]),
        ]))
        writes = coll.requests[0].variable_writes or []
        assert writes, "应识别 pm.environment.set 写入"
        # 解析器输出键名是 `variable`（见 _extract_variable_writes）
        names = {w.get("variable") for w in writes}
        assert "sessionKey" in names, writes

    def test_extracts_collection_variables_set(self):
        from app.services.postman_collection_parser import PostmanCollectionParser

        coll = PostmanCollectionParser().parse(_collection([
            _request("write", "/api/login", test_script=[
                "pm.collectionVariables.set('caseId', '123');",
            ]),
        ]))
        writes = coll.requests[0].variable_writes or []
        names = {w.get("variable") for w in writes}
        assert "caseId" in names, writes

    def test_no_script_means_no_writes(self):
        from app.services.postman_collection_parser import PostmanCollectionParser

        coll = PostmanCollectionParser().parse(_collection([
            _request("plain", "/api/x"),
        ]))
        assert (coll.requests[0].variable_writes or []) == []


class TestDependencyInferenceWithEvidence:
    """有证据时，契约 dependencies 必须出现可落脚到具体契约的条目。"""

    async def _bundle_dependencies(self, items: list[dict]) -> list[dict]:
        """走真实的 parse_bundle 路径（内存 SQLite 不可用，故只校验契约侧 hints + 落库映射）。"""
        from app.models.api_asset import ApiInterfaceContract
        from app.services.api_bundle_service import ApiBundleService

        service = ApiBundleService.__new__(ApiBundleService)  # 不连库

        # 1) 解析 Collection
        from app.services.postman_collection_parser import PostmanCollectionParser

        collection = PostmanCollectionParser().parse(_collection(items))

        # 2) 契约侧依赖提示：复用真实实现
        hints = service._collect_dependency_hints(list(collection.requests))  # noqa: SLF001
        return hints

    async def test_hint_is_collected_from_write(self):
        hints = await self._bundle_dependencies([
            _request("login", "/api/json/api_memLogin", test_script=[
                "pm.environment.set('sessionKey', pm.response.json().Data.SessionKey);",
            ]),
        ])
        assert hints, "写入应产生依赖提示"
        assert hints[0]["variable"] == "sessionKey"
        assert hints[0]["from_request"] == "login"

    async def test_hint_targets_contract_by_endpoint_key(self):
        """提示的 variable 名应能落脚到 endpoint_key 包含它的契约。

        直接断言**真实实现**的目标解析规则（`_resolve_dependency_target`），
        不要在测试里复刻一份逻辑 —— 复刻版会与实现漂移，
        正是它掩盖了"匹配方向写反"这一缺陷。
        """
        from app.services.api_bundle_service import ApiBundleService

        class _C:
            def __init__(self, key, cid):
                self.endpoint_key = key
                self.id = cid

        contracts = [_C("api_other", "1"), _C("api_sessionKey", "2")]
        target = ApiBundleService._resolve_dependency_target("sessionKey", contracts, "9")
        assert target is not None, "变量 sessionKey 应落到 endpoint_key=api_sessionKey 的契约"
        assert target.endpoint_key == "api_sessionKey"

    async def test_no_target_for_unrelated_variable(self):
        """无法落脚到任何契约的变量必须保持无依赖，不得推测。"""
        from app.services.api_bundle_service import ApiBundleService

        class _C:
            def __init__(self, key, cid):
                self.endpoint_key = key
                self.id = cid

        contracts = [_C("api_other", "1"), _C("api_unrelated", "2")]
        target = ApiBundleService._resolve_dependency_target("someRandomVar", contracts, "9")
        assert target is None, "无关变量不得误匹配"


class TestRealCollectionBoundary:
    """真实样本：无 test script → 无变量写入 → 依赖必须为 0。"""

    def test_no_evidence_means_zero_dependencies(self):
        if not REAL_COLLECTION.exists():
            pytest.skip(f"真实样本不存在: {REAL_COLLECTION}")

        from app.services.postman_collection_parser import PostmanCollectionParser

        coll = PostmanCollectionParser().parse(REAL_COLLECTION.read_bytes())
        assert len(coll.requests) == 47, f"样本请求数应仍为 47，实际 {len(coll.requests)}"

        with_scripts = [r for r in coll.requests if (r.test_script or "").strip()]
        writes = sum(len(r.variable_writes or []) for r in coll.requests)

        assert with_scripts == [], (
            f"真实 Collection 出现了 {len(with_scripts)} 个 test script，"
            "请更新 P0-3 的验收预期（依赖可能不再为 0）"
        )
        assert writes == 0, f"真实 Collection 出现了 {writes} 次变量写入，请更新验收预期"

    def test_referenced_variables_exist_but_have_no_producer(self):
        """变量被引用但没有写入者 → 这正是 env_missing 冲突的来源，而非依赖。"""
        if not REAL_COLLECTION.exists():
            pytest.skip(f"真实样本不存在: {REAL_COLLECTION}")

        raw = json.loads(REAL_COLLECTION.read_text(encoding="utf-8"))
        import re

        var_re = re.compile(r"\{\{(\w+)\}\}")

        def collect(items):
            out = []
            for item in items or []:
                if "item" in item:
                    out += collect(item["item"])
                elif "request" in item:
                    out.append(item)
            return out

        referenced = set()
        for item in collect(raw.get("item")):
            referenced |= set(var_re.findall(json.dumps(item.get("request") or {}, ensure_ascii=False)))

        # 这些变量来自环境文件或缺失（env_missing 冲突），不是接口间依赖
        assert "sessionKey" in referenced
        assert "memberID" in referenced


class TestEvidenceReportScript:
    """诊断脚本必须可运行且结论与上一致（把"0 是正确结果"变成可复核事实）。"""

    def test_report_concludes_no_evidence_for_real_sample(self):
        if not REAL_COLLECTION.exists():
            pytest.skip(f"真实样本不存在: {REAL_COLLECTION}")

        module = _load_report_module("dep_report_real")
        report = module.build_report(REAL_COLLECTION)
        assert report["requests"] == 47
        assert report["evidence"]["variable_writes"] == 0
        assert report["provable_count"] == 0
        assert report["provable_dependencies"] == []
        assert "无证据" in report["conclusion"]

    def test_report_finds_dependencies_when_evidence_present(self):
        """有证据时诊断脚本必须报出依赖（否则"0"不可信）。"""
        import tempfile

        module = _load_report_module("dep_report_with_evidence")

        items = [
            _request("login", "/api/json/api_memLogin", test_script=[
                "pm.environment.set('sessionKey', pm.response.json().Data.SessionKey);",
            ]),
            _request("query", "/api/json/api_queryMember", body={"sessionKey": "{{sessionKey}}"}),
        ]
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "coll.json"
            path.write_bytes(_collection(items))
            report = module.build_report(path)

        assert report["evidence"]["variable_writes"] == 1
        assert report["provable_count"] == 1, report
        dep = report["provable_dependencies"][0]
        assert dep["from"] == "login"
        assert dep["to"] == "query"
        assert dep["variable"] == "sessionKey"

    def test_report_does_not_assume_cross_folder_order(self):
        """跨 folder 的顺序不构成证据（避免推测）。"""
        import tempfile

        module = _load_report_module("dep_report_cross_folder")

        items = [
            {"name": "A", "item": [
                _request("login", "/api/json/api_memLogin", test_script=[
                    "pm.environment.set('sessionKey', 'x');",
                ]),
            ]},
            {"name": "B", "item": [
                _request("query", "/api/json/api_query", body={"sessionKey": "{{sessionKey}}"}),
            ]},
        ]
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "coll.json"
            path.write_bytes(_collection(items))
            report = module.build_report(path)

        assert report["provable_count"] == 0, report["provable_dependencies"]
