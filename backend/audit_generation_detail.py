"""只读逐条审计：本地生成结果 + 已落库结果，输出可比对的全字段快照。

用法（在 backend/ 下）：
    ..\\.venv\\Scripts\\python.exe audit_generation_detail.py [--persisted]

不写入数据库、不修改任何用例；仅落盘 JSON 与 UTF-8 报告供逐条核对。
"""
from __future__ import annotations

import argparse
import builtins
import json
import re
from collections import Counter, defaultdict
from pathlib import Path

import httpx

from app.api.v2.ai import _build_minimal_cases
from app.services.requirement_structure import parse_requirement_document

DOCUMENTS = [
    {
        "project": "PR-9",
        "folder_id": "1afacc2c-fe73-4c3d-aa8a-390eb0d74b97",
        "filename": "YL-主数据管理.pdf",
        "path": Path(
            "C:/Users/admin/WorkBuddy/2026-09-29-15-29-56/.workbuddy/tmp/yulon_md/requirement.pdf"
        ),
    },
    {
        "project": "PR-2",
        "folder_id": "34d550ed-afed-4796-9779-9a18bc993dba",
        "filename": "小杨生煎-订单评价(问卷调查)功能 (1).pdf",
        "path": Path("D:/360Downloads/小杨生煎-订单评价(问卷调查)功能 (1).pdf"),
    },
]

SNAPSHOT_FIELDS = (
    "name", "description", "module", "tags", "priority", "case_type",
    "keyword", "requirement_id", "feature_id", "source_location",
)

CUSTOM_FLAGS = (
    "companion_template", "filter_field_case", "import_clause_case",
    "list_display_case", "map_interaction_case", "read_only_case",
    "manual_supplement",
)


def build_local(doc: dict) -> tuple[list[dict], dict]:
    content = doc["path"].read_bytes()
    structure = parse_requirement_document(doc["filename"], "application/pdf", content)
    cases, gaps = _build_minimal_cases(
        structure.full_text, doc["filename"], structure=structure
    )
    return [c.model_dump(mode="json") for c in cases], gaps


def normalize(case: dict, *, from_db: bool) -> dict:
    cf = case.get("custom_fields") or {}
    steps = case.get("test_case_steps") or []
    record = {field: case.get(field) for field in SNAPSHOT_FIELDS}
    record["tags"] = list(case.get("tags") or [])
    record["module"] = case.get("module") or ""
    record["custom"] = {
        "requirement_rule_id": cf.get("requirement_rule_id"),
        "confirmation_status": cf.get("confirmation_status"),
        "ambiguity_reason": cf.get("ambiguity_reason"),
        "ambiguity_kind": cf.get("ambiguity_kind"),
        "module_source": cf.get("module_source"),
        "original_module": cf.get("original_module"),
        "coverage_source": cf.get("coverage_source"),
        "executable_track": cf.get("executable_track"),
        "source_evidence": cf.get("source_evidence"),
        "source_point": cf.get("source_point"),
        "field": (cf.get("requirement_rule") or {}).get("field"),
        "rule": (cf.get("requirement_rule") or {}).get("rule"),
        "rule_source": (cf.get("requirement_rule") or {}).get("source"),
        "assertions": cf.get("assertions"),
        "title_disambiguation": cf.get("title_disambiguation"),
        "module_evidence": cf.get("module_evidence"),
        "module_note": cf.get("module_note"),
        "duplicate_removed": cf.get("duplicate_removed"),
        "flags": sorted(k for k, v in cf.items() if v and k in CUSTOM_FLAGS),
    }
    record["steps"] = [
        {"step": s.get("step"), "result": s.get("result")} for s in steps
    ]
    if from_db:
        record["identifier"] = case.get("identifier")
    return record


def signature(record: dict) -> tuple:
    """逐条一致性签名：步骤 / 断言 / 模块 / 标签 / 规则 ID 全部参与。"""
    c = record["custom"]
    return (
        record["name"],
        record["module"],
        tuple(sorted(record["tags"])),
        c["requirement_rule_id"],
        json.dumps(record["steps"], ensure_ascii=False, sort_keys=True),
        json.dumps(c["assertions"], ensure_ascii=False, sort_keys=True),
        c["confirmation_status"],
        c["executable_track"],
    )


def title_key(name: str) -> str:
    return re.sub(r"^待确认-", "", name or "").strip()


def load_persisted(client: httpx.Client, doc: dict) -> list[dict]:
    """分页取全部已落库用例（页码参数是 ``p``；``page_size`` 上限由服务端配置）。"""
    url = (
        f"http://localhost:3000/api/v2/projects/{doc['project']}"
        f"/folders/{doc['folder_id']}/test-cases"
    )
    out: list[dict] = []
    page = 1
    while True:
        response = client.get(url, params={"p": page, "page_size": 200})
        response.raise_for_status()
        payload = response.json()
        batch = payload.get("data") or []
        out.extend(batch)
        total = int((payload.get("info") or {}).get("total") or len(out))
        if not batch or len(out) >= total:
            return out
        page += 1


