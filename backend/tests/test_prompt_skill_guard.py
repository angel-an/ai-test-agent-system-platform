"""A2 验收：运行时 Skill 树不含被 Shell 策略禁止的命令。

rev13 修正（采纳复审）：
- 扫描范围 = web_cli / webwright 运行时来源下的**全部 .md**（不止 SKILL.md）；
- **运行时来源下不得存在 SHELL-POLICY-BANNED-FILE 横幅**——含被禁命令示例的文件
  必须移出运行时来源（已归档至 .claude/skills_archive/），而不是"标记并忽略"；
- 命中被禁模式的行若含 SHELL-POLICY-BANNED 标记 → 允许（声明为禁用）。
"""

import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]  # ai-test-agent-system-platform 根
AGENT_DIR = REPO_ROOT / "backend" / "app" / "agents"
SKILLS_DIR = REPO_ROOT / ".claude" / "skills"
ARCHIVE_DIR = REPO_ROOT / ".claude" / "skills_archive"

# 被 Shell 策略禁止的命令模式（与 shell_policy.py 的拒绝语义一致）
BANNED_PATTERNS = [
    r"npx playwright test",
    # 命令形态的 playwright test（后跟文件/flag）；散文"Playwright test scripts"不算
    r"playwright test\s+(?:<|--|[\w./\\-]+\.(?:spec\.ts|ts|js))",
    # rev14：选项感知——playwright-cli [-s=...] [--raw ...] eval/run-code 均须捕获
    r"playwright-cli(?:\s+--?[\w-]+(?:=\S+|\s+\S+)?)*\s+(?:eval|run-code)",
    # rev15：npx playwright cli 别名下的 eval/run-code
    # rev16：升级为选项感知——cli [--raw] [-s=x] eval/run-code 均须捕获
    r"npx playwright cli(?:\s+--?[\w-]+(?:=\S+|\s+\S+)?)*\s+(?:eval|run-code)",
    # rev15：解释器直跑（与 shell_policy FOREVER_DENIED 对齐）——
    # rev16：选项感知——python [-I] scripts/demo.py、node [--no-warnings] runner.js 均捕获；
    # (?<![.\w]) 排除 .py 扩展名误报；无扩展名/纯内联形式（python -I -c ...）由运行时兜底
    r"(?<![.\w])(?:python3?|py|node)(?:\s+--?[\w-]+(?:=\S+|\s+\S+)?)*\s+[\w./\\-]+\.(?:py|js|mjs|cjs|ts)\b",
    r"run-code",
    r"python3 final_runs",
    r"python3 playground",
    r"python final_runs",
    r"python3?\s+-c\s",
    r"node\s+-e\s",
    r"test_run\s*[\( ]",
    r"test_debug\s*[\( ]",
    r"test_list\s*[\( ]",
    r"browser_evaluate",
]

MARKER = "SHELL-POLICY-BANNED"
FILE_MARKER = "SHELL-POLICY-BANNED-FILE"

AGENT_PROMPTS = [
    AGENT_DIR / "web_cli" / "agent.py",
    AGENT_DIR / "webwright" / "agent.py",
    AGENT_DIR / "web_mcp" / "agent.py",
]


def _runtime_skill_files() -> list[Path]:
    files: list[Path] = []
    for skill_root in ("web_cli", "webwright", "web_mcp"):  # rev24：web_mcp 运行时一并纳入
        base = SKILLS_DIR / skill_root
        if base.exists():
            files.extend(sorted(base.rglob("*.md")))
    return files


@pytest.mark.parametrize("path", [str(p) for p in AGENT_PROMPTS + _runtime_skill_files()])
def test_no_banned_commands(path: str):
    p = Path(path)
    text = p.read_text(encoding="utf-8", errors="replace")
    violations = []
    for lineno, line in enumerate(text.splitlines(), start=1):
        if MARKER in line:
            continue  # 行内声明"已禁用"
        for pat in BANNED_PATTERNS:
            if re.search(pat, line, re.IGNORECASE):
                violations.append(f"  L{lineno}: {line.strip()[:90]}")
                break
    assert not violations, (
        f"被禁命令出现在运行时文件 {p.relative_to(REPO_ROOT)} "
        f"（若为禁用声明需行内含 {MARKER}）:\n"
        + "\n".join(violations)
    )


