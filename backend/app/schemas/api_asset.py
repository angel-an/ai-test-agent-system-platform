"""统一接口资产包 Pydantic Schemas — 三文件联合导入的数据契约。"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

# ---------- 枚举常量 ----------

SourceType = Literal["excel_mx", "postman_collection", "postman_environment"]
BundleStatus = Literal["draft", "parsed", "matched", "confirmed", "imported", "failed"]
ContractStatus = Literal["pending", "matched", "conflicted", "confirmed", "imported"]
ConflictSeverity = Literal["high", "medium", "low"]
ConflictStatus = Literal["open", "confirmed", "resolved", "waived"]
EnvironmentKind = Literal["uat", "production"]


# ---------- Bundle ----------

class ApiDocumentBundleCreate(BaseModel):
    """创建资产包请求"""
    name: str = Field(..., min_length=1, max_length=255, description="资产包名称")
    system_type: str | None = Field(None, description="系统类型: crm_non_pos / pos / marketing_gateway")


class ApiDocumentBundleResponse(BaseModel):
    """资产包响应"""
    id: UUID
    project_id: UUID
    name: str
    system_type: str | None
    status: BundleStatus
    current_version: int
    parse_result: dict[str, Any] | None
    created_by: str | None
    created_at: datetime
    updated_at: datetime | None

    model_config = ConfigDict(from_attributes=True)


# ---------- Source ----------

class ApiDocumentSourceResponse(BaseModel):
    """来源文件响应"""
    id: UUID
    bundle_id: UUID
    source_type: SourceType
    file_name: str
    file_hash: str
    file_size: int
    attachment_id: UUID | None
    parse_status: str
    parse_error: str | None
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


# ---------- Contract ----------

class ApiInterfaceContractSummary(BaseModel):
    """契约摘要（列表用）"""
    id: UUID
    endpoint_key: str
    method: str
    path: str
    normalized_path: str
    name: str | None
    version: int
    status: ContractStatus
    pending_count: int = Field(0, description="待确认项数量")
    source_types: list[str] = Field(default_factory=list, description="已关联来源类型")

    model_config = ConfigDict(from_attributes=True)


class ApiInterfaceContractDetail(BaseModel):
    """契约详情"""
    id: UUID
    bundle_id: UUID
    endpoint_key: str
    method: str
    path: str
    normalized_path: str
    name: str | None
    version: int
    status: ContractStatus
    request: dict[str, Any] | None
    response: dict[str, Any] | None
    auth: dict[str, Any] | None
    assertions: list[dict[str, Any]] | None
    dependencies: list[dict[str, Any]] | None
    pending_confirmations: list[dict[str, Any]] | None
    source_refs: list[dict[str, Any]] | None
    imported_endpoint_id: UUID | None
    imported_at: datetime | None

    model_config = ConfigDict(from_attributes=True)


# ---------- Conflict ----------

class ApiConflictResponse(BaseModel):
    """冲突响应"""
    id: UUID
    bundle_id: UUID
    contract_id: UUID | None
    conflict_type: str
    severity: ConflictSeverity
    status: ConflictStatus
    message: str
    details: dict[str, Any] | None
    source_refs: list[dict[str, Any]] | None

    model_config = ConfigDict(from_attributes=True)


class ApiConflictConfirmRequest(BaseModel):
    """确认冲突请求"""
    resolution: Literal["accept", "waive"] = Field(..., description="接受冲突并继续导入 / 放弃处理")
    note: str | None = Field(None, description="处理备注")


# ---------- Import ----------

class ApiImportRequest(BaseModel):
    """导入确认请求"""
    contract_ids: list[UUID] = Field(..., min_length=1, description="要导入的契约 ID 列表")
    parent_folder_id: UUID | None = Field(None, description="父文件夹 ID")


class ApiImportResult(BaseModel):
    """导入结果"""
    imported: list[dict[str, Any]] = Field(default_factory=list, description="成功导入 [{contract_id, endpoint_id}]")
    skipped: list[dict[str, Any]] = Field(default_factory=list, description="跳过（已存在）[{contract_id, endpoint_id}]")
    failed: list[dict[str, Any]] = Field(default_factory=list, description="失败 [{contract_id, reason}]")


# ---------- Match ----------

class ApiMatchResult(BaseModel):
    """匹配结果"""
    total_contracts: int
    matched: int
    conflicted: int
    pending: int
    conflicts_by_severity: dict[str, int] = Field(default_factory=dict)


# ---------- Variable ----------

class ApiVariableUpsertItem(BaseModel):
    """单个变量追加/更新请求（手动编辑）"""
    name: str = Field(..., min_length=1, max_length=255, description="变量名")
    value_ref: str = Field(..., description="值引用，必须是 env:NAME 格式")
    is_secret: bool = Field(default=False, description="是否敏感变量")
    enabled: bool = Field(default=True, description="是否启用")
    environment: str | None = Field(default=None, description="环境标识: uat / production")


class ApiVariableUpsertRequest(BaseModel):
    """批量变量追加/更新请求"""
    variables: list[ApiVariableUpsertItem] = Field(..., min_length=1)


class ApiVariableResponse(BaseModel):
    """变量响应（secret 脱敏）"""
    id: UUID
    bundle_id: UUID
    name: str
    value_ref: str | None
    is_secret: bool
    environment: str | None
    source_type: str
    enabled: bool

    model_config = ConfigDict(from_attributes=True)


# ---------- 解析统计 ----------

class ParseStats(BaseModel):
    """解析统计"""
    excel_interfaces: int = 0
    collection_requests: int = 0
    environment_variables: int = 0
    errors: list[str] = Field(default_factory=list)
