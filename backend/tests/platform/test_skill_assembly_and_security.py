# -*- coding: utf-8 -*-
"""P0 修复的回归测试：OpenAPI security 三级回退合并 + skill 装配自检。

这两个修复的共同点是**失败时静默**：
- security 只读 method 级时，"全局声明鉴权"的契约落成 None，被当成公开接口；
- skill 根目录解析错时，Agent 照常启动但 0 个 skill。
因此必须有测试兜住，不能只靠人工发现。

注：不使用 pytest 的 ``tmp_path``，改为在测试目录内自建/清理临时树。
``tmp_path`` 落在系统 TEMP 下，在 TEMP 受限的环境（CI 沙箱、受限容器）会
``PermissionError`` 而在 setup 阶段整片报错，与被测逻辑无关。
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from app.config.skill_assembly_check import (
    SKILLS_ROUTE_PREFIX,
    SkillAssemblyReport,
    SkillSourceSpec,
    check_skill_assembly,
    check_source,
    list_skills,
    resolve_source_dir,
    verify_skill_assembly,
)
from app.services.openapi_parser import OpenAPIParser


@pytest.fixture
def skill_tree():
    """自建临时目录树，不依赖系统 TEMP。"""
    base = Path(__file__).parent / "_skill_assembly_tmp"
    shutil.rmtree(base, ignore_errors=True)
    base.mkdir(parents=True, exist_ok=True)
    try:
        yield base
    finally:
        shutil.rmtree(base, ignore_errors=True)


def _write_skill(directory: Path, name: str) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "SKILL.md").write_text(f"---\nname: {name}\n---\n", encoding="utf-8")


# ---------------------------------------------------------------------------
# 1. OpenAPI security 三级回退合并（P0-3）
# ---------------------------------------------------------------------------

class TestSecurityResolution:
    @pytest.mark.parametrize(
        "method,path,global_,expect,source",
        [
            (None, None, None, None, "none"),
            (None, None, [{"bearerAuth": []}], [{"bearerAuth": []}], "global"),
            (None, [{"apiKey": []}], [{"bearerAuth": []}], [{"apiKey": []}], "path"),
            ([{"oauth": []}], [{"apiKey": []}], [{"bearerAuth": []}], [{"oauth": []}], "method"),
            # 显式空数组 = 显式声明无需鉴权，不得被上层声明覆盖
            ([], [{"apiKey": []}], [{"bearerAuth": []}], [], "method"),
            (None, [], [{"bearerAuth": []}], [], "path"),
        ],
    )
    def test_resolve_security(self, method, path, global_, expect, source):
        resolved, src = OpenAPIParser._resolve_security(method, path, global_)
        assert resolved == expect
        assert src == source

    def _group(self, paths, global_security=None):
        parser = OpenAPIParser.__new__(OpenAPIParser)  # 不触发 DB 初始化
        return parser._group_endpoints_by_tag(paths, global_security)

    def test_global_security_reaches_protected_endpoint(self):
        """回归点：全局声明鉴权时，端点 security 不能是 None。"""
        grouped = self._group(
            {"/pets": {"get": {"tags": ["pets"], "responses": {"200": {}}}}},
            [{"bearerAuth": []}],
        )
        ep = grouped["pets"][0]
        assert ep["security"] == [{"bearerAuth": []}]
        assert ep["security_source"] == "global"

    def test_method_level_empty_overrides_global(self):
        grouped = self._group(
            {
                "/pets": {
                    "get": {"tags": ["pets"], "responses": {"200": {}}},
                    "post": {"tags": ["pets"], "security": [], "responses": {"201": {}}},
                }
            },
            [{"bearerAuth": []}],
        )
        by_method = {e["method"]: e for e in grouped["pets"]}
        assert by_method["POST"]["security"] == []
        assert by_method["POST"]["security_source"] == "method"

    def test_path_level_security_used_when_method_silent(self):
        grouped = self._group(
            {
                "/pets": {
                    "security": [{"apiKey": []}],
                    "get": {"tags": ["pets"], "responses": {"200": {}}},
                }
            },
            [{"bearerAuth": []}],
        )
        assert grouped["pets"][0]["security"] == [{"apiKey": []}]
        assert grouped["pets"][0]["security_source"] == "path"

    def test_undeclared_security_reported_as_none_source(self):
        grouped = self._group({"/public": {"get": {"responses": {"200": {}}}}}, None)
        # 无 tags 时按 "Other" 分组
        ep = grouped["Other"][0]
        assert ep["security"] is None
        assert ep["security_source"] == "none"

    def test_non_dict_path_item_tolerated(self):
        assert self._group({"/bad": "not-a-dict"}, None) == {}

    def test_non_dict_method_spec_tolerated(self):
        grouped = self._group({"/x": {"get": "oops", "description": "meta"}}, None)
        assert grouped == {}


# ---------------------------------------------------------------------------
# 2. skill 装配自检（P0-4）
# ---------------------------------------------------------------------------

class TestResolveSourceDir:
    def test_root_source_maps_to_skills_root(self, skill_tree: Path):
        assert resolve_source_dir(skill_tree, f"{SKILLS_ROUTE_PREFIX}/") == skill_tree.resolve()

    def test_named_source_appends_subdir(self, skill_tree: Path):
        assert resolve_source_dir(skill_tree, "/skills/api/") == (skill_tree / "api").resolve()

    def test_source_without_legacy_prefix_is_relative(self, skill_tree: Path):
        assert resolve_source_dir(skill_tree, "api") == (skill_tree / "api").resolve()


class TestListSkills:
    def test_only_dirs_with_skill_md_counted(self, skill_tree: Path):
        _write_skill(skill_tree / "a", "a")
        (skill_tree / "b").mkdir()  # 无 SKILL.md，不算
        (skill_tree / "c.md").write_text("x", encoding="utf-8")
        assert list_skills(skill_tree) == ("a",)

    def test_missing_dir_returns_empty(self, skill_tree: Path):
        assert list_skills(skill_tree / "nope") == ()


class TestCheckSource:
    def test_ok_when_required_present(self, skill_tree: Path):
        _write_skill(skill_tree / "api" / "quality-review", "quality-review")

        spec = SkillSourceSpec("testcase", str(skill_tree), ("/skills/api/",), ("quality-review",))
        result = check_source(spec, "/skills/api/")
        assert result.ok is True
        assert result.loaded == ("quality-review",)
        assert result.problem is None

    def test_missing_required_is_failure(self, skill_tree: Path):
        _write_skill(skill_tree / "api" / "other", "other")

        spec = SkillSourceSpec("testcase", str(skill_tree), ("/skills/api/",), ("quality-review",))
        result = check_source(spec, "/skills/api/")
        assert result.ok is False
        assert "quality-review" in (result.problem or "")

    def test_missing_root_reports_reason(self, skill_tree: Path):
        spec = SkillSourceSpec("x", str(skill_tree / "missing"), ("/skills/",))
        result = check_source(spec, "/skills/")
        assert result.ok is False
        assert "skills_root 不存在" in (result.problem or "")

    def test_empty_source_reports_reason(self, skill_tree: Path):
        (skill_tree / "empty").mkdir(parents=True)
        spec = SkillSourceSpec("x", str(skill_tree), ("/skills/empty/",))
        result = check_source(spec, "/skills/empty/")
        assert result.ok is False
        assert "没有任何含 SKILL.md" in (result.problem or "")

    def test_report_serialization(self, skill_tree: Path):
        spec = SkillSourceSpec("x", str(skill_tree / "missing"), ("/skills/",))
        report = check_skill_assembly([spec])
        wrapped = SkillAssemblyReport(results=report)
        assert wrapped.ok is False
        assert len(wrapped.failures) == 1
        assert wrapped.to_dict()["failed"] == 1
        assert "Skill 装配自检" in wrapped.to_markdown()


# ---------------------------------------------------------------------------
# 3. 对本仓库真实装配的守护
# ---------------------------------------------------------------------------

class TestRealRepositoryAssembly:
    """真实配置守护：这类失效会直接削弱线上 Agent 能力，必须能在此处暴露。"""

    def test_all_sources_assemble(self):
        report = verify_skill_assembly()
        assert report.ok, "\n".join(
            f"{r.agent} {r.source}: {r.problem}" for r in report.failures
        )

    def test_api_agent_has_coverage_review_skill(self):
        report = verify_skill_assembly()
        api = next(r for r in report.results if r.agent == "api")
        assert "api-coverage-review" in api.loaded

    def test_testcase_agent_has_quality_review_skill(self):
        report = verify_skill_assembly()
        tc = next(r for r in report.results if r.agent == "testcase")
        assert "quality-review" in tc.loaded
        assert "sit-testcase-generation-v2" in tc.loaded

    def test_skills_roots_are_absolute(self):
        """相对路径的 skills_root 会随 CWD 漂移，必须已被锚定成绝对路径。"""
        from app.config.settings import settings

        for name in ("api_skills_root", "testcase_skills_root", "security_skills_root"):
            value = getattr(settings, name)
            assert Path(value).is_absolute(), f"{name} 仍是相对路径: {value}"


# ---------------------------------------------------------------------------
# 4. Skill 维护目录约定（单一目录 + 单一接线写法）
# ---------------------------------------------------------------------------

REPO_ROOT = Path(__file__).resolve().parents[3]
AGENTS_DIR = REPO_ROOT / "backend" / "app" / "agents"
CANONICAL_SKILLS_ROOT = REPO_ROOT / ".claude" / "skills"

#: 各 agent 的 skills 根配置项 → 期望的 source 子目录
EXPECTED_AGENT_SOURCES = {
    "api": ("api_skills_root", "/skills/api/"),
    "testcase": ("testcase_skills_root", "/skills/testcase/"),
    "testcase-reference": ("testcase_skills_root", "/skills/testcase/"),
    "security": ("security_skills_root", "/skills/security/"),
    "web_mcp": ("web_mcp_skills_root", "/skills/web_mcp/"),
    "web_cli": ("web_cli_skills_root", "/skills/web_cli/"),
    "webwright": ("webwright_skills_root", "/skills/webwright/"),
    "android": ("android_skills_root", "/skills/android/"),
    "ios": ("ios_skills_root", "/skills/ios/"),
}


class TestSkillsMaintenanceDirectory:
    """防止"双份维护"与接线漂移回潮（历史上 testcase 曾在
    backend/workspace/testcase/skills/ 另存一份并各自演化）。"""

    def test_single_canonical_directory_exists(self):
        assert CANONICAL_SKILLS_ROOT.is_dir(), f"唯一维护目录不存在: {CANONICAL_SKILLS_ROOT}"

    def test_all_agents_share_the_same_skills_root(self):
        from app.config.settings import settings

        for agent, (field, _source) in EXPECTED_AGENT_SOURCES.items():
            value = Path(getattr(settings, field)).resolve()
            assert value == CANONICAL_SKILLS_ROOT, (
                f"{agent} 的 {field} = {value}，应为统一根 {CANONICAL_SKILLS_ROOT}"
            )

    def test_agent_sources_follow_unified_convention(self):
        import re

        for agent, (_field, expected_source) in EXPECTED_AGENT_SOURCES.items():
            agent_file = AGENTS_DIR / agent / "agent.py"
            assert agent_file.is_file(), f"缺少 {agent_file}"
            text = agent_file.read_text(encoding="utf-8", errors="replace")
            match = re.search(r"sources\s*=\s*\[(.*?)\]", text, re.DOTALL)
            assert match, f"{agent}/agent.py 未找到 SkillsMiddleware sources"
            sources = re.findall(r'"([^"]+)"', match.group(1))
            assert sources == [expected_source], (
                f"{agent}/agent.py 的 sources={sources}，统一约定应为 ['{expected_source}']"
            )

    def test_source_dirs_exist_under_canonical_root(self):
        for agent, (_field, source) in EXPECTED_AGENT_SOURCES.items():
            rel = source.strip("/").replace("skills/", "", 1)
            target = CANONICAL_SKILLS_ROOT / rel
            assert target.is_dir(), f"{agent} 的 source 目录不存在: {target}"
            assert list_skills(target), f"{agent} 的 source 下没有含 SKILL.md 的目录: {target}"

    def test_no_duplicate_skill_copies_outside_canonical_root(self):
        """backend/workspace/** 下不得再放 SKILL.md —— 双份副本必然漂移。

        排除 node_modules / .npm-cache：那是第三方依赖（Playwright 等）自带的
        skill 文档，不属于本框架维护范围。
        """
        ignored_parts = {"node_modules", ".npm-cache", ".venv", "__pycache__"}
        offenders = [
            p.relative_to(REPO_ROOT).as_posix()
            for p in (REPO_ROOT / "backend" / "workspace").rglob("SKILL.md")
            if p.is_file() and not ignored_parts & set(p.parts)
        ]
        assert not offenders, (
            "检测到唯一维护目录之外的 SKILL.md，请合并到 .claude/skills 并删除副本：\n"
            + "\n".join(offenders)
        )

    def test_testcase_skill_set_matches_reference_set(self):
        """testcase skill 集合必须覆盖参考仓库 ai-test-agent-system 的 8 个 skill。

        参考仓库把同一套 skill 维护在 src/workspace/testcase/skills/；本框架统一到
        .claude/skills/testcase。集合只增不减，避免迁移时漏掉某个 skill。
        """
        expected = {
            "output-formatter",
            "quality-review",
            "requirement-analysis",
            "sit-testcase-generation-v2",
            "test-case-design",
            "test-data-generator",
            "test-strategy",
            "uat-scenario-design",
        }
        actual = set(list_skills(CANONICAL_SKILLS_ROOT / "testcase"))
        assert expected <= actual, f"缺少 skill: {sorted(expected - actual)}"

    def test_uat_scenario_design_is_the_evolved_version(self):
        """回归点：workspace 副本曾被更新到 194 行，合并时不得退回 117 行旧版。"""
        skill = CANONICAL_SKILLS_ROOT / "testcase" / "uat-scenario-design" / "SKILL.md"
        text = skill.read_text(encoding="utf-8", errors="replace")
        assert "UAT" in text
        assert len(text.splitlines()) > 150, "uat-scenario-design 疑似回退到旧版本"
