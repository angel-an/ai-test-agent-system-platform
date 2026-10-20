# -*- coding: utf-8 -*-
"""上传类断言的**证据判定**（判定放在服务端 Python，便于单元测试与事后审计）。

背景（复核意见 2026-09-23）：第一版把判定写在浏览器脚本里，且通过条件含
"附件列表没有增加" 或 "出现格式/大小提示" 这类弱证据，存在**假通过**路径：

1. 附件列表没增加可能只是"上传被隔离策略中断 / 页面还没处理完 / 定位错了列表"；
2. 格式断言与大小断言共用一个正则，超大文件若因**格式**问题被拒，会被算成"大小规则验证通过"；
3. 护栏造成的失败被当成了业务规则的成功。

因此这里把判定收敛为**只认对应类型的客户端提示**，其余一律 ``unverified``；
判定所需的原始事实（提示文本、附件块数量、拦截计数、样本字节数）由浏览器脚本采集，
不在浏览器里做结论——这样每个分支都能用单元测试覆盖。
"""

from __future__ import annotations

import json
import re
from typing import Any, Iterable, Mapping

#: 浏览器脚本输出的证据行标记
UPLOAD_MARKER = "UPLOAD_EVIDENCE "

PASS = "pass"
UNVERIFIED = "unverified"
BLOCKED = "blocked"

#: 客户端"格式/类型"类提示（只认这些词才说明格式规则被前端执行）
_FORMAT_PATTERNS = (
    r"格式", r"仅支持", r"不支持", r"文件类型", r"类型不正确", r"后缀", r"扩展名",
    r"extension", r"file ?type", r"invalid ?type", r"accept",
)
#: 客户端"大小"类提示
_SIZE_PATTERNS = (
    r"大小", r"超过", r"超出", r"过大", r"不能大于", r"too ?large", r"size", r"exceed",
)
#: 与规则无关的噪声（网络/中断/上传失败）
_NETWORK_PATTERNS = (
    r"上传失败", r"网络", r"超时", r"中断", r"失败", r"failed", r"error", r"abort",
)


def _hit(text: str, patterns: Iterable[str]) -> bool:
    return any(re.search(p, text, re.IGNORECASE) for p in patterns)


def is_format_message(text: str) -> bool:
    """格式类提示：必须命中格式词，且**不**只是网络/失败噪声。"""
    return _hit(text, _FORMAT_PATTERNS)


def is_size_message(text: str) -> bool:
    return _hit(text, _SIZE_PATTERNS)


def is_network_message(text: str) -> bool:
    return _hit(text, _NETWORK_PATTERNS)


def parse_upload_evidence(stdout: str) -> list[dict[str, Any]]:
    """从执行日志里解析上传证据行（原始事实，不含结论）。"""
    out: list[dict[str, Any]] = []
    for line in (stdout or "").splitlines():
        idx = line.find(UPLOAD_MARKER)
        if idx < 0:
            continue
        try:
            payload = json.loads(line[idx + len(UPLOAD_MARKER):])
        except (ValueError, TypeError):
            continue
        if isinstance(payload, dict):
            out.append(payload)
    return out


def fresh_messages(ev: Mapping[str, Any]) -> list[str]:
    """本次检查**新增**的提示：按"出现次数"做多重集差分。

    只做集合差会漏掉"两次检查产生同样文本"的情况（第二次的相同提示会被当成残留），
    因此这里比较每个文本的出现次数。
    """
    before = [str(x).strip() for x in (ev.get("messages_before") or []) if str(x).strip()]
    after = [str(x).strip() for x in (ev.get("messages_after") or []) if str(x).strip()]
    counts: dict[str, int] = {}
    for text in before:
        counts[text] = counts.get(text, 0) + 1
    fresh: list[str] = []
    for text in after:
        if counts.get(text, 0) > 0:
            counts[text] -= 1
            continue
        fresh.append(text)
    return fresh


def blocked_kind_counts(ev: Mapping[str, Any]) -> dict[str, int]:
    """三类阻断计数（兼容旧证据里没有该字段的情况）。"""
    kinds = ev.get("isolation_blocked_kinds")
    if isinstance(kinds, Mapping):
        return {str(k): int(v or 0) for k, v in kinds.items()}
    total = int(ev.get("isolation_blocks") or 0)
    return {"upload_blocked": total, "non_upload_api_blocked": 0, "unknown_request_blocked": 0}


