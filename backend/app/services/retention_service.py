"""对象存储保留期清理。

三处产物**只增不减**，跑得越久桶越胖：

- ``api-test-evidence/<run_id>/...``  失败证据（trace.zip、截图）
- ``api-test-reports/<run_id>/...``   Allure 报告 zip
- ``api-tests/.../history/<ts>-*.ts`` 脚本重建前归档（每次重建都留一份）

设计上的三条硬约束：

1. **默认 dry-run**：先出计划（哪些会被删、共多少字节），人看过再执行。
   误删证据等于毁掉工单的举证材料，不能靠"应该没问题"。
2. **按组保底**：证据/报告以 run 为组、脚本归档以"脚本目录"为组，
   每次至少保留最新 ``keep_recent`` 个 —— 防止一次时间口径错误把某次运行的
   全部证据清空（只剩半套证据比没有更容易误导）。
3. **单次上限**：``max_delete`` 封顶，超过就只删到上限并标 ``truncated``，
   避免 ``retain_days`` 传成 1 时一口气清空整个桶。
"""

from __future__ import annotations

import logging
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from app.config.minio_client import MinIOClient

logger = logging.getLogger(__name__)

TARGET_EVIDENCE = "evidence"
TARGET_REPORTS = "reports"
TARGET_SCRIPT_HISTORY = "script_history"

#: 每个清理目标的前缀与默认保留期（天）。
TARGET_CONFIG: dict[str, dict[str, Any]] = {
    TARGET_EVIDENCE: {
        "prefix": "api-test-evidence/",
        "retain_days": 30,
        "keep_recent": 2,
        "description": "失败证据（trace.zip / 截图），按 run 分组",
    },
    TARGET_REPORTS: {
        "prefix": "api-test-reports/",
        "retain_days": 30,
        "keep_recent": 1,
        "description": "Allure 报告 zip，按 run 分组",
    },
    TARGET_SCRIPT_HISTORY: {
        "prefix": "api-tests/",
        "retain_days": 90,
        "keep_recent": 5,
        "description": "脚本重建前归档（…/history/<ts>-*.ts），按脚本目录分组",
    },
}

#: 单次清理最多删多少个对象。
DEFAULT_MAX_DELETE = 200
#: 保留期下限：小于 1 天没有意义，且极易被误传成"清空"。
MIN_RETAIN_DAYS = 1
#: 计划里最多回显多少条明细（避免响应体被几百条路径撑爆）。
MAX_PLAN_ITEMS = 50


def _as_datetime(value: Any) -> Optional[datetime]:
    """把对象存储返回的 last_modified 统一成带时区的 datetime。"""
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    if isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
    return None


def _belongs_to_target(target: str, object_name: str) -> bool:
    """脚本归档前缀 ``api-tests/`` 下还有活脚本，只认 ``/history/`` 目录。"""
    if target == TARGET_SCRIPT_HISTORY:
        return "/history/" in object_name
    return True


def _group_key(target: str, object_name: str) -> str:
    """分组键：证据/报告按 run，脚本归档按脚本目录。"""
    if target == TARGET_SCRIPT_HISTORY:
        return object_name.split("/history/", 1)[0]
    parts = object_name.split("/")
    return "/".join(parts[:2])


def _resolve_days(target: str, overrides: Optional[dict]) -> int:
    raw = None
    if overrides:
        raw = overrides.get(target)
    if raw is None:
        raw = TARGET_CONFIG[target]["retain_days"]
    try:
        days = int(raw)
    except (TypeError, ValueError):
        days = int(TARGET_CONFIG[target]["retain_days"])
    if days < MIN_RETAIN_DAYS:
        logger.warning(
            "[Retention] %s 的保留期 %s 天低于下限，按 %s 天处理",
            target, raw, MIN_RETAIN_DAYS,
        )
        days = MIN_RETAIN_DAYS
    return days


