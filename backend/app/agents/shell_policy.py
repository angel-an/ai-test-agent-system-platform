"""Shell 执行面安全策略 —— 执行治理层 P0-1（第 3 项，rev4 加固）

背景：deepagents.LocalShellBackend 以 subprocess.run(shell=True) 执行任意命令，
官方文档明示"无沙箱、可读写任意文件、可外联"。对 Web 测试 agent 来说，这等于
把宿主机 shell 直接交给模型。

rev4 加固（回应复审）：
- **路径工作区包含**：所有文件参数（含 cd 目标）必须解析在 workspace root 内，
  拒绝绝对路径读/写外部文件（type C:\\Windows\\win.ini）、`..` 逃逸、cd 出界；
- **禁止重定向**：除 2>&1 / 1>&2 外的所有 `>` / `<` 一律拒绝（echo x > C:\\tmp\\a 写任意文件）；
- **禁止任意解释器模块执行**：python/py 的 -m/--module，node 的 -r/--require/--loader 等拒绝；
- 保留既有：命令白名单、显式黑名单、解释器内联代码参数（-c/-e/heredoc/命令替换）、
  复合命令逐段校验、npx 仅 playwright、危险删除命令绝对路径防护。

**残留风险（明确声明，P0-1 第 3 项不关闭的原因）**：
"python <工作区内脚本>" 是 Track C 脚本回放的被授权执行通道——模型可先写入工作区
脚本再经白名单解释器运行，效果仍为宿主机代码执行。本策略只能约束"通道内行为"
（不逃逸文件系统、不重定向、不调任意模块），**真正的进程隔离需要容器/低权限执行器**
（独立工作项，见评审文档）。warn 模式的 agent 仅审计，不构成执行层加固。

模式（env: SHELL_POLICY_MODE，默认 enforce）：
- enforce：命中策略直接终局拒绝；
- warn：记录告警后照常执行（迁移/审计用）；
- off：关闭校验（不推荐）。
"""

from __future__ import annotations

import json
import logging
import os
import re
from typing import Any

from deepagents.backends.local_shell import ExecuteResponse, LocalShellBackend

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# 白名单（默认放行；SHELL_ALLOWED_COMMANDS 可追加）
# ---------------------------------------------------------------------------
DEFAULT_ALLOWED_COMMANDS: frozenset[str] = frozenset({
    # 工作区 / 文本操作
    "cd", "pwd", "ls", "dir", "cat", "type", "echo", "mkdir", "rmdir", "cp", "mv",
    "rm", "del", "touch", "find", "grep", "head", "tail", "wc", "tee", "tree",
    "sort", "uniq", "cut", "tr", "sed", "cls", "clear", "where",
    # Web 测试工作流（webwright / web_cli / web_mcp）
    # rev11：解释器已永久禁用（FOREVER_DENIED）；playwright（test-runner）移除——
    # 脚本执行唯一通道 = execute_web_script；npx 仅允许 playwright cli 别名；
    # playwright-cli 仅浏览器交互子命令（eval/run-code 拒绝）
    "npx", "playwright-cli",
    # 注意：xargs / find -exec 等"代理执行"命令已移除/禁用（P0 复审修正），
    # 否则 echo x | xargs python -c 可绕过所有子命令限制；
    # set 已移除（rev7）：set NODE_OPTIONS=--require=evil.js 可经环境变量
    # 将 Node 危险加载参数注入后续执行
})

# 永远拒绝（即使被 env SHELL_ALLOWED_COMMANDS 追加也不放行）
FOREVER_DENIED_COMMANDS: frozenset[str] = frozenset({
    # 通用 shell / 解释器内联
    "bash", "sh", "zsh", "ksh", "csh", "fish", "cmd", "powershell", "pwsh",
    "perl", "ruby", "php", "python2", "env", "eval", "source", "start",
    # rev11（P0 复审修正）：Web 执行面永久禁用代码解释器——python/python3/py/node
    # 即使被 SHELL_ALLOWED_COMMANDS=python,node 追加也无法重新启用
    "python", "python3", "py", "node",
    # 网络外联 / 渗透 / 系统管理
    "curl", "wget", "nc", "ncat", "telnet", "ssh", "scp", "sftp", "ftp",
    "nmap", "msfconsole", "reg", "regedit", "format", "diskpart", "bcdedit",
    "taskkill", "shutdown", "net", "netsh", "attrib", "cacls", "icacls",
})

