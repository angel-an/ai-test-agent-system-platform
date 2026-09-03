"""
IDP 缺陷服务

负责：
- 生成缺陷标题（统一格式）
- 生成缺陷描述（富文本 Delta 格式）
- 提取 reqid
- 敏感信息脱敏
- 调用 IDP 客户端创建缺陷
- 保存缺陷记录到数据库
"""

import json
import logging
import re
from datetime import datetime, timezone
from typing import Any, Optional
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.config.settings import settings
from app.models.idp_defect_record import IDPDefectRecord
from app.repositories.idp_defect_repo import IDPDefectRecordRepository
from app.schemas.enums import IDPDefectStatus
from app.services.defect_decision_service import DefectDecisionResult, DefectPriority
from app.services.defect_fingerprint import DefectFingerprintService
from app.services.idp_client import IDPClient
from app.services.idp_project_resolver import IDPProjectResolver

logger = logging.getLogger(__name__)


class IDPDefectService:
    """
    IDP 缺陷服务

    处理缺陷的生成、创建和记录
    """

    def __init__(self, session: AsyncSession):
        self.session = session
        self.idp_client = IDPClient()
        self.record_repo = IDPDefectRecordRepository(session)
        self.dry_run = settings.idp_dry_run
        self.auto_create_enabled = settings.idp_auto_create_enabled

    async def process_test_failure(
        self,
        test_run_id: UUID,
        test_case_id: Optional[UUID],
        source_project_key: str,
        decision: DefectDecisionResult,
        scenario_name: str,
        endpoint: str,
        method: str,
        request_summary: Optional[dict],
        response_summary: Optional[dict],
        report_url: Optional[str] = None,
        allow_create: bool = True,
        source_type: str = "api",
    ) -> IDPDefectRecord:
        """
        处理测试失败，创建 IDP 缺陷

        Args:
            test_run_id: 测试运行 ID
            test_case_id: 测试用例 ID
            source_project_key: 本地项目标识符
            decision: 缺陷决策结果
            scenario_name: 测试场景名称
            endpoint: API 端点
            method: HTTP 方法
            request_summary: 请求摘要
            response_summary: 响应摘要
            report_url: 测试报告地址
            allow_create: 是否允许自动创建
            source_type: 来源类型 (api/web/security)

        Returns:
            IDPDefectRecord: 缺陷记录
        """
        # 1. 解析项目映射
        mapping = IDPProjectResolver.resolve(source_project_key)
        if not mapping:
            return await self._create_skipped_record(
                test_run_id=test_run_id,
                test_case_id=test_case_id,
                source_project_key=source_project_key,
                fingerprint="",
                reason="项目未匹配",
                decision=decision,
                source_type=source_type,
            )

        # 2. 生成缺陷指纹
        fingerprint = DefectFingerprintService.generate_from_api_result(
            source_project_key=source_project_key,
            method=method,
            endpoint=endpoint,
            error_type=decision.error_type,
            failure_summary=decision.failure_summary,
        )

        # 3. 检查同一次运行内是否已处理
        existing = await self.record_repo.get_by_fingerprint_and_run(
            fingerprint, test_run_id
        )
        if existing:
            logger.info(
                "[IDPDefectService] 同一次运行内已存在相同缺陷记录: %s",
                existing.id,
            )
            return existing

        # 4. 检查跨运行去重（如果之前已创建成功）
        duplicate = await self.record_repo.get_duplicate_by_fingerprint(
            fingerprint, mapping.idp_project_id
        )
        if duplicate:
            return await self._create_duplicate_record(
                test_run_id=test_run_id,
                test_case_id=test_case_id,
                source_project_key=source_project_key,
                idp_project_id=mapping.idp_project_id,
                fingerprint=fingerprint,
                duplicate_issue_id=duplicate.idp_issue_id,
                duplicate_issue_key=duplicate.idp_issue_key,
                duplicate_issue_url=duplicate.idp_issue_url,
                decision=decision,
                source_type=source_type,
            )

        # 5. 提取 reqid
        reqid = self._extract_reqid(request_summary, response_summary)

        # 6. 生成缺陷标题
        title = self._generate_title(
            scenario_name=scenario_name,
            endpoint=endpoint,
            method=method,
            failure_summary=decision.failure_summary,
            source_type=source_type,
        )

        # 7. 生成缺陷描述（Delta 格式）
        description = self._generate_description(
            decision=decision,
            scenario_name=scenario_name,
            endpoint=endpoint,
            method=method,
            request_summary=request_summary,
            response_summary=response_summary,
            reqid=reqid,
            test_run_id=test_run_id,
            test_case_id=test_case_id,
            report_url=report_url,
            source_project_key=source_project_key,
            source_type=source_type,
        )

        # 8. 创建缺陷记录（pending 状态）
        record = await self.record_repo.create(
            source_type=source_type,
            source_run_id=test_run_id,
            source_case_id=test_case_id,
            test_run_id=test_run_id if source_type == "api" else None,
            test_case_id=test_case_id if source_type == "api" else None,
            source_project_key=source_project_key,
            idp_project_id=mapping.idp_project_id,
            fingerprint=fingerprint,
            create_status=IDPDefectStatus.PENDING.value,
            reqid=reqid,
            defect_title=title,
            defect_priority=decision.priority.value,
            report_url=report_url,
        )

        # 9. 调用 IDP 创建缺陷（仅在 allow_create=True 且 auto_create_enabled 时）
        if not allow_create:
            logger.info(
                "[IDPDefectService] allow_create=False，记录保持 pending 状态，"
                "等待人工确认"
            )
            return record

        if not self.auto_create_enabled:
            logger.info("[IDPDefectService] 自动创建已禁用，记录保持 pending 状态")
            return record

        try:
            # 联调确认必填字段：typeCode, issueTypeId, priorityCode, priorityId
            issue_data = {
                "summary": title,
                "description": description,
                "typeCode": mapping.type_code,  # 联调确认必填: "bug"
                "issueTypeId": mapping.issue_type_id,  # 联调确认必填: 3
                "priorityCode": decision.priority_code,  # 联调确认必填: "priority-2"
                "priorityId": decision.priority_id,  # 联调确认必填: 2
            }

            # 可选字段（联调确认允许为空，但建议传入）
            if mapping.default_sprint_id:
                issue_data["sprintId"] = mapping.default_sprint_id
            if mapping.default_epic_id:
                issue_data["epicId"] = mapping.default_epic_id
            if mapping.default_assignee_id:
                issue_data["assigneeId"] = mapping.default_assignee_id

            if self.dry_run:
                result = await self.idp_client.create_issue_dry_run(
                    mapping.idp_project_id, issue_data
                )
            else:
                result = await self.idp_client.create_issue(
                    mapping.idp_project_id, issue_data
                )

            # 更新记录为 created 状态
            # 联调确认响应字段：issueId, issueNum
            issue_id = result.get("issueId")
            issue_key = result.get("issueNum")
            # 使用 IDP 前端地址生成可点击链接（优先使用前端 URL）
            web_base = getattr(settings, 'idp_web_base_url', settings.idp_base_url).rstrip('/')
            issue_url = f"{web_base}/agile/issues/{issue_id}" if issue_id else None

            updated = await self.record_repo.update(
                record,
                create_status=IDPDefectStatus.CREATED.value,
                idp_issue_id=issue_id,
                idp_issue_key=issue_key,
                idp_issue_url=issue_url,
            )

            logger.info(
                "[IDPDefectService] 缺陷创建成功: %s (IDP ID: %s)",
                issue_key,
                issue_id,
            )
            return updated

        except Exception as e:
            logger.exception("[IDPDefectService] 创建缺陷失败")
            updated = await self.record_repo.update(
                record,
                create_status=IDPDefectStatus.SYNC_FAILED.value,
                error_message=str(e)[:500],
            )
            return updated

    async def _create_skipped_record(
        self,
        test_run_id: UUID,
        test_case_id: Optional[UUID],
        source_project_key: str,
        fingerprint: str,
        reason: str,
        decision: DefectDecisionResult,
        source_type: str = "api",
    ) -> IDPDefectRecord:
        """创建跳过的记录"""
        return await self.record_repo.create(
            source_type=source_type,
            source_run_id=test_run_id,
            source_case_id=test_case_id,
            test_run_id=test_run_id if source_type == "api" else None,
            test_case_id=test_case_id if source_type == "api" else None,
            source_project_key=source_project_key,
            idp_project_id=0,
            fingerprint=fingerprint or "skipped",
            create_status=IDPDefectStatus.SKIPPED.value,
            error_message=reason,
            defect_title=decision.failure_summary[:200] if decision else None,
        )

    async def _create_duplicate_record(
        self,
        test_run_id: UUID,
        test_case_id: Optional[UUID],
        source_project_key: str,
        idp_project_id: int,
        fingerprint: str,
        duplicate_issue_id: Optional[int],
        duplicate_issue_key: Optional[str],
        duplicate_issue_url: Optional[str],
        decision: DefectDecisionResult,
        source_type: str = "api",
    ) -> IDPDefectRecord:
        """创建重复的记录"""
        return await self.record_repo.create(
            source_type=source_type,
            source_run_id=test_run_id,
            source_case_id=test_case_id,
            test_run_id=test_run_id if source_type == "api" else None,
            test_case_id=test_case_id if source_type == "api" else None,
            source_project_key=source_project_key,
            idp_project_id=idp_project_id,
            fingerprint=fingerprint,
            create_status=IDPDefectStatus.DUPLICATE.value,
            idp_issue_id=duplicate_issue_id,
            idp_issue_key=duplicate_issue_key,
            idp_issue_url=duplicate_issue_url,
            defect_title=decision.failure_summary[:200] if decision else None,
        )

    @staticmethod
    def _extract_reqid(
        request_summary: Optional[dict],
        response_summary: Optional[dict],
    ) -> str:
        """
        提取 reqid

        按以下顺序提取：
        1. reqid
        2. x-request-id
        3. trace-id
        4. 响应体中的 reqid
        5. 测试执行上下文

        未获取到时返回 "未获取"
        """
        # 从请求头中提取
        if request_summary:
            headers = request_summary.get("headers", {})
            for key in ["reqid", "x-request-id", "trace-id", "x-trace-id"]:
                value = headers.get(key) or headers.get(key.lower())
                if value:
                    return str(value)

        # 从响应头中提取
        if response_summary:
            headers = response_summary.get("headers", {})
            for key in ["reqid", "x-request-id", "trace-id", "x-trace-id"]:
                value = headers.get(key) or headers.get(key.lower())
                if value:
                    return str(value)

            # 从响应体中提取
            body = response_summary.get("body", {})
            if isinstance(body, dict):
                for key in ["reqid", "requestId", "traceId", "xRequestId"]:
                    value = body.get(key)
                    if value:
                        return str(value)

        return "未获取"

    @staticmethod
    def _generate_title(
        scenario_name: str,
        endpoint: str,
        method: str,
        failure_summary: str,
        source_type: str = "api",
    ) -> str:
        """
        生成缺陷标题

        统一格式：【自动化测试】【类型】【业务模块】缺陷现象
        类型：API / Web / 安全
        示例：【自动化测试】【API】【会员卡】分页参数 pageNum=9999 返回 500
        """
        # 从场景名称中提取业务模块
        module = IDPDefectService._extract_module(scenario_name)

        # 类型映射
        type_map = {
            "api": "API",
            "web": "Web",
            "security": "安全",
        }
        type_label = type_map.get(source_type, "API")

        # 生成缺陷现象描述
        phenomenon = IDPDefectService._extract_phenomenon(
            failure_summary, endpoint, method
        )

        title = f"【自动化测试】【{type_label}】【{module}】{phenomenon}"

        # 截断到 200 字符
        if len(title) > 200:
            title = title[:197] + "..."

        return title

    @staticmethod
    def _extract_module(scenario_name: str) -> str:
        """从场景名称中提取业务模块"""
        if not scenario_name:
            return "未知模块"

        # 尝试提取方括号或引号中的模块名
        match = re.search(r"[【\[]([^】\]]+)[】\]]", scenario_name)
        if match:
            return match.group(1)

        # 取前 10 个字符作为模块名
        return scenario_name[:10] if len(scenario_name) <= 10 else scenario_name[:10] + "..."

    @staticmethod
    def _extract_phenomenon(failure_summary: str, endpoint: str, method: str) -> str:
        """提取缺陷现象"""
        # 使用失败摘要作为现象
        phenomenon = failure_summary

        # 去除过长的部分
        if len(phenomenon) > 100:
            phenomenon = phenomenon[:97] + "..."

        return phenomenon

    @staticmethod
    def _generate_description(
        decision: DefectDecisionResult,
        scenario_name: str,
        endpoint: str,
        method: str,
        request_summary: Optional[dict],
        response_summary: Optional[dict],
        reqid: str,
        test_run_id: UUID,
        test_case_id: Optional[UUID],
        report_url: Optional[str],
        source_project_key: str,
        source_type: str = "api",
    ) -> str:
        """
        生成缺陷描述（Delta 格式）

        IDP description 使用富文本 Delta 字符串，由服务统一进行 JSON 转义
        包含完整的测试上下文信息，便于开发人员定位和修复问题
        支持 API / Web / 安全 三类测试
        """
        # 提取请求参数（脱敏）
        request_params = IDPDefectService._sanitize_request_params(request_summary)

        # 提取响应信息
        response_status = response_summary.get("status_code", "N/A") if response_summary else "N/A"
        response_body = IDPDefectService._sanitize_response_body(response_summary)
        response_headers = IDPDefectService._sanitize_headers(response_summary)

        # 提取请求头
        request_headers = IDPDefectService._sanitize_headers(request_summary)

        # 测试类型显示文本
        type_display = {"api": "API 自动化测试", "web": "Web 自动化测试", "security": "安全测试"}
        test_type_display = type_display.get(source_type, "API 自动化测试")

        # 构建 Delta 格式的描述
        delta_ops = []

        # ========== 问题概述 ==========
        delta_ops.append({"insert": "问题概述\n", "attributes": {"bold": True}})
        delta_ops.append({"insert": f"{decision.failure_summary}\n\n"})

        # ========== 测试类型 ==========
        delta_ops.append({"insert": "测试类型\n", "attributes": {"bold": True}})
        delta_ops.append({"insert": f"{test_type_display}\n\n"})

        # ========== 业务项目 ==========
        delta_ops.append({"insert": "业务项目\n", "attributes": {"bold": True}})
        delta_ops.append({"insert": f"{source_project_key} ({IDPDefectService._get_project_name(source_project_key)})\n\n"})

        # ========== 测试环境 ==========
        delta_ops.append({"insert": "测试环境\n", "attributes": {"bold": True}})
        base_url = request_summary.get("base_url", "N/A") if request_summary else "N/A"
        delta_ops.append({"insert": f"Base URL: {base_url}\n"})
        delta_ops.append({"insert": f"测试时间: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S')} UTC\n\n"})

        # ========== 复现步骤 ==========
        delta_ops.append({"insert": "复现步骤\n", "attributes": {"bold": True}})

        if source_type == "web":
            # Web 测试复现步骤
            screenshot_url = request_summary.get("screenshot_url") if request_summary else None
            delta_ops.append({"insert": f"1. 打开页面: {endpoint}\n"})
            delta_ops.append({"insert": f"2. 执行操作: {method}\n"})
            delta_ops.append({"insert": f"3. 观察页面结果\n"})
            if screenshot_url:
                delta_ops.append({"insert": f"4. 截图证据: {screenshot_url}\n"})
            delta_ops.append({"insert": "\n"})
        elif source_type == "security":
            # 安全测试复现步骤
            vuln_name = request_summary.get("vulnerability_name", "未知漏洞") if request_summary else "未知漏洞"
            reproduction = request_summary.get("reproduction_steps", "") if request_summary else ""
            delta_ops.append({"insert": f"1. 目标: {endpoint}\n"})
            delta_ops.append({"insert": f"2. 漏洞: {vuln_name}\n"})
            if reproduction:
                delta_ops.append({"insert": f"3. 复现步骤:\n{reproduction}\n"})
            delta_ops.append({"insert": "\n"})
        else:
            # API 测试复现步骤（默认）
            delta_ops.append({"insert": f"1. 发送 {method} 请求到 {endpoint}\n"})
            delta_ops.append({"insert": f"2. 使用以下请求参数\n"})
            delta_ops.append({"insert": f"3. 观察响应结果\n\n"})

        # ========== 预期结果 ==========
        delta_ops.append({"insert": "预期结果\n", "attributes": {"bold": True}})
        if source_type == "security":
            delta_ops.append({"insert": "不存在安全漏洞\n"})
            delta_ops.append({"insert": "输入已正确过滤和校验\n\n"})
        else:
            expected = request_summary.get("expected_status_code", "200 OK") if request_summary else "200 OK"
            delta_ops.append({"insert": f"HTTP 状态码: {expected}\n"})
            delta_ops.append({"insert": "响应结构符合接口契约\n"})
            delta_ops.append({"insert": "业务逻辑正确\n\n"})

        # ========== 实际结果 ==========
        delta_ops.append({"insert": "实际结果\n", "attributes": {"bold": True}})
        if source_type == "security":
            severity = request_summary.get("severity", "未知") if request_summary else "未知"
            delta_ops.append({"insert": f"风险等级: {severity}\n"})
            delta_ops.append({"insert": f"漏洞详情: {decision.failure_summary}\n\n"})
        else:
            delta_ops.append({"insert": f"HTTP 状态码: {response_status}\n"})
            delta_ops.append({"insert": f"错误类型: {decision.error_type}\n"})
            delta_ops.append({"insert": f"错误详情: {decision.failure_summary}\n\n"})

        # ========== 请求信息 ==========
        delta_ops.append({"insert": "请求信息\n", "attributes": {"bold": True}})
        delta_ops.append({"insert": f"Method: {method}\n"})
        delta_ops.append({"insert": f"URL: {endpoint}\n"})
        if base_url != "N/A":
            delta_ops.append({"insert": f"Base URL: {base_url}\n\n"})

        # 请求头
        if request_headers and request_headers != "N/A":
            delta_ops.append({"insert": "请求头:\n"})
            delta_ops.append({"insert": f"{request_headers}\n\n"})

        # 请求参数
        if source_type != "security":
            delta_ops.append({"insert": "请求参数:\n"})
            delta_ops.append({"insert": f"{request_params}\n\n"})

        # ========== 响应信息 ==========
        if source_type != "security":
            delta_ops.append({"insert": "响应信息\n", "attributes": {"bold": True}})
            delta_ops.append({"insert": f"HTTP 状态码: {response_status}\n"})

            # 响应头
            if response_headers and response_headers != "N/A":
                delta_ops.append({"insert": "响应头:\n"})
                delta_ops.append({"insert": f"{response_headers}\n\n"})

            # 响应体
            if response_body and response_body != "N/A":
                delta_ops.append({"insert": "响应体:\n"})
                delta_ops.append({"insert": f"{response_body}\n\n"})

        # ========== 追踪信息 ==========
        delta_ops.append({"insert": "追踪信息\n", "attributes": {"bold": True}})
        delta_ops.append({"insert": f"reqid: {reqid}\n"})
        delta_ops.append({"insert": f"测试运行 ID: {test_run_id}\n"})
        if test_case_id:
            delta_ops.append({"insert": f"测试用例 ID: {test_case_id}\n"})
        if report_url:
            delta_ops.append({"insert": f"测试报告地址: {report_url}\n"})
        delta_ops.append({"insert": "\n"})

        # ========== 优先级依据 ==========
        delta_ops.append({"insert": "优先级依据\n", "attributes": {"bold": True}})
        delta_ops.append({"insert": f"优先级: {decision.priority.value} ({decision.priority_code})\n"})
        delta_ops.append({"insert": f"判定理由: {decision.reason}\n\n"})

        # ========== 影响分析 ==========
        delta_ops.append({"insert": "影响分析\n", "attributes": {"bold": True}})
        if decision.priority == DefectPriority.HIGH:
            delta_ops.append({"insert": "该问题可能影响服务稳定性或数据正确性，建议优先处理。\n\n"})
        elif decision.priority == DefectPriority.MEDIUM:
            delta_ops.append({"insert": "该问题影响功能正常使用，建议尽快修复。\n\n"})
        else:
            delta_ops.append({"insert": "该问题影响较小，可按计划修复。\n\n"})

        # 转换为 JSON 字符串
        # IDP 富文本字段接受 Quill Delta 操作数组的 JSON 字符串，而非 {"ops": ...} 包装对象。
        return json.dumps(delta_ops, ensure_ascii=False)

    @staticmethod
    def _sanitize_request_params(request_summary: Optional[dict]) -> str:
        """
        脱敏请求参数

        隐藏敏感信息：Token、密码、Cookie 等
        """
        if not request_summary:
            return "N/A"

        params = {}

        # 复制请求参数
        body = request_summary.get("body", {})
        if isinstance(body, dict):
            params.update(body)

        headers = request_summary.get("headers", {})
        if isinstance(headers, dict):
            params.update(headers)

        # 脱敏处理
        sanitized = {}
        sensitive_keys = [
            "password", "token", "auth", "authorization", "cookie",
            "secret", "key", "api_key", "apikey", "access_token",
            "refresh_token", "credential", "credentials",
        ]

        for k, v in params.items():
            if any(s in k.lower() for s in sensitive_keys):
                sanitized[k] = "***REDACTED***"
            else:
                sanitized[k] = v

        return json.dumps(sanitized, ensure_ascii=False, indent=2)

    @staticmethod
    def _sanitize_response_body(response_summary: Optional[dict]) -> str:
        """脱敏响应体"""
        if not response_summary:
            return "N/A"

        body = response_summary.get("body", {})
        if not body:
            return "N/A"

        if isinstance(body, dict):
            # 脱敏处理
            sensitive_keys = ["token", "password", "secret"]
            sanitized = {}
            for k, v in body.items():
                if any(s in k.lower() for s in sensitive_keys):
                    sanitized[k] = "***REDACTED***"
                else:
                    sanitized[k] = v
            return json.dumps(sanitized, ensure_ascii=False, indent=2)

        return str(body)[:500]

    @staticmethod
    def _get_project_name(source_project_key: str) -> str:
        """
        根据项目标识符获取项目名称

        目前支持的项目映射：
        - PR-2: 小杨生煎储值免单活动
        """
        project_names = {
            "PR-2": "小杨生煎储值免单活动",
        }
        return project_names.get(source_project_key, "未知项目")

    @staticmethod
    def _sanitize_headers(summary: Optional[dict]) -> str:
        """
        脱敏请求头或响应头

        隐藏敏感信息：Authorization、Cookie、Token 等
        """
        if not summary:
            return "N/A"

        headers = summary.get("headers", {})
        if not headers:
            return "N/A"

        if not isinstance(headers, dict):
            return str(headers)[:500]

        # 脱敏处理
        sanitized = {}
        sensitive_keys = [
            "authorization", "cookie", "token", "x-auth",
            "api-key", "apikey", "secret", "credential",
        ]

        for k, v in headers.items():
            if any(s in k.lower() for s in sensitive_keys):
                sanitized[k] = "***REDACTED***"
            else:
                # 截断过长的值
                val_str = str(v)
                if len(val_str) > 200:
                    sanitized[k] = val_str[:200] + "..."
                else:
                    sanitized[k] = v

        return json.dumps(sanitized, ensure_ascii=False, indent=2)
