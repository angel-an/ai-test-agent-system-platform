"""项目级业务模板注册接口（P6：表单定位器数据化注册）。

解决的问题：此前"某业务模板用哪张表单定位器表"是**代码里的字典**
（``web_case_compiler.TEMPLATE_FORM_LOCATORS``），新业务模板要么改代码发版，
要么在每个子功能里重复配置 ``custom_config.field_locators``。本接口把它变成
**项目级数据**：一个项目注册一次，本项目内所有子功能复用，可在线维护。

解析优先级（与执行链路完全一致，见 ``resolve_form_locators``）：
1. 子功能 ``custom_config.field_locators``（最具体）；
2. **本接口注册的模板**（项目级）；
3. 平台内置表（老项目零改动兜底）；
4. 都没有 → 不生成表单检查，断言如实留在 ``pending_locator``。

`/resolve-preview` 就是把这套优先级**摊开展示**：谁能生效、字段有哪些、
以及"为什么没生效"，避免出现"配了却不生效、且没有任何提示"的情况。
"""

from typing import Any
from uuid import UUID

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import select

from app.api.deps import DbSessionDep
from app.models.project import Project
from app.models.web_form_template import WebFormTemplate
from app.models.web_function import WebSubFunction
from app.schemas.common import SuccessResponse
from app.services.web_case_compiler import (
    NON_LOCATOR_CONFIG_KEYS,
    TEMPLATE_FORM_LOCATORS,
    merge_registered_locators,
    parse_columns,
    parse_list_checks,
    parse_negative_scenarios,
    resolve_form_locators,
)


router = APIRouter(
    prefix="/projects/{project_identifier}/web-form-templates",
    tags=["业务模板注册"],
)


class WebFormTemplatePayload(BaseModel):
    """注册请求体。

    ``fields`` 与既有定位器表 ``locators["fields"]`` **同构**：
    ``{字段名: {"kind": "input|radio|textarea|select", ...}}``——
    必须是同一套结构，注册的数据才能直接用于生成表单检查计划。
    """

    name: str = Field(..., min_length=1, max_length=255)
    fields: dict[str, dict[str, Any]] = Field(
        ...,
        min_length=1,
        description='字段名 → 规格对象，例如 {"商品名称": {"kind": "input"}}',
    )
    extra_config: dict[str, Any] = Field(
        default_factory=dict,
        description=(
            "定位器表的其余键（form_button / modal / search_button / field_modes / "
            "upload_fields / toggles / defaults 等）。与 fields 合并后即为**完整**定位器表；"
            "缺少表单打开配置（form_button 等）会让执行器无法打开表单。"
        ),
    )
    source_ref: str | None = Field(
        default=None,
        max_length=255,
        description="定位器出处（用于审计，例如「2026-09-30 自 UAT 页面实测抓取」）",
    )
    sub_function_id: UUID | None = Field(default=None, description="登记来源子功能（仅溯源，不参与匹配）")
    description: str | None = None
    status: str = Field(default="active", description="active/disabled")


class WebFormTemplateCreatePayload(WebFormTemplatePayload):
    template_key: str = Field(
        ..., min_length=1, max_length=120,
        description="业务模板键（与用例推导出的模板键一致），项目内唯一",
    )


async def _load_project(db: Any, project_identifier: str) -> Project:
    """项目解析：项目不存在直接 404（与其它项目级路由一致）。"""
    resolved = (
        await db.execute(select(Project).where(Project.identifier == project_identifier))
    ).scalars().first()
    if resolved is None:
        raise HTTPException(status_code=404, detail="项目不存在")
    return resolved


async def _load_registration(db: Any, project_id: UUID, template_key: str) -> WebFormTemplate | None:
    return (
        await db.execute(
            select(WebFormTemplate)
            .where(WebFormTemplate.project_id == project_id)
            .where(WebFormTemplate.template_key == template_key)
        )
    ).scalars().first()


def _to_dict(row: WebFormTemplate) -> dict[str, Any]:
    return {
        "id": str(row.id),
        "project_id": str(row.project_id),
        "template_key": row.template_key,
        "name": row.name,
        "fields": row.fields or {},
        "field_count": len(row.fields or {}),
        "extra_config": row.extra_config or {},
        "extra_keys": sorted((row.extra_config or {}).keys()),
        "source_ref": row.source_ref,
        "sub_function_id": str(row.sub_function_id) if row.sub_function_id else None,
        "description": row.description,
        "status": row.status,
        "created_at": row.created_at.isoformat() if row.created_at else None,
        "updated_at": row.updated_at.isoformat() if row.updated_at else None,
    }


