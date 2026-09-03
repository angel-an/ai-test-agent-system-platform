"""
IDP 项目映射解析器

MVP 实现：
- 加载 YAML 配置文件
- 精确匹配 source_project_key → idp_project_id
- 验证项目是否启用
- 严禁自动使用默认项目兜底
"""

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import yaml

from app.config.settings import settings

logger = logging.getLogger(__name__)


@dataclass
class IDPProjectMapping:
    """IDP 项目映射配置"""

    source_project_key: str
    source_project_name: str
    idp_project_id: int
    idp_project_name: str
    apply_type: str
    bug_type_id: int
    issue_type_id: int
    type_code: str  # 联调确认必填：bug="bug"
    default_priority_id: int
    default_priority_code: str
    default_sprint_id: int
    default_epic_id: int
    default_assignee_id: int
    enabled: bool


class IDPProjectResolver:
    """
    IDP 项目映射解析器

    MVP 只实现精确匹配，不做模糊关键词自动选择
    """

    _mappings: dict[str, IDPProjectMapping] = {}
    _loaded: bool = False

    @classmethod
    def _load_config(cls) -> None:
        """加载项目映射配置文件"""
        if cls._loaded:
            return

        config_path = Path(settings.idp_project_mapping_path)

        if not config_path.exists():
            logger.warning(
                "[IDPProjectResolver] 项目映射配置文件不存在: %s",
                config_path,
            )
            cls._loaded = True
            return

        try:
            with open(config_path, "r", encoding="utf-8") as f:
                config = yaml.safe_load(f)

            projects = config.get("projects", [])
            for project in projects:
                mapping = IDPProjectMapping(
                    source_project_key=project.get("source_project_key", ""),
                    source_project_name=project.get("source_project_name", ""),
                    idp_project_id=project.get("idp_project_id", 0),
                    idp_project_name=project.get("idp_project_name", ""),
                    apply_type=project.get("apply_type", "agile"),
                    bug_type_id=project.get("bug_type_id", 0),
                    issue_type_id=project.get("issue_type_id", 0),
                    type_code=project.get("type_code", "bug"),
                    default_priority_id=project.get("default_priority_id", 2),
                    default_priority_code=project.get("default_priority_code", "priority-2"),
                    default_sprint_id=project.get("default_sprint_id", 0),
                    default_epic_id=project.get("default_epic_id", 0),
                    default_assignee_id=project.get("default_assignee_id", 0),
                    enabled=project.get("enabled", True),
                )
                cls._mappings[mapping.source_project_key] = mapping

            logger.info(
                "[IDPProjectResolver] 已加载 %s 个项目映射",
                len(cls._mappings),
            )
            cls._loaded = True

        except Exception as e:
            logger.error("[IDPProjectResolver] 加载配置文件失败: %s", e)
            cls._loaded = True

    @classmethod
    def resolve(cls, source_project_key: str) -> Optional[IDPProjectMapping]:
        """
        解析本地项目对应的 IDP 项目

        Args:
            source_project_key: 本地项目标识符，如 "PR-2"

        Returns:
            Optional[IDPProjectMapping]: 映射配置，未找到或禁用时返回 None
        """
        cls._load_config()

        mapping = cls._mappings.get(source_project_key)

        if not mapping:
            logger.warning(
                "[IDPProjectResolver] 未找到项目映射: %s",
                source_project_key,
            )
            return None

        if not mapping.enabled:
            logger.warning(
                "[IDPProjectResolver] 项目映射已禁用: %s",
                source_project_key,
            )
            return None

        logger.info(
            "[IDPProjectResolver] 项目映射成功: %s → %s (IDP ID: %s)",
            source_project_key,
            mapping.idp_project_name,
            mapping.idp_project_id,
        )
        return mapping

    @classmethod
    def get_all_mappings(cls) -> dict[str, IDPProjectMapping]:
        """获取所有项目映射（用于管理）"""
        cls._load_config()
        return dict(cls._mappings)

    @classmethod
    def reload(cls) -> None:
        """重新加载配置"""
        cls._mappings = {}
        cls._loaded = False
        cls._load_config()
