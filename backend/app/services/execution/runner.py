"""
Playwright 异步运行器

使用 asyncio.create_subprocess_exec 执行 Playwright 测试，
避免阻塞事件循环。
"""
"""
andan
"""

# noqa  MC80OmFIVnBZMlhscm9ua3VMazZSbTFrUnc9PTo4MmJmMzg4Yg==

import asyncio
import json
import logging
import os
import re
import subprocess
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional

from app.services.execution.models import RunnerResult
from app.services.web_case_compiler import COMPILER_REV, compiler_source_digest

logger = logging.getLogger(__name__)

#: 产物指纹行的正则（P0-2）：生成期写在 spec 头部，形如 ``// COMPILER_REV: rev117``。
_ARTIFACT_REV_RE = re.compile(r"COMPILER_REV:\s*([A-Za-z0-9._-]+)")
#: 内容摘要行（rev118）：``// COMPILER_DIGEST: 1a2b3c4d5e6f``。
_ARTIFACT_DIGEST_RE = re.compile(r"COMPILER_DIGEST:\s*([0-9a-f]{6,64})")

#: ``WEB_TEST_STRICT_ARTIFACT`` 的三个取值。**默认 auto**（理由见 ``resolve_strict_mode``）。
STRICT_MODES = ("auto", "on", "off")


def read_artifact_rev(script_path: Path, head_chars: int = 8192) -> Optional[str]:
    """读 spec 头部的编译器版本指纹；读不到（旧产物/手工脚本）返回 ``None``。

    只读头部：指纹固定在文件最前面，避免把 200KB+ 的脚本整个读进来。
    """
    try:
        head = script_path.read_text(encoding="utf-8", errors="replace")[:head_chars]
    except OSError:
        return None
    match = _ARTIFACT_REV_RE.search(head)
    return match.group(1) if match else None


def read_artifact_digest(script_path: Path, head_chars: int = 8192) -> Optional[str]:
    """读 spec 头部的源码摘要（rev118+）；rev117 及更早的产物没有，返回 ``None``。"""
    try:
        head = script_path.read_text(encoding="utf-8", errors="replace")[:head_chars]
    except OSError:
        return None
    match = _ARTIFACT_DIGEST_RE.search(head)
    return match.group(1) if match else None


def resolve_strict_mode(raw: Optional[str] = None) -> str:
    """解析严格模式，返回 ``"auto"`` / ``"on"`` / ``"off"``。

    ``auto``（**默认**）的语义刻意不对称：

    - ``legacy_no_fingerprint``（压根没指纹的历史脚本）→ **放行，只告警**。
      平台上仍有 700+ 个 10-17 前的旧产物，默认拒绝只会让所有人立刻停摆，
      反而促使大家去关掉这个开关 —— 规则就死了。
    - ``rev_mismatch`` / ``digest_mismatch``（**打过指纹，但版本落后**）→ **拒绝执行**。
      这类脚本说明资产升级过一次，现在跑的却是旧版本：要么编译逻辑改了没重新生成，
      要么有人手工回退脚本。它跑出来的结论一定是错的（TR-703 静默烧 1745s 就是这类）。
      「已经能识别版本却任其倒退」比「从不识别」更危险，所以这里不给放行选项。
    """
    if raw is None:
        raw = os.environ.get("WEB_TEST_STRICT_ARTIFACT")
    token = str(raw or "").strip().lower()
    if token in {"1", "true", "yes", "on"}:
        return "on"
    if token in {"0", "false", "no", "off"}:
        return "off"
    return "auto"


def should_block_stale(reason: str, mode: str) -> bool:
    """给定过期原因与模式，是否拒绝执行（纯函数，便于直接测）。

    抽成独立函数而不是内联在 ``run()`` 里：否则测试只能测到"模式解析"，
    测不到真正决定命运的"哪个 reason 会被拒" —— 而后者才是这条规范的落点。
    """
    if mode == "on":
        return True
    if mode == "off":
        return False
    return reason != "legacy_no_fingerprint"


def artifact_stale_payload(
    artifact_rev: Optional[str],
    current_rev: str = COMPILER_REV,
    script: str = "",
    *,
    artifact_digest: Optional[str] = None,
    current_digest: Optional[str] = None,
) -> Optional[dict]:
    """产物过期判定的结构化结果；一致时返回 ``None``。

    判据三条，按"信息量从少到多"排列：

    1. 无 rev → ``legacy_no_fingerprint``（历史产物/手工脚本）
    2. rev 不同 → ``rev_mismatch``（编译器已 bump）
    3. rev 相同但 digest 不同 → ``digest_mismatch``（**改了编译逻辑却忘了 bump rev**）
       这是 ``COMPILER_REV`` 靠人手维护时最容易漏的一档，也是把注释规则变成硬约束的关键。
       老产物没有 digest 行（rev117）时跳过该判据，不误伤。
    """
    if artifact_rev is None:
        reason = "legacy_no_fingerprint"
    elif artifact_rev != current_rev:
        reason = "rev_mismatch"
    elif (
        artifact_digest
        and current_digest
        and artifact_digest != current_digest
    ):
        reason = "digest_mismatch"
    else:
        return None
    return {
        "artifact_rev": artifact_rev,
        "current_rev": current_rev,
        "artifact_digest": artifact_digest,
        "current_digest": current_digest,
        "reason": reason,
        "script": script,
    }


