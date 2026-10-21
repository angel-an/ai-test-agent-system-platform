# -*- coding: utf-8 -*-
"""rev116（2026-10-19）：两个探针的**行为**验证（不是子串断言）。

上一轮踩过的坑：``assert "调用语句" in script`` 在那一行被注释掉后照样通过
（假通过）。所以这里不比对字符串，而是把生成的 TS 切出来、编译、用假对象真跑：

1. ``refreshCrmScope``：入口 iframe 尚未插入（iframes=0 → 选了 page）是
   10-17 那批菜单失败的可疑机制之一。这里验证三件事：
   - 0 → 1 时**会等**到 iframe 出现并返回 iframe scope（changed=true）；
   - 一直是 0 时**超时退出**，返回 page（changed=false），不无限等；
   - 入口已有 1 个 iframe 时**不等待**（再等也是取第一个，纯浪费时间）。
2. ``__labelRe``（覆盖度软校验）：``店鋪名稱(CN）`` 与页面 ``店鋪名稱（CN）``
   只差全/半角括号，旧实现 ``getByText(label)`` 精确子串匹配 0 命中 ——
   会被误读成"字段不存在"。这里用页面文案真跑一遍，并校验未命中标签被记名。
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

TSC_CANDIDATES = ("tsc", "tsc.cmd")


def _node() -> str | None:
    return shutil.which("node")


def _tsc() -> tuple[str, str] | None:
    """返回 (node_exe, tsc_entry)。tsc 优先用 PATH 上的 .cmd，其次仓库内 typescript。"""
    node = _node()
    if not node:
        return None
    for name in TSC_CANDIDATES:
        found = shutil.which(name)
        if found:
            return node, found
    repo = Path(__file__).resolve().parents[2]
    entry = repo / "gitnexus-web" / "node_modules" / "typescript" / "bin" / "tsc"
    if entry.exists():
        return node, str(entry)
    return None


requires_node = pytest.mark.skipif(_node() is None, reason="本机无 node，跳过行为验证")


def _compile_and_run(tmp_path: Path, source: str, name: str = "probe") -> str:
    tool = _tsc()
    assert tool is not None, "需要 tsc（PATH 或仓库内 typescript）"
    node, tsc = tool
    ts = tmp_path / f"{name}.ts"
    ts.write_text(source, encoding="utf-8")
    out_dir = tmp_path / "js"
    emit = subprocess.run(
        [node, tsc, "--target", "ES2020", "--module", "commonjs",
         "--skipLibCheck", "--outDir", str(out_dir), str(ts)],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
    )
    assert emit.returncode == 0, f"tsc 编译失败：\n{emit.stdout}\n{emit.stderr}"
    js = out_dir / f"{name}.js"
    # rev116-fix：子进程输出含中文（字段名/日志），必须显式按 UTF-8 解码。
    # 只写 text=True 会让父进程用 locale（Windows 中文环境 = GBK）解码，
    # 实测 `UnicodeDecodeError: 'gbk' codec can't decode byte 0xae` —— 与代码无关的环境性失败。
    run = subprocess.run(
        [node, str(js)], capture_output=True, text=True, encoding="utf-8", errors="replace",
    )
    assert run.returncode == 0, f"node 运行失败：\n{run.stdout}\n{run.stderr}"
    return run.stdout


# --------------------------------------------------------- refreshCrmScope

_REFRESH_PRELUDE = """
type Page = any;
type ClickScope = any;
declare function visibleMenuTexts(scope: any): Promise<string[]>;
"""

_REFRESH_HARNESS = """
let __seq: number[] = SEQ;
let __idx = 0;
let __waits = 0;
const __iframeScope: any = { __scope: 'iframe#0' };
const __appPage: any = {
  __scope: 'page',
  locator: (_sel: string) => ({
    count: async () => {
      const v = __seq[Math.min(__idx, __seq.length - 1)];
      __idx += 1;
      return v;
    },
  }),
  waitForTimeout: async (_ms: number) => { __waits += 1; },
  frameLocator: (_sel: string) => ({ first: () => __iframeScope }),
};
(async () => {
  const started = Date.now();
  // CURRENT 传的是「当前 scope 对象」本身（rev116-fix 后的新签名），不再传帧数
  const __currentKind: string = CURRENT;
  const __current: any = __currentKind === 'page' ? __appPage : __iframeScope;
  const r = await refreshCrmScope(__appPage, __current, 'unit', WAIT_MS);
  console.log('RESULT ' + JSON.stringify({
    frames: r.frames,
    stale: r.stale,
    isIframe: !!(r.scope && (r.scope as any).__scope === 'iframe#0'),
    waits: __waits,
    elapsedMs: Date.now() - started,
  }));
})();
"""


def _refresh_source(seq: list[int], current: str = "page", wait_ms: int = 300) -> str:
    from app.services.web_case_link_service import _FRAME_DIAG_TEMPLATE

    block = _FRAME_DIAG_TEMPLATE
    harness = (
        _REFRESH_HARNESS.replace("SEQ", json.dumps(seq))
        .replace("CURRENT", json.dumps(current))
        .replace("WAIT_MS", str(wait_ms))
    )
    return (
        _REFRESH_PRELUDE
        + "declare const process: any;\n"
        + block
        + harness
    )


@requires_node
class TestRefreshCrmScopeBehavior:
    def test_waits_until_iframe_appears(self, tmp_path):
        """入口 iframes=0，200ms 后 iframe 才插入 —— 这是要修的那个失败模式。"""
        out = _compile_and_run(tmp_path, _refresh_source([0, 0, 1], current="page"))
        payload = json.loads(out.split("RESULT ", 1)[1].strip())
        assert payload["frames"] == 1
        assert payload["stale"] is True
        assert payload["isIframe"] is True
        assert payload["waits"] >= 1

    def test_race_page_scope_switches_even_if_frame_count_unchanged(self, tmp_path):
        """rev116-fix 的核心回归：竞态下「scope=page 但帧数已经是 1」。

        旧实现里调用方会另读一次 frameCount 得到 1，于是重取判 `frames !== prev` 为假
        ⇒ 直接放弃，scope 永远停在 page（实测 probes=["page","page"]）。
        新实现由 scope 身份反推，帧数一直是 1 也必须判 stale 并切换。
        """
        out = _compile_and_run(tmp_path, _refresh_source([1], current="page"))
        payload = json.loads(out.split("RESULT ", 1)[1].strip())
        assert payload["frames"] == 1
        assert payload["stale"] is True, "帧数没变但当前 scope 是 page —— 必须换回 iframe"
        assert payload["isIframe"] is True
        # 帧已经在，不该再等 6s
        assert payload["waits"] == 0

    def test_detached_iframe_falls_back_to_page(self, tmp_path):
        """反向竞态：当前停在 iframe，但帧已经脱离。同样要换，否则后续全打在空 frame 上。"""
        out = _compile_and_run(tmp_path, _refresh_source([0], current="iframe", wait_ms=200))
        payload = json.loads(out.split("RESULT ", 1)[1].strip())
        assert payload["frames"] == 0
        assert payload["stale"] is True
        assert payload["isIframe"] is False

    def test_gives_up_when_iframe_never_appears(self, tmp_path):
        out = _compile_and_run(tmp_path, _refresh_source([0], current="page", wait_ms=200))
        payload = json.loads(out.split("RESULT ", 1)[1].strip())
        assert payload["frames"] == 0
        assert payload["stale"] is False
        assert payload["isIframe"] is False
        # 有上限，不会无限等
        assert payload["elapsedMs"] < 3000

    def test_no_wait_when_iframe_already_present(self, tmp_path):
        """已停在 iframe 且帧还在 —— 再等也还是取第一个，不该浪费 6s。"""
        out = _compile_and_run(tmp_path, _refresh_source([1], current="iframe", wait_ms=2000))
        payload = json.loads(out.split("RESULT ", 1)[1].strip())
        assert payload["frames"] == 1
        assert payload["stale"] is False
        assert payload["waits"] == 0

    def test_emits_scope_refresh_line(self, tmp_path):
        out = _compile_and_run(tmp_path, _refresh_source([0, 1], current="page"))
        assert "SCOPE_REFRESH" in out
        line = [l for l in out.splitlines() if l.startswith("SCOPE_REFRESH")][0]
        logged = json.loads(line.split(" ", 1)[1])
        assert logged["reason"] == "unit"
        assert logged["prevScope"] == "page"
        assert logged["chosen"] == "iframe#0"
        # adopt 必须与实际决策一致：日志里说换了，调用方就真的换（旧实现在竞态下会自证失真）
        assert logged["adopt"] is logged["stale"]

    def test_log_does_not_claim_switch_when_staying(self, tmp_path):
        """停在 iframe 且帧还在时，日志不能写「会换 scope」。"""
        out = _compile_and_run(tmp_path, _refresh_source([1], current="iframe"))
        line = [l for l in out.splitlines() if l.startswith("SCOPE_REFRESH")][0]
        assert json.loads(line.split(" ", 1)[1])["adopt"] is False



# ----------------------------------------------------------- __labelRe 行为

_LABEL_PAGE_TEXTS = ["店鋪名稱（CN）", "營業時間: 開始", "Store Name", "联系电话"]
_LABEL_LABELS = ["店鋪名稱(CN）", "營業時間：開始", "Store Name*", "联系电话", "不存在的字段"]


@requires_node
class TestLabelMatchBehavior:
    def _source(self, page_texts: list[str] | None = None,
                labels: list[str] | None = None) -> str:
        from app.services.web_case_compiler import render_coverage_snippet

        page_texts = _LABEL_PAGE_TEXTS if page_texts is None else page_texts
        labels = _LABEL_LABELS if labels is None else labels
        return (
            "declare const process: any;\n"
            f"const PAGE_TEXTS: string[] = {json.dumps(page_texts, ensure_ascii=False)};\n"
            "const COMPILED_CASES: any = "
            + json.dumps({
                "field_labels": labels,
                "case_count": 1, "rule_case_count": 1, "narrative_case_count": 0,
                "dangling_rule_ids": [], "assertion_count": 5,
                "locator_depended_assertion_count": 0, "soft_assertion_count": 0, "rule_ids": ["R1"],
            }, ensure_ascii=False)
            + ";\n"
            "const appScope: any = {\n"
            "  getByText: (re: RegExp) => ({\n"
            "    first: () => ({\n"
            "      count: async () => (PAGE_TEXTS.filter((t) => re.test(t)).length ? 1 : 0),\n"
            "    }),\n"
            "  }),\n"
            "};\n"
            "async function __run() {\n"
            + render_coverage_snippet()
            + "\n}\n__run();\n"
        )

    def test_fullwidth_and_space_variants_hit(self, tmp_path):
        out = _compile_and_run(tmp_path, self._source(), name="cov")
        line = [l for l in out.splitlines() if l.startswith("COMPILED_COVERAGE")][0]
        payload = json.loads(line.split(" ", 1)[1])
        # 4 个真实字段都要命中（旧实现只有「联系电话」命中）
        assert payload["field_labels_found"] == 4
        assert payload["field_labels_total"] == 5

    def test_halfwidth_page_is_matched_by_halfwidth_label(self, tmp_path):
        """rev116-fix（复核 §3）：半角页面必须也能命中。

        这条是变异 D′（把 `[（(]` 收窄成只认全角）的**捕获测试**：
        只覆盖全角页面的样本抓不到 D′，因为 label 的半角 `(` 被换成全角后仍能匹配全角页面。
        """
        out = _compile_and_run(
            tmp_path,
            self._source(
                page_texts=["Store Name(CN)", "联系电话"],
                labels=["Store Name(CN)", "联系电话"],
            ),
            name="cov_half",
        )
        line = [l for l in out.splitlines() if l.startswith("COMPILED_COVERAGE")][0]
        payload = json.loads(line.split(" ", 1)[1])
        assert payload["field_labels_found"] == 2, payload["field_labels_missing"]
        assert payload["field_labels_missing"] == []

    def test_halfwidth_page_is_matched_by_fullwidth_label(self, tmp_path):
        """文档是全角、页面是半角 —— 反向也不能漏（同样能抓 D′）。"""
        out = _compile_and_run(
            tmp_path,
            self._source(
                page_texts=["Store Name(CN)"],
                labels=["Store Name（CN）"],
            ),
            name="cov_half2",
        )
        line = [l for l in out.splitlines() if l.startswith("COMPILED_COVERAGE")][0]
        payload = json.loads(line.split(" ", 1)[1])
        assert payload["field_labels_found"] == 1, payload["field_labels_missing"]


    def test_missing_labels_are_named(self, tmp_path):
        out = _compile_and_run(tmp_path, self._source(), name="cov2")
        line = [l for l in out.splitlines() if l.startswith("COMPILED_COVERAGE")][0]
        payload = json.loads(line.split(" ", 1)[1])
        assert payload["field_labels_missing"] == ["不存在的字段"]
