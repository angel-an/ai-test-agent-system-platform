# -*- coding: utf-8 -*-
"""P0：**本地浏览器级受控页面测试**（不接触 UAT、不产生任何外部副作用）。

复核意见要求：判定与策略虽已本地可测，但"`route.abort()` 是否真的拦住请求""被拦后附件未增加
是否还会判通过"等**浏览器级组合**必须在本地独立复现。这里做法是：

1. 起两个本地 HTTP 服务：`app`（承载受控页面 + 业务接口 `/api/query`）与 `upload`
   （模拟 OSS：另一个 host:port，页面选中合法文件后会向它 POST）；
2. 用**发布出去的**执行器片段（`render_form_executor`）生成一个真实 Playwright 用例，
   页面标记与 antd 一致（`.ant-form-item`、`.ant-radio-wrapper`、`.ant-upload-list-item`、
   `.ant-form-item-explain-error`），执行器代码不做任何改写；
3. 断言覆盖复核点名的 6 个场景，并用"服务端零请求"作为"确实没发出去"的硬证据。

需要本机有 node + @playwright/test（项目 `backend/workspace/web_cli` 已具备）。
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

import pytest

from app.services.web_case_compiler import build_form_plan, compile_cases, render_form_executor
from app.services.web_upload_evidence import (
    BLOCKED,
    PASS,
    UNVERIFIED,
    classify_upload,
    contract_deviation,
    fresh_messages,
    is_format_message,
    is_size_message,
    parse_upload_evidence,
)

BACKEND_DIR = Path(__file__).resolve().parents[1]
WEB_CLI = BACKEND_DIR / "workspace" / "web_cli"
NODE = shutil.which("node")
NPX = shutil.which("npx")
pytestmark = pytest.mark.skipif(
    not (NODE and NPX and (WEB_CLI / "node_modules" / "@playwright" / "test").exists()),
    reason="需要本机 node + @playwright/test（backend/workspace/web_cli）",
)

# --------------------------------------------------------------------------- #
# 受控页面
# --------------------------------------------------------------------------- #
PAGE = """<!doctype html>
<html lang="zh"><head><meta charset="utf-8"><title>local recharge form</title>
<style>.ant-modal{display:block}</style></head>
<body>
  <input placeholder="请输入手机号码" id="phone">
  <button id="search">搜索</button>
  <button id="open">企业充值/退款</button>
  <div id="modal" class="ant-modal">
    <div class="ant-form-item" id="row-type">
      变动类型
      <label class="ant-radio-wrapper"><input type="radio" name="t" value="recharge" checked><span>企业储值充值</span></label>
      <label class="ant-radio-wrapper"><input type="radio" name="t" value="refund"><span>企业储值退款（仅扣减储值不退款）</span></label>
    </div>
    <div class="ant-form-item" id="row-mode">
      变更方式
      <label class="ant-radio-wrapper"><input type="radio" name="m" value="manual" checked><span>手动输入</span></label>
      <label class="ant-radio-wrapper"><input type="radio" name="m" value="batch"><span>批量导入</span></label>
    </div>
    <div class="ant-form-item" id="row-upload">
      上传文件
      <span class="ant-upload"><input id="file" type="file" accept=".xlsx,.xls"></span>
      <div id="up-list"></div>
      <div id="up-err"></div>
    </div>
  </div>
