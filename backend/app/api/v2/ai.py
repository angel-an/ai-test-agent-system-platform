"""Minimal AI-compatible routes used by the document generation dialog."""

from __future__ import annotations

import re
from uuid import UUID

from fastapi import APIRouter, File, Form, HTTPException, UploadFile, status

from app.api.deps import CurrentUserIdDep, DbSessionDep
from app.schemas.enums import Priority
from app.schemas.test_case import TestCaseCreate, TestStepCreate
from app.services.api_doc_parser_service import APIDocParserService
from app.services.test_case_service import TestCaseService
from app.test_platform.requirements.gate import (
    GateOverride,
    GateOverrideAuditError,
    GateOverrideNotAuthorizedError,
    record_generated,
    start_session,
    update_session,
)
from app.test_platform.services.requirement_quality_service import (
    RequirementQualityService,
    resolve_sole_feature_id,
)


router = APIRouter(
    prefix="/projects/{project_identifier}/ai",
    tags=["AI 生成"],
)


@router.post("/generate-from-document", summary="从文档生成最小可用测试用例")
async def generate_from_document(
    project_identifier: str,
    current_user_id: CurrentUserIdDep,
    db: DbSessionDep,
    file: UploadFile = File(..., description="需求文档，支持 PDF/TXT/DOCX"),
    folder_id: UUID | None = Form(default=None),
    additional_prompt: str | None = Form(default=None),
    template: str = Form(default="test_case"),
    output_type: str = Form(
        default="test_case",
        description="输出类型：test_case（生成用例）/ requirement_analysis（只输出分析报告）",
    ),
    analysis_payload: str | None = Form(
        default=None,
        description="可选：结构化需求分析结果（JSON）。解析失败自动降级 Incomplete 并阻断生成",
    ),
    force_generate: bool = Form(
        default=False,
        description="已废弃：不再生效。人工放行请使用 override_token + override_reason（服务端受控）",
        deprecated=True,
    ),
    override_token: str | None = Form(
        default=None,
        description="人工放行凭证：必须与服务端 REQUIREMENT_GATE_OVERRIDE_TOKEN 一致（默认未启用）",
    ),
    override_reason: str | None = Form(
        default=None,
        description="人工放行原因（必填，用于审计；不少于 10 个字符）",
    ),
    session_key: str | None = Form(default=None, description="生成会话标识，用于状态跟踪"),
    requirement_id: str | None = Form(default=None, description="需求编号，写入用例追踪字段"),
) -> dict:
    content_bytes = await file.read()
    if len(content_bytes) > 15 * 1024 * 1024:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="文件大小不能超过 15MB",
        )

    text = _extract_text(file.filename or "", file.content_type, content_bytes)
    if additional_prompt:
        text = f"{text}\n\n补充说明:\n{additional_prompt}"

    source = file.filename or "需求文档"

    # ---- 阶段一：需求质量门控（硬门控）-------------------------------------
    # 人工放行只能由服务端运维配置凭证后触发；force_generate 旧参数已废弃且不生效。
    gate_override: GateOverride | None = None
    if override_token or override_reason:
        gate_override = GateOverride(
            token=override_token or "",
            reason=override_reason or "",
            operator=project_identifier,
            requirement_id=requirement_id or "",
            source=source,
        )

    quality_service = RequirementQualityService()
    try:
        pipeline = quality_service.pipeline(
            text,
            source=source,
            analysis_payload=analysis_payload or None,
            override=gate_override,
        )
    except GateOverrideNotAuthorizedError as exc:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)) from exc
    except GateOverrideAuditError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)
        ) from exc
    analysis = pipeline.analysis

    if session_key:
        start_session(session_key, source=source)
        update_session(session_key, analysis)

    # 只输出分析报告：不生成用例
    if output_type == "requirement_analysis":
        return {
            "success": True,
            "test_cases": [],
            "message": (
                f"需求分析完成：{analysis.status.label}"
                f"（{analysis.status.value}）"
            ),
            **pipeline.response,
            "analysis_report_markdown": pipeline.analysis_report_markdown,
        }

    # Incomplete / Blocked → 输出报告并暂停用例生成（不接受客户端布尔参数绕过）
    if not analysis.can_generate_testcases:
        result = {
            "success": False,
            "test_cases": [],
            "message": (
                f"需求质量判定为 {analysis.status.label}，已暂停测试用例生成；"
                "请先补齐缺失项（详见 analysis_report）"
            ),
            **pipeline.response,
            "analysis_report_markdown": pipeline.analysis_report_markdown,
        }
        if force_generate:
            result["warning"] = (
                "force_generate 参数已废弃且不生效；如确需人工放行，"
                "请使用服务端受控的 override_token + override_reason。"
            )
        # 会话状态已由上面的 update_session() 置为 blocked，此处不再改动，
        # 更不得调用 record_generated（那会把状态误标为 generated）。
        return result

    # ---- 通过门控：走既有生成链路 -----------------------------------------
    feature_points = pipeline.feature_points
    # 追踪字段落库规则（阶段二）：
    # - 仅当需求只有一个功能点时，才允许用该唯一功能点兜底；
    # - 多功能点需求不得默认绑定 FP-001，缺失 feature_id 的用例会被标记为未追踪，
    #   由契约校验的 traceability_problems 报出，而不是产生错误关联。
    sole_feature_id = resolve_sole_feature_id(feature_points)

    service = TestCaseService(db)
    created = []
    untraced_case_titles: list[str] = []
    for case in _build_minimal_cases(text, source):
        case.requirement_id = case.requirement_id or (requirement_id or None)
        if not case.feature_id and sole_feature_id:
            case.feature_id = sole_feature_id
        elif not case.feature_id:
            untraced_case_titles.append(case.name)
        case.source_location = case.source_location or source
        created.append(
            await service.create_test_case(
                project_identifier=project_identifier,
                data=case,
                created_by=current_user_id,
                folder_id=folder_id,
            )
        )

    await db.commit()

    created_ids = [str(item.identifier) for item in created if getattr(item, "identifier", None)]
    if session_key:
        record_generated(session_key, created_ids)

    return {
        "success": True,
        "test_cases": [item.model_dump(mode="json") for item in created],
        "message": f"成功生成 {len(created)} 个测试用例",
        **pipeline.response,
        "feature_points": [fp.to_dict() for fp in feature_points],
        "analysis_report_markdown": pipeline.analysis_report_markdown,
        "traceability_warning": (
            f"有 {len(untraced_case_titles)} 条用例未能确定功能点归属（多功能点需求不默认绑定 FP-001），"
            "请在用例追踪字段补齐 feature_id："
            + "、".join(untraced_case_titles[:5])
        ) if untraced_case_titles else None,
    }