# 解释器危险选项（rev6：全参数扫描，防"前置无害选项"绕过）
# 长选项名：命中（含 --flag=value 等号形式）即拒绝
INLINE_LONG_FLAGS: dict[str, tuple[str, ...]] = {
    "python": ("--command", "--module"),
    "python3": ("--command", "--module"),
    "py": ("--command", "--module"),
    # --experimental-loader 是 --loader 的兼容别名（rev9），一并禁用
    "node": ("--eval", "--print", "--inspect", "--require", "--loader",
             "--experimental-loader", "--import"),
}
# 短选项字母：在选项簇（如 -Ic / -c=code）中出现即拒绝
INLINE_SHORT_FLAGS: dict[str, str] = {
    "python": "cmi",
    "python3": "cmi",
    "py": "cmi",
    "node": "epri",
}
# 需要独立取值的无害短选项（用于正确跳过值 token，避免漏扫后续危险选项）
VALUE_TAKING_SHORT: dict[str, str] = {
    "python": "WX",
    "python3": "WX",
    "py": "WX",
    "node": "",
}
# 需要独立取值的无害长选项
_LONG_WITH_VALUE: dict[str, tuple[str, ...]] = {
    "python": ("--check-hash-based-pycs",),
    "python3": ("--check-hash-based-pycs",),
    "py": ("--check-hash-based-pycs",),
    "node": (),
}

# npx 仅允许 playwright 子命令（rev11：进一步限定为 playwright cli 别名）
NPX_ALLOWED_SUBCOMMANDS: tuple[str, ...] = ("playwright",)

# playwright-cli 代码执行子命令（rev11：eval/run-code 执行任意 JS，拒绝）
_PLAYWRIGHT_CLI_CODE_SUBCOMMANDS: frozenset[str] = frozenset({
    "eval", "evaluate", "run-code", "run_code",
})


def _playwright_cli_code_subcommand(tokens: list[str]) -> str | None:
    """取 playwright-cli 首个非 flag token（子命令）；若为代码执行子命令则返回其名。"""
    for tok in tokens:
        if tok.startswith("-"):
            continue
        if tok in _PLAYWRIGHT_CLI_CODE_SUBCOMMANDS:
            return tok
        return None  # 首个非 flag token 即子命令，非代码执行 → 放行
    return None

# 危险删除命令的绝对路径防护
DESTRUCTIVE_COMMANDS: frozenset[str] = frozenset({"rm", "rmdir", "del", "erase"})
_ABSOLUTE_PATH_RE = re.compile(r"(^|\s)[A-Za-z]:[\\/]|^\s*[\\/]|^\s*\.[\\/]")

# 复合命令分隔符：&& / || / ; / | / 单 &（Windows cmd 的 & 也是命令分隔符）
# 注意：2>&1 / 1>&2 中的 & 前面是 >/<，用负向前瞻排除，避免误拆
_SEGMENT_SPLIT_RE = re.compile(r"\s*(?:&&|\|\||;|\||(?<![<>])&)\s*")

# 路径特征 token：含分隔符 / 盘符 / 点开头
_PATH_LIKE_RE = re.compile(r"[\\/]|^[A-Za-z]:|^\.{1,2}[\\/]|^\.{1,2}$")


def policy_mode() -> str:
    """当前策略模式（env: SHELL_POLICY_MODE，默认 enforce）。"""
    mode = os.getenv("SHELL_POLICY_MODE", "enforce").strip().lower()
    return mode if mode in ("enforce", "warn", "off") else "enforce"


