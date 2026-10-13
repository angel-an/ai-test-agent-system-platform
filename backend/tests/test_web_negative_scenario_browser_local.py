# -*- coding: utf-8 -*-
"""P1-3 阶段 B 验收：**规则级负路径场景的浏览器级受控测试**
（不接触 UAT、不产生任何外部副作用）。

验收标准（来自 ``docs/plans/p1c_negative_path_templating_design.md`` §三 阶段 B）：

> 用手写的 DEFECT-001 场景配置，跑出与 ``demo2_script.ts`` 等价的结论。

`demo2_script.ts` 的判定口径（原文）::

    if (!submitResp) { console.log('未捕获到 modify 响应（客户端拦截）'); return; }
    if (code !== '0') { expect(msg).toContain('已新建'); PASS }
    else { expect.soft(false, `[待确认] modify 接受了冲突商品 11281: ...`) }

= 捕获不到 → 不下结论；服务端拒绝且文案命中 → 通过；服务端受理 → **坐实缺陷**。

本用例用一个受控页面复刻"编辑 → 追加被占用的商品 → 提交"的交互形态，
并把 **平台渲染出来的执行器产物原样** 放进去跑，覆盖 5 条出口：

===========  ==========================================  ====================
场景          服务端/环境行为                              期望结论
===========  ==========================================  ====================
NEG-ACCEPT   ``resultCode=0``（受理了应拒绝的请求）           blocked 疑似缺陷坐实
NEG-MATCH    拒绝且 ``resultMsg`` 命中期望关键词             pass
NEG-OTHER    拒绝但文案是别的原因（如"无权限"）              unverified
NEG-BLOCKED  目标接口不在任何放行规则里（写隔离收窄）         unverified + 服务端零请求
NEG-SETUP    前置条件构造失败（找不到入口）                   unverified + 未提交
===========  ==========================================  ====================

关键安全断言：**未被任何场景声明的接口，服务端一个字节都收不到**——
规则级场景是唯一会真实写库的段，写隔离必须按设计文档 §四"收窄到单接口"。

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

# --------------------------------------------------------------------------- #
# 受控页面：复刻"列表 → 编辑 → 追加冲突商品 → 提交"的交互形态
# --------------------------------------------------------------------------- #
#: 连续提交时依次打到的接口（与场景声明顺序一一对应）。
#: 第 4 个 ``.../delete`` **故意不被任何场景声明** —— 用来验证写隔离真的把
#: 未声明的写请求拦在客户端（服务端零请求是最硬的证据）。
GATEWAY = "/gw/api/mini-mgmt-app"
PAGE = """<!doctype html>
<html lang="zh"><head><meta charset="utf-8"><title>local purchase limit</title></head>
<body>
  <button id="new" type="button">新建</button>
  <button id="edit" type="button">编辑</button>
  <table id="list"><tr><td>限购活动 9</td></tr></table>

  <div id="form" class="ant-modal" style="display:none">
    <div class="ant-form-item">限购商品 <span id="goods">（空）</span></div>
    <button id="pick" type="button">选择限购商品</button>
    <div class="ant-modal-footer">
      <button id="cancel" type="button">取 消</button>
      <button id="submit" type="button">提 交</button>
    </div>
  </div>

  <div id="picker" class="ant-modal" style="display:none">
    <table>
      <tr><td><input type="checkbox" id="chk-all"></td><td>全选</td></tr>
      <tr><td><input type="checkbox" id="chk-11281"></td><td>11281</td></tr>
    </table>
    <div class="ant-modal-footer"><button id="pick-ok" type="button">确 认</button></div>
  </div>
  <div id="msg"></div>

