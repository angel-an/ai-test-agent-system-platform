# -*- coding: utf-8 -*-
"""交付完整性检查：暂存内容是否"引用了未跟踪/未暂存的文件"。

为什么需要它
------------
`scripts/check_staged_closure.py` 只校验**前端** `@/` 别名，且判定条件是
``Path.exists()`` —— 文件在磁盘上即可。这带来一个真实事故：
P0 提交时 `backend/app/models/api_asset.py`、`postman_collection_parser.py`、
`api_spec_normalizer.py`、迁移 `0017-0019` 等**从未被跟踪**，暂存的服务代码却
import/依赖它们；闭包检查全部通过、638 个测试通过（测的是工作区），
但**提交快照在干净检出后无法工作**。

本检查器做的是"提交后能不能跑"：

1. 以暂存文件为根，沿 ``import`` / ``from ... import`` 递归解析后端模块；
2. 把模块解析到具体文件（``app/x/y.py`` 或 ``app/x/y/__init__.py``）；
3. 报告**既未被 git 跟踪、也不在暂存区**的文件；
4. 同时报告"已被跟踪但工作区有未暂存改动"的 P0 关键文件（可选，--changed）。

用法::

    python scripts/check_delivery_completeness.py            # 只看未跟踪缺失
    python scripts/check_delivery_completeness.py --changed  # 连未暂存改动一起看
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"

#: 从暂存的后端文件出发，解析这些顶层包
SCOPE_PREFIXES = ("backend/app/", "backend/alembic/", "backend/scripts/", "backend/tests/")

#: 基线既有缺口（**显式登记，不静默忽略**）。
#:
#: 这些文件在本次交付之前就从未被 git 跟踪，且**不由本次交付的文件引用**——
#: 它们由基线已跟踪文件（如 `api_test_service.py`）引用。属"仓库未跟踪文件治理"
#: 范畴（见交付说明第九节技术债），不在本次交付范围内。
#: 任一新增缺口都会让本检查失败；只有下表内条目被豁免。
BASELINE_GAPS: dict[str, str] = {
    "backend/app/services/api_spec_test_generator.py":
        "基线未跟踪；由基线文件 api_test_service.py 引用",
    "backend/app/services/artifact_publisher.py":
        "基线未跟踪；由基线文件 execution_preflight.py 引用",
    "backend/app/services/execution_preflight.py":
        "基线未跟踪；由基线文件 unified_execution.py 引用",
    "backend/app/services/test_run_artifact_service.py":
        "基线未跟踪；由基线文件 api_test_service.py 引用（本次已改为 naive UTC，仍待纳入提交）",
    "backend/app/services/unified_execution.py":
        "基线未跟踪；由基线文件 api_test_service.py 引用",
    "backend/app/agents/api/tools/intent_tools.py":
        "基线未跟踪；由基线文件 api/v2/api_tests.py 引用（平台层，非本次交付）",
    "backend/app/schemas/intent.py":
        "基线未跟踪；由基线文件 api/v2/api_tests.py 引用（平台层，非本次交付）",
}

IMPORT_RE = re.compile(
    r"^\s*(?:from\s+([A-Za-z_][\w.]*)\s+import\s+([^\n#]+)|import\s+([A-Za-z_][\w.]*))",
    re.MULTILINE,
)


def git(*args: str) -> set[str]:
    result = subprocess.run(
        ["git", *args], cwd=str(ROOT), capture_output=True, text=True,
        encoding="utf-8", errors="replace",
    )
    return {line.strip() for line in (result.stdout or "").splitlines() if line.strip()}


def module_to_candidates(module: str) -> list[Path]:
    """`app.services.foo` → 可能的文件路径（**绝对路径**）。"""
    if not module.startswith("app"):
        return []
    parts = module.split(".")
    base = BACKEND.joinpath(*parts)
    return [base.with_suffix(".py"), base / "__init__.py"]


def rel_if_inside(path: Path) -> str | None:
    """把绝对路径转成仓库相对 posix 路径；不在仓库内则返回 None。

    注意：``module_to_candidates`` 返回绝对路径，而 ``git ls-files`` 输出相对路径，
    早期版本直接比较两者导致所有依赖都被误判为"未跟踪"（实测把 0 缺口报成 110）。
    """
    try:
        return path.resolve().relative_to(ROOT.resolve()).as_posix()
    except ValueError:
        return None


def extract_imports(text: str) -> set[str]:
    modules: set[str] = set()
    for from_mod, names, plain in IMPORT_RE.findall(text):
        if plain:
            modules.add(plain)
        if from_mod:
            modules.add(from_mod)
            # `from app.services import foo` → 也可能是 app.services.foo
            head = from_mod.split(".")[0]
            if head in {"app", "tests", "alembic", "scripts"}:
                for name in names.split(","):
                    name = name.strip().split(" as ")[0].strip()
                    if name and name.isidentifier():
                        modules.add(f"{from_mod}.{name}")
    return modules


def main() -> int:
    parser = argparse.ArgumentParser(description="交付完整性检查")
    parser.add_argument("--head", action="store_true",
                        help="以 HEAD 提交快照为种子（校验提交后是否可运行）")
    parser.add_argument("--changed", action="store_true",
                        help="同时列出被跟踪但有未暂存改动的文件")
    args = parser.parse_args()

    staged = {p.replace("\\", "/") for p in git("diff", "--cached", "--name-only")}
    tracked = {p.replace("\\", "/") for p in git("ls-files")}

    if args.head:
        # 必须用 ls-tree 取**整个提交树**：`git show --name-only HEAD` 只列出该次提交
        # 改动的文件，用它做种子会把"上一提交引入、本次未改动"的文件全部漏掉。
        committed = git("ls-tree", "-r", "--name-only", "HEAD")
        seeds = sorted(
            p for p in committed
            if p.startswith(SCOPE_PREFIXES) and not p.startswith("\ufeff")
        )
        scope_label = "HEAD 提交快照"
    else:
        seeds = sorted(p for p in staged if p.startswith(SCOPE_PREFIXES))
        scope_label = "暂存区"

    if not seeds:
        print(f"{scope_label}没有后端文件，跳过完整性检查")
        return 0

    seen: set[str] = set()
    queue = list(seeds)
    #: module -> 首次被谁引用
    referenced: dict[str, str] = {}
    missing: dict[str, str] = {}

    while queue:
        rel = queue.pop()
        if rel in seen:
            continue
        seen.add(rel)
        path = ROOT / rel
        if not path.exists():
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue

        for module in extract_imports(text):
            candidates = module_to_candidates(module)
            if not candidates:
                continue
            hit = next((c for c in candidates if c.exists()), None)
            if hit is None:
                continue
            rel_hit = rel_if_inside(hit)
            if rel_hit is None:
                continue
            referenced.setdefault(rel_hit, rel)
            if rel_hit not in seen:
                queue.append(rel_hit)
            # 判定：文件存在但从未被跟踪、也不在暂存区
            if rel_hit not in tracked and rel_hit not in staged:
                missing.setdefault(rel_hit, rel)

    print(f"范围: {scope_label}")
    print(f"种子后端文件: {len(seeds)}")
    print(f"沿 import 解析到的文件: {len(seen)}")
    print(f"未被跟踪且未暂存的依赖: {len(missing)}")

    exempt = {k: v for k, v in missing.items() if k in BASELINE_GAPS}
    fresh = {k: v for k, v in missing.items() if k not in BASELINE_GAPS}

    for rel_hit, by in sorted(exempt.items()):
        print(f"  [基线豁免] {rel_hit}   <- 由 {by} 引用；{BASELINE_GAPS[rel_hit]}")
    for rel_hit, by in sorted(fresh.items()):
        print(f"  [缺失] {rel_hit}   <- 由 {by} 引用")

    changed: list[str] = []
    if args.changed:
        changed = sorted(
            p for p in git("diff", "--name-only")
            if p.startswith(SCOPE_PREFIXES)
        )
        if changed:
            print()
            print(f"被跟踪但有未暂存改动（{len(changed)}）—— 这些改动**不会**进入提交：")
            for rel in changed:
                print(f"  [未暂存] {rel}")

    problems = len(fresh) + (len(changed) if args.changed else 0)
    print()
    print(f"基线豁免 {len(exempt)} 项（已登记，见 BASELINE_GAPS）、新增缺口 {len(fresh)} 项")
    if problems:
        print("结果: 存在新增完整性缺口，提交快照可能无法在干净检出后运行")
        return 1
    print("结果: 通过（无新增缺口；基线缺口均已登记）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
