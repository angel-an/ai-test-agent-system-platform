"""Minimal AI-compatible routes used by the document generation dialog."""

from __future__ import annotations

import hashlib
import json
import logging
import re
from collections import Counter
from typing import Any
from uuid import UUID

from fastapi import APIRouter, File, Form, HTTPException, UploadFile, status

from app.api.deps import CurrentUserIdDep, DbSessionDep
from app.schemas.enums import Priority
from app.schemas.test_case import TestCaseCreate, TestStepCreate
from app.services.api_doc_parser_service import APIDocParserService
from app.services.product_confirmations import find_confirmation
from app.services.requirement_structure import (
    FieldRule,
    StructuredRequirement,
    is_heading_line,
    locate_evidence,
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
MAX_NARRATIVE_POINTS = 60
#: 每个字段规则最多产出的用例数（必填/枚举、边界值、条件显示）
MAX_CASES_PER_FIELD = 3
#: 单次生成的用例总量上限（安全阀，避免超大表格把用例库刷爆）
MAX_GENERATED_CASES = 80

#: 通用建议（伴生场景）专用模块名：这些用例**不是** PRD 条款，不强行归入业务大模块，
#: 用可自解释的模块名 + 「通用建议非PRD条款」标签双重标识，便于过滤与人工取舍。
GENERIC_SUGGESTION_MODULE = "通用建议（非PRD条款）"

#: 无业务模块归属的 PRD 用例（如"需求背景"章节里的叙事点）——同样用可自解释的模块名，
#: 不再让"文档名"充当模块（否则模块分布看起来像多了一个业务模块）。
UNMAPPED_MODULE = "未归属（非业务模块）"

#: 待确认项的两种来源（不得混为一谈）：
#: - document_ambiguity：文档自身矛盾/表述不清，需产品裁决；
#: - manual_supplement：文档未约定，需人工补充预期。
AMBIGUITY_DOCUMENT = "document_ambiguity"
AMBIGUITY_MANUAL_SUPPLEMENT = "manual_supplement"

#: 模糊词（rev183）：需求里这些词进不了断言——"尽量/适当"没有阈值，"等"没有闭集，
#: "支持多种"没有数量。此前它们静默变成"需人工执行"，用户只看到"没自动覆盖"却
#: 不知道是**文档写法**的问题。裁决：不做自动替换（替换会凭空造出阈值，跑绿也是假通过），
#: 只在生成期点名，让写文档的人把"适当限制"改成"最多20字"。
#: 刻意**不含**裸「等」——"等待""等于"会大面积误伤；只认「…等」列举未闭合的形态。
_VAGUE_WORD_PATTERNS: list[tuple[str, "re.Pattern[str]", str]] = [
    ("尽量", re.compile(r"尽量|盡量|尽可能|儘可能"), "「尽量/尽可能」无阈值，无法判定合格线"),
    ("适当", re.compile(r"适当|適當|合理(?:的)?(?:范围|範圍|限制|长度|長度)?"), "「适当/合理」没有可判定的界"),
    ("支持多种", re.compile(r"多种|多種|若干|一些|多个|多個"), "「多种/多个」没有闭集，枚举校验无法穷举"),
    ("等等", re.compile(r"等等|等其[它他]|之类的|之類的"), "「等等/之类」取值未闭合"),
    ("若干位", re.compile(r"若干位|若干個?|数量不限|數量不限|不限数量|不限數量"), "数量未给定，产出不了边界值"),
    ("视情况", re.compile(r"视情况|視情況|看情况|酌情|自行判断|自行判斷"), "「视情况/酌情」把判定权交给了人，机器无法判"),
    ("优化", re.compile(r"友好|友善|美观|美觀|优化|優化|流畅|流暢"), "体验类形容词，没有可观察的判定点"),
]
#: 需求点里出现这些"具体数值"就不算模糊（「尽量控制在20字以内」「3秒内」其实可判定）
_CONCRETE_VALUE_RE = re.compile(
    r"\d+(?:\.\d+)?\s*(?:个字|個字|字|位|条|條|个|個|次|天|秒|毫秒|分钟|分鐘|小时|小時|"
    r"M|MB|G|GB|KB|%|px|元)"
)
#: rev184：这些「多」是在讲**结构/格式/数量关系**，不是"取值没列完"，不能算模糊词。
#: 实测真实文档：「有多个用英文逗号分开」= 分隔规则（完全可判定），
#: 却被「多个」命中并报「枚举校验无法穷举」——告警说错方向比不告警更糟。
_VAGUE_MULTI_FALSE_POSITIVE_RE = re.compile(
    r"多[个個条條项項]?\s*(?:用|以|之间|之間|分隔|隔开|隔開|拼接|合并|合併|并列|並列)"
    r"|(?:英文|中文|逗号|逗號|分号|分號|顿号|頓號|空格|换行|換行)\s*多[个個]"
    r"|多[个個]\s*(?:选项|選項)?\s*(?:之间|之間)\s*(?:用|以)"
)

#: 子章节标题 → 场景标签（同名用例靠它区分「哪个页面/哪张表」的同一字段）
_SCENARIO_LABELS: tuple[tuple[str, str], ...] = (
    ("篩選查詢", "列表頁篩選查詢"),
    ("筛选查询", "列表頁篩選查詢"),
    ("字段說明", "新增/編輯表單字段"),
    ("字段说明", "新增/編輯表單字段"),
    ("列表顯示", "列表顯示"),
    ("列表显示", "列表顯示"),
    ("導入", "導入"),
    ("导入", "導入"),
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
    minimal_cases, assertion_gaps, expand_skips = _build_minimal_cases(
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
    expand_skip_warning = _build_expand_skip_warning(expand_skips)
    # 模糊词（rev183）：不自动改写，只在生成期点名——「适当/尽量/多种」这类描述
    # 是文档写法问题，凭空补一个阈值会把假通过写进断言。
    vague_hits = scan_vague_words(
        (structure.narrative_text if structure is not None else text) or text,
        filename=source,
    )
    vague_warning = _build_vague_word_warning(vague_hits)
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
        "expand_skip_warning": expand_skip_warning,
        "expand_skip_points": expand_skips,
        "vague_word_warning": vague_warning,
        "vague_word_hits": vague_hits,
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
    narrative_source = structure.narrative_text if structure is not None else text
    source_points = _extract_requirement_points(narrative_source, filename=filename)
    narrative_cases, assertion_gaps, expand_skips = _build_requirement_cases(
        source_points, filename, test_mode=test_mode, module_hint=module_hint,
        field_rules=structure.field_rules if structure is not None else None,
    )

    module = narrative_cases[0].module if narrative_cases else ""
    # rev108：列表顯示字段表 → 欄位完整性校驗用例。放在 field_rules 早退**之前**——
    # 文檔可以只有列表表沒有字段表，此類用例不應隨 field_rules 歸零而消失。
    list_cases = _build_list_display_cases(
        structure.display_columns if structure is not None else [],
        filename, module_hint=module_hint or module)
    field_rules = structure.field_rules if structure is not None else []
    import_source = structure.full_text if structure is not None else text
    import_cases = _build_import_clause_cases(import_source, filename)
    supplement_cases = _build_import_supplement_cases(import_source, filename)
    export_cases = _build_export_detail_cases(
        structure, filename, module_hint=module_hint or module)
    if not field_rules:
        cases = _assign_case_metadata(
            narrative_cases + list_cases + import_cases + supplement_cases + export_cases,
            import_source, filename, structure)
        return _stamp_document_fingerprint(
            _resolve_duplicate_titles(cases), text), assertion_gaps, expand_skips

    field_cases = _build_field_rule_cases(
        field_rules, filename, test_mode=test_mode, module_hint=module_hint or module
    )
    # rev105：字段規則之外的**通用伴生場景**——人工用例對比發現框架只產「字段校驗」類，
    # 不產交互/系統/UI/分頁類。這裡按通用測試慣例補一組**與項目無關**的伴生用例
    # （名稱/步驟均為通用措辞），帶 test_case_steps，可作手工執行依據。
    companion_cases = _build_companion_cases(filename, test_mode=test_mode,
                                             module_hint=module_hint or module)
    # rev107：篩選字段用例 + 導入條款用例（人工用例對比發現的兩類結構性缺口）
    filter_cases = _build_filter_field_cases(
        field_rules, filename, module_hint=module_hint or module)
    # rev109（P1）：地圖組件交互 + 只讀字段用例——證據都在字段說明列的敘述文字裡，
    # 表格行不進敘事通道，此前無任何用例承接；證據隨 field_rules，故在早退之後。
    map_cases = _build_map_interaction_cases(
        field_rules, filename, module_hint=module_hint or module)
    read_only_cases = _build_read_only_field_cases(
        field_rules, filename, module_hint=module_hint or module)
    # rev185：關聯對象「配置保真」場景族——適用門店/適用商品這類關聯選擇器字段，
    # 生成「選 N 個 → 保存 → 重開 → 逐字段比對回顯」端到端用例。字段級校驗
    # （必填/取值/邊界）覆蓋不了保存→落庫→重開→回顯鏈路，此前整族缺失。
    echo_cases = _build_persistence_echo_cases(
        field_rules, filename, module_hint=module_hint or module)
    # 字段规则用例本身携带断言，不产生缺口；缺口只来自叙述条目回链失败的部分
    cases = _assign_case_metadata(narrative_cases + field_cases + companion_cases
            + filter_cases + import_cases + list_cases
            + map_cases + read_only_cases + echo_cases + supplement_cases + export_cases,
            import_source, filename, structure)
    return _stamp_document_fingerprint(
        _resolve_duplicate_titles(cases), text), assertion_gaps, expand_skips


def _stamp_document_fingerprint(
    cases: list[TestCaseCreate], text: str
) -> list[TestCaseCreate]:
    """给每条用例盖上**文档内容指纹**（P1-2）：只记文件名无法回答"这批用例是不是按最新
    PRD 生成的"。指纹取"解析后全文"的 sha1——文档内容一变指纹就变，重新生成后可直接比对。"""
    digest = hashlib.sha1((text or "").encode("utf-8")).hexdigest()
    for case in cases:
        cf = dict(case.custom_fields or {})
        cf["source_text_sha1"] = digest
        cf["source_text_chars"] = len(text or "")
        case.custom_fields = cf
    return cases


def _assign_case_metadata(
    cases: list[TestCaseCreate], text: str, filename: str,
    structure: StructuredRequirement | None,
) -> list[TestCaseCreate]:
    """按 PRD 一级章节归属补齐元数据；无唯一证据时保持原模块推导。"""
    def compact(value: str) -> str:
        return re.sub(r"[\s\u200b\u200c\u200d\ufeff，。；、：:,.（）()]", "", value or "")

    sections = [(name, compact(body)) for name, body in _prd_sections(text)]
    # 表内同名字段共享章节归属：用同表的独有说明定位，避免「创建人」误归类。
    table_owners: dict[Any, set[str]] = {}
    if structure:
        for r in structure.field_rules:
            token = compact(r.description)
            owners = {n for n, b in sections if len(token) >= 8 and token in b}
            if len(owners) == 1:
                table_owners.setdefault(r.source.get("table"), set()).update(owners)

    rules = structure.field_rules if structure else []
    kinds = (("filter_field_case", "筛选校验"), ("import_clause_case", "导入校验"),
             ("list_display_case", "列表校验"), ("map_interaction_case", "地图交互"),
             ("read_only_case", "只读校验"), ("companion_template", "通用伴生"),
             ("manual_supplement", "人工补充"))
    for case in cases:
        cf = dict(case.custom_fields or {})
        rule = cf.get("requirement_rule") or {}
        field = str(rule.get("field") or "")
        source_owners = table_owners.get((rule.get("source") or {}).get("table"), set())
        evidence = [str(cf.get("source_evidence") or "")]
        if cf.get("import_clause_case"):
            evidence.append(str(rule.get("rule") or "").split("；子项：")[0])
        elif cf.get("filter_field_case"):
            evidence.append(str(rule.get("rule") or "").split("匹配規則：", 1)[-1])
            evidence.append(field)
            filter_owners = set()
            for source_rule in rules:
                if source_rule.name == field and _is_filter_field_rule(source_rule):
                    filter_owners.update(table_owners.get(source_rule.source.get("table"), set()))
            if len(filter_owners) == 1:
                source_owners = filter_owners
        elif cf.get("list_display_case") and structure:
            for table in structure.display_columns:
                names = [c["name"] for c in table.get("columns", []) if c.get("name")]
                if names and all(name in str(rule.get("rule") or "") for name in names):
                    evidence.extend(names)
        else:
            evidence.append(field)
            narrative = cf.get("source_point") or ""
            if narrative:
                evidence.append(narrative)
            for source_rule in rules:
                if source_rule.name == field or (cf.get("map_interaction_case") and
                        any(m in source_rule.description for m in _MAP_MARKERS)):
                    evidence.append(source_rule.description)
        owners: set[str] = set(source_owners) if len(source_owners) == 1 else set()
        for value in evidence:
            token = compact(value)
            if len(token) < 3:
                continue
            matches = {name for name, body in sections if token in body}
            if len(matches) == 1 and not source_owners:
                owners.update(matches)
        previous = case.module or _derive_requirement_module([], filename)
        # 证据定位算一次，两处用：① 归属回退（无唯一归属时）；② source_location 统一（P1-1）
        location = _locate_case_evidence(cf, structure, text)
        if len(owners) == 1 and not cf.get("companion_template"):
            case.module = next(iter(owners))
            cf["original_module"] = previous
            cf["module_source"] = "prd_section"
        elif cf.get("companion_template"):
            # 通用建议（伴生场景）**不强行归类**：名称/步骤均为通用惯例，没有 PRD 归属证据。
            # 用可自解释的模块名 + 标签双重标识，绝不塞进业务大模块。
            case.module = GENERIC_SUGGESTION_MODULE
            cf["original_module"] = previous
            cf["module_source"] = "generic_suggestion"
        else:
            # 回退证据：把该用例的证据钉回文档行号 → 最近标题 → 一级章节。
            # 命不中就保持原模块（宁可留在未归属分组，也不猜归属），并如实记录原因。
            if location and location.get("section") in {n for n, _ in sections}:
                case.module = location["section"]
                cf["original_module"] = previous
                cf["module_source"] = "prd_section_evidence"
                cf["module_evidence"] = {
                    "line": location["line"],
                    "heading": location["heading"],
                    "heading_title": location.get("heading_title", ""),
                    "scope": location.get("scope", ""),
                    "unique": location.get("unique", False),
                }
            else:
                stem_module = _derive_requirement_module([], filename)
                if previous == stem_module:
                    # 无业务模块归属 → 用可自解释的名字，别让"文档名"看起来像业务模块
                    case.module = UNMAPPED_MODULE
                    cf["module_source"] = "unmapped"
                else:
                    case.module = previous
                cf["original_module"] = previous
                if location:
                    cf["module_note"] = (
                        "证据位于《{heading}》，该章节不是文档一级业务模块，"
                        "故不强行归类".format(
                            heading=location.get("heading") or location.get("section") or "")
                    )
                else:
                    cf["module_note"] = "未在文档中定位到该用例证据，不强行归类"
        category = next((label for key, label in kinds if cf.get(key)), "")
        tags = list(case.tags or []) + ["AI生成", "需求来源", filename]
        if cf.get("companion_template"):
            tags = [t for t in tags if t != "需求来源"]
            tags.append("通用建议非PRD条款")
            cf["coverage_source"] = "generic_suggestion"
        if category:
            tags.append(category)
        if cf.get("manual_supplement"):
            tags.append("待确认")
        # 歧义判定只看**该用例自身的规则证据**：早期版本用"同名字段所有规则的描述拼接"
        # 做判定，会把 A 规则的歧义扣到 B 规则生成的用例上（实测 YL：筛选区「店鋪編號」
        # 规则本身无歧义，却被连带标成待确认）。
        point = str(rule.get("evidence") or "") or str(cf.get("source_point") or "")
        ambiguity = ""
        if re.search(r"未填.{0,10}店[鋪铺][編编][號号].{0,20}生成品牌[編编][號号]", point):
            ambiguity = "未填写店铺编号却约定生成品牌编号，编号对象需产品确认"
        if re.search(r"支付完成|支付完", point) and re.search(r"30\s*(?:min|分钟)", point, re.I) and re.search(r"支付完成\s*20\s*(?:min|分钟)", text, re.I):
            ambiguity = "正文30分钟与批注20分钟不一致，批注不作为正式规则，时点需确认"
        if ambiguity:
            # 产品已裁决过的歧义：不再标待确认，直接用结论形成**可判定预期**
            # （否则每次重生成都会把已裁决项重新标成待确认）。
            confirmed = find_confirmation(
                filename, ambiguity,
                f"{case.name} {cf.get('source_point') or ''} {rule.get('evidence') or ''}")
            if confirmed:
                cf["confirmation_status"] = "confirmed"
                cf["confirmation_key"] = confirmed["key"]
                cf["confirmed_conclusion"] = confirmed["conclusion"]
                cf["confirmed_by"] = confirmed.get("confirmed_by")
                cf["confirmed_at"] = confirmed.get("confirmed_at")
                cf["confirmation_source"] = confirmed.get("source")
                cf["ambiguity_kind"] = AMBIGUITY_DOCUMENT
                cf["ambiguity_reason"] = ambiguity
                if confirmed.get("config_landing"):
                    cf["confirmation_config_landing"] = confirmed["config_landing"]
                if confirmed.get("open_questions"):
                    cf["confirmation_open_questions"] = confirmed["open_questions"]
                cf["executable_track"] = "manual"
                case.description = (case.description or "") + "；产品已确认：" + confirmed["conclusion"]
                if confirmed.get("step_result"):
                    steps = case.test_case_steps or []
                    if steps:
                        steps[-1].result = confirmed["step_result"]
                tags.append("已确认")
            else:
                cf["confirmation_status"] = "pending"
                cf["ambiguity_kind"] = AMBIGUITY_DOCUMENT
                cf["ambiguity_reason"] = ambiguity
                cf["executable_track"] = "manual"
                cf.pop("assertions", None)
                case.name = "待确认-" + case.name[:115]
                case.description = (case.description or "") + "；待确认：" + ambiguity
                for step in case.test_case_steps or []:
                    step.result = "记录实际结果；产品确认文档歧义后再判定通过或失败"
                tags.append("待确认")
        # 统一来源位置（P1-1）：字段规则用例已有"字段说明表 第N行"，其余用例此前只有文件名；
        # 既然证据已定位到行号/子章节，就把它写进 source_location，UI 里追溯一致。
        if not case.source_location and location and location.get("line") is not None:
            heading = location.get("heading") or location.get("heading_title") or ""
            case.source_location = (
                f"{filename}#第{location['line']}行" + (f"《{heading}》" if heading else ""))
        case.tags = list(dict.fromkeys(tags))
        case.custom_fields = cf
    return cases


def _case_evidence_candidates(cf: dict[str, Any]) -> list[str]:
    """该用例「证据文本」的候选（按可信度排序，去重保序）。

    只放**来自文档的原文**（字段描述 / 导入条款 / 需求条目），不放模板合成句
    （如「篩選字段「X」匹配規則：…」这种拼出来的文本在文档里当然找不到）。
    """
    rule = cf.get("requirement_rule") or {}
    candidates = [
        str(rule.get("evidence") or ""),
        str(rule.get("description") or ""),
        str(cf.get("source_evidence") or ""),
    ]
    if cf.get("import_clause_case") or cf.get("manual_supplement"):
        candidates.insert(0, str(rule.get("rule") or "").split("；子项：")[0])
    if cf.get("source_point"):
        candidates.insert(0, str(cf["source_point"]))
    out: list[str] = []
    for value in candidates:
        value = value.strip()
        if value and value not in out:
            out.append(value)
    return out


def _locate_case_evidence(
    cf: dict[str, Any],
    structure: StructuredRequirement | None,
    text: str,
) -> dict[str, Any] | None:
    """把用例证据定位到文档位置（返回**定位结果**，是否采纳由调用方按章节白名单判断）。

    - 字段规则优先用解析期算好的定位（``source.section``，来自表头所在子章节）；
      其次用 ``structure.table_locations[表号]``；
    - 其余（需求条目/导入条款/人工补充）按证据原文定位，有页码就限定页内；
    - 定位不到返回 ``None``。
    """
    rule = cf.get("requirement_rule") or {}
    source = rule.get("source") or {}
    table = source.get("table") if isinstance(source, dict) else None
    page = source.get("page") if isinstance(source, dict) else None

    if isinstance(source, dict) and source.get("section"):
        return {"section": source["section"], "line": source.get("line"),
                "heading": source.get("heading", ""),
                "heading_title": source.get("heading_title", ""),
                "scope": "field_rule", "unique": True}
    table_location = (getattr(structure, "table_locations", None) or {}).get(table)
    if table_location and table_location.get("section"):
        return {**table_location, "scope": "table_header"}

    page_line_counts = getattr(structure, "page_line_counts", None) or []
    for candidate in _case_evidence_candidates(cf):
        location = locate_evidence(
            text, candidate, page=page, page_line_counts=page_line_counts)
        if location and location.get("section"):
            return location
    return None


def _case_scenario_label(case: TestCaseCreate) -> str:
    """同名用例的区分标签：优先文档子章节标题（原文证据），其次用例类别，最后表/行号。

    **只描述来源位置**，不下业务结论——标题里出现的是「哪张表/哪个页面」，
    不是猜出来的业务语义。
    """
    cf = case.custom_fields or {}
    rule = cf.get("requirement_rule") or {}
    source = rule.get("source") if isinstance(rule.get("source"), dict) else {}
    heading_title = ""
    evidence = cf.get("module_evidence") or {}
    if evidence.get("heading_title"):
        heading_title = str(evidence["heading_title"])
    elif source.get("heading_title"):
        heading_title = str(source["heading_title"])
    elif source.get("heading"):
        heading_title = str(source["heading"])
    for keyword, label in _SCENARIO_LABELS:
        if keyword in heading_title:
            return label
    if heading_title:
        return heading_title
    category = next((label for key, label in (
        ("filter_field_case", "列表頁篩選查詢"),
        ("list_display_case", "列表顯示"),
        ("map_interaction_case", "地圖交互"),
        ("read_only_case", "只讀校驗"),
        ("import_clause_case", "導入校驗"),
        ("manual_supplement", "人工補充"),
        ("companion_template", "通用伴生場景"),
    ) if cf.get(key)), "")
    if category:
        return category
    table, row = source.get("table"), source.get("row")
    if table is not None and row is not None:
        return f"來源表{table}第{row}行"
    if source.get("line") is not None:
        return f"來源第{source['line']}行"
    return ""


def _duplicate_identity(case: TestCaseCreate) -> tuple:
    """判断「是否同一条规则」的身份：规则 ID + 表/行 + 步骤 + 断言。"""
    cf = case.custom_fields or {}
    rule = cf.get("requirement_rule") or {}
    source = rule.get("source") if isinstance(rule.get("source"), dict) else {}
    steps = [(s.step, s.result) for s in (case.test_case_steps or [])]
    return (
        str(cf.get("requirement_rule_id") or ""),
        source.get("table"), source.get("row"),
        json.dumps(steps, ensure_ascii=False),
        json.dumps(cf.get("assertions") or [], ensure_ascii=False, sort_keys=True),
    )


def _resolve_duplicate_titles(cases: list[TestCaseCreate]) -> list[TestCaseCreate]:
    """同名标题收口：同一规则重复 → 去重；不同来源/场景 → 标题补足场景，规则 ID 全部保留。

    背景（本轮质量问题）：YL 文档同一字段在两处出现（新建/编辑表单「字段说明」与
    列表页「筛选查询区」），生成出两条**同名**用例而规则/来源不同——既不像重复，
    也无法只看标题判断该测哪个页面。此处按证据分别处理，并记录可审计的去重/改名结果。

    标签冲突时才升级：场景标签仍重名（如商品与支付各自的「筛选查询区」）→ 加模块前缀；
    仍重名 → 追加规则 ID。任何一步都不改动规则 ID，追踪链完整。
    """
    groups: dict[str, list[TestCaseCreate]] = {}
    for case in cases:
        title = re.sub(r"^待确认-", "", case.name or "").strip()
        groups.setdefault(title, []).append(case)

    kept: list[TestCaseCreate] = []
    removed = 0
    renamed: list[dict[str, Any]] = []
    seen_ids: set[int] = set()
    for title, items in groups.items():
        if len(items) == 1:
            continue
        identities = [_duplicate_identity(c) for c in items]
        if len(set(identities)) == 1:
            # 同一条规则被重复产出：保留首条，其余丢弃（并留痕）
            for extra in items[1:]:
                seen_ids.add(id(extra))
                removed += 1
            cf = dict(items[0].custom_fields or {})
            cf["duplicate_removed"] = len(items) - 1
            items[0].custom_fields = cf
            continue
        # 不同规则/不同来源：标题必须能自解释，规则 ID 原样保留以维持追踪
        labels = []
        for case in items:
            cf = case.custom_fields or {}
            label = _case_scenario_label(case)
            if not label:
                source = ((cf.get("requirement_rule") or {}).get("source") or {})
                table, row = source.get("table"), source.get("row")
                label = (f"來源表{table}第{row}行" if table is not None and row is not None
                         else "來源位置待補")
            labels.append(label)
        if len(set(labels)) != len(labels):
            labels = [f"{c.module}-{label}" for c, label in zip(items, labels)]
        if len(set(labels)) != len(labels):
            labels = [f"{label}｜{(c.custom_fields or {}).get('requirement_rule_id')}"
                      for c, label in zip(items, labels)]
        for case, label in zip(items, labels):
            pending_prefix = "待确认-" if (case.name or "").startswith("待确认-") else ""
            body = (case.name or "")[len(pending_prefix):]
            case.name = f"{pending_prefix}{body}（{label}）"[:120]
            cf = dict(case.custom_fields or {})
            cf["title_disambiguation"] = label
            case.custom_fields = cf
            renamed.append({
                "title": title, "label": label, "module": case.module,
                "rule_id": cf.get("requirement_rule_id"),
            })
    for case in cases:
        if id(case) not in seen_ids:
            kept.append(case)
    if removed or renamed:
        logging.getLogger(__name__).info(
            "[ai] 同名标题收口：去重 %s 条；按文档场景明确标题 %s 条：%s",
            removed, len(renamed), json.dumps(renamed, ensure_ascii=False),
        )
    return kept


def _prd_sections(text: str) -> list[tuple[str, str]]:
    """取文档最上层的业务章节；嵌套页面不覆盖已有大模块。"""
    heading = re.compile(r"^\s*(\d+(?:[.．]\d+)*)\s*[.．、]?\s+([^\d].{1,35})$")
    candidates = []
    for index, line in enumerate((text or "").splitlines()):
        match = heading.match(_ZERO_WIDTH_RE.sub("", line))
        if (match and is_heading_line(line)
                and re.search(r"管理|主數據|主数据|任务|任務|模块|模塊|中心|设置|設置|问卷|問卷|评价|評價", match.group(2))
                and not re.search(r"必填|限制|字符|可编辑|可編輯|不可|支持|允许|允許|不得|用于|用於|显示|顯示|点击|點擊|校验|校驗|初始|完成|未填|未选", match.group(2))):
            candidates.append((index, match.group(1).count("."), match.group(2).strip()))
    if not candidates:
        return []
    depth = min(d for _, d, _ in candidates)
    boundaries = [(i, n) for i, d, n in candidates if d == depth]
    raw_lines = (text or "").splitlines()
    return [(name, "\n".join(raw_lines[start + 1:end]))
            for (start, name), (end, _) in zip(boundaries, boundaries[1:] + [(len(raw_lines), "")])]




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
        if re.match(r"^\s*[a-zA-Z][.．、]\s*", raw_line) and re.search(r"导入|導入", text or ""):
            continue  # 导入子项只走继承父条款的专用通道
        clean_line = _clean_point_text(raw_line)
        stem = _clean_point_text(filename_stem)
        title_base = re.split(r"[（(]", stem, maxsplit=1)[0].strip()
        if ((title_base and clean_line.startswith(title_base))
                or re.search(r"已[锁鎖]定.{0,8}勿改", clean_line)
                or re.match(r"^字符[类類]型\s*[:：]", clean_line)):
            continue
        head = re.sub(r"^\s*(?:[-*#]|\d+[.)、])\s*", "", raw_line).strip()
        if not head:
            continue
        # 章节切换：标题行本身不生成用例，但决定后续行是否参与
        if re.match(r"^\s*\d+(?:\.\d+)+\s+", raw_line) and not re.search(r"支持|必须|必須|不得|校验|校驗", raw_line):
            continue
        if is_heading_line(head) or _section_kind(head) != "normal":
            section = _section_kind(head)
            if re.match(r"^[\d一二三四五六七八九十]+(\.\d+)*[\s.、]", head) or _REVIEW_SECTION_RE.search(head) \
                    or _CHANGE_SECTION_RE.search(head):
                continue
        if section in {"review", "change"}:
            continue
        # 段落内再按标点切分成条目（保持既有行为）
        for raw in re.split(r"[。；;]+", raw_line):
            item = re.sub(r"^\s*(?:[-*#]|\d+(?:\.\d+)*[.)、]?\s+|[a-zA-Z][.．、])\s*", "", raw).strip()
            # 先清理零宽字符与**行尾协作批注**（CS 规格书把「作者+月日+时间」附在条目末尾），
            # 否则批注会粘进用例名，且长度虚高绕过 <8 字的最小长度门槛。
            item = _clean_point_text(item)
            # rev133：状态矩阵表（状态｜说明｜可操作按钮）的行会被 PDF 文本抽取**并进同一条叙述行**，
            # 产出「操作：支持新建…导出明细可操作按钮待启用初始创建完成的问卷状态即为待启用…」
            # 这类 120 字的用例名（复验7 TC-644460 实测）。表头词「可操作按钮」之后是表格残渣，
            # 不是本条需求的延续 → 在表头词处截断；矩阵本身仍走独立的「状态×按钮」通道（已知缺口）。
            item = re.split(r"可操作按钮|可操作按鈕", item)[0].strip(" ，、,;；")
            if _is_incomplete_clause(item) or re.search(r"[\w\u4e00-\u9fa5]{1,4}\d{1,2}月\d{1,2}日", item):
                continue
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
                    and (not _is_review_annotation(item) or bool(re.search(r"若|如果|取消|状态|狀態", item)))
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
        if not keep:
            peers = [r for r, n in names if n == rule.name]
            if len(peers) > 1 and not _is_filter_field_rule(rule) and sum(not _is_filter_field_rule(r) for r in peers) == 1:
                keep = [rule.name]
        if keep:
            out.append((rule, keep))
    return out


#: 主语区边界：标签前缀（"查询条件："、"列表信息："、
#: "文件名格式："）之后的字段名只是**被提到**，不是该句要验证的对象。
#: rev182 补 `--`/`—`/`->`/`→`：需求文档里极常见的「菜单名--路径--操作」写法。
#: 不加的话「增加会员限购商品管理列表--交易云-运营-增加菜单」的主语区会覆盖整串，
#: 里面的「限购商品」被当成主语回链 → 给一个"新增菜单"条目挂上「限购商品=必填8字」断言
#: （与 rev133 的假通过同型）。回链判据必须只看分隔符前的部分。
_SUBJECT_DELIM_RE = re.compile(r"[：:，,。；;]|\s*--+|—+|–+|->|→|\s-\s")
#: 占位符 / 括号注释里的字段名同理——"调查问卷-{问卷标题+问卷编码}" 描述的是文件名构成，
#: 不是「问卷标题必填」。
_PLACEHOLDER_RE = re.compile(r"\{[^{}]*\}|\[[^\[\]]*\]|（[^（）]*）|\([^()]*\)")
#: 示例值（"调查问卷-这里是问卷标题名称1234567890-20260101100000.xlsx"）是产物样例，
#: 不是需求条款，挂断言等于凭空造判据。
_EXAMPLE_VALUE_RE = re.compile(
    r"\.(?:xlsx|xls|csv|pdf|docx|doc|png|jpe?g|gif|zip|rar|txt)\s*$", re.IGNORECASE)
#: 段首编号（"1."、"1.2.1 "、"（3）"、"①"、"-"）不参与主语判定
_LEADING_ENUM_RE = re.compile(
    r"^\s*(?:\d+(?:\.\d+)*\s*[.、)）]?\s*|[（(]\s*\d+\s*[）)]\s*|[①-⑩]\s*|[-*•·]\s*)")


def _subject_zone(point: str) -> str:
    """取出"这句话在讲谁"——占位符剔除、段首编号剥离、遇到标签冒号/逗号/路径分隔即止。

    ⚠️ 这里**不**因为「分隔符后是约束」就并回主语区。主语区越宽，越容易把
    「这句话只是提到了某字段」误判成「这句话在验证某字段」——那正是 rev133
    修掉的假通过（标题讲导出、断言验必填）。约束信息由**标题层**保留
    （见 `_is_path_tail`：尾段是约束时整个保留），断言层仍然只认主语。
    """
    text = _PLACEHOLDER_RE.sub("", point or "").strip()
    text = _LEADING_ENUM_RE.sub("", text).strip()
    return _SUBJECT_DELIM_RE.split(text, 1)[0]


#: 可测行为词（rev181）。需求点要能扩写成「反向/边界」用例，必须至少说清**一个行为**；
#: 只描述"有这个东西/新增了这个东西"的条目（如「增加会员限购商品管理列表--交易云-运营-
#: 增加菜单(菜单名称：会员限购商品管理)」）扩写出来就是纯噪音，实测产出
#: 「…的必填或非法输入校验」「…的边界条件处理」两条无断言用例（TC-843363 / 2026-10-22 审计）。
_TESTABLE_VERBS = (
    "必填", "必選", "必选", "選填", "选填", "填写", "填入", "输入", "錄入", "录入", "提交", "保存", "儲存",
    "校验", "校驗", "校验", "拦截", "攔截", "提示", "限制", "上限", "下限", "字符", "字数", "字數",
    "长度", "長度", "最多", "至少", "不能超过", "不能超過", "不得", "小于", "小於", "大于", "大於",
    "范围", "範圍", "格式", "上传", "上傳", "选择", "選擇", "勾选", "勾選", "单选", "多选",
    "默认为", "默認", "默认", "自动带出", "自動帶出", "回填", "展示", "显示", "顯示", "隐藏",
    "隐藏", "置灰", "禁用", "可编辑", "可編輯", "只读", "只讀", "不可", "排序", "筛选", "篩選",
    "查询", "查詢", "导出", "導出", "导入", "導入", "删除", "刪除", "启用", "啟用", "停用",
    "切换", "切換", "生效", "重复", "重複", "唯一", "去重", "同步", "回写", "回寫", "计算",
    "計算", "取整", "精度", "小数", "小數", "天数", "天數", "次数", "次數", "数量", "數量",
    "金额", "金額", "价格", "價格", "比例", "折扣", "状态", "狀態", "流转", "流轉",
    # rev182：「可以不填 / 无需填写 / 非必填」是**可测的选填语义**（反向场景验「不填也能提交」），
    # 原先全缺席 ⇒ 「备注可以不填」被当成"没有可验证行为"跳过，理由还归到了 no_behavior 上。
    "可以不填", "可以不填寫", "无需填", "無需填", "非必填", "非必選", "非必选", "不必填",
)
#: 「只描述存在/入口」的弱语气词——命中它们本身不构成可测行为
_PRESENCE_ONLY_RE = re.compile(
    r"^\s*(?:增加|新增|添加|新建|建立|提供|支持|實現|实现|可以|可)[^，。；;]{0,30}"
    r"(?:菜单|菜單|入口|按鈕|按钮|頁面|页面|列表|模塊|模块|功能)\s*(?:\(|（|$)"
)
#: 「提供/增加一个导出按钮」= 只说有按钮，没说按钮怎么表现 → 仍属存在性描述。
#: 需要与「新增题目」这类**真的行为**区分（「题目」不在元素词里），故只按 UI 元素词判定。
_CREATE_ELEMENT_RE = re.compile(
    r"^\s*(?:增加|新增|添加|新建|建立|提供|支持|增加一?个|新增一?个|添加一?个)"
    r"[^，。；;]{0,20}"
    r"(?:按钮|按鈕|菜单|菜單|入口|图标|圖標|链接|連結|tab|标签页|標籤頁|开关|開關)\s*$"
)


def _has_testable_behavior(point: str) -> bool:
    """需求点是否描述了**可验证的行为**（而不是"存在某入口/某菜单"）。

    只用于决定「要不要按反向/边界模板扩写」：判 false 时仍保留正向叙述用例，
    不丢需求，只是不再机械复制两条无断言的变体（rev181）。
    """
    text = (point or "").strip()
    if not text:
        return False
    if _PRESENCE_ONLY_RE.match(text) and not any(v in text for v in _TESTABLE_VERBS):
        return False
    if _CREATE_ELEMENT_RE.match(text):
        return False
    return any(v in text for v in _TESTABLE_VERBS)


def _match_field_for_point(
    point: str, aliases: list[tuple[FieldRule, list[str]]]
) -> tuple[FieldRule, str] | None:
    """叙述条目 → 字段规则回链：取最长命中别名的字段（一条至多链一个字段）。

    rev133（小杨生煎问卷实测）：原先只要句子里**出现**字段名就回链，导致
    "文件名格式：调查问卷-{问卷标题+问卷编码}-{导出时间}" 被挂上「问卷标题-必填」
    断言，而"查询条件：问卷标题---支持模糊查询"被挂上同一个必填断言——标题讲的是
    导出/查询行为，断言却在验必填，**跑绿了也不代表需求对了**（假通过）。
    5 条错配全是这一类。因此回链必须满足「该字段是本句主语」：
    ① 整句是示例值（带文件扩展名）→ 不是需求条款，不回链；
    ② 占位符 `{}`/`[]`/括号里的字段名只是引用，剔除后再判；
    ③ 只在主语区（首个冒号/逗号/分号之前）命中才回链。
    """
    if _EXAMPLE_VALUE_RE.search(point or ""):
        return None
    zone = _subject_zone(point)
    if not zone:
        return None
    best: tuple[int, FieldRule, str] | None = None
    for rule, cands in aliases:
        for alias in cands:
            if alias and alias in zone:
                if best is None or len(alias) > best[0]:
                    best = (len(alias), rule, alias)
    if best is None:
        return None
    return best[1], best[2]


#: 叙述场景类型 → 断言分组（与 _rule_assertions 的 by_kind 对齐）
_NARRATIVE_SCENARIO_ASSERTION_KIND = {"正向": "required", "反向": "required", "边界": "boundary"}


def _assertions_for_scenario(
    keyword: str, bucket_map: dict[str, list[dict[str, Any]]]
) -> list[dict[str, Any]]:
    """按场景类型取断言——**不要**把「反向=required」写死成单一桶。

    rev182：原实现 `_rule_assertions(rule).get(assertion_kind)` 让反向场景只认 `required`。
    反向场景名为「必填**或**非法输入校验」，对「消费金额门槛」这类没有必填约束、
    只有范围约束的字段，取断言时拿到空列表——正是本次要消灭的空壳形态。
    现在反向场景按「必填 → 条件显示 → 取值约束（长度/范围/精度/格式/文件）」顺序
    取**第一个非空桶**；边界场景取 `boundary`。

    判定「扩不扩写」也调用本函数，保证「能扩」⇔「有断言可挂」是同一个判据。
    """
    if keyword == "边界":
        return list(bucket_map.get("boundary") or [])
    if keyword == "反向":
        for bucket in ("required", "condition", "boundary"):
            items = bucket_map.get(bucket) or []
            if items:
                return list(items)
        return []
    return list(bucket_map.get(_NARRATIVE_SCENARIO_ASSERTION_KIND.get(keyword, "required")) or [])


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
    aliases = _distinctive_field_aliases(field_rules or [])
    # rev182：记录「为什么没扩写」——原来静默跳过，用户只看得到用例变少，
    # 分不清是"不该扩"还是"逻辑漏了"。这条清单进生成回执。
    expand_skips: dict[str, dict[str, Any]] = {}
    for point in points:
        scenarios.append((point, "正向", Priority.HIGH if test_mode == "smoke" else Priority.MEDIUM))
        # rev181：扩写前置闸门——需求点必须①能回链到字段规则（否则没断言可挂）
        # ②本身描述可测行为。两个条件缺一就不扩写：
        #   有 reverse 无断言 = 不可判定用例（2026-10-22 审计：95 条里 29 条断言为 0）；
        #   无行为词 = 纯噪音（「增加…菜单(…）的必填或非法输入校验」TC-843363）。
        if test_mode == "smoke":
            continue
        # rev182：判定顺序 = 从「原因最具体」到「最笼统」。
        # 「未回链」是**数据侧**原因（字段说明表里没这个字段），必须先判——否则
        # 「增加会员限购商品管理列表（交易云-运营-增加菜单）」会先命中 list_or_nav
        # 和 no_behavior，让人误以为"加了定位器就能扩写"，实际是根本没绑字段。
        linked_rule = _match_field_for_point(point, aliases)
        if not linked_rule:
            expand_skips[point] = {"point": point, "reason": "unlinked",
                                   "detail": "未回链到字段说明表的字段规则，扩写无断言可挂"}
            continue
        # 「列表/导航」次之：这类条目本来就不适合反向/边界，与有没有约束无关。
        # ⚠️ 必须先于 no_behavior 判：列表类条目常带「点击/查询」等行为词，
        # 用 no_behavior 解释会把"不适用"说成"没有行为"。
        if re.search(r"列表|入口|点击|點擊|拖拽", point):
            expand_skips[point] = {"point": point, "reason": "list_or_nav",
                                   "detail": "列表/导航类条目，反向与边界场景不适用"}
            continue
        if not _has_testable_behavior(point):
            expand_skips[point] = {"point": point, "reason": "no_behavior",
                                   "detail": "只描述了存在/入口（如新增某菜单），没有可验证行为"}
            continue
        # rev182：扩写必须**有断言可挂**——按目标场景 kind 探测该字段在此维度上是否真有约束。
        # rev181 只保证「能回链到字段」，实测（全库 238 条扩写产物）182 条断言为 0：
        # 回链成功 ≠ 有断言。`限购标签`（必填 + 最多8字）扩边界场景时断言桶是空的。
        buckets = _rule_assertions(linked_rule[0])
        field_name = linked_rule[0].name
        added: list[str] = []
        # 判定「扩不扩」与「挂什么断言」必须用同一套取桶逻辑，否则会出现
        # 「扩写了但断言为空」——那正是 rev182 要消灭的形态。
        if _assertions_for_scenario("反向", buckets):
            scenarios.append((f"{point}的必填或非法输入校验", "反向", Priority.MEDIUM))
            added.append("reverse")
        if _assertions_for_scenario("边界", buckets):
            scenarios.append((f"{point}的边界条件处理", "边界", Priority.MEDIUM))
            added.append("boundary")
        if not added:
            expand_skips[point] = {
                "point": point, "reason": "no_constraint", "field": field_name,
                "detail": f"字段「{field_name}」在必填/取值/边界三个维度均无约束，扩写会是空断言用例",
            }

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
        custom_fields: dict[str, Any] = {"requirement_rule_id": rule_id,
                                          "source_point": base_point,
                                          # rev106 雙軌標記：默認人工軌，回鏈到字段規則後轉自動
                                          "executable_track": "manual"}
        description = f"基于上传需求文档《{filename}》生成；场景来源：{title}。"
        if linked is not None:
            rule, alias = linked
            bucket_map = _rule_assertions(rule)
            _linked_assertions = _assertions_for_scenario(keyword, bucket_map)
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
            custom_fields["assertions"] = _linked_assertions
            # rev106 修正：回鏈成功但該場景種類抽不出斷言（如規則無 required 約束時的正向場景）
            # → 不得標自動軌，否則「空斷言的自動用例」會被當成可執行材料。
            # 来源标记：文档抽取而来（文档变更后可重新生成；人工回写的会标 manual）
            custom_fields["assertions_source"] = "doc_extracted"
            if _linked_assertions:
                custom_fields["executable_track"] = "auto"
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
        case_title = _case_title_from_requirement(title)
        if case_title != (title or "").strip():
            # rev181 留痕：标题被规范化过，原文必须可追（验收时能逐字对回需求）
            custom_fields["title_normalized_from"] = (title or "")[:200]
        cases.append(TestCaseCreate(
            name=case_title,
            description=description,
            preconditions="已按上传需求准备角色、权限和前置数据。",
            priority=priority,
            test_case_steps=[
                TestStepCreate(step=f"准备并执行需求场景：{title}", result=f"核对文档约定：{base_point}"),
                TestStepCreate(step="检查页面、数据和状态变化，记录实际结果", result=f"实际结果符合原文：{base_point}"),
            ],
            module=module,
            keyword=keyword,
            risk_level="medium",
            case_kind=test_mode if test_mode in {"sit", "uat", "smoke", "api"} else "sit",
            tags=["AI生成", "需求来源", filename],
            # 命中字段的场景带规则体+断言（compiled）；未命中只给稳定规则编号（dangling，不编造断言）
            custom_fields=custom_fields,
        ))
    return cases, list(assertion_gaps.values()), list(expand_skips.values())


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


#: rev107-1：篩選字段規則的判定標記（表單字段規則描述不會出現這些詞）
_FILTER_RULE_MARKERS = ("搜索", "匹配", "篩選", "筛选", "模糊", "範圍", "范围", "查詢", "查询")


def _is_filter_field_rule(rule: Any) -> bool:
    """該字段規則是否來自「篩選查詢區」表（而非新建/編輯表單表）。"""
    parts = [str(getattr(rule, "name", "") or ""),
             str(getattr(rule, "description", "") or "")]
    for c in (getattr(rule, "constraints", None) or []):
        parts.append(str(getattr(c, "raw", c) or ""))
    blob = " ".join(parts)
    return bool(re.search(r"模糊|精[确確]|匹配|查[询詢]|[筛篩]选|[筛篩]選", blob))


def _build_filter_field_cases(
    field_rules: list,
    filename: str,
    *,
    module_hint: str = "",
    limit: int = 40,
) -> list[TestCaseCreate]:
    """篩選查詢區字段 → 每字段一條「匹配規則校驗」用例（rev107）。

    人工對比缺口：篩選表（rev104 已入 field_rules）此前只產表單字段用例，
    篩選字段的精準/模糊/範圍匹配規則無用例承接。此處每字段產一條
    完整手工形態用例（步驟含查詢與結果核對），executable_track=manual
    （需真實數據判定）。
    """
    cases: list[TestCaseCreate] = []
    seen: set[str] = set()
    for rule in field_rules or []:
        if len(cases) >= limit:
            break
        if not _is_filter_field_rule(rule):
            continue
        name = str(getattr(rule, "name", "") or "").strip()
        desc = str(getattr(rule, "description", "") or "").strip()
        source = dict(getattr(rule, "source", None) or {})
        # rev110：表号进 key——同一字段名在**不同表**（商品/支付各自的筛选查询区）
        # 是两条独立规则，早前按 name|desc 去重会把后者整条吃掉（少用例 + 归属错）。
        key = f"{name}|{desc[:30]}|{source.get('table')}"
        if not name or key in seen:
            continue
        seen.add(key)
        rule_point = f"篩選字段「{name}」匹配規則：{desc or '見需求文檔篩選查詢區'}"
        cases.append(TestCaseCreate(
            name=f"篩選-{name}-匹配規則校驗"[:120],
            preconditions="已登錄並打開對應列表頁，列表存在已知測試數據",
            description=f"來源文件：{filename}；{rule_point}",
            priority=Priority.MEDIUM,
            custom_fields={
                "requirement_rule_id": _rule_id("FLT", filename, key),
                "requirement_rule": {"field": name, "rule": rule_point,
                                     "type": str(getattr(rule, "field_type", "") or ""),
                                     "source": source,
                                     "evidence": desc},
                "filter_field_case": True,
                "executable_track": "manual",
                "source_file": filename,
            },
            test_case_steps=[
                {"step": f"在篩選區設置字段「{name}」的查詢條件", "result": "條件設置成功"},
                {"step": "執行查詢", "result": "列表刷新且只返回符合匹配規則的數據"},
                {"step": "核對返回結果與匹配規則一致性", "result": f"結果符合：{rule_point}"},
            ] or None,
        ))
    return cases


#: rev185：通用「关联对象」实体名词表（框架级惯例，**不含任何项目/业务专有词**）。
#: 关联选择器字段的实体在需求文档里高度重复：适用/关联/所属/指定/选择 + 门店/商品/分类…
#: 这张表只做**识别**，不参与断言取值（取值永远是运行期真实数据）。
_RELATION_ENTITY_NOUNS: tuple[str, ...] = (
    "門店", "门店", "店鋪", "店铺", "商品", "貨品", "货品",
    "分類", "分类", "品類", "品类", "類目", "类目", "標籤", "标签", "品牌",
    "供應商", "供应商", "倉庫", "仓库", "商戶", "商户",
    "員工", "员工", "職員", "职员", "角色", "部門", "部门",
    "會員", "会员", "客戶", "客户", "優惠券", "优惠券", "卡券",
    "活動", "活动", "頁面", "页面", "模板",
    "門店分組", "门店分组", "商品分組", "商品分组",
)

#: 关联字段的「选择语义」动词/名词（出现在**字段名**里才算）
_RELATION_NAME_RE = re.compile(
    r"適用|适用|關聯|关联|所屬|所属|指定|選擇|选择|投放|參與|参与|應用|应用|綁定|绑定|使用範圍|使用范围"
)
#: 「选择器」类控件的类型/说明信号（出现在**类型列或说明列**）
_RELATION_CONTROL_RE = re.compile(r"多選|多选|單選|单选|下拉|選擇器|选择器|選擇框|选择框|關聯|关联|彈窗|弹窗|穿梭框")
#: 多选信号（决定端到端用例里"选 N 个"的 N 与追加/移除步骤）
_RELATION_MULTI_RE = re.compile(r"多選|多选|可多選|可多选|多個|多个")
#: 只读/系统生成字段不可配置，不做配置保真
_NON_CONFIGURABLE_EDITABLE = ("否", "系統自動生成", "系统自动生成", "自動生成", "自动生成", "只讀", "只读")


def _relation_field_profile(rule: Any) -> dict[str, Any] | None:
    """识别「关联对象选择器」字段（rev185，通用）。

    命中条件（满足其一，且字段名含实体名词）：
      ① 字段名带选择语义动词（适用/关联/所属/指定/选择/投放/参与/应用/绑定…）；
      ② 类型列/说明列出现选择器控件词（多选/单选/下拉/选择器/关联/弹窗/穿梭框）。
    返回 ``{"entity", "multi", "inferred_type", "evidence"}``，未命中返回 None。
    诚实边界：只做识别，不编造可选值——回显比对永远依赖运行期真实数据。
    """
    name = str(getattr(rule, "name", "") or "").strip()
    type_text = str(getattr(rule, "field_type", "") or "").strip()
    desc = str(getattr(rule, "description", "") or "").strip()
    if not name:
        return None
    entity = next((e for e in _RELATION_ENTITY_NOUNS if e in name), None)
    if not entity:
        return None
    has_verb = bool(_RELATION_NAME_RE.search(name))
    has_control = bool(_RELATION_CONTROL_RE.search(f"{type_text} {desc}"))
    if not (has_verb or has_control):
        return None
    multi = bool(_RELATION_MULTI_RE.search(f"{type_text} {desc}"))
    label = f"关联{'多选' if multi else '单选'}-{entity}"
    evidence_parts = []
    if has_verb:
        evidence_parts.append("字段名含选择语义")
    if has_control:
        evidence_parts.append("类型/说明含选择器控件词")
    return {"entity": entity, "multi": multi, "inferred_type": label,
            "evidence": "；".join(evidence_parts)}


def _is_configurable_relation_rule(rule: Any) -> bool:
    """可做配置保真的关联字段：非筛选区、非只读/系统生成。"""
    if _is_filter_field_rule(rule):
        return False
    editable = str(getattr(rule, "editable", "") or "").strip()
    if editable and any(m in editable for m in _NON_CONFIGURABLE_EDITABLE):
        return False
    kinds = {c.kind for c in (getattr(rule, "constraints", None) or [])}
    if "read_only" in kinds:
        return False
    return True


def _build_persistence_echo_cases(
    field_rules: list,
    filename: str,
    *,
    module_hint: str = "",
    limit: int = 12,
) -> list[TestCaseCreate]:
    """關聯對象字段 → 「配置保真與回顯一致性」端到端用例（rev185）。

    缺口來源（3194/3195 類缺陷的通用形態）：创建/编辑时选了特定门店/商品，
    保存后重开，回显与配置不一致。字段级校验（必填/取值/边界）天生覆盖不了
    「保存→落库→重开→回显」链路；此前生成器对关联选择器只产必填/取值用例，
    配置保真场景整族缺失，只能靠人工发现。

    通用形态（不含任何项目业务词）：每个关联字段一条端到端用例——
    选 N 个具体对象 → 保存 → 重开编辑/查看 → 逐字段比对回显 → 列表行回显
    → （多选）增删后再验证。executable_track=manual：回显比对需要运行期
    真实对象数据；编译期 ``echo_checks`` 独立通道落地后可升 auto（与
    filter_checks rev170 同形演进路线）。
    """
    cases: list[TestCaseCreate] = []
    seen: set[str] = set()
    for rule in field_rules or []:
        if len(cases) >= limit:
            break
        profile = _relation_field_profile(rule)
        if not profile or not _is_configurable_relation_rule(rule):
            continue
        name = str(getattr(rule, "name", "") or "").strip()
        desc = str(getattr(rule, "description", "") or "").strip()
        source = dict(getattr(rule, "source", None) or {})
        key = f"{name}|{desc[:30]}|{source.get('table')}"
        if not name or key in seen:
            continue
        seen.add(key)
        entity = profile["entity"]
        multi = profile["multi"]
        n = 2 if multi else 1
        if multi:
            steps = [
                (f"新建记录：在「{name}」选择 {n} 个不同的{entity}（记下所选对象 A、B）",
                 f"选择成功，已选 {entity} 数为 {n}"),
                ("保存记录", "保存成功"),
                ("重新打开该记录的编辑页（或查看页）",
                 f"「{name}」完整回显步骤 1 所选的 {n} 个{entity}：无遗漏、无多余、名称逐个一致"),
                ("返回列表页查看该记录行",
                 f"列表中「{name}」的回显与配置一致（若列表不展示该字段则如实记录并跳过）"),
                (f"再次编辑：移除其中 1 个{entity}、追加另 1 个{entity}，保存后重开",
                 "回显按最新配置展示：被移除的对象不再出现、新追加的对象正常回显"),
            ]
        else:
            steps = [
                (f"新建记录：在「{name}」选择 1 个{entity}（记下所选对象 A）",
                 "选择成功"),
                ("保存记录", "保存成功"),
                ("重新打开该记录的编辑页（或查看页）",
                 f"「{name}」回显步骤 1 所选的{entity}，与配置一致"),
                ("返回列表页查看该记录行",
                 f"列表中「{name}」的回显与配置一致（若列表不展示该字段则如实记录并跳过）"),
                (f"再次编辑：改选另 1 个{entity}，保存后重开",
                 "回显按最新配置展示，旧值不残留"),
            ]
        rule_point = (f"关联对象「{name}」配置保真：保存后重开，回显与配置逐字段一致"
                      f"（{'多选' if multi else '单选'}-{entity}）")
        cases.append(TestCaseCreate(
            name=f"{name}-配置保真与回显一致性校验"[:120],
            preconditions=(f"已登录并进入新建/编辑页面；系统内存在至少 {n + 1} 个可选的{entity}数据"),
            description=(
                f"基于上传需求文档《{filename}》的字段说明生成；字段：{name}"
                f"（{rule.field_type or profile['inferred_type']}）；场景：{rule_point}"
                + (f"；识别依据：{profile['evidence']}" if not rule.field_type else "")
            ),
            priority=Priority.HIGH if multi else Priority.MEDIUM,
            custom_fields={
                "requirement_rule_id": _rule_id("PE", filename, key),
                "requirement_rule": {"field": name,
                                     "type": str(getattr(rule, "field_type", "") or ""),
                                     "inferred_type": profile["inferred_type"],
                                     "required": bool(getattr(rule, "required", False)),
                                     "source": source,
                                     "evidence": desc},
                "persistence_echo_case": True,
                "echo_spec": {"field": name, "entity": entity,
                              "multi": multi, "sample_count": n},
                # 诚实轨道：回显比对需运行期真实对象数据；echo_checks 编译通道落地后升 auto
                "executable_track": "manual",
                "source_file": filename,
            },
            test_case_steps=[
                {"step": step, "result": result} for step, result in steps
            ] or None,
        ))
    return cases


#: rev107-2：導入規則條款的識別（編號條款 + 導入語義詞）
_IMPORT_CLAUSE_RE = re.compile(r"^\s*(?:\d+|[a-zA-Z])\s*[、.．]\s*(.+)$")
_IMPORT_CLAUSE_MARKERS = ("必填", "失敗", "重複", "保持", "最多", "導入", "导入",
                          "創建", "创建", "更新", "刪除", "删除", "錯誤", "错误")


def _extract_import_clauses(text: str, limit: int = 25) -> list[str]:
    """抽取编号条款及继承父条款语义的字母子项，保留 PDF 换行后的正文。"""
    if limit <= 0:
        return []
    if not re.search(r"导入|導入", text or ""):
        return []
    entries: list[tuple[bool, str]] = []
    for raw in (text or "").splitlines():
        line = _ZERO_WIDTH_RE.sub("", raw).strip()
        if not line or re.match(r"^=+\s*(?:PAGE|页|頁)\s*\d+\s*=+$", line, re.I):
            continue
        if is_heading_line(line) or _section_kind(line) != "normal":
            entries.append((False, ""))  # 章节边界清除父条款，防止跨节继承
            continue
        match = _IMPORT_CLAUSE_RE.match(line)
        if match:
            entries.append((bool(re.match(r"^[a-zA-Z]", line)), match.group(1).strip()))
        elif entries and entries[-1][1] and not _is_doc_noise(line):
            child, body = entries[-1]
            entries[-1] = (child, body + line)
        else:
            entries.append((False, ""))
    out: list[str] = []
    seen: set[str] = set()
    parent = ""
    for child, body in entries:
        if not body:
            parent = ""
            continue
        clause = f"{parent}；子项：{body}" if child and parent else body
        if child and not parent:
            continue
        if not child:
            parent = ""
        # 純章節標題（如「2.3 導入門店」「2.3.2 導入規則：」）不產用例——無謂語不成條款
        if re.match(r"^[\d.]+\s*[^，；、]{2,14}$", clause):
            continue
        if len(clause) < 8:
            continue
        if not any(k in clause for k in _IMPORT_CLAUSE_MARKERS):
            continue
        if not child:
            parent = clause
        key = clause
        if key in seen:
            continue
        seen.add(key)
        out.append(clause)
        if len(out) >= limit:
            break
    return out


def _build_import_clause_cases(
    text: str,
    filename: str,
    *,
    limit: int = 25,
) -> list[TestCaseCreate]:
    """導入規則/錯誤原因條款 → 每條款一條用例（rev107）。

    人工對比缺口：導入 6 規則 + 9 錯誤原因此前被敘事抽取上限擠掉。
    條款型規則一條款一用例（不再套 正/反向/邊界 三模板）。
    """
    cases: list[TestCaseCreate] = []
    for clause in _extract_import_clauses(text, limit=limit):
        if _is_incomplete_clause(clause):
            continue
        child = clause.split("；子项：", 1)[-1]
        short = child[:90].rstrip("；;。")
        if "；子项：" in clause:
            if re.search(r"電話|电话|關鍵詞|关键词|營業時間|营业时间", child):
                subject = next(label for pattern, label in ((r"電話|电话", "店铺电话"), (r"關鍵詞|关键词", "搜索关键词"), (r"營業時間|营业时间", "营业时间")) if re.search(pattern, child))
                short = subject + "字段组完整性"
        cases.append(TestCaseCreate(
            name=f"導入-{short}校驗"[:120],
            preconditions="已登錄且具備導入權限；已準備符合/不符合條款的導入文件",
            description=f"來源文件：{filename}；導入條款：{clause}",
            priority=Priority.MEDIUM,
            custom_fields={
                "requirement_rule_id": _rule_id("IMP", filename, clause),
                "requirement_rule": {"field": "導入", "rule": clause},
                "import_clause_case": True,
                "executable_track": "manual",
                "source_file": filename,
            },
            test_case_steps=[
                {"step": f"構造觸發該條款的導入數據（{short}…）", "result": "文件可正常上傳"},
                {"step": "執行導入並等待處理完成", "result": "導入結果與條款約定一致（成功/失敗及原因提示準確）"},
            ] or None,
        ))
    return cases


def _is_incomplete_clause(text: str) -> bool:
    return bool(re.search(r"\b[xX]{2,}\s*字段|[xX]{2,}字段", text or "")
                or (text or "").rstrip().endswith(("：", ":")))


def _build_import_supplement_cases(text: str, filename: str) -> list[TestCaseCreate]:
    """营业时间导入未约定重叠处理时，显式留下人工确认项，不计需求覆盖。"""
    clauses = _extract_import_clauses(text, limit=1000)
    evidence = next((c for c in clauses if re.search(r"營業時間|营业时间", c)), "")
    if not evidence or re.search(r"重疊|重叠|overlap", text or "", re.I):
        return []
    return [TestCaseCreate(
        name="人工補充-營業時間段重疊處理待確認",
        description=(f"人工补充项；来源文件：{filename}。文档仅约定：{evidence}。"
                     "未明确时间段重叠时应允许、拒绝或合并，需产品确认后补充预期。"),
        preconditions="已找到营业时间导入条款；重叠处理规则待产品确认",
        priority=Priority.MEDIUM,
        custom_fields={
            "manual_supplement": True,
            "confirmation_status": "pending",
            # 与「文档歧义」区分：这是**文档未约定**、需人工补充预期的项，
            # 不是文档自相矛盾；两类在确认口径上不同，不得混为一谈。
            "ambiguity_kind": AMBIGUITY_MANUAL_SUPPLEMENT,
            "ambiguity_reason": "文檔未約定同一營業日多個時間段重疊/端點相接的處理規則，需人工補充預期",
            "executable_track": "manual",
            "source_file": filename,
            "source_evidence": evidence,
            # 刻意**不挂** requirement_rule / requirement_rule_id：人工补充项不是 PRD 规则，
            # 挂上会被当成"文档里真有这条规则"（定位仍可用 source_evidence 原文）。
        },
        test_case_steps=[{
            "step": "向产品确认同一营业日多个时间段发生重叠及端点相接时的处理规则",
            "result": "记录确认结论并补充可判定预期；确认前不判定通过或失败",
        }],
    )]


#: 列表显示表 → 模块名判定（按列名关键词），用于用例命名与步骤措辞
_LIST_MODULE_HINTS = (("店鋪", "店鋪"), ("店铺", "店鋪"), ("商品", "商品"),
                      ("支付方式", "支付方式"), ("會員", "會員"), ("会员", "會員"))

#: rev109（P1）：地图交互/只读字段的判定标记（字段说明描述里的编辑态与组件证据）
_MAP_MARKERS = ("地圖", "地图", "谷歌地圖", "Google Maps", "google maps")
#: 坐標欄位名（含 YL 文檔實際用詞「精度/維度」——經緯度在該文檔裡就這麼命名）
_COORD_NAME_HINTS = ("經度", "纬度", "经度", "緯度", "坐標", "坐标",
                     "精度", "維度", "longitude", "latitude")
#: 「是否可編輯」列中 = 只讀語義的取值（YL 店鋪字段表：否/系統自動生成）
_READ_ONLY_EDITABLE_VALUES = ("否", "系統自動生成", "系统自动生成", "只讀", "只读", "N", "不可編輯", "不可编辑")


def _is_coord_rule(rule: Any) -> bool:
    name = str(getattr(rule, "name", "") or "").lower()
    return any(h.lower() in name for h in _COORD_NAME_HINTS)


def _build_map_interaction_cases(
    field_rules: list,
    filename: str,
    *,
    module_hint: str = "",
) -> list[TestCaseCreate]:
    """地圖組件交互 → 每文檔一條「地址搜索定位與坐標回填」用例（rev109 / P1）。

    人工對比缺口（YL 主數據）：「輸入詳細地址，從地圖組件搜索詳細的位置；
    搜索的範圍在店鋪行政區範圍」與「通過詳細地址在谷歌地圖中搜索返回具體的
    經度/緯度，不可編輯」都在字段說明列的**敘述文字**裡——表格行不進敘事通道，
    extract_constraints 也不抽地圖語義 → 此前無任何用例承接地圖交互。
    這裡按 desc 命中「地圖」路由：地址欄位（觸發搜索）+ 坐標欄位（回填+只讀）
    動態組裝步驟，executable_track=manual（地圖組件多為外部嵌入，需真實頁面判定）。
    """
    coord_rules = [r for r in field_rules or [] if _is_coord_rule(r)]
    addr_rules = [
        r for r in field_rules or []
        if any(m in str(getattr(r, "description", "") or "") for m in _MAP_MARKERS)
        and not _is_coord_rule(r)
    ]
    evidence = addr_rules + coord_rules
    if not evidence or not any(
            any(m in str(getattr(r, "description", "") or "") for m in _MAP_MARKERS)
            for r in evidence):
        return []
    addr = str(addr_rules[0].name) if addr_rules else "詳細地址"
    lng = next((str(r.name) for r in coord_rules
                if "經" in str(r.name) or "lon" in str(r.name).lower()),
               str(coord_rules[0].name) if coord_rules else "經度")
    lat = next((str(r.name) for r in coord_rules if r.name != lng), "緯度")
    blobs = [f"「{r.name}」{str(getattr(r, 'description', '') or '')[:60]}" for r in evidence[:3]]
    rule_point = "地圖組件交互：" + "；".join(blobs)
    sig = "|".join(sorted(str(r.name) for r in evidence))
    prefix = f"{module_hint}-" if module_hint else ""
    return [TestCaseCreate(
        name=f"{prefix}地圖組件-地址搜索定位與坐標回填校驗"[:120],
        preconditions="已登錄並打開含地圖組件的新建/編輯頁面",
        description=f"來源文件：{filename}；{rule_point}",
        priority=Priority.MEDIUM,
        custom_fields={
                "requirement_rule_id": _rule_id("MAP", filename, sig),
            "requirement_rule": {"field": "地圖組件", "rule": rule_point},
            "map_interaction_case": True,
            "executable_track": "manual",
            "source_file": filename,
        },
        test_case_steps=[
            {"step": f"在「{addr}」輸入地址關鍵字並等待組件響應",
             "result": "地圖組件發起搜索並返回候選位置（範圍按文檔限定，如店鋪行政區）"},
            {"step": "從候選位置中選擇目標點",
             "result": f"「{lng}」「{lat}」自動回填為所選位置的坐標"},
            {"step": f"嘗試直接修改「{lng}」「{lat}」",
             "result": "字段為禁用/只讀態，無法輸入或修改"},
            {"step": "保存記錄並重新打開查看",
             "result": "地址與坐標一致；坐標值與地圖選點相符，未被手工篡改"},
        ] or None,
    )]


def _build_read_only_field_cases(
    field_rules: list,
    filename: str,
    *,
    module_hint: str = "",
    limit: int = 20,
) -> list[TestCaseCreate]:
    """只讀（不可編輯）字段 → 每字段一條「只讀校驗」用例（rev109 / P1）。

    编辑态證據有兩個來源，命中任一即產用例：
    ①「是否可編輯」列（YL 店鋪字段表：值=否/系統自動生成）——解析层 rev109 起
      進 FieldRule.editable；
    ② 說明列敘述（「不可編輯」「禁用狀態」）——read_only 約束。
    不夸大——文檔兩處都沒寫的字段不生產此類用例。
    """
    cases: list[TestCaseCreate] = []
    seen: set[str] = set()
    for rule in field_rules or []:
        if len(cases) >= limit:
            break
        ro = rule.constraint("read_only") if hasattr(rule, "constraint") else None
        editable = str(getattr(rule, "editable", "") or "").strip()
        col_evidence = editable if editable in _READ_ONLY_EDITABLE_VALUES else ""
        if ro is None and not col_evidence:
            continue
        name = str(getattr(rule, "name", "") or "").strip()
        if not name or name in seen:
            continue
        seen.add(name)
        desc_raw = str(getattr(ro, "raw", "") or "") if ro is not None else ""
        basis = "、".join(x for x in (f"是否可編輯={col_evidence}" if col_evidence else "",
                                      f"說明列：{desc_raw}" if desc_raw else "") if x)
        module = module_hint or "業務"
        cases.append(TestCaseCreate(
            name=f"{module}-{name}-只讀（不可編輯）校驗"[:120],
            preconditions="已登錄並打開含該字段的編輯/詳情頁面",
            description=f"來源文件：{filename}；字段「{name}」{basis}（見需求文檔字段說明）",
            priority=Priority.MEDIUM,
            custom_fields={
                "requirement_rule_id": _rule_id("RO", filename, name),
                "requirement_rule": {"field": name,
                                     "rule": f"「{name}」只讀/不可手工修改（{basis}）",
                                     "editable": editable},
                "read_only_case": True,
                "executable_track": "manual",
                "source_file": filename,
            },
            test_case_steps=[
                {"step": f"定位字段「{name}」並檢查其輸入控件狀態",
                 "result": f"字段呈禁用/只讀態（文檔依據：{basis}）"},
                {"step": f"嘗試對「{name}」輸入或修改值", "result": "無法輸入或修改；值保持不變"},
                {"step": "保存並重新打開記錄", "result": "該字段值與修改前一致，未被篡改"},
            ] or None,
        ))
    return cases


def _build_export_detail_cases(
    structure: StructuredRequirement | None,
    filename: str,
    *,
    module_hint: str = "",
    limit: int = 6,
) -> list[TestCaseCreate]:
    """「導出明細」表 → 每表一條「欄位完整性校驗」用例（rev111）。

    实测缺口（PR-2）：`2.3.6 導出明細` 的四张表共 13 行字段规则（"固定显示，获取用户昵称，
    为空时显示'-'"…）此前**一条用例都没有**——既没有必填/枚举/长度约束，说明里也没有筛选
    关键词，于是既不进字段校验用例也不进筛选匹配用例。此处按表产一条完整性用例
    （形态同「列表顯示字段」的 LST 用例），把"导出文件应有哪些列、空值怎么显示"落成可执行步骤。
    """
    if structure is None:
        return []
    locations = getattr(structure, "table_locations", None) or {}
    grouped: dict[Any, list[Any]] = {}
    for rule in structure.field_rules:
        grouped.setdefault(rule.source.get("table"), []).append(rule)
    cases: list[TestCaseCreate] = []
    for table, rules in grouped.items():
        if len(cases) >= limit:
            break
        location = locations.get(table) or {}
        heading = str(location.get("heading") or "")
        if not re.search(r"導出明細|导出明细|導出欄位|导出字段|導出清單|导出清单", heading):
            continue
        names = [str(getattr(rule, "name", "") or "").strip() for rule in rules]
        names = [n for n in names if n]
        if not names:
            continue
        col_list = "、".join(names[:20])
        rule_point = f"「導出明細」欄位（{len(names)}項）：{col_list}"
        empty_rules = [
            str(getattr(rule, "description", "") or "")
            for rule in rules
            if re.search(r"為空時顯示|为空时显示|若無則|若无则",
                         str(getattr(rule, "description", "") or ""))
        ]
        source = {k: location.get(k) for k in
                  ("page", "line", "heading", "heading_title", "section")
                  if location.get(k) is not None}
        source["table"] = table
        steps = [
            {"step": "在問卷列表頁點擊「導出明細」，選擇時間段後導出",
             "result": "導出成功，得到 Excel/CSV 文件"},
            {"step": "逐個核對導出文件的表頭與文檔「導出明細」欄位清單",
             "result": f"欄位齊全且順序一致：{col_list}"},
        ]
        if empty_rules:
            steps.append({
                "step": "構造空值數據（如未關聯訂單號、無會員卡號）後再次導出",
                "result": "空值欄位按文檔約定顯示為「-」（" + "；".join(empty_rules[:3])[:120] + "）",
            })
        steps.append({"step": "檢查有無文檔未聲明的多餘欄位或缺失欄位",
                      "result": "無多餘/缺失，與文檔清單完全一致"})
        cases.append(TestCaseCreate(
            name=f"導出明細-欄位完整性校驗（{len(names)}欄）"[:120],
            preconditions="已登錄並打開問卷列表頁，存在可導出的問卷填寫數據",
            description=f"來源文件：{filename}；{rule_point}",
            priority=Priority.MEDIUM,
            module=module_hint or None,
            custom_fields={
                "requirement_rule_id": _rule_id(
                    "LST", filename, f"export|{table}|{'|'.join(names)}"),
                "requirement_rule": {"field": "導出明細欄位", "rule": rule_point,
                                     "source": source},
                "list_display_case": True,
                "export_detail_case": True,
                "executable_track": "manual",
                "source_file": filename,
            },
            test_case_steps=[TestStepCreate(step=s["step"], result=s["result"]) for s in steps],
        ))
    return cases


def _build_list_display_cases(
    display_tables: list,
    filename: str,
    *,
    module_hint: str = "",
    limit: int = 8,
) -> list[TestCaseCreate]:
    """「列表顯示字段」表 → 每表一條「欄位完整性校驗」用例（rev108）。

    人工對比缺口（YL 主數據）：文檔列出店鋪 15 列/商品 7 列/支付 5 列的
    列表顯示欄位，此前因「說明」不在字段表判定詞表整表落入 template_tables
    （只存行数），列清單全丢、無任何用例承接。rev108 起解析层产出
    display_columns，這裡一表一條手工形態用例（核對欄位齊全/順序/無多餘缺失），
    executable_track=manual（需真實頁面判定）。
    """
    cases: list[TestCaseCreate] = []
    for table in display_tables or []:
        if len(cases) >= limit:
            break
        columns = [c for c in (table.get("columns") or []) if c.get("name")]
        if not columns:
            continue
        names = [c["name"] for c in columns]
        blob = " ".join(names)
        module = next((label for kw, label in _LIST_MODULE_HINTS if kw in blob), module_hint or "業務")
        col_list = "、".join(names[:20])
        rule_point = f"「{module}」列表顯示欄位（{len(names)}列）：{col_list}"
        sig = "|".join(names)
        cases.append(TestCaseCreate(
            name=f"列表顯示-{module}-欄位完整性校驗"[:120],
            preconditions="已登錄並打開對應列表頁",
            description=f"來源文件：{filename}；{rule_point}",
            priority=Priority.MEDIUM,
            custom_fields={
                "requirement_rule_id": _rule_id("LST", filename, f"{table.get('table')}|{sig}"),
                "requirement_rule": {"field": "列表顯示欄位", "rule": rule_point},
                "list_display_case": True,
                "executable_track": "manual",
                "source_file": filename,
            },
            test_case_steps=[
                {"step": f"打開「{module}」列表頁", "result": "列表正常加載，表頭欄位完整渲染"},
                {"step": "逐個核對表頭欄位與需求文檔「列表顯示字段」清單",
                 "result": f"欄位齊全且順序一致：{col_list}"},
                {"step": "檢查有無文檔未聲明的多餘欄位或缺失欄位",
                 "result": "無多餘/缺失，與文檔清單完全一致"},
            ] or None,
        ))
    return cases


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
                # rev106 雙軌標記：伴生場景為通用手工場景，綁定定位器後可轉自動
                "executable_track": "manual",
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
    summary = structure_summary or {}
    if int(summary.get("field_rule_count") or 0) > 0:
        # rev132：规则数 > 0 也要点名「有多少条其实产不出用例」。
        # 实测小杨生煎问卷 PDF：27 条字段规则只有 10 条具备必填/枚举约束，
        # 另外 17 条（填写渠道、用户名、门店名称…只读展示列）被生成门槛静默丢弃，
        # 响应里一个字都没有 ⇒ 看起来"覆盖了 27 个字段"，实际只校验了 10 个。
        total = int(summary.get("field_rule_count") or 0)
        eligible = summary.get("field_rule_case_eligible")
        if eligible is None and summary.get("field_rules"):
            eligible = sum(
                1 for rule in summary["field_rules"]
                if rule.get("required")
                or any(c.get("kind") == "options" for c in rule.get("constraints") or [])
            )
        if isinstance(eligible, int) and 0 < eligible < total:
            dropped_names: list[str] = []
            for rule in (summary.get("field_rules") or []):
                name = str(rule.get("name") or "")
                if not name or name in dropped_names:
                    continue          # 同名字段在两张表里重复出现（选项/添加选项…）只列一次
                if rule.get("required"):
                    continue
                if any(c.get("kind") == "options" for c in rule.get("constraints") or []):
                    continue
                dropped_names.append(name)
                if len(dropped_names) >= 8:
                    break
            listed = "、".join(dropped_names)
            return (
                f"文档解析出 {total} 条字段规则，其中 {eligible} 条具备必填/枚举约束、已生成校验用例；"
                f"另外 {total - eligible} 条无机器可判定的约束，未生成用例"
                + (f"（{listed} 等）" if listed else "")
                + "。这类字段多为只读展示列，若要覆盖请在文档中补充必填、取值范围或长度限制。"
            )
        return None
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


def _build_expand_skip_warning(skips: list[dict[str, Any]]) -> str | None:
    """扩写被跳过的说明（rev182）。

    rev181 起"不扩写"是静默的——用户只看到用例条数变少，分不清是"本来就该少"
    还是"逻辑漏了"。这里按原因分类点名，让"为什么没生成反向/边界场景"可解释。
    """
    if not skips:
        return None
    by_reason: dict[str, list[dict[str, Any]]] = {}
    for item in skips:
        by_reason.setdefault(item.get("reason") or "other", []).append(item)
    labels = {
        "unlinked": "未回链到字段说明表的字段规则（没有断言可挂）",
        "no_behavior": "只描述了存在/入口，没有可验证行为",
        "list_or_nav": "列表/导航类条目，反向与边界不适用",
        "no_constraint": "字段在必填/取值/边界三个维度均无约束（扩写会是空断言用例）",
    }
    parts = []
    for reason, items in by_reason.items():
        names = "、".join(f"「{(i.get('point') or '')[:30]}」" for i in items[:3])
        more = f" 等 {len(items)} 个" if len(items) > 3 else ""
        parts.append(f"{len(items)} 个需求点{labels.get(reason, reason)}：{names}{more}")
    return (
        f"有 {len(skips)} 个需求点未生成反向/边界扩写场景（正向用例均已保留）："
        + "；".join(parts) + "。"
        "反向/边界场景只在字段规则**确实能给出断言**时才生成，避免产出无法判定的空壳用例。"
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


def scan_vague_words(text: str, filename: str = "") -> list[dict[str, Any]]:
    """扫描需求文本里的模糊词，逐条给出「原句 + 词 + 为什么判不了 + 改写建议」（rev183）。

    裁决背景（2026-10-21 用户裁定）：**不做自动替换**——把「适当限制」改成「最多20字」
    等于凭空造阈值，用例跑绿也只是验证了自己编的数。改成生成期告警：
    文档写法的问题要让写文档的人看见，而不是沉淀成一批「需人工执行」的静默用例。

    返回结构（可 JSON 序列化）：`[{word, point, reason, suggestion}]`，同一需求点只报一次。
    """
    if not text:
        return []
    points = _extract_requirement_points(text, filename=filename) if filename else _split_sentences(text)
    hits: list[dict[str, Any]] = []
    seen: set[str] = set()
    for point in points:
        for label, pattern, reason in _VAGUE_WORD_PATTERNS:
            hit = pattern.search(point)
            if not hit:
                continue
            # 「必填，最多输入20个字，尽量控制在简短范围内」：整句有数值，但**含模糊词的那个分句**
            # 并没有阈值。判据落在分句上，不能因为别处写了数字就放行。
            clause = _clause_around(point, hit.start())
            if _CONCRETE_VALUE_RE.search(clause):
                continue
            # 「有多个用英文逗号分开」是分隔规则，不是"取值没列完"（rev184 实测误报）
            if label == "支持多种" and _VAGUE_MULTI_FALSE_POSITIVE_RE.search(
                point[max(0, hit.start() - 6): hit.end() + 12]
            ):
                continue
            key = f"{label}|{point[:40]}"
            if key in seen:
                continue
            seen.add(key)
            hits.append({
                "word": label,
                "point": point[:120],
                "clause": clause[:60],
                "reason": reason,
                "suggestion": _vague_suggestion(label, point),
            })
            break  # 一个需求点只报最具体的那一个模糊词，避免同一句话被反复点名
    return hits


def _clause_around(text: str, index: int) -> str:
    """取命中位置所在的分句（按逗号/分号/顿号切）。"""
    left = max(
        (text.rfind(sep, 0, index) for sep in "，,；;。"), default=-1
    )
    right_candidates = [
        p for p in (text.find(sep, index) for sep in "，,；;。") if p != -1
    ]
    right = min(right_candidates) if right_candidates else len(text)
    return text[left + 1: right].strip()


def _vague_suggestion(label: str, point: str) -> str:
    """给出可操作的改写方向（不是自动改写，只提示换成什么形态）。"""
    table = {
        "尽量": "改成具体上限，如「最多输入20个字」「不超过3个」",
        "适当": "给出可判定的界，如「长度 1–50 字」「取值范围 [0,99999]」",
        "支持多种": "列出全部合法取值（闭集），如「仅支持 jpg、png」",
        "等等": "把取值列完，去掉「等等」",
        "若干位": "写明数量，如「最多5张」",
        "视情况": "写明触发条件，如「仅当订单类型=外卖时显示」",
        "优化": "换成可观察的判定点，如「提交后 3 秒内出现成功提示」",
    }
    return table.get(label, "把描述换成可判定的阈值/闭集/触发条件")


def _split_sentences(text: str) -> list[str]:
    """无 filename 时的兜底切句（扫描用，不做需求点抽取）。"""
    parts = re.split(r"[\n。；;]+", text or "")
    return [p.strip() for p in parts if p.strip()]


def _build_vague_word_warning(hits: list[dict[str, Any]]) -> str | None:
    """模糊词告警文案：点名句子 + 为什么判不了 + 怎么改（无命中返回 None）。"""
    if not hits:
        return None
    by_word: dict[str, list[dict[str, Any]]] = {}
    for item in hits:
        by_word.setdefault(item.get("word") or "other", []).append(item)
    parts: list[str] = []
    for word, items in sorted(by_word.items(), key=lambda kv: -len(kv[1])):
        sample = items[0]
        parts.append(
            f"「{word}」{len(items)} 处（{sample.get('reason')}；例：{(sample.get('point') or '')[:40]}）"
        )
    return (
        f"需求文本中有 {len(hits)} 处模糊表述，无法据此生成可判定断言："
        + "；".join(parts)
        + "。这不影响用例生成，但对应条目只能人工执行——"
        "把这些描述换成具体阈值/闭集/触发条件后重新生成，即可转自动校验。"
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
    rule_id = _rule_id("FR", filename, f"{rule.name}|{rule.source.get('table')}|{rule.source.get('row')}")
    assertions = _rule_assertions(rule)
    # rev185：类型标注补齐——文档未标注类型时，用通用关联对象识别给出推断标注，
    # 让下游（控件类型判定 / echo_checks 通道）有类型可消费，而不是永远"未标注类型"。
    _rel = _relation_field_profile(rule)
    _type_label = rule.field_type or (
        f"未标注类型，推断：{_rel['inferred_type']}" if _rel else "未标注类型")
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
                f"（{_type_label}）"
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
                    # rev185：推断标注（未标注类型 + 关联选择器识别命中时非空）
                    "inferred_type": _rel["inferred_type"] if _rel else "",
                    "required": rule.required,
                    "condition": rule.condition,
                    "source": rule.source,
                    # 规则原文（歧义判定与文档定位都只认本条规则自身的证据）
                    "description": rule.description,
                    "evidence": rule.description,
                },
                "assertions": assertions.get(assertion_kind, []),
                # rev106 雙軌標記：帶機器可判定斷言 → 自動軌
                "executable_track": "auto" if assertions.get(assertion_kind) else "manual",
                # 来源标记：文档抽取而来（人工回写的断言标 manual，见 test_assertion_service）
                "assertions_source": "doc_extracted",
            },
        )

    out: list[TestCaseCreate] = []
    options = rule.constraint("options")

    # 1) 必填 / 枚举校验
    # rev132：门槛收口到 FieldRule.case_eligible（必填 或 有枚举取值），
    # 与 requirement_structure.to_dict() 里的 field_rule_case_eligible 同源，
    # 避免"解析侧说 27 条、生成侧只出 10 条"这类对不上的账。
    if rule.case_eligible:
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
            if unit in ("天", "日"):
                steps.append((f"在「{rule.name}」選擇跨度 {limit} 天", "通过校验"))
                steps.append((f"在「{rule.name}」選擇跨度 {limit + 1} 天", "被拒绝或提示超出可选范围"))
            elif unit in ("組", "组", "道", "時間段", "时间段", "項", "项"):
                steps.append((f"在「{rule.name}」設置 {limit} {unit}", "通过校验"))
                steps.append((f"在「{rule.name}」嘗試設置 {limit + 1} {unit}",
                              "被拒绝并提示超出数量上限"))
            else:
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
                # rev181：summary 自身可能已含括号（「上传文件（企业充值）」+「文件≤1M；格式：…」
                # → 标题里堆两对括号、41~42 字）。这里统一收成一对：字段名里的括号去掉，
                # 只留规则摘要那一对；仍超 40 字则截到摘要的第一个分句。
                _bracket_title(rule.name, rule.summary()) if rule.summary()
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


def _bracket_title(field_name: str, summary: str, limit: int = 40) -> str:
    """字段规则类标题：`{字段}-边界值与格式校验（{规则摘要}）`，保证**只有一对括号**。

    rev181：字段名自带括号（「上传文件（企业充值）」）时，原写法拼出来是
    「上传文件（企业充值）-边界值与格式校验（文件≤1M；格式：xlsx/xls/csv）」= 42 字、两对括号。
    现在把字段名的括号摊平进主语（「上传文件-企业充值」），摘要超限时取第一个分句。
    """
    head, groups = _split_bracket_groups(field_name or "")
    head = head.strip() or (field_name or "").strip()
    main = "、".join(g for g in groups if g)
    subject = f"{head}-{main}" if main else head
    base = f"{subject}-边界值与格式校验"
    room = limit - len(base) - 2  # 两对全角括号
    body = (summary or "").strip()
    if room <= 0:
        return f"{subject}-边界校验"
    if len(body) > room:
        for sep in ("；", ";", "，"):
            idx = body.find(sep)
            if 0 < idx <= room:
                body = body[:idx]
                break
        else:
            body = body[:room]
    return f"{base}（{body}）"


def _num_fmt(value: float) -> str:
    """数值格式化：保留小数精度（"%g" 只有 6 位有效数字，会截掉 99999.01）。"""
    text = f"{float(value):.10f}".rstrip("0").rstrip(".")
    return text if text else "0"


#: 路径/目录式描述（「会员限购商品管理列表--交易云-运营-增加菜单」）在标题里是噪音，
#: 不是被测对象。只在含 `--` 或 ≥2 个 `-` 时切掉尾段，正常词（「问卷-新建」）不受影响。
_PATH_TAIL_RE = re.compile(
    r"\s*(?:--|[—–]{1,2}|->|→)\s*[^，。；;\r\n]{1,40}(?:\s*[（(][^（）()]{0,20}[）)])?\s*$")
#: 尾段像「路径」的判据：含层级分隔（交易云-运营-增加菜单）或是纯动作+入口（→增加菜单）。
#: 只有像路径才切——`--` 在需求文档里也常作「对象---行为约束」的分隔符。
_PATH_SEGMENTS_RE = re.compile(r"[-—–/→>]+")
_PATH_ACTION_RE = re.compile(r"^(?:增加|新增|添加|新建|進入|进入|打開|打开|點擊|点击|跳轉|跳转)")
#: 尾段若是**可测约束**（支持模糊查询 / 默认选中 / 最多N字…），它才是这句的判定依据，
#: 不能当路径切掉。实测回归：`查询条件：问卷标题---支持模糊查询` 被切成
#: `查询条件：问卷标题`，标题不再表达"模糊查询"这一唯一的可断言点（rev182 落地后引入）。
_PATH_CONSTRAINT_RE = re.compile(
    r"支持|默認|默认|最多|至少|不少於|不少于|不能|不得|限制|校驗|校验|必填|必選|必选|"
    r"模糊|精確|精确|可選|可选|範圍|范围|時長|时长|排序|升序|降序|"
    r"自動|自动|實時|实时|同步|隱藏|隐藏|置灰|禁用")
#: 标题里可省略的弱意义尾句（不影响可判定性）
_WEAK_TAIL_RE = re.compile(r"[，,]\s*(?:即可|就行了|同上|以此类推|等等)\s*$")
#: 流水线自己生成的场景后缀（扩写模板句式），不属于需求现象
_SCENARIO_SUFFIX = (
    "必填或非法输入校验", "必填与取值校验", "必填與取值校驗",
    "边界值与格式校验", "边界條件與格式校驗", "边界条件处理", "条件显示校验", "边界校验",
)
_SCENARIO_SUFFIX_RE = re.compile(
    r"的?(?:" + "|".join(re.escape(s) for s in _SCENARIO_SUFFIX) + r")\s*$")
#: 标题噪音替换：模糊词 → 可测表述。顺序敏感（长串优先）。
_TITLE_NOISE_RULES: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"可(?:以)?正常(保存|提交|通过|生效|显示|展示|操作)"), r"可\1"),
    (re.compile(r"正常(生效|保存|提交|显示|展示|运行|使用|操作)"), r"\1"),
    (re.compile(r"无(?:任何)?异常|无问题|没有问题"), "无报错"),
    (re.compile(r"符合预期"), "与需求一致"),
    (re.compile(r"功能正常|使用正常"), "功能可用"),
)
#: 现象归一：同一语义统一成一种叫法，便于按标题聚合与检索
_TITLE_CANON: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"不显示|未显示|不展示|未展示|看不到|不可见"), "未显示"),
    (re.compile(r"不能保存|保存失败|无法保存|未保存"), "保存失败"),
    (re.compile(r"不能提交|提交失败|无法提交"), "提交失败"),
    (re.compile(r"被拦截|校验拦截|提示不能|无法通过"), "被拦截"),
)


