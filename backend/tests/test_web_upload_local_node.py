# -*- coding: utf-8 -*-
"""本地受控测试：直接执行**发布出去的**代码段（从执行器模板里按标记抽取），在 node 里跑。

复核意见 6 要求"假通过分支本地就能验证"，这里覆盖两条无需浏览器、也无需写 UAT 的分支：

1. 隔离策略判定：放行清单 + 默认拒绝（含"不含上传关键词的地址"也必须阻断）；
2. 字节精确的工作簿样本：node 生成的真实字节用 Python ``zipfile`` 校验结构与大小。
"""

from __future__ import annotations

import base64
import io
import json
import shutil
import subprocess
import textwrap
import zipfile
from pathlib import Path

import pytest

from app.services.web_case_compiler import _FORM_EXECUTOR_TS
from app.services.web_upload_fixtures import XLSX_PARTS, xlsx_parts_base64, xlsx_stored_overhead

NODE = shutil.which("node")
MARKERS = {
    "zip": ("// >>> UPLOAD_ZIP_BUILDER_BEGIN", "// <<< UPLOAD_ZIP_BUILDER_END"),
    "policy": ("// >>> UPLOAD_ISOLATION_POLICY_BEGIN", "// <<< UPLOAD_ISOLATION_POLICY_END"),
}


def _extract(which: str) -> str:
    """按标记抽取真实代码段（与线上同源，避免"另写一份假实现"）。"""
    begin, end = MARKERS[which]
    body = _FORM_EXECUTOR_TS.split(begin, 1)[1].split(end, 1)[0]
    return textwrap.dedent(body)


def test_markers_exist():
    for begin, end in MARKERS.values():
        assert begin in _FORM_EXECUTOR_TS and end in _FORM_EXECUTOR_TS


def _run_node(args: list[str]) -> subprocess.CompletedProcess:
    """执行 node：按 UTF-8 解码并容错（Windows 默认 GBK 会把中文输出解成异常）。"""
    return subprocess.run([NODE, *args], capture_output=True)


def _text(proc: subprocess.CompletedProcess) -> tuple[str, str]:
    return (proc.stdout.decode("utf-8", "replace"), proc.stderr.decode("utf-8", "replace"))


@pytest.mark.skipif(NODE is None, reason="本机没有 node，跳过对发布脚本的本地执行测试")
class TestShippedZipBuilder:
    def test_generated_bytes_are_valid_zip_of_exact_size(self, tmp_path: Path):
        driver = tmp_path / "gen.ts"
        driver.write_text(
            "const FIX = {workbook_parts: %s};\n" % json.dumps(xlsx_parts_base64(), ensure_ascii=False)
            + _extract("zip")
            + "\nconst out: Record<string, string> = {};\n"
              "const targets = [%d, 4096, 1024 * 1024 + 1];\n"
              "for (const t of targets) {\n"
              "  const buf = workbookOfExactBytes(t);\n"
              "  out[String(t)] = buf ? buf.toString('base64') : '';\n"
              "}\n"
              "console.log(JSON.stringify(out));\n" % (xlsx_stored_overhead() + 512),
            encoding="utf-8",
        )
        proc = _run_node([str(driver)])
        out, err = _text(proc)
        assert proc.returncode == 0, err

        payload = json.loads([l for l in out.splitlines() if l.startswith("{")][-1])
        for target in (xlsx_stored_overhead() + 512, 4096, 1024 * 1024 + 1):
            data = base64.b64decode(payload[str(target)])
            assert len(data) == target, "字节数必须精确（N/N+1 临界样本依赖它）"
            with zipfile.ZipFile(io.BytesIO(data)) as zf:
                assert zf.testzip() is None
                assert set(XLSX_PARTS) <= set(zf.namelist())
                # 工作簿部件内容必须与 Python 侧一致（不是随便填的字节）
                assert zf.read("xl/worksheets/sheet1.xml") == XLSX_PARTS["xl/worksheets/sheet1.xml"]


