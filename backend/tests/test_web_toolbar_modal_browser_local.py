# -*- coding: utf-8 -*-
"""O10/rev98 验收：**`flow: toolbar_modal`（工具条按钮 → 弹窗字段 → 提交）的浏览器级受控测试**
（不接触 UAT、不产生任何外部副作用）。

需求来源（``docs`` 同口径，路线图 §8.12.2）：CS 的 email 入口
（WSF-48576977 / cs-case-email，67197 的形态）=

    勾选列表行 → 工具条「Email a Link」→ 弹窗 ``#emails`` / ``#remark`` → Send
    → ``POST /cs/cases/send-email``

既有 flow（recharge_modal / create_page / timeline_subpage）都表达不了这个入口：
必须先**选中列表行**（工具条动作才有目标），再点工具条按钮进弹窗。

受控页面复刻该交互形态（关键语义：未勾选任何行时点「Email a Link」**不会**弹窗——
因此「弹窗真的打开并提交成功」即证明了「行被勾选 → 工具条按钮被点」整条链）：

=======================  ==============================================
用例                     期望
=======================  ==============================================
TOOLBAR-HAPPY            行勾选 → 按钮 → 弹窗字段填充通过 → Send 真实打到服务端
TOOLBAR-NO-BUTTON        工具条按钮名配错 → **可读**硬失败（flow=toolbar_modal），不崩溃
TOOLBAR-NO-ROWS          列表无数据行 → 如实诊断（未等到数据行 + 容器等待超时含 flow）
=======================  ==============================================

需要本机有 node + @playwright/test（项目 ``backend/workspace/web_cli`` 已具备）。
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

BACKEND_DIR = Path(__file__).resolve().parents[1]
WEB_CLI = BACKEND_DIR / "workspace" / "web_cli"
NODE = shutil.which("node")
NPX = shutil.which("npx")
pytestmark = pytest.mark.skipif(
    not (NODE and NPX and (WEB_CLI / "node_modules" / "@playwright" / "test").exists()),
    reason="需要本机 node + @playwright/test（backend/workspace/web_cli）",
)

ENDPOINT = "/cs/cases/send-email"
REQUEST_ID = "0A000607-MT2B4XUV-27373"     # 与 67197 工单里的形态一致


# --------------------------------------------------------------------------- #
# 受控页面：列表（行内 checkbox）→ 工具条「Email a Link」→ 弹窗 #emails/#remark → Send
# --------------------------------------------------------------------------- #
PAGE = """<!doctype html>
<html lang="zh"><head><meta charset="utf-8"><title>local case email</title></head>
<body>
  <div class="ant-table"><div class="ant-table-container"><table><tbody class="ant-table-tbody">
__ROWS__  </tbody></table></div></div>

  <div class="ant-toolbar" style="margin:12px 0">
    <button id="email-link" type="button" onclick="openEmail()"><span>Email a Link</span></button>
  </div>

  <div id="email-modal" class="ant-modal" style="display:none">
    <div class="ant-modal-content"><div class="ant-modal-body">
      <div class="ant-form-item"><label>Emails</label><textarea id="emails" rows="2"></textarea></div>
      <div class="ant-form-item"><label>Remark</label><input id="remark"></div>
    </div>
    <div class="ant-modal-footer">
      <button type="button">Cancel</button>
      <button id="send" type="button" onclick="sendEmail()">Send</button>
    </div></div>
  </div>

<script>
// 复刻 67197 的真实语义：未勾选任何行时，工具条动作没有目标 → 不弹窗
function openEmail() {
  var anyChecked = Array.from(document.querySelectorAll(
    '.ant-table-tbody input[type=checkbox]')).some(function (c) { return c.checked; });
  if (!anyChecked) { window.__openBlocked = (window.__openBlocked || 0) + 1; return; }
  document.getElementById('email-modal').style.display = 'block';
}
function sendEmail() {
  fetch('__ENDPOINT__', {method: 'POST', headers: {'content-type': 'application/json'},
    body: JSON.stringify({
      emails: document.getElementById('emails').value,
      remark: document.getElementById('remark').value,
    })}).then(function (r) { return r.json(); }).then(function (res) {
    if (String(res.resultCode) === '0') {
      document.getElementById('email-modal').style.display = 'none';
    } else {
      // 复刻真实 antd 交互：服务端拒绝 → 错误 toast + 弹窗保持打开
      var t = document.createElement('div');
      t.className = 'ant-message-notice-error';
      t.textContent = res.resultMsg || 'error';
      document.body.appendChild(t);
    }
  });
}
</script></body></html>
"""

ROWS = """    <tr class="ant-table-row">
      <td><span class="ant-checkbox"><input type="checkbox" class="ant-checkbox-input"></span></td>
      <td><a>case-1001 客诉：食品异物</a></td>
    </tr>
    <tr class="ant-table-row">
      <td><span class="ant-checkbox"><input type="checkbox" class="ant-checkbox-input"></span></td>
      <td><a>case-1002 客诉：配送延误</a></td>
    </tr>
