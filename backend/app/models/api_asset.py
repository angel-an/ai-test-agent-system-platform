"""统一接口资产包模型 — 三文件（Excel/Environment/Collection）联合导入的承载结构。

设计原则：
- 一个 ApiDocumentBundle 代表一次"三文件联合导入"的会话（project 隔离）。
- ApiInterfaceContract 是统一接口契约（method+path 归一化唯一），可关联多个来源片段。
- ApiDocumentSource 记录每个来源文件（Excel/Collection/Environment）的 hash 与解析结果。
- ApiRequestTemplate / ApiVariable / ApiAssertion / ApiDependency / ApiConflict 挂接到契约或 bundle。
- 全部使用 JSONB 存业务负载，保证 schema 演进不需要频繁迁移。
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import ForeignKey, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID, JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin, UUIDMixin


class ApiDocumentBundle(Base, UUIDMixin, TimestampMixin):
    """接口资产包：一次三文件联合导入的顶层容器。"""

    __tablename__ = "api_document_bundles"
    __table_args__ = (
        UniqueConstraint("project_id", "name", name="uq_bundle_project_name"),
        {"comment": "接口资产包表"},
    )

    project_id: Mapped[UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("projects.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
        comment="所属项目 ID",
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False, comment="资产包名称")
    system_type: Mapped[str | None] = mapped_column(
        String(50), nullable=True, comment="系统类型: crm_non_pos / pos / marketing_gateway"
    )
    status: Mapped[str] = mapped_column(
        String(30), nullable=False, default="draft", index=True,
        comment="状态: draft / parsed / matched / confirmed / imported / failed",
    )
    current_version: Mapped[int] = mapped_column(default=1, comment="当前版本号")
    parse_result: Mapped[dict | None] = mapped_column(
        JSONB, nullable=True, comment="最近一次解析结果摘要 {stats, errors}"
    )
    created_by: Mapped[str | None] = mapped_column(String(255), nullable=True, comment="创建人")

    sources: Mapped[list["ApiDocumentSource"]] = relationship(
        "ApiDocumentSource", back_populates="bundle", cascade="all, delete-orphan"
    )
    contracts: Mapped[list["ApiInterfaceContract"]] = relationship(
        "ApiInterfaceContract", back_populates="bundle", cascade="all, delete-orphan"
    )
    variables: Mapped[list["ApiVariable"]] = relationship(
        "ApiVariable", back_populates="bundle", cascade="all, delete-orphan"
    )
    conflicts: Mapped[list["ApiConflict"]] = relationship(
        "ApiConflict", back_populates="bundle", cascade="all, delete-orphan"
    )


class ApiDocumentSource(Base, UUIDMixin, TimestampMixin):
    """来源文件记录：Excel / Collection / Environment 各一条。"""

    __tablename__ = "api_document_sources"
    __table_args__ = (
        UniqueConstraint("bundle_id", "source_type", name="uq_source_bundle_type"),
        {"comment": "接口资产包来源文件表"},
    )

    bundle_id: Mapped[UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("api_document_bundles.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    source_type: Mapped[str] = mapped_column(
        String(30), nullable=False, comment="来源类型: excel_mx / postman_collection / postman_environment"
    )
    file_name: Mapped[str] = mapped_column(String(500), nullable=False, comment="原始文件名")
    file_hash: Mapped[str] = mapped_column(
        String(64), nullable=False, index=True, comment="SHA-256（重复上传去重依据）"
    )
    file_size: Mapped[int] = mapped_column(default=0, comment="文件大小（字节）")
    attachment_id: Mapped[UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("attachments.id", ondelete="SET NULL"),
        nullable=True,
        comment="原始文件附件 ID",
    )
    parse_status: Mapped[str] = mapped_column(
        String(20), nullable=False, default="pending", comment="pending / completed / failed"
    )
    parse_result: Mapped[dict | None] = mapped_column(
        JSONB, nullable=True, comment="解析结果（统一模型列表或变量列表）"
    )
    parse_error: Mapped[str | None] = mapped_column(Text, nullable=True, comment="解析失败原因")

    bundle: Mapped["ApiDocumentBundle"] = relationship("ApiDocumentBundle", back_populates="sources")


class ApiInterfaceContract(Base, UUIDMixin, TimestampMixin):
    """统一接口契约：method + normalized_path 在 bundle 内唯一。"""

    __tablename__ = "api_interface_contracts"
    __table_args__ = (
        UniqueConstraint("bundle_id", "method", "normalized_path", name="uq_contract_bundle_method_path"),
        {"comment": "统一接口契约表"},
    )

    bundle_id: Mapped[UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("api_document_bundles.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    endpoint_key: Mapped[str] = mapped_column(
        String(255), nullable=False, index=True, comment="接口标识（如 api_memLogin）"
    )
    method: Mapped[str] = mapped_column(String(10), nullable=False, comment="HTTP 方法")
    path: Mapped[str] = mapped_column(String(2048), nullable=False, comment="原始路径")
    normalized_path: Mapped[str] = mapped_column(
        String(2048), nullable=False, comment="归一化路径（去 base_url/变量/尾斜杠/小写）"
    )
    name: Mapped[str | None] = mapped_column(String(500), nullable=True, comment="接口显示名")
    version: Mapped[int] = mapped_column(default=1, comment="契约版本（内容变化 +1）")
    status: Mapped[str] = mapped_column(
        String(30), nullable=False, default="pending", index=True,
        comment="pending / matched / conflicted / confirmed / imported",
    )
    request: Mapped[dict | None] = mapped_column(JSONB, nullable=True, comment="请求契约 {fields, body}")
    response: Mapped[dict | None] = mapped_column(JSONB, nullable=True, comment="响应契约 {fields, error_codes}")
    auth: Mapped[dict | None] = mapped_column(JSONB, nullable=True, comment="鉴权配置")
    assertions: Mapped[list | None] = mapped_column(JSONB, nullable=True, comment="断言列表")
    dependencies: Mapped[list | None] = mapped_column(JSONB, nullable=True, comment="依赖列表")
    pending_confirmations: Mapped[list | None] = mapped_column(
        JSONB, nullable=True, comment="待确认项（冲突/缺失）"
    )
    source_refs: Mapped[list | None] = mapped_column(
        JSONB, nullable=True, comment="来源引用 [{source_type, location}]"
    )
    imported_endpoint_id: Mapped[UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("api_endpoints.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
        comment="导入后对应的 APIEndpoint ID",
    )
    imported_at: Mapped[datetime | None] = mapped_column(
        nullable=True, comment="导入时间"
    )

    bundle: Mapped["ApiDocumentBundle"] = relationship("ApiDocumentBundle", back_populates="contracts")
    templates: Mapped[list["ApiRequestTemplate"]] = relationship(
        "ApiRequestTemplate", back_populates="contract", cascade="all, delete-orphan"
    )


class ApiRequestTemplate(Base, UUIDMixin, TimestampMixin):
    """请求模板：来自 Collection 的原始请求（headers/query/body/script）。"""

    __tablename__ = "api_request_templates"
    __table_args__ = {"comment": "请求模板表"}

    contract_id: Mapped[UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("api_interface_contracts.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    name: Mapped[str] = mapped_column(String(500), nullable=False, comment="模板名称")
    headers: Mapped[dict | None] = mapped_column(JSONB, nullable=True, comment="请求头")
    query: Mapped[dict | None] = mapped_column(JSONB, nullable=True, comment="查询参数")
    body: Mapped[dict | None] = mapped_column(JSONB, nullable=True, comment="请求体")
    pre_request_script: Mapped[str | None] = mapped_column(Text, nullable=True, comment="pre-request 脚本")
    test_script: Mapped[str | None] = mapped_column(Text, nullable=True, comment="test 脚本")
    source_location: Mapped[dict | None] = mapped_column(
        JSONB, nullable=True, comment="来源位置 {folder, item_index}"
    )

    contract: Mapped["ApiInterfaceContract"] = relationship("ApiInterfaceContract", back_populates="templates")


class ApiVariable(Base, UUIDMixin, TimestampMixin):
    """环境变量：来自 Environment 或 Collection 变量。"""

    __tablename__ = "api_variables"
    __table_args__ = (
        UniqueConstraint("bundle_id", "name", name="uq_variable_bundle_name"),
        {"comment": "环境变量表"},
    )

    bundle_id: Mapped[UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("api_document_bundles.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False, comment="变量名")
    value_ref: Mapped[str | None] = mapped_column(
        String(500), nullable=True, comment="值引用（env:XXX），secret 不明文存储"
    )
    is_secret: Mapped[bool] = mapped_column(default=False, comment="是否敏感变量")
    environment: Mapped[str | None] = mapped_column(
        String(50), nullable=True, comment="环境标识: uat / production"
    )
    source_type: Mapped[str] = mapped_column(
        String(30), nullable=False, default="postman_environment", comment="变量来源"
    )
    enabled: Mapped[bool] = mapped_column(default=True, comment="是否启用")

    bundle: Mapped["ApiDocumentBundle"] = relationship("ApiDocumentBundle", back_populates="variables")


class ApiAssertion(Base, UUIDMixin, TimestampMixin):
    """断言：来自 Collection test 脚本或 Excel 契约。"""

    __tablename__ = "api_assertions"
    __table_args__ = {"comment": "断言表"}

    contract_id: Mapped[UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("api_interface_contracts.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    source_type: Mapped[str] = mapped_column(
        String(30), nullable=False, comment="来源: collection_script / excel_contract"
    )
    assert_type: Mapped[str] = mapped_column(
        String(50), nullable=False, comment="类型: status / json_path / header / body_contains"
    )
    expression: Mapped[str] = mapped_column(Text, nullable=False, comment="断言表达式")
    expected: Mapped[str | None] = mapped_column(Text, nullable=True, comment="期望值")
    source_location: Mapped[dict | None] = mapped_column(JSONB, nullable=True, comment="来源位置")


class ApiDependency(Base, UUIDMixin, TimestampMixin):
    """接口依赖：如 memLogout 依赖 memLogin 的 SessionKey。"""

    __tablename__ = "api_dependencies"
    __table_args__ = {"comment": "接口依赖表"}

    bundle_id: Mapped[UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("api_document_bundles.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    from_contract_id: Mapped[UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("api_interface_contracts.id", ondelete="CASCADE"),
        nullable=False,
        comment="依赖方契约 ID",
    )
    to_contract_id: Mapped[UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("api_interface_contracts.id", ondelete="CASCADE"),
        nullable=False,
        comment="被依赖方契约 ID",
    )
    variable: Mapped[str] = mapped_column(String(255), nullable=False, comment="传递的变量名")
    extract_expr: Mapped[str | None] = mapped_column(
        Text, nullable=True, comment="提取表达式（如 json.Data.SessionKey）"
    )


class ApiConflict(Base, UUIDMixin, TimestampMixin):
    """冲突记录：跨来源匹配时产生，含等级与状态。"""

    __tablename__ = "api_conflicts"
    __table_args__ = {"comment": "接口冲突表"}

    bundle_id: Mapped[UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("api_document_bundles.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    contract_id: Mapped[UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("api_interface_contracts.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
        comment="关联契约（未匹配时为空）",
    )
    conflict_type: Mapped[str] = mapped_column(
        String(50), nullable=False, comment="冲突类型: method/path/field/required/auth/assertion/env_missing/production_url/script_unconvertible/excel_only/collection_only"
    )
    severity: Mapped[str] = mapped_column(
        String(10), nullable=False, default="medium", index=True,
        comment="等级: high / medium / low",
    )
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default="open", index=True,
        comment="open / confirmed / resolved / waived",
    )
    message: Mapped[str] = mapped_column(Text, nullable=False, comment="冲突描述")
    details: Mapped[dict | None] = mapped_column(JSONB, nullable=True, comment="冲突细节")
    source_refs: Mapped[list | None] = mapped_column(
        JSONB, nullable=True, comment="冲突双方来源引用"
    )

    bundle: Mapped["ApiDocumentBundle"] = relationship("ApiDocumentBundle", back_populates="conflicts")