<script>
window.API_BASE = new URLSearchParams(location.search).get('api') || '';
window.FIXED_RULES = new URLSearchParams(location.search).get('fixed') === '1';
if (window.FIXED_RULES) {
  document.getElementById('file').setAttribute('accept', '.xlsx,.xls,.csv');
}
async function callApi() {
  try { await fetch(window.API_BASE + '/api/query', {method: 'POST',
        headers: {'content-type': 'application/json'}, body: '{"phone":"133"}'}); }
  catch (e) { /* 被隔离策略阻断时这里会失败，属预期 */ }
}
document.getElementById('search').addEventListener('click', callApi);
document.getElementById('file').addEventListener('change', async function () {
  const f = this.files[0]; if (!f) return;
  const name = f.name.toLowerCase();
  const errBox = document.getElementById('up-err');
  const list = document.getElementById('up-list');
  const addErr = (t) => { const d = document.createElement('div');
                          d.className = 'ant-form-item-explain-error'; d.textContent = t;
                          errBox.appendChild(d); };
  const addChip = (file) => {
    const chip = document.createElement('div');
    chip.className = 'ant-upload-list-item';
    const label = document.createElement('span'); label.textContent = file.name;
    const del = document.createElement('span'); del.className = 'anticon-close'; del.textContent = '×';
    del.addEventListener('click', () => chip.remove());
    chip.appendChild(label); chip.appendChild(del);
    list.appendChild(chip);
  };
  const pushUpload = async (file) => {
    try {                                    // 模拟 antd 自动上传（应被隔离策略阻断）
      const body = new FormData(); body.append('file', file);
      await fetch(window.UPLOAD_URL, {method: 'POST', body});
    } catch (e) { /* 预期：请求被 abort */ }
  };

  // ===== 模式 A：修复**前**的规则（复现两项契约偏差）=====
  if (!window.FIXED_RULES) {
    if (name.includes('silent')) { return; }
    await callApi();
    if (name.includes('bad')) { addErr('文件格式仅支持：xlsx、xls'); return; }
    if (name.includes('oversize')) { addErr('文件大小不能超过1M'); return; }
    if (name.includes('dup')) {
      addErr('文件格式仅支持：xlsx、xls');
      addErr('文件格式仅支持：xlsx、xls');
      return;
    }
    const ext = name.split('.').pop();
    if (!['xlsx', 'xls'].includes(ext)) { addErr('文件格式仅支持：xlsx、xls'); return; }
    if (f.size >= 1048576) { addErr('文件大小不能超过1M'); return; }   // 线上：正好 1M 被误拒
    addChip(f); await pushUpload(f);
    return;
  }

  // ===== 模式 B：修复**后**的规则（契约：xlsx/xls/csv；≤1,048,576 允许）=====
  if (name.includes('silent')) { return; }
  await callApi();
  const ALLOWED = ['xlsx', 'xls', 'csv'];
  const ext2 = name.split('.').pop();
  if (!ALLOWED.includes(ext2)) { addErr('文件格式仅支持：' + ALLOWED.join('、')); return; }
  if (f.size > 1048576) { addErr('文件大小不能超过1M'); return; }     // > 才是"超过"；等于放行
  addChip(f); await pushUpload(f);
});
</script></body></html>
"""


class _Recorder(BaseHTTPRequestHandler):
    """记录收到的请求（用于"确实没发出去"的硬证据）。"""

    received: list[tuple[str, str]]

    def _handle(self, method: str) -> None:
        length = int(self.headers.get("content-length") or 0)
        if length:
            self.rfile.read(length)
        self.received.append((method, self.path))
        ctype = self.headers.get("content-type") or ""
        if self.path.startswith("/form"):
            body = PAGE.encode("utf-8")
            self.send_response(200)
            self.send_header("content-type", "text/html; charset=utf-8")
        else:
            body = b'{"ok":true}'
            self.send_response(200)
            self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(body)))
        if ctype:
            self.send_header("x-req-content-type", ctype[:60])
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):  # noqa: N802
        self._handle("GET")

    def do_POST(self):  # noqa: N802
        self._handle("POST")

    def log_message(self, *args):  # 静默
        return


def _serve() -> tuple[ThreadingHTTPServer, str, list[tuple[str, str]]]:
    received: list[tuple[str, str]] = []
    handler = type("H", (_Recorder,), {"received": received})
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    host = "127.0.0.1:%d" % server.server_address[1]
    return server, host, received


# --------------------------------------------------------------------------- #
# 计划：复用线上定位器语义，只把页面换成受控页面
# --------------------------------------------------------------------------- #
def _local_plan() -> dict:
    case = SimpleNamespace(
        identifier="TC-U1", name="上传文件-格式与大小校验", module="local",
        description="", custom_fields={
            "requirement_rule_id": "FR-U1",
            "requirement_rule": {"field": "上传文件（企业充值）", "required": True},
            "assertions": [
                {"kind": "file_format", "data": ".xlsx", "expect": "accept",
                 "rule": "文件格式仅支持：xlsx、xls"},
                {"kind": "file_format", "data": ".exe", "expect": "reject",
                 "rule": "文件格式仅支持：xlsx、xls"},
                {"kind": "file_size", "data": "<=1M", "expect": "accept", "rule": "文件大小1M"},
                {"kind": "file_size", "data": ">1M", "expect": "reject", "rule": "文件大小1M"},
            ],
        },
        test_case_steps=[],
    )
    manifest = compile_cases([case])
    loc = {
        "name": "local",
        "flow": "local_modal",
        "form_button": ["企业充值/退款"],
        "filter_phone_placeholder": "请输入手机号码",
        "search_button": "搜索",
        "modal": ".ant-modal",
        "fields": {"变动类型": {"kind": "radio"}, "变更方式": {"kind": "radio"}},
        "field_modes": {},
        "default_field_mode": {"变动类型": "企业储值充值", "变更方式": "手动输入"},
        "defaults": [],
        "toggles": [],
        "upload_fields": {
            "上传文件（企业充值）": {
                "row_label": "上传文件",
                "mode": {"变动类型": "企业储值充值", "变更方式": "批量导入"},
            },
        },
    }
    plan = build_form_plan(manifest, loc, allow_upload=True)
    assert plan is not None
    for check in plan["checks"]:
        if check["type"] == "upload_file":
            check["row_label"] = "上传文件"
            check["mode"] = {"变动类型": "企业储值充值", "变更方式": "批量导入"}
    # 追加两条受控场景检查（都使用**真实可构造**的 .exe 样本）：
    #   ① upload_silent.exe：页面什么都不做 → 弱证据 + 等待超时，必须 unverified；
    #   ② upload_dup.exe：页面产生与上一条**相同文本**且次数增加 → 仍必须能归属。
    reject = next(c for c in plan["checks"]
                  if c["type"] == "upload_file" and c["expect"] == "reject"
                  and c["evidence_kind"] == "format")
    for name in ("upload_silent.exe", "upload_dup.exe"):
        extra = dict(reject)
        extra["sample"] = {"name": name, "mime": "application/octet-stream",
                           "source": "bad_extension", "bytes": 256,
                           "kind": "disallowed_extension", "threshold_confirmed": False}
        plan["checks"].append(extra)
    return plan


SPEC = """import {{ test, expect }} from '@playwright/test';