def plan_cleanup(
    *,
    targets: Optional[list[str]] = None,
    retain_days: Optional[dict[str, int]] = None,
    keep_recent: Optional[dict[str, int]] = None,
    max_delete: int = DEFAULT_MAX_DELETE,
    now: Optional[datetime] = None,
) -> dict:
    """算出"该删哪些"，**不删任何东西**。

    返回::

        {"cutoff": {...}, "targets": {name: {scanned, matched, deletable,
         deletable_bytes, kept_recent, truncated, items:[...]}}, "errors": [...]}
    """
    now = now or datetime.now(timezone.utc)
    chosen = targets or list(TARGET_CONFIG)
    report: dict[str, Any] = {"scanned_at": now.isoformat(), "targets": {}, "errors": []}
    budget = max(0, int(max_delete))

    for target in chosen:
        conf = TARGET_CONFIG.get(target)
        if conf is None:
            report["errors"].append(f"未知清理目标: {target}")
            continue

        days = _resolve_days(target, retain_days)
        floor = int((keep_recent or {}).get(target, conf["keep_recent"]) or 0)
        cutoff = now - timedelta(days=days)

        try:
            objects = MinIOClient.list_objects(conf["prefix"])
        except Exception as exc:  # noqa: BLE001 —— 单个目标失败不影响其他目标
            report["errors"].append(f"{target}: 列举对象失败 {exc}")
            report["targets"][target] = {
                "prefix": conf["prefix"], "retain_days": days, "error": str(exc),
            }
            continue

        matched = [o for o in objects if _belongs_to_target(target, o["object_name"])]
        groups: dict[str, list[dict]] = defaultdict(list)
        for obj in matched:
            groups[_group_key(target, obj["object_name"])].append(obj)

        deletable: list[dict] = []
        kept_recent = 0
        for objs in groups.values():
            objs.sort(key=lambda o: _as_datetime(o.get("last_modified")) or now, reverse=True)
            for idx, obj in enumerate(objs):
                if idx < floor:
                    kept_recent += 1
                    continue
                ts = _as_datetime(obj.get("last_modified"))
                if ts is not None and ts >= cutoff:
                    continue
                deletable.append(obj)

        truncated = len(deletable) > budget
        if truncated:
            # 先删最旧的：列表内按时间正序排，只保留预算内的
            deletable.sort(key=lambda o: _as_datetime(o.get("last_modified")) or now)
            deletable = deletable[:budget]
            budget -= len(deletable)
        else:
            budget -= len(deletable)

        report["targets"][target] = {
            "prefix": conf["prefix"],
            "description": conf["description"],
            "retain_days": days,
            "keep_recent": floor,
            "cutoff": cutoff.isoformat(),
            "scanned": len(objects),
            "matched": len(matched),
            "groups": len(groups),
            "kept_recent": kept_recent,
            "deletable": len(deletable),
            "deletable_bytes": sum(int(o.get("size") or 0) for o in deletable),
            "truncated": truncated,
            # 完整清单留给 execute_cleanup 用；回显只取前 MAX_PLAN_ITEMS 条
            "_pending": deletable,
            "items": [
                {
                    "object_name": o["object_name"],
                    "size": o.get("size"),
                    "last_modified": (
                        _as_datetime(o.get("last_modified")).isoformat()
                        if _as_datetime(o.get("last_modified")) else None
                    ),
                }
                for o in deletable[:MAX_PLAN_ITEMS]
            ],
        }

    return report


def execute_cleanup(
    *,
    targets: Optional[list[str]] = None,
    retain_days: Optional[dict[str, int]] = None,
    keep_recent: Optional[dict[str, int]] = None,
    max_delete: int = DEFAULT_MAX_DELETE,
    dry_run: bool = True,
    now: Optional[datetime] = None,
) -> dict:
    """出计划，``dry_run=False`` 时才真的删。"""
    plan = plan_cleanup(
        targets=targets,
        retain_days=retain_days,
        keep_recent=keep_recent,
        max_delete=max_delete,
        now=now,
    )
    plan["dry_run"] = dry_run

    deleted_total = 0
    bytes_total = 0
    errors: list[str] = list(plan["errors"])

    for target, info in plan["targets"].items():
        info["deleted"] = 0
        pending = info.pop("_pending", []) or []
        if dry_run:
            continue
        for item in pending:
            try:
                MinIOClient.delete_file(item["object_name"])
                info["deleted"] += 1
                deleted_total += 1
                bytes_total += int(item.get("size") or 0)
            except Exception as exc:  # noqa: BLE001
                msg = f"{target}: 删除失败 {item['object_name']} -> {exc}"
                logger.warning("[Retention] %s", msg)
                errors.append(msg)

    plan["deleted_total"] = deleted_total
    plan["deleted_bytes"] = bytes_total
    plan["errors"] = errors
    return plan


def current_policy() -> dict:
    """当前生效的默认保留策略（供接口回显，便于和运维对齐口径）。"""
    return {
        "targets": {
            name: {
                "prefix": conf["prefix"],
                "retain_days": conf["retain_days"],
                "keep_recent": conf["keep_recent"],
                "description": conf["description"],
            }
            for name, conf in TARGET_CONFIG.items()
        },
        "max_delete": DEFAULT_MAX_DELETE,
        "min_retain_days": MIN_RETAIN_DAYS,
        "default_dry_run": True,
    }