def _is_path_tail(tail: str) -> bool:
    """尾段到底是不是「导航路径」？不是所有 `--` 后面都是路径。

    2026-10-22 实测回归（rev182 落地后引入）：
    `查询条件：问卷标题---支持模糊查询` 被切成 `查询条件：问卷标题`，
    标题丢掉唯一的可断言点（模糊匹配）。但 `增加XX列表--交易云-运营-增加菜单`
    确实该切（那是菜单位置，不是被测行为）。两者的差别是**语义**，不是分隔符：
    - 像路径 = 含层级分隔（`交易云-运营-增加菜单`）或以动作+入口开头（`→增加菜单`）；
    - 像约束 = 含「支持/默认/最多/必填/模糊/隐藏…」等**可判定**的词。
    约束优先：只要尾段里出现可测约束词，就整句保留——宁可标题长一点，也不能删掉判据。
    """
    tail = (tail or "").strip()
    if not tail:
        return True
    if _PATH_CONSTRAINT_RE.search(tail):
        return False
    return bool(_PATH_SEGMENTS_RE.search(tail)) or bool(_PATH_ACTION_RE.match(tail))


def _strip_path_tail(text: str) -> str:
    """切掉「--交易云-运营-增加菜单」这类导航路径尾段（只保留被测对象）。

    只认**路径标记**（`--`/`—`/`->`/`→`），普通连字符不算——「问卷列表-状态筛选」里的
    `-` 是对象名的分隔符。括号里的路径说明同样处理（先拆括号，再逐段清）。
    是否真属路径由 `_is_path_tail` 判——尾段是约束（「支持模糊查询」）时必须保留。
    """
    def _cut(segment: str, in_group: bool = False) -> str:
        raw = (segment or "").strip()
        match = _PATH_TAIL_RE.search(raw)
        if not match or not _is_path_tail(match.group(0)):
            return raw
        trimmed = _PATH_TAIL_RE.sub("", raw).strip()
        if not trimmed:
            return raw
        # 顶层标题切太短会毁掉标题本身（「会员限购商品列表--交易云-运营-增加菜单」→「会员…」），
        # 所以要求剩余 ≥6 字；括号里的内容本来只是解释说明，切短也照切
        # （「(路径说明--交易云-运营-增加菜单)」→「(路径说明)」）。
        if in_group:
            return trimmed
        return trimmed if len(trimmed) >= 6 else raw

    value = text or ""
    head, groups = _split_bracket_groups(value)
    if groups:
        head = _cut(head)
        cleaned = [_cut(g, in_group=True) for g in groups]
        rebuilt = head + "".join(f"（{g}）" for g in cleaned if g)
        if rebuilt.strip():
            return rebuilt
    return _cut(value)