def _extract_text(filename: str, content_type: str | None, content: bytes) -> str:
    suffix = filename.lower().rsplit(".", 1)[-1] if "." in filename else ""
    if suffix == "pdf" or content_type == "application/pdf":
        try:
            import fitz  # type: ignore

            doc = fitz.open(stream=content, filetype="pdf")
            return "\n".join(page.get_text("text") for page in doc)
        except Exception:
            return filename

    if suffix in {"txt", "md", "docx"} or (content_type or "").startswith("text/"):
        try:
            return APIDocParserService().extract_text_from_file(filename, content_type, content)
        except ValueError as exc:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

    raise HTTPException(
        status_code=status.HTTP_400_BAD_REQUEST,
        detail="当前最小闭环仅支持 PDF、TXT、MD、DOCX 需求文档",
    )


def _build_minimal_cases(text: str, filename: str) -> list[TestCaseCreate]:
    normalized = re.sub(r"\s+", " ", text)
    if _contains_any(normalized, ["企业储值", "储值充值", "企业充值"]):
        return _build_enterprise_storage_cases(normalized, filename)

    activity_name = "储值免单活动" if "储值免单" in normalized or "免单" in filename else "需求功能"
    module = "B端-活动管理" if _contains_any(normalized, ["B端", "营销云", "活动管理", "新建活动"]) else "需求验证"

    scenarios = [
        ("新建储值免单活动基础信息保存成功", "正向", Priority.HIGH),
        ("活动类型选择储值免单活动后展示专属配置项", "正向", Priority.MEDIUM),
        ("必填字段缺失时保存失败并展示校验提示", "反向", Priority.HIGH),
        ("活动时间边界校验：开始时间等于结束时间保存失败", "边界", Priority.MEDIUM),
        ("适用门店与活动规则配置完成后活动创建成功", "正向", Priority.HIGH),
    ]


