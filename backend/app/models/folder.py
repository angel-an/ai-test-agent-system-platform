"""
文件夹模型

定义文件夹表结构，支持层级结构
"""

# pylint: disable  MC80OmFIVnBZMlhscm9ua3VMazZUMFpYWkE9PTpjOWNkY2MxOA==

from sqlalchemy import Enum, ForeignKey, String, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin, UUIDMixin
from app.models.folder_type import FolderType
# type: ignore  MS80OmFIVnBZMlhscm9ua3VMazZUMFpYWkE9PTpjOWNkY2MxOA==

class Folder(Base, UUIDMixin, TimestampMixin):
    """
    文件夹表

    存储测试用例和API测试文件夹信息，支持无限层级嵌套
    """
    __tablename__ = "folders"
    __table_args__ = {"comment": "文件夹表"}

    project_id: Mapped[UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("projects.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
        comment="所属项目 ID"
    )
    parent_id: Mapped[UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("folders.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
        comment="父文件夹 ID，为空表示根文件夹"
    )
    name: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
        comment="文件夹名称"
    )
    description: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
        comment="文件夹描述"
    )
    folder_type: Mapped[FolderType] = mapped_column(
        Enum(
            FolderType,
            name="foldertype",
            create_type=False,
            # Use enum values instead of names for database storage
            values_callable=lambda obj: [e.value for e in obj]
        ),
        default=FolderType.TEST_CASE,
        nullable=False,
        comment="文件夹类型：test_case 或 api_test"
    )
# noqa  Mi80OmFIVnBZMlhscm9ua3VMazZUMFpYWkE9PTpjOWNkY2MxOA==
    
    # 关系
    project: Mapped["Project"] = relationship(
        "Project",
        back_populates="folders"
    )
    parent: Mapped["Folder | None"] = relationship(
        "Folder",
        back_populates="children",
        remote_side="Folder.id"
    )
    children: Mapped[list["Folder"]] = relationship(
        "Folder",
        back_populates="parent",
        cascade="all, delete-orphan"
    )
    test_cases: Mapped[list["TestCase"]] = relationship(
        "TestCase",
        back_populates="folder",
        cascade="all, delete-orphan"
    )
    api_tests: Mapped[list["APITest"]] = relationship(
        "APITest",
        back_populates="folder",
        cascade="all, delete-orphan"
    )
    api_endpoints: Mapped[list["APIEndpoint"]] = relationship(
        "APIEndpoint",
        back_populates="folder",
        cascade="all, delete-orphan"
    )
    web_tests: Mapped[list["WebTest"]] = relationship(
        "WebTest",
        back_populates="folder",
        cascade="all, delete-orphan"
    )
    web_functions: Mapped[list["WebFunction"]] = relationship(
        "WebFunction",
        back_populates="folder",
        cascade="all, delete-orphan"
    )
    web_sub_functions: Mapped[list["WebSubFunction"]] = relationship(
        "WebSubFunction",
        back_populates="folder",
        cascade="all, delete-orphan"
    )
    android_apps: Mapped[list["AndroidApp"]] = relationship(
        "AndroidApp",
        back_populates="folder",
        cascade="all, delete-orphan"
    )
    android_sub_functions: Mapped[list["AndroidSubFunction"]] = relationship(
        "AndroidSubFunction",
        back_populates="folder",
        cascade="all, delete-orphan"
    )
    ios_apps: Mapped[list["IOSApp"]] = relationship(
        "IOSApp",
        back_populates="folder",
        cascade="all, delete-orphan"
    )
    ios_sub_functions: Mapped[list["IOSSubFunction"]] = relationship(
        "IOSSubFunction",
        back_populates="folder",
        cascade="all, delete-orphan"
    )

    def __repr__(self) -> str:
        return f"<Folder(id={self.id}, name={self.name}, folder_type={self.folder_type}, parent_id={self.parent_id})>"

# noqa  My80OmFIVnBZMlhscm9ua3VMazZUMFpYWkE9PTpjOWNkY2MxOA==
