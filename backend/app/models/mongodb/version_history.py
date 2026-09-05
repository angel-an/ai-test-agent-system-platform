"""
测试用例版本历史模型

使用 MongoDB 存储测试用例的版本历史记录
"""

from datetime import datetime, timezone
from typing import Any, Optional
from uuid import UUID
# noqa  MC8zOmFIVnBZMlhscm9ua3VMazZZV1ppUnc9PTozODkxYjM0NA==

from pydantic import BaseModel, ConfigDict, Field, field_serializer

# noqa  MS8zOmFIVnBZMlhscm9ua3VMazZZV1ppUnc9PTozODkxYjM0NA==

class TestCaseVersionHistory(BaseModel):
    """
    测试用例版本历史
    
    记录测试用例每次修改的完整快照
    """
    test_case_id: UUID = Field(..., description="测试用例 ID")
    version: int = Field(..., description="版本号")
    snapshot: dict[str, Any] = Field(..., description="测试用例快照数据")
    changed_by: UUID = Field(..., description="修改者 ID")
    changed_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),
        description="修改时间"
    )
    change_description: Optional[str] = Field(
        default=None,
        description="修改说明"
    )

    @field_serializer("test_case_id", "changed_by")
    def serialize_uuid(self, value: UUID) -> str:
        return str(value)

    @field_serializer("changed_at")
    def serialize_datetime(self, value: datetime) -> str:
        return value.isoformat()

    @classmethod
    def collection_name(cls) -> str:
        """获取 MongoDB 集合名称"""
        return "test_case_versions"
    
    def to_document(self) -> dict:
        """转换为 MongoDB 文档"""
        return {
            "test_case_id": str(self.test_case_id),
            "version": self.version,
            "snapshot": self.snapshot,
            "changed_by": str(self.changed_by),
            "changed_at": self.changed_at,
            "change_description": self.change_description,
        }

# fmt: off  Mi8zOmFIVnBZMlhscm9ua3VMazZZV1ppUnc9PTozODkxYjM0NA==

class TestCaseVersionListItem(BaseModel):
    """版本历史列表项"""
    version: int = Field(..., description="版本号")
    changed_by: str = Field(..., description="修改者邮箱")
    changed_at: datetime = Field(..., description="修改时间")
    change_description: Optional[str] = Field(default=None, description="修改说明")

