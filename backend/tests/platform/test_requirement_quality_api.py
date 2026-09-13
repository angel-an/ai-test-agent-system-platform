# -*- coding: utf-8 -*-
"""需求质量门控接口测试（接口级，不依赖数据库与认证）。

验证 API 契约：
    {
      "prd_quality": {...},
      "analysis_report": {...},
      "testcase_generation": {"status": "not_started|blocked|generated|rejected"}
    }

通过直接挂载路由 + httpx ASGITransport 调用，避免引入项目的认证/DB 依赖。
"""

from __future__ import annotations

import json

import httpx
import pytest
from fastapi import FastAPI

from app.api.v2.requirement_quality import router


COMPLETE_PRD = """# 会员储值充值需求

优先级：P1

## 功能点清单

| 编号 | 名称 |
| --- | --- |
| FP-001 | 企业储值充值 |

### FP-001 企业储值充值

业务目标：企业客户可在后台为企业账户充值余额。
角色权限：企业管理员可操作；无权限用户访问时提示权限不足。
前置条件：已登录企业控制台并开通储值账户。
操作流程：进入储值账户列表，点击创建储值充值，输入手机号与充值金额后保存。
输入与数据规则：手机号必填；金额必填，最多两位小数。
业务规则：单笔充值金额上限 50000 元，超出时提示金额超限。
正常结果：保存成功并生成储值流水。
验收标准：充值成功后余额增加对应金额，流水记录可查询。
计算公式：充值后余额 = 原余额 + 充值金额，精度保留两位小数（四舍五入）。
状态流转：待支付 → 支付成功 → 已入账。
"""

INCOMPLETE_PRD = """# 会员积分查询需求

## 功能点

### FP-001 会员积分查询

业务目标：门店店员可查询会员当前可用积分。
角色权限：门店店员可操作；无权限用户访问时提示权限不足。
前置条件：已登录门店端。
操作流程：进入会员中心，输入手机号，点击查询。
输入与数据规则：手机号必填。
业务规则：积分按整数显示。
正常结果：页面展示会员当前可用积分。
"""

BLOCKED_PRD = """# 标签管理

### FP-001 标签管理

角色权限：运营人员可操作；无权限用户访问时提示权限不足。
前置条件：已登录后台。
操作流程：进入标签列表，点击新建标签，填写名称后保存。
输入与数据规则：标签名称必填。
业务规则：标签名称不可重复。
正常结果：列表展示新建标签。
验收标准：新建标签后可在列表查询到。
"""


@pytest.fixture()
def client() -> httpx.AsyncClient:
    app = FastAPI()
    app.include_router(router, prefix="/api/v2")
    transport = httpx.ASGITransport(app=app)
    return httpx.AsyncClient(transport=transport, base_url="http://test")


def _url(path: str) -> str:
    return f"/api/v2/projects/PROJ-1/requirements{path}"


class TestAnalyzeEndpoint:
    async def test_complete_requirement_can_generate(self, client):
        async with client:
            resp = await client.post(
                _url("/analyze"),
                json={"requirement_text": COMPLETE_PRD, "requirement_id": "REQ-1"},
            )
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["prd_quality"]["status"] == "Complete"
        assert data["prd_quality"]["can_generate_testcases"] is True
        assert data["testcase_generation"]["status"] == "not_started"

    async def test_incomplete_requirement_is_reported_not_failed(self, client):
        async with client:
            resp = await client.post(
                _url("/analyze"), json={"requirement_text": INCOMPLETE_PRD}
            )
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["prd_quality"]["status"] == "Incomplete"
        assert data["prd_quality"]["can_generate_testcases"] is False
        assert data["testcase_generation"]["status"] == "blocked"
        assert data["analysis_report"]["missing_items"]

    async def test_blocked_requirement_exposes_reasons(self, client):
        async with client:
            resp = await client.post(
                _url("/analyze"), json={"requirement_text": BLOCKED_PRD}
            )
        data = resp.json()["data"]
        assert data["prd_quality"]["status"] == "Blocked"
        assert data["analysis_report"]["blocked_reasons"]
        assert data["testcase_generation"]["reasons"]

    async def test_analysis_returns_markdown_report(self, client):
        async with client:
            resp = await client.post(
                _url("/analyze"), json={"requirement_text": BLOCKED_PRD}
            )
        markdown = resp.json()["data"]["analysis_report_markdown"]
        assert "需求质量分析报告" in markdown
        assert "业务目标" in markdown

    async def test_broken_structured_payload_blocks_generation(self, client):
        async with client:
            resp = await client.post(
                _url("/analyze"),
                json={
                    "requirement_text": COMPLETE_PRD,
                    "analysis_payload": "{not-json",
                },
            )
        data = resp.json()["data"]
        assert data["prd_quality"]["status"] == "Incomplete"
        assert data["testcase_generation"]["status"] == "blocked"
        assert data["analysis_report"]["parse_error"]