def report_group(title: str, items: list[dict], source: str, emit=None) -> None:
    emit = emit or builtins.print
    emit(f"  [{source}] {title}  x{len(items)}")
    for item in items:
        c = item["custom"]
        src = (c["rule_source"] or {}) if c["rule_source"] else {}
        emit(
            "      rule_id={rid} module={mod!r} table={tbl} page={pg} evidence={ev!r} "
            "flags={flags} status={st}".format(
                rid=c["requirement_rule_id"], mod=item["module"],
                tbl=src.get("table"), pg=src.get("page"),
                ev=(c["source_evidence"] or c["source_point"] or "")[:70],
                flags=c["flags"], st=c["confirmation_status"],
            )
        )
        emit(f"        field={c['field']!r}")
        emit(f"        name={item['name']!r}")
        if c.get("title_disambiguation"):
            emit(f"        disambiguation={c['title_disambiguation']!r} "
                 f"module_evidence={c.get('module_evidence')!r}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--persisted", action="store_true", help="同时拉取已落库结果")
    parser.add_argument("--out", default="audit_generation_detail.json")
    parser.add_argument("--report", default="audit_report.md", help="UTF-8 报告落盘路径")
    args = parser.parse_args()

    report_lines: list[str] = []
    console = builtins.print

    def print(*parts, **kwargs):  # noqa: A001 - 报告需同时落盘，避免控制台编码损坏中文
        line = " ".join(str(p) for p in parts)
        report_lines.append(line)
        console(line, **kwargs)

    payload = []
    with httpx.Client(timeout=180) as client:
        for doc in DOCUMENTS:
            local, _gaps = build_local(doc)
            local = [normalize(c, from_db=False) for c in local]
            entry = {"document": doc["filename"], "local": local}

            print("=" * 100)
            print(f"DOC {doc['filename']}  local={len(local)}")
            print("-- module distribution --")
            for module, count in Counter(r["module"] for r in local).most_common():
                print(f"   {count:4d}  {module}")

            print("-- duplicate titles (local) --")
            groups = defaultdict(list)
            for r in local:
                groups[title_key(r["name"])].append(r)
            dup = {k: v for k, v in groups.items() if len(v) > 1}
            print(f"   {len(dup)} duplicate title groups")
            for title, items in dup.items():
                report_group(title, items, "local", print)

            print("-- fallback-module cases (module == document stem) --")
            stem = doc["filename"].rsplit(".", 1)[0]
            fallback = [r for r in local if r["module"] == stem]
            print(f"   {len(fallback)} cases in module {stem!r}")
            for r in fallback:
                c = r["custom"]
                print(
                    "   * {name!r} rule_id={rid} flags={flags} evidence={ev!r}".format(
                        name=r["name"], rid=c["requirement_rule_id"],
                        flags=c["flags"],
                        ev=(c["source_evidence"] or c["source_point"] or c["rule"] or "")[:70],
                    )
                )

            print("-- pending items (local) --")
            for r in local:
                if r["custom"]["confirmation_status"] == "pending":
                    print(
                        "   * {name!r} flags={flags} kind={kind} reason={reason!r}".format(
                            name=r["name"], flags=r["custom"]["flags"],
                            kind=r["custom"]["ambiguity_kind"],
                            reason=r["custom"]["ambiguity_reason"],
                        )
                    )

            if args.persisted:
                stored = [normalize(c, from_db=True) for c in load_persisted(client, doc)]
                entry["persisted"] = stored
                print(f"-- persisted={len(stored)} --")
                print("-- duplicate titles (persisted) --")
                g2 = defaultdict(list)
                for r in stored:
                    g2[title_key(r["name"])].append(r)
                dup2 = {k: v for k, v in g2.items() if len(v) > 1}
                print(f"   {len(dup2)} duplicate title groups")
                for title, items in dup2.items():
                    report_group(title, items, "db", print)

                print("-- per-case signature diff --")
                left = Counter(signature(r) for r in local)
                right = Counter(signature(r) for r in stored)
                only_local = left - right
                only_db = right - left
                print(f"   identical signatures: {sum((left & right).values())}/{len(local)}")
                for sig, count in only_local.items():
                    print(f"   ONLY-LOCAL x{count}: {sig[0]!r} module={sig[1]!r} rid={sig[3]}")
                for sig, count in only_db.items():
                    print(f"   ONLY-DB    x{count}: {sig[0]!r} module={sig[1]!r} rid={sig[3]}")
                entry["signature_diff"] = {
                    "identical": sum((left & right).values()),
                    "only_local": [list(s) for s in only_local],
                    "only_db": [list(s) for s in only_db],
                }

            payload.append(entry)

    Path(args.out).write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    Path(args.report).write_text("\n".join(report_lines), encoding="utf-8")
    console(f"\nwrote {args.out}  {args.report}")


if __name__ == "__main__":
    main()
