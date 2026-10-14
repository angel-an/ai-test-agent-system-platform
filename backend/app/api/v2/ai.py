"""Minimal AI-compatible routes used by the document generation dialog."""

from __future__ import annotations

import hashlib
import re
from collections import Counter
from typing import Any
from uuid import UUID

from fastapi import APIRouter, File, Form, HTTPException, UploadFile, status

from app.api.deps import CurrentUserIdDep, DbSessionDep
from app.schemas.enums import Priority
from app.schemas.test_case import TestCaseCreate, TestStepCreate
from app.services.api_doc_parser_service import APIDocParserService
from app.services.requirement_structure import (
    FieldRule,
    StructuredRequirement,
    is_heading_line,
    parse_requirement_document,
)
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

#: 叙述性需求条目上限（原为 6：文档后半段与表格规则会整段丢失）
MAX_NARRATIVE_POINTS = 12
#: 每个字段规则最多产出的用例数（必填/枚举、边界值、条件显示）
MAX_CASES_PER_FIELD = 3
#: 单次生成的用例总量上限（安全阀，避免超大表格把用例库刷爆）
MAX_GENERATED_CASES = 80


@router.post("/generate-from-document", summary="从文档生成最小可用测试用例")
async def generate_from_document(
    project_identifier: str,
    current_user_id: CurrentUserIdDep,
    db: DbSessionDep,
    file: UploadFile = File(..., description="需求文档，支持 PDF/TXT/DOCX"),
    folder_id: UUID | None = Form(default=None),
    additional_prompt: str | None = Form(default=None),
    template: str = Form(default="test_case"),
    test_mode: str = Form(default="sit"),
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
    structure = _extract_structure(file.filename or "", file.content_type, content_bytes)
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
            # B1：把「解析器抽出的结构化字段规则数」交给门控。
            # 字段规格类文档（无 PRD 章节体、但有大量可判定字段规则）按适用档案放行，
            # 依据记录在 analysis.applicable_profile / profile_reason 与报告里。
            field_rule_count=len(structure.field_rules),
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
        # 可断言性预览（2026-09-25）：需求分析报告里直接说明
        # "哪些需求点提取不出可判定断言"，让缺口在生成前就可识别、可补充。
        assertability = _assertability_preview(text, structure, source)
        assertability_md = _render_assertability_markdown(
            assertability, len(structure.field_rules) if structure is not None else 0
        )
        return {
            "success": True,
            "test_cases": [],
            "message": (
                f"需求分析完成：{analysis.status.label}"
                f"（{analysis.status.value}）"
            ),
            **pipeline.response,
            "assertability_preview": assertability,
            "analysis_report_markdown": pipeline.analysis_report_markdown + assertability_md,
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
    module_hint = feature_points[0].feature_name if len(feature_points) == 1 else ""
    minimal_cases, assertion_gaps = _build_minimal_cases(
        text, source, test_mode=test_mode, module_hint=module_hint, structure=structure
    )
    for case in minimal_cases:
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

    # pipeline.response 中 testcase_generation.created_count 来自门控阶段（值为 0），
    # 与下方实际创建数不一致。以实际落库数覆盖，避免响应内两个数互相矛盾。
    response_payload = dict(pipeline.response)
    response_payload.setdefault("testcase_generation", {})
    response_payload["testcase_generation"]["created_count"] = len(created)

    # 结构化解析自证：字段规则数决定"这批用例有没有可执行断言"。
    # 实测 PR-9（PDF）：field_rule_count=0 时整批 36 条用例 0 断言，
    # 而响应里没有任何提示 —— 这里显式暴露，不再静默。
    structure_summary = structure.to_dict() if structure is not None else {}
    compilability_warning = _build_compilability_warning(structure_summary, len(created))

    # 断言缺口说明（2026-09-25）：生成完成时即点名哪些需求点/用例提取不出
    # 可判定断言、为什么、怎么补——方便识别补充，而不是等执行后看到
    # 一排「需人工执行」再回头查原因。
    assertion_gap_warning = _build_assertion_gap_warning(assertion_gaps)
    assertability = _assertability_preview(text, structure, source)
    assertability_md = _render_assertability_markdown(
        assertability, len(structure.field_rules) if structure is not None else 0
    )

    return {
        "success": True,
        "test_cases": [item.model_dump(mode="json") for item in created],
        "message": f"成功生成 {len(created)} 个测试用例",
        **response_payload,
        "feature_points": [fp.to_dict() for fp in feature_points],
        "analysis_report_markdown": pipeline.analysis_report_markdown + assertability_md,
        "requirement_structure": structure_summary,
        "compilability_warning": compilability_warning,
        "assertion_gap_warning": assertion_gap_warning,
        "assertion_gap_cases": assertion_gaps,
        "assertability_preview": assertability,
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


def _extract_structure(
    filename: str, content_type: str | None, content: bytes
) -> "StructuredRequirement":
    """结构化解析（段落/表格分离 + 字段规则）；失败降级为纯文本，不阻断上传。

    注意：降级会让**全部字段规则消失**（该批用例随之 0 条可执行断言），
    因此降级必须留痕（``degraded`` / ``degraded_reason``）并在响应里告警，
    否则"解析失败"与"文档本身没有字段表"在外部看起来完全一样。
    """
    try:
        return parse_requirement_document(filename, content_type, content)
    except Exception as exc:  # noqa: BLE001 —— 结构化是增强项，失败不得影响既有链路
        import logging

        logging.getLogger(__name__).warning(
            "[ai] 结构化解析失败，已降级为纯文本（字段规则将为空）: %s: %s",
            type(exc).__name__, exc,
        )
        text = _extract_text(filename, content_type, content)
        return StructuredRequirement(
            full_text=text,
            narrative_text=text,
            degraded=True,
            degraded_reason=f"{type(exc).__name__}: {exc}",
        )


def _build_minimal_cases(
    text: str,
    filename: str,
    *,
    test_mode: str = "sit",
    module_hint: str = "",
    structure: "StructuredRequirement | None" = None,
) -> list[TestCaseCreate]:
    # 仅保留与上传需求直接相关的通用生成逻辑；不再根据历史项目关键词
    # 只根据当前上传需求生成，不引入历史项目业务上下文。
    narrative_source = (structure.narrative_text if structure is not None else text) or text
    source_points = _extract_requirement_points(narrative_source, filename=filename)
    narrative_cases, assertion_gaps = (
        _build_requirement_cases(
            source_points, filename, test_mode=test_mode, module_hint=module_hint,
            field_rules=structure.field_rules if structure is not None else None,
        )
        if source_points
        else _build_requirement_cases(
            [filename.rsplit(".", 1)[0] or "上传需求"], filename,
            test_mode=test_mode, module_hint=module_hint,
            field_rules=structure.field_rules if structure is not None else None,
        )
    )

    field_rules = structure.field_rules if structure is not None else []
    if not field_rules:
        return narrative_cases, assertion_gaps

    module = narrative_cases[0].module if narrative_cases else ""
    field_cases = _build_field_rule_cases(
        field_rules, filename, test_mode=test_mode, module_hint=module_hint or module
    )
    # rev105：字段規則之外的**通用伴生場景**——人工用例對比發現框架只產「字段校驗」類，
    # 不產交互/系統/UI/分頁類。這裡按通用測試慣例補一組**與項目無關**的伴生用例
    # （名稱/步驟均為通用措辞），帶 test_case_steps，可作手工執行依據。
    companion_cases = _build_companion_cases(filename, test_mode=test_mode,
                                             module_hint=module_hint or module)
    # 字段规则用例本身携带断言，不产生缺口；缺口只来自叙述条目回链失败的部分
    return narrative_cases + field_cases + companion_cases, assertion_gaps


def _is_review_annotation(item: str) -> bool:
    """评审/确认批注行（不是需求）：如「秋刀：原功能没有“导出原文件”按钮，跟产品确认，去掉」。

    实测根因（2026-09-23 PR-2 重测批次）：docx 的「3. 待确认事项」问答表被当需求点，
    再经三场景扩写成 `…的必填或非法输入校验` 这类用例（TC-405179 / TC-939415）。
    这里只拦**明确的评审/确认语义**，不按"作者名"猜，避免误杀正常需求。
    """
    text = (item or "").strip()
    if not text:
        return False
    editorial = (
        r"原功能没有|跟产品确认|跟客户确认|待客户确认|待处理|待確認|已确认|已確認|已增加|已修改"
        r"|已补充|已補充|需确认|需確認|建议|建議|评审|評審|批注|批註|去掉|删除该|不展示|不显示"
        r"|需要查看|要不然|无法进行|已在[^，。；;]{0,12}(添加|补充|更新)"
    )
    return re.search(editorial, text) is not None


def _is_change_delta(item: str) -> bool:
    """变更清单条目（不是可测规则）：如「新增：小程序账户变动金额、和企业账户变动金额，」「删除：变动金额、」。

    这类行描述的是本次改动的增删，不是字段规则；实测被当成需求点后生成了
    「删除：变动金额、的边界条件处理」等无意义用例。
    """
    return re.match(r"^\s*(新增|删除|刪除|修改|变更|變更|调整|調整)\s*[:：]", (item or "").strip()) is not None


#: 段落级排除：这些章节整段都不是"可验证需求"（评审/确认、变更记录、术语等）
_REVIEW_SECTION_RE = re.compile(
    r"(待确认|待確認|已确认|已確認|评审|評審|批注|批註|问题清单|問題清單|遗留|遺留|风险|風險"
    r"|变更记录|變更記錄|修订记录|修訂記錄|修订历史|修訂歷史|会议纪要|會議紀要|确认记录|確認記錄)"
)
_CHANGE_SECTION_RE = re.compile(r"(变更说明|變更說明|改动说明|改動說明|版本记录|版本記錄|更新日志|更新日誌)")


def _section_kind(line: str) -> str:
    """按标题判断当前段落类型：``review`` / ``change`` / ``normal``。"""
    text = (line or "").strip()
    if not text or len(text) > 40:
        return "normal"
    if _REVIEW_SECTION_RE.search(text):
        return "review"
    if _CHANGE_SECTION_RE.search(text):
        return "change"
    return "normal"


#: 零宽字符（PDF 复制/协作编辑残留，实测出现在条目末尾）
_ZERO_WIDTH_RE = re.compile(r"[\u200b\u200c\u200d\ufeff]")
#: 行尾协作批注：**分隔符 + 作者(≤4字) + 月日 + 可选时间**。CS 规格书把批注附在条目末尾
#: （如「導出標題格式：cs_cases_YYYYMMDD_hhmmss（后為導出日期時間）一郎9月7日 17:15」），
#: 只拦行首的 `_is_doc_noise` 规则 1 覆盖不到。
#: 必须要求前置分隔符：否则 `{1,4}` 会把正文末尾两个字（"…消息列表一郎"）一并当成作者名吃掉。
_TRAILING_ANNOTATION_RE = re.compile(
    r"(^|[,，、。;；:：)）】\]\s\u200b\u200c\u200d])\s*@?\s*"
    r"[\w\u4e00-\u9fa5]{1,4}\s*\d{1,2}\s*月\s*\d{1,2}\s*日"
    r"(?:\s*\d{1,2}\s*[:：]\s*\d{2})?\s*$"
)
#: 导出文件名/模板样例的提示词与占位符
_EXPORT_SAMPLE_HINTS = ("導出標題格式", "导出标题格式", "【模板】", "【範本】", "件標題：", "件标题：")
_EXPORT_PLACEHOLDERS = ("YYYYMMDD", "yyyymmdd", "hhmmss", "HHMMSS")


def _clean_point_text(item: str) -> str:
    """去掉**行尾协作批注**与零宽字符，保留需求正文（不改动正文用词）。

    顺序有讲究：先按"分隔符 + 作者 + 月日 + 时间"剥离批注，再清零宽字符——
    否则零宽字符（实测批注前正好有一个）先被清掉，批注就与正文粘连而无法安全剥离。
    """
    text = _TRAILING_ANNOTATION_RE.sub(r"\1", item or "")
    text = _ZERO_WIDTH_RE.sub("", text)
    text = re.sub(r"[ \t\u3000]{2,}", " ", text).strip()
    return text.strip(" \u3000、,，;；")


def _is_export_artifact(item: str) -> bool:
    """导出文件名/模板样例——附件产物说明，不是可验证需求。

    实测（CS Module 方案説明2.0）：抽取到的前 3 条"需求点"分别是
    ``導出標題格式：cs_cases_YYYYMMDD_hhmmss（后為導出日期時間）``、
    ``【模板】cs_cases_20270101_100000.xlsx件標題：CS_Cases_YYYYMMDD_hhmmss``、
    ``導出標題格式：cs_task_YYYYMMDD_hhmmss（后為導出日期時間）cs_task_2026_0915_142345.xlsx``；
    三场景模板把它们膨胀成 9 条**无断言**的叙述用例（约占叙述侧的 1/4），属纯噪音。
    """
    text = (item or "").strip()
    if not text:
        return False
    if "【模板】" in text or "【範本】" in text or "【模板】" in text:
        return True
    if not any(token in text for token in _EXPORT_PLACEHOLDERS):
        return False
    if any(hint in text for hint in _EXPORT_SAMPLE_HINTS):
        return True
    # 具体样例文件名（含四位年/日期数字）
    return re.search(
        r"[\w]{2,}[_\-]\d{4,}[_\-]?\d{0,6}\.(xlsx|xls|csv|pdf|zip)", text, re.IGNORECASE
    ) is not None


def _is_doc_noise(item: str) -> bool:
    """判断一行是否属于"文档装饰/协作痕迹"，而不是可验证的需求。

    实测根因（PR-9 CS 批次 43 条里 36 条是噪音）：PDF 的批注/修订列被抽成纯文本后，
    「作者 + 中文月日 + 时间」这类协作痕迹长度 ≥8、又不是标题行，于是被当成需求条目，
    再经 12×3 模板膨胀成用例（如 TC-768564「一郎9月8日 14:39」）。
    这里只拦**明确无需求内容**的行，避免误杀正常需求。
    """
    text = (item or "").strip()
    if not text:
        return True
    # 1) 批注/修订头：作者(≤4字) + 月日（可选时间）**作为行首前缀**
    #    注意用前缀而不是整行匹配：平台会把基础条目膨胀成
    #    "…的必填或非法输入校验 / …的边界条件处理"，实测 TC-672229
    #    "曾志强8月31日 14:24的边界条件处理" 就是这样漏进来的。
    annotation = (
        r"^@?\s*[\w\u4e00-\u9fa5]{1,4}\s*\d{1,2}\s*月\s*\d{1,2}\s*日"
        r"(\s*\d{1,2}\s*[:：]\s*\d{2})?"
    )
    if re.match(annotation, text):
        return True
    # 2) 协作回复行：@某人 … / 已補充 / 已回覆（评论区内容不是需求）
    if re.match(r"^@[\w\u4e00-\u9fa5]{1,20}(\s|$)", text):
        return True
    if re.match(r"^@[\w\u4e00-\u9fa5]{1,20}\s*(已補充|已补充|已回覆|已回复|已修改|ok|OK)?\s*$", text):
        return True
    # 3) 导出样例名/文件标题标签：「件標題：CS_Cases_YYYYMMDD_hhmmss」
    if re.match(r"^\s*(件標題|文件标题|文件名|檔名|附件名|附件名稱)\s*[:：]", text):
        return True
    # 4) 纯样例/模板名（含日期时间占位符或日期数字），且整行就是一个名字
    if re.match(r"^[\w\u4e00-\u9fa5]{0,6}(CS_Cases|_?\d{8}_?|YYYYMMDD|hhmm?ss)[\w\u4e00-\u9fa5]{0,20}$",
                text, re.IGNORECASE) and not re.search(r"[，。；,;]", text):
        return True
    # 5) PDF 页码/分页标记
    if re.match(r"^\s*=+\s*(PAGE|页|頁)\s*\d+\s*=+\s*$", text, re.IGNORECASE):
        return True
    # 6) 纯编号/标点残片（如 "8." / "b." / "----"）
    if re.match(r"^[\d\s\.\-—、()（）a-hA-H]{1,6}$", text):
        return True
    # 7) 导出文件名/模板样例（附件产物说明，不是可验证需求）
    if _is_export_artifact(text):
        return True
    return False


def _extract_requirement_points(text: str, *, filename: str = "") -> list[str]:
    """从上传文本提取可读需求条目，作为最小闭环的生成依据。

    流程（2026-09-23 修正漂移）：先按**行**推进并跟踪段落类型——
    命中「待确认/评审/变更记录」等标题后，该段落内容**整段不参与**需求点抽取；
    其余段落再按标点切分，并逐条过滤元数据/标题/协作痕迹/评审批注/变更清单。
    """
    points: list[str] = []
    seen: set[str] = set()
    filename_stem = filename.rsplit(".", 1)[0].strip()
    section = "normal"
    metadata = r"^(文档版本|文檔版本|版本号|版本號|修改日期|修改人|修改內容|修改内容|修订历史|修訂歷史|软件需求规格说明书|軟件需求規格說明書|全称|定義/說明|备注|備註|评论|評論|V\d+(?:\.\d+)*|\d{4}[-/]\d{1,2}[-/]\d{1,2})"
    for raw_line in (text or "").splitlines():
        head = re.sub(r"^\s*(?:[-*#]|\d+[.)、])\s*", "", raw_line).strip()
        if not head:
            continue
        # 章节切换：标题行本身不生成用例，但决定后续行是否参与
        if is_heading_line(head) or _section_kind(head) != "normal":
            section = _section_kind(head)
            if re.match(r"^[\d一二三四五六七八九十]+(\.\d+)*[\s.、]", head) or _REVIEW_SECTION_RE.search(head) \
                    or _CHANGE_SECTION_RE.search(head):
                continue
        if section in {"review", "change"}:
            continue
        # 段落内再按标点切分成条目（保持既有行为）
        for raw in re.split(r"[。；;]+", raw_line):
            item = re.sub(r"^\s*(?:[-*#]|\d+[.)、])\s*", "", raw).strip()
            # 先清理零宽字符与**行尾协作批注**（CS 规格书把「作者+月日+时间」附在条目末尾），
            # 否则批注会粘进用例名，且长度虚高绕过 <8 字的最小长度门槛。
            item = _clean_point_text(item)
            if not item:
                continue
            if filename_stem and item.replace("\u200b", "").strip() == filename_stem:
                continue
            # 跳过 PDF 页眉、版本表头、日期和评论等元数据，避免它们被误生成用例。
            if (len(item) >= 8 and not re.match(metadata, item, re.IGNORECASE)
                    and not re.match(r"^(业务目标|前置条件|验收标准|功能点编号|需求范围|需求範圍|术语表|術語表)\s*[:：]?\s*$", item)
                    # 章节标题（"1.1 背景说明"/"2.1 企业充值/退款"…）不是可验证规则 → 不生成用例
                    and not is_heading_line(item)
                    # 协作痕迹/导出样例名/页码残片 → 不生成用例（漂移根因）
                    and not _is_doc_noise(item)
                    # 评审/确认批注（"秋刀：原功能没有…跟产品确认，去掉"）→ 不是需求
                    and not _is_review_annotation(item)
                    # 变更清单（"新增：…" / "删除：变动金额、"）→ 不是可测规则
                    and not _is_change_delta(item)
                    and item not in seen):
                seen.add(item)
                points.append(item[:120])
            if len(points) >= MAX_NARRATIVE_POINTS:
                return points
    return points


def _distinctive_field_aliases(
    field_rules: list[FieldRule],
) -> list[tuple[FieldRule, list[str]]]:
    """从字段说明表派生「可回链别名」，供叙述条目匹配字段规则。

    派生规则（保守，宁缺勿错）：
    - 字段名本身（如「会员手机号」）；
    - 去掉通用类型后缀的核心词（手机号/金额/文件/类型/方式/表头 → 「会员手机号」→「会员」）；
    - 按括号拆前缀（「上传文件（企业充值）」→「上传文件」）。
    再剔除**在 ≥2 个字段名中都出现**的歧义词（企业/账户/上传文件…），
    以及过短（<2 字）的词——避免把「充值流水」这类泛化叙述误链到「企业账户充值金额」。
    返回 [(rule, [别名按长度降序])]，只保留至少有一个可用别名的字段。
    """
    names = [(rule, (rule.name or "").strip()) for rule in field_rules]
    names = [(r, n) for r, n in names if n]
    candidate_map: list[tuple[FieldRule, set[str]]] = []
    for rule, name in names:
        cands: set[str] = {name}
        for suf in ("手机号", "金额", "文件", "类型", "方式", "表头", "备注"):
            if name.endswith(suf) and len(name) > len(suf):
                cands.add(name[: -len(suf)])
        m = re.match(r"^(.+?)（", name)
        if m and len(m.group(1)) >= 2:
            cands.add(m.group(1))
        candidate_map.append((rule, cands))
    counts = Counter(c for _, cs in candidate_map for c in cs)
    out: list[tuple[FieldRule, list[str]]] = []
    for rule, cands in candidate_map:
        keep = sorted(
            (c for c in cands if counts[c] == 1 and len(c) >= 2),
            key=len, reverse=True,
        )
        if keep:
            out.append((rule, keep))
    return out


def _match_field_for_point(
    point: str, aliases: list[tuple[FieldRule, list[str]]]
) -> tuple[FieldRule, str] | None:
    """叙述条目 → 字段规则回链：取最长命中别名的字段（一条至多链一个字段）。"""
    best: tuple[int, FieldRule, str] | None = None
    for rule, cands in aliases:
        for alias in cands:
            if alias and alias in point:
                if best is None or len(alias) > best[0]:
                    best = (len(alias), rule, alias)
    if best is None:
        return None
    return best[1], best[2]


#: 叙述场景类型 → 断言分组（与 _rule_assertions 的 by_kind 对齐）
_NARRATIVE_SCENARIO_ASSERTION_KIND = {"正向": "required", "反向": "required", "边界": "boundary"}


def _build_requirement_cases(
    points: list[str],
    filename: str,
    *,
    test_mode: str = "sit",
    module_hint: str = "",
    field_rules: list[FieldRule] | None = None,
) -> tuple[list[TestCaseCreate], list[dict[str, Any]]]:
    # 冒烟模式不再固定截断为 3 条；按上传需求提取出的核心条目动态生成。
    # 过滤和数量控制由需求内容与后续质量门禁决定。
    module = _derive_requirement_module(points, filename, module_hint=module_hint)
    scenarios: list[tuple[str, str, Priority]] = []
    for point in points:
        scenarios.append((point, "正向", Priority.HIGH if test_mode == "smoke" else Priority.MEDIUM))
        if test_mode != "smoke":
            scenarios.append((f"{point}的必填或非法输入校验", "反向", Priority.MEDIUM))
            scenarios.append((f"{point}的边界条件处理", "边界", Priority.MEDIUM))

    # 叙述条目规则回链（2026-09-25）：条目文本命中字段说明表字段的**独有**别名时，
    # 给该条场景用例挂上字段规则体 + 机器可判定断言，使其可被自动验证（compiled）。
    # 命不中的条目维持"只有规则编号"——诚实保持 dangling，不编造断言。
    aliases = _distinctive_field_aliases(field_rules or [])

    cases: list[TestCaseCreate] = []
    # 断言缺口（2026-09-25）：生成期即记录"哪些需求点提取不出可判定断言"，
    # 在响应里显式说明，方便识别与补充——而不是等执行后才发现整批 NOT_EXECUTED。
    assertion_gaps: dict[str, dict[str, Any]] = {}
    for title, keyword, priority in scenarios:
        # 场景标题在"反向/边界"下会带后缀，用原始条目匹配更稳：按场景前缀剥离
        base_point = title
        for suffix in ("的必填或非法输入校验", "的边界条件处理"):
            if base_point.endswith(suffix):
                base_point = base_point[: -len(suffix)]
                break
        linked: tuple[FieldRule, str] | None = None
        if aliases:
            linked = _match_field_for_point(base_point, aliases)
        rule_id = _rule_id("RQ", filename, title)
        custom_fields: dict[str, Any] = {"requirement_rule_id": rule_id}
        description = f"基于上传需求文档《{filename}》生成；场景来源：{title}。"
        if linked is not None:
            rule, alias = linked
            assertion_kind = _NARRATIVE_SCENARIO_ASSERTION_KIND.get(keyword, "required")
            custom_fields["requirement_rule"] = {
                "field": rule.name,
                "type": rule.field_type,
                "required": rule.required,
                "condition": rule.condition,
                "source": {
                    "from": "narrative_link",
                    "matched_alias": alias,
                    "point": title[:120],
                },
            }
            custom_fields["assertions"] = _rule_assertions(rule).get(assertion_kind, [])
            # 来源标记：文档抽取而来（文档变更后可重新生成；人工回写的会标 manual）
            custom_fields["assertions_source"] = "doc_extracted"
            description += f"；关联字段规则：{rule.name}（命中别名「{alias}」）。"
        else:
            # 生成期说明：这条用例为什么无法自动执行（非用例错误，是需求粒度不够）
            description += (
                "；未关联到字段说明表的字段规则，无法自动执行，需人工执行"
                "（补齐字段规则后重编译可转自动）。"
            )
            gap = assertion_gaps.setdefault(base_point, {
                "point": base_point,
                "titles": [],
                "reason": (
                    "该需求点为概述性描述，未关联到字段说明表的任何字段规则，"
                    "缺少可自动判定的输入与预期（无断言三要素：定位/动作/判定）"
                ),
                "suggestion": (
                    "① 在需求文档补充该功能点的字段说明表（字段/类型/是否必填/取值范围）后重新生成；"
                    "② 在生成的用例上人工补充规则体与断言（可通过 AI 助手完成），重编译后即可转自动执行"
                ),
            })
            gap["titles"].append(_case_title_from_requirement(title))
        cases.append(TestCaseCreate(
            name=_case_title_from_requirement(title),
            description=description,
            preconditions="已按上传需求准备角色、权限和前置数据。",
            priority=priority,
            test_case_steps=[
                TestStepCreate(step=f"进入需求描述的功能入口，执行：{title}", result="页面展示对应功能并接受本场景输入"),
                TestStepCreate(step="提交操作并检查页面、数据或状态变化", result="实际结果与该需求场景的验收标准一致，异常场景不产生错误数据"),
            ],
            module=module,
            keyword=keyword,
            risk_level="medium",
            case_kind=test_mode if test_mode in {"sit", "uat", "smoke", "api"} else "sit",
            tags=["AI生成", "需求来源", filename],
            # 命中字段的场景带规则体+断言（compiled）；未命中只给稳定规则编号（dangling，不编造断言）
            custom_fields=custom_fields,
        ))
    return cases, list(assertion_gaps.values())


#: 伴生場景模板（rev105）：通用測試慣例，**不含任何項目/業務信息**。
#: 每個模板 = (名稱, 前置, [(步驟, 預期)], 規則點文本)
_COMPANION_TEMPLATES: list[tuple[str, str, list[tuple[str, str]], str]] = [
    ("新建頁-保存按鈕-所有必填項填寫正確", "已登錄並打開新建頁面",
     [("按規則填寫全部必填字段與合法選填字段", "可保存成功並給出成功提示"),
      ("點擊保存按鈕", "數據落庫，列表可查到新記錄")],
     "保存正向路徑"),
    ("新建頁-保存按鈕-必填項未填完整", "已登錄並打開新建頁面",
     [("留空至少一個必填字段，其餘填合法值", "必填字段逐項給出必填提示"),
      ("點擊保存按鈕", "不提交；焦點定位到首個未通過校驗的字段")],
     "保存負向路徑"),
    ("新建頁-取消按鈕-點擊取消關閉且不保存", "已登錄並打開新建頁面",
     [("填寫部分字段後點擊取消按鈕", "頁面關閉返回列表；已填內容不落庫")],
     "取消語義"),
    ("編輯頁-只讀字段不可編輯", "已登錄並打開已有記錄的編輯頁面",
     [("嘗試修改標識類/系統生成類只讀字段", "字段為只讀態，無法輸入或修改")],
     "編輯態只讀約束"),
    ("查看模式-頁面置灰僅支持查看", "已登錄並打開記錄詳情/查看頁",
     [("檢查頁面字段狀態並嘗試輸入", "全部字段置灰不可編輯；無保存入口或保存不可用")],
     "查看態約束"),
    ("列表頁-分頁-默認每頁條數與總條數", "已登錄並打開列表頁且數據多於一頁",
     [("觀察默認分頁", "默認每頁條數正確；總條數顯示正確")],
     "分頁基礎"),
    ("列表頁-分頁-頁碼跳轉與每頁條數切換", "已登錄並打開列表頁",
     [("點擊頁碼跳轉並切換每頁條數", "數據正確切換；頁碼導航可用；跳轉頁功能正確")],
     "分頁導航"),
    ("列表頁-篩選-多條件組合查詢", "已登錄並打開帶篩選區的列表頁",
     [("同時設置多個篩選條件後查詢", "結果同時滿足全部條件")],
     "組合篩選"),
    ("列表頁-篩選-重置恢復默認", "已登錄並打開帶篩選區的列表頁",
     [("設置篩選條件後點擊重置/清空", "篩選條件恢復默認；列表恢復默認查詢結果")],
     "篩選重置"),
    ("列表頁-篩選-無匹配結果空態", "已登錄並打開帶篩選區的列表頁",
     [("輸入必無匹配的條件並查詢", "顯示空態提示（如無數據）；無報錯")],
     "篩選空態"),
    ("列表頁-日期範圍-起始晚於結束反向校驗", "已登錄並打開帶日期範圍篩選的列表頁",
     [("起始日期選擇晚於結束日期後查詢", "給出明確提示或自動糾正；不得查詢出錯或無響應")],
     "日期反向"),
    ("操作日誌-新建與編輯記錄準確", "已登錄且具備操作權限",
     [("分別執行一條新建與一條編輯", "日誌各記一條；操作人/時間/變更內容準確")],
     "審計日誌"),
    ("功能權限-有權限用戶可操作", "存在具備該功能權限的賬號",
     [("使用有權限賬號執行新建/編輯/刪除", "操作成功")],
     "權限正向"),
    ("功能權限-無權限用戶拒絕操作", "存在不具備該功能權限的賬號",
     [("使用無權限賬號訪問或操作", "入口隱藏或操作被拒並給出無權限提示")],
     "權限負向"),
]


def _build_companion_cases(
    filename: str,
    *,
    test_mode: str = "sit",
    module_hint: str = "",
) -> list[TestCaseCreate]:
    """按通用模板產伴生場景用例（帶步驟，可手工執行）。

    與項目無關：名稱/步驟全部通用；來源標記 companion_template 便於過濾。
    """
    cases: list[TestCaseCreate] = []
    for title, precond, steps, rule_point in _COMPANION_TEMPLATES:
        name = f"{title}"
        cases.append(TestCaseCreate(
            name=name,
            preconditions=precond,
            priority="medium",
            description=f"伴生場景（通用模板）：{rule_point}。來源文件：{filename}",
            custom_fields={
                "requirement_rule_id": f"COMPANION-{len(cases) + 1:02d}",
                "requirement_rule": {"field": "通用交互/系統場景", "rule": rule_point},
                "companion_template": True,
                "source_file": filename,
            },
            test_case_steps=[
                {"step": s, "result": e} for s, e in steps
            ] or None,
        ))
    return cases


def _build_field_rule_cases(
    field_rules: list[FieldRule],
    filename: str,
    *,
    test_mode: str = "sit",
    module_hint: str = "",
) -> list[TestCaseCreate]:
    """把「字段说明」表格规则转成用例（数据取自文档原值，预期可判定）。"""
    module = module_hint or _derive_requirement_module([], filename)
    case_kind = test_mode if test_mode in {"sit", "uat", "smoke", "api"} else "sit"
    cases: list[TestCaseCreate] = []
    for rule in field_rules:
        for case in _field_rule_cases(rule, filename, module, case_kind, test_mode):
            if len(cases) >= MAX_GENERATED_CASES:
                return cases
            cases.append(case)
    return cases


def _rule_id(prefix: str, filename: str, key: str) -> str:
    """稳定规则编号：同一文档 + 同一规则 → 同一编号（可追溯、可去重）。"""
    digest = hashlib.sha1(f"{filename}|{key}".encode("utf-8")).hexdigest()[:10]
    return f"{prefix}-{digest}"


def _build_compilability_warning(
    structure_summary: dict, created_count: int
) -> str | None:
    """字段规则数为 0 时的显式告警（否则整批用例静默地没有可执行断言）。

    实测 PR-9：上传 `CS Module方案説明2.0….pdf` 后 `field_rule_count=0`，
    36 条用例全部只有规则编号、没有断言，而响应里没有任何提示。
    """
    if int((structure_summary or {}).get("field_rule_count") or 0) > 0:
        return None
    summary = structure_summary or {}
    if summary.get("degraded"):
        return (
            "结构化解析失败，已降级为纯文本（field_rule_count=0）："
            f"生成的 {created_count} 条用例不含可执行断言，只能人工验证。"
            f"失败原因={summary.get('degraded_reason') or '(未记录)'}。"
            "这不是文档缺字段说明表，而是解析器没能跑完——请按上面的原因排查后重试。"
        )
    return (
        "本次未从文档中提取到任何「字段说明」规则（field_rule_count=0）："
        f"生成的 {created_count} 条用例不含可执行断言，只能人工验证，"
        "不会产生需求级自动验证证据。"
        f"（解析来源={summary.get('source_type')}，"
        f"重建表格数={summary.get('rebuilt_table_count')}，"
        f"重列化行数={summary.get('rebalanced_rows')}，"
        f"被拒绝的表格行={summary.get('rejected_field_count')}）"
        "请确认文档是否包含字段说明表，或其表头/列结构是否可被识别。"
    )


def _build_assertion_gap_warning(gaps: list[dict[str, Any]]) -> str | None:
    """生成完成时的断言缺口告警（无缺口返回 None）。

    实测根因（PR-2 xysj5 批次）：9 条 dangling 用例要到执行后才发现是
    NOT_EXECUTED，回头还得查需求文档才知道原因。生成期就点名缺口，
    方便识别与补充（补文档或补规则体均可转自动）。
    """
    if not gaps:
        return None
    case_count = sum(len(g.get("titles") or []) for g in gaps)
    point_names = "、".join(
        f"「{(g.get('point') or '')[:40]}」" for g in gaps[:4]
    )
    extra = f" 等 {len(gaps)} 个" if len(gaps) > 4 else ""
    return (
        f"有 {len(gaps)} 个需求点{extra}未提取到可判定断言"
        f"（对应 {case_count} 条用例将标记为「需人工执行」）：{point_names}。"
        "原因：这些需求点未关联到字段说明表的字段规则，缺少可自动判定的输入与预期。"
        "补充方式：① 在需求文档补充字段说明后重新生成；"
        "② 在用例上人工补充规则体与断言（可通过 AI 助手完成），重编译后即可转自动执行。"
    )


def _assertability_preview(
    text: str, structure: "StructuredRequirement | None", filename: str
) -> list[dict[str, Any]]:
    """逐需求点的可断言性预览：能否关联到字段说明规则（即能否产出可判定断言）。"""
    narrative_source = (structure.narrative_text if structure is not None else text) or text
    points = _extract_requirement_points(narrative_source, filename=filename)
    aliases = _distinctive_field_aliases(structure.field_rules if structure is not None else [])
    preview: list[dict[str, Any]] = []
    for point in points:
        linked = _match_field_for_point(point, aliases) if aliases else None
        preview.append({
            "point": point,
            "assertable": linked is not None,
            "matched_field": linked[0].name if linked else None,
        })
    return preview


def _render_assertability_markdown(
    preview: list[dict[str, Any]], field_rule_count: int
) -> str:
    """把可断言性预览渲染为 Markdown 段落，追加到需求分析报告末尾。"""
    if not preview:
        return ""
    assertable = [p for p in preview if p["assertable"]]
    not_assertable = [p for p in preview if not p["assertable"]]
    lines = [
        "",
        "## 可断言性预览（自动生成）",
        "",
        f"- 字段说明规则数：{field_rule_count}",
        f"- 可提取可判定断言的需求点：{len(assertable)} 个",
        (
            f"- 无法提取可判定断言的需求点：{len(not_assertable)} 个"
            "（对应用例将标记为「需人工执行」，非用例错误）"
        ),
    ]
    if not_assertable:
        lines += [
            "",
            "以下需求点为概述性描述，未关联到字段说明表的字段规则，缺少可自动判定的输入与预期：",
            "",
        ]
        for p in not_assertable:
            matched = p.get("matched_field")
            suffix = f"（最近似字段「{matched}」也未能唯一命中）" if matched else ""
            lines.append(
                f"- {p['point']}{suffix}"
                "——建议：补充字段说明/边界值/判定标准，或在用例上人工补充规则体与断言"
            )
    return "\n".join(lines) + "\n"


def _rule_assertions(rule: FieldRule) -> dict[str, list[dict[str, Any]]]:
    """按用例类型给出**机器可判定**的断言（数据 + 预期 + 文档原文依据）。

    断言只引用文档里真实存在的约束值，避免"填个字段就算覆盖"。
    """
    by_kind: dict[str, list[dict[str, Any]]] = {"required": [], "boundary": [], "condition": []}
    name = rule.name

    options = rule.constraint("options")
    if rule.required:
        by_kind["required"].append({
            "kind": "required", "field": name, "data": "",
            "expect": "reject", "rule": "是否必填=是",
        })
    if options:
        allowed = options.detail.get("options", [])
        by_kind["required"].append({
            "kind": "enum", "field": name, "data": "不存在的枚举值",
            "expect": "reject", "allowed": allowed, "rule": options.raw,
        })
        by_kind["required"].append({
            "kind": "enum", "field": name, "data": allowed[0] if allowed else "",
            "expect": "accept", "rule": options.raw,
        })
    default = rule.constraint("default")
    if default:
        by_kind["required"].append({
            "kind": "default", "field": name, "data": "",
            "expect": f"default={default.detail.get('value')}", "rule": default.raw,
        })

    range_c = rule.constraint("range")
    decimals = rule.constraint("decimal_places")
    if range_c and len(range_c.numbers) >= 2:
        low, high = range_c.numbers[0], range_c.numbers[1]
        step = 10 ** (-int(decimals.numbers[0])) if decimals and decimals.numbers else 1
        samples = [
            (low, "accept" if range_c.detail.get("min_inclusive") else "reject"),
            (low + step, "accept"),
            (high, "accept" if range_c.detail.get("max_inclusive") else "reject"),
            (high + step, "reject" if range_c.detail.get("max_inclusive") else "accept"),
        ]
        for value, expect in samples:
            by_kind["boundary"].append({
                "kind": "boundary", "field": name, "data": _num_fmt(value),
                "expect": expect, "rule": range_c.raw,
            })
    if decimals and decimals.numbers:
        digits = int(decimals.numbers[0]) + 1
        by_kind["boundary"].append({
            "kind": "precision", "field": name, "data": "1." + "2" * digits,
            "expect": "reject_or_truncate", "rule": decimals.raw,
        })

    max_len = rule.constraint("max_length")
    if max_len and max_len.numbers:
        limit = int(max_len.numbers[0])
        by_kind["boundary"].append({"kind": "max_length", "field": name,
                                    "data": f"{limit}字", "expect": "accept", "rule": max_len.raw})
        by_kind["boundary"].append({"kind": "max_length", "field": name,
                                    "data": f"{limit + 1}字", "expect": "reject", "rule": max_len.raw})

    max_cnt = rule.constraint("max_count")
    if max_cnt and max_cnt.numbers:
        limit = int(max_cnt.numbers[0])
        unit = max_cnt.detail.get("unit") or "条"
        by_kind["boundary"].append({"kind": "max_count", "field": name,
                                    "data": f"{limit}{unit}", "expect": "accept", "rule": max_cnt.raw})
        by_kind["boundary"].append({"kind": "max_count", "field": name,
                                    "data": f"{limit + 1}{unit}", "expect": "reject", "rule": max_cnt.raw})

    formats = rule.constraint("formats")
    if formats:
        allowed = formats.detail.get("formats", [])
        if allowed:
            by_kind["boundary"].append({"kind": "file_format", "field": name,
                                        "data": f".{allowed[0]}", "expect": "accept",
                                        "rule": formats.raw})
        by_kind["boundary"].append({"kind": "file_format", "field": name,
                                    "data": ".exe", "expect": "reject", "rule": formats.raw})
    size_c = rule.constraint("file_size")
    if size_c and size_c.numbers:
        limit = _num_fmt(size_c.numbers[0])
        by_kind["boundary"].append({"kind": "file_size", "field": name,
                                    "data": f"<={limit}M", "expect": "accept", "rule": size_c.raw})
        by_kind["boundary"].append({"kind": "file_size", "field": name,
                                    "data": f">{limit}M", "expect": "reject", "rule": size_c.raw})

    if rule.condition:
        by_kind["condition"].append({"kind": "visibility", "field": name,
                                     "when": rule.condition, "expect": "visible"})
        by_kind["condition"].append({"kind": "visibility", "field": name,
                                     "when": "不满足上述条件", "expect": "hidden"})
    return by_kind


def _field_rule_cases(
    rule: FieldRule,
    filename: str,
    module: str,
    case_kind: str,
    test_mode: str,
) -> list[TestCaseCreate]:
    source_location = f"{filename}#字段说明表 第{rule.source.get('row', '?')}行"
    rule_id = _rule_id("FR", filename, rule.name)
    assertions = _rule_assertions(rule)
    base_precondition = "已登录并进入需求描述的页面"
    if rule.condition:
        base_precondition += f"；{rule.condition}"

    def make(
        title: str,
        steps: list[tuple[str, str]],
        keyword: str,
        risk: str,
        assertion_kind: str,
    ) -> TestCaseCreate:
        return TestCaseCreate(
            name=title[:120],
            description=(
                f"基于上传需求文档《{filename}》的字段说明生成；字段：{rule.name}"
                f"（{rule.field_type or '未标注类型'}）"
                + (f"；显示条件：{rule.condition}" if rule.condition else "")
            ),
            preconditions=base_precondition,
            priority=Priority.MEDIUM,
            test_case_steps=[
                TestStepCreate(step=step, result=result) for step, result in steps
            ],
            module=module,
            keyword=keyword,
            risk_level=risk,
            case_kind=case_kind,
            source_location=source_location,
            tp_code=rule_id,
            tags=["AI生成", "需求来源", filename, "字段规则"],
            custom_fields={
                "requirement_rule_id": rule_id,
                "requirement_rule": {
                    "field": rule.name,
                    "type": rule.field_type,
                    "required": rule.required,
                    "condition": rule.condition,
                    "source": rule.source,
                },
                "assertions": assertions.get(assertion_kind, []),
                # 来源标记：文档抽取而来（人工回写的断言标 manual，见 test_assertion_service）
                "assertions_source": "doc_extracted",
            },
        )

    out: list[TestCaseCreate] = []
    options = rule.constraint("options")

    # 1) 必填 / 枚举校验
    if rule.required or options:
        values = options.detail.get("options", []) if options else []
        if values:
            steps = [
                (f"在「{rule.name}」选择合法值：{values[0]}", "字段接受该值，可继续提交"),
                (
                    f"在「{rule.name}」填入选项范围外的值：不存在的枚举值",
                    f"提交被拦截并提示取值非法（合法范围：{'、'.join(values[:4])}）",
                ),
            ]
            default = rule.constraint("default")
            if default:
                steps.append((
                    f"重新打开页面，查看「{rule.name}」默认值",
                    f"默认选中：{default.detail.get('value')}",
                ))
        else:
            steps = [
                (f"将「{rule.name}」留空后提交", f"提交被拦截并提示「{rule.name}」为必填"),
                (f"按文档要求填写「{rule.name}」后提交", "提交成功，字段值按输入保存"),
            ]
        out.append(make(
            f"{rule.name}-必填与取值校验", steps,
            "反向" if rule.required else "正向",
            "high" if rule.required else "medium",
            "required",
        ))

    # 2) 边界值（用文档里的真实区间/位数/长度/数量/格式/大小）
    range_c = rule.constraint("range")
    max_len = rule.constraint("max_length")
    max_cnt = rule.constraint("max_count")
    size_c = rule.constraint("file_size")
    formats_c = rule.constraint("formats")
    if range_c or max_len or max_cnt or size_c or formats_c:
        steps: list[tuple[str, str]] = []
        if range_c and len(range_c.numbers) >= 2:
            low, high = range_c.numbers[0], range_c.numbers[1]
            decimals = rule.constraint("decimal_places")
            step = 10 ** (-int(decimals.numbers[0])) if decimals and decimals.numbers else 1
            fmt = _num_fmt
            if not range_c.detail.get("min_inclusive"):
                steps.append((f"在「{rule.name}」输入 {fmt(low)}", f"被拒绝（文档要求取值 {range_c.raw}）"))
                steps.append((f"在「{rule.name}」输入 {fmt(low + step)}", "通过校验"))
            else:
                steps.append((f"在「{rule.name}」输入 {fmt(low)}", "通过校验"))
            if range_c.detail.get("max_inclusive"):
                steps.append((f"在「{rule.name}」输入 {fmt(high)}", "通过校验"))
                steps.append((f"在「{rule.name}」输入 {fmt(high + step)}", f"被拒绝（文档要求取值 {range_c.raw}）"))
            else:
                steps.append((f"在「{rule.name}」输入 {fmt(high)}", "被拒绝"))
            if decimals and decimals.numbers:
                steps.append((
                    f"在「{rule.name}」输入小数点后 {int(decimals.numbers[0]) + 1} 位数字（如 1.234）",
                    f"被拒绝或按最多 {int(decimals.numbers[0])} 位小数截断",
                ))
        if max_len and max_len.numbers:
            limit = int(max_len.numbers[0])
            steps.append((f"在「{rule.name}」输入 {limit} 个字", "通过校验"))
            steps.append((f"在「{rule.name}」输入 {limit + 1} 个字", "被拒绝或提示超出长度限制"))
        if max_cnt and max_cnt.numbers:
            limit = int(max_cnt.numbers[0])
            unit = max_cnt.detail.get("unit") or "条"
            steps.append((f"在「{rule.name}」填入 {limit} {unit}（逗号分隔）", "通过校验"))
            steps.append((f"在「{rule.name}」填入 {limit + 1} {unit}", "被拒绝并提示超出数量限制"))
        if formats_c or size_c:
            allowed = formats_c.detail.get("formats", []) if formats_c else []
            if allowed:
                steps.append((f"上传 .{allowed[0]} 文件", "上传成功"))
                steps.append(("上传 .exe 文件", f"被拒绝（文档仅支持：{'、'.join(allowed)}）"))
            if size_c and size_c.numbers:
                limit = size_c.numbers[0]
                steps.append((f"上传 {_num_fmt(limit)}M 以内的文件", "上传成功"))
                steps.append((f"上传超过 {_num_fmt(limit)}M 的文件", "被拒绝并提示文件过大"))
        if steps:
            out.append(make(
                f"{rule.name}-边界值与格式校验（{rule.summary()}）" if rule.summary()
                else f"{rule.name}-边界值与格式校验",
                steps, "边界", "high", "boundary",
            ))

    # 3) 条件显示
    if rule.condition and test_mode != "smoke":
        out.append(make(
            f"{rule.name}-条件显示校验",
            [
                (f"设置 {rule.condition} 后查看「{rule.name}」", "字段显示且可编辑"),
                (
                    "改为不满足上述条件的组合（如切换变动类型/变更方式）",
                    f"「{rule.name}」隐藏或不可用",
                ),
            ],
            "正向", "medium", "condition",
        ))

    return out[:MAX_CASES_PER_FIELD]


def _num_fmt(value: float) -> str:
    """数值格式化：保留小数精度（"%g" 只有 6 位有效数字，会截掉 99999.01）。"""
    text = f"{float(value):.10f}".rstrip("0").rstrip(".")
    return text if text else "0"


def _case_title_from_requirement(text: str) -> str:
    """保留需求原意，清理文档编号和泛化前缀，形成生产用例标题。"""
    value = re.sub(r"^\s*(?:功能点|需求验证|测试场景)\s*[-：:]\s*", "", text).strip()
    return value[:120] or "未命名需求场景"


def _derive_requirement_module(points: list[str], filename: str, *, module_hint: str = "") -> str:
    """从当前上传需求推导模块名，禁止使用固定项目模块。"""
    if module_hint.strip():
        return module_hint.strip()[:80]
    stem = filename.rsplit(".", 1)[0].strip()
    if stem:
        return stem[:80]
    if points:
        return points[0][:80]
    return "未命名需求"


