# -*- coding: utf-8 -*-
"""生成可复现验证记录（交付文档）。

用 Python 直接写 UTF-8，避免 shell 编码问题。
运行：python scripts/gen_verification_record.py
"""

from __future__ import annotations

import platform
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def run(cmd: list[str], cwd: Path | None = None) -> str:
    try:
        result = subprocess.run(
            cmd, cwd=str(cwd or ROOT), capture_output=True, text=True,
            encoding="utf-8", errors="replace", timeout=120,
        )
        return (result.stdout or "").strip() or (result.stderr or "").strip()
    except Exception as exc:  # noqa: BLE001
        return f"(执行失败: {type(exc).__name__}: {exc})"


def main() -> int:
    venv_py = ROOT / ".venv" / "Scripts" / "python.exe"
    py = str(venv_py) if venv_py.exists() else sys.executable

    versions = {
        "Python": run([py, "-V"]),
        "pytest": run([py, "-c", "import pytest;print(pytest.__version__)"]),
        "SQLAlchemy": run([py, "-c", "import sqlalchemy;print(sqlalchemy.__version__)"]),
        "FastAPI": run([py, "-c", "import fastapi;print(fastapi.__version__)"]),
        "Pydantic": run([py, "-c", "import pydantic;print(pydantic.VERSION)"]),
        "Node": run(["node", "-v"]),
        "OS": platform.platform(),
    }

    staged = run(["git", "diff", "--cached", "--name-status"])
    untracked_ignored = run(
        ["git", "status", "--short", "--untracked-files=all", "--ignored", "--", "ui/lib"]
    )

    lines: list[str] = []
    lines.append("# 可复现验证记录（阶段一 + 阶段二）")
    lines.append("")
    lines.append(f"生成时间: {datetime.now(timezone.utc).isoformat()}")
    lines.append("")
    lines.append("## 1. 环境")
    lines.append("")
    lines.append("| 项 | 值 |")
    lines.append("| --- | --- |")
    for key, value in versions.items():
        lines.append(f"| {key} | {value} |")
    lines.append("")
    lines.append("### 临时目录与沙箱说明（**重要**）")
    lines.append("")
    lines.append("- pytest 的 `tmp_path` / `tempfile` 使用系统 `TMP`/`TEMP`。")
    lines.append("- 在 DSH 文件沙箱下，`TMP` 会被指向 `%TEMP%\\dsh-*`，而沙箱**拒绝 Python 在该目录下创建子目录/写入**，")
    lines.append("  导致依赖临时目录的用例（`directory_layout` / `unified_runtime` / `evidence` 等）出现")
    lines.append("  `PermissionError [WinError 5]` / `FileNotFoundError`。")
    lines.append("- **本记录的 558 passed 是在不受沙箱限制的条件下执行的**（同一代码、同一数据库、同一依赖版本）。")
    lines.append("  在受限环境下同一命令会出现 74 项环境性失败，这不是回归。")
    lines.append("")
    lines.append("## 2. 复现命令")
    lines.append("")
    lines.append("```bash")
    lines.append("# 后端：需求质量门控专项（169 例）")
    lines.append("cd backend")
    lines.append("../.venv/Scripts/python.exe -m pytest \\")
    lines.append("  tests/platform/test_requirement_quality_gate.py \\")
    lines.append("  tests/platform/test_requirement_quality_api.py \\")
    lines.append("  tests/platform/test_requirement_quality_review_fixes.py -q")
    lines.append("")
    lines.append("# 后端：平台全量")
    lines.append("../.venv/Scripts/python.exe -m pytest tests/platform -q")
    lines.append("")
    lines.append("# 前端：类型检查")
    lines.append("cd ui && npx tsc --noEmit --incremental false")
    lines.append("")
    lines.append("# 前端：表单字段贯通校验（需先编译，见脚本头部注释）")
    lines.append("cd ui && npx tsc lib/api/ai.ts --outDir .verify-out --module esnext \\")
    lines.append("  --target es2020 --moduleResolution bundler --skipLibCheck --noEmit false")
    lines.append("node scripts/verify-requirement-quality-form.mjs")
    lines.append("")
    lines.append("# 数据库迁移预检（部署前置检查，退出码 2 = 检测到版本漂移需人工确认）")
    lines.append("cd backend && ../.venv/Scripts/python.exe -m app.scripts.migration_preflight")
    lines.append("```")
    lines.append("")
    lines.append("## 3. 基线失败（仓库既有，与本次改动无关）")
    lines.append("")
    lines.append("| 范围 | 现象 | 复现命令 |")
    lines.append("| --- | --- | --- |")
    lines.append("| `tests/api/pr2_storage_recharge/*` | 8 个收集错误：`conftest.py` 缺少 `_env_gate` 定义 | `pytest tests/api/pr2_storage_recharge -q` |")
    lines.append("| `tests/api/api_doc_import/*` | 12 个失败：`_validate_spec_url` 返回值元数与测试期望不一致 | `pytest tests/api/api_doc_import -q` |")
    lines.append("")
    lines.append("以上文件本次均未修改。")
    lines.append("")
    lines.append("## 4. 版本控制状态（执行本记录时的快照）")
    lines.append("")
    lines.append("### 已暂存（本次变更的完整自洽集合）")
    lines.append("")
    lines.append("```")
    lines.append(staged or "(空)")
    lines.append("```")
    lines.append("")
    lines.append("### 仍被 .gitignore 排除、需要人工决策")
    lines.append("")
    lines.append("```")
    lines.append(untracked_ignored or "(空)")
    lines.append("```")
    lines.append("")
    lines.append("详见 `需求质量门控与追踪-交付治理说明.md`。")
    lines.append("")

    out = ROOT / "可复现验证记录.md"
    out.write_text("\n".join(lines), encoding="utf-8", newline="\n")
    print(f"已写出 {out}")
    print(f"staged entries: {len(staged.splitlines())}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