def artifact_stale_line(
    artifact_rev: Optional[str],
    current_rev: str = COMPILER_REV,
    script: str = "",
    *,
    artifact_digest: Optional[str] = None,
    current_digest: Optional[str] = None,
) -> str:
    """返回一行 ``ARTIFACT_STALE {...}``；一致时返回空串（保持旧调用签名兼容）。"""
    payload = artifact_stale_payload(
        artifact_rev, current_rev, script,
        artifact_digest=artifact_digest, current_digest=current_digest,
    )
    if payload is None:
        return ""
    return "ARTIFACT_STALE " + json.dumps(payload, ensure_ascii=False) + "\n"


#: 脚本侧 Playwright 预算相对进程超时的收缩量（秒）。
#: 留这段给 Playwright 自己收尾（写 HTML 报告、关浏览器），
#: 否则进程被硬杀，报告与断言结论一起丢。
SCRIPT_BUDGET_GRACE_S = 30

#: 脚本侧预算下限（秒）：慢环境里登录本身就要 30s+，预算再小等于把用例判死在登录阶段。
MIN_SCRIPT_BUDGET_S = 60

#: 引擎侧留量（秒）：作业被取消后还要回写终态、清浏览器，这段不能算进进程超时。
ENGINE_RESERVE_S = 60

#: 进程超时的最小值：要同时容得下「脚本下限」与「收尾余量」。
MIN_RUNNER_TIMEOUT_S = MIN_SCRIPT_BUDGET_S + SCRIPT_BUDGET_GRACE_S  # 90

#: 作业预算的最小值：三层（job > runner > script）能否同时成立的分界线。
MIN_JOB_BUDGET_S = MIN_RUNNER_TIMEOUT_S + ENGINE_RESERVE_S  # 150


class TimeoutBudgetError(ValueError):
    """三层超时无法同时成立（配置过小）。宁可拒绝执行，也不要静默降级。"""


@dataclass(frozen=True)
class TimeoutTiers:
    """三层超时的一次性解算结果。

    rev116-fix（2026-10-19 复核）：此前 job / runner / script 三层是**分开算**的
    （`resolve_runner_timeout` 各算各的、`script_budget_ms` 再取一次下限），
    于是出现「进程 30s、脚本预算 60s」和「显式 7200s 反超 1800s 作业预算」两种倒挂。
    现在只允许经由本函数一次性解算，调用方不得再自行组合。
    """

    job_s: int
    runner_s: int
    script_s: int
    script_ms: int
    #: 显式进程超时是否被作业预算上限裁剪过（为 True 说明配置写了但它不管用）
    clamped: bool = False

    def as_log(self) -> str:
        return (
            f"job={self.job_s}s > runner={self.runner_s}s > script={self.script_s}s"
            + ("（显式值已被作业上限裁剪）" if self.clamped else "")
        )


def script_budget_ms(timeout_s: Any, grace_s: int = SCRIPT_BUDGET_GRACE_S) -> int:
    """把「进程超时」换算成「脚本内 test.setTimeout 预算」（毫秒）。

    rev116（2026-10-19，实测三例假失败）：三层超时必须严格递减——
    引擎作业超时 > runner 进程超时 > 脚本 test.setTimeout。
    历史实现脚本里硬编码 30 分钟、executor 600s 就杀进程，结果是
    「断言都跑完了、job 状态却是 failed」。

    rev116-fix（2026-10-19 复核）：**不变式优先于下限**。
    当进程超时小到「预算下限」与「严格小于」不能同时满足时（≤90s），
    返回 ``进程超时 - 1s`` 并告警 —— 因为此时留 60s 预算本身就是倒挂
    （进程 30s、脚本 60s），比"预算偏小"更危险。
    Web 路径已在 :func:`resolve_timeout_tiers` 拒绝这类配置，够不到这个分支；
    保留兜底只为其他执行链路（如 API 脚本超时 30s）不至于算出倒挂值。
    """
    try:
        total = int(timeout_s)
    except (TypeError, ValueError):
        total = 600
    if total <= 0:
        total = 600
    try:
        grace = int(grace_s)
    except (TypeError, ValueError):
        grace = SCRIPT_BUDGET_GRACE_S
    grace = max(0, grace)

    budget = total - grace
    if budget < MIN_SCRIPT_BUDGET_S:
        logger.warning(
            "[runner] 进程超时 %ss 过小（脚本预算下限 %ss + 收尾 %ss = %ss）："
            "按「严格小于进程超时」取 %ss，本次脚本预算不足以覆盖慢环境登录",
            total, MIN_SCRIPT_BUDGET_S, grace, MIN_RUNNER_TIMEOUT_S, max(1, total - 1),
        )
        return max(1, total - 1) * 1000
    return budget * 1000


