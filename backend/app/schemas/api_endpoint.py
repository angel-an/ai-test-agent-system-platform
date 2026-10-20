"""
API 端点相关的 Pydantic Schemas
"""

from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_serializer, field_validator


def normalize_security(value: Any) -> list[dict] | None:
    """把 ``security`` 归一化为 ``list[dict] | None``。

    历史背景：统一导入曾把鉴权配置（如 ``{"type": "mx_md5", ...}``）**整体**
    写进该字段，而响应模型要求 list，导致 ``GET /projects/{id}/api-endpoints``
    抛 ``ResponseValidationError``（18 个端点）→ 500。列表接口是前端刷新后
    校验端点是否存在的唯一入口，因此这里对两种形状都做兼容，而不是只修写入侧
    （库里已有的存量行也必须能读出来）。
    """
    if value is None:
        return None
    if isinstance(value, dict):
        return [value] if value else None
    if isinstance(value, list):
        items = [item for item in value if isinstance(item, dict)]
        return items or None
    # 其它类型（如误写成字符串）不能静默丢成 500：退化为单元素列表
    return [{"value": value}]

# fmt: off  MC80OmFIVnBZMlhscm9ua3VMazZkbU5uUWc9PTo3NzlkNDI5OA==

class APIEndpointBase(BaseModel):
    """API 端点基础 Schema"""
    display_name: str = Field(..., description="显示名称：方法 + 路径")
    path: str = Field(..., description="API 路径")
    method: str = Field(..., description="HTTP 方法")
    summary: str | None = Field(None, description="端点摘要")
    description: str | None = Field(None, description="端点详细描述")

class APIEndpointCreate(APIEndpointBase):
    """创建 API 端点 Schema"""
    project_id: UUID = Field(..., description="项目 ID")
    folder_id: UUID | None = Field(None, description="文件夹 ID")
    schema_file_id: UUID | None = Field(None, description="Schema 文件 ID")
    tag_group: str | None = Field(None, description="标签分组")
    parameters: list[dict] | None = Field(None, description="参数定义")
    request_body: dict | None = Field(None, description="请求体定义")
    responses: dict | None = Field(None, description="响应定义")
    security: list[dict] | None = Field(None, description="安全配置")
    tags: list[str] | None = Field(None, description="标签列表")

    _normalize_security = field_validator("security", mode="before")(
        staticmethod(normalize_security)
    )

class APIEndpointUpdate(BaseModel):
    """更新 API 端点 Schema"""
    display_name: str | None = None
    path: str | None = None
    method: str | None = None
    summary: str | None = None
    description: str | None = None
    tag_group: str | None = None
    parameters: list[dict] | None = None
    request_body: dict | None = None
    responses: dict | None = None
    custom_config: dict | None = None
# fmt: off  MS80OmFIVnBZMlhscm9ua3VMazZkbU5uUWc9PTo3NzlkNDI5OA==

class APIEndpointResponse(APIEndpointBase):
    """API 端点响应 Schema"""
    id: UUID
    project_id: UUID
    folder_id: UUID | None
    schema_file_id: UUID | None

    # 详细信息
    tag_group: str | None
    parameters: list[dict] | None
    request_body: dict | None
    responses: dict | None
    security: list[dict] | None
    tags: list[str] | None
    custom_config: dict | None
    # 统计信息
    total_test_cases: int
    total_test_runs: int
    last_run_status: str | None
# type: ignore  Mi80OmFIVnBZMlhscm9ua3VMazZkbU5uUWc9PTo3NzlkNDI5OA==

    # 时间戳
    created_at: datetime
    updated_at: datetime | None

    model_config = ConfigDict(from_attributes=True)

    # 兼容统一导入曾以 dict 形状写入的存量数据（否则列表接口整体 500）
    _normalize_security = field_validator("security", mode="before")(
        staticmethod(normalize_security)
    )

    @field_serializer("custom_config")
    def _redact_custom_config(self, value: dict | None) -> dict | None:
        """响应输出前脱敏 custom_config.env_config 里的明文密钥。

        env_config 含 appsecret/appid/base_url/测试数据，执行器在服务端读真实值，
        但接口响应不得把明文密钥返回给前端（安全要求）。非敏感字段（base_url、
        appid、变量名）保留，便于前端回显配置；敏感值统一替换为 ***。
        """
        if not value:
            return value
        from app.services.api_endpoint_script_executor import redact_config
        out = dict(value)
        if isinstance(out.get("env_config"), dict):
            out["env_config"] = redact_config(out["env_config"])
        return out
# pragma: no cover  My80OmFIVnBZMlhscm9ua3VMazZkbU5uUWc9PTo3NzlkNDI5OA==

class OpenAPIParseResult(BaseModel):
    """OpenAPI 解析结果 Schema"""
    schema_title: str = Field(..., description="Schema 标题")
    schema_version: str = Field(..., description="Schema 版本")
    total_endpoints: int = Field(..., description="端点总数")
    tag_folders: list[dict] = Field(..., description="创建的标签文件夹列表")
    endpoints: list[dict] = Field(..., description="创建的端点列表")