def _extra_allowed() -> frozenset[str]:
    raw = os.getenv("SHELL_ALLOWED_COMMANDS", "")
    return frozenset(s.strip().lower() for s in raw.split(",") if s.strip())


def _extra_denied() -> frozenset[str]:
    raw = os.getenv("SHELL_DENY_COMMANDS", "")
    return frozenset(s.strip().lower() for s in raw.split(",") if s.strip())


def _normalize_bin(token: str) -> str:
    b = token.strip().strip('"').strip("'").lower()
    for ext in (".exe", ".cmd", ".bat", ".ps1", ".com"):
        if b.endswith(ext):
            b = b[: -len(ext)]
            break
    return b


# ---------------------------------------------------------------------------
# 重定向检查（仅允许 2>&1 / 1>&2）
# ---------------------------------------------------------------------------

def _has_forbidden_redirect(seg: str) -> bool:
    for m in re.finditer(r"[<>]", seg):
        ch = seg[m.start()]
        if ch == ">":
            prev = seg[m.start() - 1] if m.start() > 0 else ""
            after = seg[m.start() + 1:m.start() + 3]
            if prev in ("1", "2") and len(after) == 2 and after[0] == "&" and after[1] in ("1", "2"):
                continue  # 2>&1 / 1>&2 放行
            return True
        return True  # '<' 一律拒绝
    return False


# ---------------------------------------------------------------------------
# 路径工作区包含
# ---------------------------------------------------------------------------

def _is_inside_root(path: str, root_dir: str) -> bool:
    """判断 path（绝对或相对）解析后是否位于 root_dir 内。"""
    try:
        if os.path.isabs(path):
            ap = os.path.abspath(os.path.normpath(path))
        else:
            ap = os.path.abspath(os.path.normpath(os.path.join(root_dir, path)))
        ar = os.path.abspath(root_dir)
        return os.path.commonpath([ap, ar]) == ar
    except (ValueError, OSError):
        return False


def _check_paths(seg: str, root_dir: str) -> tuple[bool, str]:
    """对所有路径特征 token 做工作区包含校验（含 cd 目标）。"""
    tokens = seg.replace("=", " ").split()
    for tok in tokens:
        if not tok:
            continue
        if tok.startswith(("-", "--")):
            continue
        if tok.startswith(("http://", "https://")):
            continue
        if tok in ("2>&1", "1>&2"):
            continue
        t = tok.strip('"').strip("'")
        if not t or not _PATH_LIKE_RE.search(t):
            continue
        if not _is_inside_root(t, root_dir):
            return False, f"路径 '{t}' 超出工作区（{root_dir}），拒绝"
    return True, "ok"


def _check_interpreter_args(bin_name: str, args_part: str) -> tuple[bool, str]:
    """全参数扫描解释器选项（rev6，P0 复审修正）。

    扫描到"脚本位置参数"或 `--` 分隔符为止；任何位置命中危险选项
    （长选项名含 = 形式、短选项簇内字母）即终局拒绝——
    堵住 `node --trace-warnings --eval=code`、`python -I -c code` 等
    "前置无害选项"绕过。带值选项正确跳过其值 token，避免漏扫。
    """
    tokens = args_part.split()
    i = 0
    while i < len(tokens):
        tok = tokens[i]
        if tok == "--":
            return True, "ok"  # 之后为脚本与脚本参数
        if tok == "-":
            return False, f"命令 '{bin_name}' 禁止从 stdin 读取代码（-）"
        if tok.startswith("--"):
            flag = tok.split("=", 1)[0]
            if flag in INLINE_LONG_FLAGS.get(bin_name, ()):
                return False, f"命令 '{bin_name}' 禁止内联代码/模块执行参数（{tok}）"
            if "=" not in tok and flag in _LONG_WITH_VALUE.get(bin_name, ()):
                i += 1  # 跳过独立取值 token
            i += 1
            continue
        if tok.startswith("-") and len(tok) > 1:
            cluster = tok[1:]
            j = 0
            while j < len(cluster):
                ch = cluster[j]
                if ch in INLINE_SHORT_FLAGS.get(bin_name, ""):
                    return False, f"命令 '{bin_name}' 禁止内联代码/模块执行参数（{tok}）"
                if ch in VALUE_TAKING_SHORT.get(bin_name, ""):
                    if j + 1 < len(cluster):
                        break  # 值已附加在簇内（如 -Wignore）
                    i += 1  # 值在下一个 token
                    break
                j += 1
            i += 1
            continue
        # 位置脚本参数：其后为脚本参数，不再有解释器选项
        return True, "ok"
    return True, "ok"