def _normalize_title_noise(text: str) -> str:
    """把模糊词换成可测表述、统一现象叫法。原句始终保留在 description。"""
    value = text or ""
    for pattern, repl in _TITLE_NOISE_RULES:
        value = pattern.sub(repl, value)
    for pattern, repl in _TITLE_CANON:
        value = pattern.sub(repl, value)
    value = _WEAK_TAIL_RE.sub("", value)
    return re.sub(r"[ \t\u3000]{2,}", " ", value).strip()


def _split_bracket_groups(text: str) -> tuple[str, list[str]]:
    """把「主语（说明）」拆成 (主语, [说明…])，说明本身嵌套括号时按深度配对。"""
    head, groups = "", []
    i = 0
    pairs = {"（": "）", "(": ")"}
    while i < len(text):
        ch = text[i]
        if ch in pairs:
            depth, j = 0, i
            while j < len(text):
                if text[j] == ch:
                    depth += 1
                elif text[j] == pairs[ch]:
                    depth -= 1
                    if depth == 0:
                        break
                j += 1
            if j >= len(text):
                break
            groups.append(text[i + 1:j].strip())
            i = j + 1
            continue
        head += ch
        i += 1
    return head.strip(), [g for g in groups if g]


def _compact_title(text: str, limit: int = 40) -> str:
    """超长标题压缩：保主语、保可判定信息（数值/规则），把括号内解释降级到 description。

    实测噪音（2026-10-22 审计）：54 字标题把需求原文的括号解释整段搬进标题——
    「限购商品需要在点单页展示(润典配置在菜单分类展示即可，不能配置在隐藏分类/加购购分类中)」，
    后半段是**配置方法说明**，不是判定条件。
    """
    value = (text or "").strip()
    if len(value) <= limit:
        return value
    head, groups = _split_bracket_groups(value)
    # ① 括号里有硬规则（数值/区间/最长/上限/格式）→ 只保这个，丢掉解释性文字
    for g in groups:
        if re.search(r"最长|最多|至少|上限|下限|字符|位数|\d+\s*(?:字|个|位|天|条|次|M|MB|KB)"
                     r"|≤|≥|[\[(（]\s*-?\d", g):
            cand = f"{head}（{g}）" if head else g
            if len(cand) <= limit:
                return cand
            return f"{head[:limit - 2]}…"
    # ② 无硬规则：先切路径/导航尾段；仍在限内就留主语+首句；否则按分句边界截断
    head = _strip_path_tail(head or value)
    if head and len(head) <= limit:
        return head if head != value or len(value) <= limit else value[:limit]
    for sep in ("，", ",", "；", ";", "。", " "):
        idx = head.find(sep)
        if 6 <= idx <= limit:
            return head[:idx]
    return (head or value)[: limit - 1] + "…"


