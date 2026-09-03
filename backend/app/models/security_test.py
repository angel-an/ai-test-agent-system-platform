"""
安全测试模型

定义安全测试（渗透测试）任务、报告和漏洞发现的表结构
"""

from sqlalchemy import ForeignKey, String, Text, Enum as SQLEnum, Float
from sqlalchemy.dialects.postgresql import UUID, JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin, UUIDMixin


# 渗透测试状态枚举
class PentestStatus(str):
    """渗透测试任务状态"""
    PENDING = "pending"           # 等待执行
    RUNNING = "running"           # 执行中
    COMPLETED = "completed"       # 执行完成
    FAILED = "failed"             # 执行失败
    CANCELLED = "cancelled"       # 已取消


# 漏洞风险等级枚举
class VulnerabilitySeverity(str):
    """漏洞风险等级"""
    CRITICAL = "Critical"         # 严重
    HIGH = "High"                 # 高危
    MEDIUM = "Medium"             # 中危
    LOW = "Low"                   # 低危
    INFO = "Info"                 # 信息


# 漏洞状态枚举
class VulnerabilityStatus(str):
    """漏洞状态"""
    OPEN = "open"                 # 未修复
    CONFIRMED = "confirmed"       # 已确认
    FIXED = "fixed"               # 已修复
    FALSE_POSITIVE = "false_positive"  # 误报
    ACCEPTED = "accepted"         # 已接受风险


class SecurityTest(Base, UUIDMixin, TimestampMixin):
    """
    安全测试（渗透测试）任务表

    存储 security_agent 生成的渗透测试任务元数据
    """
    __tablename__ = "security_tests"
    __table_args__ = {"comment": "安全测试（渗透测试）任务表"}

    # 基本信息
    project_id: Mapped[UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("projects.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
        comment="所属项目 ID"
    )
    folder_id: Mapped[UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("folders.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
        comment="所属文件夹 ID (可选)"
    )

    # 测试标识
    identifier: Mapped[str] = mapped_column(
        String(50),
        unique=True,
        nullable=False,
        index=True,
        comment="安全测试标识符，如 ST-1001"
    )

    name: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
        comment="安全测试名称"
    )

    target: Mapped[str] = mapped_column(
        String(500),
        nullable=False,
        comment="测试目标（URL/IP/域名）"
    )

    description: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
        comment="测试描述"
    )

    # 状态
    status: Mapped[str] = mapped_column(
        String(50),
        nullable=False,
        default=PentestStatus.PENDING,
        comment="测试状态: pending/running/completed/failed/cancelled"
    )

    # 扫描配置
    scan_config: Mapped[dict | None] = mapped_column(
        JSONB,
        nullable=True,
        comment="扫描配置（JSON）"
    )

    # 统计信息
    total_vulnerabilities: Mapped[int | None] = mapped_column(
        nullable=True,
        default=0,
        comment="漏洞总数"
    )
    critical_count: Mapped[int | None] = mapped_column(
        nullable=True,
        default=0,
        comment="严重漏洞数"
    )
    high_count: Mapped[int | None] = mapped_column(
        nullable=True,
        default=0,
        comment="高危漏洞数"
    )
    medium_count: Mapped[int | None] = mapped_column(
        nullable=True,
        default=0,
        comment="中危漏洞数"
    )
    low_count: Mapped[int | None] = mapped_column(
        nullable=True,
        default=0,
        comment="低危漏洞数"
    )
    info_count: Mapped[int | None] = mapped_column(
        nullable=True,
        default=0,
        comment="信息级漏洞数"
    )

    risk_score: Mapped[float | None] = mapped_column(
        Float,
        nullable=True,
        comment="综合风险评分"
    )

    thread_id: Mapped[str | None] = mapped_column(
        String(100),
        nullable=True,
        comment="关联的 LangGraph 对话线程 ID"
    )

    # 关系
    project: Mapped["Project"] = relationship(
        "Project",
        back_populates="security_tests"
    )
    vulnerabilities: Mapped[list["SecurityVulnerability"]] = relationship(
        "SecurityVulnerability",
        back_populates="security_test",
        cascade="all, delete-orphan"
    )
    reports: Mapped[list["SecurityReport"]] = relationship(
        "SecurityReport",
        back_populates="security_test",
        cascade="all, delete-orphan"
    )

    def __repr__(self) -> str:
        return f"<SecurityTest(id={self.id}, identifier={self.identifier}, name={self.name}, target={self.target})>"


