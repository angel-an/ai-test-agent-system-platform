"""本轮质量收口最终核验：真实 HTTP 生成新批次 → **逐条**比对生成结果与落库结果。

与上一轮验证脚本的区别（本轮质量问题的关键）：
- 旧脚本只比对**数量与模块分布**（`count` + `modules` 字典相等），逐条一致性未验；
- 本脚本对每条用例比对 **标题 / 模块 / 标签 / 步骤 / 断言 / 规则 ID / 确认状态 / 执行轨道**，
  任一字段不一致即列出差异并失败。

用法（在 backend/ 下，需 8000 后端已启动）：
    ..\\.venv\\Scripts\\python.exe verify_round_final.py [--dry-run] [--out verify_round_final.json]
"""
from __future__ import annotations

import argparse
import json
import re
from collections import Counter, defaultdict
from pathlib import Path

import httpx

from app.api.v2.ai import _build_minimal_cases
from app.services.requirement_structure import parse_requirement_document

API = "http://localhost:3000/api/v2/projects"

DOCUMENTS = [
    {
        "project": "PR-9",
        "filename": "YL-主数据管理.pdf",
        "folder": "YL-主数据管理10",
        "path": Path(
            "C:/Users/admin/WorkBuddy/2026-09-29-15-29-56/.workbuddy/tmp/yulon_md/requirement.pdf"
        ),
    },
    {
        "project": "PR-2",
        "filename": "小杨生煎-订单评价(问卷调查)功能 (1).pdf",
        "folder": "订单评价(问卷调查)-复验6",
        "path": Path("D:/360Downloads/小杨生煎-订单评价(问卷调查)功能 (1).pdf"),
    },
]

#: 逐条比对的字段（缺一不可）
COMPARE_FIELDS = (
    "name", "module", "tags", "priority", "case_type", "keyword", "risk_level",
    "case_kind", "source_location", "preconditions", "description",
)

#: API schema 默认值（backend/app/schemas/test_case.py: TestCreate.test_case_type）：
#: 生成侧不显式给 case_type 时，落库读回必然是 functional —— 比对时按同一口径归一，
#: 否则会把"schema 默认值"误报成"生成与落库不一致"。
CASE_TYPE_DEFAULT = "functional"


def canonical(case: dict, *, from_db: bool) -> dict:
    """把用例归一成可比对结构（步骤逐条、断言逐条，均按文档顺序）。"""
    cf = case.get("custom_fields") or {}
    steps = case.get("test_case_steps") or []
    fields = {f: case.get(f) for f in COMPARE_FIELDS}
    fields["case_type"] = fields.get("case_type") or CASE_TYPE_DEFAULT
    return {
        "fields": fields,
        "tags_sorted": sorted(case.get("tags") or []),
        "steps": [(s.get("step"), s.get("result")) for s in steps],
        "rule_id": cf.get("requirement_rule_id"),
        "assertions": json.dumps(cf.get("assertions"), ensure_ascii=False, sort_keys=True),
        "confirmation_status": cf.get("confirmation_status"),
        "ambiguity_kind": cf.get("ambiguity_kind"),
        "executable_track": cf.get("executable_track"),
        "module_source": cf.get("module_source"),
        "flags": sorted(k for k, v in cf.items() if v and k in (
            "companion_template", "filter_field_case", "import_clause_case",
            "list_display_case", "map_interaction_case", "read_only_case",
            "manual_supplement")),
        "identifier": case.get("identifier") if from_db else None,
    }


def identity_key(rec: dict) -> str:
    """稳定身份：规则 ID（缺失时退回标题）——同一批内可去重比对。"""
    return rec["rule_id"] or f"NAME::{rec['fields']['name']}"


def diff_case(local: dict, stored: dict) -> list[str]:
    problems = []
    for field, value in local["fields"].items():
        if stored["fields"].get(field) != value:
            problems.append(f"字段 {field}: 生成={value!r} 落库={stored['fields'].get(field)!r}")
    for key in ("tags_sorted", "steps", "rule_id", "assertions", "confirmation_status",
                "ambiguity_kind", "executable_track", "module_source", "flags"):
        if local[key] != stored[key]:
            problems.append(f"{key}: 生成={local[key]!r} 落库={stored[key]!r}")
    return problems


