# -*- coding: utf-8 -*-
"""数据库迁移预检（部署前置检查）。

背景：本仓库存在**版本漂移**风险 —— 某些环境里已经手工建过表/列，但 ``alembic_version``
没有对应记录，直接 ``alembic upgrade head`` 会报 ``DuplicateTable`` / ``DuplicateColumn`` 中断部署。

本脚本因此**不做任何自动 stamp**（自动猜测会掩盖真实漂移），只做三件事：

1. 读取 ``alembic_version`` 与 ``head``，列出待执行版本；
2. 对每个待执行版本，解析其 upgrade() 中的 DDL（CREATE TABLE / ADD COLUMN），
   与数据库实际结构比对，判定「已应用 / 未应用 / 部分应用（漂移）」；
3. 输出明确结论与建议命令，退出码：
   - 0：无需人工介入（可直接 upgrade，或本就已是最新）
   - 2：检测到结构已存在但版本缺失 → **必须人工确认后手工 stamp**
   - 3：元数据/连接异常

用法::

    cd backend
    ..\\.venv\\Scripts\\python.exe -m app.scripts.migration_preflight
    ..\\.venv\\Scripts\\python.exe -m app.scripts.migration_preflight --json
"""

from __future__ import annotations

import argparse
import ast
import json
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Optional

from sqlalchemy import create_engine, inspect
from sqlalchemy.exc import SQLAlchemyError


BACKEND_ROOT = Path(__file__).resolve().parents[2]
VERSIONS_DIR = BACKEND_ROOT / "alembic" / "versions"

EXIT_OK = 0
EXIT_DRIFT = 2
EXIT_ERROR = 3


@dataclass
class MigrationOps:
    """某个迁移版本声明的 DDL 操作。"""

    revision: str
    down_revision: Optional[str]
    path: str
    create_tables: list[str] = field(default_factory=list)
    add_columns: list[tuple[str, str]] = field(default_factory=list)


def _literal_str(node: ast.AST) -> Optional[str]:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    return None


def _literal_str_pair(node: ast.AST) -> Optional[tuple[str, str]]:
    if isinstance(node, ast.Tuple) and len(node.elts) == 2:
        first, second = _literal_str(node.elts[0]), _literal_str(node.elts[1])
        if first and second:
            return first, second
    return None


def _literal_str_list(node: ast.AST) -> Optional[str]:
    """解析 revision / down_revision 的字面量（支持 str 及其 Union 包装）。"""
    value = _literal_str(node)
    if value is not None:
        return value
    # Union[str, None] 之类不影响字面量取值
    if isinstance(node, ast.BinOp):
        return _literal_str(node.left) or _literal_str(node.right)
    return None


def _collect_module_string_assignments(tree: ast.AST) -> dict[str, Optional[str]]:
    """收集模块级 ``revision`` / ``down_revision`` 赋值。

    同时支持：
    - ``revision = "0020"``（普通赋值）
    - ``revision: str = "0020"``（带类型注解赋值）
    """
    found: dict[str, Optional[str]] = {}
    for node in getattr(tree, "body", []):
        names: list[str] = []
        value: Optional[ast.AST] = None

        if isinstance(node, ast.Assign):
            names = [t.id for t in node.targets if isinstance(t, ast.Name)]
            value = node.value
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            names = [node.target.id]
            value = node.value

        if value is None:
            continue
        for name in names:
            if name in ("revision", "down_revision") and name not in found:
                found[name] = _literal_str_list(value)
    return found


def parse_migration_ops() -> dict[str, MigrationOps]:
    """用 AST 静态解析各迁移文件声明的 revision 与 DDL 操作（不导入、不执行）。"""
    ops_by_revision: dict[str, MigrationOps] = {}
    if not VERSIONS_DIR.is_dir():
        return ops_by_revision

    for path in sorted(VERSIONS_DIR.glob("*.py")):
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except (OSError, SyntaxError):
            continue

        declares = _collect_module_string_assignments(tree)
        revision = declares.get("revision")
        down_revision = declares.get("down_revision")

        if not revision:
            continue

        ops = MigrationOps(revision=revision, down_revision=down_revision, path=path.name)

        for node in ast.walk(tree):
            if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
                continue
            func_name = node.func.attr

            # op.create_table("name", ...)
            if func_name == "create_table" and node.args:
                table = _literal_str(node.args[0])
                if table:
                    ops.create_tables.append(table)

            # op.add_column("table", sa.Column("col", ...))
            elif func_name == "add_column" and len(node.args) >= 2:
                table = _literal_str(node.args[0])
                column_arg = node.args[1]
                column_name: Optional[str] = None
                if isinstance(column_arg, ast.Call) and column_arg.args:
                    column_name = _literal_str(column_arg.args[0])
                if table and column_name:
                    ops.add_columns.append((table, column_name))

        ops_by_revision[revision] = ops

    return ops_by_revision