def _validate_fields(fields: dict[str, Any]) -> None:
    """字段校验：字段名非空、规格是非空对象——避免注册了却生成不出任何检查。"""
    if not isinstance(fields, dict) or not fields:
        raise HTTPException(status_code=422, detail="fields 不能为空")
    for raw_name, spec in fields.items():
        name = str(raw_name or "").strip()
        if not name:
            raise HTTPException(status_code=422, detail="存在空的字段名")
        if not isinstance(spec, dict) or not spec:
            raise HTTPException(
                status_code=422,
                detail=f"字段 {name} 的规格必须是非空对象（如 {{\"kind\": \"input\"}}）",
            )


@router.get("", response_model=SuccessResponse[dict[str, Any]])
async def list_web_form_templates(
    project_identifier: str,
    db: DbSessionDep,
    status: str | None = Query(default=None),
    template_key: str | None = Query(default=None),
) -> SuccessResponse[dict[str, Any]]:
    """列出本项目已注册的业务模板。"""
    project = await _load_project(db, project_identifier)
    stmt = select(WebFormTemplate).where(WebFormTemplate.project_id == project.id)
    if status:
        stmt = stmt.where(WebFormTemplate.status == status)
    if template_key:
        stmt = stmt.where(WebFormTemplate.template_key == template_key)
    rows = (await db.execute(stmt.order_by(WebFormTemplate.template_key.asc()))).scalars().all()
    items = [_to_dict(row) for row in rows]
    return SuccessResponse(success=True, data={"items": items, "total": len(items)})


@router.post("", response_model=SuccessResponse[dict[str, Any]])
async def register_web_form_template(
    project_identifier: str,
    payload: WebFormTemplateCreatePayload,
    db: DbSessionDep,
) -> SuccessResponse[dict[str, Any]]:
    """注册一个业务模板（**项目 + 模板键唯一**）。

    重复注册返回 409（而不是静默覆盖）：模板键是执行链路的匹配键，
    悄悄覆盖会让"这条用例到底用了哪张定位器表"失去可追溯性。
    """
    project = await _load_project(db, project_identifier)
    key = (payload.template_key or "").strip()
    if not key:
        raise HTTPException(status_code=422, detail="template_key 不能为空")

    existing = await _load_registration(db, project.id, key)
    if existing is not None:
        raise HTTPException(
            status_code=409,
            detail=(
                f"该项目已注册模板键 {key}（id={existing.id}）；"
                "如需修改请用 PUT 更新，避免同一模板键出现两份互相冲突的定位器表"
            ),
        )

    fields = {str(k).strip(): v for k, v in payload.fields.items()}
    _validate_fields(fields)

    if payload.sub_function_id is not None:
        sub_function = await db.get(WebSubFunction, payload.sub_function_id)
        if sub_function is None or sub_function.project_id != project.id:
            raise HTTPException(status_code=404, detail="指定的子功能不存在或不属于本项目")

    extra = {k: v for k, v in (payload.extra_config or {}).items() if k != "fields"}
    if not isinstance(extra, dict):
        raise HTTPException(status_code=422, detail="extra_config 必须是对象")

    row = WebFormTemplate(
        project_id=project.id,
        sub_function_id=payload.sub_function_id,
        template_key=key,
        name=payload.name,
        fields=fields,
        extra_config=extra,
        source_ref=payload.source_ref,
        description=payload.description,
        status=payload.status or "active",
    )
    db.add(row)
    await db.commit()
    await db.refresh(row)
    return SuccessResponse(success=True, data=_to_dict(row))