# ---------------------------------------------------------------------------
# 段校验
# ---------------------------------------------------------------------------

def _check_segment(seg: str, root_dir: str | None) -> tuple[bool, str]:
    seg = seg.strip()
    if not seg:
        return True, "空段放行"
    # rev8（P0 复审修正）：cmd.exe 预处理与策略解析不一致——
    #   ^  是 cmd 转义符，执行前被移除：--e^val 实际传给解释器是 --eval；
    #   %VAR% 是环境变量展开：%USERPROFILE%\x 可越界读文件、%NODE_OPTIONS% 可注入危险选项。
    # 工作流无需使用二者，最简收敛：在进入任何命令解析前直接终局拒绝。
    if "^" in seg or "%" in seg:
        return False, "命令包含 cmd 转义符 ^ 或环境变量展开 %，拒绝（防 shell 预处理绕过）"
    # 重定向（除 2>&1 / 1>&2）
    if _has_forbidden_redirect(seg):
        return False, "检测到文件重定向（> / <），拒绝"
    # heredoc 内联代码
    if re.search(r"<<\s*[A-Za-z_]", seg):
        return False, "检测到 heredoc（<<）内联代码，拒绝"
    # 命令替换（$(...) / 反引号）——任意代码
    if "$(" in seg or "`" in seg:
        return False, "检测到命令替换（$(...) 或反引号），拒绝"
    m = re.match(r'^\s*"?([^\s"|&;]+)', seg)
    if not m:
        return False, f"无法解析命令首词: {seg[:40]!r}"
    bin_name = _normalize_bin(m.group(1))
    if bin_name in FOREVER_DENIED_COMMANDS or bin_name in _extra_denied():
        return False, f"命令 '{bin_name}' 被安全策略禁用（SHELL_DENY_COMMANDS）"
    if bin_name not in DEFAULT_ALLOWED_COMMANDS and bin_name not in _extra_allowed():
        return False, f"命令 '{bin_name}' 不在 shell 白名单内（SHELL_ALLOWED_COMMANDS）"
    args_part = seg[m.end():].strip()
    # rev7（P0 复审修正）：解释器命令禁止带引号参数——LocalShellBackend 用
    # shell=True 执行，Windows shell 会去除外层引号，解释器实际仍收到危险选项，
    # 使 "--eval=code" / "-c" 等绕过全参数扫描（严格结构化参数模型前的最简收敛）
    if bin_name in ("python", "python3", "py", "node") and ('"' in args_part or "'" in args_part):
        return False, f"命令 '{bin_name}' 禁止带引号参数（防引号绕过）"
    # 解释器内联代码 / 任意模块执行参数（rev6：全参数扫描，
    # 覆盖前置无害选项 + --flag=value 等号形式 + 短选项簇）
    if bin_name in INLINE_LONG_FLAGS or bin_name in INLINE_SHORT_FLAGS:
        ok, reason = _check_interpreter_args(bin_name, args_part)
        if not ok:
            return False, reason
    # npx：仅允许 `npx playwright cli <子命令>`（playwright-cli 别名）；
    # `npx playwright test <file>` 会运行测试脚本（代码执行）→ 拒绝（rev11）
    if bin_name == "npx" and args_part:
        toks = args_part.split()
        if len(toks) < 2 or toks[0] not in NPX_ALLOWED_SUBCOMMANDS or toks[1] != "cli":
            return False, "npx 仅允许 'playwright cli <子命令>'（脚本执行请走 execute_web_script）"
        denied = _playwright_cli_code_subcommand(toks[2:])
        if denied:
            return False, f"playwright-cli 禁止代码执行子命令（{denied}）"
    # playwright-cli：仅浏览器交互子命令；eval / run-code 执行任意 JS → 拒绝（rev11）
    if bin_name == "playwright-cli" and args_part:
        denied = _playwright_cli_code_subcommand(args_part.split())
        if denied:
            return False, f"playwright-cli 禁止代码执行子命令（{denied}）"
    # find 禁止 -exec/-execdir/-ok（代理执行子命令，P0 复审修正）
    if bin_name == "find" and re.search(r"-(?:exec(dir)?|ok(dir)?)\b", args_part):
        return False, "find 禁止 -exec/-execdir/-ok/-okdir（代理执行）"
    # 危险删除命令禁止绝对路径
    if bin_name in DESTRUCTIVE_COMMANDS and _ABSOLUTE_PATH_RE.search(args_part):
        return False, f"命令 '{bin_name}' 禁止操作绝对路径"
    # 路径工作区包含（root_dir 为空时跳过——由调用方保证传入）
    if root_dir:
        ok, reason = _check_paths(seg, root_dir)
        if not ok:
            return False, reason
    return True, "ok"


