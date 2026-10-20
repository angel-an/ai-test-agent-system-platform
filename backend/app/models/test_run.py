"""
测试运行模型

定义测试运行及其关联测试用例的表结构
参考: https://www.browserstack.com/docs/test-management/api-reference/test-runs
"""

from datetime import datetime

from sqlalchemy import ForeignKey, Integer, String, Text, Enum as SQLEnum
from sqlalchemy.dialects.postgresql import UUID, JSONB, ARRAY
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin, UUIDMixin
from app.schemas.enums import (
    TestRunState, TestRunActiveState, TestResultStatus,
    ScriptType, ExecutionMode, TriggerType, JobStatus,
)
# fmt: off  MC80OmFIVnBZMlhscm9ua3VMazZlazVMVEE9PTowMTc3ZWY0YQ==

class TestRun(Base, UUIDMixin, TimestampMixin):
    """
    测试运行表

    存储测试运行的基本信息
    """
    __tablename__ = "test_runs"
    __table_args__ = {"comment": "测试运行表"}
# noqa  MS80OmFIVnBZMlhscm9ua3VMazZlazVMVEE9PTowMTc3ZWY0YQ==

    project_id: Mapped[UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("projects.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
        comment="所属项目 ID"
    )
    identifier: Mapped[str] = mapped_column(
        String(50),
        unique=True,
        nullable=False,
        index=True,
        comment="测试运行标识符，如 TR-123"
    )
    name: Mapped[str] = mapped_column(
        String(500),
        nullable=False,
        comment="测试运行名称"
    )
    description: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
        comment="测试运行描述"
    )
    run_state: Mapped[TestRunState] = mapped_column(
        SQLEnum(TestRunState),
        default=TestRunState.NEW_RUN,
        nullable=False,
        comment="运行状态"
    )
    active_state: Mapped[TestRunActiveState] = mapped_column(
        SQLEnum(TestRunActiveState),
        default=TestRunActiveState.ACTIVE,
        nullable=False,
        comment="活跃状态"
    )
    assignee: Mapped[str | None] = mapped_column(
        String(255),
        nullable=True,
        comment="负责人邮箱"
    )
    test_plan_id: Mapped[UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("test_plans.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
        comment="关联的测试计划 ID"
    )
    tags: Mapped[list | None] = mapped_column(
        JSONB,
        nullable=True,
        default=list,
        comment="标签列表"
    )
    issues: Mapped[list | None] = mapped_column(
        JSONB,
        nullable=True,
        default=list,
        comment="关联的问题列表"
    )
    configurations: Mapped[list | None] = mapped_column(
        JSONB,
        nullable=True,
        default=list,
        comment="配置 ID 列表"
    )
    # 统计字段 - 冗余存储以提高查询性能
    test_cases_count: Mapped[int] = mapped_column(
        Integer,
        default=0,
        comment="测试用例总数"
    )
    passed_count: Mapped[int] = mapped_column(
        Integer,
        default=0,
        comment="通过数量"
    )
    failed_count: Mapped[int] = mapped_column(
        Integer,
        default=0,
        comment="失败数量"
    )
    skipped_count: Mapped[int] = mapped_column(
        Integer,
        default=0,
        comment="跳过数量"
    )
    blocked_count: Mapped[int] = mapped_column(
        Integer,
        default=0,
        comment="阻塞数量"
    )
    not_executed_count: Mapped[int] = mapped_column(
        Integer,
        default=0,
        comment="未执行数量"
    )

    # ---- R5（2026-10-17）：断言指标模型，run 级只存聚合数，明细走 /assertion-ledger ----
    # 两类**分别计数、分别算通过率，不得合并**（共享一个 passed_count 无法给出
    # "用例通过率"与"配置检查通过率"两个口径）：
    # - case_attributed：检查带 case_id/owner，是**用例结论**的唯一依据；
    # - config_only：来自定位器配置（无对应用例断言），只反映配置声明与页面是否一致。
    # 另有 unattributed（既无归属、又非配置驱动）——只告警，不计入任何通过率。
    case_attributed_total: Mapped[int] = mapped_column(
        Integer, default=0, comment="归属用例的检查数（去重后的检查口径）"
    )
    case_attributed_pass: Mapped[int] = mapped_column(Integer, default=0, comment="归属用例的检查通过数")
    case_attributed_fail: Mapped[int] = mapped_column(Integer, default=0, comment="归属用例的检查失败数")
    case_attributed_unverified: Mapped[int] = mapped_column(Integer, default=0, comment="归属用例的检查未验证数")
    case_attributed_blocked: Mapped[int] = mapped_column(Integer, default=0, comment="归属用例的检查阻塞数")
    config_only_total: Mapped[int] = mapped_column(
        Integer, default=0, comment="配置驱动检查数（无对应用例断言）"
    )
    config_only_pass: Mapped[int] = mapped_column(Integer, default=0, comment="配置驱动检查通过数")
    config_only_fail: Mapped[int] = mapped_column(Integer, default=0, comment="配置驱动检查失败数")
    config_only_unverified: Mapped[int] = mapped_column(Integer, default=0, comment="配置驱动检查未验证数")
    config_only_blocked: Mapped[int] = mapped_column(Integer, default=0, comment="配置驱动检查阻塞数")
    unattributed_checks: Mapped[int] = mapped_column(
        Integer, default=0, comment="未归属检查数（只告警，不计入任何通过率）"
    )

    # 执行配置字段（来自 0002 迁移）
    execution_mode: Mapped[ExecutionMode] = mapped_column(
        SQLEnum(ExecutionMode, name="executionmode", values_callable=lambda x: [e.value for e in x]),
        default=ExecutionMode.SEQUENTIAL,
        nullable=False,
        comment="执行模式"
    )
    max_concurrency: Mapped[int] = mapped_column(
        Integer,
        default=5,
        nullable=False,
        comment="最大并发数"
    )
    trigger_type: Mapped[TriggerType] = mapped_column(
        SQLEnum(TriggerType, name="triggertype", values_callable=lambda x: [e.value for e in x]),
        default=TriggerType.MANUAL,
        nullable=False,
        comment="触发类型"
    )
    scheduled_by: Mapped[UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("scheduled_runs.id", ondelete="SET NULL", use_alter=True),
        nullable=True,
        index=True,
        comment="调度来源 ID"
    )

    # 关系
    project: Mapped["Project"] = relationship("Project", back_populates="test_runs")
    test_plan: Mapped["TestPlan"] = relationship(
        "TestPlan",
        back_populates="test_runs",
        foreign_keys=[test_plan_id]
    )
    test_run_cases: Mapped[list["TestRunTestCase"]] = relationship(
        "TestRunTestCase",
        back_populates="test_run",
        cascade="all, delete-orphan"
    )
    script_jobs: Mapped[list["TestRunScriptJob"]] = relationship(
        "TestRunScriptJob",
        back_populates="test_run",
        cascade="all, delete-orphan"
    )

    def __repr__(self) -> str:
        return f"<TestRun(id={self.id}, identifier={self.identifier}, name={self.name})>"

class TestRunTestCase(Base, UUIDMixin, TimestampMixin):
    """
    测试运行与测试用例关联表

    存储测试运行中包含的测试用例及其执行状态
    """
    __tablename__ = "test_run_test_cases"
    __table_args__ = {"comment": "测试运行测试用例关联表"}
# type: ignore  Mi80OmFIVnBZMlhscm9ua3VMazZlazVMVEE9PTowMTc3ZWY0YQ==

    test_run_id: Mapped[UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("test_runs.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
        comment="测试运行 ID"
    )
    test_case_id: Mapped[UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("test_cases.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
        comment="测试用例 ID"
    )
    configuration_id: Mapped[int | None] = mapped_column(
        Integer,
        nullable=True,
        comment="配置 ID"
    )
    assignee: Mapped[str | None] = mapped_column(
        String(255),
        nullable=True,
        comment="负责人邮箱"
    )
    latest_status: Mapped[TestResultStatus] = mapped_column(
        SQLEnum(TestResultStatus),
        default=TestResultStatus.NOT_EXECUTED,
        nullable=False,
        comment="最新测试结果状态"
    )
    latest_result_id: Mapped[UUID | None] = mapped_column(
        UUID(as_uuid=True),
        nullable=True,
        comment="最新测试结果 ID"
    )
    # ---- N2（2026-10-17）：写入来源与轮次，供"唯一写入者 + 旧作业不得覆盖新轮次"判定 ----
    # 迁移前的行（以及非作业路径创建的行）保持 -1/NULL，视作"无轮次信息"，不阻断写入。
    latest_job_id: Mapped[UUID | None] = mapped_column(
        UUID(as_uuid=True),
        nullable=True,
        comment="最后写入该行结论的脚本作业 ID"
    )
    latest_execution_order: Mapped[int] = mapped_column(
        Integer,
        default=-1,
        nullable=False,
        server_default="-1",
        comment="最后写入的作业执行顺序（-1=无轮次信息）"
    )
    latest_retry_count: Mapped[int] = mapped_column(
        Integer,
        default=0,
        nullable=False,
        server_default="0",
        comment="最后写入的作业重试次数"
    )
    latest_writer: Mapped[str | None] = mapped_column(
        String(20),
        nullable=True,
        comment="最后写入者：mapper/engine/finalize/manual"
    )

    # 关系
    test_run: Mapped["TestRun"] = relationship(
        "TestRun",
        back_populates="test_run_cases"
    )
    test_case: Mapped["TestCase"] = relationship(
        "TestCase",
        back_populates="test_run_cases"
    )
# pylint: disable  My80OmFIVnBZMlhscm9ua3VMazZlazVMVEE9PTowMTc3ZWY0YQ==

    def __repr__(self) -> str:
        return f"<TestRunTestCase(test_run_id={self.test_run_id}, test_case_id={self.test_case_id})>"


class TestRunScriptJob(Base, UUIDMixin, TimestampMixin):
    """
    测试运行脚本作业表

    统一脚本作业表，协调测试运行中所有脚本作业的执行
    """
    __tablename__ = "test_run_script_jobs"
    __table_args__ = {"comment": "测试运行脚本作业表"}

    test_run_id: Mapped[UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("test_runs.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
        comment="测试运行 ID"
    )
    script_type: Mapped[ScriptType] = mapped_column(
        SQLEnum(ScriptType, name="scripttype", values_callable=lambda x: [e.value for e in x]),
        nullable=False,
        comment="脚本类型"
    )
    script_id: Mapped[UUID] = mapped_column(
        UUID(as_uuid=True),
        nullable=False,
        index=True,
        comment="脚本 ID"
    )
    script_identifier: Mapped[str] = mapped_column(
        String(50),
        nullable=False,
        comment="脚本标识符"
    )
    script_name: Mapped[str | None] = mapped_column(
        String(500),
        nullable=True,
        comment="脚本名称"
    )
    execution_order: Mapped[int] = mapped_column(
        Integer,
        default=0,
        nullable=False,
        comment="执行顺序"
    )
    execution_mode: Mapped[ExecutionMode] = mapped_column(
        SQLEnum(ExecutionMode, name="executionmode", values_callable=lambda x: [e.value for e in x]),
        default=ExecutionMode.SEQUENTIAL,
        nullable=False,
        comment="执行模式"
    )
    status: Mapped[JobStatus] = mapped_column(
        SQLEnum(JobStatus, name="jobstatus", values_callable=lambda x: [e.value for e in x]),
        default=JobStatus.PENDING,
        nullable=False,
        comment="作业状态"
    )
    started_at: Mapped[datetime | None] = mapped_column(
        nullable=True,
        comment="开始时间"
    )
    completed_at: Mapped[datetime | None] = mapped_column(
        nullable=True,
        comment="完成时间"
    )
    duration_ms: Mapped[int | None] = mapped_column(
        Integer,
        nullable=True,
        comment="执行耗时（毫秒）"
    )
    result_summary: Mapped[dict | None] = mapped_column(
        JSONB,
        nullable=True,
        comment="结果摘要"
    )
    error_message: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
        comment="错误信息"
    )
    stdout: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
        comment="标准输出日志"
    )
    stderr: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
        comment="标准错误日志"
    )
    report_path: Mapped[str | None] = mapped_column(
        String(2048),
        nullable=True,
        comment="报告路径"
    )
    retry_count: Mapped[int] = mapped_column(
        Integer,
        default=0,
        nullable=False,
        comment="重试次数"
    )
    max_retries: Mapped[int] = mapped_column(
        Integer,
        default=0,
        nullable=False,
        comment="最大重试次数"
    )
    execution_config: Mapped[dict | None] = mapped_column(
        JSONB,
        nullable=True,
        comment="执行配置"
    )

    # 关系
    test_run: Mapped["TestRun"] = relationship(
        "TestRun",
        back_populates="script_jobs"
    )

    def __repr__(self) -> str:
        return f"<TestRunScriptJob(id={self.id}, script_type={self.script_type}, status={self.status})>"

