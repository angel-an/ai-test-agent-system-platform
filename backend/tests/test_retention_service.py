"""对象存储保留期清理的门禁测试（2026-10-28）。

清理是**破坏性**动作，误删证据等于毁掉工单的举证材料。本文件锁住五条底线：

1. 默认 dry-run —— 不传 ``dry_run=False`` 一个对象都不删；
2. 按组保底 —— 每个 run / 每个脚本目录至少留最新 N 个（防整组被清空）；
3. 只碰归档 —— ``api-tests/`` 前缀下的**活脚本**不能被当成 history 删掉；
4. 单次上限 —— 超出 ``max_delete`` 只删最旧的到上限并标 ``truncated``；
5. 保留期下限 —— 传 0/负数按 1 天处理，不会变成"清空桶"。
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from app.services import retention_service as rs


NOW = datetime(2026, 10, 28, 12, 0, 0, tzinfo=timezone.utc)


def _obj(name: str, days_ago: int, size: int = 100) -> dict:
    return {
        "object_name": name,
        "size": size,
        "last_modified": NOW - timedelta(days=days_ago),
    }


@pytest.fixture()
def fake_store(monkeypatch):
    """按前缀返回预置对象，并记录 delete 调用。"""
    store: dict[str, list[dict]] = {}
    deleted: list[str] = []

    def _list(prefix: str = "", **_kw):
        return [o for k, v in store.items() if k == prefix for o in v]

    def _delete(name: str):
        deleted.append(name)

    monkeypatch.setattr(rs.MinIOClient, "list_objects", classmethod(lambda cls, prefix="", **kw: _list(prefix)))
    monkeypatch.setattr(rs.MinIOClient, "delete_file", classmethod(lambda cls, name: _delete(name)))
    return {"store": store, "deleted": deleted}


def test_dry_run_deletes_nothing(fake_store):
    fake_store["store"]["api-test-evidence/"] = [
        _obj("api-test-evidence/run-a/trace.zip", 90),
        _obj("api-test-evidence/run-a/shot.png", 80),
        _obj("api-test-evidence/run-a/new.png", 1),
    ]

    result = rs.execute_cleanup(targets=[rs.TARGET_EVIDENCE], now=NOW, dry_run=True)

    assert fake_store["deleted"] == []
    info = result["targets"][rs.TARGET_EVIDENCE]
    assert info["deleted"] == 0
    assert result["dry_run"] is True


def test_keep_recent_protects_group(fake_store):
    """同组里即使全过期，也要留下最新 keep_recent 个。"""
    fake_store["store"]["api-test-evidence/"] = [
        _obj(f"api-test-evidence/run-a/f{i}.png", 100 - i) for i in range(4)
    ]

    result = rs.execute_cleanup(
        targets=[rs.TARGET_EVIDENCE],
        retain_days={rs.TARGET_EVIDENCE: 30},
        keep_recent={rs.TARGET_EVIDENCE: 2},
        now=NOW,
        dry_run=True,
    )
    info = result["targets"][rs.TARGET_EVIDENCE]

    # 4 个全过期，保底 2 个 → 只剩 2 个可删
    assert info["kept_recent"] == 2
    assert info["deletable"] == 2
    names = [i["object_name"] for i in info["items"]]
    assert "api-test-evidence/run-a/f0.png" in names  # 最旧的先删
    assert "api-test-evidence/run-a/f3.png" not in names


def test_recent_objects_are_kept(fake_store):
    fake_store["store"]["api-test-evidence/"] = [
        _obj("api-test-evidence/run-a/old.zip", 60),
        _obj("api-test-evidence/run-a/today.zip", 0),
        _obj("api-test-evidence/run-a/yesterday.zip", 1),
    ]

    result = rs.execute_cleanup(
        targets=[rs.TARGET_EVIDENCE],
        retain_days={rs.TARGET_EVIDENCE: 30},
        keep_recent={rs.TARGET_EVIDENCE: 0},
        now=NOW,
        dry_run=True,
    )
    names = [i["object_name"] for i in result["targets"][rs.TARGET_EVIDENCE]["items"]]
    assert names == ["api-test-evidence/run-a/old.zip"]


def test_script_history_never_touches_live_scripts(fake_store):
    fake_store["store"]["api-tests/"] = [
        # 活脚本：没有 /history/ 段，即便很旧也不能删
        _obj("api-tests/PR-2/endpoints/ep-1/test-script.ts", 999),
        # 归档：按脚本目录分组
        _obj("api-tests/PR-2/endpoints/ep-1/history/20260101T000000-test-script.ts", 300),
        _obj("api-tests/PR-2/endpoints/ep-1/history/20260102T000000-test-script.ts", 290),
        # 另一个脚本目录只留 1 条归档，属保底范围
        _obj("api-tests/PR-2/endpoints/ep-2/history/20260101T000000-test-script.ts", 300),
    ]

    result = rs.execute_cleanup(
        targets=[rs.TARGET_SCRIPT_HISTORY],
        retain_days={rs.TARGET_SCRIPT_HISTORY: 90},
        keep_recent={rs.TARGET_SCRIPT_HISTORY: 1},
        now=NOW,
        dry_run=True,
    )
    info = result["targets"][rs.TARGET_SCRIPT_HISTORY]

    assert info["matched"] == 3  # 活脚本不计入
    assert info["groups"] == 2
    names = [i["object_name"] for i in info["items"]]
    assert "api-tests/PR-2/endpoints/ep-1/test-script.ts" not in names
    # ep-2 唯一一条归档被保底 → 只剩 ep-1 的最旧归档
    assert names == ["api-tests/PR-2/endpoints/ep-1/history/20260101T000000-test-script.ts"]


def test_max_delete_truncates_oldest_first(fake_store):
    fake_store["store"]["api-test-evidence/"] = [
        _obj(f"api-test-evidence/run-{i}/trace.zip", 100 + i) for i in range(5)
    ]

    result = rs.execute_cleanup(
        targets=[rs.TARGET_EVIDENCE],
        retain_days={rs.TARGET_EVIDENCE: 30},
        keep_recent={rs.TARGET_EVIDENCE: 0},
        max_delete=2,
        now=NOW,
        dry_run=True,
    )
    info = result["targets"][rs.TARGET_EVIDENCE]

    assert info["truncated"] is True
    assert info["deletable"] == 2  # 预算封顶
    days = [i["last_modified"] for i in info["items"]]
    assert days == sorted(days)  # 最旧的排在前面


def test_execute_deletes_when_not_dry_run(fake_store):
    fake_store["store"]["api-test-reports/"] = [
        _obj("api-test-reports/run-a/allure-report.zip", 100),
        _obj("api-test-reports/run-a/allure-report-v2.zip", 0),
    ]

    result = rs.execute_cleanup(
        targets=[rs.TARGET_REPORTS],
        retain_days={rs.TARGET_REPORTS: 30},
        keep_recent={rs.TARGET_REPORTS: 0},
        now=NOW,
        dry_run=False,
    )

    assert result["deleted_total"] == 1
    assert fake_store["deleted"] == ["api-test-reports/run-a/allure-report.zip"]
    assert result["targets"][rs.TARGET_REPORTS]["deleted"] == 1


def test_retain_days_below_minimum_is_clamped(fake_store):
    fake_store["store"]["api-test-evidence/"] = [
        _obj("api-test-evidence/run-a/old.zip", 2),
        {
            "object_name": "api-test-evidence/run-a/fresh.zip",
            "size": 100,
            # 12 小时前：若把保留期误传成 0 天，它会被当成过期一起删掉
            "last_modified": NOW - timedelta(hours=12),
        },
    ]
    result = rs.execute_cleanup(
        targets=[rs.TARGET_EVIDENCE],
        retain_days={rs.TARGET_EVIDENCE: 0},
        keep_recent={rs.TARGET_EVIDENCE: 0},
        now=NOW,
        dry_run=True,
    )
    info = result["targets"][rs.TARGET_EVIDENCE]
    assert info["retain_days"] == rs.MIN_RETAIN_DAYS
    names = [i["object_name"] for i in info["items"]]
    assert names == ["api-test-evidence/run-a/old.zip"]  # 12 小时前的被保住


def test_list_failure_is_reported_not_swallowed(monkeypatch):
    def _boom(cls, prefix="", **_kw):
        raise RuntimeError("minio down")

    monkeypatch.setattr(rs.MinIOClient, "list_objects", classmethod(_boom))
    result = rs.execute_cleanup(targets=[rs.TARGET_EVIDENCE], now=NOW, dry_run=True)

    assert result["errors"]
    assert "列举对象失败" in result["errors"][0]


def test_current_policy_exposes_defaults():
    policy = rs.current_policy()
    assert policy["default_dry_run"] is True
    assert policy["targets"][rs.TARGET_EVIDENCE]["retain_days"] == 30
    assert policy["targets"][rs.TARGET_SCRIPT_HISTORY]["retain_days"] == 90