<script>
const GW = '__GATEWAY__';
// 连续第 N 次提交打第 N 个接口（由 Python 侧注入，便于给"从新建表单进入"的场景
// 单独跑一轮时只声明它自己那一个接口）
const ENDPOINTS = __ENDPOINTS__.map((p) => GW + p);
let submits = 0;
const $ = (id) => document.getElementById(id);
const form = $('form'), picker = $('picker');
$('new').addEventListener('click', () => { form.style.display = 'block'; picker.style.display = 'none'; });
$('edit').addEventListener('click', () => { form.style.display = 'block'; picker.style.display = 'none'; });
$('cancel').addEventListener('click', () => { form.style.display = 'none'; picker.style.display = 'none'; });
$('pick').addEventListener('click', () => { picker.style.display = 'block'; });
$('pick-ok').addEventListener('click', () => {
  if ($('chk-11281').checked) $('goods').textContent = '11281';
  picker.style.display = 'none';
});
$('submit').addEventListener('click', async () => {
  const ep = ENDPOINTS[Math.min(submits, ENDPOINTS.length - 1)];
  submits += 1;
  try {
    const r = await fetch(ep, {
      method: 'PUT', headers: {'content-type': 'application/json'},
      body: JSON.stringify({activityId: 9, goods: [11281]}),
    });
    const j = await r.json();
    $('msg').textContent = 'server:' + JSON.stringify(j);
  } catch (e) {
    $('msg').textContent = 'blocked:' + String(e);
  }
});
</script></body></html>
""".replace("__GATEWAY__", GATEWAY)

#: 默认提交序列。第 4 个 ``.../delete`` **故意不被任何场景声明** ——
#: 用来验证写隔离真的把未声明的写请求拦在客户端（服务端零请求是最硬的证据）。
DEFAULT_ENDPOINTS = [
    "/v1/purchaseLimit/modify",
    "/v1/purchaseLimit/add",
    "/v1/purchaseLimit/approve",
    "/v1/purchaseLimit/delete",
]


def _page(endpoints: list[str]) -> str:
    return PAGE.replace("__ENDPOINTS__", json.dumps(endpoints))

#: 按路径给出服务端响应（信封式：HTTP 恒 200，靠 resultCode 区分）
RESPONSES = {
    GATEWAY + "/v1/purchaseLimit/modify": {"resultCode": "0", "resultMsg": "success"},
    # 文案里必须**真的**含期望关键词（"已新建"）；写成"已被其他活动新建"会漏掉
    # 连续子串"已新建"，场景会正确地判 unverified —— 这里刻意写对，用来验证 pass 出口
    GATEWAY + "/v1/purchaseLimit/add": {"resultCode": "1", "resultMsg": "该商品已新建，不可重复添加"},
    GATEWAY + "/v1/purchaseLimit/approve": {"resultCode": "1", "resultMsg": "无权限操作"},
    GATEWAY + "/v1/purchaseLimit/delete": {"resultCode": "0", "resultMsg": "success"},
}
REQUEST_ID = "0A000578-MUM4VRA6-5657"      # 与 DEFECT-001 工单里的形态一致


class _Gateway(BaseHTTPRequestHandler):
    """记录收到的请求（"确实没发出去"的硬证据）+ 按路径返回信封响应。"""

    received: list[tuple[str, str]]
    page_html: str

    def _handle(self, method: str) -> None:
        length = int(self.headers.get("content-length") or 0)
        body_in = self.rfile.read(length) if length else b""
        self.received.append((method, self.path))
        if self.path.startswith("/form"):
            body = self.page_html.encode("utf-8")
            ctype = "text/html; charset=utf-8"
        else:
            payload = RESPONSES.get(self.path.split("?")[0], {"resultCode": "0", "resultMsg": "success"})
            payload = {**payload, "echo": body_in.decode("utf-8", "replace")[:120]}
            body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            ctype = "application/json; charset=utf-8"
        self.send_response(200)
        self.send_header("content-type", ctype)
        self.send_header("content-length", str(len(body)))
        # 平台从响应头取名（大小写不敏感）——缺陷工单必须能直接引用这个值
        self.send_header("requestid", REQUEST_ID)
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):  # noqa: N802
        self._handle("GET")

    def do_PUT(self):  # noqa: N802
        self._handle("PUT")

    def do_POST(self):  # noqa: N802
        self._handle("POST")

    def log_message(self, *args):  # 静默
        return


def _serve(endpoints: list[str] | None = None
           ) -> tuple[ThreadingHTTPServer, str, list[tuple[str, str]]]:
    received: list[tuple[str, str]] = []
    handler = type("H", (_Gateway,), {
        "received": received,
        "page_html": _page(list(endpoints or DEFAULT_ENDPOINTS)),
    })
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server, "127.0.0.1:%d" % server.server_address[1], received


# --------------------------------------------------------------------------- #
# 场景配置：等价于手写 demo2_script.ts 的那一条 + 四条边界出口
# --------------------------------------------------------------------------- #
_SETUP = [
    {"action": "click", "labels": ["编辑"]},
    {"action": "click", "labels": ["选择限购商品"]},
    {"action": "select_row", "row_index": 1},          # 0=全选，1=商品行（与手写脚本一致）
    {"action": "click", "labels": ["确 认", "确认"]},
]


def _scenario(sid: str, *, suffix: str, target_entry: str = "edit", **expect) -> dict:
    """按"目标接口 = GATEWAY + suffix"生成场景（allow 与 api_path_hint 各取所需）。"""
    return {
        "scenario_id": sid,
        "rule_ref": "同一商品仅能属于一条活动",
        "target_entry": target_entry,
        "case_id": "TC-200998",
        "rule_id": "FR-PL-004",
        "setup": _SETUP,
        "submit_label": ["提 交", "提交"],
        "expect_reject": {
            "http_ok": True,
            "result_code_not": "0",
            "message_contains": ["已新建", "已存在", "重复"],
            # 响应匹配：URL 子串；写隔离放行：path 前缀（两者语义不同，真实网关上是两个值）
            "api_path_hint": suffix,
            "allow": {"path_prefix": GATEWAY + suffix, "methods": ["PUT"]},
            **expect,
        },
    }


SCENARIOS = [
    # ① 服务端受理了应被拒绝的请求 → 疑似缺陷坐实（= DEFECT-001 的形态）
    _scenario("NEG-ACCEPT", suffix="/v1/purchaseLimit/modify"),
    # ② 服务端拒绝且文案命中期望规则 → pass
    _scenario("NEG-MATCH", suffix="/v1/purchaseLimit/add"),
    # ③ 服务端拒绝，但驳回文案与期望规则不符（如权限）→ 无法确认为本规则生效
    _scenario("NEG-OTHER", suffix="/v1/purchaseLimit/approve"),
    # ④ 目标接口不在任何放行规则里 → 写隔离拦住（服务端零请求）
    _scenario("NEG-BLOCKED", suffix="/v1/purchaseLimit/not-declared"),
    # ⑤ 前置条件构造失败 → 不提交、不下结论
    {**_scenario("NEG-SETUP", suffix="/v1/purchaseLimit/recheck"),
     "setup": [{"action": "click", "labels": ["这个入口不存在ZZZ"]}]},
]

#: 单独一轮：``target_entry="create"`` 的场景要**直接使用平台已打开的新建表单**。
#: setup 为空 —— 任何"先关掉表单"的行为都会让它找不到提交按钮（→ unverified），
#: 因此这一轮是"不误关已打开表单"这条语义的**决定性**验收。
CREATE_ENTRY_SCENARIOS = [
    {
        **_scenario("NEG-CREATE-ENTRY", suffix="/v1/purchaseLimit/add",
                    target_entry="create"),
        "setup": [],
    },
]


def _local_plan(scenarios: list[dict] | None = None) -> dict:
    case = SimpleNamespace(
        identifier="TC-200998", name="限购商品管理-编辑追加重复商品", module="local",
        description="",
        custom_fields={
            "requirement_rule_id": "FR-PL-004",
            "requirement_rule": {"field": "限购商品"},
            # 条件显示断言缺 when → 计划期登记为 skipped；于是 checks 为空，
            # 恰好覆盖"只有规则级场景也要能执行"这条新分支（DEFECT-001 正是这种形态）
            "assertions": [{"kind": "visibility", "expect": "visible", "field": "限购商品"}],
        },
        test_case_steps=[],
    )
    loc = {
        "name": "local_purchase_limit", "flow": "modal", "form_button": ["新建"],
        "modal": ".ant-modal", "fields": {"限购商品": {"kind": "input"}},
        "field_modes": {}, "default_field_mode": {}, "defaults": [], "toggles": [],
        "upload_fields": {},
        "negative_scenarios": SCENARIOS if scenarios is None else scenarios,
    }
    plan = build_form_plan(compile_cases([case]), loc)
    assert plan is not None
    assert plan["executable"] is True
    assert plan["checks"] == [], "本用例刻意不带字段级检查，以覆盖「只有场景」的路径"
    return plan


SPEC = """import {{ test, expect }} from '@playwright/test';

