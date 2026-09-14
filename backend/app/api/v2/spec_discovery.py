"""
Spec 自动发现 API 路由

提供 Swagger UI / Knife4j 页面地址自动发现 OpenAPI 文档地址的能力。
"""

from fastapi import APIRouter, HTTPException, status

from app.schemas.common import SuccessResponse
from app.schemas.spec_discovery import (
    SpecDiscoveryRequest,
    SpecDiscoveryResponse,
    SpecValidateRequest,
    SpecValidateResponse,
)
from app.services.spec_discovery_service import spec_discovery_service

router = APIRouter(tags=["Spec 自动发现"])


@router.post("/spec-discovery/discover", response_model=SuccessResponse[SpecDiscoveryResponse])
async def discover_spec(request: SpecDiscoveryRequest) -> SuccessResponse[SpecDiscoveryResponse]:
    """
    自动发现 OpenAPI/Swagger Spec 地址

    支持从以下地址自动发现：
    - Swagger UI 页面（/swagger-ui.html, /swagger/index.html）
    - Knife4j 页面（/doc.html）
    - 直接 OpenAPI JSON/YAML 地址（直接返回验证结果）

    对于需要认证的页面，可通过 headers 传递认证信息。
    """
    try:
        result = await spec_discovery_service.discover(
            url=request.url,
            headers=request.headers,
        )

        response_data = SpecDiscoveryResponse(**result.to_dict())
        return SuccessResponse(success=True, data=response_data)

    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"发现服务内部错误: {str(e)}"
        )


@router.post("/spec-discovery/validate", response_model=SuccessResponse[SpecValidateResponse])
async def validate_spec(request: SpecValidateRequest) -> SuccessResponse[SpecValidateResponse]:
    """
    验证单个 Spec URL 是否有效

    直接请求给定 URL 并校验返回内容是否为有效的 OpenAPI/Swagger 文档。
    """
    try:
        valid, endpoint_count, error = await spec_discovery_service._validate_spec_url(
            request.url, request.headers
        )

        response_data = SpecValidateResponse(
            valid=valid,
            url=request.url,
            endpoint_count=endpoint_count,
            error=error,
        )

        return SuccessResponse(success=True, data=response_data)

    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"验证服务内部错误: {str(e)}"
        )
