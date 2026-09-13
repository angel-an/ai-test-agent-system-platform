"""运行产物模型 — 记录每次运行的产物状态、哈希和绑定信息。

状态流转：
    draft -> published -> executing -> completed
                             \-> failed
                             \-> blocked

约束：同一个运行实例禁止重复绑定不同项目或测试对象。
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import ForeignKey, String, Text, Integer
from sqlalchemy.dialects.postgresql import UUID as PGUUID, JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin


class TestRunArtifact(Base, TimestampMixin):
    """测试运行产物表。

    记录每次执行绑定的产物信息，用于执行前校验和审计追踪。
    """

    __tablename__ = "test_run_artifacts"
    __table_args__ = {"comment": "测试运行产物表（执行治理层）"}

    id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), primary_key=True, default=uuid4, comment="主键 ID"
    )

    # 项目绑定
    project_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("projects.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
        comment="所属项目 ID"
    )

    # 测试对象绑定（三选一）
    test_case_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("test_cases.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
        comment="测试用例 ID（API/Web 测试）"
    )
    scenario_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("test_scenarios.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
        comment="场景 ID"
    )

    # 运行标识
    run_id: Mapped[str] = mapped_column(
        String(100), nullable=False, index=True, comment="运行标识符"
    )

    # 产物信息
    artifact_type: Mapped[str] = mapped_column(
        String(50), nullable=False, index=True,
        comment="产物类型: api_script / web_script / scenario_manifest / orchestration"
    )
    artifact_path: Mapped[str] = mapped_column(
        Text, nullable=False, comment="产物文件路径"
    )
    artifact_version: Mapped[str | None] = mapped_column(
        String(50), nullable=True, comment="产物版本号"
    )

    # 哈希校验
    expected_sha256: Mapped[str | None] = mapped_column(
        String(64), nullable=True, comment="登记 SHA-256"
    )
    actual_sha256: Mapped[str | None] = mapped_column(
        String(64), nullable=True, comment="实际 SHA-256"
    )

    # 状态
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default="draft", index=True,
        comment="状态: draft / published / executing / completed / failed / blocked"
    )

    # 执行信息
    mode: Mapped[str] = mapped_column(
        String(10), nullable=False, default="safe",
        comment="执行模式: safe / live"
    )
    executed_at: Mapped[datetime | None] = mapped_column(
        nullable=True, comment="执行开始时间"
    )
    completed_at: Mapped[datetime | None] = mapped_column(
        nullable=True, comment="执行完成时间"
    )
    duration_ms: Mapped[int | None] = mapped_column(
        Integer, nullable=True, comment="执行耗时（毫秒）"
    )

    # 阻断信息
    blocked_reason: Mapped[str | None] = mapped_column(
        Text, nullable=True, comment="阻断原因"
    )
    blocked_reason_code: Mapped[str | None] = mapped_column(
        String(50), nullable=True, comment="阻断原因代码"
    )

    # 扩展信息（metadata 是 SQLAlchemy Declarative 保留字，改用 artifact_metadata）
    artifact_metadata: Mapped[dict | None] = mapped_column(
        "metadata", JSONB, nullable=True, default=dict, comment="扩展元数据"
    )

    def __repr__(self) -> str:
        return (
            f"<TestRunArtifact(id={self.id}, run_id={self.run_id}, "
            f"type={self.artifact_type}, status={self.status})>"
        )
