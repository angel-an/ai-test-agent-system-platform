"""Checklist 同步服务层：表格行解析、列映射与锚定计算。

平台端点（/checklist/sync-preview、/checklist/import）与 checklist-sync skill 脚本
（.claude/skills/checklist-sync/sync_to_platform.py）共用同一套规则，任一侧改动须同步另一侧
（脚本面向无平台网络的环境，保留一份独立实现，两侧有交叉注释）。

锚定规则（2026-10-28 与库内既有 96 行实测 96/96 吻合，勿改）：
    row_id      = "CK-" + md5(f"{主模块}|{子模块}|{功能点}")[:8]      (utf-8)
    content_md5 = md5(f"{标题}|{预期}|{优先级}|{探针类型}|{数据模式}")   (utf-8)
"""

from __future__ import annotations

import csv
import hashlib
import io
import json

# 列名别名 → ChecklistRowIn 字段（键全部小写后匹配）。
# 与 .claude/skills/checklist-sync/sync_to_platform.py 的 COLUMN_ALIASES 保持一致。
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

# 参与 content_md5 的列，diff 展示也以此为口径
CONTENT_FIELDS = ("title", "expected", "priority", "probe_kind", "data_mode")


def parse_sheet(data: bytes, filename: str = "") -> list[dict]:
    """解析腾讯文档导出的表格行为字典列表（JSON 数组或 CSV，按内容判断）。"""
    head = data.lstrip()[:1]
    if filename.lower().endswith(".csv") or head not in (b"[", b""):
        text = data.decode("utf-8-sig", errors="replace")
        return [dict(r) for r in csv.DictReader(io.StringIO(text))]
    parsed = json.loads(data.decode("utf-8"))
    if not isinstance(parsed, list):
        raise ValueError("JSON 输入须为行对象数组")
    return parsed


def map_row(raw: dict, index: int) -> dict | None:
    """表格行 → ChecklistRowIn 载荷。缺主模块且缺功能点的行视为空行（None）。

    与 sync_to_platform.py 的 map_row 保持一致。
    """
    mapped: dict = {}
    for k, v in raw.items():
        field = COLUMN_ALIASES.get(str(k).strip().lower())
        if field and v is not None:
            mapped[field] = str(v).strip()
    main_module = mapped.get("main_module", "")
    sub_module = mapped.get("sub_module", "")
    feature = mapped.get("feature", "")
    if not feature and not main_module:
        return None
    title = mapped.get("title") or f"[{main_module}/{sub_module}] {feature}"
    probe_kind = mapped.get("probe_kind") or PROBE_KIND_DEFAULT
    data_mode = mapped.get("data_mode") or DATA_MODE_DEFAULT
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
        "probe_kind": probe_kind,
        "data_mode": data_mode,
        "content_md5": content_md5(title, mapped.get("expected", ""),
                                   mapped.get("priority", ""), probe_kind, data_mode),
    }
    extra = {k: v for k, v in raw.items()
             if str(k).strip().lower() not in COLUMN_ALIASES and v not in (None, "")}
    if extra:
        item["extra"] = extra
    return item


def content_md5(title: str, expected: str, priority: str,
                probe_kind: str, data_mode: str) -> str:
    return hashlib.md5(
        "|".join([title, expected, priority, probe_kind, data_mode]).encode("utf-8")
    ).hexdigest()


def build_items(raw_rows: list[dict]) -> tuple[list[dict], int]:
    """全部行 → (有效载荷列表, 重复 row_id 数)。"""
    items = [m for i, r in enumerate(raw_rows) if (m := map_row(r, i)) is not None]
    dup = len(items) - len({x["row_id"] for x in items})
    return items, dup


# ---------------------------------------------------------------------------
# 回写载荷（writeback-preview 端点用）。
# 与 .claude/skills/checklist-writeback/build_writeback_payload.py 的输出保持
# 同一份列契约，任一侧加列须同步另一侧（脚本面向离线环境，保留独立实现）。
# ---------------------------------------------------------------------------

VERDICT_LABEL = {"pass": "通过", "fail": "失败", "unverified": "未验证", "empty": "无结果"}

WRITEBACK_COLUMNS = (
    "执行时间", "执行方式", "项目画像", "判定", "通过", "失败", "未验证",
    "流程类跳过", "失败行", "报告链接", "run_id",
)


def build_writeback_payload(
    run: dict, results: list[dict], label: str = "自动化执行"
) -> dict:
    """执行记录 → 插入腾讯文档《上线Checklist》「自动化执行」区的一行载荷。

    run: 含 run_id/run_verdict/profile_name/summary/skipped_flow/report_url/created_at。
    """
    summary = run.get("summary") or {}
    verdict = run.get("run_verdict") or "empty"
    failed = [r for r in results if r.get("status") == "fail"]
    return {
        "执行时间": fmt_cn_time(run.get("created_at") or ""),
        "执行方式": label,
        "项目画像": run.get("profile_name") or "",
        "判定": VERDICT_LABEL.get(verdict, verdict),
        "通过": summary.get("pass", 0),
        "失败": summary.get("fail", 0),
        "未验证": summary.get("unverified", 0),
        "流程类跳过": run.get("skipped_flow", 0),
        "失败行": ", ".join(
            f"{r.get('row_id')} {r.get('feature') or ''}({r.get('error') or '无原因'})"
            for r in failed
        ),
        "报告链接": run.get("report_url") or "",
        "run_id": run.get("run_id") or "",
    }


def fmt_cn_time(iso: str) -> str:
    """ISO 时间（可能带时区）→ 'YYYY-MM-DD HH:MM:SS' 东八区；不带时区按原样截断。"""
    from datetime import datetime, timedelta, timezone
    cn_tz = timezone(timedelta(hours=8))
    try:
        dt = datetime.fromisoformat(iso.replace("Z", "+00:00"))
    except ValueError:
        return iso[:19].replace("T", " ")
    if dt.tzinfo is None:
        return dt.strftime("%Y-%m-%d %H:%M:%S")
    return dt.astimezone(cn_tz).strftime("%Y-%m-%d %H:%M:%S")
