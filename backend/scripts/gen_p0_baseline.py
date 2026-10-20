# -*- coding: utf-8 -*-
"""生成唯一 P0 测试基线（命令 + 数字 + commit）。

读取 `backend/tests/p0_baseline_manifest.py` 的清单，执行固定命令，
把结果写入仓库根 `P0-测试基线.md`。**数字由实际执行决定，不写死**。

用法::

    cd backend
    ..\\.venv\\Scripts\\python.exe -X utf8 scripts\\gen_p0_baseline.py --write
"""

from __future__ import annotations

import argparse
import subprocess
import sys
import re
from datetime import datetime, timezone
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
REPO = BACKEND.parent
PY = sys.executable


def _load_manifest():
    sys.path.insert(0, str(BACKEND))
    from tests.p0_baseline_manifest import (  # noqa: PLC0415
        CLONE_SUBSET,
        P0_ROOT_FILES,
        P0_ROOTS,
        P1_OWNED_TESTS,
    )

    return P0_ROOTS, P0_ROOT_FILES, P1_OWNED_TESTS, CLONE_SUBSET


def _git(*args: str) -> str:
    out = subprocess.run(["git", *args], cwd=str(REPO), capture_output=True)
    return (out.stdout or b"").decode("utf-8", errors="replace").strip()


def _run_pytest(targets: list[str]) -> tuple[int, str]:
    cmd = [PY, "-X", "utf8", "-m", "pytest", *targets, "-q", "-p", "no:cacheprovider"]
    out = subprocess.run(cmd, cwd=str(BACKEND), capture_output=True)
    text = (out.stdout or b"").decode("utf-8", errors="replace") + \
        (out.stderr or b"").decode("utf-8", errors="replace")
    return out.returncode, text


def _parse_counts(text: str) -> dict[str, int]:
    """从 pytest 汇总行解析 passed/failed/error/skipped。"""
    counts: dict[str, int] = {}
    match = re.search(r"(\d+) passed", text)
    if match:
        counts["passed"] = int(match.group(1))
    for key in ("failed", "error", "errors", "skipped"):
        m = re.search(rf"(\d+) {key}\b", text)
        if m:
            normalised = "error" if key == "errors" else key
            counts[normalised] = int(m.group(1))
    return counts


def main() -> int:
    parser = argparse.ArgumentParser(description="生成 P0 测试基线")
    parser.add_argument("--write", action="store_true")
    parser.add_argument(
        "--allow-skip",
        action="store_true",
        help="允许存在 skipped 用例（默认禁止：跳过会掩盖样本路径漂移等问题）",
    )
    args = parser.parse_args()

    p0_roots, p0_root_files, p1_owned, clone_subset = _load_manifest()

    targets = [*p0_roots, *p0_root_files]
    # P1 的测试文件**不在 P0 覆盖列表内**；若被误加入，下面的校验会立即失败。
    for rel in p1_owned:
        if rel in targets:
            raise SystemExit(
                f"清单错误：P1 归属的测试 {rel} 不应出现在 P0 覆盖范围内"
            )

    # 但 `tests/platform`、`tests/api` 是**目录级目标**，pytest 会收集目录下所有现存文件。
    # 工作区里 P1 文件未被跟踪却真实存在于磁盘 → 必须显式 --ignore，
    # 否则 P1 用例会混进 P0 数字（实测：738 = 719 + 19 个 P1 用例）。
    # 仅在文件存在时加 --ignore，干净检出（P1 不在）时不产生噪声。
    exclude_args = [
        f"--ignore={rel}" for rel in p1_owned if (BACKEND / rel).exists()
    ]

    command = " ".join(
        ["..\\.venv\\Scripts\\python.exe", "-X", "utf8", "-m", "pytest", *targets,
         "-q", "-p", "no:cacheprovider", *exclude_args]
    )

    print(f"执行 P0 基线（{len(targets)} 个目标；P1 归属测试 {len(p1_owned)} 个已登记且不在范围内）…")
    code, output = _run_pytest([*targets, *exclude_args])
    counts = _parse_counts(output)
    tail = [l for l in output.strip().splitlines() if "passed" in l or "failed" in l]

    print(f"退出码: {code}")
    print("汇总:", tail[-1] if tail else "(无汇总行)")
    print("解析:", counts)

    # 跳过门禁：skipped 会掩盖"样本路径写错"这类真实问题
    # （本仓库已发生两次：test_excel_api_parser.py、test_api_spec_generator.py）
    skipped = counts.get("skipped", 0)
    if skipped and not args.allow_skip:
        print()
        print(f"结果: 存在 {skipped} 个 skipped 用例 —— 基线不成立。")
        print("      跳过通常意味着样本路径/前置条件缺失，请修正或显式加 --allow-skip。")
        for line in output.splitlines():
            if line.strip().startswith("SKIPPED"):
                print("      " + line.strip())
        return 1

    head = _git("rev-parse", "HEAD")[:12]
    branch = _git("rev-parse", "--abbrev-ref", "HEAD")

    lines = [
        "# P0 测试基线（唯一口径）",
        "",
        f"生成时间（UTC）：{datetime.now(timezone.utc).isoformat(timespec='seconds')}",
        "",
        "## 唯一命令（逐字复制可复现）",
        "",
        "```bash",
        "cd backend",
        command,
        "```",
        "",
        "## 本次执行结果",
        "",
        f"- 分支：`{branch}`",
        f"- 提交：`{head}`",
        f"- 退出码：`{code}`",
        f"- **passed：{counts.get('passed', '未知')}**",
        f"- failed：{counts.get('failed', 0)}，error：{counts.get('error', 0)}，"
        f"skipped：{counts.get('skipped', 0)}",
        "",
        "## 覆盖范围（由 `backend/tests/p0_baseline_manifest.py` 定义）",
        "",
        "包含：",
        "",
        *[f"- `{t}`" for t in targets],
        "",
        "**不在 P0 范围（P1 归属，已登记）**：",
        "",
        *[f"- `{p}` —— {p1_owned[p]}" for p in p1_owned],
        "",
        "## 口径区分（不得相加或互相替代）",
        "",
        "| 口径 | 含义 |",
        "| --- | --- |",
        "| **P0 基线**（本文件） | 上表命令的 passed 数，唯一权威 P0 数字 |",
        f"| clean clone 核心子集 | {'、'.join(f'`{c}`' for c in clone_subset)} 在仅检出提交内容的目录内执行，"
        "用于证明提交快照可运行 |",
        "| P1 专项 | `tests/api/test_contract_driven_p1.py`，P1 单独验收后另立基线 |",
        "",
        "> 历史数字（638 / 642 / 649）来自不同轮次与不同命令，**已作废**，引用时请使用本文件的数字。",
        "",
    ]

    content = "\n".join(lines)
    if args.write:
        target = REPO / "P0-测试基线.md"
        target.write_text(content, encoding="utf-8")
        print(f"已写入 {target}")
    else:
        sys.stdout.write(content)
    return 0 if code == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
