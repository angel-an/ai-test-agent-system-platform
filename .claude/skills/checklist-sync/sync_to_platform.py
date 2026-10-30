#!/usr/bin/env python3
"""checklist-sync：把上线 Checklist 表格行同步进平台（row_id 幂等 upsert）。

数据源契约
----------
腾讯在线文档（https://docs.qq.com/sheet/DVWVLUFFnYVhwdGdH?tab=1h8nmv）的读取
依赖 WorkBuddy 的腾讯文档授权：由 WorkBuddy 打开文档导出/读出表格行，交给
本脚本的 --input。本脚本只依赖标准库，任何机器可跑。

用法
----
    python sync_to_platform.py --input rows.json --project PR-2 \
        --base http://127.0.0.1:8000 [--token JWT] [--dry-run]

    rows.json: 行对象数组（键名为表格列名，支持下方别名），或 CSV（首行为列名）。

锚定规则（与库内既有 96 行实测 96/96 吻合，勿改）
--------------------------------------------------
    row_id      = "CK-" + md5(f"{主模块}|{子模块}|{功能点}")[:8]      (utf-8)
    content_md5 = md5(f"{标题}|{预期}|{优先级}|{探针类型}|{数据模式}")   (utf-8)

content_md5 用于变更检测：一致 → skipped（仅刷新行号快照）；
不一致 → updated；新 row_id → created。平台不做删除——表格删行请人工处理。

退出码：0 全部 skipped；3 存在 updated/created（行漂移信号，CI 可判红）。
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import sys
import urllib.request


# 列名别名 → ChecklistRowIn 字段。键全部小写后匹配。
COLUMN_ALIASES: dict[str, str] = {
    "主模块": "main_module", "主模块名": "main_module", "模块": "main_module",
    "main_module": "main_module", "module": "main_module",
    "子模块": "sub_module", "子模块名": "sub_module",
    "sub_module": "sub_module",
    "功能点": "feature", "功能": "feature", "feature": "feature",
    "预期结果": "expected", "预期": "expected", "expected": "expected",
    "优先级": "priority", "priority": "priority",
    "探针类型": "probe_kind", "探针": "probe_kind", "probe_kind": "probe_kind",
    "数据模式": "data_mode", "数据供给模式": "data_mode", "data_mode": "data_mode",
    "标题": "title", "title": "title",
    "行号": "row", "row": "row", "序号": "row",
}

PROBE_KIND_DEFAULT = "reachable"
DATA_MODE_DEFAULT = "readonly"


def norm_key(k: str) -> str:
    return k.strip().lower()


def load_rows(path: str) -> list[dict]:
    """读取 JSON / CSV / Excel(.xlsx) 为行字典列表（与后端 checklist_sync.parse_sheet 同源）。"""
    lower = path.lower()
    if lower.endswith(".xlsx"):
        from openpyxl import load_workbook
        wb = load_workbook(path, read_only=True, data_only=True)
        ws = wb.worksheets[0]
        it = ws.iter_rows(values_only=True)
        headers = [str(h).strip() if h is not None else "" for h in next(it, [])]
        out = []
        for row in it:
            item = {headers[i]: ("" if v is None else str(v))
                    for i, v in enumerate(row) if i < len(headers) and headers[i]}
            if any(v.strip() for v in item.values()):
                out.append(item)
        wb.close()
        return out
    with open(path, "rb") as f:
        head = f.read(4)
    if lower.endswith(".csv") or not head.lstrip().startswith(b"["):
        with open(path, newline="", encoding="utf-8-sig") as f:
            return [dict(r) for r in csv.DictReader(f)]
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    if not isinstance(data, list):
        raise SystemExit("JSON 输入须为行对象数组")
    return data


def map_row(raw: dict, index: int) -> dict:
    """表格行 → ChecklistRowIn。缺主模块/功能点的行视为空行跳过（返回 None）。"""
    mapped: dict = {}
    for k, v in raw.items():
        field = COLUMN_ALIASES.get(norm_key(str(k)))
        if field and v is not None:
            mapped[field] = str(v).strip()
    main_module = mapped.get("main_module", "")
    sub_module = mapped.get("sub_module", "")
    feature = mapped.get("feature", "")
    if not feature and not main_module:
        return None  # 空行
    title = mapped.get("title") or f"[{main_module}/{sub_module}] {feature}"
    item = {
        "row_id": "CK-" + hashlib.md5(
            f"{main_module}|{sub_module}|{feature}".encode("utf-8")
        ).hexdigest()[:8],
        "row": mapped.get("row") or index + 1,
        "title": title,
        "main_module": main_module,
        "sub_module": sub_module,
        "feature": feature,
        "expected": mapped.get("expected", ""),
        "priority": mapped.get("priority", ""),
        "probe_kind": mapped.get("probe_kind") or PROBE_KIND_DEFAULT,
        "data_mode": mapped.get("data_mode") or DATA_MODE_DEFAULT,
        "content_md5": hashlib.md5(
            "|".join([
                title,
                mapped.get("expected", ""),
                mapped.get("priority", ""),
                mapped.get("probe_kind") or PROBE_KIND_DEFAULT,
                mapped.get("data_mode") or DATA_MODE_DEFAULT,
            ]).encode("utf-8")
        ).hexdigest(),
    }
    # 未映射的额外列进 extra（如人工结果列），原样透传
    extra = {k: v for k, v in raw.items()
             if norm_key(str(k)) not in COLUMN_ALIASES and v not in (None, "")}
    if extra:
        item["extra"] = extra
    return item


def post_import(base: str, project: str, rows: list[dict], token: str | None) -> dict:
    url = f"{base.rstrip('/')}/api/v2/projects/{project}/checklist/import"
    body = json.dumps({"rows": rows}).encode("utf-8")
    req = urllib.request.Request(url, data=body, method="POST", headers={
        "Content-Type": "application/json",
    })
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    with urllib.request.urlopen(req, timeout=60) as resp:
        return json.loads(resp.read().decode("utf-8"))


def main() -> int:
    ap = argparse.ArgumentParser(description="同步上线 Checklist 行到平台")
    ap.add_argument("--input", required=True, help="表格行 JSON/CSV（WorkBuddy 授权导出）")
    ap.add_argument("--project", required=True, help="项目 identifier，如 PR-2")
    ap.add_argument("--base", default="http://127.0.0.1:8000", help="平台后端地址")
    ap.add_argument("--token", default=None, help="JWT（可选，checklist 端点当前不强制）")
    ap.add_argument("--dry-run", action="store_true", help="只构建载荷不提交")
    args = ap.parse_args()

    raw_rows = load_rows(args.input)
    items = [m for i, r in enumerate(raw_rows) if (m := map_row(r, i)) is not None]
    dup = len(items) - len({x["row_id"] for x in items})
    if dup:
        print(f"[warn] {dup} 个重复 row_id（主模块|子模块|功能点 相同），后者覆盖前者",
              file=sys.stderr)

    print(f"[info] 解析 {len(raw_rows)} 行 → 有效 {len(items)} 行，项目 {args.project}")
    if args.dry_run:
        json.dump({"rows": items}, sys.stdout, ensure_ascii=False, indent=2)
        print()
        return 0

    result = post_import(args.base, args.project, items, args.token)
    data = result.get("data", {})
    counts = data.get("counts", {})
    print(f"[done] created={counts.get('created')} updated={counts.get('updated')} "
          f"skipped={counts.get('skipped')} total={data.get('total')}")
    if counts.get("updated") or counts.get("created"):
        print("[drift] 存在行新增/变更，请核对表格是否有改动", file=sys.stderr)
        return 3
    return 0


if __name__ == "__main__":
    sys.exit(main())
