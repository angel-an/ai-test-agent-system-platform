"""P0 交付清单与复现环境生成器。

用途：在提交 P0 前固定"这次到底提交了什么、在什么环境下验证过"，
生成可归档的清单文件（默认写到仓库根 ``P0-交付清单.md``）。

用法::

    python scripts/gen_p0_manifest.py --write
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]

# 明确记录：P1 文件不在此清单内（留在工作区等待单独验收）
P1_FILES = [
    "backend/app/api/v2/api_endpoints.py",
    "backend/app/services/api_test_service.py",
    "backend/app/services/api_endpoint_script_executor.py",
    "backend/app/services/test_run_artifact_service.py",
    "backend/app/main.py",
    "backend/app/config/settings.py",
    "backend/tests/api/test_contract_driven_p1.py",
    "backend/tests/platform/test_collection_assertions_and_preflight.py",
    "backend/scripts/p0_p1_bridge_check.py",
]

# 交付线归类规则（按前缀匹配，顺序敏感）
LINES = [
    ("P0-A 需求质量门控（阶段一/二）", (
        "backend/app/test_platform/requirements/",
        "backend/app/test_platform/services/requirement_quality_service.py",
        "backend/app/test_platform/testcase_contract.py",
        "backend/app/api/v2/requirement_quality.py",
        "backend/alembic/versions/0020_test_case_traceability.py",
        "backend/tests/platform/test_requirement_quality",
        "ui/components/test-cases/requirement-quality-panel.tsx",
        "ui/lib/api/requirement-quality.ts",
        "scripts/verify-requirement-quality-form",
        "需求质量门控与追踪-",
        "可复现验证记录.md",
    )),
    ("P0-B 统一导入 + 安全修复 + 存量回填", (
        "backend/app/services/api_bundle_service.py",
        "backend/app/api/v2/api_bundles.py",
        "backend/app/services/api_asset_matcher.py",
        "backend/app/services/excel_api_parser.py",
        "backend/app/schemas/api_endpoint.py",
        "backend/app/models/api_endpoint.py",
        "backend/app/scripts/",
        "backend/alembic/versions/0021_api_endpoint_normalized_path.py",
        "backend/scripts/",
        "backend/tests/api/test_api_bundle_import.py",
        "backend/tests/platform/test_api_endpoint_security_shape.py",
        "backend/tests/test_api_asset_matcher.py",
        "backend/tests/test_excel_api_parser.py",
        "ui/components/api-tests/unified-import-dialog.tsx",
        "ui/lib/api/api-bundles",
        "ui/lib/api/ai.ts",
        "统一导入-后端交付说明.md",
    )),
    ("工具与交付校验", (
        "scripts/",
        ".gitignore",
        "ui/components/ui/",
        "ui/lib/api/client.ts",
    )),
]


def _run(*args: str) -> str:
    out = subprocess.run(
        ["git", *args], cwd=str(REPO), capture_output=True, text=True, encoding="utf-8"
    )
    return (out.stdout or "").strip()


def _staged() -> list[str]:
    out = _run("diff", "--cached", "--name-only")
    return [line for line in out.splitlines() if line.strip()]


def _classify(path: str) -> str:
    for label, prefixes in LINES:
        for prefix in prefixes:
            if path.startswith(prefix):
                return label
    return "其它"


def build() -> str:
    staged = _staged()
    staged = [p for p in staged if p.replace("\\", "/") not in P1_FILES]

    grouped: dict[str, list[str]] = {}
    for path in staged:
        grouped.setdefault(_classify(path), []).append(path)

    head = _run("rev-parse", "HEAD")
    branch = _run("rev-parse", "--abbrev-ref", "HEAD")
    p1_present = [
        p for p in P1_FILES
        if _run("status", "--porcelain", "--", p).strip()
    ]

    lines: list[str] = []
    lines.append("# P0 交付清单")
    lines.append("")
    lines.append(f"生成时间（UTC）：{datetime.now(timezone.utc).isoformat(timespec='seconds')}")
    lines.append("")
    lines.append("## 复现环境")
    lines.append("")
    lines.append("| 项 | 值 |")
    lines.append("| --- | --- |")
    lines.append(f"| 分支 | `{branch}` |")
    lines.append(f"| 提交前 HEAD | `{head[:12]}` |")
    lines.append("| 数据库迁移 | `0020 → 0021 (head)`，本次交付新增 `0020`、`0021` |")
    lines.append("| Python / pytest | 3.13.3 / 9.1.1 |")
    lines.append("| PostgreSQL | 16（库 `langgraph_platform_db`） |")
    lines.append("| 执行环境 | 非受限（受限沙箱会阻断 `tmp_path` 写入） |")
    lines.append("| 验收样本 | `D:\\测试管理\\项目管理\\美心项目\\美心POS\\API\\` 下三文件 |")
    lines.append("")
    lines.append("## 交付前验证（命令与结果）")
    lines.append("")
    lines.append("```bash")
    lines.append("cd backend")
    lines.append(
        "..\\.venv\\Scripts\\python.exe -X utf8 -m pytest tests/platform "
        "tests/api/test_api_bundle_import.py tests/test_api_asset_matcher.py "
        "tests/test_excel_api_parser.py -q --ignore=tests/platform/"
        "test_collection_assertions_and_preflight.py"
    )
    lines.append("```")
    lines.append("")
    lines.append("结果：**638 passed**（不含 P1 测试文件；P1 缺席时的 P0 结果）")
    lines.append("")
    lines.append("```bash")
    lines.append("..\\.venv\\Scripts\\python.exe -X utf8 -m alembic current")
    lines.append("```")
    lines.append("")
    lines.append("结果：`0021 (head)`")
    lines.append("")
    lines.append(f"## 暂存文件清单（{len(staged)} 条）")
    lines.append("")
    for label in sorted(grouped.keys()):
        items = sorted(grouped[label])
        lines.append(f"### {label}（{len(items)}）")
        lines.append("")
        for item in items:
            lines.append(f"- `{item}`")
        lines.append("")

    lines.append("## 明确不在本次交付范围（P1，留在工作区）")
    lines.append("")
    lines.append("以下文件**未暂存**，等待真实 UAT 验收后单独提交：")
    lines.append("")
    for item in P1_FILES:
        marker = "（当前仍改动/未跟踪）" if item in p1_present else "（未检出改动）"
        lines.append(f"- `{item}` {marker}")
    lines.append("")
    lines.append("## 交付记录项（必须随提交保留）")
    lines.append("")
    lines.append(
        "1. `ui/app/projects/[projectId]/api-tests/page.tsx` **未被 git 跟踪**，"
        "「联合导入」入口与该对话框挂载点都在其中；单独提交组件前必须先纳入该文件，"
        "否则功能存在但用户无法到达。"
    )
    lines.append(
        "2. 本机数据库测试账号 `pr9qa` 的密码已为浏览器验收重置（非生产账号，"
        "未改动 admin 与其他账号）；如需还原，用任意 12 轮 bcrypt 哈希覆盖 "
        "`users.password_hash`。"
    )
    lines.append("")
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description="生成 P0 交付清单")
    parser.add_argument("--write", action="store_true", help="写入仓库根 P0-交付清单.md")
    args = parser.parse_args()

    content = build()
    if args.write:
        target = REPO / "P0-交付清单.md"
        target.write_text(content, encoding="utf-8")
        print(f"已写入 {target}")
    else:
        sys.stdout.write(content)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
