# -*- coding: utf-8 -*-
"""Skill 装配自检（P0-4）。

动机
----
`SkillsMiddleware` 在 skills source 解析不到目录时**只记一条 warning**，
Agent 照常启动但拿不到任何 skill。失败因此是静默的：表现为"Agent 能力变弱"，
不会被任何门禁拦住。

实测已发生两例（2026-09 复核）：
1. `cd backend` 启动时 `testcase_skills_root`（当时是相对路径且与 sources 约定不符）
   被解析成 `<repo>/backend/backend/workspace/testcase` → testcase Agent 加载 0 个
   skill，`quality-review` / `sit-testcase-generation-v2` 全部失效；
2. 从仓库根用 `start_server_*.py` 启动时，根 `.env` 的 `API_SKILLS_ROOT` 指向不存在的
   `backend/app/agents/api/agent_skills`，API Agent 同样降到 0 个 skill。

本模块提供确定性的装配自检：不依赖 deepagents，直接按
"sources 去掉 /skills/ 前缀后拼到 skills_root" 的规则解析并核对 SKILL.md 是否存在。

用法
----
- 启动时：`main.py` lifespan 调用 `log_skill_assembly_report()`（不阻断启动）；
- 人工排查：`python -m app.config.skill_assembly_check`；
- 单测：`check_skill_assembly(specs)` 可传自定义 specs。

自检为**只读**：不创建目录、不改配置。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path

logger = logging.getLogger(__name__)

SKILLS_ROUTE_PREFIX = "/skills"


@dataclass(frozen=True)
class SkillSourceSpec:
    """一个 Agent 的 skills 装配声明。

    Attributes:
        agent: Agent 名称（日志展示用）。
        skills_root: SkillsMiddleware 的 backend 根目录（与 settings 中同名配置一致）。
        sources: 传给 SkillsMiddleware 的 sources 列表。
        required: 必须装配到的 skill 目录名；缺失即判为装配错误。
    """

    agent: str
    skills_root: str
    sources: tuple[str, ...]
    required: tuple[str, ...] = ()


@dataclass(frozen=True)
class SkillSourceResult:
    """单个 source 的装配结果。"""

    agent: str
    source: str
    root: str
    source_dir: str
    root_exists: bool
    dir_exists: bool
    loaded: tuple[str, ...]
    missing_required: tuple[str, ...] = ()

    @property
    def ok(self) -> bool:
        return self.dir_exists and bool(self.loaded) and not self.missing_required

    @property
    def problem(self) -> str | None:
        """返回人类可读的失败原因；正常时为 None。"""
        if not self.root_exists:
            return f"skills_root 不存在：{self.root}"
        if not self.dir_exists:
            return f"source 目录不存在：{self.source_dir}（source={self.source!r}）"
        if not self.loaded:
            return f"source 下没有任何含 SKILL.md 的 skill 目录：{self.source_dir}"
        if self.missing_required:
            return f"缺少必需 skill：{', '.join(self.missing_required)}"
        return None


def resolve_source_dir(skills_root: str | Path, source: str) -> Path:
    """按 SkillsMiddleware 的挂载语义把 source 解析为文件系统目录。

    CompositeBackend 的 ``/skills/`` 路由会把该前缀去掉后交给以
    ``skills_root`` 为根的 backend，因此：

    - ``/skills/``            -> ``<skills_root>/``
    - ``/skills/api/``        -> ``<skills_root>/api/``
    - ``/skills/testcase/``   -> ``<skills_root>/testcase/``
    """
    root = Path(skills_root)
    rel = source
    if rel == SKILLS_ROUTE_PREFIX or rel == f"{SKILLS_ROUTE_PREFIX}/":
        rel = ""
    elif rel.startswith(f"{SKILLS_ROUTE_PREFIX}/"):
        rel = rel[len(SKILLS_ROUTE_PREFIX) + 1 :]
    rel = rel.strip("/\\").replace("\\", "/")
    return (root / rel).resolve() if rel else root.resolve()


def list_skills(source_dir: Path) -> tuple[str, ...]:
    """列出目录下所有含 SKILL.md 的 skill 目录名（与 SkillsMiddleware 判定一致）。"""
    if not source_dir.is_dir():
        return ()
    names = [
        child.name
        for child in sorted(source_dir.iterdir())
        if child.is_dir() and (child / "SKILL.md").is_file()
    ]
    return tuple(names)


def check_source(spec: SkillSourceSpec, source: str) -> SkillSourceResult:
    """检查单个 source 的装配情况。"""
    root = Path(spec.skills_root).resolve()
    source_dir = resolve_source_dir(root, source)
    loaded = list_skills(source_dir)
    missing = tuple(name for name in spec.required if name not in loaded)
    return SkillSourceResult(
        agent=spec.agent,
        source=source,
        root=str(root),
        source_dir=str(source_dir),
        root_exists=root.is_dir(),
        dir_exists=source_dir.is_dir(),
        loaded=loaded,
        missing_required=missing,
    )


def check_skill_assembly(
    specs: list[SkillSourceSpec] | None = None,
) -> list[SkillSourceResult]:
    """检查全部（或指定）Agent 的 skills 装配情况。"""
    specs = specs if specs is not None else default_specs()
    results: list[SkillSourceResult] = []
    for spec in specs:
        for source in spec.sources:
            results.append(check_source(spec, source))
    return results


def default_specs() -> list[SkillSourceSpec]:
    """从 settings 读取各 Agent 的 skills 装配声明。

    与各 ``app/agents/<name>/agent.py`` 中的 roots/sources 保持一致；
    新增 Agent 时需同步此处，否则不会被自检覆盖。
    """
    from app.config.settings import settings

    return [
        SkillSourceSpec(
            "api",
            settings.api_skills_root,
            ("/skills/api/",),
            required=("planner", "generator", "scenario"),
        ),
        # root = .claude/skills（所有 agent 共用），source = /skills/<agent目录>/
        SkillSourceSpec(
            "testcase",
            settings.testcase_skills_root,
            ("/skills/testcase/",),
            required=("quality-review", "sit-testcase-generation-v2"),
        ),
        SkillSourceSpec(
            "testcase-reference",
            settings.testcase_skills_root,
            ("/skills/testcase/",),
        ),
        SkillSourceSpec(
            "security",
            settings.security_skills_root,
            ("/skills/security/",),
        ),
        SkillSourceSpec(
            "web_mcp",
            settings.web_mcp_skills_root,
            ("/skills/web_mcp/",),
        ),
        SkillSourceSpec(
            "web_cli",
            settings.web_cli_skills_root,
            ("/skills/web_cli/",),
        ),
        SkillSourceSpec(
            "webwright",
            settings.webwright_skills_root,
            ("/skills/webwright/",),
        ),
        SkillSourceSpec(
            "android",
            settings.android_skills_root,
            ("/skills/android/",),
        ),
        SkillSourceSpec(
            "ios",
            settings.ios_skills_root,
            ("/skills/ios/",),
        ),
    ]


@dataclass
class SkillAssemblyReport:
    """汇总结果。"""

    results: list[SkillSourceResult] = field(default_factory=list)

    @property
    def failures(self) -> list[SkillSourceResult]:
        return [r for r in self.results if not r.ok]

    @property
    def ok(self) -> bool:
        return not self.failures

    def to_dict(self) -> dict:
        return {
            "ok": self.ok,
            "checked": len(self.results),
            "failed": len(self.failures),
            "sources": [
                {
                    "agent": r.agent,
                    "source": r.source,
                    "skills_root": r.root,
                    "source_dir": r.source_dir,
                    "loaded": list(r.loaded),
                    "ok": r.ok,
                    "problem": r.problem,
                }
                for r in self.results
            ],
        }

    def to_markdown(self) -> str:
        lines = ["## Skill 装配自检", ""]
        lines.append(f"- 检查 source 数：{len(self.results)}")
        lines.append(f"- 失败 source 数：{len(self.failures)}")
        lines.append("")
        lines.append("| Agent | source | 目录存在 | 已装配 skill | 结论 |")
        lines.append("|---|---|---|---|---|")
        for r in self.results:
            icon = "✅" if r.ok else "❌"
            names = ", ".join(r.loaded) if r.loaded else "—"
            lines.append(f"| {r.agent} | `{r.source}` | {'是' if r.dir_exists else '否'} | {names} | {icon} |")
        for r in self.failures:
            lines.append("")
            lines.append(f"- ❌ **{r.agent}** `{r.source}`：{r.problem}")
        return "\n".join(lines)


def verify_skill_assembly() -> SkillAssemblyReport:
    """执行装配自检并返回汇总报告。"""
    return SkillAssemblyReport(results=check_skill_assembly())


def log_skill_assembly_report() -> SkillAssemblyReport:
    """执行装配自检并按结果分级记录日志（不抛异常、不阻断启动）。

    失败时用 ERROR 级别，因为 skill 装配失败会让 Agent 静默降级为"无 skill"，
    属于必须在日志里可见的故障。
    """
    try:
        report = verify_skill_assembly()
    except Exception as exc:  # noqa: BLE001 - 自检本身绝不能阻断启动
        logger.warning("[skill-assembly] 自检执行失败（不阻断启动）：%s: %s", type(exc).__name__, exc)
        return SkillAssemblyReport()

    for result in report.results:
        if result.ok:
            logger.info(
                "[skill-assembly] %s %s -> %d skills: %s",
                result.agent,
                result.source,
                len(result.loaded),
                ", ".join(result.loaded),
            )
        else:
            logger.error(
                "[skill-assembly] %s %s 装配失败：%s",
                result.agent,
                result.source,
                result.problem,
            )

    if report.ok:
        logger.info("[skill-assembly] 全部 %d 个 source 装配正常", len(report.results))
    else:
        logger.error(
            "[skill-assembly] %d/%d 个 source 装配失败，相关 Agent 将拿不到任何 skill",
            len(report.failures),
            len(report.results),
        )
    return report


def _main() -> int:
    import sys

    # Windows 控制台默认 GBK，报告含 ✅/❌ 会触发 UnicodeEncodeError。
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):  # pragma: no cover
            pass

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    report = log_skill_assembly_report()
    print()
    print(report.to_markdown())
    return 0 if report.ok else 1


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(_main())
