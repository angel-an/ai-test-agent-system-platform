"""
IDP 客户端异常场景单元测试

测试五类异常场景：
1. Token 过期 / 401 重试
2. 字段校验失败 (400/422)
3. IDP 5xx 异常
4. 重复缺陷创建
5. 项目映射失败

额外测试：
- 缺少 reqid
- IDP 创建成功后报告回写
"""

import json

import pytest
from unittest.mock import AsyncMock, patch, MagicMock

from app.services.idp_client import IDPClient
from app.services.idp_auth_service import IDPAuthService


class TestIDPClientErrors:
    """IDP 客户端异常场景测试"""

    @pytest.fixture(autouse=True)
    def reset_auth(self):
        """每个测试前重置认证状态"""
        IDPAuthService._token = None
        IDPAuthService._token_expires_at = None
        yield
        IDPAuthService._token = None
        IDPAuthService._token_expires_at = None

    @pytest.fixture
    def mock_token(self):
        """提供 mock token"""
        with patch("app.config.settings.settings.idp_token", "mock-test-token-12345"):
            yield

    @pytest.mark.asyncio
    async def test_token_missing_error(self):
        """测试 Token 缺失时的错误提示"""
        with patch("app.config.settings.settings.idp_token", ""):
            with patch("app.config.settings.settings.idp_username", ""):
                with patch("app.config.settings.settings.idp_password", ""):
                    client = IDPClient()
                    with pytest.raises(ValueError, match="Token 未配置"):
                        await client.get_project_info(1169)

    @pytest.mark.asyncio
    async def test_401_token_expired_no_retry(self, mock_token):
        """测试 401 Token 过期后直接报错（静态 Token 模式不重试）"""
        # 设置初始 Token
        IDPAuthService._token = "old-token"
        IDPAuthService._token_expires_at = None

        mock_response_401 = MagicMock()
        mock_response_401.status_code = 401
        mock_response_401.text = "Unauthorized"

        with patch("httpx.AsyncClient.request", new_callable=AsyncMock) as mock_request:
            mock_request.return_value = mock_response_401

            client = IDPClient()
            with pytest.raises(Exception, match="Token"):
                await client.get_project_info(1169)

            # 静态 Token 模式下只调用一次，不重试
            assert mock_request.call_count == 1

    @pytest.mark.asyncio
    async def test_401_token_invalid(self, mock_token):
        """测试 401 Token 无效时直接报错"""
        IDPAuthService._token = "invalid-token"
        IDPAuthService._token_expires_at = None

        mock_response = MagicMock()
        mock_response.status_code = 401
        mock_response.text = "Unauthorized"

        with patch("httpx.AsyncClient.request", new_callable=AsyncMock) as mock_request:
            mock_request.return_value = mock_response

            client = IDPClient()
            with pytest.raises(Exception, match="Token"):
                await client.get_project_info(1169)
            # 静态 Token 模式不重试
            assert mock_request.call_count == 1

    @pytest.mark.asyncio
    async def test_field_validation_error_400(self, mock_token):
        """测试字段校验失败 (400)"""
        mock_response = MagicMock()
        mock_response.status_code = 400
        mock_response.text = '{"failed":true,"code":"error.IssueRule.typeCode","message":"typeCode不能为空"}'
        mock_response.json.return_value = {
            "failed": True,
            "code": "error.IssueRule.typeCode",
            "message": "typeCode不能为空",
        }

        with patch("httpx.AsyncClient.request", new_callable=AsyncMock) as mock_request:
            mock_request.return_value = mock_response

            client = IDPClient()
            with pytest.raises(Exception, match="校验失败"):
                await client.create_issue(1169, {"summary": "test"})

    @pytest.mark.asyncio
    async def test_update_issue_uses_confirmed_collection_endpoint(self):
        """描述更新使用 IDP 已确认的集合 PUT 接口和乐观锁请求体。"""
        client = IDPClient()
        update_data = {
            "issueId": 912747,
            "objectVersionNumber": 2,
            "description": '[{"insert":"reqid: test\\n"}]',
        }

        with patch.object(client, "_request", new_callable=AsyncMock) as mock_request:
            mock_request.return_value = {"issueId": 912747}
            await client.update_issue(1169, update_data)

        mock_request.assert_awaited_once_with(
            "PUT",
            "/agile/v1/projects/1169/issues",
            json=update_data,
        )

    def test_generated_description_is_delta_operation_array(self):
        """自动创建的描述必须使用 IDP 接受的顶层 Delta 操作数组。"""
        from app.services.defect_decision_service import (
            DefectDecision,
            DefectDecisionResult,
            DefectPriority,
        )
        from app.services.idp_defect_service import IDPDefectService

        description = IDPDefectService._generate_description(
            decision=DefectDecisionResult(
                decision=DefectDecision.CREATE,
                priority=DefectPriority.MEDIUM,
                priority_code="priority-2",
                priority_id=2,
                error_type="status_mismatch",
                failure_summary="Expected 200 but got 500",
                reason="状态码不匹配",
            ),
            scenario_name="【券模板】列表分页",
            endpoint="/api/imarketing/v1/couponTemplate/list",
            method="GET",
            request_summary={"expected_status_code": 200},
            response_summary={"status_code": 500},
            reqid="test-reqid",
            test_run_id=MagicMock(),
            test_case_id=None,
            report_url=None,
            source_project_key="PR-2",
        )

        delta_ops = json.loads(description)
        assert isinstance(delta_ops, list)
        assert any("reqid: test-reqid" in op.get("insert", "") for op in delta_ops)

    @pytest.mark.asyncio
    async def test_idp_500_error(self, mock_token):
        """测试 IDP 5xx 异常"""
        mock_response = MagicMock()
        mock_response.status_code = 500
        mock_response.text = "Internal Server Error"

        with patch("httpx.AsyncClient.request", new_callable=AsyncMock) as mock_request:
            mock_request.return_value = mock_response

            client = IDPClient()
            with pytest.raises(Exception, match="服务器"):
                await client.get_project_info(1169)

    @pytest.mark.asyncio
    async def test_idp_503_error(self, mock_token):
        """测试 IDP 503 服务不可用"""
        mock_response = MagicMock()
        mock_response.status_code = 503
        mock_response.text = "Service Unavailable"

        with patch("httpx.AsyncClient.request", new_callable=AsyncMock) as mock_request:
            mock_request.return_value = mock_response

            client = IDPClient()
            with pytest.raises(Exception, match="服务器"):
                await client.create_issue(1169, {"summary": "test"})

    @pytest.mark.asyncio
    async def test_network_error(self, mock_token):
        """测试网络连接错误"""
        import httpx

        with patch("httpx.AsyncClient.request", new_callable=AsyncMock) as mock_request:
            mock_request.side_effect = httpx.ConnectError("Connection refused")

            client = IDPClient()
            with pytest.raises(Exception, match="请求失败"):
                await client.get_project_info(1169)


