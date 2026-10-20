# -*- coding: utf-8 -*-
"""API 覆盖度评审工具（API Agent）。

在**用例设计完成之后、脚本生成与执行之前**对接口契约覆盖面做确定性评审，
并把结论落到两个地方：

1. **成果物**：整份报告（JSON + Markdown）作为 ``API_TEST_REPORT`` 附件存入 MinIO；
2. **端点字段**：每条端点的 ``custom_config["coverage_gate"]`` 记录门禁结论，
   供执行路径（``execute_api_script``）在真正跑脚本前读取并拦截。

与既有能力的分工：
- ``quality-review``      → 功能测试用例质量（业务视角，非接口契约面）
- ``api/reporter``        → 执行**后**的文字覆盖分析
- ``review_api_coverage`` → 执行**前**的确定性覆盖度评分（本工具）
"""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from typing import Any, Optional
from uuid import UUID

from langchain_core.tools import tool
from sqlalchemy import select

from app.config.database import async_session_factory
from app.config.minio_client import MinIOClient
from app.models.api_endpoint import APIEndpoint
from app.models.attachment import Attachment, AttachmentEntityType
from app.models.project import Project
from app.test_platform.api_coverage_review import (
    PLATFORM_RULES,
    ApiCoverageReport,
    review_api_coverage,
)

logger = logging.getLogger(__name__)

#: 端点 custom_config 中记录门禁结论的键。
COVERAGE_GATE_KEY = "coverage_gate"

#: 门禁严格度环境变量。
#: - "warn"（默认）：执行前只记录告警，不阻断——避免影响既有流水线；
#: - "strict"：存在阻断结论时直接拦截执行。
COVERAGE_GATE_STRICT_ENV = "API_COVERAGE_GATE_STRICT"


def is_strict_mode() -> bool:
    return str(os.environ.get(COVERAGE_GATE_STRICT_ENV, "warn")).strip().lower() in {
        "1", "true", "yes", "strict", "on",
    }


# ---------------------------------------------------------------------------
# 门禁结论读写（供执行路径复用）
# ---------------------------------------------------------------------------

async def read_coverage_gate(endpoint_id: str) -> dict[str, Any] | None:
    """读取端点已记录的覆盖度门禁结论；未评审过则返回 None。"""
    try:
        endpoint_uuid = UUID(str(endpoint_id))
    except (ValueError, AttributeError, TypeError):
        return None

    try:
        async with async_session_factory() as session:
            result = await session.execute(
                select(APIEndpoint).where(APIEndpoint.id == endpoint_uuid)
            )
            endpoint = result.scalar_one_or_none()
            if endpoint is None:
                return None
            config = endpoint.custom_config or {}
            gate = config.get(COVERAGE_GATE_KEY)
            return gate if isinstance(gate, dict) else None
    except Exception as exc:  # noqa: BLE001 - 门禁读取失败不得影响执行判定以外的逻辑
        logger.warning("[coverage-gate] 读取端点 %s 门禁结论失败: %s", endpoint_id, exc)
        return None


def build_gate_block_response(endpoint_id: str, gate: dict[str, Any]) -> dict[str, Any]:
    """构造统一的执行阻断响应。"""
    return {
        "success": False,
        "status": "blocked",
        "reason_code": "COVERAGE_GATE_BLOCKED",
        "endpoint_id": endpoint_id,
        "message": (
            "执行被阻断: 该接口未通过 API 覆盖度评审（设计后/执行前门禁）。"
            "请先补齐缺失项并重新评审。"
        ),
        "coverage_gate": gate,
    }


def decide_gate_action(
    gate: dict[str, Any] | None, *, strict: bool
) -> dict[str, Any] | None:
    """纯函数：根据门禁结论与严格度决定是否阻断。

    Returns:
        需要阻断时返回阻断响应；放行时返回 None。
    """
    if not gate:
        return None
    if not gate.get("blocked"):
        return None
    if not strict:
        return None
    return {
        "success": False,
        "status": "blocked",
        "reason_code": "COVERAGE_GATE_BLOCKED",
        "message": (
            "执行被阻断: 该接口未通过 API 覆盖度评审（设计后/执行前门禁）。"
            "请先补齐缺失项并重新评审。"
        ),
        "coverage_gate": gate,
    }


