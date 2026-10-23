# -*- coding: utf-8 -*-
"""编译器门禁集（compiler gate）——改 ``web_case_compiler`` 时的**最小必跑集**。

为什么单独一层
--------------
仓库现有三层测试口径，但都盖不住编译器：

- **P0 基线**（``tests/p0_baseline_manifest.py``）：`tests/platform` + 几个 api 文件，
  约 719 条，证明「平台主链路可用」，**不含** ``test_channel_rev*`` / ``test_web_*``；
- **全量**：2830 条，约 60 分钟（实测 1.27s/条），合入前才跑；
- **回显/编译器族**：散在根目录 30 多个文件里，没有名字，只能靠 grep 找。

后果是实测过的：``test_anchored_error_rev140`` 把 ``COMPILER_REV`` 追成字面量 ``"rev180"``，
此后每 bump 一次红一次，从 rev181 一直红到 rev196；``test_submit_resubmit_reconcile_rev139``
的断言自 rev165 改实现后就一直红。两条红线埋了十几个版本，因为没人有一条约定的命令去跑它们。

发现规则
--------
不维护一份手写文件清单（会漂），而是**按依赖关系自动发现**：

    测试文件正文里出现 ``web_case_compiler`` / ``render_form_executor`` /
    ``build_form_plan`` / ``_FORM_EXECUTOR_TS`` 之一 ⇒ 属于门禁集。

需要排除的
----------
``*_browser_local.py`` 与 ``test_web_upload_local_node.py`` 会拉起真实浏览器 /
Node，依赖 UAT 可达性，属于**集成层**而非门禁层：它们慢、且失败原因多半是环境。
门禁集必须「红了就是代码红了」，所以一律排除（见 ``INTEGRATION_EXCLUDED``）。

维护约定
--------
1. 新增依赖编译器的测试 → 自动纳入，无需改本文件；
2. 新增**必须**排除的文件 → 加进 ``INTEGRATION_EXCLUDED`` 并写明理由；
3. 不要在本文件里写死用例数量 —— 数量由 ``scripts/run_compiler_gate.py`` 实际执行决定。
"""

from __future__ import annotations

from pathlib import Path

TESTS_DIR = Path(__file__).resolve().parent
BACKEND_DIR = TESTS_DIR.parent

#: 判定「依赖编译器」的源码特征（任一命中即纳入）
DEPENDENCY_MARKERS = (
    "web_case_compiler",
    "_FORM_EXECUTOR_TS",
    "render_form_executor",
    "build_form_plan",
)

#: 集成层排除项：需要真实浏览器 / Node / 外部可达环境。
#: 它们不进内存基线 —— 失败时无法区分「代码坏了」还是「环境不可达」。
INTEGRATION_EXCLUDED = {
    "tests/test_web_negative_scenario_browser_local.py": "拉真实浏览器跑负路径场景（依赖 UAT 可达）",
    "tests/test_web_toolbar_modal_browser_local.py": "拉真实浏览器验证工具栏/弹窗（依赖 UAT 可达）",
    "tests/test_web_upload_browser_local.py": "拉真实浏览器验证上传控件（依赖 UAT 可达）",
    "tests/test_web_upload_local_node.py": "起本地 Node 跑上传脚本（依赖 node 与临时目录）",
    "tests/test_shell_policy.py": "需要 anthropic 包（本机 managed python 未装，收集即失败）",
}

#: 本机跑门禁时额外要忽略的（可选，脚本按需追加）
SPEED_BUDGET_SECONDS = 300


def discover(tests_dir: Path | None = None) -> list[str]:
    """返回门禁集文件列表（仓库相对路径，已排除集成层）。"""
    root = tests_dir or TESTS_DIR
    found: list[str] = []
    for path in sorted(root.rglob("test_*.py")):
        if "__pycache__" in path.parts:
            continue
        rel = path.relative_to(BACKEND_DIR).as_posix()
        if rel in INTEGRATION_EXCLUDED:
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
        except OSError:  # pragma: no cover - 权限/占用
            continue
        if any(marker in text for marker in DEPENDENCY_MARKERS):
            found.append(rel)
    return found


def excluded_hits(tests_dir: Path | None = None) -> dict[str, list[str]]:
    """被排除的文件里，哪些**确实**依赖编译器（用于发现「排除理由已过期」）。"""
    root = tests_dir or TESTS_DIR
    hits: dict[str, list[str]] = {}
    for rel in INTEGRATION_EXCLUDED:
        path = BACKEND_DIR / rel
        if not path.exists():
            hits[rel] = ["文件不存在"]
            continue
        text = path.read_text(encoding="utf-8", errors="ignore")
        matched = [m for m in DEPENDENCY_MARKERS if m in text]
        if matched:
            hits[rel] = matched
    return hits
