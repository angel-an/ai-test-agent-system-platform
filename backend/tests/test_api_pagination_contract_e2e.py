# -*- coding: utf-8 -*-
"""P5 分页契约接线：**真实执行**验收（本地受控服务端 + 平台渲染的脚本原样跑）。

为什么必须有这一层：生成器层只能证明"脚本里出现了断言"，不能证明"跑出来的结论对"。
本文件起一个本地 HTTP 服务端复刻**信封式 SUT**（业务失败也返 HTTP 200），
把平台渲染出的 spec **原样**丢给 Playwright 跑，验收设计文档写的三条标准：

* 阶段 A：凭据失效时报 ``AUTH_OR_BUSINESS_ENVELOPE``，而不是"通过"（堵住恒真断言）；
* 阶段 B：DEFECT-002 形态（越界 ``data:null``）被判为 **known_issue**（失败但标注问题单），
  与手跑矩阵 ``p5_pagination_contract_matrix.spec.ts`` 的结论一致；
* 阶段 C：``until`` 过期 → 从 known_issue 转为普通失败；缺陷修复后（越界返回完整空结构）
  **自动转绿**，无需人工清理例外标记。
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import threading
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import parse_qs, urlparse

import pytest

from app.services import api_pagination_contracts as contracts
from app.services.api_case_link_service import APICaseLinkService
from app.services.api_pagination_contracts import PaginationContract

REPO = Path(__file__).resolve().parents[2]
API_WORKSPACE = REPO / "backend" / "workspace" / "api"
NPX = shutil.which("npx")
pytestmark = pytest.mark.skipif(
    not (NPX and (API_WORKSPACE / "node_modules" / "@playwright" / "test").exists()),
    reason="需要本机 npx + @playwright/test（backend/workspace/api）",
)

#: 越界判定阈值：pageNum 大于它就当越界页（真实 SUT 的 pages 是 1 / 13 / 14 等）
PAGE_THRESHOLD = 100
#: 越界返回 data:null 的接口（DEFECT-002 主体形态）
DEFECT_PATHS = {"/v1/purchaseLimit/list", "/v1/purchase/list"}

PW_CONFIG = """import { defineConfig } from '@playwright/test';