def test_runtime_sources_have_no_banned_file_marker():
    """rev13：运行时来源下不得存在 SHELL-POLICY-BANNED-FILE 横幅——
    含被禁命令示例的文件必须移出运行时来源，而非"标记并忽略"。"""
    violations = []
    for p in _runtime_skill_files():
        text = p.read_text(encoding="utf-8", errors="replace")
        if FILE_MARKER in text:
            violations.append(str(p.relative_to(REPO_ROOT)))
    assert not violations, (
        "运行时 Skill 树中发现 SHELL-POLICY-BANNED-FILE 横幅文件，需移出运行时来源"
        "（归档至 .claude/skills_archive/）或改为无可复制命令的说明:\n"
        + "\n".join(violations)
    )


def test_banned_pattern_matches_option_forms():
    """rev14 回归：带 CLI 选项的 eval/run-code 必须被静态检查捕获
    （此前 'playwright-cli eval' 连续匹配漏掉 '-s=<sess> eval'）。"""
    for line in [
        'playwright-cli -s=<sess> eval "el => el.id" e5',
        'playwright-cli --raw eval "JSON.stringify(x)"',
        'playwright-cli -s=test1 run-code "async page => {}"',
        'npx playwright test file.spec.ts',
        'playwright test tests/a.spec.ts --reporter=html',
        # rev15：解释器直跑与 npx cli 别名下的代码执行
        'python scripts/demo.py',
        'python3 run_001/final_script.py',
        'py test.py',
        'node runner.js',
        'node scripts/app.mjs',
        'npx playwright cli eval "document.title"',
        'npx playwright cli run-code "async page => {}"',
        # rev16：带选项的别名/直跑形式
        'npx playwright cli --raw eval "JSON.stringify(x)"',
        'npx playwright cli -s=x run-code "async page => {}"',
        'python -I scripts/demo.py',
        'node --no-warnings runner.js',
        'python3 -m pytest tests/test_demo.py',
    ]:
        assert any(re.search(p, line, re.IGNORECASE) for p in BANNED_PATTERNS), (
            f"模式未捕获被禁命令: {line}"
        )


def test_archived_files_exist():
    """归档目录存在且非空（被移出的文件有据可查）。"""
    assert ARCHIVE_DIR.exists(), "skills_archive 目录不存在"
    archived = list(ARCHIVE_DIR.rglob("*.md"))
    assert archived, "skills_archive 无归档 .md 文件"


def test_web_cli_prompt_uses_execute_web_script():
    text = (AGENT_DIR / "web_cli" / "agent.py").read_text(encoding="utf-8")
    assert "execute_web_script(" in text


def test_webwright_prompt_uses_execute_web_script():
    text = (AGENT_DIR / "webwright" / "agent.py").read_text(encoding="utf-8")
    assert "execute_web_script(" in text


def test_execute_web_script_calls_include_sub_function():
    """rev23：运行时提示词/Skill 中每个 execute_web_script 调用都必须携带
    sub_function_id/sub_function_ids（严格模式必填）；纯省略 execute_web_script(...)
    的"已下线"映射行除外。防止模型按运行时指引调用后触发终局拒绝。"""
    violations = []
    for p in AGENT_PROMPTS + _runtime_skill_files():
        text = p.read_text(encoding="utf-8", errors="replace")
        for m in re.finditer(r"execute_web_script\(", text):
            start = m.end()
            depth = 1
            i = start
            while i < len(text) and depth > 0:
                if text[i] == "(":
                    depth += 1
                elif text[i] == ")":
                    depth -= 1
                i += 1
            if depth != 0:
                continue  # 无法配平括号，跳过
            call = text[m.start():i]
            if "sub_function_id" in call:
                continue
            # rev26：仅豁免"纯历史描述"——含已下线 且 无 execute_web_script 调用；
            # "已下线"映射行右侧若给出 execute_web_script 替代，则必须为完整调用
            line_start = text.rfind("\n", 0, m.start()) + 1
            line_end = text.find("\n", i)
            line = text[line_start:line_end if line_end != -1 else len(text)]
            if "已下线" in line and "execute_web_script(" not in line:
                continue
            violations.append(f"{p.relative_to(REPO_ROOT)}: {call[:110]}")
    assert not violations, (
        "execute_web_script 调用缺少必填 sub_function_id（严格模式）:\n"
        + "\n".join(violations)
    )