@router.put("/{template_key}", response_model=SuccessResponse[dict[str, Any]])
async def update_web_form_template(
    project_identifier: str,
    template_key: str,
    payload: WebFormTemplatePayload,
    db: DbSessionDep,
) -> SuccessResponse[dict[str, Any]]:
    """更新已注册模板（模板键不可改；未注册则 404）。"""
    project = await _load_project(db, project_identifier)
    row = await _load_registration(db, project.id, (template_key or "").strip())
    if row is None:
        raise HTTPException(status_code=404, detail=f"该项目未注册模板键 {template_key}")

    fields = {str(k).strip(): v for k, v in payload.fields.items()}
    _validate_fields(fields)
    row.name = payload.name
    row.fields = fields
    row.extra_config = {k: v for k, v in (payload.extra_config or {}).items() if k != "fields"}
    row.source_ref = payload.source_ref
    row.description = payload.description
    row.status = payload.status or row.status
    if payload.sub_function_id is not None:
        row.sub_function_id = payload.sub_function_id
    await db.commit()
    await db.refresh(row)
    return SuccessResponse(success=True, data=_to_dict(row))


@router.delete("/{template_key}", response_model=SuccessResponse[dict[str, Any]])
async def delete_web_form_template(
    project_identifier: str,
    template_key: str,
    db: DbSessionDep,
) -> SuccessResponse[dict[str, Any]]:
    """删除注册（删除后回到内置表兜底；老项目行为不变）。"""
    project = await _load_project(db, project_identifier)
    row = await _load_registration(db, project.id, (template_key or "").strip())
    if row is None:
        raise HTTPException(status_code=404, detail=f"该项目未注册模板键 {template_key}")
    await db.delete(row)
    await db.commit()
    return SuccessResponse(success=True, data={"template_key": template_key, "deleted": True})


