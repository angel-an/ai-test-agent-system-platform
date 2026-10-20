# -*- coding: utf-8 -*-
"""P5 回归：分页契约断言的**发布逻辑**在 node 里直接执行（与线上同一份代码段）。

与 `test_web_upload_local_node.py` 同一套路：从 TS 文件里按标记抽取核心段（避免"另写一份假实现"），
在 node 里跑，验证：
  1) 正常页结构 → ok；
  2) 越界返回 null → 失败且原因可读；
  3) 越界返回空数组 + 真实 total/pages → ok；
  4) 缺 pages 字段 → 失败；
  5) 声明有效 known_issue → 归类为 known_issue（**不是**通过），并保留原始响应体；
  6) known_issue 过期 / 无票据 → 一律按普通失败。
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

NODE = shutil.which("node")
CONTRACT_TS = (
    Path(__file__).resolve().parents[1] / "workspace" / "api" / "_lib" / "response_contract.ts"
)
BEGIN = "// >>> RESPONSE_CONTRACT_CORE_BEGIN"
END = "// <<< RESPONSE_CONTRACT_CORE_END"

pytestmark = pytest.mark.skipif(
    NODE is None or not CONTRACT_TS.exists(),
    reason="需要本机 node 与 workspace/api/_lib/response_contract.ts",
)


def _core() -> str:
    text = CONTRACT_TS.read_text(encoding="utf-8")
    assert BEGIN in text and END in text, "核心代码段标记缺失（抽取必须与线上同源）"
    return text.split(BEGIN, 1)[1].split(END, 1)[0]


def _run_node(tmp_path: Path, driver_body: str) -> object:
    # .mts = TypeScript ESM：node 23+ 默认剥离类型注解，且不会被当作 CJS（实测 .ts 会报 Unexpected token 'export'）
    driver = tmp_path / "contract.mts"
    driver.write_text(_core() + "\n" + driver_body, encoding="utf-8")
    proc = subprocess.run([NODE, str(driver)], capture_output=True)
    out = proc.stdout.decode("utf-8", "replace")
    err = proc.stderr.decode("utf-8", "replace")
    assert proc.returncode == 0, f"node 退出码 {proc.returncode}\n{err[-800:]}"
    payload = [l for l in out.splitlines() if l.startswith("{")]
    assert payload, f"未拿到 JSON 输出\n{out[-800:]}\n{err[-400:]}"
    return json.loads(payload[-1])


GOOD = {"resultCode": "0", "data": {"list": [{"id": 1}], "total": 9, "pages": 1, "pageNum": 1}}
OUT_OF_RANGE_NULL = {"resultCode": "0", "data": None}
OUT_OF_RANGE_EMPTY = {"resultCode": "0", "data": {"list": [], "total": 9, "pages": 1, "pageNum": 999999}}
MISSING_PAGES = {"resultCode": "0", "data": {"list": [], "total": 9}}


class TestPaginationContract:
    def test_normal_page_passes(self, tmp_path: Path):
        result = _run_node(tmp_path, f"""
const cases = {json.dumps({'body': GOOD, 'opts': {'pageNum': 1}})};
console.log(JSON.stringify(checkPaginationContract(cases.body, cases.opts)));
""")
        assert result["ok"] is True and result["failures"] == [], result

    def test_out_of_range_null_fails_with_readable_reason(self, tmp_path: Path):
        result = _run_node(tmp_path, f"""
console.log(JSON.stringify(checkPaginationContract(
  {json.dumps(OUT_OF_RANGE_NULL)}, {{pageNum: 999999}})));
""")
        assert result["ok"] is False
        assert any("data 应为对象" in f for f in result["failures"]), result
        assert result["raw"]  # 原始响应体必须保留

    def test_out_of_range_empty_structure_passes(self, tmp_path: Path):
        result = _run_node(tmp_path, f"""
console.log(JSON.stringify(checkPaginationContract(
  {json.dumps(OUT_OF_RANGE_EMPTY)}, {{pageNum: 999999}})));
""")
        assert result["ok"] is True, result

    def test_missing_pages_field_fails(self, tmp_path: Path):
        result = _run_node(tmp_path, f"""
console.log(JSON.stringify(checkPaginationContract({json.dumps(MISSING_PAGES)}, {{}})));
""")
        assert result["ok"] is False
        assert any("data.pages 缺失" in f for f in result["failures"]), result

    def test_known_issue_is_not_a_pass(self, tmp_path: Path):
        result = _run_node(tmp_path, f"""
console.log(JSON.stringify(checkPaginationContract(
  {json.dumps(OUT_OF_RANGE_NULL)}, {{pageNum: 999999}},
  {{id: 'DEFECT-002', until: '2099-12-31', today: '2026-09-30', note: '越界返回 null'}})));
""")
        assert result["ok"] is False, "known_issue 不得变成通过"
        assert result["knownIssue"] is True
        assert result["knownIssueId"] == "DEFECT-002"
        assert "已知问题" in result["summary"]
        assert result["raw"], "原始失败证据必须保留"

    def test_expired_known_issue_falls_back_to_failure(self, tmp_path: Path):
        result = _run_node(tmp_path, f"""
console.log(JSON.stringify(checkPaginationContract(
  {json.dumps(OUT_OF_RANGE_NULL)}, {{pageNum: 999999}},
  {{id: 'DEFECT-002', until: '2026-09-01', today: '2026-09-30'}})));
""")
        assert result["knownIssue"] is False, "过期例外必须失效"
        assert "分页契约失败" in result["summary"]

    def test_known_issue_without_ticket_is_inactive(self, tmp_path: Path):
        result = _run_node(tmp_path, f"""
console.log(JSON.stringify(checkPaginationContract(
  {json.dumps(OUT_OF_RANGE_NULL)}, {{pageNum: 999999}}, {{id: '   '}})));
""")
        assert result["knownIssue"] is False
        assert result["ok"] is False

    def test_reporter_throws_instead_of_silent_pass(self, tmp_path: Path):
        """包装层：known_issue 与普通失败都必须抛出（测试保持失败），不能静默通过。"""
        result = _run_node(tmp_path, f"""
const annotations = [];
const attachments = [];
const hook = {{annotate: (t, d) => annotations.push(t), attach: (n) => attachments.push(n)}};
const known = checkPaginationContract({json.dumps(OUT_OF_RANGE_NULL)}, {{pageNum: 999999}},
  {{id: 'DEFECT-002', until: '2099-12-31'}});
let knownThrew = '';
try {{ reportContractResult(known, hook); }} catch (e) {{ knownThrew = String(e.message); }}
const plain = checkPaginationContract({json.dumps(MISSING_PAGES)}, {{}});
let plainThrew = '';
try {{ reportContractResult(plain, hook); }} catch (e) {{ plainThrew = String(e.message); }}
console.log(JSON.stringify({{knownThrew, plainThrew, annotations, attachments,
  okCase: reportContractResult.length >= 1}}));
""")
        assert result["knownThrew"].startswith("KNOWN_ISSUE[DEFECT-002]"), result
        assert result["plainThrew"].startswith("CONTRACT_FAILURE"), result
        assert "known_issue" in result["annotations"] and "contract_failure" in result["annotations"]
        assert "pagination-response" in result["attachments"]