def check_shell_command(command: str, root_dir: str | None = None) -> tuple[bool, str]:
    """校验 shell 命令。返回 (allowed, reason)。

    root_dir 提供时额外执行"路径工作区包含"校验（生产调用方必须传入
    GuardedLocalShellBackend 的 workspace root）。
    """
    if not command or not isinstance(command, str):
        return False, "命令为空或非字符串"
    for seg in _SEGMENT_SPLIT_RE.split(command):
        allowed, reason = _check_segment(seg, root_dir)
        if not allowed:
            return False, f"[{seg.strip()[:60]}] {reason}"
    return True, "ok"


def _denial_response(reason: str) -> ExecuteResponse:
    return ExecuteResponse(
        output=(
            '{"success": false, "final": true, "guard": "shell_policy", '
            f'"reason": {json.dumps(reason, ensure_ascii=False)}, '
            '"message": "该 shell 命令被安全策略终局拒绝，请勿重试或换参数重试。"}'
        ),
        exit_code=1,
        truncated=False,
    )


class GuardedLocalShellBackend(LocalShellBackend):
    """带 shell 安全策略的本地执行后端（P0-1 第 3 项）。

    mode（优先于 env SHELL_POLICY_MODE）：
      - enforce（默认）：命中策略直接终局拒绝；
      - warn：记录告警后照常执行（审计/迁移用）；
      - off：关闭校验（不推荐）。
    """

    def __init__(self, *args: Any, mode: str | None = None, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._guard_mode = (mode or policy_mode()).strip().lower()

    def execute(self, command: str, *, timeout: int | None = None) -> ExecuteResponse:
        root_dir = str(self.cwd)
        allowed, reason = check_shell_command(command, root_dir=root_dir)
        if not allowed:
            if self._guard_mode == "enforce":
                logger.warning("[ShellGuard] %s -> FINAL DENY: %s", command[:80], reason)
                return _denial_response(reason)
            logger.warning("[ShellGuard] %s 模式放行（审计）: %s", self._guard_mode, reason)
        return super().execute(command, timeout=timeout)


__all__ = [
    "DEFAULT_ALLOWED_COMMANDS",
    "FOREVER_DENIED_COMMANDS",
    "INLINE_LONG_FLAGS",
    "INLINE_SHORT_FLAGS",
    "GuardedLocalShellBackend",
    "check_shell_command",
    "policy_mode",
]
