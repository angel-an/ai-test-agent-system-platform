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

#: 额外必须扫描的目录（**新增但未被跟踪的文件**，其 import 关系无法从已跟踪文件推导）。
#:
#: 盲区成因：本检查器沿 import 图遍历，起点是"提交/暂存里的文件"。
#: 一个**从未被跟踪、又不被任何已跟踪文件 import** 的文件（如顶层新增测试文件）
#: 永远不会出现在图里，因此不会被发现。
#: 实测事故：`tests/test_api_asset_models.py`、`tests/test_postman_parsers.py`
#: 未跟踪，却被 `p0_baseline_manifest.py` 列为基线目标 →
#: clean clone 里基线命令直接 exit 4（usage error）。
EXTRA_SCAN_DIRS = ("backend/tests/",)

#: 清单类文件：其字符串字面量引用会被当作"必须存在"的依赖来检查。
MANIFEST_FILES = ("backend/tests/p0_baseline_manifest.py",)

#: 基线既有缺口（**显式登记，不静默忽略**）。
#:
#: 历史：P0 交付期间此处登记过 7 项（`api_spec_test_generator.py`、
#: `artifact_publisher.py`、`execution_preflight.py`、`test_run_artifact_service.py`、
#: `unified_execution.py`、`intent_tools.py`、`schemas/intent.py`），全部由基线已跟踪
#: 文件引用且从未被 git 跟踪，会让 clean clone 无法 import。
#: **现已全部纳入版本控制**（提交 `d29c945` / `02456b3`），故清单清空。
#:
#: 维护约定：新增缺口会让本检查失败；确需豁免时必须在此登记路径、归属与原因，
#: 并在相关文件被纳入版本控制后**立即移除**（检查器会报告"豁免过期"）。见 `stale` 检测。
BASELINE_GAPS: dict[str, str] = {}

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

    # --- 盲区补丁：清单类文件里以字符串字面量引用的文件必须存在**且已被跟踪** ---
    # （如 p0_baseline_manifest.py 列出的测试目标；它们不被 import，
    #   因此不进入 import 图，但缺了会让基线命令直接失败）
    #
    # 注意：不做"目录下未跟踪文件"的泛扫描 —— 其它工作流的未跟踪测试
    # 不属于本次交付，泛扫描会产生大量假阳性。本检查只认**显式引用**。
    # 清单登记为 P1 归属的文件**故意未纳入版本控制**（等待 P1 单独提交），
    # 因此不算缺口。这里**直接读取清单模块**而不是文本解析 ——
    # 文本解析曾把注释里的路径也算进去，产生假阳性。
    p1_registered: set[str] = set()
    try:
        import importlib.util

        manifest_path = ROOT / "backend" / "tests" / "p0_baseline_manifest.py"
        if manifest_path.exists():
            spec = importlib.util.spec_from_file_location("_p0_manifest", manifest_path)
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            p1_registered = {str(p) for p in getattr(module, "P1_OWNED_TESTS", {})}
    except Exception as exc:  # noqa: BLE001
        print(f"  [警告] 读取 P0 清单失败，P1 登记集合按空处理: {type(exc).__name__}")

    for rel in MANIFEST_FILES:
        manifest = ROOT / rel
        if not manifest.exists():
            continue
        text = manifest.read_text(encoding="utf-8", errors="replace")
        for quoted in re.findall(r'["\'](tests/[\w./-]+\.py)["\']', text):
            if quoted in p1_registered:
                continue
            target = f"backend/{quoted}"
            if not (ROOT / target).exists():
                missing.setdefault(target, f"{rel}（清单引用，文件不存在）")
            elif target not in tracked and target not in staged:
                missing.setdefault(target, f"{rel}（清单引用但未被跟踪）")

    while queue:
        rel = queue.pop()
        if rel in seen:
            continue
        seen.add(rel)
        path = ROOT / rel
        if not path.exists():
            continue
        # 只遍历**已提交或已暂存**的源文件：
        # 工作区里可能还有未纳入本次交付的在途文件（如 P1 的 WIP 模块），
        # 它们的依赖不属于"提交快照"的完整性责任，不应阻塞本校验。
        if rel not in tracked and rel not in staged:
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

    # 豁免清单漂移检测：登记项已不再缺口 → 必须从豁免表移除，否则豁免表会长期失维
    # （基线文件被纳入版本控制、或引用方被移除后，旧豁免会掩盖新问题）。
    stale = sorted(
        path for path in BASELINE_GAPS
        if path not in missing
    )

    for rel_hit, by in sorted(exempt.items()):
        print(f"  [基线豁免] {rel_hit}   <- 由 {by} 引用；{BASELINE_GAPS[rel_hit]}")
    for rel_hit, by in sorted(fresh.items()):
        print(f"  [缺失] {rel_hit}   <- 由 {by} 引用")
    for path in stale:
        print(f"  [豁免过期] {path}   —— 已不再缺口，请从 BASELINE_GAPS 移除该条")

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

    problems = len(fresh) + len(stale) + (len(changed) if args.changed else 0)
    print()
    print(
        f"基线豁免 {len(exempt)} 项（已登记，见 BASELINE_GAPS）、"
        f"新增缺口 {len(fresh)} 项、豁免过期 {len(stale)} 项"
    )
    if problems:
        print("结果: 存在新增缺口或豁免过期，提交快照完整性未通过")
        return 1
    print("结果: 通过（无新增缺口；基线缺口均已登记且未过期）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