@pytest.mark.skipif(NODE is None, reason="本机没有 node，跳过对发布脚本的本地执行测试")
class TestShippedIsolationPolicy:
    CASES = [
        ["GET", "https://xysj-cdp-uat-mall.oss-cn-shanghai.aliyuncs.com/upload", "continue"],
        ["POST", "https://xysj-cdp-uat-mall.oss-cn-shanghai.aliyuncs.com/", "abort"],
        # 关键：**不含任何上传关键词**的地址 + 二进制请求体，旧的特征匹配会漏，这里必须阻断
        ["POST", "https://telemetry.example.net/beacon", "abort"],
        ["PUT", "https://storage.example.org/put", "abort"],
        ["POST", "data:text/plain,hello", "abort"],
        # 即便同源、即便在旧 host 清单里：**写请求**默认阻断（不再默认放行同源写）
        ["POST", "https://console-uat.example.com/api/query", "abort"],
        ["PUT", "https://console-uat.example.com/api/import", "abort"],
    ]

    def _decide(self, tmp_path: Path, rules: list[dict], name: str) -> list[str]:
        driver = tmp_path / f"{name}.ts"
        driver.write_text(
            "const rules = %s;\n" % json.dumps(rules)
            + "const legacyHosts = ['console-uat.example.com'];\n"
            + _extract("policy")
            + "\nconst cases = %s;\n" % json.dumps([c[:2] for c in self.CASES])
            + "console.log(JSON.stringify(cases.map(([m, u]) => "
              "isolationDecision(m, u, rules, legacyHosts, ''))));\n",
            encoding="utf-8",
        )
        proc = _run_node([str(driver)])
        out, err = _text(proc)
        assert proc.returncode == 0, err
        return json.loads([l for l in out.splitlines() if l.startswith("[")][-1])

    def test_default_blocks_all_writes(self, tmp_path: Path):
        """默认（规则为空）：只有 GET 放行，所有写请求阻断 —— 不默认放行同源写。"""
        assert self._decide(tmp_path, [], "policy_default") == [c[2] for c in self.CASES]

    def test_explicit_rule_allows_named_write_only(self, tmp_path: Path):
        """显式规则只能放行**指定路径+方法**的写请求，同主机其它写请求仍阻断。"""
        rules = [{"host": "console-uat.example.com", "path_prefix": "/api/query",
                  "methods": ["POST"]}]
        decisions = self._decide(tmp_path, rules, "policy_rule")
        by_case = {tuple(c[:2]): d for c, d in zip(self.CASES, decisions)}
        assert by_case[("POST", "https://console-uat.example.com/api/query")] == "continue"
        assert by_case[("PUT", "https://console-uat.example.com/api/import")] == "abort"
        assert by_case[("POST", "https://telemetry.example.net/beacon")] == "abort"

    def test_multipart_is_blocked_even_when_rule_matches(self, tmp_path: Path):
        """**写**放行规则挡不住 multipart：传文件必须由专门的 allow_upload 规则放行。

        （2026-10-16 rev114 起 multipart 不再是"永不放行"，而是"只认 allow_upload"；
        本用例锁死"写规则不能顺带放行上传"这一条。）
        """
        driver = tmp_path / "policy_ct.ts"
        driver.write_text(
            "const rules = [{'host': 'oss.example.com'}];\n"
            + _extract("policy")
            + "\nconsole.log(isolationDecision('POST', 'https://oss.example.com/put', "
              "rules, [], 'multipart/form-data; boundary=x'));\n",
            encoding="utf-8",
        )
        proc = _run_node([str(driver)])
        out, err = _text(proc)
        assert proc.returncode == 0, err
        assert out.strip().splitlines()[-1].strip() == "abort"

    def _decide_with_ct(self, tmp_path: Path, rules: list[dict], upload_rules: list[dict],
                        cases: list[list[str]], name: str) -> list[str]:
        driver = tmp_path / f"{name}.ts"
        driver.write_text(
            "const rules = %s;\n" % json.dumps(rules)
            + "const uploadRules = %s;\n" % json.dumps(upload_rules)
            + "const legacyHosts: string[] = [];\n"
            + _extract("policy")
            + "\nconst cases = %s;\n" % json.dumps(cases)
            + "console.log(JSON.stringify(cases.map((c) => "
              "isolationDecision(c[0], c[1], rules, legacyHosts, c[2], uploadRules))));\n",
            encoding="utf-8",
        )
        proc = _run_node([str(driver)])
        out, err = _text(proc)
        assert proc.returncode == 0, err
        return json.loads([l for l in out.splitlines() if l.startswith("[")][-1])

    def test_upload_opt_in_default_is_byte_identical(self, tmp_path: Path):
        """安全边界回归锁（rev114 核心保证）：不声明上传放行规则 ⇒ multipart 一律 abort。

        与改动前**逐字一致**：默认拒绝的口径没有因为新增口子而松动。
        """
        mp = "multipart/form-data; boundary=----x"
        cases = [
            ["POST", "https://static.uat.eatizen.com.hk/", mp],
            ["POST", "https://oss.example.com/put", mp],
            ["PUT", "https://static.uat.eatizen.com.hk/x", mp],
            # 非 multipart 的写请求不受影响：没有**写**放行规则时仍然阻断
            ["POST", "https://console-uat.example.com/api/query", "application/json"],
        ]
        assert self._decide_with_ct(tmp_path, [], [], cases, "ct_off") == ["abort"] * 4

    def test_upload_rule_allows_only_declared_multipart(self, tmp_path: Path):
        """显式声明才放行 multipart，且仍被 host + 路径前缀 + 方法三重收窄。"""
        upload_rules = [{"host_suffix": "static.uat.eatizen.com.hk",
                         "path_prefix": "/", "methods": ["POST"]}]
        mp = "multipart/form-data; boundary=----x"
        cases = [
            # 声明的目标 → 放行
            ["POST", "https://static.uat.eatizen.com.hk/", mp],
            # 主机不同 → 阻断（后缀匹配不得越界到别的域）
            ["POST", "https://static.uat.eatizen.com.hk.attacker.net/", mp],
            # 方法不符 → 阻断
            ["PUT", "https://static.uat.eatizen.com.hk/", mp],
            # 不是 multipart → 不归本规则管（也没有写放行规则）→ 阻断
            ["POST", "https://static.uat.eatizen.com.hk/", "application/json"],
        ]
        assert self._decide_with_ct(tmp_path, [], upload_rules, cases, "ct_on") == [
            "continue", "abort", "abort", "abort"]

    def test_upload_rule_path_prefix_is_enforced(self, tmp_path: Path):
        """路径前缀同样收窄（防止"给根路径开个口子"扩成整站放行）。"""
        upload_rules = [{"host_suffix": "cdn.example.com", "path_prefix": "/upload/",
                         "methods": ["POST"]}]
        mp = "multipart/form-data; boundary=----x"
        cases = [
            ["POST", "https://cdn.example.com/upload/a.png", mp],
            ["POST", "https://cdn.example.com/other", mp],
            ["POST", "https://cdn.example.com/", mp],
        ]
        assert self._decide_with_ct(tmp_path, [], upload_rules, cases, "ct_path") == [
            "continue", "abort", "abort"]

    def test_policy_source_has_no_blanket_self_origin_allow(self):
        policy = _extract("policy")
        assert "multipart/form-data" in policy
        assert "rules" in policy
        # 不得出现"同源即放行"的写法（安全边界回归）
        assert "selfHost ? [selfHost]" not in policy