async def enforce_coverage_gate(endpoint_id: Optional[str]) -> dict[str, Any] | None:
    """执行前的覆盖度门禁检查。

    Returns:
        需要阻断时返回阻断响应；放行（含 warn 模式与未评审）时返回 None。
    """
    if not endpoint_id:
        return None

    gate = await read_coverage_gate(endpoint_id)
    if not gate:
        logger.info("[coverage-gate] 端点 %s 尚未做覆盖度评审，放行（可先调用 review_api_coverage）", endpoint_id)
        return None

    strict = is_strict_mode()
    action = decide_gate_action(gate, strict=strict)

    if action is None:
        if gate.get("blocked"):
            # warn 模式：不阻断，但必须让问题可见
            logger.warning(
                "[coverage-gate] 端点 %s 覆盖度评审未通过（score=%s），当前为 warn 模式未阻断；"
                "设 %s=strict 可硬阻断。原因：%s",
                endpoint_id, gate.get("score"), COVERAGE_GATE_STRICT_ENV,
                "; ".join(gate.get("block_reasons") or []),
            )
        else:
            logger.info(
                "[coverage-gate] 端点 %s 覆盖度评审通过（score=%s），放行",
                endpoint_id, gate.get("score"),
            )
        return None

    logger.error(
        "[coverage-gate] 端点 %s 覆盖度评审未通过，strict 模式阻断执行：%s",
        endpoint_id, "; ".join(gate.get("block_reasons") or []),
    )
    action["endpoint_id"] = endpoint_id
    return action


# ---------------------------------------------------------------------------
# 工具
# ---------------------------------------------------------------------------

def _parse_claims(raw: Any) -> dict[str, list[dict[str, Any]]]:
    """解析覆盖声明入参。

    期望结构：``{"<endpoint_id>": [claim, ...], "*": [claim, ...]}``
    也接受直接给出单个端点的声明数组（此时按 ``"*"`` 处理）。
    """
    if raw in (None, "", "null"):
        return {}
    data = raw
    if isinstance(raw, str):
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            return {}
    if isinstance(data, list):
        return {"*": [c for c in data if isinstance(c, dict)]}
    if isinstance(data, dict):
        out: dict[str, list[dict[str, Any]]] = {}
        for key, value in data.items():
            if isinstance(value, dict):
                out[str(key)] = [value]
            elif isinstance(value, list):
                out[str(key)] = [c for c in value if isinstance(c, dict)]
        return out
    return {}


@tool
async def review_api_coverage(
    project_identifier: str,
    endpoint_ids: Optional[list[str]] = None,
    coverage_claims: Optional[str] = None,
    core_endpoint_ids: Optional[list[str]] = None,
    save_report: bool = True,
) -> dict:
    """对接口做覆盖度评审（设计后、执行前），低于门槛时返回缺失项并阻止执行。

    评审 6 个可从 OpenAPI 契约直接提取的维度（权重合计 100，平台规则）：
    状态码 25 / 必填参数 20 / 枚举 15 / 类型与格式 15 / 请求体字段 15 / 鉴权 10。

    口径要点：
    - 契约未提供数据的维度标记为 unknown，从分母剔除并重归一化，**不按 0 分计入**；
    - 数据不足到无法评分时判为 insufficient_data，转人工确认，不给分；
    - 单维度扣分 = 满分 − 得分，恒不超过该维度满分，不存在重复扣分；
    - 业务规则、权限矩阵、状态机、限流/超时、安全 payload、响应核心字段
      无结构化来源，一律作为人工确认项，不参与自动评分。

    Args:
        project_identifier: 项目标识符（如 "PR-1234"）。
        endpoint_ids: 要评审的端点 ID 列表；为空表示评审项目下全部端点。
        coverage_claims: 用例覆盖声明（JSON 字符串或对象），结构：
            {"<endpoint_id>": [{"case_id": "TC-1",
                                "status_codes": ["200","400"],
                                "parameters": ["x-tenant"],
                                "enum_values": {"body:status": ["a","b"]},
                                "body_fields": ["name"],
                                "auth_scenarios": ["with_token","without_token"]}],
             "*": [...]}   # "*" 为兜底，适用于未单独声明的端点
        core_endpoint_ids: 核心接口 ID 列表，套用更严的核心接口门槛（85 分）。
        save_report: 是否把报告存为 API 测试成果物（默认 True）。

    Returns:
        dict: 含 blocked、summary、block_reasons、report_markdown，
              以及成果物 attachment_id（保存成功时）。
    """
    if not project_identifier:
        return {"success": False, "error": "project_identifier 不能为空"}

    claims_map = _parse_claims(coverage_claims)

    try:
        async with async_session_factory() as session:
            project_result = await session.execute(
                select(Project).where(Project.identifier == project_identifier)
            )
            project = project_result.scalar_one_or_none()
            if project is None:
                return {"success": False, "error": f"项目 {project_identifier} 不存在"}

            stmt = select(APIEndpoint).where(APIEndpoint.project_id == project.id)
            if endpoint_ids:
                try:
                    uuids = [UUID(str(x)) for x in endpoint_ids]
                except (ValueError, AttributeError):
                    return {"success": False, "error": "endpoint_ids 含非法 UUID"}
                stmt = stmt.where(APIEndpoint.id.in_(uuids))

            result = await session.execute(stmt.order_by(APIEndpoint.sort_order))
            endpoints = result.scalars().all()

            if not endpoints:
                return {
                    "success": False,
                    "error": "未找到任何端点；请确认 project_identifier / endpoint_ids",
                }

            endpoint_dicts = [
                {
                    "id": str(ep.id),
                    "display_name": ep.display_name,
                    "method": ep.method,
                    "path": ep.path,
                    "parameters": ep.parameters,
                    "request_body": ep.request_body,
                    "responses": ep.responses,
                    "security": ep.security,
                    "tags": ep.tags,
                    "custom_config": ep.custom_config,
                }
                for ep in endpoints
            ]

            report = review_api_coverage(
                endpoint_dicts,
                claims_map,
                rules=PLATFORM_RULES,
                core_endpoint_ids=core_endpoint_ids or (),
            )

            # 1. 门禁结论回写到端点，供执行路径拦截
            reviewed_at = datetime.now(timezone.utc).isoformat()
            for review in report.reviews:
                ep = next(
                    (e for e in endpoints if str(e.id) == review.surface.endpoint_id), None
                )
                if ep is None:
                    continue
                config = dict(ep.custom_config or {})
                config[COVERAGE_GATE_KEY] = {
                    "blocked": bool(review.fair),
                    "score": round(review.score, 2) if review.score is not None else None,
                    "status": review.status,
                    "known_weight_ratio": round(review.known_weight_ratio, 4),
                    "block_reasons": list(review.fair),
                    "reviewed_at": reviewed_at,
                    "rules_provenance": report.rules.provenance,
                }
                ep.custom_config = config
            await session.commit()

        # 2. 报告存为成果物
        attachment_id = None
        object_name = None
        if save_report:
            attachment_id, object_name = await _save_report_artifact(
                project_identifier, project.id, report
            )

        summary = report.to_dict()["summary"]
        logger.info(
            "[review_api_coverage] project=%s endpoints=%d blocked=%s avg=%s",
            project_identifier, summary["total_endpoints"], summary["blocked"],
            summary["average_score"],
        )

        return {
            "success": True,
            "project_identifier": project_identifier,
            "blocked": report.blocked,
            "summary": summary,
            "block_reasons": report.block_reasons,
            "manual_review_items": report.manual_review_items,
            "invalid_claims": [i.to_dict() for i in report.claim_issues],
            "report_markdown": report.to_markdown(),
            "report": report.to_dict(),
            "artifact": (
                {"attachment_id": attachment_id, "object_name": object_name}
                if attachment_id else None
            ),
            "next_action": (
                "禁止进入脚本生成/执行阶段：先补齐 block_reasons 中的缺失项后重新评审。"
                if report.blocked
                else "评审通过，可进入脚本生成与执行阶段。"
            ),
        }

    except Exception as exc:  # noqa: BLE001
        logger.exception("[review_api_coverage] 评审失败")
        return {"success": False, "error": f"覆盖度评审失败: {type(exc).__name__}: {exc}"}