class TestGenerateEndpoint:
    async def test_blocked_requirement_generates_nothing(self, client):
        async with client:
            resp = await client.post(
                _url("/generate-testcases"),
                json={
                    "requirement_text": BLOCKED_PRD,
                    "test_cases": [
                        {"id": "TC-1", "title": "不该被接收",
                         "steps": [{"step": "a", "result": "b"}]}
                    ],
                },
            )
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["testcase_generation"]["status"] == "blocked"
        assert data["testcase_generation"]["created_count"] == 0
        assert data["cases"] == []
        assert "coverage" not in data

    async def test_complete_requirement_validates_and_tracks(self, client):
        async with client:
            resp = await client.post(
                _url("/generate-testcases"),
                json={
                    "requirement_text": COMPLETE_PRD,
                    "requirement_id": "REQ-1",
                    "source": "req.md",
                    "test_cases": [
                        {
                            "id": "TC-100",
                            "title": "企业储值充值-正向",
                            "feature_id": "FP-001",
                            "requirement_id": "REQ-1",
                            "acceptance_id": "FP-001-AC-01",
                            "source_location": "req.md#FP-001",
                            "steps": [{"step": "输入手机号与金额", "result": "页面提示已保存，并生成一条储值流水记录"}],
                        }
                    ],
                },
            )
        data = resp.json()["data"]
        assert data["testcase_generation"]["status"] == "generated"
        assert data["testcase_generation"]["created_count"] == 1
        assert data["contract_validation"]["passed"] is True
        assert data["coverage"]["covered_feature_count"] == 1
        assert data["requirements"]["features"][0]["case_count"] == 1

    async def test_untraced_new_case_is_reported(self, client):
        async with client:
            resp = await client.post(
                _url("/generate-testcases"),
                json={
                    "requirement_text": COMPLETE_PRD,
                    "test_cases": [
                        {"id": "TC-X", "title": "缺追踪",
                         "steps": [{"step": "a", "result": "b"}]}
                    ],
                },
            )
        data = resp.json()["data"]
        assert data["contract_validation"]["passed"] is False
        assert data["contract_validation"]["traceability_problems"]


class TestCoverageEndpoint:
    async def test_coverage_reports_gaps(self, client):
        async with client:
            resp = await client.post(
                _url("/coverage"),
                json={
                    "requirement_text": COMPLETE_PRD,
                    "requirement_id": "REQ-1",
                    "test_cases": [
                        {
                            "id": "TC-100",
                            "title": "t",
                            "feature_id": "FP-001",
                            "requirement_id": "REQ-1",
                            "steps": [{"step": "a", "result": "b"}],
                        },
                        {"id": "TC-OLD", "title": "旧用例",
                         "steps": [{"step": "a", "result": "b"}]},
                    ],
                },
            )
        data = resp.json()["data"]
        assert data["coverage"]["feature_count"] >= 1
        assert data["compatibility"]["legacy_case_ids"] == ["TC-OLD"]
        assert data["coverage"]["traced_case_count"] == 1

    async def test_query_cases_by_feature(self, client):
        async with client:
            resp = await client.post(
                _url("/coverage"),
                json={
                    "requirement_text": COMPLETE_PRD,
                    "feature_id": "FP-001",
                    "test_cases": [
                        {"id": "TC-100", "title": "t1", "feature_id": "FP-001",
                         "requirement_id": "REQ-1", "steps": [{"step": "a", "result": "b"}]},
                        {"id": "TC-101", "title": "t2", "feature_id": "FP-001",
                         "requirement_id": "REQ-1", "steps": [{"step": "a", "result": "b"}]},
                    ],
                },
            )
        data = resp.json()["data"]
        assert [c["id"] for c in data["cases_by_feature"]] == ["TC-100", "TC-101"]


class TestCompatibilityEndpoint:
    async def test_legacy_case_conversion(self, client):
        legacy = {
            "id": "TC-186482",
            "title": "B端储值免单活动新建入口冒烟",
            "steps": [
                {"seq": 1, "action": "进入营销活动管理", "target": "活动管理菜单"},
                {"seq": 2, "action": "点击新建活动", "target": "新建按钮"},
            ],
            "expected_results": ["1. 打开新建活动页面", "2. 展示储值免单活动类型"],
        }
        async with client:
            resp = await client.post(
                _url("/compatibility"),
                json={
                    "test_cases": [legacy],
                    "feature_id": "FP-001",
                    "requirement_id": "REQ-1",
                },
            )
        data = resp.json()["data"]
        converted = data["converted_cases"][0]
        assert converted["steps"][0]["step"] == "进入营销活动管理 [活动管理菜单]"
        assert converted["steps"][0]["result"] == "1. 打开新建活动页面"
        assert converted["feature_id"] == "FP-001"
        assert data["classification"]["traced"] == ["TC-186482"]


class TestGateSessionEndpoint:
    async def test_session_status_roundtrip(self, client):
        async with client:
            await client.post(
                _url("/analyze"),
                json={"requirement_text": BLOCKED_PRD, "session_key": "S-1"},
            )
            status = await client.get(_url("/gate"), params={"session_key": "S-1"})
            cleared = await client.delete(_url("/gate"), params={"session_key": "S-1"})
            after = await client.get(_url("/gate"), params={"session_key": "S-1"})

        assert status.json()["data"]["status"] == "blocked"
        assert cleared.json()["data"]["cleared"] is True
        assert after.json()["data"]["exists"] is False