class TestIDPDefectDuplicate:
    """重复缺陷创建测试"""

    @pytest.mark.asyncio
    async def test_duplicate_defect_not_created(self):
        """测试重复缺陷不会重复创建"""
        from app.services.defect_fingerprint import DefectFingerprintService

        # 生成相同指纹两次
        fp1 = DefectFingerprintService.generate(
            source_project_key="PR-2",
            method="GET",
            url="/api/v1/users",
            error_type="status_mismatch",
            failure_summary="Expected 200 but got 500",
        )
        fp2 = DefectFingerprintService.generate(
            source_project_key="PR-2",
            method="GET",
            url="/api/v1/users",
            error_type="status_mismatch",
            failure_summary="Expected 200 but got 500",
        )

        # 指纹应该相同
        assert fp1 == fp2

    @pytest.mark.asyncio
    async def test_different_defects_different_fingerprints(self):
        """测试不同缺陷有不同指纹"""
        from app.services.defect_fingerprint import DefectFingerprintService

        fp1 = DefectFingerprintService.generate(
            source_project_key="PR-2",
            method="GET",
            url="/api/v1/users",
            error_type="status_mismatch",
            failure_summary="Expected 200 but got 500",
        )
        fp2 = DefectFingerprintService.generate(
            source_project_key="PR-2",
            method="POST",
            url="/api/v1/users",
            error_type="status_mismatch",
            failure_summary="Expected 200 but got 500",
        )

        # 不同方法应该产生不同指纹
        assert fp1 != fp2


class TestIDPProjectMappingFailure:
    """项目映射失败测试"""

    def test_resolve_unknown_project(self):
        """测试未知项目映射失败"""
        from app.services.idp_project_resolver import IDPProjectResolver

        IDPProjectResolver._mappings = {}
        IDPProjectResolver._loaded = True

        result = IDPProjectResolver.resolve("PR-UNKNOWN")
        assert result is None

    def test_resolve_disabled_project(self):
        """测试禁用项目映射失败"""
        from app.services.idp_project_resolver import IDPProjectResolver, IDPProjectMapping

        IDPProjectResolver._mappings = {
            "PR-DISABLED": IDPProjectMapping(
                source_project_key="PR-DISABLED",
                source_project_name="禁用项目",
                idp_project_id=9999,
                idp_project_name="禁用项目",
                apply_type="agile",
                bug_type_id=1,
                issue_type_id=3,
                type_code="bug",
                default_priority_id=2,
                default_priority_code="priority-2",
                default_sprint_id=0,
                default_epic_id=0,
                default_assignee_id=0,
                enabled=False,
            )
        }
        IDPProjectResolver._loaded = True

        result = IDPProjectResolver.resolve("PR-DISABLED")
        assert result is None


