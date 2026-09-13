# -*- coding: utf-8 -*-
"""交付前最终校验：对暂存集合做编码、闭包、gitignore 生效性检查。

运行：python scripts/final_delivery_check.py
退出码 0 表示全部通过。
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

# 说明文档中会举例写出 mojibake 标记，需排除该行避免自我误报
DOC_EXAMPLE_MARKERS = ("常见 mojibake 前缀",)

# 本脚本自身与文档会书写 mojibake 字面量用于检测/说明，需排除
SELF_EXCLUDED = (
    "scripts/final_delivery_check.py",
)

TEXT_EXTS = (".ts", ".tsx", ".js", ".jsx", ".mjs", ".py", ".md", ".json", ".yaml")
MOJIBAKE = ("锟斤拷", "\u00c3\u00a5", "\u00ef\u00bb\u00bf")


def git(*args: str) -> str:
    result = subprocess.run(
        ["git", *args], cwd=str(ROOT), capture_output=True, text=True,
        encoding="utf-8", errors="replace",
    )
    return result.stdout or ""


def staged_files() -> list[str]:
    out = subprocess.run(
        ["git", "diff", "--cached", "--name-only", "-z"], cwd=str(ROOT),
        capture_output=True,
    ).stdout.decode("utf-8")
    return [p for p in out.split("\x00") if p.strip()]


def check_encoding(files: list[str]) -> list[str]:
    problems: list[str] = []
    checked = 0
    for rel in files:
        path = ROOT / rel
        if not path.is_file():
            continue
        if rel.replace("\\", "/") in SELF_EXCLUDED:
            continue
        if not (rel.endswith(TEXT_EXTS) or path.name == ".gitignore"):
            continue
        checked += 1
        try:
            text = path.read_bytes().decode("utf-8")
        except UnicodeDecodeError as exc:
            problems.append(f"{rel}: 非 UTF-8 ({exc})")
            continue
        if "\ufffd" in text:
            problems.append(f"{rel}: 含替换符 U+FFFD")
        for lineno, line in enumerate(text.splitlines(), 1):
            if any(marker in line for marker in DOC_EXAMPLE_MARKERS):
                continue
            if any(token in line for token in MOJIBAKE):
                problems.append(f"{rel}:{lineno}: 疑似 mojibake")
            if "\u00c3" in line or "\u00c2" in line:
                problems.append(f"{rel}:{lineno}: 疑似 mojibake 前缀")
    print(f"[编码] 检查 {checked} 个文本文件，问题 {len(problems)}")
    return problems


def check_closure() -> list[str]:
    result = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "check_staged_closure.py")],
        cwd=str(ROOT), capture_output=True, text=True, encoding="utf-8", errors="replace",
    )
    ok = result.returncode == 0
    print(f"[闭包] {'自洽' if ok else '存在断链引用'}")
    if ok:
        return []
    return [line for line in (result.stdout or "").splitlines() if line.strip()]


def check_ignore_rules() -> list[str]:
    """ui/lib 源码必须可跟踪；构建产物/依赖/虚拟环境必须仍被忽略。"""
    problems: list[str] = []

    def ignore_of(target: str) -> str:
        return git("check-ignore", "-v", "--no-index", target).strip()

    must_be_trackable = [
        "ui/lib/api/ai.ts",
        "ui/lib/api/requirement-quality.ts",
        "ui/lib/utils.ts",
        "ui/components/ui/button.tsx",
    ]
    for target in must_be_trackable:
        if ignore_of(target):
            problems.append(f"{target} 仍被忽略（应可跟踪）")

    must_be_ignored = [
        ("ui/.next/build-manifest.json", ".next"),
        ("ui/node_modules", "node_modules"),
        (".venv/Lib/site-packages/Crypto/Cipher/AES.py", ".venv"),
    ]
    for target, expect in must_be_ignored:
        rule = ignore_of(target)
        if not rule:
            problems.append(f"{target} 未被忽略（应保持忽略，期望规则 {expect}）")

    print(f"[ignore] 可跟踪项 {len(must_be_trackable)}，应忽略项 {len(must_be_ignored)}，问题 {len(problems)}")
    return problems


def main() -> int:
    files = staged_files()
    print(f"[暂存] 条目数 {len(files)}")
    print()

    problems: list[str] = []
    problems += check_encoding(files)
    problems += check_closure()
    problems += check_ignore_rules()

    print()
    diff_check = subprocess.run(
        ["git", "diff", "--cached", "--check"], cwd=str(ROOT),
        capture_output=True, text=True, encoding="utf-8", errors="replace",
    )
    if diff_check.returncode == 0:
        print("[diff] git diff --cached --check 通过")
    else:
        print("[diff] git diff --cached --check 未通过")
        problems.append((diff_check.stdout or "").strip())

    print()
    if problems:
        print(f"结果: 存在 {len(problems)} 个问题")
        for item in problems:
            print("  -", item)
        return 1

    print("结果: 全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