def resolve_timeout_tiers(
    job_budget_s: Any = None,
    explicit_runner_s: Any = None,
    grace_s: int = SCRIPT_BUDGET_GRACE_S,
) -> TimeoutTiers:
    """一次性解算三层超时，并**在这里**保证 ``job > runner > script``。

    rev116-fix（2026-10-19 复核）：这是三层唯一的计算入口。

    规则（顺序即优先级）：
      1. 作业预算非法/缺失 → 用默认 1800s（不做静默降级，只补默认）；
      2. 作业预算 < :data:`MIN_JOB_BUDGET_S` → **拒绝执行**（三层装不下）；
      3. 进程超时 = min(显式值, 作业预算 - 引擎留量)；显式值超上限时裁剪并记 ``clamped``；
      4. 进程超时 < :data:`MIN_RUNNER_TIMEOUT_S` → 拒绝执行（多为显式值写得过小）；
      5. 脚本预算 = 进程超时 - 收尾余量，必须 ≥ 脚本下限且严格小于进程超时。

    为什么"拒绝"而不是"夹到最小值就走"：夹值会让配置写的数字与生效数字不一致，
    而这类不一致的表现是"脚本预算 / 进程超时谁先到"这类极难判读的现场。
    配置错误应当直接报错，不该变成运行期谜题。
    """
    default_job = 1800
    try:
        job = int(job_budget_s) if job_budget_s is not None else default_job
    except (TypeError, ValueError):
        job = default_job
    if job <= 0:
        job = default_job

    if job < MIN_JOB_BUDGET_S:
        raise TimeoutBudgetError(
            f"作业预算 {job}s 过小：三层超时需 job > runner > script，"
            f"至少要 {MIN_JOB_BUDGET_S}s（script {MIN_SCRIPT_BUDGET_S}s + 收尾 "
            f"{SCRIPT_BUDGET_GRACE_S}s + 引擎留量 {ENGINE_RESERVE_S}s）。"
            "请调整 job_timeout_seconds，或放宽执行配置。"
        )

    cap = job - ENGINE_RESERVE_S
    requested: Optional[int] = None
    if explicit_runner_s is not None:
        try:
            value = int(explicit_runner_s)
        except (TypeError, ValueError):
            value = 0
        if value > 0:
            requested = value

    clamped = bool(requested is not None and requested > cap)
    runner = min(requested, cap) if requested is not None else cap

    if runner < MIN_RUNNER_TIMEOUT_S:
        raise TimeoutBudgetError(
            f"进程超时 {runner}s 过小：脚本预算至少要 {MIN_SCRIPT_BUDGET_S}s + 收尾 "
            f"{SCRIPT_BUDGET_GRACE_S}s = {MIN_RUNNER_TIMEOUT_S}s。请把 execution_config.timeout "
            f"提到 ≥{MIN_RUNNER_TIMEOUT_S}s（当前上限受作业预算 {job}s 约束）。"
        )

    try:
        grace = max(0, int(grace_s))
    except (TypeError, ValueError):
        grace = SCRIPT_BUDGET_GRACE_S

    script = runner - grace
    if script < MIN_SCRIPT_BUDGET_S:
        raise TimeoutBudgetError(
            f"脚本预算 {script}s 低于下限 {MIN_SCRIPT_BUDGET_S}s（进程超时 {runner}s - 收尾 {grace}s），"
            "请调大进程超时或调小收尾余量。"
        )

    # 不变式自检：任何一条不成立都说明上面算错了，不能带着倒挂的值往下跑
    if not job > runner > script:
        raise TimeoutBudgetError(
            f"三层超时未严格递减：job={job}s, runner={runner}s, script={script}s"
        )

    return TimeoutTiers(
        job_s=job, runner_s=runner, script_s=script, script_ms=script * 1000, clamped=clamped
    )


def _get_npx_cmd() -> list[str]:
    """
    获取平台相关的 npx 命令。

    Windows 上 asyncio.create_subprocess_exec 不经过 shell，
    需要显式使用 npx.cmd 或在 PATH 中能找到 .cmd 文件。
    """
    if os.name == "nt":  # Windows
        return ["npx.cmd"]
    return ["npx"]


def _candidate_node_modules(workspace_dir: Path) -> list[Path]:
    """返回可复用的 node_modules 目录，兼容历史 workspace 路径。"""
    backend_root = Path(__file__).resolve().parents[3]
    candidates = [
        workspace_dir / "node_modules",
        backend_root / "workspace" / "api" / "node_modules",
    ]
    seen: set[Path] = set()
    result: list[Path] = []
    for candidate in candidates:
        resolved = candidate.resolve()
        if resolved.exists() and resolved not in seen:
            seen.add(resolved)
            result.append(resolved)
    return result


def _get_playwright_cmd(workspace_dir: Path) -> list[str]:
    """优先使用已安装的 Playwright CLI，避免 npx 临时环境找不到依赖。"""
    bin_name = "playwright.cmd" if os.name == "nt" else "playwright"
    for node_modules in _candidate_node_modules(workspace_dir):
        local_cmd = node_modules / ".bin" / bin_name
        if local_cmd.exists():
            return [str(local_cmd)]
    return [*_get_npx_cmd(), "playwright"]


def _ensure_node_in_path(env: dict[str, str]) -> dict[str, str]:
    """确保 PATH 包含常见的 Node.js 安装目录。"""
    node_paths = [
        r"C:\Program Files\nodejs",
        r"C:\Program Files (x86)\nodejs",
        os.path.expanduser(r"~\AppData\Roaming\npm"),
        "/usr/local/bin",
        "/usr/bin",
    ]
    current_path = env.get("PATH", "")
    # 只添加当前 PATH 中不存在的路径
    paths_to_add = [p for p in node_paths if p not in current_path]
    if paths_to_add:
        env = {**env, "PATH": os.pathsep.join(paths_to_add + [current_path])}
    return env


def _read_self_reflect_status(script_path: Path) -> str | None:
    """读取脚本目录的 self_reflect_result.json 自评状态（webwright 模式产物）。

    rev35（P0 归因）：脚本自评 failed（业务步骤未通过但进程退出码 0）时，
    HTTP 运行链（WebTestService → PlaywrightRunner）不得按退出码误判成功。
    """
    data = _read_self_reflect_data(script_path)
    if not data:
        return None
    status = data.get("execution_status")
    return status if isinstance(status, str) else None


