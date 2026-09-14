"""
Spec 自动发现 API Schema
"""

from typing import Any

from pydantic import BaseModel, Field


class SpecDiscoveryRequest(BaseModel):
    """Spec 发现请求"""
    url: str = Field(..., description="Swagger UI 页面地址或 OpenAPI JSON/YAML 地址")
    headers: dict[str, str] = Field(
        default_factory=dict,
        description="可选的请求头（用于认证等，如 Authorization、Cookie）"
    )


class SpecCandidate(BaseModel):
    """候选 Spec 地址详情"""
    url: str = Field(..., description="候选地址 URL")
    valid: bool = Field(..., description="是否验证通过")
    endpoint_count: int = Field(default=0, description="接口数量（验证通过时）")
    error: str | None = Field(default=None, description="验证失败时的错误信息")


class SpecDiscoveryResponse(BaseModel):
    """Spec 发现响应"""
    source_type: str = Field(..., description="来源类型: swagger_ui, knife4j, direct_json, direct_yaml, html_page, unknown")
    ui_url: str | None = Field(default=None, description="原始 UI 页面地址（如果是 UI 页面）")
    spec_url: str | None = Field(default=None, description="发现的 Spec 地址")
    format: str | None = Field(default=None, description="格式: openapi, swagger")
    version: str | None = Field(default=None, description="OpenAPI/Swagger 版本")
    endpoint_count: int = Field(default=0, description="接口数量")
    requires_auth: bool = Field(default=False, description="是否需要认证")
    candidates: list[SpecCandidate] = Field(default_factory=list, description="所有候选地址及验证结果")
    message: str | None = Field(default=None, description="提示信息（发现失败时）")


class SpecValidateRequest(BaseModel):
    """Spec 验证请求"""
    url: str = Field(..., description="要验证的 Spec URL")
    headers: dict[str, str] = Field(
        default_factory=dict,
        description="可选的请求头"
    )


class SpecValidateResponse(BaseModel):
    """Spec 验证响应"""
    valid: bool = Field(..., description="是否有效")
    url: str = Field(..., description="验证的 URL")
    format: str | None = Field(default=None, description="格式")
    version: str | None = Field(default=None, description="版本")
    endpoint_count: int = Field(default=0, description="接口数量")
    error: str | None = Field(default=None, description="错误信息")