def environment_failure(ev: Mapping[str, Any]) -> str:
    """是否需要按"环境/隔离失败"处理：业务接口或未知请求被阻断 → 不得作为业务结论。"""
    kinds = blocked_kind_counts(ev)
    api = kinds.get("non_upload_api_blocked", 0)
    unknown = kinds.get("unknown_request_blocked", 0)
    if api or unknown:
        return ("环境/隔离失败：本轮有 %d 个业务接口请求、%d 个未知请求被隔离策略阻断，"
                "页面上下文可能不完整 → 该结果不作为业务规则结论" % (api, unknown))
    return ""


def stage_note(ev: Mapping[str, Any]) -> str:
    """把"没有提示"拆成可区分的阶段原因（复核意见 P1-2/P1-3）。"""
    stages = ev.get("stages") if isinstance(ev.get("stages"), Mapping) else {}
    timed_out_stage = str(ev.get("timed_out_stage") or "")
    if timed_out_stage == "file_selected":
        return "阶段①文件选择事件未观测到（控件可能未收到文件或页面异常）"
    if timed_out_stage == "client_feedback":
        return ("阶段②客户端反馈超时：已观测到文件选择（%sms），但在 %sms 内既无提示也无上传请求"
                % (stages.get("change_ms"), stages.get("feedback_ms")))
    if not timed_out_stage and ev.get("timed_out"):
        return "阶段超时（未记录具体阶段）"
    parts = []
    if stages:
        parts.append("文件选择=%sms" % stages.get("change_ms"))
        parts.append("反馈=%sms" % stages.get("feedback_ms"))
        parts.append("上传请求=%s" % ("有" if stages.get("saw_request") else "无"))
    return "；".join(parts)


def contract_deviation(ev: Mapping[str, Any]) -> dict[str, Any] | None:
    """识别**契约偏差**：客户端行为与已确认口径不一致（复核意见 2026-09-23）。

    - ``size_boundary_rejected``：文档口径"≤ 1M 应接受"，但客户端对**边界内样本**报了超限；
    - ``format_not_accepted``：口径声明该格式受支持，但客户端按格式拒绝。

    这两种情况**既不是通过、也不是普通未验证**：它们是可复现的契约偏差，必须单独登记待人工确认。
    """
    kind = str(ev.get("evidence_kind") or "")
    expect = str(ev.get("expect") or "")
    if expect != "accept":
        return None
    fresh = fresh_messages(ev)
    sample = ev.get("sample") or {}
    size_msgs = [m for m in fresh if is_size_message(m)]
    format_msgs = [m for m in fresh if is_format_message(m) and not is_network_message(m)]
    if kind == "size" and size_msgs:
        return {
            "code": "size_boundary_rejected",
            "sample": sample.get("name"),
            "bytes": sample.get("bytes"),
            "kind": sample.get("kind"),
            "threshold_confirmed": bool(sample.get("threshold_confirmed")),
            "message": size_msgs[0][:80],
        }
    if kind == "format" and format_msgs:
        return {
            "code": "format_not_accepted",
            "sample": sample.get("name"),
            "message": format_msgs[0][:80],
        }
    return None


