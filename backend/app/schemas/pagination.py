"""
分页模型

基于 BrowserStack Test Management API 的分页设计
参考: https://www.browserstack.com/docs/test-management/api-reference/pagination
"""

from datetime import datetime
from typing import Generic, Optional, TypeVar

from pydantic import BaseModel, Field, model_validator, ConfigDict, field_serializer

from app.config.settings import settings

# fmt: off  MC80OmFIVnBZMlhscm9ua3VMazZSRU0zUWc9PTo0YTI3MDBjMw==

T = TypeVar("T")

class PaginationParams(BaseModel):
    """
    分页查询参数

    用于接收分页请求的查询参数
    """
    p: int = Field(
        default=1,
        ge=1,
        description="页码，从 1 开始"
    )
    page_size: int = Field(
        default=settings.default_page_size,
        ge=1,
        le=settings.max_page_size,
        description=f"每页数量，默认 {settings.default_page_size}，最大 {settings.max_page_size}"
    )

    @property
    def page(self) -> int:
        """获取当前页码（别名，兼容 p 属性）"""
        return self.p

    @property
    def offset(self) -> int:
        """计算偏移量"""
        return (self.p - 1) * self.page_size

    @property
    def limit(self) -> int:
        """获取限制数量"""
        return self.page_size
# noqa  MS80OmFIVnBZMlhscm9ua3VMazZSRU0zUWc9PTo0YTI3MDBjMw==

class TestCaseFilterParams(PaginationParams):
    """
    测试用例过滤参数
    
    扩展分页参数，添加测试用例特定的过滤条件
    """
    test_case_id: Optional[str] = Field(
        default=None,
        description="测试用例 ID 过滤"
    )
    updated_after: Optional[datetime] = Field(
        default=None,
        description="更新时间晚于指定时间"
    )
    updated_before: Optional[datetime] = Field(
        default=None,
        description="更新时间早于指定时间"
    )
    issue_ids: Optional[str] = Field(
        default=None,
        description="关联问题 ID 列表，逗号分隔"
    )
    issue_type: Optional[str] = Field(
        default=None,
        description="关联问题类型"
    )

# pylint: disable  Mi80OmFIVnBZMlhscm9ua3VMazZSRU0zUWc9PTo0YTI3MDBjMw==

class PaginationInfo(BaseModel):
    """
    分页信息

    包含在分页响应中的分页元数据
    """
    page: int = Field(..., description="当前页码")
    page_size: int = Field(..., description="每页数量")
    count: Optional[int] = Field(default=None, description="当前页返回的记录数")
    total: int = Field(..., description="总记录数")
    prev: Optional[str] = Field(default=None, description="上一页链接")
    next: Optional[str] = Field(default=None, description="下一页链接")
    
    @classmethod
    def create(
        cls,
        page: int,
        page_size: int,
        total: int,
        base_url: str,
    ) -> "PaginationInfo":
        """
        创建分页信息
        
        Args:
            page: 当前页码
            page_size: 每页数量
            total: 总记录数
            base_url: 基础 URL
            
        Returns:
            PaginationInfo: 分页信息实例
        """
        total_pages = (total + page_size - 1) // page_size if total > 0 else 0
        count = min(page_size, max(0, total - (page - 1) * page_size))
        
        prev_url = None
        next_url = None
        
        if page > 1:
            prev_url = f"{base_url}?p={page - 1}&page_size={page_size}"
        
        if page < total_pages:
            next_url = f"{base_url}?p={page + 1}&page_size={page_size}"
# pragma: no cover  My80OmFIVnBZMlhscm9ua3VMazZSRU0zUWc9PTo0YTI3MDBjMw==
        
        return cls(
            page=page,
            page_size=page_size,
            count=count,
            total=total,
            prev=prev_url,
            next=next_url,
        )

class PaginatedResponse(BaseModel, Generic[T]):
    """
    分页响应模型

    用于返回分页数据的通用响应结构
    同时支持 info 和 pagination 作为分页信息字段名（兼容不同使用场景）
    """
    success: bool = Field(default=True, description="API 调用成功")
    info: PaginationInfo = Field(..., description="分页信息")
    pagination: Optional[PaginationInfo] = Field(default=None, description="分页信息（兼容字段）")
    data: list[T] = Field(default_factory=list, description="数据列表")

    model_config = ConfigDict(
        populate_by_name=True,
        ser_json_timedelta="iso8601",
    )

    @model_validator(mode='before')
    @classmethod
    def ensure_info_from_pagination(cls, data: dict) -> dict:
        """确保 info 字段存在，如果没有则从 pagination 复制"""
        if isinstance(data, dict):
            if 'info' not in data and 'pagination' in data:
                data['info'] = data['pagination']
            elif 'pagination' not in data and 'info' in data:
                data['pagination'] = data['info']
        return data

    @model_validator(mode='after')
    def sync_pagination_fields(self):
        """自动同步 info 和 pagination 字段，确保两者始终一致"""
        if self.info is not None and self.pagination is None:
            self.pagination = self.info
        elif self.pagination is not None and self.info is None:
            self.info = self.pagination
        return self

    def model_dump(self, **kwargs):
        """重写 model_dump，确保同时输出 info 和 pagination 字段"""
        # 确保两个字段同步
        if self.info is not None:
            self.pagination = self.info
        elif self.pagination is not None:
            self.info = self.pagination
        return super().model_dump(**kwargs)