class TestReqidExtraction:
    """reqid 提取测试"""

    def test_extract_reqid_from_headers(self):
        """测试从请求头提取 reqid"""
        from app.services.idp_defect_service import IDPDefectService

        request_summary = {
            "headers": {"x-request-id": "req-123-abc"}
        }
        response_summary = {}

        reqid = IDPDefectService._extract_reqid(request_summary, response_summary)
        assert reqid == "req-123-abc"

    def test_extract_reqid_from_response_headers(self):
        """测试从响应头提取 reqid"""
        from app.services.idp_defect_service import IDPDefectService

        request_summary = {}
        response_summary = {
            "headers": {"trace-id": "trace-456-def"}
        }

        reqid = IDPDefectService._extract_reqid(request_summary, response_summary)
        assert reqid == "trace-456-def"

    def test_extract_reqid_not_found(self):
        """测试未找到 reqid"""
        from app.services.idp_defect_service import IDPDefectService

        reqid = IDPDefectService._extract_reqid(None, None)
        assert reqid == "未获取"

    def test_extract_reqid_from_response_body(self):
        """测试从响应体提取 reqid"""
        from app.services.idp_defect_service import IDPDefectService

        request_summary = {}
        response_summary = {
            "body": {"reqid": "body-req-789"}
        }

        reqid = IDPDefectService._extract_reqid(request_summary, response_summary)
        assert reqid == "body-req-789"


class TestDefectReportWriteback:
    """缺陷报告回写测试"""

    def test_generate_markdown_section(self):
        """测试 Markdown 报告回写"""
        from app.services.defect_report_service import DefectReportService

        summary = {
            "total": 3,
            "created": 1,
            "duplicate": 1,
            "skipped": 0,
            "sync_failed": 0,
            "pending": 0,
            "verified": 0,
            "written_back": 0,
            "insufficient_evidence": 0,
            "not_required": 1,
            "items": [
                {
                    "defect_title": "【自动化测试】【API】测试缺陷1",
                    "idp_issue_key": "BUG-123",
                    "idp_issue_url": "https://idp.example.com/issues/123",
                    "status": "已登记",
                    "status_code": "created",
                },
                {
                    "defect_title": "【自动化测试】【API】测试缺陷2",
                    "idp_issue_key": None,
                    "idp_issue_url": None,
                    "status": "跳过",
                    "status_code": "skipped",
                },
            ],
        }

        service = DefectReportService(None)
        markdown = service.generate_markdown_section(summary)

        assert "IDP 缺陷登记结果" in markdown
        assert "BUG-123" in markdown
        assert "【自动化测试】【API】测试缺陷1" in markdown
        assert "|" in markdown  # 表格格式

    def test_generate_html_section(self):
        """测试 HTML 报告回写"""
        from app.services.defect_report_service import DefectReportService

        summary = {
            "total": 1,
            "created": 1,
            "duplicate": 0,
            "skipped": 0,
            "sync_failed": 0,
            "pending": 0,
            "verified": 0,
            "written_back": 0,
            "insufficient_evidence": 0,
            "not_required": 0,
            "items": [
                {
                    "defect_title": "【自动化测试】【API】测试缺陷",
                    "idp_issue_key": "BUG-456",
                    "idp_issue_url": "https://idp.example.com/issues/456",
                    "status": "已登记",
                    "status_code": "created",
                },
            ],
        }

        service = DefectReportService(None)
        html = service.generate_html_section(summary)

        assert "idp-defect-section" in html
        assert "BUG-456" in html
        assert "badge-success" in html  # created 状态的颜色类


