"""API 测试脚本模板的版本指纹（P2：脚本资产跟随模板版本）。

**为什么需要这个模块**

脚本是**生成期一次性产物**：``api_case_link_service`` 把模板渲染成 TS 后传到对象存储，
执行时按 ``script_path`` 原样下载。模板改进（补 query 参数、加信封断言、改分页契约）
不会回流到已经生成过的资产 —— 于是同一个 template 的新老产物在库里长得一模一样，
"这条用例到底按哪版规则判的"只能靠猜。历史上就吃过这个亏：模板补了必填 query，
老脚本仍打残缺 URL，结论仍然是"通过"。

**与 web 侧的关系**

``web_case_compiler.COMPILER_REV`` + ``compiler_source_digest()`` 是同一套思路，
这里刻意保持同名概念与同样的三种 stale 判据（见 ``stale_reason``），
不另造一套语义 —— 两套语义迟早漂移。

- ``rev``：人手维护的语义版本。改模板**必须** bump。
- ``digest``：模板源码的哈希。用来兜住"改了模板忘了 bump rev"这一档：
  rev 相同但 digest 不同即为 stale。
"""

from __future__ import annotations

import hashlib
import re
from typing import Iterable, Mapping, Optional

#: 脚本模板语义版本。**改模板必须 bump**，并同步正文注释里的变更说明。
#:
#: 变更记录：
#:   api-rev1 (2026-10-28) 首版指纹：模板、样本值、信封断言、分页契约计入 digest。
API_SCRIPT_TEMPLATE_REV = "api-rev1"

#: 指纹行格式（写在脚本首行，纯注释，不影响执行）。
_FINGERPRINT_LINE = "// API_SCRIPT_TEMPLATE_REV: {rev} DIGEST: {digest}"

#: 解析已有脚本里的指纹行。
_FINGERPRINT_RE = re.compile(
    r"API_SCRIPT_TEMPLATE_REV:\s*(?P<rev>[A-Za-z0-9._-]+)\s+DIGEST:\s*(?P<digest>[0-9a-f]{6,64})"
)

#: 产物过期原因码（与 web 侧 ``artifact_stale_payload`` 对齐）。
STALE_LEGACY = "legacy_no_fingerprint"
STALE_REV = "rev_mismatch"
STALE_DIGEST = "digest_mismatch"


def template_digest(chunks: Iterable[str]) -> str:
    """按顺序哈希模板源码片段，返回 12 位摘要。

    片段之间插入 ``\\0`` 分隔符，避免 ``("ab","c")`` 与 ``("a","bc")`` 撞成同一个值。
    """
    hasher = hashlib.sha256()
    for chunk in chunks:
        hasher.update(chunk.encode("utf-8"))
        hasher.update(b"\0")
    return hasher.hexdigest()[:12]


def fingerprint_line(rev: str = API_SCRIPT_TEMPLATE_REV, digest: Optional[str] = None) -> str:
    """生成脚本首行的指纹注释。``digest`` 缺省时用 ``template_digest([])`` 之外的空值不合法，
    因此要求调用方显式传入（避免生成"半截指纹"让 stale 判定永远失败）。"""
    if not digest:
        raise ValueError("fingerprint_line 需要显式 digest")
    return _FINGERPRINT_LINE.format(rev=rev, digest=digest)


def parse_fingerprint(script: str) -> tuple[Optional[str], Optional[str]]:
    """从脚本正文解析 ``(rev, digest)``；没有指纹行返回 ``(None, None)``。"""
    match = _FINGERPRINT_RE.search(script or "")
    if not match:
        return None, None
    return match.group("rev"), match.group("digest")


def stale_reason(
    stamp: Optional[Mapping[str, object]],
    *,
    current_rev: str = API_SCRIPT_TEMPLATE_REV,
    current_digest: Optional[str] = None,
) -> Optional[str]:
    """判定已生成脚本相对当前模板是否过期；不过期返回 ``None``。

    三条判据按"信息量从少到多"排列：

    1. 没有指纹（历史产物）→ ``legacy_no_fingerprint``
    2. rev 不同 → ``rev_mismatch``
    3. rev 相同但 digest 不同 → ``digest_mismatch``（改了模板忘了 bump）
    """
    if not stamp or not stamp.get("rev"):
        return STALE_LEGACY
    if stamp.get("rev") != current_rev:
        return STALE_REV
    stamped_digest = stamp.get("digest")
    if current_digest and stamped_digest and stamped_digest != current_digest:
        return STALE_DIGEST
    return None


def stale_reason_text(reason: Optional[str]) -> str:
    """原因码的中文说明（给人看，不进机器判定）。"""
    return {
        None: "与当前模板一致",
        STALE_LEGACY: "历史产物无版本指纹，按哪版模板生成未知",
        STALE_REV: "模板语义版本已更新",
        STALE_DIGEST: "模板源码已变更但版本号未 bump",
    }.get(reason, reason or "")