class OpenAPIUploadRequest(BaseModel):
    """上传 OpenAPI 文件请求 Schema"""
    project_identifier: str = Field(..., description="项目标识符")
    parent_folder_id: UUID | None = Field(None, description="父文件夹 ID（可选）")
    file_content: dict = Field(..., description="OpenAPI JSON 内容")
    create_structure: bool = Field(True, description="是否自动创建文件夹结构")


# =============================================================================
# 统一导入响应结构（D1-D2：前后端数据契约）
# =============================================================================

class SourceType:
    """文档来源类型常量"""
    SWAGGER_ONLINE = "swagger_online"
    SWAGGER_OFFLINE = "swagger_offline"
    APIFOX_ONLINE = "apifox_online"
    APIFOX_OFFLINE = "apifox_offline"
    MANUAL_TEXT = "manual_text"
    MANUAL_FILE = "manual_file"


class TagFolderSummary(BaseModel):
    """标签文件夹摘要"""
    folder_id: str = Field(..., description="文件夹 ID")
    folder_name: str = Field(..., description="文件夹名称")
    endpoint_count: int = Field(..., description="接口数量")


class EndpointSummary(BaseModel):
    """端点摘要（用于预览）"""
    endpoint_id: str | None = Field(None, description="端点 ID（创建后填充）")
    display_name: str = Field(..., description="显示名称")
    method: str = Field(..., description="HTTP 方法")
    path: str = Field(..., description="API 路径")
    summary: str | None = Field(None, description="端点摘要")
    tag_group: str | None = Field(None, description="标签分组")
    has_auth: bool = Field(False, description="是否需要认证")
    auth_type: str | None = Field(None, description="认证类型")
    has_request_body: bool = Field(False, description="是否有请求体")
    has_path_params: bool = Field(False, description="是否有路径参数")
    has_query_params: bool = Field(False, description="是否有查询参数")


class ImportPreview(BaseModel):
    """导入预览摘要"""
    auth_summary: dict[str, int] = Field(
        default_factory=dict,
        description="鉴权类型统计 {bearer: 3, basic: 1, none: 5}"
    )
    method_summary: dict[str, int] = Field(
        default_factory=dict,
        description="方法统计 {GET: 10, POST: 5}"
    )
    path_prefixes: list[str] = Field(
        default_factory=list,
        description="路径前缀列表"
    )
    warnings: list[str] = Field(
        default_factory=list,
        description="预览阶段告警"
    )


class UnifiedImportResult(BaseModel):
    """
    统一导入响应结构

    两条导入链路（OpenAPI 原生 / 文本文档）都返回此结构，
    确保前端接口一致性。
    """
    source_type: str = Field(
        ...,
        description="来源类型: swagger_online/swagger_offline/apifox_online/apifox_offline/manual_text/manual_file"
    )
    schema_title: str = Field(..., description="Schema 标题")
    schema_version: str = Field(..., description="Schema 版本")
    total_endpoints: int = Field(..., description="端点总数")
    total_tags: int = Field(..., description="标签分组数")
    tag_folders: list[TagFolderSummary] = Field(..., description="标签文件夹列表")
    endpoints: list[EndpointSummary] = Field(..., description="端点摘要列表")
    warnings: list[str] = Field(
        default_factory=list,
        description="非致命告警（schema 缺失、字段不完整等）"
    )
    errors: list[str] = Field(
        default_factory=list,
        description="致命错误（格式不识别、URL 不可达等）"
    )
    preview: ImportPreview = Field(
        default_factory=ImportPreview,
        description="字段级预览摘要"
    )
    # 成功创建后的额外信息
    created: dict | None = Field(
        None,
        description="创建成功后的详细信息（落库时填充）"
    )


class UnifiedImportRequest(BaseModel):
    """统一导入请求"""
    project_identifier: str = Field(..., description="项目标识符")
    parent_folder_id: UUID | None = Field(None, description="父文件夹 ID")
    source_type: str = Field(
        ...,
        description="来源类型: swagger_online/swagger_offline/apifox_online/apifox_offline/manual_text/manual_file"
    )
    # 三种输入方式互斥
    url: str | None = Field(None, description="远程 URL（source_type 为 *_online 时必填）")
    file_content: dict | None = Field(None, description="文件内容（source_type 为 *_offline 时必填）")
    text_content: str | None = Field(None, description="文本内容（source_type 为 manual_text 时必填）")
    create_structure: bool = Field(True, description="是否自动创建文件夹结构")
    headers: dict[str, str] | None = Field(None, description="可选的请求头（用于认证等，如 Authorization、Cookie）")

    def get_input_description(self) -> str:
        """获取输入描述（用于日志/审计）"""
        if self.url:
            return f"URL: {self.url}"
        if self.file_content:
            return "文件上传"
        if self.text_content:
            return f"文本粘贴 ({len(self.text_content)} 字符)"
        return "未知输入"
