# -*- coding: utf-8 -*-
"""P0-3 依赖推断证据诊断（只处理能证明的依赖）。

设计原则（用户要求）：**没有证据的依赖保持为空，不推测**。
本脚本因此不"制造"依赖，只报告三类证据的有无与可证明的依赖条数：

1. folder 顺序：Collection 内同一 folder 的请求次序（提供"先后"语义）；
2. 变量写入：test script 中的 `pm.*.set('var', ...)`（提供"产出"证据）；
3. 后续请求引用：`{{var}}`（提供"消费"证据）。

**可证明的依赖** = 某个变量被 A 写入、且被同 folder 中次序更晚的 B 引用。
三者缺一即为"无证据"，此时依赖数为 0 是正确结果，而不是功能缺失。

用法::

    cd backend
    ..\\.venv\\Scripts\\python.exe -X utf8 scripts\\dependency_evidence_report.py --json
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

BACKEND = Path(__file__).resolve().parents[1]
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from app.services.postman_collection_parser import PostmanCollectionParser  # noqa: E402

from tests.sample_paths import COLLECTION_PATH

DEFAULT_COLLECTION = COLLECTION_PATH

VAR_REF_RE = re.compile(r"\{\{(\w+)\}\}")
VAR_WRITE_RE = re.compile(
    r"pm\.(?:environment|collectionVariables|variables|globals)\.set\(\s*['\"](\w+)['\"]"
)


@dataclass
class RequestNode:
    order: int
    folder: tuple[str, ...]
    name: str
    writes: set[str] = field(default_factory=set)
    reads: set[str] = field(default_factory=set)


def flatten(items: list[dict], path: tuple[str, ...] = (), counter: list[int] | None = None) -> list[RequestNode]:
    if counter is None:
        counter = [0]
    out: list[RequestNode] = []
    for item in items or []:
        name = item.get("name") or ""
        if "item" in item:
            out += flatten(item["item"], path + (name,), counter)
            continue
        if "request" not in item:
            continue

        writes: set[str] = set()
        for event in item.get("event") or []:
            if event.get("listen") != "test":
                continue
            script = event.get("script", {}).get("exec", "")
            script_text = "\n".join(script) if isinstance(script, list) else str(script)
            writes |= set(VAR_WRITE_RE.findall(script_text))

        reads = set(VAR_REF_RE.findall(json.dumps(item.get("request") or {}, ensure_ascii=False)))

        out.append(RequestNode(
            order=counter[0], folder=path, name=name, writes=writes, reads=reads,
        ))
        counter[0] += 1
    return out


def build_report(collection_path: Path) -> dict[str, Any]:
    raw = json.loads(collection_path.read_text(encoding="utf-8"))
    nodes = flatten(raw.get("item"))

    parser = PostmanCollectionParser().parse(collection_path.read_bytes())

    writes_by_var: dict[str, list[RequestNode]] = {}
    for node in nodes:
        for var in node.writes:
            writes_by_var.setdefault(var, []).append(node)

    proven: list[dict[str, Any]] = []
    for node in nodes:
        for var in sorted(node.reads):
            writers = writes_by_var.get(var, [])
            # 只认"同 folder 内、次序更早"的写入者，避免跨 folder 的顺序假设
            earlier = [
                w for w in writers
                if w.folder == node.folder and w.order < node.order
            ]
            if not earlier:
                continue
            source = sorted(earlier, key=lambda w: w.order)[-1]
            proven.append({
                "variable": var,
                "from": source.name,
                "to": node.name,
                "folder": list(node.folder),
                "evidence": "test_script_write_then_reference",
            })

    folder_counts: dict[str, int] = {}
    for node in nodes:
        key = "/".join(node.folder) or "(root)"
        folder_counts[key] = folder_counts.get(key, 0) + 1

    return {
        "collection": collection_path.name,
        "requests": len(nodes),
        "folders": folder_counts,
        "evidence": {
            "test_scripts": sum(1 for n in nodes if n.writes),
            "variable_writes": sum(len(n.writes) for n in nodes),
            "variable_references": sorted({v for n in nodes for v in n.reads}),
            "parser_variable_writes": sum(len(r.variable_writes or []) for r in parser.requests),
        },
        "provable_dependencies": proven,
        "provable_count": len(proven),
        "conclusion": (
            "有证据：依赖应落库"
            if proven else
            "无证据：依赖数为 0 是正确结果（Collection 未提供变量写入，无法证明先后依赖）"
        ),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="P0-3 依赖推断证据诊断")
    parser.add_argument("--collection", default=str(DEFAULT_COLLECTION))
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    path = Path(args.collection)
    if not path.exists():
        print(f"Collection 不存在: {path}")
        return 2

    report = build_report(path)
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0

    print("=" * 72)
    print(f"P0-3 依赖证据诊断: {report['collection']}")
    print("=" * 72)
    print(f"请求数: {report['requests']}")
    print("folder 分布:")
    for folder, count in sorted(report["folders"].items(), key=lambda x: -x[1]):
        print(f"  {folder}: {count}")
    ev = report["evidence"]
    print(f"带 test script 的请求: {ev['test_scripts']}")
    print(f"变量写入条数: {ev['variable_writes']}（解析器识别 {ev['parser_variable_writes']}）")
    print(f"被引用变量: {', '.join(ev['variable_references']) or '(无)'}")
    print(f"可证明依赖: {report['provable_count']}")
    for dep in report["provable_dependencies"]:
        print(f"  - {dep['from']} -> {dep['to']} via {dep['variable']} ({dep['folder']})")
    print()
    print(f"结论: {report['conclusion']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
