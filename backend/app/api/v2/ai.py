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

    service = TestCaseService(db)
    created = []
    for case in _build_minimal_cases(text, file.filename or "需求文档"):
        created.append(
            await service.create_test_case(
                project_identifier=project_identifier,
                data=case,
                created_by=current_user_id,
                folder_id=folder_id,
            )
        )

    await db.commit()
    return {
        "success": True,
        "test_cases": [item.model_dump(mode="json") for item in created],
        "message": f"成功生成 {len(created)} 个测试用例",
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
