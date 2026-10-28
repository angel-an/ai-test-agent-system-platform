# -*- coding: utf-8 -*-
"""IDP 建单响应拿不到 issueId 时，不得把记录标成 ``created``。

背景：2026-09-30 07:21 产生过一条 ``create_status='created'`` 但
``idp_issue_id / idp_issue_key / idp_issue_url`` **全为空**的记录
（记录 ``e2d6142b``，早于 ``idp_defect_service`` 对 IDP 响应做
camelCase/snake_case 双键兼容的那次修复）。

这类记录是"死记录"：没有单号 ⇒ 无法回读校验、无法关联、无法回写，
状态却显示"已创建"，统计上还会被算进"已创建待校验"。

本文件锁定修复后的行为：
1. 响应里没有 issueId ⇒ ``sync_failed`` + 可读原因（并把响应键写进原因里）；
2. 有 ``issueNum`` 但没有 issueId ⇒ 仍然失败，但单号仍然落库，便于人工排查；
3. 正常响应（camelCase / dry-run 的 snake_case 占位）不受影响，仍写 ``created``。
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.services import idp_defect_service as svc_module
from app.services.defect_decision_service import (
    DefectDecision,
    DefectDecisionResult,
    DefectPriority,
)
from app.services.idp_defect_service import IDPDefectService
from app.schemas.enums import IDPDefectStatus


class _FakeRepo:
    """只实现 process_test_failure 用到的四个方法。"""

    def __init__(self) -> None:
        self.created_kwargs: dict = {}
        self.updates: list[dict] = []

    async def get_by_fingerprint_and_run(self, fingerprint, run_id):
        return None

    async def get_duplicate_by_fingerprint(self, fingerprint, idp_project_id):
        return None

    async def create(self, **kwargs):
        self.created_kwargs = kwargs
        # 模拟 ORM 记录的完整字段（未写入的 IDP 字段是 None，不是不存在）
        return SimpleNamespace(
            id="rec-1",
            idp_issue_id=None,
            idp_issue_key=None,
            idp_issue_url=None,
            error_message=None,
            **kwargs,
        )

    async def update(self, instance, **kwargs):
        self.updates.append(kwargs)
        for key, value in kwargs.items():
            setattr(instance, key, value)
        return instance


class _FakeClient:
    def __init__(self, result: dict) -> None:
        self.result = result
        self.calls = 0

    async def create_issue(self, project_id, issue_data):
        self.calls += 1
        return self.result

    async def create_issue_dry_run(self, project_id, issue_data):
        self.calls += 1
        return self.result


def _decision() -> DefectDecisionResult:
    return DefectDecisionResult(
        decision=DefectDecision.CREATE,
        priority=DefectPriority.HIGH,
        priority_code="priority-1",
        priority_id=1,
        error_type="test_failed",
        failure_summary="modify 接受了已被占用的商品 11281（TCCS001）",
        reason="检测到缺陷",
        defect_type="数据校验缺失",
    )


def _mapping() -> SimpleNamespace:
    return SimpleNamespace(
        source_project_key="PR-2",
        idp_project_id=1169,
        apply_type="agile",
        type_code="bug",
        issue_type_id=3,
        default_priority_id=2,
        default_priority_code="priority-2",
        default_sprint_id=None,
        default_epic_id=None,
        default_assignee_id=None,
        enabled=True,
    )


@pytest.fixture()
def service(monkeypatch):
    """构造一个不碰网络、不碰数据库的 IDPDefectService。"""
    monkeypatch.setattr(
        svc_module.IDPProjectResolver, "resolve", staticmethod(lambda key: _mapping())
    )

    def _build(result: dict) -> tuple[IDPDefectService, _FakeRepo]:
        svc = IDPDefectService(session=None)  # type: ignore[arg-type]
        repo = _FakeRepo()
        svc.record_repo = repo
        svc.idp_client = _FakeClient(result)
        svc.dry_run = False
        svc.auto_create_enabled = True
        # 描述渲染与本次判据无关，替换掉以隔离模板细节
        svc._generate_description = lambda **kwargs: "desc"  # type: ignore[assignment]
        return svc, repo

    return _build


async def _run(svc: IDPDefectService):
    return await svc.process_test_failure(
        test_run_id="11111111-1111-1111-1111-111111111111",
        test_case_id=None,
        source_project_key="PR-2",
        decision=_decision(),
        scenario_name="限购商品管理-负路径",
        endpoint="/v1/purchaseLimit/modify",
        method="PUT",
        request_summary={"body": {"id": "9", "goods_ids": "11165,11281"}},
        response_summary={"status_code": 200, "body": {"resultMsg": "success"}},
    )


@pytest.mark.asyncio
async def test_missing_issue_id_is_sync_failed_not_created(service):
    """响应里完全没有单号 → 不能标 created。"""
    svc, repo = service({"success": True, "message": "ok"})

    record = await _run(svc)

    assert record.create_status == IDPDefectStatus.SYNC_FAILED.value
    assert record.idp_issue_id is None
    assert "未返回 issueId" in (record.error_message or "")
    # 原因里要带上响应键，方便事后定位是哪一版字段名
    assert "success" in (record.error_message or "")
    assert all(u.get("create_status") != IDPDefectStatus.CREATED.value for u in repo.updates)


@pytest.mark.asyncio
async def test_issue_num_without_issue_id_still_fails_but_keeps_key(service):
    """只有 issueNum 没有 issueId：仍失败，但把单号留下供人工排查。"""
    svc, _ = service({"success": True, "issueNum": "jf-20260122392-01-3229"})

    record = await _run(svc)

    assert record.create_status == IDPDefectStatus.SYNC_FAILED.value
    assert record.idp_issue_id is None
    assert record.idp_issue_key == "jf-20260122392-01-3229"
    assert record.idp_issue_url is None


@pytest.mark.asyncio
async def test_camel_case_response_still_created(service):
    """真实 IDP 响应（camelCase）正常路径不被误伤。"""
    svc, _ = service({"issueId": 934812, "issueNum": "jf-20260122392-01-3229"})

    record = await _run(svc)

    assert record.create_status == IDPDefectStatus.CREATED.value
    assert record.idp_issue_id == 934812
    assert record.idp_issue_key == "jf-20260122392-01-3229"
    assert record.idp_issue_url.endswith("/agile/issues/934812")


@pytest.mark.asyncio
async def test_dry_run_snake_case_placeholder_still_created(service):
    """dry-run 的 999999 占位号是显式返回值，不算"缺单号"。"""
    svc, _ = service({"dry_run": True, "issue_id": 999999, "issue_key": "BUG-DRY-RUN"})

    record = await _run(svc)

    assert record.create_status == IDPDefectStatus.CREATED.value
    assert record.idp_issue_id == 999999
    assert record.idp_issue_key == "BUG-DRY-RUN"


@pytest.mark.asyncio
async def test_issue_id_zero_is_treated_as_missing(service):
    """issueId=0 与空等价，不能当有效单号。"""
    svc, _ = service({"issueId": 0, "issueNum": "jf-x"})

    record = await _run(svc)

    assert record.create_status == IDPDefectStatus.SYNC_FAILED.value
    assert record.idp_issue_id is None