def compare_batch(local_cases: list[dict], stored_cases: list[dict]) -> dict:
    local = [canonical(c, from_db=False) for c in local_cases]
    stored = [canonical(c, from_db=True) for c in stored_cases]
    report: dict = {"local_count": len(local), "stored_count": len(stored),
                    "count_match": len(local) == len(stored), "mismatches": [], "missing": [],
                    "extra": []}
    by_stored = {}
    for rec in stored:
        by_stored.setdefault(identity_key(rec), []).append(rec)
    matched_stored: set[int] = set()
    for rec in local:
        pool = by_stored.get(identity_key(rec), [])
        hit = next((s for s in pool if id(s) not in matched_stored), None)
        if hit is None:
            report["missing"].append({"rule_id": rec["rule_id"], "name": rec["fields"]["name"]})
            continue
        matched_stored.add(id(hit))
        problems = diff_case(rec, hit)
        if problems:
            report["mismatches"].append({
                "rule_id": rec["rule_id"], "name": rec["fields"]["name"], "problems": problems,
            })
    for rec in stored:
        if id(rec) not in matched_stored:
            report["extra"].append({"rule_id": rec["rule_id"],
                                    "name": rec["fields"]["name"],
                                    "identifier": rec.get("identifier")})
    report["consistent"] = (not report["mismatches"] and not report["missing"]
                            and not report["extra"] and report["count_match"])
    return report


def quality_checks(stored_cases: list[dict], *, stem: str = "") -> dict:
    """本轮四个质量点的落库侧自检。"""
    cases = [canonical(c, from_db=True) for c in stored_cases]
    titles = [re.sub(r"^待确认-", "", c["fields"]["name"] or "") for c in cases]
    dup_titles = {t: n for t, n in Counter(titles).items() if n > 1}
    fallback = []
    for case in stored_cases:
        cf = case.get("custom_fields") or {}
        if cf.get("module_note"):
            fallback.append({"name": case.get("name"), "module": case.get("module"),
                             "note": cf["module_note"]})
    pending = [{"name": c["fields"]["name"], "kind": c["ambiguity_kind"],
                "reason": (c["fields"]["description"] or "")[-160:]}
               for c in cases if c["confirmation_status"] == "pending"]
    missing_meta = [c["fields"]["name"] for c in cases
                    if not c["fields"]["module"] or not c["tags_sorted"]]
    no_rule_id = [c["fields"]["name"] for c in cases if not c["rule_id"]]
    modules = Counter(c["fields"]["module"] for c in cases)
    generic = sum(1 for c in cases if c["module_source"] == "generic_suggestion")
    generic_module_ok = all(
        c["fields"]["module"] == "通用建议（非PRD条款）"
        for c in cases if c["module_source"] == "generic_suggestion")
    return {
        "duplicate_titles": dup_titles,
        "duplicate_title_count": len(dup_titles),
        "fallback_module_cases": fallback,
        "fallback_module_count": sum(1 for c in cases
                                     if stem and c["fields"]["module"] == stem),
        "pending": pending,
        "pending_by_kind": dict(Counter(c["ambiguity_kind"] for c in cases
                                        if c["confirmation_status"] == "pending")),
        "missing_module_or_tags": missing_meta,
        "cases_without_rule_id": no_rule_id,
        "modules": dict(modules),
        "generic_suggestion_cases": generic,
        "generic_module_naming_ok": generic_module_ok,
        "steps_all_present": all(c["steps"] for c in cases),
        "assertion_coverage": sum(1 for c in cases if c["assertions"] not in ("null", "[]")),
    }