def _ops_state(ops: MigrationOps, existing_tables: set[str], columns_of) -> str:
    """判定某版本的操作在数据库中是否已存在。

    Returns:
        "applied"         全部已存在（结构与版本记录不一致 → 漂移）
        "not_applied"     全部不存在（可安全 upgrade）
        "partial"         部分存在（漂移更严重，必须人工确认）
        "unknown"         该版本无 CREATE TABLE / ADD COLUMN 可判定
    """
    checks: list[bool] = []

    for table in ops.create_tables:
        checks.append(table in existing_tables)

    for table, column in ops.add_columns:
        if table not in existing_tables:
            checks.append(False)
            continue
        checks.append(column in set(columns_of(table)))

    if not checks:
        return "unknown"
    if all(checks):
        return "applied"
    if any(checks):
        return "partial"
    return "not_applied"


def _resolve_linear_chain(
    ops_by_revision: dict[str, MigrationOps],
    head: str,
) -> list[str]:
    """从 head 回溯出线性升级链（旧 → 新）。"""
    by_down: dict[Optional[str], list[str]] = {}
    for revision, ops in ops_by_revision.items():
        by_down.setdefault(ops.down_revision, []).append(revision)

    chain: list[str] = []
    current: Optional[str] = head
    guard = 0
    while current and guard < 10_000:
        guard += 1
        chain.append(current)
        ops = ops_by_revision.get(current)
        if ops is None:
            break
        current = ops.down_revision
    chain.reverse()
    return chain


def run_preflight(database_url: Optional[str] = None) -> dict[str, Any]:
    """执行预检，返回结构化结果。"""
    report: dict[str, Any] = {
        "database_url_kind": "explicit" if database_url else "settings",
        "current": None,
        "head": None,
        "pending": [],
        "drift": [],
        "unknown": [],
        "conclusion": "",
        "recommended_commands": [],
    }

    try:
        from alembic.config import Config
        from alembic.script import ScriptDirectory
    except ImportError as exc:  # pragma: no cover
        report["conclusion"] = f"缺少 alembic 依赖: {exc}"
        report["exit_code"] = EXIT_ERROR
        return report

    if database_url is None:
        from app.config.settings import settings

        database_url = settings.postgres_sync_url

    alembic_ini = BACKEND_ROOT / "alembic.ini"
    cfg = Config(str(alembic_ini))
    cfg.set_main_option("script_location", str(BACKEND_ROOT / "alembic"))
    script = ScriptDirectory.from_config(cfg)

    heads = script.get_heads()
    head = heads[0] if heads else None
    report["head"] = head
    report["heads"] = sorted(heads)

    # 迁移文件目录与 alembic 脚本目录不一致时（历史原因），以文件解析为准补全 DDL 信息
    ops_by_revision = parse_migration_ops()
    for revision, ops in ops_by_revision.items():
        if not ops.down_revision:
            script_ops = next(
                (s for s in script.walk_revisions() if s.revision == revision), None
            )
            if script_ops is not None:
                ops_by_revision[revision] = MigrationOps(
                    revision=revision,
                    down_revision=script_ops.down_revision,
                    path=ops.path,
                    create_tables=ops.create_tables,
                    add_columns=ops.add_columns,
                )

    try:
        engine = create_engine(database_url)
        inspector = inspect(engine)
        existing_tables = set(inspector.get_table_names())
        columns_cache: dict[str, list[str]] = {}

        def columns_of(table: str) -> list[str]:
            if table not in columns_cache:
                columns_cache[table] = [c["name"] for c in inspector.get_columns(table)]
            return columns_cache[table]

        current = None
        if "alembic_version" in existing_tables:
            with engine.connect() as conn:
                row = conn.exec_driver_sql("select version_num from alembic_version").fetchone()
                current = row[0] if row else None
        report["current"] = current
        report["alembic_version_table_exists"] = "alembic_version" in existing_tables

        if not head:
            report["conclusion"] = "未能解析 alembic head，请检查 alembic/versions 目录。"
            report["exit_code"] = EXIT_ERROR
            return report

        chain = _resolve_linear_chain(ops_by_revision, head)
        pending = _pending_after(chain, current)
        report["pending"] = pending

        for revision in pending:
            ops = ops_by_revision.get(revision)
            if ops is None:
                continue
            state = _ops_state(ops, existing_tables, columns_of)
            item = {
                "revision": revision,
                "down_revision": ops.down_revision,
                "file": ops.path,
                "state": state,
                "create_tables": ops.create_tables,
                "add_columns": [f"{t}.{c}" for t, c in ops.add_columns],
            }
            if state == "applied":
                report["drift"].append(item)
            elif state == "partial":
                report["drift"].append({**item, "partial": True})
            elif state == "unknown":
                report["unknown"].append(item)
    except SQLAlchemyError as exc:
        report["conclusion"] = f"数据库连接/查询失败: {type(exc).__name__}: {exc}"
        report["exit_code"] = EXIT_ERROR
        return report

    if report["drift"]:
        report["conclusion"] = (
            f"检测到 {len(report['drift'])} 个版本的数据库结构已存在但 alembic_version 未记录（版本漂移）。"
            "不要直接 upgrade head：请先人工确认结构，再对每个漂移版本执行 alembic stamp。"
        )
        for item in report["drift"]:
            if item.get("partial"):
                report["recommended_commands"].append(
                    f"# 部分漂移，需人工核对结构后再决定: alembic stamp {item['revision']}"
                )
            else:
                report["recommended_commands"].append(
                    f"# 结构已存在，确认无误后: alembic stamp {item['revision']}"
                )
        report["recommended_commands"].append("alembic upgrade head")
        report["exit_code"] = EXIT_DRIFT
    elif report["pending"]:
        report["conclusion"] = (
            f"未检测到结构漂移，可安全执行 upgrade（待执行 {len(report['pending'])} 个版本）。"
        )
        report["recommended_commands"].append("alembic upgrade head")
        report["exit_code"] = EXIT_OK
    else:
        report["conclusion"] = "数据库已是最新版本。"
        report["exit_code"] = EXIT_OK

    return report