def classify_upload(ev: Mapping[str, Any]) -> tuple[str, str]:
    """(状态, 可读原因)。**只认对应类型的客户端提示**，其余一律 unverified。"""
    kind = str(ev.get("evidence_kind") or "")
    expect = str(ev.get("expect") or "")
    sample = ev.get("sample") or {}
    sample_name = str(sample.get("name") or "")
    sample_bytes = sample.get("bytes")
    kind_label = str(sample.get("kind") or "")
    threshold_ok = bool(sample.get("threshold_confirmed"))
    fresh = fresh_messages(ev)
    format_msgs = [m for m in fresh if is_format_message(m) and not is_network_message(m)]
    size_msgs = [m for m in fresh if is_size_message(m)]
    network_msgs = [m for m in fresh if m not in format_msgs and m not in size_msgs]
    blocked = blocked_kind_counts(ev).get("upload_blocked", 0)
    chips_before = int(ev.get("chips_before") or 0)
    chips_after = int(ev.get("chips_after") or 0)
    chips_added = chips_after > chips_before
    timed_out = bool(ev.get("timed_out"))
    reset = bool(ev.get("control_reset"))
    accept_attr = str(ev.get("accept_attr") or "")

    facts = (
        "样本=%s(%s字节)｜accept=%s｜附件块 %s→%s｜本次新增提示=%s｜阻断(上传/业务接口/未知)=%s/%s/%s"
        "｜上下文=%s｜阶段[%s]｜等待=%sms%s%s"
        % (sample_name or "(无)", sample_bytes if sample_bytes is not None else "未知",
           accept_attr or "(未设置)", chips_before, chips_after,
           " / ".join(fresh)[:60] or "无",
           blocked_kind_counts(ev).get("upload_blocked", 0),
           blocked_kind_counts(ev).get("non_upload_api_blocked", 0),
           blocked_kind_counts(ev).get("unknown_request_blocked", 0),
           ev.get("context_label") or "未知",
           stage_note(ev) or "—",
           ev.get("waited_ms") or 0, "｜超时未确认" if timed_out else "",
           "" if reset else "｜控件未重置")
    )

    env_failure = environment_failure(ev)
    if env_failure:
        return BLOCKED, env_failure + "｜" + facts
    # 阶段①未发生（控件没收到文件）→ 不是产品结论，单独给出原因
    if str(ev.get("timed_out_stage") or "") == "file_selected":
        return UNVERIFIED, stage_note(ev) + "｜" + facts

    if expect == "reject":
        if sample.get("error"):
            return UNVERIFIED, "未能取得上传控件或夹具（%s），本次不构成规则证据｜%s" % (
                sample.get("error"), facts)
        if kind == "size":
            if size_msgs and not format_msgs:
                note = "" if threshold_ok else "；**临界点未验证**（阈值字节数未确认，用的是明显超大样本）"
                return PASS, "客户端按大小规则拒绝：%s%s｜%s" % (size_msgs[0][:40], note, facts)
            if format_msgs:
                return UNVERIFIED, ("出现的是**格式**提示（%s），不能证明大小规则被前端执行" % format_msgs[0][:30]) + "｜" + facts
            return UNVERIFIED, ("未观测到大小类客户端提示（%s；附件未增加/网络中断都不算证据）"
                                % stage_note(ev)) + "｜" + facts
        if kind == "format":
            if format_msgs and not size_msgs:
                return PASS, "客户端按格式规则拒绝：%s｜%s" % (format_msgs[0][:40], facts)
            if size_msgs:
                return UNVERIFIED, ("出现的是**大小**提示（%s），不能证明格式规则被前端执行" % size_msgs[0][:30]) + "｜" + facts
            return UNVERIFIED, ("未观测到格式类客户端提示（%s；附件未增加/网络中断都不算证据）"
                                % stage_note(ev)) + "｜" + facts
        return UNVERIFIED, "样本类型未知，无法判定｜" + facts

    # accept：客户端模式下**只能**说明"客户端未按规则拒绝"，不能推出"服务端接受"或"导入成功"
    if sample.get("error"):
        head = "未能取得上传控件或夹具（%s）" % sample.get("error")
    elif kind == "size":
        head = ("客户端未报大小错误" if not size_msgs else
                "⚠️ 客户端对**边界内**样本报了大小限制提示（%s）——与文档口径可能不一致，需人工确认"
                % size_msgs[0][:30])
    elif kind == "format":
        head = ("客户端未报格式错误" if not format_msgs else
                "⚠️ 客户端对**声明允许**的格式报了拒绝提示（%s）——与文档口径可能不一致，需人工确认"
                % format_msgs[0][:30])
    else:
        head = "样本类型未知"
    if blocked > 0:
        tail = "；上传请求被隔离策略阻断（未发往服务器）→ 服务端接受与导入结果**未验证**"
    elif chips_added:
        tail = "；控件已把文件放入待上传列表，但缺少上传响应/存储证据 → 服务端接受与导入结果**未验证**"
    else:
        tail = "；无上传响应与存储证据 → 服务端接受与导入结果**未验证**"
    return UNVERIFIED, head + tail + "｜" + facts


def upload_case_items(stdout: str) -> list[dict[str, Any]]:
    """把上传证据行转成与 ``COMPILED_ASSERTION`` 同构的明细项，供结果映射复用。

    契约偏差会以 ``⚠️[契约偏差:<code>]`` 前缀写入 detail，便于报告单独列出。
    """
    items: list[dict[str, Any]] = []
    for ev in parse_upload_evidence(stdout):
        outcome, observed = classify_upload(ev)
        deviation = contract_deviation(ev)
        if deviation:
            observed = "⚠️[契约偏差:%s] %s" % (deviation["code"], observed)
        sample = ev.get("sample") or {}
        items.append({
            "name": "upload_file:%s [%s]" % (ev.get("field") or "", sample.get("name") or ""),
            "outcome": outcome,
            "observed": observed,
            "rule_id": str(ev.get("rule_id") or ""),
            "case_id": str(ev.get("case_id") or ""),
            "deviation": deviation,
        })
    return items


__all__ = [
    "BLOCKED",
    "PASS",
    "UNVERIFIED",
    "UPLOAD_MARKER",
    "classify_upload",
    "contract_deviation",
    "fresh_messages",
    "is_format_message",
    "is_network_message",
    "is_size_message",
    "parse_upload_evidence",
    "upload_case_items",
]
