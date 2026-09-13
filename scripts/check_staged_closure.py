# -*- coding: utf-8 -*-
"""校验暂存集合的依赖闭包自洽性。

检查暂存的前端文件所 import 的 `@/...` 别名模块，是否都能解析到
「已暂存」或「已被 git 跟踪」的文件；否则提交后该文件将引用不存在的模块。
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
UI = ROOT / "ui"

IMPORT_RE = re.compile(r"""from\s+["'](@/[^"']+)["']""")


def git(*args: str) -> set[str]:
    result = subprocess.run(
        ["git", *args], cwd=str(ROOT), capture_output=True, text=True,
        encoding="utf-8", errors="replace",
    )
    return {line.strip() for line in (result.stdout or "").splitlines() if line.strip()}


def resolve(alias: str) -> list[Path]:
    """把 @/x/y 解析为 ui/ 下可能的真实文件路径。"""
    base = UI / alias[2:]
    candidates = [base]
    for ext in (".ts", ".tsx", ".js", ".jsx"):
        candidates.append(base.with_suffix(ext))
    for ext in (".ts", ".tsx"):
        candidates.append(base / f"index{ext}")
    return candidates


def main() -> int:
    staged = git("diff", "--cached", "--name-only")
    tracked = git("ls-files")
    known = {p.replace("\\", "/") for p in (staged | tracked)}

    frontend = sorted(p for p in staged if p.startswith("ui/") and p.endswith((".ts", ".tsx")))
    print(f"暂存的前端文件: {len(frontend)}")

    unresolved: list[tuple[str, str]] = []
    for rel in frontend:
        text = (ROOT / rel).read_text(encoding="utf-8", errors="replace")
        for alias in sorted(set(IMPORT_RE.findall(text))):
            paths = resolve(alias)
            hit = next((p for p in paths if p.exists()), None)
            if hit is None:
                continue  # 别名指向未生成文件（如 shadcn 组件），跳过
            rel_hit = hit.relative_to(ROOT).as_posix()
            if rel_hit not in known:
                unresolved.append((rel, f"{alias} -> {rel_hit}"))

    if unresolved:
        print()
        print("以下 import 目标既未暂存、也未被跟踪（提交后将成为断链）:")
        for src, target in unresolved:
            print(f"  - {src}\n      {target}")
        return 1

    print("闭包自洽: 暂存前端文件的所有本地 import 都已暂存或被跟踪。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