def _strip_pipeline_suffix(text: str) -> str:
    """切掉**尾句**是流水线场景句式的情况——那是模板话术，不是需求现象。

    实测（2026-10-22 审计 TC-265298，54 字）：「限购商品需要在点单页展示(润典配置…)的必填或
    非法输入校验」——前缀是需求原文、尾巴是扩写模板，两者被拼进同一个标题。扩写场景已由
    `_scenario_suffix` 字段表达，标题里不该再重复。

    只在「后缀前是**整句/带括号解释」的重型前置**（≥16 字或含括号）时切；像
    「题目类型-必填与取值校验」这种轻量字段名标题，后缀本身就是主要判定信息，保留原样。
    """
    match = _SCENARIO_SUFFIX_RE.search(text or "")
    if not match:
        return text or ""
    head = (text or "")[: match.start()].strip(" -—、,，;；")
    if len(head) < 16 and "（" not in head and "(" not in head:
        return text or ""
    # 括号限定 + 校验后缀 = 标题缺了「在验什么」，此时后缀是唯一判定信息 → 保留并重排
    if head.endswith("）") or head.endswith(")"):
        return text or ""
    return head or (text or "")


def _collapse_bracket_duplicate(text: str) -> str:
    """去括号信息重复：「上传文件（企业退款）-条件显示校验」→「上传文件-条件显示校验（企业退款）」。

    字段名带括号限定时后缀拼在括号外，读起来像「上传文件（企业退款）然后再条件显示校验」。
    把限定挪到末尾括号、后缀紧贴主语，语义不变但结构统一。仅当前缀以
    「主语 + 括号组」结束（即后缀前面就是括号）时才改，正常嵌套说明不动。
    """
    match = _SCENARIO_SUFFIX_RE.search(text or "")
    if not match:
        return text or ""
    prefix = (text or "")[: match.start()].strip(" -—、,，;；")
    suffix = (text or "")[match.start():].strip()
    if not prefix.endswith(("）", ")")):
        return text or ""  # 「主语-后缀」结构，括号不在后缀前面 → 不是错位，别动
    head, groups = _split_bracket_groups(prefix)
    if not head or not groups:
        return text or ""
    head = head.strip(" -—、,，;；")
    if not head:
        return text or ""
    # 前缀里括号后面还跟着内容（如「…（企业退款）-边界值…（文件≤1M）」）→ 结构已是
    # 「主语-后缀（说明）」，不是「字段名（限定）」错位，交给 _compact_title 处理
    trailing = prefix[len(head):] if prefix.startswith(head) else ""
    if trailing.strip(" -—、,，;；") and not _SCENARIO_SUFFIX_RE.search(head):
        return text or ""
    merged: list[str] = []
    for g in groups:
        for part in g.split("；"):
            part = part.strip()
            if part and part not in merged:
                merged.append(part)
    if not merged:
        return text or ""
    return f"{head}-{suffix}（{'；'.join(merged)}）"