def _build_enterprise_storage_cases(text: str, filename: str) -> list[TestCaseCreate]:
    """Create a small but usable SIT suite for the enterprise stored-value form."""
    navigation_path = _extract_web_navigation_path(text)
    path_text = " -> ".join(navigation_path)
    scenarios = [
        ("储值账户列表表单与创建储值充值入口展示", "正向", Priority.HIGH),
        ("手动输入会员手机号并保存企业储值充值", "正向", Priority.HIGH),
        ("非会员手机号提示清单且其他会员可继续保存", "反向", Priority.HIGH),
        ("企业储值充值金额边界与两位小数校验", "边界", Priority.MEDIUM),
        ("批量储值充值导入文件格式与大小校验", "边界", Priority.MEDIUM),
        ("企业储值退款余额不足时仅扣减至零", "边界", Priority.MEDIUM),
    ]

    return [
        TestCaseCreate(
            name=f"企业储值充值-{title}",
            description=(
                f"基于上传需求文档《{filename}》生成的 B 端 SIT 用例。\n\n"
                f"Web验证路径：{path_text}"
            ),
            preconditions=(
                "已具备企业控制台和营销云的储值管理权限。\n"
                f"Web验证路径：{path_text}"
            ),
            priority=priority,
            test_case_steps=[
                TestStepCreate(
                    step="按 Web 验证路径进入储值账户列表，检查默认展示的变动类型、变更方式和会员手机号表单。",
                    result="储值账户列表及企业储值充值入口可访问，表单字段符合需求说明。",
                ),
                TestStepCreate(
                    step=f"按场景执行：{title}。",
                    result="页面校验、提示和数据处理结果符合需求说明。",
                ),
                TestStepCreate(
                    step="保存或提交后查看页面反馈和储值流水。",
                    result="系统返回明确结果；成功场景产生正确储值流水，失败场景不产生错误数据。",
                ),
            ],
            module="B端-储值管理",
            keyword=keyword,
            risk_level="high" if priority == Priority.HIGH else "medium",
            case_kind="sit",
            tags=["AI生成", "最小闭环", "企业储值充值", "Web路径"],
        )
        for title, keyword, priority in scenarios
    ]


def _extract_web_navigation_path(text: str) -> list[str]:
    match = re.search(r"Web验证路径：\s*(.+?)(?=目标入口：|账号密码|优先生成|$)", text)
    if match:
        items = [item.strip() for item in re.split(r"\s*(?:->|→)\s*", match.group(1))]
        items = [item for item in items if item]
        if items:
            return items
    return [
        "企业控制台登录",
        "门户中心",
        "数盈营销云",
        "忠诚度菜单",
        "储值管理",
        "储值账户列表",
        "检查表单",
        "创建储值充值",
    ]
    if _contains_any(normalized, ["复制", "删除", "列表"]):
        scenarios.append(("活动列表支持储值免单活动查询、复制和删除入口展示", "正向", Priority.MEDIUM))

    return [
        TestCaseCreate(
            name=f"{activity_name}-{title}",
            description=f"基于上传需求文档《{filename}》生成的最小闭环 SIT 用例。",
            preconditions="已登录 B 端管理后台，具备营销活动管理权限。",
            priority=priority,
            test_case_steps=[
                TestStepCreate(step="进入营销活动管理并点击新建活动", result="系统打开新建活动页面"),
                TestStepCreate(step=f"按场景执行：{title}", result="页面行为和数据结果符合需求说明"),
                TestStepCreate(step="保存或提交后查看页面反馈", result="系统返回明确成功或失败提示，数据状态正确"),
            ],
            module=module,
            keyword=keyword,
            risk_level="high" if priority == Priority.HIGH else "medium",
            case_kind="sit",
            tags=["AI生成", "最小闭环", activity_name],
        )
        for title, keyword, priority in scenarios
    ]


def _contains_any(text: str, keywords: list[str]) -> bool:
    return any(keyword in text for keyword in keywords)