def fetch_all_cases(client: httpx.Client, base: str, folder_id: str) -> list[dict]:
    """分页取全部用例（页码参数是 ``p``，``page_size`` 上限由服务端配置决定）。

    早前用 ``page_size=500`` 被服务端 422 拒绝——分页口径必须按 API 契约来，
    否则"取到的落库结果"可能只是第一页，逐条比对会假通过。
    """
    out: list[dict] = []
    page = 1
    while True:
        response = client.get(base + f"/folders/{folder_id}/test-cases",
                              params={"p": page, "page_size": 200})
        response.raise_for_status()
        payload = response.json()
        batch = payload.get("data") or []
        out.extend(batch)
        info = payload.get("info") or {}
        total = int(info.get("total") or len(out))
        if not batch or len(out) >= total:
            return out
        page += 1


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true", help="只跑本地生成与自检，不建文件夾/不写库")
    parser.add_argument("--out", default="verify_round_final.json")
    parser.add_argument(
        "--reuse", default="",
        help="复用已生成批次做比对（不新建文件夹/不重新生成），格式：PR-9=<folder_id>,PR-2=<folder_id>")
    args = parser.parse_args()

    reuse: dict[str, str] = {}
    for item in (args.reuse or "").split(","):
        if "=" in item:
            project, folder_id = item.split("=", 1)
            reuse[project.strip()] = folder_id.strip()

    results = []
    with httpx.Client(timeout=600) as client:
        for doc in DOCUMENTS:
            content = doc["path"].read_bytes()
            structure = parse_requirement_document(doc["filename"], "application/pdf", content)
            local_cases, gaps = _build_minimal_cases(
                structure.full_text, doc["filename"], structure=structure)
            for case in local_cases:
                # 与 /ai/generate-from-document 端点保持一致：未给来源位置时用上传文件名兜底
                case.source_location = case.source_location or doc["filename"]
            local_dump = [c.model_dump(mode="json") for c in local_cases]
            entry = {"document": doc["filename"], "project": doc["project"],
                     "local_count": len(local_dump)}
            print(f"\n=== {doc['filename']} 本地生成 {len(local_dump)} 条 "
                  f"（断言缺口 {len(gaps)} 条）===", flush=True)
            if args.dry_run:
                results.append(entry)
                continue

            base = f"{API}/{doc['project']}"
            if doc["project"] in reuse:
                folder_id = reuse[doc["project"]]
                print(f"复用已生成批次 {doc['project']} -> {folder_id}（不重新生成）", flush=True)
                stored = fetch_all_cases(client, base, str(folder_id))
                batch = compare_batch(local_dump, stored)
                entry.update({
                    "folder_id": str(folder_id), "folder": "（复用批次）",
                    "generated_count": len(stored),
                    "compare": batch,
                    "quality": quality_checks(
                        stored, stem=doc["filename"].rsplit(".", 1)[0]),
                })
                print(f"逐条比对：一致={batch['consistent']} 不一致={len(batch['mismatches'])} "
                      f"缺={len(batch['missing'])} 多={len(batch['extra'])}", flush=True)
                for item in batch["mismatches"][:10]:
                    print("  !", item["name"], item["problems"][:3], flush=True)
                results.append(entry)
                continue
            response = client.post(base + "/folders", json={
                "name": doc["folder"],
                "description": "本轮质量收口后的最终复验批次（同名标题/兜底模块/待确认分类修复）"})
            response.raise_for_status()
            
            folder = response.json()["data"]
            folder_id = folder.get("id") or folder.get("identifier")
            print(f"新建文件夹 {doc['folder']} -> {folder_id}", flush=True)
            response = client.post(
                base + "/ai/generate-from-document",
                data={"folder_id": str(folder_id)},
                files={"file": (doc["filename"], content, "application/pdf")},
            )
            response.raise_for_status()
            payload = response.json()
            assert payload.get("success"), payload.get("message")
            stored = fetch_all_cases(client, base, str(folder_id))

            batch = compare_batch(local_dump, stored)
            quality = quality_checks(stored, stem=doc["filename"].rsplit(".", 1)[0])
            entry.update({
                "folder_id": str(folder_id), "folder": doc["folder"],
                "generated_count": len(payload.get("test_cases") or []),
                "compare": batch, "quality": quality,
                "assertion_gap_warning": payload.get("assertion_gap_warning"),
                "compilability_warning": payload.get("compilability_warning"),
                "traceability_warning": payload.get("traceability_warning"),
            })
            print(f"逐条比对：一致={batch['consistent']} 不一致={len(batch['mismatches'])} "
                  f"缺={len(batch['missing'])} 多={len(batch['extra'])}", flush=True)
            for item in batch["mismatches"][:10]:
                print("  !", item["name"], item["problems"][:3], flush=True)
            for item in batch["missing"][:5]:
                print("  - 落库缺失:", item, flush=True)
            for item in batch["extra"][:5]:
                print("  + 落库多出:", item, flush=True)
            print("质量自检：", json.dumps(quality, ensure_ascii=False)[:900], flush=True)
            results.append(entry)

    Path(args.out).write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\nwrote {args.out}")
    if not args.dry_run:
        bad = [r for r in results if not r.get("compare", {}).get("consistent")]
        if bad:
            raise SystemExit(f"逐条一致性未通过：{[r['document'] for r in bad]}")


if __name__ == "__main__":
    main()

