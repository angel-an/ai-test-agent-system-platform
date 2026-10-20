# -*- coding: utf-8 -*-
"""P5 分页契约接线：契约配置 + 生成脚本接线（阶段 B）。

背景：平台生成的 API 脚本原先只断言 HTTP 2xx，而小杨生煎是**信封式 SUT**
（业务失败也返 HTTP 200）→ 该断言恒真 → DEFECT-002（越界返回 ``data:null``）
在平台自动执行下必然漏检。

本文件覆盖**生成器层**（不跑 Playwright）：配置解析、匹配规则、注入与否、
降级行为、脚本自洽性。真实执行结论由
``test_api_pagination_contract_e2e.py`` 用本地受控服务端验收。
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.services import api_pagination_contracts as contracts
from app.services.api_case_link_service import APICaseLinkService
from app.services.api_pagination_contracts import (
    PAGINATION_CONTRACTS,
    REQUIRED_CORE_SYMBOLS,
    PaginationContract,
    load_contract_core,
    resolve_pagination_contract,
)

REPO = Path(__file__).resolve().parents[2]
API_WORKSPACE = REPO / "backend" / "workspace" / "api"
TSC = API_WORKSPACE / "node_modules" / "typescript" / "bin" / "tsc"
MATRIX_TS = API_WORKSPACE / "tests" / "p5_pagination_contract_matrix.spec.ts"


def _render(path: str, method: str = "GET") -> str:
    svc = object.__new__(APICaseLinkService)
    endpoint = SimpleNamespace(
        method=method, path=path, request_body=None, parameters=[],
        display_name=f"{method} {path}",
    )
    return svc._generate_playwright_script(endpoint)


def _scratch(tag: str) -> Path:
    """自建临时目录。

    不用 pytest 的 ``tmp_path``：本机默认 basetemp
    （``%TEMP%\\pytest-of-<user>``）会 ``PermissionError``，
    那会让"环境问题"伪装成"断言失败"。临时目录统一落在 workspace 下，
    既能被 node/tsc 解析到 node_modules，也确定可写。
    """
    d = API_WORKSPACE / f".p5_scratch_{tag}_{uuid4().hex[:8]}"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _cleanup(path: Path) -> None:
    try:
        shutil.rmtree(path, ignore_errors=True)
    except BaseException:  # noqa: BLE001  受管环境可能对批量删除 SystemExit
        pass


def _parse_matrix() -> list[dict[str, str]]:
    """解析手跑矩阵的 ``MATRIX`` 数据表（真值来源）。"""
    text = MATRIX_TS.read_text(encoding="utf-8")
    body = text.split("const MATRIX: Row[] = [", 1)[1].split("];", 1)[0]
    rows: list[dict[str, str]] = []
    for block in re.finditer(r"\{(.*?)\}", body, re.DOTALL):
        chunk = block.group(1)
        row: dict[str, str] = {}
        for key in ("name", "path", "provenance", "expectOutOfRange", "knownIssueId", "note"):
            found = re.search(rf"{key}:\s*'([^']*)'", chunk)
            if found:
                row[key] = found.group(1)
        if row.get("path"):
            rows.append(row)
    return rows


def _top_level_symbols(core: str) -> set[str]:
    """核心段里**顶层**声明的符号（函数内局部变量不属于此列）。"""
    return set(re.findall(r"(?m)^(?:function|const|let|type)\s+([A-Za-z_$][\w$]*)", core))


# --------------------------------------------------------------------------- #
# 一、契约配置
# --------------------------------------------------------------------------- #
class TestContractConfig:
    def test_python_config_matches_the_hand_run_matrix_row_by_row(self):
        """Python 配置必须与手跑矩阵（ts）逐行一致。

        这是"平台结论与手跑一致"的**配置层**防线：两边任何一处漂移
        （改了 ts 忘了改 py，或反之）都会让平台与手跑得出不同结论，
        而那种分叉在报告里表现为"平台说通过、手跑说失败"，最难归因。
        """
        rows = _parse_matrix()
        assert rows, "未能从手跑矩阵解析出行（格式变了？）"
        by_path = {c.endpoint_path: c for c in PAGINATION_CONTRACTS}
        assert set(by_path) == {r["path"] for r in rows}, "配置与矩阵的接口集合不一致"
        for row in rows:
            contract = by_path[row["path"]]
            assert contract.provenance == row["provenance"], row["path"]
            assert contract.expect_out_of_range == row["expectOutOfRange"], row["path"]
            assert contract.known_issue_id == row.get("knownIssueId", ""), row["path"]
            assert contract.note == row["note"], row["path"]

    def test_known_issue_expiry_matches_the_matrix(self):
        """有效期也要与矩阵对齐 —— 否则平台会比手跑早/晚失效，两边结论分叉。"""
        text = MATRIX_TS.read_text(encoding="utf-8")
        until = re.findall(r"until:\s*'([^']+)'", text)
        assert len(set(until)) == 1, f"矩阵里的 known_issue 有效期不唯一：{until}"
        for contract in PAGINATION_CONTRACTS:
            if contract.expect_out_of_range == "known_issue":
                assert contract.known_issue_until == until[0], contract.endpoint_path

    def test_defect_002_subject_is_five_endpoints(self):
        known = [c for c in PAGINATION_CONTRACTS if c.expect_out_of_range == "known_issue"]
        assert len(known) == 5
        assert {c.known_issue_id for c in known} == {"DEFECT-002"}
        unverified = {c.endpoint_path for c in PAGINATION_CONTRACTS
                      if c.provenance == "unverified"}
        assert unverified == {"/v1/recommend/list", "/v1/slide/page"}

    def test_known_issue_requires_ticket_and_expiry(self):
        """没有票据/有效期的 known_issue 会变成永久豁免 —— 配置期就该拒绝。"""
        with pytest.raises(ValueError, match="known_issue_id"):
            PaginationContract(endpoint_path="/x", provenance="generic_params",
                               expect_out_of_range="known_issue", known_issue_until="2026-12-31")
        with pytest.raises(ValueError, match="known_issue_until"):
            PaginationContract(endpoint_path="/x", provenance="generic_params",
                               expect_out_of_range="known_issue", known_issue_id="DEFECT-002")

    def test_unknown_provenance_and_expectation_are_rejected(self):
        with pytest.raises(ValueError, match="provenance"):
            PaginationContract(endpoint_path="/x", provenance="guessed",
                               expect_out_of_range="structure")
        with pytest.raises(ValueError, match="expect_out_of_range"):
            PaginationContract(endpoint_path="/x", provenance="ui_observed",
                               expect_out_of_range="probably_fine")

    def test_ambiguous_paths_are_rejected_at_config_time(self):
        """互为后缀的路径会让匹配产生歧义（结论张冠李戴）——导入时即拒绝。"""
        with pytest.raises(ValueError, match="互为后缀"):
            contracts._validate_contracts((
                PaginationContract(endpoint_path="/v1/a/list", provenance="ui_observed",
                                   expect_out_of_range="structure"),
                PaginationContract(endpoint_path="/a/list", provenance="ui_observed",
                                   expect_out_of_range="structure"),
            ))

    def test_duplicate_paths_are_rejected(self):
        with pytest.raises(ValueError, match="重复路径"):
            contracts._validate_contracts((
                PaginationContract(endpoint_path="/v1/a", provenance="ui_observed",
                                   expect_out_of_range="structure"),
                PaginationContract(endpoint_path="/v1/a", provenance="generic_params",
                                   expect_out_of_range="structure"),
            ))


class TestPathResolution:
    def test_exact_match(self):
        got = resolve_pagination_contract("/v1/purchaseLimit/list")
        assert got is not None and got.expect_out_of_range == "known_issue"

    def test_gateway_prefixed_path_still_matches(self):
        """OpenAPI 里的 path 常带网关注册前缀；只做精确匹配会整类漏掉。"""
        got = resolve_pagination_contract(
            "/yundt-saas-gateway/api/mini-mgmt-app/v1/purchaseLimit/list")
        assert got is not None and got.endpoint_path == "/v1/purchaseLimit/list"

    @pytest.mark.parametrize("path", [
        "/v1/unknown/thing", "/v1/purchaseLimit/listExtra", "", None, "   ",
    ])
    def test_unknown_path_is_not_claimed(self, path):
        """无契约 → 不注入。宁可少断言，也不把规则套到别的接口上。"""
        assert resolve_pagination_contract(path) is None


# --------------------------------------------------------------------------- #
# 二、契约核心段（同源抽取）
# --------------------------------------------------------------------------- #
class TestContractCore:
    def test_core_is_extracted_from_the_single_source_of_truth(self):
        core = load_contract_core()
        assert core, "核心段必须可加载（生成脚本依赖它）"
        for symbol in REQUIRED_CORE_SYMBOLS:
            assert symbol in core, f"核心段缺少 {symbol!r}"
        assert "export " not in core, "内联到 spec 前必须剥离 export"

    def test_core_is_extractable_by_the_node_test_too(self):
        """与 ``test_api_response_contract_node.py`` 用同一对标记，标记缺失会双输。"""
        text = contracts.CONTRACT_CORE_TS.read_text(encoding="utf-8")
        assert contracts.CONTRACT_CORE_BEGIN in text
        assert contracts.CONTRACT_CORE_END in text


# --------------------------------------------------------------------------- #
# 三、生成脚本接线
# --------------------------------------------------------------------------- #
class TestWiring:
    def test_non_pagination_endpoint_gets_no_pagination_assertions(self):
        """未命中契约的接口：不引入分页符号、不额外请求。

        注意名字里的口径：**不是**"完全不碰" —— 信封断言仍会注入（按接口数据解析字段名；
        未声明时降级为只判可解析 + 在报告里 push `envelope_check_skipped` 注解）。
        `testInfo` 为**无条件注入**（渲染模板的信封降级分支是静态文本、固定引用它），
        因此这里不再断言"不需要 testInfo"。
        """
        script = _render("/v1/some/other/endpoint")
        assert "checkPaginationContract" not in script
        assert "reportContractResult" not in script
        assert "buildPageUrl" not in script
        assert "async ({ request }, testInfo)" in script
        assert script.count("await request.get(") == 1
        # 信封层：未声明形状 → 降级且留痕（不静默）
        assert "ENVELOPE_CHECK_SKIPPED" in script
        assert "envelope_check_skipped" in script

    def test_known_issue_endpoint_gets_the_full_assertion_stack(self):
        script = _render("/v1/purchaseLimit/list")
        # 第 3 层：越界页探测 + 判定
        assert "buildPageUrl(buildUrl(\"/v1/purchaseLimit/list\"), 1)" in script
        assert "const OUT_OF_RANGE_PAGE = 999999;" in script
        assert "checkPaginationContract(" in script
        assert "reportContractResult(" in script
        # 第 2 层：正常页结构
        assert "const normalContract = checkPaginationContract(envelope, { pageNum: 1 });" in script
        # known_issue 例外必须带上票据与有效期（否则等于永久豁免）
        assert '"id": "DEFECT-002"' in script and '"until": "2026-12-31"' in script
        # 机读结论出口
        assert "P5_CONTRACT " in script
        assert "async ({ request }, testInfo)" in script

    def test_structure_endpoint_has_no_known_issue_exemption(self):
        script = _render("/v1/store/binding/list")
        assert "checkPaginationContract(" in script
        # 精确看判定调用的实参：参照组必须传 null（不能带例外）
        call = script.split("const oorContract = checkPaginationContract(", 1)[1].split(");", 1)[0]
        assert "DEFECT-002" not in call
        assert re.search(r"'structure'|\bnull\b", call), call
        assert re.search(r"\{\s*pageNum: OUT_OF_RANGE_PAGE\s*\}\s*,\s*null\s*,", script), \
            "参照组不得携带 known_issue 例外"

    def test_unverified_endpoint_is_skipped_explicitly(self):
        """参数未确认的接口必须显式 skip：报告里 skipped，绝不伪装成通过。"""
        script = _render("/v1/recommend/list")
        assert "test.skip(true," in script
        assert "provenance=unverified" in script
        # 参数都没确认，结构断言没有意义 → 不该注入
        assert "checkPaginationContract" not in script
        assert "buildPageUrl" not in script

    def test_out_of_range_page_also_checks_the_envelope(self):
        """越界页必须先判 resultCode。

        否则"越界页 + 凭据失效"也会得到 ``data:null``，被 known_issue 吸收成"已知问题"
        —— 那等于用 DEFECT-002 掩盖另一个问题。
        """
        script = _render("/v1/purchaseLimit/list")
        oor = script.split("const oorCode =", 1)[1]
        assert "AUTH_OR_BUSINESS_ENVELOPE" in oor
        assert script.count("await request.get(") == 2, "正常页 + 越界页各一次"
        # 越界页判定必须出现在契约判定之前，否则结论用的是没验过的数据
        assert oor.index("AUTH_OR_BUSINESS_ENVELOPE") < oor.index("checkPaginationContract(")
        # 且必须判 resultCode == '0' —— 只判"字段存在"时，越界页返回 401 也会落到
        # data:null，被 known_issue 例外吸收（实测漏过一轮，见 e2e 的 TestOutOfRangeEnvelope）
        before_contract = oor.split("checkPaginationContract(", 1)[0]
        assert "toBe('0')" in before_contract
        assert ".not.toBe('')" not in before_contract, \
            "越界页信封判定过弱：401 有 resultCode 字段，not.toBe('') 拦不住它"

    def test_missing_core_degrades_visibly(self, monkeypatch):
        """核心段拿不到 → 降级为仅信封断言，但**必须留痕**（不许静默少断言）。"""
        monkeypatch.setattr(
            "app.services.api_case_link_service.load_contract_core", lambda: None)
        script = _render("/v1/purchaseLimit/list")
        assert "checkPaginationContract" not in script
        assert "pagination_contract_skipped" in script
        assert "契约核心段未加载" in script
        # 阶段 A 的信封断言仍在（降级后仍能堵住"认证失败被误判为通过"）
        assert "AUTH_OR_BUSINESS_ENVELOPE" in script

    def test_inlined_core_does_not_collide_with_generator_symbols(self):
        """核心段的**顶层**符号不得与生成脚本注入的符号撞名。

        同一作用域内重名会静默改语义（后声明的覆盖先声明的），而 TS 在
        ``--skipLibCheck`` 下不一定报错。只查顶层：核心段内部的局部变量
        （``raw``/``text`` 等）各自在自己的函数里，撞名无碍。
        """
        core = load_contract_core()
        assert core
        symbols = _top_level_symbols(core)
        assert symbols, "未能解析出核心段顶层符号（抽取结果不对？）"
        script = _render("/v1/purchaseLimit/list")
        for name in sorted(symbols):
            # 允许 0~2 空格缩进：脚本顶层=0，核心段内联进 test 回调后=2；函数内局部（4+）不算
            occurrences = len(re.findall(
                rf"(?m)^\s{{0,2}}(?:function|const|let|type)\s+{re.escape(name)}\b", script))
            assert occurrences == 1, f"{name} 被定义了 {occurrences} 次（撞名）"

    def test_gateway_prefixed_endpoint_is_still_wired(self):
        script = _render("/yundt-saas-gateway/api/mini-mgmt-app/v1/purchase/list")
        assert "checkPaginationContract(" in script
        assert "P5_CONTRACT " in script


class TestRenderedScriptSyntax:
    """渲染产物必须能编译 —— 注入的是 TS 代码，语法错会整轮执行炸掉。"""

    @pytest.mark.skipif(
        shutil.which("node") is None or not TSC.exists(),
        reason="需要本机 node 与 workspace/api 的 typescript",
    )
    def test_rendered_scripts_compile(self):
        # 必须建在 workspace/api 下：产物 import '@playwright/test'，要能解析到那里的 node_modules
        workdir = _scratch("syntax")
        try:
            for name, path in {
                "plain": "/v1/some/other/endpoint",
                "known": "/v1/purchaseLimit/list",
                "structure": "/v1/store/binding/list",
                "unverified": "/v1/recommend/list",
            }.items():
                (workdir / f"{name}.ts").write_text(_render(path), encoding="utf-8")
            proc = subprocess.run(
                ["node", str(TSC), "--noEmit", "--target", "es2020", "--module", "esnext",
                 "--moduleResolution", "node", "--skipLibCheck",
                 *[str(p) for p in sorted(workdir.glob("*.ts"))]],
                capture_output=True, cwd=str(API_WORKSPACE),
            )
            out = proc.stdout.decode("utf-8", "replace") + proc.stderr.decode("utf-8", "replace")
            errors = [l for l in out.splitlines() if "error TS" in l]
            assert errors == [], "渲染产物编译失败：\n" + "\n".join(errors[:10])
        finally:
            _cleanup(workdir)


class TestNodeAndPythonAgree:
    """Python 侧抽取的核心段，与 node 侧执行的核心段必须是同一份。"""

    @pytest.mark.skipif(shutil.which("node") is None, reason="需要本机 node")
    def test_extracted_core_runs_and_agrees(self):
        core = load_contract_core()
        assert core
        workdir = _scratch("agree")
        try:
            driver = workdir / "agree.mts"
            driver.write_text(core + "\n" + (
                "console.log(JSON.stringify(checkPaginationContract("
                "  {resultCode: '0', data: null}, {pageNum: 999999},"
                "  {id: 'DEFECT-002', until: '2099-12-31', today: '2026-09-30'})));"
            ), encoding="utf-8")
            proc = subprocess.run(["node", str(driver)], capture_output=True)
            assert proc.returncode == 0, proc.stderr.decode("utf-8", "replace")[-600:]
            payload = [l for l in proc.stdout.decode("utf-8", "replace").splitlines()
                       if l.startswith("{")]
            result = json.loads(payload[-1])
            # 与手跑矩阵同一判定：data:null + 有效票据 → known_issue（**不是**通过）
            assert result["ok"] is False
            assert result["knownIssue"] is True and result["knownIssueId"] == "DEFECT-002"
        finally:
            _cleanup(workdir)

    @pytest.mark.skipif(shutil.which("node") is None, reason="需要本机 node")
    def test_extracted_core_flags_expired_ticket_as_plain_failure(self):
        core = load_contract_core()
        assert core
        workdir = _scratch("expired")
        try:
            driver = workdir / "expired.mts"
            driver.write_text(core + "\n" + (
                "console.log(JSON.stringify(checkPaginationContract("
                "  {resultCode: '0', data: null}, {pageNum: 999999},"
                "  {id: 'DEFECT-002', until: '2026-09-01', today: '2026-09-30'})));"
            ), encoding="utf-8")
            proc = subprocess.run(["node", str(driver)], capture_output=True)
            assert proc.returncode == 0, proc.stderr.decode("utf-8", "replace")[-600:]
            payload = [l for l in proc.stdout.decode("utf-8", "replace").splitlines()
                       if l.startswith("{")]
            result = json.loads(payload[-1])
            assert result["knownIssue"] is False, "过期例外必须失效，否则缺陷修完也没人知道"
            assert "分页契约失败" in result["summary"]
        finally:
            _cleanup(workdir)
