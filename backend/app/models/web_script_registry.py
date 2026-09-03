"""脚本来源注册表（执行治理层 2a 严格来源授权）。

save_web_test_script 保存脚本时，在同一事务内登记
(真实项目标识, attachment_id, script_hash)；execute_web_script 执行前按
真实项目 + 内容哈希校验，未登记/项目不符 → 终局拒绝。

rev20 修正：
- **每附件一个当前哈希**（attachment_id UNIQUE + upsert）：重复保存同一脚本 →
  更新而非新增（不触发唯一约束）；附件更新为新脚本 → 旧哈希自动失效（旧内容
  不再通过授权）——满足"精确版本审计/撤销"语义；
- project_identifier 存**真实项目标识**（由子功能 project_id 反查 Project.identifier，
  不信任工具参数）；
- attachment_id 为外键关联 attachments（版本锚点，支持审计/撤销到具体保存版本）；
- 使用通用 `sqlalchemy.Uuid`（与迁移 sa.Uuid 对齐，PG/SQLite 均可用，便于真实 DB 测试）。
"""

from __future__ import annotations

from uuid import UUID, uuid4

from sqlalchemy import ForeignKey, String, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin


class WebScriptRegistry(Base, TimestampMixin):
    """Web 测试脚本来源注册表。"""

    __tablename__ = "web_script_registry"
    __table_args__ = {"comment": "Web 测试脚本来源注册表（执行治理层 2a）"}

    id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), primary_key=True, default=uuid4, comment="主键 ID"
    )

    # 真实项目标识（由子功能 project_id 反查 Project.identifier 得到，非调用方参数）
    project_identifier: Mapped[str] = mapped_column(
        String(100), nullable=False, index=True, comment="真实项目标识（来源归属）"
    )
    # 附件版本锚点：每附件仅一个当前哈希（rev20，唯一），支持精确审计/撤销
    attachment_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("attachments.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
        index=True,
        comment="附件 ID（save_web_test_script 产物，唯一：每附件一个当前哈希）",
    )
    script_hash: Mapped[str] = mapped_column(
        String(64), nullable=False, index=True, comment="脚本内容 SHA-256（当前版本）"
    )
    # rev38/P0-2：版本状态——effective（当前生效，执行授权基准）/ proposed（待评审发布）/
    # old（历史版本）。存量行默认 effective（迁移 0009），现有脚本不失效。
    version_status: Mapped[str] = mapped_column(
        String(20), default="effective", nullable=False, index=True,
        comment="脚本版本状态：effective/proposed/old",
    )
    script_language: Mapped[str] = mapped_column(
        String(32), default="", comment="脚本语言（typescript/python 等）"
    )
    script_format: Mapped[str] = mapped_column(
        String(32), default="", comment="脚本格式（playwright 等）"
    )
    created_by: Mapped[str] = mapped_column(String(64), default="web-agent")
