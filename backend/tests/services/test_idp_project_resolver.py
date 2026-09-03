"""
IDP 项目映射解析器单元测试

测试项目映射、禁用项目处理
"""

import pytest
from pathlib import Path
from unittest.mock import patch, mock_open

from app.services.idp_project_resolver import IDPProjectResolver, IDPProjectMapping


class TestIDPProjectResolver:
    """IDP 项目映射解析器测试"""

    @pytest.fixture(autouse=True)
    def reset_resolver(self):
        """每个测试前重置解析器状态"""
        IDPProjectResolver._mappings = {}
        IDPProjectResolver._loaded = False
        yield
        IDPProjectResolver._mappings = {}
        IDPProjectResolver._loaded = False

    def test_load_config_success(self):
        """测试成功加载配置"""
        yaml_content = """
projects:
  - source_project_key: "PR-2"
    source_project_name: "小杨生煎"
    idp_project_id: 1169
    idp_project_name: "小杨生煎"
    apply_type: "agile"
    bug_type_id: 12536
    issue_type_id: 3
    type_code: "bug"
    default_priority_id: 2
    default_priority_code: "priority-2"
    default_sprint_id: 4167
    default_epic_id: 859111
    default_assignee_id: 6557
    enabled: true
"""
        with patch("builtins.open", mock_open(read_data=yaml_content)):
            with patch("pathlib.Path.exists", return_value=True):
                mapping = IDPProjectResolver.resolve("PR-2")

        assert mapping is not None
        assert mapping.source_project_key == "PR-2"
        assert mapping.idp_project_id == 1169
        assert mapping.type_code == "bug"
        assert mapping.enabled is True

    def test_resolve_not_found(self):
        """测试未找到项目映射"""
        yaml_content = """
projects:
  - source_project_key: "PR-2"
    source_project_name: "小杨生煎"
    idp_project_id: 1169
    type_code: "bug"
    enabled: true
"""
        with patch("builtins.open", mock_open(read_data=yaml_content)):
            with patch("pathlib.Path.exists", return_value=True):
                mapping = IDPProjectResolver.resolve("PR-999")

        assert mapping is None

    def test_resolve_disabled(self):
        """测试禁用的项目映射"""
        yaml_content = """
projects:
  - source_project_key: "PR-2"
    source_project_name: "小杨生煎"
    idp_project_id: 1169
    type_code: "bug"
    enabled: false
"""
        with patch("builtins.open", mock_open(read_data=yaml_content)):
            with patch("pathlib.Path.exists", return_value=True):
                mapping = IDPProjectResolver.resolve("PR-2")

        assert mapping is None

    def test_resolve_no_config_file(self):
        """测试配置文件不存在"""
        with patch("pathlib.Path.exists", return_value=False):
            mapping = IDPProjectResolver.resolve("PR-2")

        assert mapping is None

    def test_get_all_mappings(self):
        """测试获取所有映射"""
        yaml_content = """
projects:
  - source_project_key: "PR-2"
    source_project_name: "小杨生煎"
    idp_project_id: 1169
    type_code: "bug"
    enabled: true
  - source_project_key: "PR-3"
    source_project_name: "测试项目"
    idp_project_id: 1170
    type_code: "bug"
    enabled: true
"""
        with patch("builtins.open", mock_open(read_data=yaml_content)):
            with patch("pathlib.Path.exists", return_value=True):
                mappings = IDPProjectResolver.get_all_mappings()

        assert len(mappings) == 2
        assert "PR-2" in mappings
        assert "PR-3" in mappings

    def test_reload(self):
        """测试重新加载配置"""
        yaml_content = """
projects:
  - source_project_key: "PR-2"
    source_project_name: "小杨生煎"
    idp_project_id: 1169
    type_code: "bug"
    enabled: true
"""
        with patch("builtins.open", mock_open(read_data=yaml_content)):
            with patch("pathlib.Path.exists", return_value=True):
                # 第一次加载
                mapping1 = IDPProjectResolver.resolve("PR-2")
                assert mapping1 is not None

                # 重新加载
                IDPProjectResolver.reload()
                mapping2 = IDPProjectResolver.resolve("PR-2")
                assert mapping2 is not None
                assert mapping2.idp_project_id == 1169

    def test_mapping_dataclass(self):
        """测试映射数据类"""
        mapping = IDPProjectMapping(
            source_project_key="PR-2",
            source_project_name="小杨生煎",
            idp_project_id=1169,
            idp_project_name="小杨生煎",
            apply_type="agile",
            bug_type_id=12536,
            issue_type_id=3,
            type_code="bug",
            default_priority_id=2,
            default_priority_code="priority-2",
            default_sprint_id=4167,
            default_epic_id=859111,
            default_assignee_id=6557,
            enabled=True,
        )
        assert mapping.source_project_key == "PR-2"
        assert mapping.idp_project_id == 1169
        assert mapping.type_code == "bug"