def _read_self_reflect_data(script_path: Path) -> dict | None:
    """读取 self_reflect_result.json 完整内容（rev51：统计映射需要步骤数）。

    契约扩展（rev51）：脚本除 execution_status 外可写入结构化统计——
    {"execution_status": "passed", "total": 20, "passed": 20, "failed": 0, "skipped": 0}，
    供 PlaywrightRunner 映射到 web_test_runs 运行统计（total/passed/failed/skipped）。

    rev51-fix（真实 E2E 暴露）：脚本执行 cwd 为 workspace 根（run_with_job cwd），
    自评文件可能写在 workspace 根而非 script_path.parent（workspace/tests/）——
    两处候选目录都检查；且 tests/ 目录可能存在**历史残留**（旧 webwright 格式
    total_steps/passed_steps，无新契约字段），**优先选择含新契约统计字段的
    本次执行产物**，避免旧数据掩盖统计映射。
    """
    best: dict | None = None
    for cand in _self_reflect_candidates(script_path):
        sr = cand / "self_reflect_result.json"
        if not sr.exists():
            continue
        try:
            data = json.loads(sr.read_text(encoding="utf-8"))
        except Exception:
            continue
        if not isinstance(data, dict):
            continue
        if any(k in data for k in ("total", "passed", "failed")) and "total_steps" not in data:
            return data  # 新契约结构化统计优先
        if best is None:
            best = data
    return best


def _find_self_reflect_dir(script_path: Path) -> Path | None:
    """定位本次执行 self_reflect 产物所在目录（用于 report_path）。

    优先新契约统计格式所在目录；无则回退第一个存在 self_reflect 的候选目录。
    """
    first: Path | None = None
    for cand in _self_reflect_candidates(script_path):
        sr = cand / "self_reflect_result.json"
        if not sr.exists():
            continue
        try:
            data = json.loads(sr.read_text(encoding="utf-8"))
        except Exception:
            data = None
        if isinstance(data, dict) and any(k in data for k in ("total", "passed", "failed")) \
                and "total_steps" not in data:
            return cand
        if first is None:
            first = cand
    return first


def _read_text_tail(path: Path, limit: int = 64 * 1024) -> str:
    """兜底：直接从落盘日志读尾部（process_guard 未附输出时使用）。"""
    try:
        if not path.exists():
            return ""
        size = path.stat().st_size
        with open(path, "rb") as fh:
            if size > limit:
                fh.seek(size - limit)
            return fh.read().decode("utf-8", errors="replace")
    except OSError:  # pragma: no cover
        return ""


def _partial_output(raw: "bytes | str | None", limit: int = 4000) -> str:
    """超时现场文本化：保留**末尾** limit 字符。

    Playwright 的统计行（`N passed/N failed`）与失败栈都在输出末尾，
    而挂死前的最后一段日志正是定位挂死点的关键证据，因此截断保留尾部。
    """
    if not raw:
        return ""
    text = (raw.decode("utf-8", errors="replace")
            if isinstance(raw, (bytes, bytearray)) else str(raw)).strip()
    if not text:
        return ""
    if len(text) > limit:
        return "...(前文省略)...\n" + text[-limit:]
    return text


def _self_reflect_candidates(script_path: Path) -> list[Path]:
    """self_reflect_result.json 候选目录：脚本同目录（tests/）与 workspace 根。"""
    return [script_path.parent, script_path.parent.parent]