// 执行器在上传阶段会引用它（本用例不跑上传阶段，但函数必须存在）
async function enterStorageAccountList(pg: any, _ctx: any) {{
  await pg.goto(process.env.LOCAL_PAGE_URL!);
  return pg;
}}

test('本地受控页面：规则级负路径场景', async ({{ page, context }}) => {{
  const appScope: any = await enterStorageAccountList(page, context);
{executor}
}});
"""


def _cleanup(path: Path) -> None:
    """删临时目录。**清理失败不得让测试红**：

    受管环境可能对 ``shutil.rmtree`` 施加批量删除保护并直接 ``SystemExit``
    （本机实测：64 个文件触发阈值 → SystemExit(1) → 整个用例报 ERROR，
    而真正的断言全部通过）。清理是收尾动作，不该决定结论。
    """
    try:
        shutil.rmtree(path, ignore_errors=True)
    except BaseException:  # noqa: BLE001  含受管环境的 SystemExit
        pass


def _run_local_browser(plan: dict | None = None,
                       endpoints: list[str] | None = None,
                       tag: str = "main") -> tuple[dict, list[tuple[str, str]], str]:
    server, host, received = _serve(endpoints)
    workdir = WEB_CLI / (f".negative_scenario_local_{tag}_" + uuid.uuid4().hex[:8])
    workdir.mkdir(parents=True, exist_ok=True)
    try:
        executor = render_form_executor(plan if plan is not None else _local_plan())
        (workdir / "local-negative.spec.ts").write_text(
            SPEC.format(executor=executor), encoding="utf-8")
        env = {**os.environ, "LOCAL_PAGE_URL": "http://%s/form" % host}
        proc = subprocess.run(
            [NPX, "playwright", "test", "--reporter=line", "--workers=1",
             "local-negative.spec.ts"],
            cwd=str(workdir), env=env, capture_output=True,
        )
        out = (proc.stdout or b"").decode("utf-8", "replace") + \
              (proc.stderr or b"").decode("utf-8", "replace")
        return {"returncode": proc.returncode}, received, out
    finally:
        server.shutdown()
        _cleanup(workdir)


def _assertions(stdout: str) -> list[dict]:
    """解析 ``COMPILED_ASSERTION`` 行（执行器的机读结论出口）。"""
    out: list[dict] = []
    for line in stdout.splitlines():
        marker = "COMPILED_ASSERTION "
        if marker not in line:
            continue
        payload = line.split(marker, 1)[1].strip()
        try:
            out.append(json.loads(payload))
        except json.JSONDecodeError:
            continue
    return out


def _by_scenario(rows: list[dict]) -> dict[str, dict]:
    return {r["name"].split("]")[0].lstrip("["): r for r in rows}


@pytest.fixture(scope="module")
def negative_run():
    result, received, raw = _run_local_browser()
    return {"result": result, "received": received, "raw": raw,
            "rows": _assertions(raw)}


@pytest.fixture(scope="module")
def create_entry_run():
    """单独一轮：``target_entry="create"`` + setup 为空。

    平台在字段级阶段会先打开"新建"表单；``create`` 语义就是"用这个表单"，
    因此执行器不得把它关掉。任何"先关掉"的行为 → 找不到提交按钮 → unverified。
    """
    plan = _local_plan(CREATE_ENTRY_SCENARIOS)
    result, received, raw = _run_local_browser(
        plan=plan, endpoints=["/v1/purchaseLimit/add"], tag="create")
    return {"result": result, "received": received, "raw": raw,
            "rows": _assertions(raw)}


# --------------------------------------------------------------------------- #
# 验收
# --------------------------------------------------------------------------- #
class TestNegativeScenarioBrowser:
    def test_all_five_scenarios_reach_a_conclusion(self, negative_run):
        """5 条场景都必须落到**明确结论**（不许静默消失）。"""
        rows = _by_scenario(negative_run["rows"])
        assert set(rows) == {"NEG-ACCEPT", "NEG-MATCH", "NEG-OTHER",
                             "NEG-BLOCKED", "NEG-SETUP"}, rows
        assert all(r["source"] == "negative_scenario" for r in rows.values())
        # 归属：场景把结论记回它声明的用例与需求规则
        assert rows["NEG-ACCEPT"]["case_id"] == "TC-200998"
        assert rows["NEG-ACCEPT"]["rule_id"] == "FR-PL-004"

    def test_server_accepted_conflict_is_pinned_as_a_defect(self, negative_run):
        """① 等价于 demo2_script.ts 的 ``expect.soft(false, '[待确认] …接受了冲突商品')``。"""
        row = _by_scenario(negative_run["rows"])["NEG-ACCEPT"]
        assert row["outcome"] == "blocked", row
        observed = row["observed"]
        assert "疑似缺陷坐实" in observed
        assert "同一商品仅能属于一条活动" in observed           # 规则名进证据
        assert "resultCode" in observed and '"resultCode":"0"' in observed.replace(" ", "")
        # 定位凭据：requestId 必须来自响应头，而不是留空
        assert "定位凭据：requestId=" + REQUEST_ID in observed
        assert observed.count(REQUEST_ID) >= 2                  # 证据串与定位凭据各一次

    def test_rejected_with_matching_message_passes(self, negative_run):
        """② 等价于 demo2 的 ``expect(msg).toContain('已新建')`` → PASS。"""
        row = _by_scenario(negative_run["rows"])["NEG-MATCH"]
        assert row["outcome"] == "pass", row
        assert "服务端按规则拒绝" in row["observed"]
        assert "文案命中「已新建」" in row["observed"]

    def test_rejected_for_a_different_reason_is_not_a_pass(self, negative_run):
        """③ 被拒但原因是"无权限"：既不能算本规则生效，也不算本规则失效。"""
        row = _by_scenario(negative_run["rows"])["NEG-OTHER"]
        assert row["outcome"] == "unverified", row
        assert "无法确认为本规则生效" in row["observed"]
        assert "无权限操作" in row["observed"]

    def test_undeclared_endpoint_never_reaches_the_server(self, negative_run):
        """④ 写隔离收窄：未被任何场景声明的接口，服务端**一个请求都收不到**。"""
        row = _by_scenario(negative_run["rows"])["NEG-BLOCKED"]
        assert row["outcome"] == "unverified", row
        assert "写隔离策略阻断" in row["observed"]
        paths = [p for _m, p in negative_run["received"] if not p.startswith("/form")]
        assert not [p for p in paths if p.endswith("/delete")], \
            "未声明的写请求竟然到达了服务端：%s" % negative_run["received"]

    def test_setup_failure_does_not_submit(self, negative_run):
        """⑤ 前置条件构造失败 → 不提交、不下结论（更不许静默通过）。"""
        row = _by_scenario(negative_run["rows"])["NEG-SETUP"]
        assert row["outcome"] == "unverified", row
        assert "前置条件未构造成功" in row["observed"]

    def test_only_declared_writes_reach_the_server(self, negative_run):
        """整轮只应有 3 个写请求（modify / add / approve），顺序与场景一致。"""
        writes = [p for m, p in negative_run["received"] if m == "PUT"]
        assert writes == [
            GATEWAY + "/v1/purchaseLimit/modify",
            GATEWAY + "/v1/purchaseLimit/add",
            GATEWAY + "/v1/purchaseLimit/approve",
        ], writes

    def test_phase_counters_are_emitted_and_split_from_field_level(self, negative_run):
        lines = [l for l in negative_run["raw"].splitlines() if "NEGATIVE_PHASE" in l]
        assert lines, "没有输出 NEGATIVE_PHASE（无法核实到底跑了几条场景）"
        payload = json.loads(lines[-1].split("NEGATIVE_PHASE ", 1)[1])
        assert payload["configured"] == 5
        assert payload["executed"] == 5
        assert (payload["pass"], payload["blocked"], payload["unverified"]) == (1, 1, 3)
        assert payload["blocked_requests"] == 1
        assert payload["isolation_kinds"]["non_upload_api_blocked"] == 1

    def test_plan_rejects_are_printed_for_diagnosis(self, negative_run):
        """本用例没有非法配置 → 不应出现 [negative-skip]（配置干净时不许有噪声）。"""
        assert "[negative-skip]" not in negative_run["raw"]

    def test_whole_run_fails_so_the_defect_is_visible(self, negative_run):
        """坐实缺陷必须让整轮**红**（沿用既有口径：blocked = 硬失败），不能只写日志。"""
        assert negative_run["result"]["returncode"] != 0, \
            "服务端受理了应拒绝的请求，整轮居然通过了：\n" + negative_run["raw"][-2000:]
        assert "编译断言执行受阻" in negative_run["raw"]

    def test_create_entry_scenario_uses_the_already_open_form(self, create_entry_run):
        """``target_entry="create"``：必须**直接用**平台已打开的新建表单。

        setup 为空 → 只要执行器在进场景前把表单关掉（``closeAnyForm``），
        就会找不到提交按钮、退化成 unverified。因此这条 ``pass`` 是
        "不误关用户要用的表单"这条语义的**决定性**验收。
        """
        rows = _by_scenario(create_entry_run["rows"])
        row = rows["NEG-CREATE-ENTRY"]
        assert row["outcome"] == "pass", row
        assert "服务端按规则拒绝" in row["observed"]
        # 提交真实到达了服务端（且只到了一次）
        writes = [p for m, p in create_entry_run["received"] if m == "PUT"]
        assert writes == [GATEWAY + "/v1/purchaseLimit/add"], writes
        lines = [l for l in create_entry_run["raw"].splitlines() if "NEGATIVE_PHASE" in l]
        payload = json.loads(lines[-1].split("NEGATIVE_PHASE ", 1)[1])
        assert (payload["configured"], payload["executed"], payload["pass"]) == (1, 1, 1)