async def _save_report_artifact(
    project_identifier: str,
    project_id: UUID,
    report: ApiCoverageReport,
) -> tuple[str | None, str | None]:
    """把评审报告（Markdown + JSON）存为 API_TEST_REPORT 成果物。

    Returns:
        (attachment_id, object_name)；失败时返回 (None, None)，不影响评审结果返回。
    """
    try:
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        base = f"api-tests/{project_identifier}/coverage-review/{timestamp}"
        md_object = f"{base}.md"
        json_object = f"{base}.json"

        md_bytes = report.to_markdown().encode("utf-8")
        json_bytes = report.to_json().encode("utf-8")

        MinIOClient.upload_bytes(object_name=md_object, data=md_bytes, content_type="text/markdown")
        MinIOClient.upload_bytes(object_name=json_object, data=json_bytes, content_type="application/json")

        summary = report.to_dict()["summary"]
        description = (
            f"API 覆盖度评审报告（设计后/执行前）\n"
            f"接口总数: {summary['total_endpoints']} | 已评分: {summary['scored_endpoints']} | "
            f"数据不足: {summary['insufficient_data_endpoints']}\n"
            f"平均分: {summary['average_score']} | 是否放行: "
            f"{'否' if summary['blocked'] else '是'}"
        )

        async with async_session_factory() as session:
            attachment = Attachment(
                entity_type=AttachmentEntityType.API_TEST_REPORT,
                entity_id=project_id,
                project_id=project_id,
                file_name=f"api-coverage-review-{timestamp}.md",
                file_size=len(md_bytes),
                content_type="text/markdown",
                object_name=md_object,
                description=description,
                created_by="api-agent",
            )
            session.add(attachment)
            await session.commit()
            await session.refresh(attachment)
            return str(attachment.id), md_object

    except Exception as exc:  # noqa: BLE001 - 报告保存失败不应抹掉评审结论
        logger.warning("[review_api_coverage] 报告成果物保存失败: %s", exc)
        return None, None


__all__ = [
    "COVERAGE_GATE_KEY",
    "COVERAGE_GATE_STRICT_ENV",
    "enforce_coverage_gate",
    "is_strict_mode",
    "read_coverage_gate",
    "review_api_coverage",
]
