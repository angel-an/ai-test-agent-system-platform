# -*- coding: utf-8 -*-
"""P4 采集触发回归（本地受控，不碰 UAT）：计划生成 → 运行期定位失败 → 产出 [observation] → 台账可读。

复核意见（2026-09-30）：TR-155 只证明"已有 observation 能被接口与 UI 消费"，
不能替代"当前版本遇到定位失败时仍会生成 observation"这条**采集触发**回归。
本用例用受控合成页 + 线上同一份 `render_form_executor` 产物补上这条链路，
并顺带锁定两件事：
  1) 错配 selector_hint 必须**秒级**返回（回归 TR-154 的 602s 挂死缺陷）；
  2) 现场必须含真实控件清单（placeholder / maxLength），且台账解析器能读到。
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
import time
import uuid
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.services.assertion_ledger import build_assertion_ledger
from app.services.web_case_compiler import render_form_executor

BACKEND_DIR = Path(__file__).resolve().parents[1]
WEB_CLI = BACKEND_DIR / "workspace" / "web_cli"
NODE = shutil.which("node")
NPX = shutil.which("npx")
pytestmark = pytest.mark.skipif(
    not (NODE and NPX and (WEB_CLI / "node_modules" / "@playwright" / "test").exists()),
    reason="需要本机 node + @playwright/test（backend/workspace/web_cli）",
)

#: 受控页面：字段名与占位符都是测试自造，**不引用任何业务/项目信息**
FIELD = "字段A"
PLACEHOLDER = "请输入字段A"
MAX_LENGTH = 12

PAGE = f"""<!doctype html>
<html lang="zh"><head><meta charset="utf-8"><title>controlled form</title>
<style>.ant-modal{{display:block}}</style></head>
<body>
  <button id="open">新建</button>
  <div id="host"></div>
<script>
  document.getElementById('open').addEventListener('click', function () {{
    var host = document.getElementById('host');
    if (!host.innerHTML.trim()) {{
      host.innerHTML = '<div class="ant-modal"><div class="ant-form-item">'
        + '<div>{FIELD}</div>'
        + '<input id="f" class="ant-input" type="text" placeholder="{PLACEHOLDER}" '
        + 'maxlength="{MAX_LENGTH}" /></div></div>';
    }}
    host.style.display = 'block';
  }});
</script>
</body></html>
"""


def _plan(selector_hint: str) -> dict:
    return {
        "locators": {
            "flow": "page_form",
            "form_button": ["新建"],
            "modal": ".ant-modal",
            "fields": {FIELD: {"kind": "input", "selector_hint": selector_hint}},
        },
        "checks": [{
            "type": "input_validation", "field": FIELD, "data": f"{MAX_LENGTH}字",
            "expect": "accept", "rule": f"{FIELD}≤{MAX_LENGTH}字",
            "rule_id": "R-T1", "case_id": "TC-P4T", "probe_value": "x" * MAX_LENGTH,
        }],
        "skipped": [], "skipped_count": 0,
    }


def _run_executor(selector_hint: str, tag: str) -> tuple[float, str]:
    """把线上同一份执行器产物渲染成 spec 并在受控页上执行，返回 (耗时秒, stdout)。"""
    workdir = WEB_CLI / "tests" / f"p4_trigger_{tag}_{uuid.uuid4().hex[:8]}"
    workdir.mkdir(parents=True, exist_ok=True)
    page_path = workdir / "page.html"
    page_path.write_text(PAGE, encoding="utf-8")
    spec = "\n".join([
        "import { test, expect } from '@playwright/test';",
        "",
        f"test('P4 trigger {tag}', async ({{ page }}) => {{",
        f"  await page.goto('{page_path.as_uri()}');",
        "  const appScope = page;",
        render_form_executor(_plan(selector_hint)),
        "});",
        "",
    ])
    spec_path = workdir / "trigger.spec.ts"
    spec_path.write_text(spec, encoding="utf-8")

    started = time.time()
    proc = subprocess.run(
        [NPX, "playwright", "test", spec_path.relative_to(WEB_CLI).as_posix(), "--reporter=list"],
        cwd=str(WEB_CLI), capture_output=True,
    )
    elapsed = time.time() - started
    out = proc.stdout.decode("utf-8", "replace") + proc.stderr.decode("utf-8", "replace")
    return elapsed, out


def _assertion_record(stdout: str) -> dict:
    for line in stdout.splitlines():
        if "COMPILED_ASSERTION {" in line:
            return json.loads(line.split("COMPILED_ASSERTION ", 1)[1])
    return {}


def _observation_record(stdout: str) -> dict | None:
    lines = [l for l in stdout.splitlines() if l.startswith("[observation] ")]
    return json.loads(lines[0][len("[observation] "):]) if lines else None


class TestObservationTrigger:
    """错配定位器：必须产出可读现场。"""

    def test_miss_produces_observation_and_ledger_reads_it(self):
        elapsed, stdout = _run_executor("#not-exist-xyz", "miss")

        record = _assertion_record(stdout)
        assert record.get("outcome") == "unverified", record
        assert "未定位到输入框" in str(record.get("observed") or ""), record

        observation = _observation_record(stdout)
        assert observation is not None, f"必须输出 [observation] 行\n{stdout[-1200:]}"
        assert observation.get("field") == FIELD
        controls = observation.get("controls") or []
        assert any(c.get("placeholder") == PLACEHOLDER and c.get("maxLength") == MAX_LENGTH
                   for c in controls), controls
        assert observation.get("pages"), "现场必须含页面清单"

        # 台账解析器能把这行变成结构化数据（页面/接口消费的就是它）
        job = SimpleNamespace(script_identifier="WT-T1", status="completed", stdout=stdout)
        ledger = build_assertion_ledger([job])
        assert ledger["totals"]["observations"] == 1, ledger["totals"]
        assert ledger["observations"][0]["field"] == FIELD

        # 回归 TR-154：定位落空必须秒级返回，不再挂到 runner 超时（曾 602s、stdout 全空）
        assert elapsed < 90, f"定位落空应在秒级返回，实测 {elapsed:.1f}s"

    def test_correct_locator_has_no_observation(self):
        _elapsed, stdout = _run_executor("#f", "hit")
        record = _assertion_record(stdout)
        assert record.get("outcome") == "pass", record
        assert _observation_record(stdout) is None, "定位成功不应产生落空现场"
        job = SimpleNamespace(script_identifier="WT-T2", status="completed", stdout=stdout)
        assert build_assertion_ledger([job])["totals"]["observations"] == 0