class PlaywrightRunner:
    """Playwright 测试异步运行器"""

    def __init__(self, workspace_dir: Path):
        self.workspace_dir = workspace_dir.resolve()
        self.tests_dir = self.workspace_dir / "tests"
        self.tests_dir.mkdir(parents=True, exist_ok=True)

    async def run(
        self,
        script_path: Path,
        config: Optional[Dict[str, Any]] = None,
        timeout: int = 600,
    ) -> RunnerResult:
        """
        异步执行单个 Playwright 测试脚本。

        Args:
            script_path: 测试脚本路径（相对于 workspace_dir 或绝对路径）
            config: 执行配置 {base_url, reporter, env, ...}
            timeout: 超时时间（秒）

        Returns:
            RunnerResult
        """
        start_time = datetime.now(timezone.utc)
        config = config or {}
        proc: Optional[asyncio.subprocess.Process] = None

        # 环境变量：确保 PATH 包含 Node.js
        env = _ensure_node_in_path({**os.environ})
        npm_cache_dir = self.workspace_dir / ".npm-cache"
        npm_cache_dir.mkdir(parents=True, exist_ok=True)
        env["npm_config_cache"] = str(npm_cache_dir)
        env["NPM_CONFIG_CACHE"] = str(npm_cache_dir)
        node_modules_dirs = _candidate_node_modules(self.workspace_dir)
        if node_modules_dirs:
            existing_node_path = env.get("NODE_PATH", "")
            env["NODE_PATH"] = os.pathsep.join(
                [str(path) for path in node_modules_dirs]
                + ([existing_node_path] if existing_node_path else [])
            )
            existing_path = env.get("PATH", "")
            env["PATH"] = os.pathsep.join(
                [str(path / ".bin") for path in node_modules_dirs]
                + ([existing_path] if existing_path else [])
            )
        if config.get("base_url"):
            env["API_BASE_URL"] = config["base_url"]
        if "html" in str(config.get("reporter", "list")):
            env["CI"] = "1"
        env_vars = config.get("env") or config.get("environment_variables")
        if env_vars:
            env.update(env_vars)

        # rev116（2026-10-19）：把**本次进程超时**下发给脚本，让 Playwright 的
        # `test.setTimeout` 严格小于进程预算。否则脚本自带 30 分钟预算、执行器 600s
        # 就杀进程 —— 现象是"断言都跑完了、job 状态却是 failed"（实测 3 例）。
        # 留 grace 给 Playwright 收尾（写 HTML 报告、关浏览器）后再被杀。
        try:
            _grace_s = int(os.environ.get("WEB_TEST_BUDGET_GRACE_S", SCRIPT_BUDGET_GRACE_S) or SCRIPT_BUDGET_GRACE_S)
            env["WEB_TEST_BUDGET_MS"] = str(script_budget_ms(timeout, _grace_s))
        except (TypeError, ValueError):
            # grace 配置非法时不阻塞执行：按默认余量换算
            env["WEB_TEST_BUDGET_MS"] = str(script_budget_ms(timeout))

        playwright_cmd = _get_playwright_cmd(self.workspace_dir)

        # 1. 验证 Playwright CLI 可用
        # rev32：Windows 上预检同样经 Job Object（run_with_job），
        # 避免 node_modules/.bin 中的本地 CLI（可写 workspace 来源）绕过 B3 资源限制；
        # POSIX 保持原 create_subprocess_exec 语义。
        #
        # rev115（2026-10-16，实测 TR-445/446）：预检超时原为 10s，但 Windows 上
        # playwright.cmd 是批处理壳（cmd.exe → node），冷启动实测基线
        # 6.5~7.3s（本机 `playwright.cmd --version` 连测三次：7164/6533/7277 ms），
        # 再加 Job Object 创建开销与机器并发负载，就会**随机越过 10s**
        # ——现象是正式通道整轮失败且 stdout_len=0、报「Playwright CLI 检查超时」，
        # 与用例本身、被测系统都无关。预检的目的只是"CLI 存在且可执行"，
        # 不该比它探测的对象还紧，故放宽到 30s（约 4 倍余量）。
        PRECHECK_TIMEOUT_S = 30
        try:
            if os.name == "nt":
                from app.agents.tools.web.process_guard import run_with_job

                precheck = await asyncio.to_thread(
                    run_with_job,
                    [*playwright_cmd, "--version"],
                    str(self.workspace_dir),
                    env,
                    PRECHECK_TIMEOUT_S,
                    False,
                )
                rc = precheck.returncode
                err_text = (precheck.stderr or b"").decode("utf-8", errors="replace")
            else:
                playwright_proc = await asyncio.create_subprocess_exec(
                    *playwright_cmd, "--version",
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                    env=env,
                )
                stdout, stderr = await asyncio.wait_for(
                    playwright_proc.communicate(), timeout=PRECHECK_TIMEOUT_S
                )
                rc = playwright_proc.returncode
                err_text = stderr.decode("utf-8", errors="replace")
            if rc != 0:
                return RunnerResult(
                    success=False,
                    error_message=f"Playwright CLI 不可用: {err_text}",
                )
        except subprocess.TimeoutExpired:
            return RunnerResult(
                success=False,
                error_message=f"Playwright CLI 检查超时（预检 {PRECHECK_TIMEOUT_S}s；"
                              "多为 CLI 冷启动慢或机器负载高，非用例问题）",
            )
        except asyncio.TimeoutError:
            return RunnerResult(
                success=False,
                error_message=f"Playwright CLI 检查超时（预检 {PRECHECK_TIMEOUT_S}s；"
                              "多为 CLI 冷启动慢或机器负载高，非用例问题）",
            )
        except Exception as e:
            return RunnerResult(success=False, error_message=f"Playwright CLI 检查失败: {e}")
