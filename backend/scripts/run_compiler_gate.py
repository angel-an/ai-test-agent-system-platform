# -*- coding: utf-8 -*-
"""跑编译器门禁集（改 ``web_case_compiler`` 时的最小必跑集）。

用法::

    cd backend
    ..\\.venv\\Scripts\\python.exe -X utf8 scripts\\run_compiler_gate.py            # 跑一遍
    ..\\.venv\\Scripts\\python.exe -X utf8 scripts\\run_compiler_gate.py --list      # 只看范围
    ..\\.venv\\Scripts\\python.exe -X utf8 scripts\\run_compiler_gate.py --bisect    # 逐文件定位

为什么要有 ``--bisect``
-----------------------
2026-10-23 实测：全量 2830 条 ≈ 60 分钟（1.27s/条），跑一次「17 分钟后没结束」被误判成挂死，
实际上是体量。真需要定位时，整批跑 + subprocess 超时 + 自动退化逐文件，比人工二分快得多
（本次用它 160 秒定位到具体用例，发现其实只是慢，不是死锁）。

退出码：0 = 全绿；1 = 有失败；2 = 清单/环境问题（如发现集为空）。
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
import time
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
PY = sys.executable

if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from tests.compiler_gate_manifest import (  # noqa: E402
    INTEGRATION_EXCLUDED,
    SPEED_BUDGET_SECONDS,
    discover,
)


def _pytest_cmd(targets: list[str]) -> list[str]:
    return [PY, "-X", "utf8", "-m", "pytest", *targets,
            "-q", "-p", "no:cacheprovider", "--tb=line"]


def _run(targets: list[str], timeout: float) -> tuple[str, float, str]:
    """跑一次 pytest；超时则整批 kill 并返回 timed_out 标记。"""
    started = time.time()
    try:
        proc = subprocess.run(
            _pytest_cmd(targets), cwd=str(BACKEND),
            capture_output=True, timeout=timeout,
        )
        text = (proc.stdout or b"").decode("utf-8", errors="replace") + \
            (proc.stderr or b"").decode("utf-8", errors="replace")
        return text, time.time() - started, ""
    except subprocess.TimeoutExpired as exc:
        out = (exc.stdout or b"") if isinstance(exc.stdout, bytes) else b""
        partial = out.decode("utf-8", errors="replace")
        return partial, time.time() - started, f"超时（>{timeout:.0f}s）"


def _parse(text: str) -> dict[str, int]:
    counts: dict[str, int] = {}
    for key, pattern in (
        ("passed", r"(\d+) passed"),
        ("failed", r"(\d+) failed"),
        ("error", r"(\d+) errors?"),
        ("skipped", r"(\d+) skipped"),
    ):
        m = re.search(pattern, text)
        if m:
            counts[key] = int(m.group(1))
    return counts


def _bisect(targets: list[str], per_file_timeout: float) -> int:
    """逐文件跑，打印耗时表，返回第一个超时文件的索引（找不到返回 -1）。"""
    print(f"逐文件定位（{len(targets)} 个文件，单个上限 {per_file_timeout:.0f}s）…")
    slow: list[tuple[str, float, str]] = []
    stuck: list[str] = []
    for rel in targets:
        text, secs, note = _run([rel], per_file_timeout)
        counts = _parse(text)
        if note:
            stuck.append(rel)
            status = "TIMEOUT"
        elif counts.get("failed") or counts.get("error"):
            status = f"FAILED(failed={counts.get('failed', 0)},err={counts.get('error', 0)})"
        else:
            status = f"ok({counts.get('passed', 0)})"
        print(f"  {secs:6.1f}s  {status:28s} {rel}")
        slow.append((rel, secs, status))

    print()
    print("最慢 5 个：")
    for rel, secs, status in sorted(slow, key=lambda x: -x[1])[:5]:
        print(f"  {secs:6.1f}s  {status:28s} {rel}")

    if stuck:
        print()
        print("卡住（超时）的文件：")
        for rel in stuck:
            print(f"  - {rel}")
        return 1
    bad = [r for r, _s, st in slow if st.startswith("FAILED")]
    if bad:
        print()
        print("失败文件：")
        for rel in bad:
            print(f"  - {rel}")
        return 1
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="跑编译器门禁集")
    parser.add_argument("--list", action="store_true", help="只打印范围，不执行")
    parser.add_argument("--bisect", action="store_true", help="逐文件跑并给出耗时表")
    parser.add_argument("--timeout", type=float, default=SPEED_BUDGET_SECONDS * 2,
                        help="整批超时秒数（超时自动退化逐文件）")
    parser.add_argument("--per-file-timeout", type=float, default=120.0)
    args = parser.parse_args(argv)

    targets = discover()
    if not targets:
        print("发现集为空：compiler_gate_manifest.DEPENDENCY_MARKERS 可能失效了", file=sys.stderr)
        return 2

    print(f"编译器门禁集：{len(targets)} 个文件"
          f"（已排除 {len(INTEGRATION_EXCLUDED)} 个集成/环境依赖文件）")
    if args.list:
        for rel in targets:
            print(f"  {rel}")
        print()
        print("排除项：")
        for rel, why in INTEGRATION_EXCLUDED.items():
            print(f"  - {rel} —— {why}")
        return 0

    if args.bisect:
        return _bisect(targets, args.per_file_timeout)

    text, secs, note = _run(targets, args.timeout)
    counts = _parse(text)
    print()
    if note:
        print(f"整批{note}。退化逐文件定位：")
        return _bisect(targets, args.per_file_timeout)

    summary = "，".join(f"{k}={v}" for k, v in counts.items()) or "(无汇总行)"
    print(f"耗时 {secs:.1f}s  |  {summary}")
    if secs > SPEED_BUDGET_SECONDS:
        print(f"⚠️ 超过速度预算 {SPEED_BUDGET_SECONDS}s —— 门禁太慢就会被跳过，考虑拆分或查慢点（--bisect）")

    if counts.get("failed") or counts.get("error"):
        print()
        tail = [l for l in text.splitlines() if l.startswith("FAILED")]
        for line in tail[:20]:
            print("  " + line)
        return 1
    if "passed" not in text:
        print("未解析到 passed 汇总行，输出尾部：")
        print("\n".join(text.strip().splitlines()[-10:]), file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