class SecurityVulnerability(Base, UUIDMixin, TimestampMixin):
    """
    安全漏洞发现表

    存储渗透测试过程中发现的漏洞详情
    """
    __tablename__ = "security_vulnerabilities"
    __table_args__ = {"comment": "安全漏洞发现表"}

    # 关联
    security_test_id: Mapped[UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("security_tests.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
        comment="所属安全测试 ID"
    )

    # 漏洞标识
    vuln_id: Mapped[str] = mapped_column(
        String(50),
        nullable=False,
        comment="漏洞编号，如 VL-001"
    )

    title: Mapped[str] = mapped_column(
        String(500),
        nullable=False,
        comment="漏洞标题"
    )

    # 分类
    severity: Mapped[str] = mapped_column(
        String(50),
        nullable=False,
        comment="风险等级: Critical/High/Medium/Low/Info"
    )

    vuln_type: Mapped[str | None] = mapped_column(
        String(100),
        nullable=True,
        comment="漏洞类型，如 SQL Injection, XSS, LFI"
    )

    # 影响范围
    affected_url: Mapped[str | None] = mapped_column(
        String(500),
        nullable=True,
        comment="受影响 URL"
    )

    parameter: Mapped[str | None] = mapped_column(
        String(200),
        nullable=True,
        comment="受影响参数"
    )

    # 详情
    description: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
        comment="漏洞描述"
    )

    reproduction: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
        comment="复现步骤"
    )

    evidence: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
        comment="证据"
    )

    remediation: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
        comment="修复建议"
    )

    cvss_score: Mapped[float | None] = mapped_column(
        Float,
        nullable=True,
        comment="CVSS 评分"
    )

    # 状态
    status: Mapped[str] = mapped_column(
        String(50),
        nullable=False,
        default=VulnerabilityStatus.OPEN,
        comment="漏洞状态: open/confirmed/fixed/false_positive/accepted"
    )

    # 关系
    security_test: Mapped["SecurityTest"] = relationship(
        "SecurityTest",
        back_populates="vulnerabilities"
    )

    def __repr__(self) -> str:
        return f"<SecurityVulnerability(id={self.id}, vuln_id={self.vuln_id}, title={self.title}, severity={self.severity})>"


class SecurityReport(Base, UUIDMixin, TimestampMixin):
    """
    安全测试报告表

    存储生成的渗透测试报告元数据
    """
    __tablename__ = "security_reports"
    __table_args__ = {"comment": "安全测试报告表"}

    # 关联
    security_test_id: Mapped[UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("security_tests.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
        comment="所属安全测试 ID"
    )

    # 报告信息
    name: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
        comment="报告名称"
    )

    report_type: Mapped[str] = mapped_column(
        String(50),
        nullable=False,
        default="full",
        comment="报告类型: full/executive/technical"
    )

    format: Mapped[str] = mapped_column(
        String(50),
        nullable=False,
        default="markdown",
        comment="报告格式: markdown/html/json"
    )

    # 内容
    content: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
        comment="报告内容（完整文本）"
    )

    file_path: Mapped[str | None] = mapped_column(
        String(500),
        nullable=True,
        comment="MinIO 文件路径"
    )

    # 统计
    risk_score: Mapped[float | None] = mapped_column(
        Float,
        nullable=True,
        comment="风险评分"
    )

    summary: Mapped[dict | None] = mapped_column(
        JSONB,
        nullable=True,
        comment="报告摘要数据（JSON）"
    )

    # 关系
    security_test: Mapped["SecurityTest"] = relationship(
        "SecurityTest",
        back_populates="reports"
    )

    def __repr__(self) -> str:
        return f"<SecurityReport(id={self.id}, name={self.name}, type={self.report_type})>"