"""

#: 完整页（两行数据）；空表页用于「无数据行」诊断出口。
FULL_PAGE = PAGE.replace("__ROWS__", ROWS)
EMPTY_PAGE = PAGE.replace("__ROWS__", "<!-- 无数据行 -->\n")


class _Gateway(BaseHTTPRequestHandler):
    """记录收到的请求 + 按路径返回信封响应（形态同真实网关）。"""

    received: list[tuple[str, str]]
    page_html: str

    def _handle(self, method: str) -> None:
        length = int(self.headers.get("content-length") or 0)
        body_in = self.rfile.read(length) if length else b""
        self.received.append((method, self.path, body_in.decode("utf-8", "replace")[:2000]))
        if self.path.startswith("/form"):
            body = self.page_html.encode("utf-8")
            ctype = "text/html; charset=utf-8"
        else:
            raw = body_in.decode("utf-8", "replace")
            try:
                payload_in = json.loads(raw)
            except (ValueError, TypeError):
                payload_in = {}
            # 复刻真实服务端校验：501 字探针必须被拒（否则平台会按「受理非法请求」判疑似缺陷——
            # 那是真缺陷的出口，不是夹具想要的行为）
            if len(str(payload_in.get("remark") or "")) > 500:
                payload = {"resultCode": "5001", "resultMsg": "内容无效：超过500字上限",
                           "echo": raw[:120]}
            else:
                payload = {"resultCode": "0", "resultMsg": "success", "echo": raw[:120]}
            body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            ctype = "application/json; charset=utf-8"
        self.send_response(200)
        self.send_header("content-type", ctype)
        self.send_header("content-length", str(len(body)))
        self.send_header("requestid", REQUEST_ID)
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):  # noqa: N802
        self._handle("GET")

    def do_POST(self):  # noqa: N802
        self._handle("POST")

    def log_message(self, *args):  # 静默
        return


def _serve(page_html: str) -> tuple[ThreadingHTTPServer, str, list[tuple[str, str, str]]]:
    received: list[tuple[str, str, str]] = []
    page_html = page_html.replace("__ENDPOINT__", ENDPOINT)
    handler = type("H", (_Gateway,), {"received": received, "page_html": page_html})
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server, "127.0.0.1:%d" % server.server_address[1], received


# --------------------------------------------------------------------------- #
# 计划：toolbar_modal 定位器表（与生产配置同形：子功能 custom_config.field_locators）
# --------------------------------------------------------------------------- #
def _plan(toolbar_button: list[str] | None = None, *, row_pick: str = "checkbox") -> dict:
    case = SimpleNamespace(
        identifier="TC-900001", name="工单管理-Email a Link 多收件人", module="local",
        description="",
        custom_fields={
            "requirement_rule_id": "FR-CS-EMAIL",
            "requirement_rule": {"field": "Remark"},
            "assertions": [
                # 500 字上限：换算成真实 500 字符样本（断言 fill 走 selector_hint #remark）
                {"kind": "max_length", "data": "500字", "expect": "accept",
                 "field": "Remark", "rule": "限制500個字"},
                # 501 字超限：probe 项驱动**提交相位**（点 Send 真实打服务端）——
                # 纯 accept 计划的提交相位为空转（实测 submit_phase 全 0），
                # 这一条是「Send 真的被点到」的触发器；夹具服务端恒受理，
                # 该 probe 行的结论是什么不影响本测试的链路断言。
                {"kind": "max_length", "data": "501字", "expect": "reject",
                 "field": "Remark", "rule": "限制500個字"},
            ],
        },
        test_case_steps=[],
    )
    loc = {
        "name": "local_case_email", "flow": "toolbar_modal",
        "toolbar_button": toolbar_button if toolbar_button is not None else ["Email a Link"],
        "row_pick": row_pick,
        "modal": "#email-modal",
        "fields": {
            "Emails": {"kind": "input", "selector_hint": "#emails"},
            "Remark": {"kind": "input", "selector_hint": "#remark"},
        },
        "field_modes": {}, "default_field_mode": {}, "defaults": [], "toggles": [],
        "upload_fields": {},
    }
    plan = build_form_plan(compile_cases([case]), loc)
    assert plan is not None and plan["executable"] is True
    assert len(plan["checks"]) >= 1, "toolbar_modal 计划应含字段级检查"
    return plan


SPEC = """import {{ test, expect }} from '@playwright/test';

