"""
测试命名冲突检查脚本。
"""

import sys
import tempfile
from pathlib import Path

import pytest

# 将脚本目录加入路径
sys.path.insert(0, str(Path(__file__).parent.parent.parent / "scripts"))

from check_stdlib_conflicts import (
    STDLIB_MODULES,
    DEFAULT_EXCLUDE_DIRS,
    check_directory,
    has_conflicts,
    format_report,
)


class TestStdlibModules:
    """测试标准库模块集合。"""

    def test_platform_in_stdlib(self):
        """platform 必须在标准库集合中（这是本次问题的根源）。"""
        assert "platform" in STDLIB_MODULES

    def test_config_not_in_stdlib(self):
        """config 不是标准库名，不应在集合中。"""
        assert "config" not in STDLIB_MODULES

    def test_stdlib_is_frozenset(self):
        """标准库集合应为不可变集合，防止运行时修改。"""
        assert isinstance(STDLIB_MODULES, frozenset)


class TestCheckDirectory:
    """测试目录扫描逻辑。"""

    def test_empty_directory(self):
        """空目录应返回无冲突。"""
        with tempfile.TemporaryDirectory() as tmpdir:
            result = check_directory(Path(tmpdir))
            assert not has_conflicts(result)
            assert result == {
                "stdlib_package": [],
                "stdlib_module": [],
                "reserved_package": [],
                "reserved_module": [],
            }

    def test_stdlib_package_detected(self):
        """应检测到与标准库同名的包目录。"""
        with tempfile.TemporaryDirectory() as tmpdir:
            (Path(tmpdir) / "platform").mkdir()
            (Path(tmpdir) / "platform" / "__init__.py").write_text("")
            result = check_directory(Path(tmpdir))
            assert has_conflicts(result)
            assert result["stdlib_package"] == [("platform", "platform")]
            assert result["stdlib_module"] == []

    def test_stdlib_module_detected(self):
        """应检测到与标准库同名的 .py 文件。"""
        with tempfile.TemporaryDirectory() as tmpdir:
            (Path(tmpdir) / "json.py").write_text("")
            result = check_directory(Path(tmpdir))
            assert has_conflicts(result)
            assert result["stdlib_module"] == [("json.py", "json")]

    def test_reserved_name_detected(self):
        """应检测到项目保留名。"""
        with tempfile.TemporaryDirectory() as tmpdir:
            (Path(tmpdir) / "myapp").mkdir()
            (Path(tmpdir) / "myapp" / "config").mkdir()
            (Path(tmpdir) / "myapp" / "config" / "__init__.py").write_text("")
            result = check_directory(
                Path(tmpdir),
                reserve_names=frozenset({"config"}),
            )
            assert has_conflicts(result)
            assert result["reserved_package"] == [("myapp/config", "config")]
            assert result["stdlib_package"] == []  # config 不是标准库

    def test_exclude_dirs(self):
        """应正确排除指定目录。"""
        with tempfile.TemporaryDirectory() as tmpdir:
            (Path(tmpdir) / ".venv").mkdir()
            (Path(tmpdir) / ".venv" / "json.py").write_text("")
            result = check_directory(Path(tmpdir))
            assert not has_conflicts(result)  # .venv 被排除

    def test_nested_stdlib_package(self):
        """应检测到嵌套路径中的标准库包名冲突。"""
        with tempfile.TemporaryDirectory() as tmpdir:
            (Path(tmpdir) / "app" / "platform").mkdir(parents=True)
            (Path(tmpdir) / "app" / "platform" / "__init__.py").write_text("")
            result = check_directory(Path(tmpdir))
            assert has_conflicts(result)
            assert result["stdlib_package"] == [("app/platform", "platform")]

    def test_no_false_positive_for_normal_names(self):
        """正常业务名不应被误报。"""
        with tempfile.TemporaryDirectory() as tmpdir:
            (Path(tmpdir) / "services").mkdir()
            (Path(tmpdir) / "services" / "user_service.py").write_text("")
            result = check_directory(Path(tmpdir))
            assert not has_conflicts(result)

    def test_non_py_files_ignored(self):
        """非 .py 文件应被忽略。"""
        with tempfile.TemporaryDirectory() as tmpdir:
            (Path(tmpdir) / "json.txt").write_text("")
            (Path(tmpdir) / "platform.js").write_text("")
            result = check_directory(Path(tmpdir))
            assert not has_conflicts(result)


class TestFormatReport:
    """测试报告格式化。"""

    def test_ok_report(self):
        """无冲突时应输出 OK。"""
        result = {
            "stdlib_package": [],
            "stdlib_module": [],
            "reserved_package": [],
            "reserved_module": [],
        }
        report = format_report(result, Path("/tmp/test"))
        assert "[OK]" in report
        assert "[FAIL]" not in report

    def test_fail_report_includes_paths(self):
        """有冲突时报告应包含冲突路径。"""
        result = {
            "stdlib_package": [("app/platform", "platform")],
            "stdlib_module": [],
            "reserved_package": [],
            "reserved_module": [],
        }
        report = format_report(result, Path("/tmp/test"))
        assert "[FAIL]" in report
        assert "app/platform" in report
        assert "platform" in report


class TestDefaultExcludeDirs:
    """测试默认排除目录。"""

    def test_common_excludes_present(self):
        """默认排除目录应包含常见缓存和虚拟环境。"""
        assert ".venv" in DEFAULT_EXCLUDE_DIRS
        assert "__pycache__" in DEFAULT_EXCLUDE_DIRS
        assert ".git" in DEFAULT_EXCLUDE_DIRS
        assert "node_modules" in DEFAULT_EXCLUDE_DIRS

    def test_exclude_dirs_is_frozenset(self):
        """排除目录集合应为不可变集合。"""
        assert isinstance(DEFAULT_EXCLUDE_DIRS, frozenset)
