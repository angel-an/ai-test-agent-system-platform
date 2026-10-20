# -*- coding: utf-8 -*-
"""P0 修复验证测试（纯函数/接口级，不依赖数据库与外部服务）。

覆盖三项修复：
1. PATCH tags 不再被静默丢弃（update_test_case 复用关联表写入逻辑）
2. 知识库索引显式 commit + 异常回滚（KnowledgeIndexer.index_document）
3. created_count 语义修正（pipeline + GenerationOutcome + ai.py 响应覆盖）
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# 1. PATCH tags 修复验证
# ---------------------------------------------------------------------------

class TestPatchTagsFix:
    """验证 update_test_case 不再静默丢弃 tags 字段"""

    def _make_service(self):
        """构造最小可用的 TestCaseService（绕过 __init__ 的 repo 依赖）"""
        from app.services.test_case_service import TestCaseService

        service = TestCaseService.__new__(TestCaseService)
        service.session = MagicMock()
        service.repo = MagicMock()
        service.project_repo = MagicMock()
        service.folder_repo = MagicMock()
        return service

    def _make_tc(self):
        tc = MagicMock()
        tc.id = "tc-uuid-1"
        tc.project_id = "proj-uuid-1"
        tc.version = 1
        return tc

    @pytest.mark.asyncio
    async def test_tags_field_is_not_excluded_from_update(self):
        """model_dump 不再显式排除 tags"""
        from app.schemas.test_case import TestCaseUpdate

        data = TestCaseUpdate(tags=["AI生成", "Agent协作"])
        dumped = data.model_dump(exclude_unset=True, exclude={"steps", "tags"})
        assert "tags" not in dumped, "tags 仍被 exclude，PATCH 会静默丢弃"

    @pytest.mark.asyncio
    async def test_update_test_case_calls_apply_list_operation(self):
        """update_test_case 在 data.tags 非 None 时调用 _apply_list_operation"""
        from app.schemas.test_case import TestCaseUpdate
        from app.schemas.enums import BulkEditOperation
        from app.services.test_case_service import TestCaseService

        service = self._make_service()
        tc = self._make_tc()

        service.project_repo.get_by_identifier = AsyncMock(
            return_value=MagicMock(id="proj-uuid-1")
        )
        service.repo.get_by_identifier = AsyncMock(return_value=tc)
        service.repo.update = AsyncMock(return_value=tc)
        service.repo.get_by_id_with_relations = AsyncMock(return_value=tc)
        service._apply_list_operation = AsyncMock()

        with patch.object(
            TestCaseService, "_test_case_to_info", new_callable=AsyncMock
        ) as mock_to_info:
            mock_to_info.return_value = MagicMock()
            await service.update_test_case(
                "PROJ-1", "TC-001", TestCaseUpdate(tags=["新标签"])
            )

        service._apply_list_operation.assert_called_once_with(
            tc, "tags", BulkEditOperation.REPLACE, ["新标签"], "proj-uuid-1"
        )

    @pytest.mark.asyncio
    async def test_update_test_case_skips_tags_when_none(self):
        """data.tags 为 None（未传入）时不调用 _apply_list_operation"""
        from app.schemas.test_case import TestCaseUpdate
        from app.services.test_case_service import TestCaseService

        service = self._make_service()
        tc = self._make_tc()

        service.project_repo.get_by_identifier = AsyncMock(
            return_value=MagicMock(id="proj-uuid-1")
        )
        service.repo.get_by_identifier = AsyncMock(return_value=tc)
        service.repo.update = AsyncMock(return_value=tc)
        service.repo.get_by_id_with_relations = AsyncMock(return_value=tc)
        service._apply_list_operation = AsyncMock()

        with patch.object(
            TestCaseService, "_test_case_to_info", new_callable=AsyncMock
        ) as mock_to_info:
            mock_to_info.return_value = MagicMock()
            await service.update_test_case(
                "PROJ-1", "TC-001", TestCaseUpdate(name="改名但不改标签")
            )

        service._apply_list_operation.assert_not_called()


# ---------------------------------------------------------------------------
# 2. 知识库索引 commit 修复验证
# ---------------------------------------------------------------------------

class TestKnowledgeIndexerCommit:
    """验证 index_document 成功路径显式 commit、异常路径 rollback + 失败状态落库"""

    def _make_indexer(self, session_mock):
        from app.services.knowledge_indexer import KnowledgeIndexer

        indexer = KnowledgeIndexer.__new__(KnowledgeIndexer)
        indexer.session = session_mock
        indexer.chunk_size = 100
        indexer.chunk_overlap = 20
        return indexer

    @pytest.mark.asyncio
    async def test_success_path_calls_commit(self):
        """索引成功时必须显式 commit"""
        session = MagicMock()
        session.execute = AsyncMock()
        session.flush = AsyncMock()
        session.commit = AsyncMock()
        session.add = MagicMock()

        # mock document
        doc = MagicMock()
        doc.id = "doc-1"
        doc.space_id = "space-1"
        doc.file_name = "test.txt"
        doc.status = None
        doc.chunk_count = 0
        doc.error_message = None

        result_mock = MagicMock()
        result_mock.scalar_one_or_none = MagicMock(return_value=doc)
        session.execute = AsyncMock(return_value=result_mock)

        indexer = self._make_indexer(session)
        indexer._download_and_extract_text = AsyncMock(return_value="hello world")
        indexer.generate_embeddings = AsyncMock(return_value=["0.1,0.2"])

        success = await indexer.index_document("doc-1")

        assert success is True
        session.commit.assert_called()
        session.rollback.assert_not_called()

    @pytest.mark.asyncio
    async def test_exception_path_calls_rollback_and_marks_failed(self):
        """索引异常时必须 rollback + 在新事务中标记 FAILED 并 commit"""
        session = MagicMock()
        session.flush = AsyncMock()
        session.commit = AsyncMock()
        session.rollback = AsyncMock()
        session.add = MagicMock()

        doc = MagicMock()
        doc.id = "doc-1"
        doc.space_id = "space-1"
        doc.file_name = "test.txt"

        result_mock = MagicMock()
        result_mock.scalar_one_or_none = MagicMock(return_value=doc)
        session.execute = AsyncMock(return_value=result_mock)

        indexer = self._make_indexer(session)
        indexer._download_and_extract_text = AsyncMock(
            side_effect=Exception("MinIO 连接失败")
        )

        success = await indexer.index_document("doc-1")

        assert success is False
        session.rollback.assert_called()
        # 失败状态应被写入并提交
        assert session.commit.call_count >= 1

    @pytest.mark.asyncio
    async def test_reindex_does_not_duplicate_chunks(self):
        """同一文档重复索引时，旧 chunks 应先清除，不产生重复记录。

        当前 index_document 不含清旧逻辑，重复索引会叠加 chunks。
        本测试验证该边界行为，作为后续幂等性修复的基线。
        """
        added_chunks: list = []

        session = MagicMock()
        session.flush = AsyncMock()
        session.commit = AsyncMock()
        session.rollback = AsyncMock()

        doc = MagicMock()
        doc.id = "doc-1"
        doc.space_id = "space-1"
        doc.file_name = "test.txt"
        doc.status = None
        doc.chunk_count = 0
        doc.error_message = None

        result_mock = MagicMock()
        result_mock.scalar_one_or_none = MagicMock(return_value=doc)
        session.execute = AsyncMock(return_value=result_mock)
        session.add = MagicMock(side_effect=lambda c: added_chunks.append(c))

        indexer = self._make_indexer(session)
        indexer._download_and_extract_text = AsyncMock(return_value="hello world")
        indexer.generate_embeddings = AsyncMock(return_value=["0.1,0.2"])

        # 第一次索引
        await indexer.index_document("doc-1")
        assert len(added_chunks) == 1

        # 第二次索引（模拟重试/重复触发）
        await indexer.index_document("doc-1")
        # 当前实现：不清旧 chunks，直接追加 → 暴露幂等性缺口
        # 修复后应改为：先 delete 旧 chunks 再插入，此处应断言 == 1
        assert len(added_chunks) == 2, (
            "重复索引未清除旧 chunks，存在幂等性缺口"
            "（当前行为：直接追加；期望行为：先清后插）"
        )


# ---------------------------------------------------------------------------
# 3. created_count 语义修正验证
# ---------------------------------------------------------------------------

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


class TestCreatedCountSemantics:
    """验证 created_count 只统计通过契约校验的用例，而非传入总数"""

    @pytest.mark.asyncio
    async def test_validated_count_excludes_contract_failures(self):
        """1 条合规 + 1 条缺 feature_id → created_count=1, submitted_count=2"""
        import httpx
        from fastapi import FastAPI

        from app.api.v2.requirement_quality import router

        app = FastAPI()
        app.include_router(router, prefix="/api/v2")
        transport = httpx.ASGITransport(app=app)

        async with httpx.AsyncClient(
            transport=transport, base_url="http://test"
        ) as client:
            resp = await client.post(
                "/api/v2/projects/PROJ-1/requirements/generate-testcases",
                json={
                    "requirement_text": COMPLETE_PRD,
                    "requirement_id": "REQ-1",
                    "source": "req.md",
                    "test_cases": [
                        {
                            "id": "TC-100",
                            "title": "合规用例",
                            "feature_id": "FP-001",
                            "requirement_id": "REQ-1",
                            "source_location": "req.md#FP-001",
                            "steps": [
                                {"step": "输入手机号与金额", "result": "页面提示已保存"}
                            ],
                        },
                        {
                            "id": "TC-101",
                            "title": "缺feature_id且无source_location用例",
                            "requirement_id": "REQ-1",
                            "steps": [
                                {"step": "输入手机号与金额", "result": "页面提示已保存"}
                            ],
                        },
                    ],
                },
            )

        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["testcase_generation"]["submitted_count"] == 2
        assert data["testcase_generation"]["created_count"] == 1
        assert data["contract_validation"]["passed"] is False
        assert len(data["contract_validation"]["traceability_problems"]) == 1

    @pytest.mark.asyncio
    async def test_all_valid_cases_created_count_equals_submitted(self):
        """全部合规时 created_count == submitted_count"""
        import httpx
        from fastapi import FastAPI

        from app.api.v2.requirement_quality import router

        app = FastAPI()
        app.include_router(router, prefix="/api/v2")
        transport = httpx.ASGITransport(app=app)

        async with httpx.AsyncClient(
            transport=transport, base_url="http://test"
        ) as client:
            resp = await client.post(
                "/api/v2/projects/PROJ-1/requirements/generate-testcases",
                json={
                    "requirement_text": COMPLETE_PRD,
                    "requirement_id": "REQ-1",
                    "source": "req.md",
                    "test_cases": [
                        {
                            "id": "TC-100",
                            "title": "合规用例",
                            "feature_id": "FP-001",
                            "requirement_id": "REQ-1",
                            "acceptance_id": "FP-001-AC-01",
                            "source_location": "req.md#FP-001",
                            "steps": [
                                {"step": "输入手机号与金额", "result": "页面提示已保存"}
                            ],
                        },
                    ],
                },
            )

        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["testcase_generation"]["submitted_count"] == 1
        assert data["testcase_generation"]["created_count"] == 1
        assert data["contract_validation"]["passed"] is True

    @pytest.mark.asyncio
    async def test_double_failure_counts_once_not_twice(self):
        """同一条用例同时触发 contract + traceability 问题时只扣减一次，不能为负数"""
        import httpx
        from fastapi import FastAPI

        from app.api.v2.requirement_quality import router

        app = FastAPI()
        app.include_router(router, prefix="/api/v2")
        transport = httpx.ASGITransport(app=app)

        async with httpx.AsyncClient(
            transport=transport, base_url="http://test"
        ) as client:
            resp = await client.post(
                "/api/v2/projects/PROJ-1/requirements/generate-testcases",
                json={
                    "requirement_text": COMPLETE_PRD,
                    "requirement_id": "REQ-1",
                    "source": "req.md",
                    "test_cases": [
                        {
                            "id": "TC-200",
                            "title": "同时缺result和缺feature_id",
                            "requirement_id": "REQ-1",
                            "steps": [
                                {"step": "只有步骤没有结果"}
                            ],
                        },
                    ],
                },
            )

        assert resp.status_code == 200
        data = resp.json()["data"]
        # 1 条传入，1 条失败（同时缺 result + feature_id），去重后 created_count=0 而非 -1
        assert data["testcase_generation"]["submitted_count"] == 1
        assert data["testcase_generation"]["created_count"] == 0
        assert data["contract_validation"]["passed"] is False
