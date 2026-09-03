"""
SQLAlchemy 数据库模型模块

定义所有 PostgreSQL 数据库表的 ORM 模型
"""

# fmt: off  MC80OmFIVnBZMlhscm9ua3VMazZVR1puYVE9PTpjOGU1MzFiNw==

from .base import Base, TimestampMixin, UUIDMixin
from .user import User
from .team import Team, ProjectTeam
from .project import Project
from .folder import Folder
from .folder_type import FolderType
from .test_case import TestCase, TestStep, Tag, TestCaseTag
from .test_run import TestRun, TestRunTestCase
from .test_result import TestResult, TestStepResult
from .attachment import Attachment, AttachmentEntityType
from .configuration import Configuration
from .test_plan import TestPlan
from .api_test import APITest, APITestRun, APITestResult
from .api_endpoint import APIEndpoint
from .web_test import WebTest, WebTestRun, WebTestResult
from .web_function import WebFunction, WebSubFunction
from .security_test import SecurityTest, SecurityVulnerability, SecurityReport
from .android_app import AndroidApp, AndroidSubFunction
from .ios_app import IOSApp, IOSSubFunction
from .test_scenario import (
    TestScenario,
    ScenarioStep,
    StepDataMapping,
    ScenarioVariable,
    ScenarioRun,
    ScenarioStepResult,
)
from .idp_defect_record import IDPDefectRecord
from .web_script_registry import WebScriptRegistry
from .knowledge_space import KnowledgeSpace
from .knowledge_document import KnowledgeDocument, DocumentStatus
from .knowledge_chunk import KnowledgeChunk
from .knowledge_retrieval_log import KnowledgeRetrievalLog
# pylint: disable  MS80OmFIVnBZMlhscm9ua3VMazZVR1puYVE9PTpjOGU1MzFiNw==

# 枚举类型从 schemas.enums 导入，避免重复定义
from ..schemas.enums import TestPlanStatus, TestPlanActiveState
# noqa  Mi80OmFIVnBZMlhscm9ua3VMazZVR1puYVE9PTpjOGU1MzFiNw==

__all__ = [
    "Base",
    "TimestampMixin",
    "UUIDMixin",
    "User",
    "Team",
    "ProjectTeam",
    "Project",
    "Folder",
    "FolderType",
    "TestCase",
    "TestStep",
    "Tag",
    "TestCaseTag",
    "TestRun",
    "TestRunTestCase",
    "TestResult",
    "TestStepResult",
    "Attachment",
    "AttachmentEntityType",
    "Configuration",
    "TestPlan",
    "TestPlanStatus",
    "TestPlanActiveState",
    "APITest",
    "APITestRun",
    "APITestResult",
    "APIEndpoint",
    "WebTest",
    "WebTestRun",
    "WebTestResult",
    "WebFunction",
    "WebSubFunction",
    "SecurityTest",
    "SecurityVulnerability",
    "SecurityReport",
    "AndroidApp",
    "AndroidSubFunction",
    "IOSApp",
    "IOSSubFunction",
    "TestScenario",
    "ScenarioStep",
    "StepDataMapping",
    "ScenarioVariable",
    "ScenarioRun",
    "ScenarioStepResult",
    "IDPDefectRecord",
    "WebScriptRegistry",
    "KnowledgeSpace",
    "KnowledgeDocument",
    "DocumentStatus",
    "KnowledgeChunk",
    "KnowledgeRetrievalLog",
]

# noqa  My80OmFIVnBZMlhscm9ua3VMazZVR1puYVE9PTpjOGU1MzFiNw==