# pylint: disable  MS80OmFIVnBZMlhscm9ua3VMazZSbTFrUnc9PTo4MmJmMzg4Yg==

        # 2. 解析脚本路径
        if not script_path.is_absolute():
            script_path = self.workspace_dir / script_path

        if not script_path.exists():
            return RunnerResult(
                success=False,
                error_message=f"脚本文件不存在: {script_path}",
            )

        relative_path = script_path.relative_to(self.workspace_dir)

        # rev117（P0-2）：产物指纹比对。旧产物（或没有指纹的历史脚本）一律显式告警，
        # 严格模式下直接拒绝执行 —— 实测代价：TR-703 用旧产物跑满 1745s 被硬杀且无 TEST_BUDGET，
        # TR-725 新产物 1710s 自行收尾，两者从结果上无法区分，白烧 29 分钟。
        artifact_rev = read_artifact_rev(script_path)
        artifact_digest = read_artifact_digest(script_path)
        stale_payload = artifact_stale_payload(
            artifact_rev, COMPILER_REV, relative_path.as_posix(),
            artifact_digest=artifact_digest, current_digest=compiler_source_digest(),
        )
        artifact_stale = (
            "ARTIFACT_STALE " + json.dumps(stale_payload, ensure_ascii=False) + "\n"
            if stale_payload else ""
        )
        artifact_note = ""
        if stale_payload:
            reason = stale_payload["reason"]
            stale_msg = (
                f"产物版本不一致（{reason}）：脚本内 COMPILER_REV={artifact_rev or '(无指纹)'}"
                f"/DIGEST={artifact_digest or '(无)'}，当前编译器 {COMPILER_REV}"
                f"/{stale_payload['current_digest']}。该脚本可能不含最新修复"
                "（预算下发/取帧/看门狗），结论按「旧产物」看待，建议重新生成后再执行。"
            )
            mode = resolve_strict_mode()
            # auto 的不对称语义：无指纹（历史存量）放行；打过指纹却版本倒退必须拒绝。
            block = should_block_stale(reason, mode)
            logger.warning("[PlaywrightRunner] %s（mode=%s, blocked=%s）", stale_msg, mode, block)
            if block:
                return RunnerResult(
                    success=False,
                    stdout=artifact_stale,
                    error_message=(
                        f"[artifact_stale] {stale_msg} 已拒绝执行"
                        f"（WEB_TEST_STRICT_ARTIFACT={mode}, reason={reason}）。"
                    ),
                )
            artifact_note = artifact_stale

        # rev95（执行期可观测性 / 超时快照）：子进程输出**实时落盘**到脚本同目录。
        # 10-12 实测教训：只走 PIPE 时，超时被 ProcessGuard 杀掉后 stdout 为空、
        # artifacts 目录为空，**挂死点无法定位**（602s 超时却拿不到一行输出）。
        # 日志每次执行覆盖，上一份改名为 *.prev 供改动前后 A/B 对比。
        # 注意：文件名不以 .spec/.test 结尾，不会被 playwright test 收集为用例。
        exec_log = script_path.parent / f"{script_path.stem}.exec.log"
        exec_log_err = script_path.parent / f"{script_path.stem}.exec.err.log"
        for _log_p in (exec_log, exec_log_err):
            try:
                if _log_p.exists():
                    _log_p.replace(_log_p.with_suffix(_log_p.suffix + ".prev"))
            except OSError:  # pragma: no cover - 备份失败不影响执行
                pass

        # 3. 根据文件扩展名确定执行方式
        # rev53（报告目录隔离）：python 脚本在工作目录下执行会导致 self_reflect/
        # 截图等产物写入共享 workspace 根，报告打包 rglob('*') 把历史残留一并
        # 打包（实测 284MB）。python 分支改为**脚本所在目录**为执行 cwd
        # （HTTP 链脚本现已放置于每次 run 的专属子目录），产物天然隔离；
        # 非 python（Playwright TS）仍需 workspace 根（配置文件/依赖解析）。
        is_python_script = script_path.suffix == '.py'
        reporter = config.get("reporter", "list")
        exec_cwd = str(self.workspace_dir)
        if is_python_script:
            # Python 脚本：使用 python 命令直接执行（绝对路径 + 脚本目录 cwd）
            exec_cwd = str(script_path.parent)
            cmd = [
                sys.executable or "python",
                str(script_path),
            ]
            logger.info(
                "[PlaywrightRunner] 检测到 Python 脚本，使用 python 执行（cwd=脚本目录）: %s",
                script_path,
            )
        else:
            # TypeScript/JavaScript 脚本：使用 Playwright 执行
            cmd = [
                *playwright_cmd, "test",
                relative_path.as_posix(),
                f"--reporter={reporter}",
            ]

        # 为 HTML 报告指定独立的输出目录，避免批量执行时报告被覆盖
        # 注意：Python 脚本不支持 Playwright HTML reporter
        report_dir_name = None
        json_report_path = None
        if not is_python_script and "html" in reporter:
            report_dir_name = f"playwright-report-{int(time.time() * 1000)}-{os.urandom(4).hex()}"
            report_dir = self.workspace_dir / report_dir_name
            env["PLAYWRIGHT_HTML_OUTPUT_DIR"] = str(report_dir)
            json_report_path = self.workspace_dir / f"{report_dir_name}.json"
            env["PLAYWRIGHT_JSON_OUTPUT_NAME"] = str(json_report_path)
            if "json" not in reporter.split(","):
                reporter = f"{reporter},json"
                cmd[3] = f"--reporter={reporter}"
            cmd.extend(["--output", f"test-results-{report_dir_name}"])
            logger.info(
                "[PlaywrightRunner] 使用独立报告目录: %s", report_dir_name
            )

        logger.info(
            "[PlaywrightRunner] 执行命令: %s, 工作目录: %s",
            " ".join(cmd),
            exec_cwd,
        )

        # 4. 启动异步子进程
        # rev31（执行治理层 2b-B3）：Windows 上统一经 Job Object 资源限制
        # （run_with_job：内存/活动进程上限 + KILL_ON_JOB_CLOSE + 超时杀整棵进程树），
        # 覆盖 HTTP 执行面（web-tests run / api-tests run 等经 PlaywrightRunner 的路径）；
        # POSIX 保持原 create_subprocess_exec 语义。
        duration_ms = int(
            (datetime.now(timezone.utc) - start_time).total_seconds() * 1000
        )
        try:
            if os.name == "nt":
                from app.agents.tools.web.process_guard import run_with_job

                completed = await asyncio.to_thread(
                    run_with_job, cmd, exec_cwd, env, timeout, False,
                    stdout_path=str(exec_log), stderr_path=str(exec_log_err),
                )
                stdout = (completed.stdout or b"").decode("utf-8", errors="replace")
                stderr = (completed.stderr or b"").decode("utf-8", errors="replace")
                returncode = completed.returncode
            else:
                proc = await asyncio.create_subprocess_exec(
                    *cmd,
                    cwd=exec_cwd,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                    env=env,
                )
                # 5. 等待执行完成（带超时）
                stdout_bytes, stderr_bytes = await asyncio.wait_for(
                    proc.communicate(), timeout=timeout
                )
                stdout = stdout_bytes.decode("utf-8", errors="replace")
                stderr = stderr_bytes.decode("utf-8", errors="replace")
                returncode = proc.returncode
        except asyncio.TimeoutError:
            logger.warning("[PlaywrightRunner] 执行超时，正在终止子进程...")
            if proc.returncode is None:
                try:
                    proc.kill()
                    await asyncio.wait_for(proc.wait(), timeout=5)
                except Exception:
                    pass
            duration_ms = int(
                (datetime.now(timezone.utc) - start_time).total_seconds() * 1000
            )
            return RunnerResult(
                success=False,
                stdout=artifact_note,
                error_message=f"测试执行超时（超过 {timeout} 秒）",
                duration_ms=duration_ms,
            )
        except subprocess.TimeoutExpired as exc:
            # run_with_job 超时：TerminateJobObject 已杀整棵进程树；
            # rev95：现场由 process_guard 从执行日志回读并附在异常上（exc.stdout），
            # 落到 RunnerResult —— 让 test_run_script_jobs.stdout / error_message
            # 里能直接看到「挂死前最后做了什么」，不再出现「超时但零输出」。
            logger.warning("[PlaywrightRunner] 执行超时（Job Object 已终止进程树）")
            duration_ms = int(
                (datetime.now(timezone.utc) - start_time).total_seconds() * 1000
            )
            partial = _partial_output(getattr(exc, "stdout", None)) \
                or _partial_output(_read_text_tail(exec_log))
            log_size = exec_log.stat().st_size if exec_log.exists() else 0
            base_msg = (
                f"测试执行超时（超过 {timeout} 秒）。"
                f"执行期输出已落盘：{exec_log}（{log_size} 字节）"
            )
            return RunnerResult(
                success=False,
                stdout=artifact_note + partial,
                error_message=(f"{base_msg}；现场输出尾部：\n{partial}" if partial
                               else f"{base_msg}，但无可读输出"),
                duration_ms=duration_ms,
            )
        except Exception as e:
            duration_ms = int(
                (datetime.now(timezone.utc) - start_time).total_seconds() * 1000
            )
            return RunnerResult(
                success=False,
                error_message=f"启动子进程失败: {e}",
                duration_ms=duration_ms,
            )
