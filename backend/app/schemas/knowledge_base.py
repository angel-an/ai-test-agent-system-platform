"""
知识库相关的 Pydantic 模型

定义知识库系统的 Schema 接口
"""

from datetime import datetime
from typing import Optional, Any
from uuid import UUID

from pydantic import BaseModel, Field

from app.schemas.common import TimestampMixin


# ============================================================================
# 知识空间 Schema
# ============================================================================

class KnowledgeSpaceBase(BaseModel):
    """知识空间基础模型"""
    name: str = Field(..., min_length=1, max_length=255, description="知识空间名称")
    description: Optional[str] = Field(default=None, description="知识空间描述")
    business_line: Optional[str] = Field(default=None, description="业务线标识")
    folder_scope: Optional[list[str]] = Field(default=None, description="关联文件夹 ID 列表")


class KnowledgeSpaceCreate(KnowledgeSpaceBase):
    """创建知识空间请求模型"""
    pass


class KnowledgeSpaceUpdate(BaseModel):
    """更新知识空间请求模型"""
    name: Optional[str] = Field(default=None, min_length=1, max_length=255)
    description: Optional[str] = Field(default=None)
    business_line: Optional[str] = Field(default=None)
    folder_scope: Optional[list[str]] = Field(default=None)


class KnowledgeSpaceInfo(KnowledgeSpaceBase, TimestampMixin):
    """知识空间信息模型（响应用）"""
    id: UUID = Field(..., description="知识空间 ID")
    project_id: UUID = Field(..., description="所属项目 ID")
    created_by: UUID = Field(..., description="创建者 ID")
    document_count: int = Field(default=0, description="文档数量")

    model_config = {"from_attributes": True}


# ============================================================================
# 知识文档 Schema
# ============================================================================

class KnowledgeDocumentBase(BaseModel):
    """知识文档基础模型"""
    title: Optional[str] = Field(default=None, description="文档标题")


class KnowledgeDocumentCreate(KnowledgeDocumentBase):
    """创建知识文档请求模型"""
    file_name: str = Field(..., description="原始文件名")
    file_path: str = Field(..., description="MinIO 对象路径")
    file_size: int = Field(default=0, description="文件大小")
    content_type: str = Field(..., description="文件 MIME 类型")


class KnowledgeDocumentInfo(KnowledgeDocumentBase):
    """知识文档信息模型（响应用）"""
    id: UUID = Field(..., description="文档 ID")
    space_id: UUID = Field(..., description="所属知识空间 ID")
    file_name: str = Field(..., description="原始文件名")
    file_size: int = Field(default=0, description="文件大小")
    content_type: str = Field(..., description="文件 MIME 类型")
    status: str = Field(default="pending", description="索引状态")
    chunk_count: int = Field(default=0, description="切片数量")
    error_message: Optional[str] = Field(default=None, description="错误信息")
    created_by: UUID = Field(..., description="创建者 ID")
    created_at: datetime = Field(..., description="创建时间")
    updated_at: Optional[datetime] = Field(default=None, description="更新时间")

    model_config = {"from_attributes": True}


# ============================================================================
# 知识切片 Schema（内部使用）
# ============================================================================

class KnowledgeChunkInfo(BaseModel):
    """知识切片信息模型"""
    id: UUID = Field(..., description="切片 ID")
    document_id: UUID = Field(..., description="所属文档 ID")
    space_id: UUID = Field(..., description="所属知识空间 ID")
    content: str = Field(..., description="切片文本内容")
    chunk_index: int = Field(..., description="切片序号")
    meta_data: Optional[dict] = Field(default=None, description="切片元数据")

    model_config = {"from_attributes": True}


# ============================================================================
# 检索请求/响应 Schema
# ============================================================================

class KnowledgeRetrievalRequest(BaseModel):
    """知识检索请求模型"""
    query: str = Field(..., min_length=1, description="检索查询文本")
    space_id: Optional[str] = Field(default=None, description="指定知识空间 ID")
    top_k: int = Field(default=5, ge=1, le=20, description="返回结果数量")
    similarity_threshold: Optional[float] = Field(default=0.7, ge=0, le=1, description="相似度阈值")


class KnowledgeRetrievalResult(BaseModel):
    """单条检索结果"""
    chunk_id: str = Field(..., description="切片 ID")
    document_id: str = Field(..., description="文档 ID")
    document_name: str = Field(..., description="文档名称")
    content: str = Field(..., description="切片内容")
    score: float = Field(..., description="相似度分数")
    meta_data: Optional[dict] = Field(default=None, description="元数据")


class KnowledgeRetrievalResponse(BaseModel):
    """知识检索响应模型"""
    success: bool = Field(default=True, description="是否成功")
    results: list[KnowledgeRetrievalResult] = Field(default_factory=list, description="检索结果")
    total: int = Field(default=0, description="结果总数")
    degraded: bool = Field(default=False, description="是否触发降级")
    degraded_reason: Optional[str] = Field(default=None, description="降级原因")
    query: str = Field(..., description="原始查询")


# ============================================================================
# 上传响应 Schema
# ============================================================================

class KnowledgeUploadResponse(BaseModel):
    """文档上传响应"""
    document_id: str = Field(..., description="文档 ID")
    file_name: str = Field(..., description="文件名")
    file_size: int = Field(..., description="文件大小")
    status: str = Field(default="pending", description="索引状态")
    message: str = Field(default="上传成功，等待索引", description="状态消息")


# ============================================================================
# 空间列表查询参数
# ============================================================================

class KnowledgeSpaceListParams(BaseModel):
    """知识空间列表查询参数"""
    search: Optional[str] = Field(default=None, description="搜索关键词")
    business_line: Optional[str] = Field(default=None, description="业务线过滤")