// 与线上模板同形的两个函数（本地受控页面下等价实现）：
//   * enterStorageAccountList：进入目标业务页面并返回作用域；
//   * 独立上下文阶段会重新调用它，因此本地也能覆盖"独立上下文"路径。
async function enterStorageAccountList(pg: any, _ctx: any) {{
  await pg.goto(process.env.LOCAL_PAGE_URL!);
  await pg.addScriptTag({{content: `window.UPLOAD_URL = "${{process.env.LOCAL_UPLOAD_URL}}";`}});
  return pg;
}}

test('本地受控页面：上传校验与隔离策略', async ({{ page, context }}) => {{
  const appScope: any = await enterStorageAccountList(page, context);
{executor}
}});
"""


def _run_local_browser(api_base: str = "", *, fixed_rules: bool = False,
                       plan: dict | None = None) -> tuple[dict, list[tuple[str, str]], list[tuple[str, str]], str]:
    """跑一轮本地浏览器测试，返回 (stdout 解析结果, app 收到的请求, upload 收到的请求, 原始输出)。"""
    app_server, app_host, app_received = _serve()
    up_server, up_host, up_received = _serve()
    workdir = WEB_CLI / (".upload_local_e2e_" + uuid.uuid4().hex[:8])
    workdir.mkdir(parents=True, exist_ok=True)
    try:
        plan = plan or _local_plan()
        # 收紧后的安全边界：写请求默认阻断 → 这里**显式**放行页面查询接口（host+路径+方法），
        # 用于验证"显式规则生效，且不会被误判成上传"。
        executor = render_form_executor(
            plan,
            upload_allow_rules=[{"host": app_host, "path_prefix": "/api/query",
                                 "methods": ["POST"]}],
        )
        spec = SPEC.format(executor=executor)
        (workdir / "local-upload.spec.ts").write_text(spec, encoding="utf-8")
        env = {**os.environ,
            "LOCAL_PAGE_URL": "http://%s/form?api=%s%s" % (
                app_host, api_base, "&fixed=1" if fixed_rules else ""),
            "LOCAL_UPLOAD_URL": "http://%s/put-object" % up_host,
            "PR2_STORAGE_ACCOUNT_NO": "13300000000",
        }
        proc = subprocess.run(
            [NPX, "playwright", "test", "--reporter=line", "--workers=1",
             "local-upload.spec.ts"],
            cwd=str(workdir), env=env, capture_output=True,
        )
        out = (proc.stdout or b"").decode("utf-8", "replace") + \
              (proc.stderr or b"").decode("utf-8", "replace")
        return {"returncode": proc.returncode, "stdout": out}, app_received, up_received, out
    finally:
        app_server.shutdown()
        up_server.shutdown()
        shutil.rmtree(workdir, ignore_errors=True)


def _evidence(stdout: str) -> list[dict]:
    return parse_upload_evidence(stdout)


@pytest.fixture(scope="module")
def local_run():
    result, app_received, up_received, raw = _run_local_browser()
    assert result["returncode"] == 0, "本地 Playwright 用例失败：\n" + raw[-3000:]
    return {"evidence": _evidence(raw), "app": app_received, "upload": up_received,
            "raw": raw}


class TestLocalBrowserUpload:
    """复核点名的 6 个场景在本地浏览器里逐条复现。"""


    def test_upload_phase_runs_in_isolated_context(self, local_run):
        """复核意见：上传校验要与业务执行上下文隔离 —— 本地必须看到 isolated 标签。"""
        lines = [l for l in local_run["raw"].splitlines() if "UPLOAD_CONTEXT" in l]
        assert lines, "没有输出 UPLOAD_CONTEXT（无法证明上下文隔离）"
        payload = json.loads(lines[-1].split("UPLOAD_CONTEXT ", 1)[1])
        assert payload["label"] == "isolated", payload
        assert all((ev.get("context_label") == "isolated") for ev in local_run["evidence"])

    def test_upload_request_is_really_blocked(self, local_run):
        """① route.abort() 确实拦住了：模拟 OSS 的服务端**一个请求都没收到**。"""
        assert local_run["upload"] == [], "上传请求竟然到达了服务端：%s" % local_run["upload"]
        blocked = [l for l in local_run["raw"].splitlines() if "ISOLATION_BLOCKED" in l]
        assert blocked, "没有出现 ISOLATION_BLOCKED 日志"
        kinds = {json.loads(l.split("ISOLATION_BLOCKED ", 1)[1])["kind"] for l in blocked}
        assert "upload_blocked" in kinds

    def test_non_upload_business_post_is_not_misjudged_as_upload(self, local_run):
        """④ 业务接口（同源 POST，在放行清单内）**不被误拦**。"""
        api_calls = [r for r in local_run["app"] if r[0] == "POST" and r[1].startswith("/api/query")]
        assert api_calls, "页面业务接口请求没有到达服务端（可能被误拦）"
        kinds = [json.loads(l.split("ISOLATION_BLOCKED ", 1)[1])["kind"]
                 for l in local_run["raw"].splitlines() if "ISOLATION_BLOCKED" in l]
        assert "non_upload_api_blocked" not in kinds

    def test_reject_passes_only_with_matching_message(self, local_run):
        """格式/大小拒绝：只有对应类型的客户端提示才算通过。"""
        verdicts = {}
        for ev in local_run["evidence"]:
            name = (ev.get("sample") or {}).get("name", "")
            outcome, reason = classify_upload(ev)
            verdicts[name] = (outcome, reason)
        assert verdicts["upload_bad.exe"][0] == PASS
        assert "格式" in verdicts["upload_bad.exe"][1]
        assert verdicts["upload_oversize.xlsx"][0] == PASS
        assert "大小" in verdicts["upload_oversize.xlsx"][1]

    def test_chip_not_added_alone_is_not_a_pass(self, local_run):
        """② 附件未增加 + 无提示（且等待超时）→ 必须 unverified，且超时原因按阶段给出。"""
        silent = [ev for ev in local_run["evidence"]
                  if (ev.get("sample") or {}).get("name") == "upload_silent.exe"]
        assert silent, local_run["raw"][-1500:]
        ev = silent[0]
        assert ev["chips_after"] <= ev["chips_before"]
        assert ev["stages"]["saw_file_selected"] is True, "文件选择事件必须被观测到"
        assert ev["timed_out_stage"] == "client_feedback"
        outcome, reason = classify_upload(ev)
        assert outcome == UNVERIFIED, reason
        assert "阶段②客户端反馈超时" in reason
        assert "客户端未接受该文件" not in reason

    def test_every_evidence_has_stage_timings(self, local_run):
        """分阶段记录必须落到每条证据上（含时间戳），否则无法区分超时原因。"""
        assert local_run["evidence"], local_run["raw"][-1500:]
        for ev in local_run["evidence"]:
            stages = ev.get("stages") or {}
            assert stages.get("saw_file_selected") is True, ev
            assert stages.get("change_ms") is not None
            assert "file_selected" not in str(ev.get("timed_out_stage"))

    def test_duplicate_message_is_attributed_by_count(self, local_run):
        """⑤ 同一文本重复出现：按**出现次数**差分后仍能归属到本次。"""
        dup = [ev for ev in local_run["evidence"]
               if (ev.get("sample") or {}).get("name") == "upload_dup.exe"]
        assert dup, local_run["raw"][-1500:]
        ev = dup[0]
        same = "文件格式仅支持：xlsx、xls"
        assert ev["messages_before"].count(same) < ev["messages_after"].count(same), \
            "受控页面没有制造出'同文本且次数增加'的场景"
        assert classify_upload(ev)[0] == PASS

    def test_accept_stays_unverified_when_upload_is_isolated(self, local_run):
        """③ 接受类断言：上传被隔离 → 必须 unverified，且原因指明隔离。"""
        accepted = [ev for ev in local_run["evidence"]
                    if str(ev.get("expect")) == "accept"]
        assert accepted, local_run["raw"][-1500:]
        for ev in accepted:
            outcome, reason = classify_upload(ev)
            assert outcome == UNVERIFIED, reason
            assert "隔离" in reason or "未验证" in reason


class TestLocalBrowserEnvironmentFailure:
    """业务接口被阻断时必须标为环境/隔离失败，不能继续统计业务结论。"""

    def test_blocked_business_api_marks_environment_failure(self):
        # 收紧后的边界：**同源写请求也默认阻断** → 这里页面把查询接口指向另一端口且不给规则，
        # 该 POST 必然被阻断，用于验证"业务接口被阻断＝环境/隔离失败"。
        app_server, app_host, _app_received = _serve()
        other_server, other_host, _ = _serve()
        try:
            workdir = WEB_CLI / (".upload_local_e2e_" + uuid.uuid4().hex[:8])
            workdir.mkdir(parents=True, exist_ok=True)
            try:
                plan = _local_plan()
                executor = render_form_executor(plan)   # 不给任何放行规则
                (workdir / "local-upload.spec.ts").write_text(
                    SPEC.format(executor=executor), encoding="utf-8")
                env = {**os.environ,
                    "LOCAL_PAGE_URL": "http://%s/form?api=http://%s" % (app_host, other_host),
                    "LOCAL_UPLOAD_URL": "http://%s/put-object" % other_host,
                    "PR2_STORAGE_ACCOUNT_NO": "13300000000",
                }
                proc = subprocess.run(
                    [NPX, "playwright", "test", "--reporter=line", "--workers=1",
                     "local-upload.spec.ts"],
                    cwd=str(workdir), env=env, capture_output=True)
                raw = (proc.stdout or b"").decode("utf-8", "replace") + \
                      (proc.stderr or b"").decode("utf-8", "replace")
            finally:
                shutil.rmtree(workdir, ignore_errors=True)
        finally:
            app_server.shutdown()
            other_server.shutdown()

        evidence = _evidence(raw)
        assert evidence, raw[-2000:]
        api_blocked = [ev for ev in evidence
                       if (ev.get("isolation_blocked_kinds") or {}).get("non_upload_api_blocked")]
        assert api_blocked, "没有制造出业务接口被阻断的场景：%s" % raw[-1500:]
        for ev in api_blocked:
            outcome, reason = classify_upload(ev)
            assert outcome == BLOCKED, reason
            assert "环境/隔离失败" in reason


def _fixed_rules_plan() -> dict:
    """按**已确认契约**展开的计划：xlsx/xls/csv + N−1/N/N+1（用于验证前端补丁效果）。"""
    case = SimpleNamespace(
        identifier="TC-U1", name="上传文件-格式与大小校验", module="local",
        description="", custom_fields={
            "requirement_rule_id": "FR-U1",
            "requirement_rule": {"field": "上传文件（企业充值）", "required": True},
            "assertions": [
                {"kind": "file_format", "data": ".xlsx", "expect": "accept",
                 "rule": "文件格式仅支持：xlsx、xls、csv"},
                {"kind": "file_format", "data": ".exe", "expect": "reject",
                 "rule": "文件格式仅支持：xlsx、xls、csv"},
                {"kind": "file_size", "data": "<=1M", "expect": "accept", "rule": "文件大小1M"},
                {"kind": "file_size", "data": ">1M", "expect": "reject", "rule": "文件大小1M"},
            ],
        },
        test_case_steps=[],
    )
    loc = {
        "name": "local", "flow": "local_modal",
        "form_button": ["企业充值/退款"], "filter_phone_placeholder": "请输入手机号码",
        "search_button": "搜索", "modal": ".ant-modal",
        "fields": {"变动类型": {"kind": "radio"}, "变更方式": {"kind": "radio"}},
        "field_modes": {},
        "default_field_mode": {"变动类型": "企业储值充值", "变更方式": "手动输入"},
        "defaults": [], "toggles": [],
        "upload_fields": {"上传文件（企业充值）": {
            "row_label": "上传文件",
            "mode": {"变动类型": "企业储值充值", "变更方式": "批量导入"}}},
        "upload_size_limit_bytes": 1_048_576,
        "upload_allowed_formats": ["xlsx", "xls", "csv"],
        "upload_unsupported_sample_formats": {},
    }
    plan = build_form_plan(compile_cases([case]), loc, allow_upload=True)
    assert plan is not None
    for check in plan["checks"]:
        if check["type"] == "upload_file":
            check["row_label"] = "上传文件"
            check["mode"] = {"变动类型": "企业储值充值", "变更方式": "批量导入"}
    return plan


@pytest.fixture(scope="module")
def fixed_run():
    result, _app_received, up_received, raw = _run_local_browser(
        fixed_rules=True, plan=_fixed_rules_plan())
    assert result["returncode"] == 0, "本地 Playwright 用例失败：\n" + raw[-3000:]
    return {"evidence": _evidence(raw), "upload": up_received, "raw": raw}


class TestFixedRulesClearDeviations:
    """在本地受控页面证明：按契约修复后的规则能清掉两项契约偏差，且不削弱拒绝能力。

    （UAT 侧由 `verify_frontend_fix_gate.py` 复验；这里先用同一套判定在本地跑通。）
    """

    def _by_sample(self, run) -> dict:
        return {(ev.get("sample") or {}).get("name"): ev for ev in run["evidence"]}

    def test_no_contract_deviation_remains(self, fixed_run):
        devs = [contract_deviation(ev) for ev in fixed_run["evidence"]]
        codes = sorted({d["code"] for d in devs if d})
        assert codes == [], "修复后仍有契约偏差：%s" % codes

    def test_one_mib_is_accepted_client_side(self, fixed_run):
        ev = self._by_sample(fixed_run)["upload_1048576.xlsx"]
        msgs = [m for m in fresh_messages(ev) if is_size_message(m)]
        assert msgs == [], "1,048,576 字节不应被判超限：%s" % msgs

    def test_csv_is_accepted_client_side(self, fixed_run):
        ev = self._by_sample(fixed_run)["upload_valid.csv"]
        msgs = [m for m in fresh_messages(ev) if is_format_message(m)]
        assert msgs == [], "合法 CSV 不应被格式拒绝：%s" % msgs

    def test_reject_capability_is_kept(self, fixed_run):
        by = self._by_sample(fixed_run)
        assert classify_upload(by["upload_1048577.xlsx"])[0] == PASS      # N+1 仍被拒
        assert classify_upload(by["upload_bad.exe"])[0] == PASS           # .exe 仍被拒

    def test_other_allowed_formats_still_pass_client_side(self, fixed_run):
        by = self._by_sample(fixed_run)
        for name in ("upload_valid.xlsx", "upload_valid.xls"):
            msgs = [m for m in fresh_messages(by[name]) if is_format_message(m)]
            assert msgs == [], "%s 被误拒：%s" % (name, msgs)

    def test_upload_still_not_sent_to_server(self, fixed_run):
        assert fixed_run["upload"] == [], fixed_run["upload"]
