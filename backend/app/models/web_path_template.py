"""项目级 Web 验证路径模板。"""

from sqlalchemy import ForeignKey, String, Text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin, UUIDMixin


class ProjectWebPathTemplate(Base, UUIDMixin, TimestampMixin):
    """项目内可复用的 Web 验证路径配置。"""

    __tablename__ = "project_web_path_templates"
    __table_args__ = {"comment": "项目级 Web 验证路径模板"}

    project_id: Mapped[UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("projects.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
        comment="所属项目 ID",
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False, comment="模板名称")
    side: Mapped[str | None] = mapped_column(String(50), nullable=True, comment="端类型，如 B端/C端")
    module: Mapped[str | None] = mapped_column(String(255), nullable=True, comment="业务模块")
    business_type: Mapped[str | None] = mapped_column(String(255), nullable=True, comment="业务类型")
    action: Mapped[str | None] = mapped_column(String(100), nullable=True, comment="操作动作")
    base_url: Mapped[str | None] = mapped_column(String(2048), nullable=True, comment="入口 URL，可使用占位值")
    login_profile: Mapped[str | None] = mapped_column(String(100), nullable=True, comment="登录配置引用")
    match_keywords: Mapped[list | None] = mapped_column(JSONB, nullable=True, default=list, comment="匹配关键词")
    navigation_path: Mapped[list] = mapped_column(JSONB, nullable=False, default=list, comment="Web 验证路径")
    description: Mapped[str | None] = mapped_column(Text, nullable=True, comment="说明")
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="active", index=True, comment="状态")