@router.get("/{template_key}/resolve-preview", response_model=SuccessResponse[dict[str, Any]])
async def preview_template_resolution(
    project_identifier: str,
    template_key: str,
    db: DbSessionDep,
    sub_function_id: UUID | None = Query(default=None, description="按该子功能的 override 参与评选"),
) -> SuccessResponse[dict[str, Any]]:
    """摊开显示"这个模板键最终会用哪张定位器表"，以及落选原因。

    与执行链路同一个 ``resolve_form_locators``，因此这里看到的就是执行时会用的。
    """
    project = await _load_project(db, project_identifier)
    key = (template_key or "").strip()

    override = None
    legacy_columns: dict[str, Any] = {}
    if sub_function_id is not None:
        sub_function = await db.get(WebSubFunction, sub_function_id)
        if sub_function is None or sub_function.project_id != project.id:
            raise HTTPException(status_code=404, detail="指定的子功能不存在或不属于本项目")
        custom = getattr(sub_function, "custom_config", None) or {}
        if isinstance(custom, dict):
            override = custom.get("field_locators")
            # 与执行链路同一口径：CS 的列键挂在 custom_config 顶层，不在 field_locators 里。
            legacy_columns = {
                k: custom[k] for k in ("columns_22", "requirement_fields_16",
                                       "sortable_columns", "locators")
                if k in custom
            }

    row = await _load_registration(db, project.id, key)
    registered = None
    if row is not None and row.status == "active" and row.fields:
        # 必须复用执行链路的合并逻辑：只传 {"fields": ...} 会丢掉 extra_config
        # （form_button / modal 等），而执行器在打开表单处需要它们——
        # 曾经因此报 `FORM_PLAN.locators.form_button is not iterable` 整条运行硬失败（TR-163）。
        # 若这里少带，预览就会显示"一切正常"，而实际执行必崩。
        registered = merge_registered_locators(row)

    locators, source = resolve_form_locators(key, override, registered)
    builtin = TEMPLATE_FORM_LOCATORS.get(key)

    candidates = [
        {
            "source": "sub_function_override",
            "available": bool(isinstance(override, dict) and override.get("fields")),
            "selected": source == "sub_function_override",
            "field_count": len(override.get("fields") or {}) if isinstance(override, dict) else 0,
        },
        {
            "source": "project_registration",
            "available": registered is not None,
            "selected": source == "project_registration",
            "field_count": len(row.fields or {}) if row is not None else 0,
            "status": row.status if row is not None else None,
        },
        {
            "source": "template_default",
            "available": bool(isinstance(builtin, dict) and builtin.get("fields")),
            "selected": source == "template_default",
            "field_count": len((builtin or {}).get("fields") or {}) if isinstance(builtin, dict) else 0,
        },
    ]
    if source == "none":
        reason = (
            "三种来源都没有可用定位器 → 执行时不会生成表单检查，断言留在 pending_locator"
            "（平台不会回退到别的业务的定位器表）"
        )
    else:
        reason = f"按优先级选中 {source}"
    # 规则级负路径场景（P1-3 阶段 B）：与执行器**同一个**解析函数。
    # 预览里必须能看到"哪些场景会被执行、哪些被拒及原因"——
    # 与 locator_keys 同一教训：预览说没问题、执行时才发现配置被拒，等于没预览。
    scenarios, scenario_rejects = parse_negative_scenarios(
        (locators or {}).get("negative_scenarios")
    )
    # 「列表可见」检查（2026-10-16）：与执行器**同一个**解析函数。
    # 预览必须能看到"哪几条会跑、哪几条被拒及原因"——否则配了但没跑只能靠人比对日志。
    list_checks, list_check_rejects = parse_list_checks((locators or {}).get("list_checks"))
    # 列契约（N3，2026-10-18）：与编译器**同一个**解析函数。预览必须能看出
    # "列配置到底能被读出来几列"——CS 侧的 columns_22/requirement_fields_16 是
    # 一次性键名，不看解析结果根本不知道有没有被识别。
    columns_contract, column_rejects = parse_columns(
        {**(locators or {}), **({"columns_legacy": legacy_columns} if legacy_columns else {})}
    )
    return SuccessResponse(
        success=True,
        data={
            "template_key": key,
            "selected_source": source,
            "reason": reason,
            "field_count": len((locators or {}).get("fields") or {}),
            "fields": list(((locators or {}).get("fields") or {}).keys()),
            # 执行时实际使用的**全部**定位器键（含 form_button / modal 等非字段配置）。
            # 只暴露 fields 时，注册漏带 extra_config 导致的执行期硬失败在预览里完全不可见。
            # 剔除**非定位器**的键（来源元信息 + 行为配置如 negative_scenarios）——
            # 用 NON_LOCATOR_CONFIG_KEYS 而不是只剔 REGISTRATION_META_KEYS，
            # 与执行侧保持同一份集合，否则两端会分叉。
            "locator_keys": sorted(
                k for k in (locators or {}) if k not in NON_LOCATOR_CONFIG_KEYS
            ),
            # 规则级负路径场景（P1-3 阶段 B）：只回可核对的摘要，配置原文仍以注册行为准。
            # `rejects` 必须可见 —— 被拒场景在运行期只是几行 [negative-skip]，
            # 不在这里提前暴露就只能靠人去比对日志。
            "negative_scenario_count": len(scenarios),
            "negative_scenarios": [
                {
                    "scenario_id": s["scenario_id"],
                    "rule_ref": s["rule_ref"],
                    "target_entry": s["target_entry"],
                    "setup_steps": [step["action"] for step in s["setup"]],
                    "submit_label": s["submit_label"],
                    "api_path_hint": s["expect_reject"]["api_path_hint"],
                    "allow": s["expect_reject"]["allow"],
                    "result_code_not": s["expect_reject"]["result_code_not"],
                    "message_contains": s["expect_reject"]["message_contains"],
                    "case_id": s["case_id"],
                    "rule_id": s["rule_id"],
                }
                for s in scenarios
            ],
            "negative_scenario_rejects": scenario_rejects,
            # 「列表可见」检查（2026-10-16）：只回可核对摘要，配置原文以注册行为准。
            "list_check_count": len(list_checks),
            "list_checks": [
                {
                    "name": c["name"],
                    "value": c["value"],
                    "expect": c["expect"],
                    "list_url": c["list_url"],
                    "search_placeholder": c["search_placeholder"],
                    "max_pages": c["max_pages"],
                    "case_id": c["case_id"],
                    "rule_id": c["rule_id"],
                }
                for c in list_checks
            ],
            "list_check_rejects": list_check_rejects,
            # 列契约（N3）：本期没有列表检查执行器，这里只回"能读出什么"，
            # 明确标注 supported=false，避免被读成"列检查已经会跑了"。
            "columns": {
                "supported": False,
                "table_scope": (columns_contract or {}).get("table_scope", ""),
                "column_count": (columns_contract or {}).get("column_count", 0),
                "source": (columns_contract or {}).get("source", ""),
                "items": (columns_contract or {}).get("items", []),
                "notes": (columns_contract or {}).get("notes", []),
            },
            "column_rejects": column_rejects,
            "candidates": candidates,
            "registration": _to_dict(row) if row is not None else None,
        },
    )