def _pending_after(chain: Iterable[str], current: Optional[str]) -> list[str]:
    """线性链中 current 之后待执行的版本。"""
    chain_list = list(chain)
    if current is None:
        return chain_list
    if current not in chain_list:
        # current 不在链上（可能是另一分支/未知版本）：全部视为待执行并提示
        return chain_list
    return chain_list[chain_list.index(current) + 1:]


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="数据库迁移预检（部署前置检查）")
    parser.add_argument("--json", action="store_true", help="以 JSON 输出完整报告")
    parser.add_argument("--db-url", default=None, help="显式指定数据库 URL（默认取 settings）")
    args = parser.parse_args(argv)

    report = run_preflight(args.db_url)

    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return int(report.get("exit_code", EXIT_ERROR))

    print("=" * 64)
    print("数据库迁移预检")
    print("=" * 64)
    print(f"当前版本: {report.get('current') or '(未 stamp / 无 alembic_version)'}")
    print(f"目标版本: {report.get('head')}")
    pending = report.get("pending") or []
    print(f"待执行版本: {len(pending)} {pending if pending else ''}")
    print()

    if report.get("drift"):
        print("⚠ 版本漂移（结构已存在，版本未记录）：")
        for item in report["drift"]:
            kind = "部分漂移" if item.get("partial") else "已存在"
            print(f"  - {item['revision']} ({item['file']}) [{kind}]")
            for table in item.get("create_tables") or []:
                print(f"      表已存在: {table}")
            for column in item.get("add_columns") or []:
                print(f"      列已存在: {column}")
        print()

    if report.get("unknown"):
        print("ℹ 无法自动判定（无 CREATE TABLE / ADD COLUMN）：")
        for item in report["unknown"]:
            print(f"  - {item['revision']} ({item['file']})")
        print()

    print(f"结论: {report.get('conclusion')}")
    if report.get("recommended_commands"):
        print()
        print("建议命令（请人工确认后执行）：")
        for command in report["recommended_commands"]:
            print(f"  {command}")

    return int(report.get("exit_code", EXIT_ERROR))


if __name__ == "__main__":
    sys.exit(main())