def _case_title_from_requirement(text: str) -> str:
    """保留需求原意，清理文档编号、导航路径与模糊词，形成可判定的生产用例标题。

    rev181（2026-10-22 用例质量审计）：原实现只剥「功能点：」前缀再 `[:120]` 截断，
    于是 ① 标题=需求原句（含 `--交易云-运营` 这类路径、含整段括号解释，最长 54 字）；
    ② 模糊词原样保留（「…可正常保存」「…正常生效」，不可判定）。现改为：
    去路径尾段 → 去弱尾句 → 模糊词可测化 → 现象归一 → 超长按主语+硬规则压缩。
    """
    value = re.sub(r"^\s*(?:功能点|需求验证|测试场景)\s*[-：:]\s*", "", text or "").strip()
    value = _strip_pipeline_suffix(value)
    value = _strip_path_tail(value)
    value = _collapse_bracket_duplicate(value)
    value = value.replace("((", "（").replace("))", "）")
    # 中文语境统一全角括号（英文括号夹中文读起来断裂）
    if re.search(r"[\u4e00-\u9fa5]", value):
        value = re.sub(r"\(([^()]*)\)", r"（\1）", value)
    value = _normalize_title_noise(value)
    value = re.sub(r"（\s*）|\(\s*\)", "", value).strip(" -—、,，;；")
    value = _compact_title(value, limit=40)
    return value or "未命名需求场景"


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


