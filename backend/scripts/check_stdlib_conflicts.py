#!/usr/bin/env python3
"""
检查项目中是否存在命名冲突的模块/包。

支持两类检查：
1. 标准库冲突：与 Python 标准库同名的模块/包（高风险，会导致导入遮蔽）
2. 项目保留名：项目内部约定的保留名称（中风险，可能导致语义混淆）

用法:
    python check_stdlib_conflicts.py [目录路径] [--reserve-names name1,name2]

退出码:
    0 - 无冲突
    1 - 发现冲突
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path


# ---------------------------------------------------------------------------
# 规则定义
# ---------------------------------------------------------------------------

# 第一类：Python 标准库模块名 —— 绝对禁止作为自定义模块/包名
# 来源：sys.stdlib_module_names（Python 3.10+）
STDLIB_MODULES: frozenset[str] = frozenset(
    sys.stdlib_module_names if hasattr(sys, "stdlib_module_names") else {
        "abc", "aifc", "argparse", "array", "ast", "asynchat", "asyncio",
        "asyncore", "atexit", "audioop", "base64", "bdb", "binascii",
        "binhex", "bisect", "builtins", "bz2", "calendar", "cgi", "cgitb",
        "chunk", "cmath", "cmd", "code", "codecs", "codeop", "collections",
        "colorsys", "compileall", "concurrent", "configparser", "contextlib",
        "contextvars", "copy", "copyreg", "cProfile", "crypt", "csv",
        "ctypes", "curses", "dataclasses", "datetime", "dbm", "decimal",
        "difflib", "dis", "distutils", "doctest", "email", "encodings",
        "enum", "errno", "faulthandler", "fcntl", "filecmp", "fileinput",
        "fnmatch", "fractions", "ftplib", "functools", "gc", "getopt",
        "getpass", "gettext", "glob", "graphlib", "grp", "gzip", "hashlib",
        "heapq", "hmac", "html", "http", "idlelib", "imaplib", "imghdr",
        "imp", "importlib", "inspect", "io", "ipaddress", "itertools",
        "json", "keyword", "lib2to3", "linecache", "locale", "logging",
        "lzma", "mailbox", "mailcap", "marshal", "math", "mimetypes",
        "mmap", "modulefinder", "multiprocessing", "netrc", "nis",
        "nntplib", "numbers", "operator", "optparse", "os", "ossaudiodev",
        "pathlib", "pdb", "pickle", "pickletools", "pipes", "pkgutil",
        "platform", "plistlib", "poplib", "posix", "posixpath", "pprint",
        "profile", "pstats", "pty", "pwd", "py_compile", "pyclbr", "pydoc",
        "queue", "quopri", "random", "re", "readline", "reprlib", "resource",
        "rlcompleter", "runpy", "sched", "secrets", "select", "selectors",
        "shelve", "shlex", "shutil", "signal", "site", "smtpd", "smtplib",
        "sndhdr", "socket", "socketserver", "spwd", "sqlite3", "ssl",
        "stat", "statistics", "string", "stringprep", "struct",
        "subprocess", "sunau", "symtable", "sys", "sysconfig", "syslog",
        "tabnanny", "tarfile", "telnetlib", "tempfile", "termios", "test",
        "textwrap", "threading", "time", "timeit", "tkinter", "token",
        "tokenize", "trace", "traceback", "tracemalloc", "tty", "turtle",
        "turtledemo", "types", "typing", "unicodedata", "unittest",
        "urllib", "uu", "uuid", "venv", "warnings", "wave", "weakref",
        "webbrowser", "winreg", "winsound", "wsgiref", "xdrlib", "xml",
        "xmlrpc", "zipapp", "zipfile", "zipimport", "zlib",
    }
)

# 第二类：项目保留名 —— 不是标准库，但项目内部约定避免使用
# 这些名字不会导致导入遮蔽，但可能造成语义混淆
DEFAULT_RESERVED_NAMES: frozenset[str] = frozenset()

# 默认排除的目录（不扫描）
DEFAULT_EXCLUDE_DIRS: frozenset[str] = frozenset({
    ".venv", "venv", "__pycache__", ".git", ".pytest_cache",
    ".mypy_cache", "node_modules", ".claude", ".idea", ".vscode",
    "dist", "build", ".egg-info",
})


# ---------------------------------------------------------------------------
# 核心检查逻辑
# ---------------------------------------------------------------------------

def check_directory(
    directory: Path,
    *,
    exclude_dirs: frozenset[str] | None = None,
    reserve_names: frozenset[str] | None = None,
) -> dict[str, list[tuple[str, str]]]:
    """
    扫描目录下的命名冲突。

    Args:
        directory: 要扫描的根目录
        exclude_dirs: 需要排除的目录名集合（默认 DEFAULT_EXCLUDE_DIRS）
        reserve_names: 项目保留名集合（默认 DEFAULT_RESERVED_NAMES）

    Returns:
        按冲突类型分组的字典，键为 'stdlib_package' | 'stdlib_module' |
        'reserved_package' | 'reserved_module'，值为 (相对路径, 冲突名) 列表
    """
    if exclude_dirs is None:
        exclude_dirs = DEFAULT_EXCLUDE_DIRS
    if reserve_names is None:
        reserve_names = DEFAULT_RESERVED_NAMES

    result: dict[str, list[tuple[str, str]]] = {
        "stdlib_package": [],
        "stdlib_module": [],
        "reserved_package": [],
        "reserved_module": [],
    }

    for root, dirs, files in os.walk(directory):
        # 过滤掉排除目录
        dirs[:] = [d for d in dirs if d not in exclude_dirs]

        rel_root = Path(root).relative_to(directory)

        for d in dirs:
            rel_path = str(rel_root / d).replace("\\", "/") if str(rel_root) != "." else d
            if d in STDLIB_MODULES:
                result["stdlib_package"].append((rel_path, d))
            elif d in reserve_names:
                result["reserved_package"].append((rel_path, d))

        for f in files:
            if not f.endswith(".py"):
                continue
            module_name = f[:-3]
            rel_path = str(rel_root / f).replace("\\", "/") if str(rel_root) != "." else f
            if module_name in STDLIB_MODULES:
                result["stdlib_module"].append((rel_path, module_name))
            elif module_name in reserve_names:
                result["reserved_module"].append((rel_path, module_name))

    return result


def has_conflicts(result: dict[str, list]) -> bool:
    """检查结果中是否存在任何冲突。"""
    return any(v for v in result.values())


def format_report(
    result: dict[str, list[tuple[str, str]]],
    target_dir: Path,
) -> str:
    """生成人类可读的检查报告。"""
    lines: list[str] = []
    lines.append(f"[CHECK] 检查目录: {target_dir.absolute()}")
    lines.append(f"   Python 版本: {sys.version.split()[0]}")
    lines.append("")

    if not has_conflicts(result):
        lines.append("[OK] 未发现命名冲突")
        return "\n".join(lines)

    total = sum(len(v) for v in result.values())
    lines.append(f"[FAIL] 发现 {total} 个命名冲突:\n")

    if result["stdlib_package"]:
        lines.append("[STD-PKG] 标准库包名冲突 (高风险 - 导入遮蔽):")
        for path, name in result["stdlib_package"]:
            lines.append(f"   - {path}  (与标准库 '{name}' 冲突)")
        lines.append("")

    if result["stdlib_module"]:
        lines.append("[STD-MOD] 标准库模块名冲突 (高风险 - 导入遮蔽):")
        for path, name in result["stdlib_module"]:
            lines.append(f"   - {path}  (与标准库 '{name}' 冲突)")
        lines.append("")

    if result["reserved_package"]:
        lines.append("[RSV-PKG] 项目保留包名冲突 (中风险 - 语义混淆):")
        for path, name in result["reserved_package"]:
            lines.append(f"   - {path}  (与保留名 '{name}' 冲突)")
        lines.append("")

    if result["reserved_module"]:
        lines.append("[RSV-MOD] 项目保留模块名冲突 (中风险 - 语义混淆):")
        for path, name in result["reserved_module"]:
            lines.append(f"   - {path}  (与保留名 '{name}' 冲突)")
        lines.append("")

    lines.append("[TIP] 建议:")
    lines.append("   1. 标准库冲突：必须重命名，使用带前缀的描述性名字")
    lines.append("   2. 保留名冲突：建议重命名，避免语义混淆")
    lines.append("")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> int:
    parser = argparse.ArgumentParser(
        description="检查 Python 项目中的命名冲突",
    )
    parser.add_argument(
        "directory",
        nargs="?",
        default=".",
        help="要扫描的目录 (默认: 当前目录)",
    )
    parser.add_argument(
        "--reserve-names",
        default="",
        help="逗号分隔的项目保留名列表 (如: config,utils,core)",
    )
    parser.add_argument(
        "--exclude-dirs",
        default="",
        help="逗号分隔的额外排除目录列表",
    )
    parser.add_argument(
        "--quiet",
        action="store_true",
        help="仅输出结果，不输出检查信息",
    )

    args = parser.parse_args()

    target_dir = Path(args.directory)
    if not target_dir.exists():
        print(f"[ERROR] 目录不存在: {target_dir}", file=sys.stderr)
        return 2

    # 解析保留名
    reserve_names: frozenset[str] = frozenset()
    if args.reserve_names:
        reserve_names = frozenset(n.strip() for n in args.reserve_names.split(",") if n.strip())

    # 解析额外排除目录
    exclude_dirs = set(DEFAULT_EXCLUDE_DIRS)
    if args.exclude_dirs:
        exclude_dirs.update(d.strip() for d in args.exclude_dirs.split(",") if d.strip())

    result = check_directory(
        target_dir,
        exclude_dirs=frozenset(exclude_dirs),
        reserve_names=reserve_names,
    )

    if not args.quiet:
        print(format_report(result, target_dir))

    return 1 if has_conflicts(result) else 0


if __name__ == "__main__":
    sys.exit(main())
