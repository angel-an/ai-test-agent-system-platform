"""项目级 Web 验证路径模板接口。"""

from typing import Any
from uuid import UUID

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import select

from app.api.deps import DbSessionDep
from app.models.project import Project
from app.models.web_path_template import ProjectWebPathTemplate
from app.schemas.common import SuccessResponse


router = APIRouter(
    prefix="/projects/{project_identifier}/web-path-templates",
    tags=["Web 路径模板"],
)


class WebPathTemplatePayload(BaseModel):
    name: str = Field(..., min_length=1, max_length=255)
    side: str | None = None
    module: str | None = None
    business_type: str | None = None
    action: str | None = None
    base_url: str | None = None
    login_profile: str | None = None
    match_keywords: list[str] = Field(default_factory=list)
    navigation_path: list[str] = Field(default_factory=list, min_length=1)
    description: str | None = None
    status: str = "active"


class WebPathTemplateMatchRequest(BaseModel):
    text: str = ""
    side: str | None = None
    module: str | None = None
    business_type: str | None = None
    action: str | None = None


async def _get_project(db: Any, project_identifier: str) -> Project:
    result = await db.execute(select(Project).where(Project.identifier == project_identifier))
    project = result.scalar_one_or_none()
    if not project:
        raise HTTPException(status_code=404, detail="项目不存在")
    return project


def _to_dict(template: ProjectWebPathTemplate) -> dict[str, Any]:
    return {
        "id": str(template.id),
        "project_id": str(template.project_id),
        "name": template.name,
        "side": template.side,
        "module": template.module,
        "business_type": template.business_type,
        "action": template.action,
        "base_url": template.base_url,
        "login_profile": template.login_profile,
        "match_keywords": template.match_keywords or [],
        "navigation_path": template.navigation_path or [],
        "description": template.description,
        "status": template.status,
        "created_at": template.created_at.isoformat() if template.created_at else None,
        "updated_at": template.updated_at.isoformat() if template.updated_at else None,
    }


def _score_template(template: ProjectWebPathTemplate, request: WebPathTemplateMatchRequest) -> int:
    text = " ".join([
        request.text or "",
        request.side or "",
        request.module or "",
        request.business_type or "",
        request.action or "",
    ])
    score = 0
    for attr, weight in [
        ("side", 3),
        ("module", 4),
        ("business_type", 5),
        ("action", 3),
    ]:
        expected = getattr(template, attr) or ""
        actual = getattr(request, attr) or ""
        if expected and actual and expected in actual:
            score += weight
        elif expected and expected in text:
            score += max(1, weight - 1)

    for keyword in template.match_keywords or []:
        if keyword and keyword in text:
            score += 2
    return score


@router.get("", response_model=SuccessResponse[dict[str, Any]])
async def list_web_path_templates(
    project_identifier: str,
    db: DbSessionDep,
    status: str | None = Query(default=None),
) -> SuccessResponse[dict[str, Any]]:
    project = await _get_project(db, project_identifier)
    stmt = select(ProjectWebPathTemplate).where(ProjectWebPathTemplate.project_id == project.id)
    if status:
        stmt = stmt.where(ProjectWebPathTemplate.status == status)
    result = await db.execute(stmt.order_by(ProjectWebPathTemplate.created_at.desc()))
    items = [_to_dict(item) for item in result.scalars().all()]
    return SuccessResponse(success=True, data={"items": items, "total": len(items)})


@router.post("", response_model=SuccessResponse[dict[str, Any]])
async def create_web_path_template(
    project_identifier: str,
    payload: WebPathTemplatePayload,
    db: DbSessionDep,
) -> SuccessResponse[dict[str, Any]]:
    project = await _get_project(db, project_identifier)
    template = ProjectWebPathTemplate(
        project_id=project.id,
        name=payload.name,
        side=payload.side,
        module=payload.module,
        business_type=payload.business_type,
        action=payload.action,
        base_url=payload.base_url,
        login_profile=payload.login_profile,
        match_keywords=payload.match_keywords,
        navigation_path=payload.navigation_path,
        description=payload.description,
        status=payload.status or "active",
    )
    db.add(template)
    await db.commit()
    await db.refresh(template)
    return SuccessResponse(success=True, data=_to_dict(template))


@router.put("/{template_id}", response_model=SuccessResponse[dict[str, Any]])
async def update_web_path_template(
    project_identifier: str,
    template_id: UUID,
    payload: WebPathTemplatePayload,
    db: DbSessionDep,
) -> SuccessResponse[dict[str, Any]]:
    project = await _get_project(db, project_identifier)
    template = await db.get(ProjectWebPathTemplate, template_id)
    if not template or template.project_id != project.id:
        raise HTTPException(status_code=404, detail="路径模板不存在")

    for field, value in payload.model_dump().items():
        setattr(template, field, value)
    await db.commit()
    await db.refresh(template)
    return SuccessResponse(success=True, data=_to_dict(template))


@router.post("/match", response_model=SuccessResponse[dict[str, Any]])
async def match_web_path_template(
    project_identifier: str,
    payload: WebPathTemplateMatchRequest,
    db: DbSessionDep,
) -> SuccessResponse[dict[str, Any]]:
    project = await _get_project(db, project_identifier)
    result = await db.execute(
        select(ProjectWebPathTemplate)
        .where(ProjectWebPathTemplate.project_id == project.id)
        .where(ProjectWebPathTemplate.status == "active")
    )
    candidates = result.scalars().all()
    scored = sorted(
        [(_score_template(item, payload), item) for item in candidates],
        key=lambda pair: pair[0],
        reverse=True,
    )
    if not scored or scored[0][0] <= 0:
        return SuccessResponse(success=True, data={"matched": False, "template": None, "score": 0})

    score, template = scored[0]
    return SuccessResponse(
        success=True,
        data={"matched": True, "template": _to_dict(template), "score": score},
    )