export default defineConfig({
  testDir: '.',
  fullyParallel: false,
  workers: 1,
  retries: 0,
});
"""


def _render(path: str, method: str = "GET") -> str:
    svc = object.__new__(APICaseLinkService)
    endpoint = SimpleNamespace(method=method, path=path, request_body=None, parameters=[])
    return svc._generate_playwright_script(endpoint)


# --------------------------------------------------------------------------- #
# 受控服务端：复刻信封式 SUT
# --------------------------------------------------------------------------- #
class _Envelope(BaseHTTPRequestHandler):
    """全部响应 HTTP 200；业务结果只体现在 ``resultCode`` / ``data`` 上。

    这正是小杨生煎 SUT 的行为，也是"只断言 HTTP 2xx 等于没有断言"的根源。
    """

    mode: str = "defect_002"
    received: list[tuple[str, str]] = []

    def log_message(self, *args) -> None:  # noqa: D102 - 静音，避免污染 stdout 解析
        return

    def do_GET(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler 约定
        self.received.append(("GET", self.path))
        parsed = urlparse(self.path)
        query = parse_qs(parsed.query)
        raw_page = (query.get("pageNum") or ["1"])[0]
        try:
            page = int(raw_page)
        except ValueError:
            page = 1
        out_of_range = page > PAGE_THRESHOLD

        if self.mode == "expired_token":
            payload: dict = {"resultCode": "401", "resultMsg": "no login", "data": None}
        elif self.mode == "bad_normal":
            # 正常页结构就是坏的（list 不是数组、pages 缺失）→ 第 2 层断言必须抓到
            payload = {"resultCode": "0", "resultMsg": "success",
                       "data": {"list": "not-an-array", "total": 9}}
        elif self.mode == "oor_auth":
            # 正常页凭据有效、越界页返回 401：越界页若不单独判信封，它同样是 data:null，
            # 会被 known_issue 例外吸收成"已知问题"（用 DEFECT-002 掩盖另一个问题）
            payload = ({"resultCode": "401", "resultMsg": "no login", "data": None}
                       if out_of_range else
                       {"resultCode": "0", "resultMsg": "success",
                        "data": {"list": [], "total": 9, "pages": 1, "pageNum": page}})
        elif self.mode == "defect_002" and out_of_range and parsed.path in DEFECT_PATHS:
            # DEFECT-002：越界页 resultCode=0，但 data 为 null（调用方无法凭 resultCode 判断结构）
            payload = {"resultCode": "0", "resultMsg": "success", "data": None}
        else:
            # 正常页；以及"缺陷已修复"时的越界页（完整空结构）
            payload = {
                "resultCode": "0", "resultMsg": "success",
                "data": {"list": [], "total": 9, "pages": 1, "pageNum": page},
            }

        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def _serve(mode: str) -> tuple[ThreadingHTTPServer, str, list[tuple[str, str]]]:
    received: list[tuple[str, str]] = []
    handler = type("_H", (_Envelope,), {"mode": mode, "received": received})
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server, "127.0.0.1:%d" % server.server_address[1], received


def _scratch(tag: str) -> Path:
    d = API_WORKSPACE / f".p5_e2e_{tag}_{uuid.uuid4().hex[:8]}"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _cleanup(path: Path) -> None:
    try:
        shutil.rmtree(path, ignore_errors=True)
    except BaseException:  # noqa: BLE001  受管环境批量删除保护会 SystemExit
        pass


def _contract_lines(stdout: str) -> list[dict]:
    """解析脚本里的机读结论出口 ``P5_CONTRACT {...}``。"""
    out: list[dict] = []
    for line in stdout.splitlines():
        marker = "P5_CONTRACT "
        if marker not in line:
            continue
        payload = line.split(marker, 1)[1].strip()
        try:
            out.append(json.loads(payload))
        except json.JSONDecodeError:
            continue
    return out


def _run(specs: dict[str, str], mode: str = "defect_002") -> dict:
    """起服务端 → 把平台渲染的脚本写进去跑 → 收回结论。

    ``specs``：``{文件名: 接口路径}``；脚本内容一律由 ``_generate_playwright_script`` 产出，
    不在测试里另写一份（否则验收的就不是平台产物了）。
    """
    server, host, received = _serve(mode)
    workdir = _scratch(mode)
    try:
        (workdir / "pw.config.ts").write_text(PW_CONFIG, encoding="utf-8")
        for name, path in specs.items():
            (workdir / f"{name}.spec.ts").write_text(_render(path), encoding="utf-8")
        env = {**os.environ, "API_BASE_URL": "http://%s" % host}
        proc = subprocess.run(
            [NPX, "playwright", "test", "--config=pw.config.ts", "--reporter=line",
             "--workers=1", *[f"{n}.spec.ts" for n in specs]],
            cwd=str(workdir), env=env, capture_output=True,
        )
        stdout = (proc.stdout or b"").decode("utf-8", "replace")
        stderr = (proc.stderr or b"").decode("utf-8", "replace")
        return {"returncode": proc.returncode, "stdout": stdout, "stderr": stderr,
                "raw": stdout + stderr, "received": received,
                "contracts": _contract_lines(stdout)}
    finally:
        server.shutdown()
        _cleanup(workdir)


def _by_stage(rows: list[dict], stage: str) -> list[dict]:
    return [r for r in rows if r.get("stage") == stage]


# --------------------------------------------------------------------------- #
# 场景 1：DEFECT-002 现状 + 参照组 + 无关接口 + unverified
# --------------------------------------------------------------------------- #
@pytest.fixture(scope="module")
def defect_002_run():
    return _run({
        "defect": "/v1/purchaseLimit/list",     # 越界 data:null → known_issue（失败）
        "reference": "/v1/store/binding/list",  # 参照组：越界也完整 → 通过
        "plain": "/v1/some/other/endpoint",     # 无契约 → 仅信封断言
        "unverified": "/v1/recommend/list",     # 参数未确认 → 显式 skip
    })


class TestDefect002Shape:
    def test_out_of_range_null_is_reported_as_known_issue_not_a_pass(self, defect_002_run):
        """★ 阶段 B 的核心验收：与手跑矩阵同一结论。

        ``data:null`` 必须被标为 ``known_issue``（绑定 DEFECT-002）：
        既不是"通过"（那等于漏检），也不是普通失败（那等于缺陷修复后没人知道）。
        """
        rows = _by_stage(defect_002_run["contracts"], "out_of_range")
        defect = [r for r in rows if r.get("known_issue_id") == "DEFECT-002"]
        assert len(defect) == 1, f"未拿到 DEFECT-002 结论：{defect_002_run['raw'][-1500:]}"
        row = defect[0]
        assert row["ok"] is False, "越界 data:null 绝不能算通过"
        assert row["known_issue"] is True
        assert row["provenance"] == "ui_observed"
        assert row["expect_out_of_range"] == "known_issue"
        assert any("data 应为对象" in f for f in row["failures"]), row["failures"]
        assert row["raw"], "原始响应体必须随结论保留（证据）"

    def test_known_issue_still_fails_the_test(self, defect_002_run):
        """例外**不等于通过**：测试必须保持失败，否则缺陷会被静默豁免。"""
        assert defect_002_run["returncode"] != 0
        assert "KNOWN_ISSUE[DEFECT-002]" in defect_002_run["raw"]

    def test_reference_endpoint_is_a_clean_pass(self, defect_002_run):
        """参照组（越界仍返回完整结构）必须通过 —— 反向证明是 SUT 不一致。"""
        rows = _by_stage(defect_002_run["contracts"], "out_of_range")
        ref = [r for r in rows if r.get("ok") is True]
        assert len(ref) == 1, rows
        assert ref[0]["expect_out_of_range"] == "structure"
        assert ref[0]["known_issue"] is False

    def test_endpoint_without_contract_gets_no_extra_probe(self, defect_002_run):
        """无契约的接口不得被套上规则，也不得凭空多打一次越界页请求。"""
        calls = [p for _m, p in defect_002_run["received"]
                 if p.startswith("/v1/some/other/endpoint")]
        assert len(calls) == 1, calls

    def test_unverified_endpoint_is_skipped_and_never_called(self, defect_002_run):
        """参数未确认 → 报告里 skipped，且**服务端零请求**（不猜参数跑一遍）。"""
        assert "skipped" in defect_002_run["raw"]
        assert not [p for _m, p in defect_002_run["received"]
                    if p.startswith("/v1/recommend/list")], \
            "unverified 接口不应发出任何请求"
        assert all(r.get("provenance") != "unverified"
                   for r in _by_stage(defect_002_run["contracts"], "out_of_range"))

    def test_contract_endpoints_probe_both_normal_and_out_of_range_pages(self, defect_002_run):
        """契约接口必须真的探测两个页码（否则结构断言只是摆设）。"""
        calls = [p for _m, p in defect_002_run["received"]
                 if p.startswith("/v1/purchaseLimit/list")]
        assert len(calls) == 2, calls
        assert any(f"pageNum={PAGE_THRESHOLD + 1}" in p or "pageNum=999999" in p
                   for p in calls), calls


# --------------------------------------------------------------------------- #
# 场景 2：正常页结构断言（第 2 层）
# --------------------------------------------------------------------------- #
class TestNormalPageContract:
    def test_broken_normal_page_is_caught(self):
        """第 2 层必须真的生效：正常页结构坏掉时要报 ``CONTRACT_FAILURE 正常页``。

        这条覆盖的是"越界页正常、正常页反而坏了"的分支 —— 只测越界页会漏掉它。
        """
        run = _run({"bad": "/v1/purchaseLimit/list"}, mode="bad_normal")
        rows = _by_stage(run["contracts"], "normal_page")
        assert len(rows) == 1, run["raw"][-1500:]
        assert rows[0]["ok"] is False
        assert "CONTRACT_FAILURE 正常页" in run["raw"]
        # 正常页就坏了，不该继续产出越界页结论（避免用坏数据下第二层判断）
        assert not _by_stage(run["contracts"], "out_of_range")


# --------------------------------------------------------------------------- #
# 场景 3：缺陷已修复 → 自动转绿（阶段 C）
# --------------------------------------------------------------------------- #
class TestFixTurnsGreenAutomatically:
    def test_fixed_out_of_range_structure_passes_without_touching_the_exemption(self):
        """缺陷修复后无需人工清理例外标记：known_issue 只在失败时生效。"""
        run = _run({"fixed": "/v1/purchaseLimit/list"}, mode="fixed")
        rows = _by_stage(run["contracts"], "out_of_range")
        assert len(rows) == 1, run["raw"][-1500:]
        assert rows[0]["ok"] is True, rows[0]
        assert rows[0]["known_issue"] is False
        assert run["returncode"] == 0, run["raw"][-1500:]


# --------------------------------------------------------------------------- #
# 场景 4：凭据失效（阶段 A）
# --------------------------------------------------------------------------- #
class TestExpiredCredential:
    def test_expired_token_is_flagged_as_auth_not_as_pass_or_contract_failure(self):
        """★ 阶段 A 验收：过期令牌必须报 ``AUTH_OR_BUSINESS_ENVELOPE``。

        改动前脚本只断言 HTTP 2xx，而信封式 SUT 凭据失效也是 HTTP 200 →
        那是**恒真通过**（报告一片绿，实际什么都没验）。
        """
        run = _run({"expired": "/v1/purchaseLimit/list"}, mode="expired_token")
        assert run["returncode"] != 0, "凭据失效绝不能通过"
        assert "AUTH_OR_BUSINESS_ENVELOPE" in run["raw"]
        # 不得被误报成契约失败（归因错了会把排查引向错误方向）
        assert "CONTRACT_FAILURE" not in run["raw"]
        assert not _by_stage(run["contracts"], "out_of_range"), \
            "凭据失效时不应产出分页契约结论"


class TestOutOfRangeEnvelope:
    def test_out_of_range_auth_failure_is_not_absorbed_by_the_known_issue(self):
        """越界页的凭据失效 ≠ DEFECT-002。

        正常页凭据有效（能过），越界页返回 401 —— 若不**单独**判越界页信封，
        它同样是 ``data:null``，会被 known_issue 例外吸收成"已知问题"：
        等于**用 DEFECT-002 掩盖另一个问题**，比不测更危险。

        这条也是"只判 resultCode 是否存在"不够的反例：401 是存在的，
        必须判 ``resultCode === '0'`` 才拦得住。
        """
        run = _run({"oor_auth": "/v1/purchaseLimit/list"}, mode="oor_auth")
        assert run["returncode"] != 0
        assert "AUTH_OR_BUSINESS_ENVELOPE" in run["raw"]
        assert "KNOWN_ISSUE" not in run["raw"], "凭据问题不得被 known_issue 吸收"
        assert not _by_stage(run["contracts"], "out_of_range"), \
            "越界页凭据失效时不应产出分页契约结论"


# --------------------------------------------------------------------------- #
# 场景 5：known_issue 过期（阶段 C）
# --------------------------------------------------------------------------- #
class TestKnownIssueExpiry:
    def test_expired_exemption_downgrades_to_plain_failure(self, monkeypatch):
        """例外过期 → 转普通失败。否则缺陷修完也不会有人知道（永久豁免）。"""
        expired = tuple(
            PaginationContract(
                endpoint_path=c.endpoint_path, provenance=c.provenance,
                expect_out_of_range=c.expect_out_of_range,
                known_issue_id=c.known_issue_id, known_issue_until="2026-09-01",
                note=c.note,
            ) if c.expect_out_of_range == "known_issue" else c
            for c in contracts.PAGINATION_CONTRACTS
        )
        monkeypatch.setattr(contracts, "PAGINATION_CONTRACTS", expired)

        run = _run({"expired_exemption": "/v1/purchaseLimit/list"}, mode="defect_002")
        rows = _by_stage(run["contracts"], "out_of_range")
        assert len(rows) == 1, run["raw"][-1500:]
        assert rows[0]["ok"] is False
        assert rows[0]["known_issue"] is False, "过期例外必须失效"
        assert "分页契约失败" in rows[0]["summary"], rows[0]["summary"]
        assert "KNOWN_ISSUE" not in run["raw"]
