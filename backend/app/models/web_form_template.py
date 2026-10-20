"""项目级「业务模板 → 表单定位器」注册表（P6 数据化注册）。

背景：此前"某业务模板用哪张表单定位器表"写在代码里
（``app/services/web_case_compiler.TEMPLATE_FORM_LOCATORS``，只有内置的储值模板一项）。
新业务模板要么改代码，要么走子功能 ``custom_config.field_locators``——
前者需要发版，后者要求每个子功能都手配一遍，同一个模板在多个子功能间无法复用。

本表把这份映射**数据化**：按 ``项目 + 模板键`` 注册一份定位器表，
同一项目下复用，且可在线维护（不必发版）。解析优先级（见 ``resolve_form_locators``）：

1. 子功能 ``custom_config.field_locators``（最具体，业务侧临时覆盖）；
2. **本表注册**（项目级复用）；
3. ``TEMPLATE_FORM_LOCATORS`` 内置表（老项目零改动兜底）；
4. 都没有 → 不生成表单检查，断言如实留在 ``pending_locator``（不回退到别的业务的表）。
"""

from sqlalchemy import ForeignKey, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin, UUIDMixin


class WebFormTemplate(Base, UUIDMixin, TimestampMixin):
    """项目内按模板键注册的表单定位器（数据化注册，替代代码硬编码）。"""

    __tablename__ = "web_form_templates"
    __table_args__ = (
        UniqueConstraint("project_id", "template_key", name="uq_web_form_template_project_key"),
        {"comment": "项目级业务模板注册（表单定位器数据化）"},
    )

    project_id: Mapped[UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("projects.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
        comment="所属项目 ID",
    )
    sub_function_id: Mapped[UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("web_sub_functions.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
        comment="登记来源子功能 ID（仅溯源用，不参与匹配；匹配键是 项目 + 模板键）",
    )
    template_key: Mapped[str] = mapped_column(
        String(120),
        nullable=False,
        index=True,
        comment="业务模板键，与用例推导出的模板键一致（如 enterprise_storage_recharge）",
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False, comment="可读名称")
    fields: Mapped[dict] = mapped_column(
        JSONB,
        nullable=False,
        default=dict,
        comment='定位器字段表，与 locators["fields"] 同构：{字段名: {"kind": "input|radio|textarea|select", ...}}',
    )
    extra_config: Mapped[dict | None] = mapped_column(
        JSONB,
        nullable=True,
        default=dict,
        comment=(
            "定位器表的其余键（form_button / modal / field_modes / upload_fields / toggles 等），"
            "与 fields 合并后即为**完整**定位器表。实测教训：只登记 fields 会让执行器拿不到 "
            "form_button 而在打开表单处崩溃（TR-163），故完整表必须可登记。"
        ),
    )
    source_ref: Mapped[str | None] = mapped_column(
        String(255),
        nullable=True,
        comment="定位器出处（如「2026-09-30 自 UAT 页面实测抓取」），供审计",
    )
    description: Mapped[str | None] = mapped_column(Text, nullable=True, comment="说明")
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default="active", index=True, comment="状态：active/disabled"
    )