# fmt: off  Mi80OmFIVnBZMlhscm9ua3VMazZSbTFrUnc9PTo4MmJmMzg4Yg==

        # 检查报告（支持 "list,html" 等多 reporter 组合）
        # 注意：Python 脚本不支持 Playwright HTML reporter，跳过 HTML 报告检查；
        # rev37（P1-2）：Python/webwright 脚本产物目录（含 self_reflect_result.json）
        # 仍设置 report_path，使 HTTP 报告链（index.html / ZIP / 附件）可达。
        report_path = None
        if not is_python_script and "html" in reporter:
            # 使用命令中指定的报告目录名称
            if report_dir_name:
                report_dir = self.workspace_dir / report_dir_name
                if (report_dir / "index.html").exists():
                    report_path = str(report_dir)
            # 兼容：检查默认目录
            if not report_path:
                default_dir = self.workspace_dir / "playwright-report"
                if (default_dir / "index.html").exists():
                    report_path = str(default_dir)
        elif is_python_script:
            # Python 脚本：产物目录 = 脚本所在目录或 workspace 根（rev51-fix：
            # 脚本 cwd 为 workspace 根，自评/截图可能落在任一候选目录）；
            # 仅当含 self_reflect_result.json（webwright 自评产物）时启用报告链；
            # 优先本次执行（新契约统计）所在目录，避免历史残留目录。
            sr_dir = _find_self_reflect_dir(script_path)
            if sr_dir is not None:
                report_path = str(sr_dir)
                logger.info(
                    "[PlaywrightRunner] rev37/51：Python 脚本产物目录设为报告路径: %s",
                    report_path,
                )

        # JSON reporter 保存测试级结果和断言步骤；无法生成时回退到 list 输出。
        result_summary = self._parse_playwright_json_report(json_report_path)
        if json_report_path:
            try:
                json_report_path.unlink(missing_ok=True)
            except OSError:
                logger.warning("[PlaywrightRunner] 无法清理 JSON 报告: %s", json_report_path)
        if not result_summary:
            result_summary = self._parse_summary(stdout)

        success = returncode == 0
        # rev50（真实环境暴露）：error_message 可能在整个作用域从未赋值——
        # rc≠0 且无 self_reflect 的失败路径进入 else 分支时 `error_message or ...`
        # 会触发 UnboundLocalError，掩盖真实失败原因。先显式初始化。
        error_message: str | None = None
        # rev35（P0 归因）+ rev51（统计映射）：Python 脚本（webwright 模式）执行后
        # 读取 self_reflect_result.json——
        #  - 自评 failed（业务步骤未通过但进程退出码 0）→ 不得判定成功；
        #  - 自评可携带结构化统计（total/passed/failed/skipped）→ 合并进
        #    result_summary，使 web_test_runs 运行统计与脚本步骤数一致（如 20/20）。
        _sr_data = None
        if is_python_script:
            _sr_data = _read_self_reflect_data(script_path)
            _sr_status = _sr_data.get("execution_status") if _sr_data else None
            if _sr_status == "failed" and success:
                success = False
                error_message = (
                    "脚本自评 failed（业务步骤未全部通过，进程退出码为 0），"
                    "详见报告 self_reflect_result.json"
                )
                logger.warning(
                    "[PlaywrightRunner] rev35：脚本自评 failed，判定为失败（退出码 0 不视为通过）"
                )
            if _sr_data:
                _sr_stats = {k: int(v) for k, v in _sr_data.items()
                             if k in ("total", "passed", "failed", "skipped")
                             and isinstance(v, (int, float))}
                if _sr_stats.get("total") or _sr_stats.get("passed") or _sr_stats.get("failed"):
                    result_summary = {**result_summary, **_sr_stats}
                    logger.info(
                        "[PlaywrightRunner] rev51：self_reflect 统计映射到运行统计: %s", _sr_stats
                    )
        if success:
            error_message = None
        else:
            # Playwright 错误通常在 stdout 中，尝试提取第一个失败的测试错误
            error_message = error_message or (self._extract_error(stdout)
                                              or stderr[:2000] or stdout[:2000]
                                              or "Playwright 测试执行失败（无详细错误信息）")

        logger.info(
            "[PlaywrightRunner] 执行完成, returncode=%s, duration_ms=%s, "
            "passed=%s, failed=%s",
            returncode,
            duration_ms,
            result_summary.get("passed", 0),
            result_summary.get("failed", 0),
        )

        return RunnerResult(
            success=success,
            stdout=artifact_note + stdout,
            stderr=stderr,
            returncode=returncode,
            report_path=report_path,
            duration_ms=duration_ms,
            error_message=error_message,
            result_summary=result_summary,
        )

    def _parse_summary(self, stdout: str) -> Dict[str, int]:
        """从 Playwright list reporter 输出解析测试统计。"""
        result = {"total": 0, "passed": 0, "failed": 0, "skipped": 0}

        # 匹配形如 "6 passed (2.5s)" 或 "2 failed, 4 passed"
        summary_match = re.search(
            r"(\d+)\s+passed",
            stdout,
        )
        if summary_match:
            result["passed"] = int(summary_match.group(1))

        failed_match = re.search(r"(\d+)\s+failed", stdout)
        if failed_match:
            result["failed"] = int(failed_match.group(1))

        skipped_match = re.search(r"(\d+)\s+skipped", stdout)
        if skipped_match:
            result["skipped"] = int(skipped_match.group(1))