// 执行器在上传/独立上下文阶段会引用它（本地用例不跑上传，但函数必须存在）
async function enterStorageAccountList(pg: any, _ctx: any) {{
  await pg.goto(process.env.LOCAL_PAGE_URL!);
  return pg;
}}

test('本地受控页面：toolbar_modal 工具条弹窗流程', async ({{ page, context }}) => {{
  const appScope: any = await enterStorageAccountList(page, context);
{executor}
}});
"""


def _cleanup(path: Path) -> None:
    """删临时目录（清理失败不得让测试红，同负路径本地测试的处置）。"""
    try:
        shutil.rmtree(path, ignore_errors=True)
    except BaseException:  # noqa: BLE001
        pass


def _run(page_html: str, plan: dict, tag: str) -> tuple[int, list[tuple[str, str, str]], str]:
    server, host, received = _serve(page_html)
    workdir = WEB_CLI / (".toolbar_modal_local_" + tag + "_" + uuid.uuid4().hex[:8])
    workdir.mkdir(parents=True, exist_ok=True)
    try:
        executor = render_form_executor(plan)
        (workdir / "local-toolbar.spec.ts").write_text(SPEC.format(executor=executor),
                                                       encoding="utf-8")
        env = {**os.environ, "LOCAL_PAGE_URL": "http://%s/form" % host}
        proc = subprocess.run(
            [NPX, "playwright", "test", "--reporter=line", "--workers=1",
             "local-toolbar.spec.ts"],
            cwd=str(workdir), env=env, capture_output=True, timeout=300)
        out = (proc.stdout or b"").decode("utf-8", "replace") + \
              (proc.stderr or b"").decode("utf-8", "replace")
        return proc.returncode, received, out
    finally:
        server.shutdown()
        _cleanup(workdir)


def _assertions(stdout: str) -> list[dict]:
    out: list[dict] = []
    for line in stdout.splitlines():
        marker = "COMPILED_ASSERTION "
        if marker not in line:
            continue
        try:
            out.append(json.loads(line.split(marker, 1)[1].strip()))
        except json.JSONDecodeError:
            continue
    return out


@pytest.fixture(scope="module")
def happy_run():
    rc, received, raw = _run(FULL_PAGE, _plan(), "happy")
    return {"rc": rc, "received": received, "raw": raw, "rows": _assertions(raw)}


# --------------------------------------------------------------------------- #
# 验收
# --------------------------------------------------------------------------- #
class TestToolbarModalBrowser:
    def test_happy_path_full_chain(self, happy_run):
        """行勾选 → 工具条按钮 → 弹窗字段 → Send：每一环都有硬证据。

        弹窗打开的前提是「确实有行被勾选」（受控页面的 openEmail 语义），
        因此「字段断言 pass + 服务端收到 POST」即证明整条入口链成立。
        """
        assert happy_run["rc"] == 0, happy_run["raw"][-2000:]
        posts = [r for r in happy_run["received"] if r[0] == "POST" and r[1] == ENDPOINT]
        assert posts, "服务端未收到 %s —— Send 未点击或弹窗未打开" % ENDPOINT
        body = json.loads(posts[0][2] or "{}")
        assert body.get("emails") is not None and body.get("remark") is not None

    def test_field_check_passes_with_probe_note(self, happy_run):
        """弹窗内字段填充走 selector_hint 且样本长度真实换算，结论精确归属到用例/规则。"""
        accept_rows = [r for r in happy_run["rows"]
                       if r.get("name", "").startswith("input_validation:Remark [500字]")]
        assert accept_rows, happy_run["rows"]
        assert all(r["outcome"] == "pass" for r in accept_rows), accept_rows
        assert any("500" in r.get("observed", "") for r in accept_rows), accept_rows
        assert all(r["case_id"] == "TC-900001" and r["rule_id"] == "FR-CS-EMAIL"
                   for r in accept_rows), accept_rows

    def test_missing_toolbar_button_is_readable_failure(self):
        """工具条按钮名配错 → 可读硬失败（不崩溃、不静默绿）。"""
        rc, _, raw = _run(FULL_PAGE, _plan(["这个按钮不存在ZZZ"]), "nobtn")
        assert rc != 0, "按钮不存在时运行应当失败"
        assert "无法打开表单（flow=toolbar_modal，定位器可能失效）" in raw, raw[-1500:]

    def test_empty_list_reports_flow_diagnosis(self):
        """列表无数据行 → 如实诊断：未等到数据行 + 容器超时信息带 flow。"""
        rc, received, raw = _run(EMPTY_PAGE, _plan(), "empty")
        assert rc != 0, "无数据行时运行应当失败"
        assert "toolbar_modal" in raw and "无法打开表单容器" in raw, raw[-1500:]
        assert not [r for r in received if r[0] == "POST" and r[1] == ENDPOINT], \
            "行都没选到就发提交 = 假执行"