class TestAllowCreateGuard:
    """allow_create=False 安全条件测试

    确保缺少完整证据时，即使 auto_create_enabled=true，
    也不会调用 IDP 创建接口。
    """

    @pytest.mark.asyncio
    async def test_allow_create_false_never_calls_idp(self):
        """allow_create=False 时，IDPClient.create_issue 调用次数必须为 0

        注入有效项目映射，确保流程走到 allow_create 守卫，
        断言：记录被创建为 pending、IDP 接口从未被调用。
        """
        from app.services.idp_defect_service import IDPDefectService
        from app.services.defect_decision_service import DefectDecisionResult, DefectDecision, DefectPriority
        from app.services.idp_project_resolver import IDPProjectResolver

        # 重置解析器缓存
        IDPProjectResolver._mappings = {}
        IDPProjectResolver._loaded = False

        mock_session = MagicMock()
        mock_session.execute = AsyncMock()
        mock_session.commit = AsyncMock()

        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = None
        mock_result.scalars.return_value.all.return_value = []
        mock_session.execute.return_value = mock_result

        service = IDPDefectService(mock_session)

        # 注入有效项目映射，确保流程走到 allow_create 守卫
        with patch.object(
            IDPProjectResolver, "resolve", return_value=MagicMock(
                idp_project_id=1169,
                type_code="bug",
                issue_type_id=3,
                default_sprint_id=0,
                default_epic_id=0,
                default_assignee_id=0,
            )
        ):
            # 强制启用自动创建（模拟生产环境配置）
            with patch.object(service, "auto_create_enabled", True):
                with patch.object(service, "dry_run", False):
                    # Mock IDPClient.create_issue，记录调用次数
                    with patch.object(
                        service.idp_client, "create_issue", new_callable=AsyncMock
                    ) as mock_create:
                        # Mock record_repo 方法
                        mock_record = MagicMock()
                        mock_record.id = "test-record-id"
                        with patch.object(
                            service.record_repo, "create", new_callable=AsyncMock
                        ) as mock_repo_create:
                            mock_repo_create.return_value = mock_record

                            decision = DefectDecisionResult(
                                decision=DefectDecision.CREATE,
                                priority=DefectPriority.MEDIUM,
                                priority_code="priority-2",
                                priority_id=2,
                                error_type="status_mismatch",
                                failure_summary="Expected 200 but got 500",
                                reason="状态码不匹配",
                            )

                            result = await service.process_test_failure(
                                test_run_id=MagicMock(),
                                test_case_id=None,
                                source_project_key="PR-2",
                                decision=decision,
                                scenario_name="GET /api/v1/users",
                                endpoint="/api/v1/users",
                                method="GET",
                                request_summary={"url": "http://test", "method": "GET"},
                                response_summary={"status_code": 500},
                                report_url=None,
                                allow_create=False,  # 关键安全条件
                            )

        # 断言 1：record_repo.create 被调用一次（pending 记录落库）
        mock_repo_create.assert_called_once()
        # 断言 2：返回的记录就是 mock_record
        assert result == mock_record
        # 断言 3：IDP 创建接口从未被调用
        mock_create.assert_not_called()

    @pytest.mark.asyncio
    async def test_allow_create_true_may_call_idp(self):
        """allow_create=True（默认）且 auto_create_enabled=True 时，允许调用 IDP"""
        from app.services.idp_defect_service import IDPDefectService
        from app.services.defect_decision_service import DefectDecisionResult, DefectDecision, DefectPriority
        from app.services.idp_project_resolver import IDPProjectResolver

        # 重置解析器缓存，避免其他测试的映射残留
        IDPProjectResolver._mappings = {}
        IDPProjectResolver._loaded = False

        mock_session = MagicMock()
        mock_session.execute = AsyncMock()
        mock_session.commit = AsyncMock()

        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = None
        mock_result.scalars.return_value.all.return_value = []
        mock_session.execute.return_value = mock_result

        service = IDPDefectService(mock_session)

        # 注入项目映射，避免解析失败
        with patch.object(
            IDPProjectResolver, "resolve", return_value=MagicMock(
                idp_project_id=1169,
                type_code="bug",
                issue_type_id=3,
                default_sprint_id=0,
                default_epic_id=0,
                default_assignee_id=0,
            )
        ):
            with patch.object(service, "auto_create_enabled", True):
                with patch.object(service, "dry_run", True):  # dry_run 避免真实调用
                    with patch.object(
                        service.idp_client, "create_issue_dry_run", new_callable=AsyncMock
                    ) as mock_create_dry:
                        mock_create_dry.return_value = {
                            "issueId": 999999,
                            "issueNum": "TEST-001",
                        }

                        mock_record = MagicMock()
                        mock_record.id = "test-record-id"
                        with patch.object(
                            service.record_repo, "create", new_callable=AsyncMock
                        ) as mock_repo_create:
                            with patch.object(
                                service.record_repo, "update", new_callable=AsyncMock
                            ):
                                mock_repo_create.return_value = mock_record

                                decision = DefectDecisionResult(
                                    decision=DefectDecision.CREATE,
                                    priority=DefectPriority.MEDIUM,
                                    priority_code="priority-2",
                                    priority_id=2,
                                    error_type="status_mismatch",
                                    failure_summary="Expected 200 but got 500",
                                    reason="状态码不匹配",
                                )

                                await service.process_test_failure(
                                    test_run_id=MagicMock(),
                                    test_case_id=None,
                                    source_project_key="PR-2",
                                    decision=decision,
                                    scenario_name="GET /api/v1/users",
                                    endpoint="/api/v1/users",
                                    method="GET",
                                    request_summary={"url": "http://test", "method": "GET"},
                                    response_summary={"status_code": 500},
                                    report_url=None,
                                    allow_create=True,  # 默认行为
                                )

        # 断言：dry_run 模式下调用了 create_issue_dry_run
        mock_create_dry.assert_called_once()