# type: ignore  My80OmFIVnBZMlhscm9ua3VMazZSbTFrUnc9PTo4MmJmMzg4Yg==

        total_match = re.search(r"Total:\s+(\d+)\s+test", stdout)
        if total_match:
            result["total"] = int(total_match.group(1))
        else:
            result["total"] = result["passed"] + result["failed"] + result["skipped"]

        return result

    @staticmethod
    def _parse_playwright_json_report(report_path: Path | None) -> Dict[str, Any]:
        """Extract a compact, report-safe view from Playwright's JSON reporter."""
        if not report_path or not report_path.exists():
            return {}
        try:
            report = json.loads(report_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            logger.warning("[PlaywrightRunner] JSON 报告解析失败: %s", exc)
            return {}

        tests: list[dict[str, Any]] = []

        def error_text(error: Any) -> str:
            if isinstance(error, dict):
                return str(error.get("message") or error.get("value") or "测试失败")
            return str(error)

        def collect(suite: Any) -> None:
            if not isinstance(suite, dict):
                return
            for spec in suite.get("specs") or []:
                if not isinstance(spec, dict):
                    continue
                for test in spec.get("tests") or []:
                    if not isinstance(test, dict):
                        continue
                    result = (test.get("results") or [{}])[0] or {}
                    steps = []
                    for step in result.get("steps") or []:
                        if not isinstance(step, dict):
                            continue
                        failed = bool(step.get("error"))
                        steps.append({
                            "title": str(step.get("title") or "未命名步骤"),
                            "status": "failed" if failed else "passed",
                            "duration_ms": int(step.get("duration") or 0),
                            "error": error_text(step["error"]) if failed else None,
                        })
                    tests.append({
                        "title": str(spec.get("title") or "未命名测试"),
                        "status": str(result.get("status") or "skipped"),
                        "duration_ms": int(result.get("duration") or 0),
                        "errors": [error_text(item) for item in result.get("errors") or []],
                        "steps": steps,
                    })
            for child in suite.get("suites") or []:
                collect(child)

        for suite in report.get("suites") or []:
            collect(suite)

        if not tests:
            return {}
        result = {"total": len(tests), "passed": 0, "failed": 0, "skipped": 0, "tests": tests}
        for test in tests:
            if test["status"] == "passed":
                result["passed"] += 1
            elif test["status"] == "failed":
                result["failed"] += 1
            else:
                result["skipped"] += 1
        return result

    def _extract_error(self, stdout: str) -> Optional[str]:
        """从 Playwright 输出中提取第一个失败的测试错误信息。"""
        error_line = re.search(
            r"^\s*(Error|TimeoutError|AssertionError|Test timeout)[^\n]{0,300}",
            stdout,
            re.MULTILINE | re.IGNORECASE,
        )
        if error_line:
            return error_line.group(0).strip()

        # Playwright 失败格式示例:
        #   1) [webkit] › file.spec.ts:23:7 › 描述 ─────
        #     Error: expect(...).toBeVisible() 调用超时
        #
        # 尝试匹配失败测试的错误块
        failed_test_pattern = re.search(
            r"^\s*\d+\)\s+.*?─+\s*\n(.*?)\n\s*(?:\d+\)|Error|Test timeout|TimeoutError|AssertionError)",
            stdout,
            re.MULTILINE | re.DOTALL,
        )
        if failed_test_pattern:
            error_block = failed_test_pattern.group(1).strip()
            # 限制长度
            if len(error_block) > 500:
                error_block = error_block[:500] + "..."
            return error_block

        return None
