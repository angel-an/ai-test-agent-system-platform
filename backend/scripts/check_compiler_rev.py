#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""编译器版本守卫（CI / pre-commit）。

为什么需要这个脚本：``COMPILER_REV`` 是人手写的字符串，靠 code review 记住"改模板要 bump"
是不可靠的 —— 漏改之后旧产物会被当成新产物，执行期完全看不出异常（TR-703 就是这样
静默烧掉 1745 秒）。这里把「忘 bump」变成**非零退出**，钩子和流水线都会挡住。

用法::

    python backend/scripts/check_compiler_rev.py            # 校验，不一致 → 退出码 1
    python backend/scripts/check_compiler_rev.py --quiet    # 只在失败时输出
    python backend/scripts/check_compiler_rev.py --update   # 把当前 (rev,digest) 登记进 golden

``--update`` 不是"让检查通过"的开关：它要求 ``COMPILER_REV`` 相对末条记录**确实变了**，
否则拒绝写入 —— 也就是说，想更新登记表，必须真的 bump 版本号。
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import date
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[1]
REGISTRY = BACKEND_DIR / "tests" / "golden" / "compiler_rev_registry.json"

sys.path.insert(0, str(BACKEND_DIR))

from app.services.web_case_compiler import COMPILER_REV, compiler_source_digest  # noqa: E402


def _rev_number(rev: str) -> int:
    match = re.fullmatch(r"rev(\d+)", rev or "")
    return int(match.group(1)) if match else -1


def _load() -> dict:
    if not REGISTRY.exists():
        return {"note": "", "entries": []}
    return json.loads(REGISTRY.read_text(encoding="utf-8"))


def _save(data: dict) -> None:
    REGISTRY.parent.mkdir(parents=True, exist_ok=True)
    REGISTRY.write_text(
        json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )


def check(data: dict, digest: str) -> tuple[bool, str]:
    entries = data.get("entries") or []
    if not entries:
        return False, "登记表为空：先执行 --update 登记首个版本"

    last = entries[-1]
    if last.get("rev") != COMPILER_REV:
        return False, (
            f"COMPILER_REV={COMPILER_REV} 与登记表末条 rev={last.get('rev')} 不一致。\n"
            "  若本次确实改了执行器模板/编译逻辑：追加一条登记（--update）。\n"
            "  若是 rebase/合并把 rev 覆盖回去了：恢复成更大的版本号。"
        )
    if last.get("digest") != digest:
        return False, (
            "执行器模板或编译逻辑已变更，当前 digest="
            f"{digest}，但 {COMPILER_REV} 登记的是 {last.get('digest')}。\n"
            "  ⇒ 这是「改了代码却没 bump COMPILER_REV」：\n"
            "     1) 把 web_case_compiler.COMPILER_REV 改成更大的版本号（如 rev119）；\n"
            "     2) 重新生成受影响用例的脚本（旧产物会被执行期判为 stale）；\n"
            "     3) python backend/scripts/check_compiler_rev.py --update"
        )

    seen: dict[str, str] = {}
    for entry in entries:
        rev, dig = entry.get("rev"), entry.get("digest")
        if dig in seen:
            return False, f"登记表内 digest 重复：{dig} 同时出现在 {seen[dig]} 与 {rev}"
        seen[dig] = rev
    for prev, cur in zip(entries, entries[1:]):
        if _rev_number(cur.get("rev")) <= _rev_number(prev.get("rev")):
            return False, f"版本号未严格递增：{prev.get('rev')} → {cur.get('rev')}"
    return True, ""


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="校验编译器版本指纹是否已 bump 并登记")
    parser.add_argument("--update", action="store_true", help="将当前 rev/digest 追加进登记表")
    parser.add_argument("--quiet", action="store_true", help="成功时不输出")
    args = parser.parse_args(argv)

    digest = compiler_source_digest()
    data = _load()
    entries = data.setdefault("entries", [])

    if args.update:
        if entries and entries[-1].get("rev") == COMPILER_REV:
            print(
                f"拒绝更新：COMPILER_REV 仍是 {COMPILER_REV}，没有 bump。\n"
                "  想登记新的 digest 必须先改版本号 —— 否则登记表就失去意义了。",
                file=sys.stderr,
            )
            return 1
        if entries and _rev_number(COMPILER_REV) <= _rev_number(entries[-1].get("rev")):
            print(
                f"拒绝更新：{COMPILER_REV} 未大于末条 {entries[-1].get('rev')}",
                file=sys.stderr,
            )
            return 1
        entries.append(
            {
                "rev": COMPILER_REV,
                "digest": digest,
                "date": date.today().isoformat(),
                "summary": "",
            }
        )
        _save(data)
        print(f"已登记 {COMPILER_REV} / {digest}（summary 请手工补一句改动说明）")
        return 0

    ok, message = check(data, digest)
    if not ok:
        print(f"[compiler-rev] 校验失败\n{message}", file=sys.stderr)
        return 1
    if not args.quiet:
        print(f"[compiler-rev] OK {COMPILER_REV} / {digest}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
