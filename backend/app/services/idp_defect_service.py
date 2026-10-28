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
from urllib.parse import urlparse
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
        parent_test_run_id: Optional[UUID] = None,
        evidence_urls: Optional[dict] = None,
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
            parent_test_run_id: 所属 TestRun（Web 场景下 test_run_id 是 WebTestRun，
                需要额外的 TestRun 维度才能做"同一次运行内去重"）
            evidence_urls: 已归档到对象存储的失败证据链接 {类别: 预签名 URL}
                （trace / screenshot 等，写进描述「证据附件」节）

        Returns:
            IDPDefectRecord: 缺陷记录
        """
        # 1. 先算指纹：去重与"项目未映射"分支都要用，且必须在任何写入之前
        fingerprint = DefectFingerprintService.generate_from_api_result(
            source_project_key=source_project_key,
            method=method,
            endpoint=endpoint,
            error_type=decision.error_type,
            failure_summary=decision.failure_summary,
        )
        #: 同一次运行内的去重维度：Web 用 TestRun（跨 WebTestRun 尝试），API 用自身
        dedup_run_id = parent_test_run_id or test_run_id

        # 2. 同一次运行内已登记过同一失败 → 直接复用，避免重试/多次尝试重复建单
        existing = await self.record_repo.get_by_fingerprint_and_run(
            fingerprint, dedup_run_id
        )
        if existing:
            logger.info(
                "[IDPDefectService] 同一次运行内已存在相同缺陷记录，跳过重复登记: %s",
                existing.id,
            )
            return existing

        # 3. 解析项目映射
        mapping = IDPProjectResolver.resolve(source_project_key)
        if not mapping:
            return await self._create_skipped_record(
                test_run_id=test_run_id,
                test_case_id=test_case_id,
                source_project_key=source_project_key,
                fingerprint=fingerprint or "",
                reason="项目未匹配",
                decision=decision,
                source_type=source_type,
                parent_test_run_id=parent_test_run_id,
                run_lineage_id=dedup_run_id,
            )

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
            evidence_urls=evidence_urls,
        )

        # 8. 创建缺陷记录（pending 状态）
        record = await self.record_repo.create(
            source_type=source_type,
            source_run_id=dedup_run_id,
            source_case_id=test_case_id,
            # 注意：test_run_id 列有指向 api_test_runs 的外键，非 api 来源必须留空
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

            # 缺陷类型（IDP 导入模板要求；非必填但建议传入）。
            # 联调未确认独立字段名，双通道投递：结构化字段 + 描述首行，不丢信息。
            if getattr(decision, "defect_type", ""):
                issue_data["defectType"] = decision.defect_type

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
            # dry-run 返回 snake_case（issue_id/issue_key），真实 API 返回
            # camelCase（issueId/issueNum）；两处键都做兼容，否则 dry-run 灰度
            # 时 key/url 恒为空，无法确认链路已通。
            issue_id = result.get("issueId") or result.get("issue_id")
            issue_key = result.get("issueNum") or result.get("issue_key")
            # 使用 IDP 前端地址生成可点击链接（优先使用前端 URL）
            web_base = getattr(settings, 'idp_web_base_url', settings.idp_base_url).rstrip('/')
            issue_url = f"{web_base}/agile/issues/{issue_id}" if issue_id else None

            # 防御：拿不到 issueId 就不能标 created。
            # 2026-09-30 出现过一批「status=created 但 issue_id/key/url 全空」的记录
            # （响应键兼容修复之前产生的），这类记录永远校验不了、永远同步不出去，
            # 也无法再去 IDP 关联。宁可判 sync_failed 让人看见，也不要静默标成功。
            if not issue_id:
                reason = (
                    "IDP 建单响应未返回 issueId（响应键: %s），无法校验/关联，按同步失败处理"
                    % ", ".join(sorted(str(k) for k in result.keys()))[:200]
                )
                logger.warning(
                    "[IDPDefectService] %s | issue_key=%s result_keys=%s",
                    reason,
                    issue_key,
                    list(result.keys()),
                )
                return await self.record_repo.update(
                    record,
                    create_status=IDPDefectStatus.SYNC_FAILED.value,
                    idp_issue_key=issue_key,
                    error_message=reason[:500],
                )

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
        parent_test_run_id: Optional[UUID] = None,
        run_lineage_id: Optional[UUID] = None,
    ) -> IDPDefectRecord:
        """创建跳过的记录"""
        return await self.record_repo.create(
            source_type=source_type,
            source_run_id=run_lineage_id or test_run_id,
            source_case_id=test_case_id,
            # 注意：test_run_id 列有指向 api_test_runs 的外键，非 api 来源必须留空
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

        ⚠️ 本口径**仅用于框架自动提单**。人工 / 半自动提单（测试执行人写单）用另一套口径：
        `【美心-HK】_UAT_<需求名称>，<对象>，<现象>`（`_UAT_` 后跟**需求名称**，不是固定段；
        同事单里的 `_UAT_CS` 只是"那批需求恰好叫 CS"，属参照实例，不可照抄）。
        两套口径并存但**不得混用**，详见 `docs/IDP_API_CONTRACT.md` §9.6。
        """
        # 从场景名称中提取业务模块（API 场景的模块名要靠 endpoint 兜底，
        # 否则场景名是 "POST /api/..." 时模块位只能是 "POST /api/..."）
        module = IDPDefectService._extract_module(
            scenario_name, endpoint if source_type == "api" else None
        )

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
    def _extract_module(scenario_name: str, endpoint: Optional[str] = None) -> str:
        """从场景名称中提取业务模块。

        ``scenario_name`` 对 API 场景常常就是接口串本身（``POST /api/icdp/v1/...``），
        历史实现"没有方括号就取前 10 个字符"，于是标题变成
        ``【自动化测试】【API】【POST /api/...】…`` —— 模块位等于没写。

        现在的顺序：
        1. 方括号 / 中括号里的显式模块名（人工写的场景名基本都带）；
        2. API 场景：从 ``endpoint`` 路径里取**最后三段**有意义的分段
           （滤掉 ``api``、``v1``、纯数字 / UUID 这类噪声），
           如 ``/api/icdp/v1/memberCard/storage-free-order/refund/calculate``
           → ``storage-free-order/refund/calculate``；
        3. 兜底：去掉方法前缀后，按第一个分隔符截断，而不是硬切 10 字符。
        """
        if not scenario_name and not endpoint:
            return "未知模块"

        if scenario_name:
            match = re.search(r"[【\[]([^】\]]+)[】\]]", scenario_name)
            if match:
                return match.group(1)

        from_path = IDPDefectService._module_from_path(endpoint)
        if from_path:
            return from_path

        if not scenario_name:
            return "未知模块"

        cleaned = re.sub(
            r"^(GET|POST|PUT|PATCH|DELETE|HEAD|OPTIONS)\s+",
            "",
            scenario_name.strip(),
            flags=re.IGNORECASE,
        )
        if cleaned.startswith("/"):
            # 纯路径串又提取不出分段（例如只有一个 /health）—— 保留路径本身
            return cleaned[:40]
        # 取**最靠前**的分隔符截断（不能按分隔符类型顺序找，否则
        # 「限购商品管理-负路径，提交」会先被「，」命中而留出多余段）
        cut = [cleaned.find(sep) for sep in ("，", ",", "、", "：", ":", "；", ";", "（", "(", " ", "-")]
        cut = [idx for idx in cut if idx > 0]
        if cut:
            cleaned = cleaned[: min(cut)]
        cleaned = cleaned.strip()
        return cleaned[:20] or "未知模块"

    @staticmethod
    def _module_from_path(endpoint: Optional[str]) -> Optional[str]:
        """从接口路径里取有业务含义的最后三段。"""
        if not endpoint:
            return None
        path = endpoint
        if "://" in path:
            path = urlparse(path).path
        path = (path or "").split("?")[0].strip("/")
        if not path:
            return None
        noise = {"api", "apis", "rest", "gateway"}
        segments = []
        for raw in path.split("/"):
            seg = raw.strip()
            if not seg:
                continue
            low = seg.lower()
            if low in noise or re.fullmatch(r"v\d+", low):
                continue
            if re.fullmatch(r"\d+", seg) or re.fullmatch(r"[0-9a-fA-F-]{16,}", seg):
                continue
            segments.append(seg)
        if not segments:
            return None
        return "/".join(segments[-3:])

    @staticmethod
    def _base_url_lines(request_summary: Optional[dict]) -> list[str]:
        """渲染 Base URL 行。

        缺陷描述里的 Base URL 必须是**这次真正执行的地址**。历史实现直接写
        ``test_config`` 里的配置值 —— ``/runs/{id}/execute`` 传的 base_url 是整体
        覆盖语义，覆盖后描述仍显示配置值，拿到工单的人按配置值复现不出问题
        （ATR-20261028-004：配置值带 ``/yundt-saas-gateway``，实际打到域根，
        工单里却写着带网关段的地址）。

        所以：优先写实际执行值并标注来源；两者不一致时把配置值也列出来并提示，
        让人一眼看出"这次跑的不是配置的那个环境/前缀"。
        """
        rs = request_summary or {}
        executed = (rs.get("base_url") or "").strip()
        configured = (rs.get("configured_base_url") or "").strip()
        source = rs.get("base_url_source") or ""

        if not executed or executed == "N/A":
            return ["Base URL: N/A（未采集到，需人工确认执行环境）\n"]

        label = "实际执行" if source == "executed" else "配置值"
        lines = [f"Base URL: {executed}（{label}）\n"]
        if configured and configured.rstrip("/") != executed.rstrip("/"):
            lines.append(
                f"配置 Base URL: {configured} —— ⚠️ 与本次实际执行地址不一致，"
                "说明运行时覆盖了环境地址；若差异在路径前缀，复现时需补回该前缀\n"
            )
        return lines

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
        evidence_urls: Optional[dict] = None,
    ) -> str:
        """
        生成缺陷描述（Delta 格式）

        IDP description 使用富文本 Delta 字符串，由服务统一进行 JSON 转义
        包含完整的测试上下文信息，便于开发人员定位和修复问题
        支持 API / Web / 安全 三类测试
        """
        # 提取请求参数（脱敏）
        defect_type = getattr(decision, "defect_type", "")
        request_params = IDPDefectService._sanitize_request_params(request_summary)

        # 提取响应信息
        response_status = (response_summary.get("status_code") if response_summary else None) or "N/A"
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

        # ========== 缺陷类型 ==========
        delta_ops.append({"insert": "缺陷类型\n", "attributes": {"bold": True}})
        delta_ops.append({"insert": f"{defect_type or '未归类'}\n\n"})

        # ========== 业务项目 ==========
        delta_ops.append({"insert": "业务项目\n", "attributes": {"bold": True}})
        _project_name = IDPDefectService._get_project_name(source_project_key)
        delta_ops.append({
            "insert": f"{source_project_key} ({_project_name})\n\n" if _project_name
            else f"{source_project_key}\n\n"
        })

        # ========== 测试环境 ==========
        delta_ops.append({"insert": "测试环境\n", "attributes": {"bold": True}})
        for _line in IDPDefectService._base_url_lines(request_summary):
            delta_ops.append({"insert": _line})
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
            # API 测试复现步骤（默认）——旧版三步是模板套话（"发送请求/使用以下参数/观察结果"），
            # 拿到工单的人无法照着复现。这里把能确证的写实，缺什么就如实标缺。
            delta_ops.append({"insert": f"1. 向 {endpoint} 发送 {method} 请求\n"})
            if IDPDefectService._has_request_body(request_summary):
                delta_ops.append({"insert": "2. 请求体见下方「请求信息 › 请求参数」\n"})
            elif str(method).upper() in ("GET", "HEAD", "OPTIONS"):
                delta_ops.append({"insert": f"2. 该请求无请求体（{method}），参数见 URL query\n"})
            else:
                delta_ops.append({
                    "insert": "2. ⚠️ 未采集到请求体 —— 需人工补充能复现的最小请求参数\n"
                })
            _expected = getattr(decision, "expected_result", None)
            _observed = getattr(decision, "observed_result", None)
            if _expected or _observed:
                delta_ops.append({
                    "insert": f"3. 观察响应：期望 {_expected or '（见「预期结果」）'}，"
                              f"实测 {_observed or response_status}\n\n"
                })
            else:
                delta_ops.append({"insert": "3. 观察响应结果，与下方「预期结果」比对\n\n"})

        # ========== 预期结果 ==========
        # 写**用例断言的真实期望值**（由 Playwright 失败消息反推，见
        # DefectDecisionService.extract_assertion_expectation）。
        #
        # 旧实现恒写「HTTP 状态码: 200 OK / 响应结构符合接口契约 / 业务逻辑正确」：
        # 对「业务失败也返回 HTTP 200」的信封式被测系统，这三行恰好把**缺陷现状**
        # 描述成了期望行为 —— 开发读工单会认为无需修复（DEFECT-001/925695 实例）。
        # 提取不到期望值时宁缺毋滥，写"待人工补充"，不做任何推断。
        delta_ops.append({"insert": "预期结果\n", "attributes": {"bold": True}})
        if source_type == "security":
            delta_ops.append({"insert": "不存在安全漏洞\n"})
            delta_ops.append({"insert": "输入已正确过滤和校验\n\n"})
        else:
            expected_result = getattr(decision, "expected_result", None)
            assertion_expr = getattr(decision, "assertion_expr", None)
            if expected_result:
                delta_ops.append({"insert": f"断言期望值: {expected_result}\n"})
                if assertion_expr:
                    delta_ops.append({"insert": f"断言表达式: {assertion_expr}\n"})
                delta_ops.append({"insert": "（取自用例断言的期望值，非模板推断）\n\n"})
            else:
                delta_ops.append({
                    "insert": "⚠️ 未能从断言中提取期望值 —— 待人工补充\n"
                              "（自动提单只能拿到断言的通过/失败，拿不到期望值时"
                              "此处不写任何推断，避免把缺陷描述成「现状即预期」）\n\n"
                })

        # ========== 实际结果 ==========
        delta_ops.append({"insert": "实际结果\n", "attributes": {"bold": True}})
        if source_type == "security":
            severity = request_summary.get("severity", "未知") if request_summary else "未知"
            delta_ops.append({"insert": f"风险等级: {severity}\n"})
            delta_ops.append({"insert": f"漏洞详情: {decision.failure_summary}\n\n"})
        else:
            delta_ops.append({"insert": f"HTTP 状态码: {response_status}\n"})
            observed_result = getattr(decision, "observed_result", None)
            if observed_result:
                delta_ops.append({"insert": f"断言实测值: {observed_result}\n"})
            delta_ops.append({"insert": f"错误类型: {decision.error_type}\n"})
            delta_ops.append({"insert": f"错误详情: {decision.failure_summary}\n\n"})

        # ========== 请求信息 ==========
        delta_ops.append({"insert": "请求信息\n", "attributes": {"bold": True}})
        delta_ops.append({"insert": f"Method: {method}\n"})
        delta_ops.append({"insert": f"URL: {endpoint}\n"})
        for _line in IDPDefectService._base_url_lines(request_summary):
            delta_ops.append({"insert": _line})
        delta_ops.append({"insert": "\n"})

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
            delta_ops.append({"insert": "测试报告地址（预签名，7 天内有效）: "})
            # 必须挂 link 属性：IDP 富文本里裸 URL 在部分客户端不可点，
            # 而报告是预签名绝对地址（相对路径 + 登录态的历史写法根本打不开）。
            delta_ops.append({"insert": report_url, "attributes": {"link": report_url}})
            delta_ops.append({"insert": "\n"})
        delta_ops.append({"insert": "\n"})

        # ========== 证据附件 ==========
        # trace / 截图由执行器在临时目录销毁前归档到对象存储（见
        # app/services/evidence_artifacts.py），这里只负责把预签名链接写成人能点开的形式。
        _evidence = {k: v for k, v in (evidence_urls or {}).items() if v}
        if _evidence:
            delta_ops.append({"insert": "证据附件\n", "attributes": {"bold": True}})
            _labels = {
                "trace": "Playwright trace（下载后用 trace.playwright.dev 打开，含完整请求/响应）",
            }
            for _kind, _url in _evidence.items():
                if _kind in _labels:
                    _label = _labels[_kind]
                elif _kind == "screenshot":
                    _label = "失败截图"
                elif _kind.startswith("screenshot_"):
                    _label = f"失败截图 {_kind.rsplit('_', 1)[-1]}"
                else:
                    _label = _kind
                delta_ops.append({"insert": f"{_label}: "})
                delta_ops.append({"insert": _url, "attributes": {"link": _url}})
                delta_ops.append({"insert": "\n"})
            delta_ops.append({
                "insert": "（链接为对象存储预签名地址，S3 协议上限 7 天，过期后点开会 403；"
                "证据对象本身长期保留，可在平台该次运行详情页重新签发取回）\n\n"
            })

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
        """脱敏请求参数。

        2026-10-28 起语义收紧：**请求体优先，只有请求体缺失时才退到请求头**，
        并且退让时明确写出来。

        历史实现把 body 与 headers 直接合并成一份 dict 塞进「请求参数」，
        根因是当时 trace 的请求体读不到（``postData.text`` 是空串、真实报文在
        ``resources/<sha1>``，见 ``trace_evidence._read_request_body``），
        于是工单里「请求参数」实际是一堆请求头 —— 现象就是
        「content-length: 28，但请求体是空的」。

        现在请求体能读到了，请求头另有一节「请求头」，不该再混进来。
        """
        if not request_summary:
            return "N/A"

        body = IDPDefectService._normalize_body(request_summary.get("body"))
        if body:
            return json.dumps(
                IDPDefectService._redact_mapping(body), ensure_ascii=False, indent=2
            )

        headers = request_summary.get("headers")
        if isinstance(headers, dict) and headers and not request_summary.get("query"):
            return (
                "（未采集到请求体，以下为请求头）\n"
                + json.dumps(
                    IDPDefectService._redact_mapping(headers),
                    ensure_ascii=False,
                    indent=2,
                )
            )

        query = request_summary.get("query")
        if isinstance(query, str) and query.strip():
            return f"（该请求无请求体，以下为 URL query 参数）\n{query.strip()}"

        # 旧实现返回 "{}"：工单里写成「请求参数: {}」，读者分不清
        # 是"请求体本就是空对象"还是"平台没采集到"（925695 即如此）。
        return "（未采集到请求体/请求头）"

    @staticmethod
    def _normalize_body(body: Any) -> dict:
        """把请求体统一成 dict（JSON 文本 / 非空 dict / 其它一律视为空）。"""
        if isinstance(body, dict):
            return body
        if isinstance(body, list):
            return {"items": body}
        if isinstance(body, str) and body.strip():
            try:
                parsed = json.loads(body)
            except (ValueError, TypeError):
                return {"raw": body}
            if isinstance(parsed, dict):
                return parsed
            return {"raw": parsed}
        return {}

    @staticmethod
    def _has_request_body(request_summary: Optional[dict]) -> bool:
        if not request_summary:
            return False
        return bool(IDPDefectService._normalize_body(request_summary.get("body")))

    @staticmethod
    def _redact_mapping(data: dict) -> dict:
        """敏感字段脱敏（Token / 密码 / Cookie 等）。"""
        sensitive_keys = [
            "password", "token", "auth", "authorization", "cookie",
            "secret", "key", "api_key", "apikey", "access_token",
            "refresh_token", "credential", "credentials",
        ]
        sanitized = {}
        for k, v in data.items():
            if any(s in str(k).lower() for s in sensitive_keys):
                sanitized[k] = "***REDACTED***"
            else:
                sanitized[k] = v
        return sanitized

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
        """取**权威**项目名；取不到返回空串（调用方据此省略括号，不编造名称）。

        此前这里是一份硬编码字典 ``{"PR-2": "小杨生煎储值免单活动"}``，
        与权威配置 ``backend/config/idp_projects.yaml`` 的 ``小杨生煎`` 不一致 ——
        每张自动提单都把项目名写错（实测工单 jf-20260122392-01-2960 中招，
        描述里写着"PR-2 (小杨生煎储值免单活动)"，而本次需求是限购商品管理）。

        改为单一真相：``idp_projects.yaml``（经 IDPProjectResolver 读取）。
        """
        try:
            from app.services.idp_project_resolver import IDPProjectResolver

            mapping = IDPProjectResolver.resolve(source_project_key)
            if mapping is not None and mapping.source_project_name:
                return mapping.source_project_name
        except Exception as exc:  # noqa: BLE001 —— 配置缺失不影响提单主流程
            logger.warning(
                "[IDPDefect] 读取项目名失败，描述中省略项目名: %s (%s)", source_project_key, exc
            )
        return ""

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
