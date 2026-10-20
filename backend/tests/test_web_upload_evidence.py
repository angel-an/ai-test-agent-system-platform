# -*- coding: utf-8 -*-
"""上传证据判定（服务端分类器）回归测试。

对应复核意见里的"假通过"分支，逐条锁死：

1. 附件列表没增加 / 上传被隔离中断 → 拒绝类断言**不得**通过；
2. 出现格式提示 ≠ 大小规则通过（反之亦然）；
3. 接受类断言在客户端模式下**永远**不能推出"服务端接受"；
4. 连续两次相同提示要能分别归属（多重集差分）；
5. 样本字节数是判定依据之一（阈值未确认时必须写明临界点未验证）。
"""

from __future__ import annotations

import json

from app.services.web_upload_evidence import (
    PASS,
    contract_deviation,
    UNVERIFIED,
    classify_upload,
    fresh_messages,
    parse_upload_evidence,
    upload_case_items,
)


def _ev(**over):
    ev = {
        "check_index": 1,
        "case_id": "TC-1",
        "rule_id": "FR-1",
        "field": "上传文件（企业充值）",
        "evidence_kind": "format",
        "expect": "reject",
        "sample": {"name": "bad.exe", "bytes": 256, "kind": "disallowed_extension",
                   "threshold_confirmed": False},
        "messages_before": [],
        "messages_after": [],
        "chips_before": 0,
        "chips_after": 0,
        "control_reset": True,
        "waited_ms": 500,
        "timed_out": False,
        "isolation_blocks": 0,
    }
    ev.update(over)
    return ev


class TestRejectNeverPassesOnWeakEvidence:
    """复核意见 2：只有"对应类型的客户端提示"才算证据。"""

    def test_format_reject_passes_only_with_format_message(self):
        outcome, reason = classify_upload(_ev(messages_after=["文件格式仅支持：xlsx、xls"]))
        assert outcome == PASS
        assert "格式" in reason and "bad.exe" in reason

    def test_chip_not_added_alone_is_not_evidence(self):
        """附件列表没增加可能只是被中断/没渲染完 —— 不能算产品拒绝。"""
        outcome, reason = classify_upload(_ev(messages_after=[], chips_after=0))
        assert outcome == UNVERIFIED
        assert "未观测到格式类客户端提示" in reason

    def test_isolation_interruption_is_not_evidence(self):
        outcome, reason = classify_upload(_ev(messages_after=["bad.exe 上传失败"], isolation_blocks=1))
        assert outcome == UNVERIFIED
        assert "附件未增加/网络中断都不算证据" in reason

    def test_size_message_does_not_prove_format_rule(self):
        """出现大小提示不能当成格式规则被前端执行。"""
        outcome, reason = classify_upload(
            _ev(evidence_kind="format", messages_after=["文件大小不能超过1M"]))
        assert outcome == UNVERIFIED
        assert "格式" in reason and "大小" in reason

    def test_format_message_does_not_prove_size_rule(self):
        outcome, reason = classify_upload(
            _ev(evidence_kind="size", expect="reject",
                sample={"name": "upload_oversize.xlsx", "bytes": 2 * 1024 * 1024,
                        "kind": "oversize_obvious", "threshold_confirmed": False},
                messages_after=["文件格式仅支持：xlsx、xls"]))
        assert outcome == UNVERIFIED
        assert "不能证明大小规则" in reason

    def test_size_reject_passes_with_size_message_and_flags_boundary(self):
        outcome, reason = classify_upload(
            _ev(evidence_kind="size", expect="reject",
                sample={"name": "upload_oversize.xlsx", "bytes": 2 * 1024 * 1024,
                        "kind": "oversize_obvious", "threshold_confirmed": False},
                messages_after=["文件大小不能超过1M"]))
        assert outcome == PASS
        assert "临界点未验证" in reason

    def test_confirmed_threshold_does_not_claim_boundary_unverified(self):
        outcome, reason = classify_upload(
            _ev(evidence_kind="size", expect="reject",
                sample={"name": "upload_1048577.xlsx", "bytes": 1024 * 1024 + 1,
                        "kind": "boundary_plus", "threshold_confirmed": True},
                messages_after=["文件大小不能超过1M"]))
        assert outcome == PASS
        assert "临界点未验证" not in reason


class TestAcceptNeverProvesServerSide:
    """复核意见 3：客户端模式下"接受类"只能是未验证。"""

    def test_no_error_and_chip_added_is_still_unverified(self):
        outcome, reason = classify_upload(_ev(
            evidence_kind="format", expect="accept",
            sample={"name": "upload_valid.xlsx", "bytes": 1400, "kind": "valid_workbook",
                    "threshold_confirmed": False},
            messages_after=[], chips_after=1))
        assert outcome == UNVERIFIED
        assert "服务端接受与导入结果" in reason
        assert "待上传列表" in reason

    def test_isolated_upload_is_explicitly_attributed_to_isolation(self):
        outcome, reason = classify_upload(_ev(
            evidence_kind="format", expect="accept",
            sample={"name": "upload_valid.xlsx", "bytes": 1400, "kind": "valid_workbook"},
            messages_after=["upload_valid.xlsx 上传失败"], isolation_blocks=1))
        assert outcome == UNVERIFIED
        assert "隔离策略阻断" in reason

    def test_state_reset_is_visible_in_reason(self):
        _outcome, reason = classify_upload(_ev(
            evidence_kind="format", expect="accept", control_reset=False))
        assert "控件未重置" in reason


class TestMessageAttribution:
    """复核意见 5：相同提示连续出现时也要能分别归属。"""

    def test_count_increase_is_attributable(self):
        """出现次数增加 → 能归属到本次（多重集差分的关键）。"""
        msg = "文件格式仅支持：xlsx、xls"
        ev = _ev(messages_before=[msg], messages_after=[msg, msg])
        assert fresh_messages(ev) == [msg]
        assert classify_upload(ev)[0] == PASS

    def test_identical_text_without_count_change_stays_unverified(self):
        """复核意见 5 的边界：同一文本在前后各出现一次时**无法**归属。

        宁可少判，也不能把它当成本次的拒绝证据——否则就是假通过。
        """
        msg = "文件格式仅支持：xlsx、xls"
        ev = _ev(messages_before=[msg], messages_after=[msg])
        assert fresh_messages(ev) == []
        assert classify_upload(ev)[0] == UNVERIFIED

    def test_stale_message_is_not_fresh(self):
        ev = _ev(messages_before=["文件格式仅支持：xlsx、xls"],
                 messages_after=["文件格式仅支持：xlsx、xls", "文件大小不能超过1M"])
        assert fresh_messages(ev) == ["文件大小不能超过1M"]

    def test_timeout_alone_is_unverified(self):
        outcome, reason = classify_upload(_ev(timed_out=True, messages_after=[]))
        assert outcome == UNVERIFIED
        assert "超时未确认" in reason

    def test_timeout_reason_names_the_stage_not_a_verdict(self):
        """复核意见 P1：超时原因必须按阶段写清楚，不能统一写成"客户端未拒绝"。"""
        ev = _ev(timed_out=True, timed_out_stage="client_feedback",
                 stages={"change_ms": 120, "feedback_ms": 12000,
                         "saw_file_selected": True, "saw_request": False, "event_count": 1},
                 messages_after=[])
        outcome, reason = classify_upload(ev)
        assert outcome == UNVERIFIED
        assert "阶段②客户端反馈超时" in reason
        assert "客户端未接受该文件" not in reason

    def test_file_not_selected_is_reported_as_stage_one(self):
        ev = _ev(timed_out=True, timed_out_stage="file_selected",
                 stages={"change_ms": 5000, "feedback_ms": 0, "saw_file_selected": False},
                 messages_after=[])
        outcome, reason = classify_upload(ev)
        assert outcome == UNVERIFIED
        assert "阶段①文件选择事件未观测到" in reason

    def test_stage_timings_are_shown_when_feedback_arrived(self):
        ev = _ev(messages_after=["文件格式仅支持：xlsx、xls"], timed_out=False,
                 stages={"change_ms": 130, "feedback_ms": 400,
                         "saw_file_selected": True, "saw_request": False, "event_count": 2})
        outcome, reason = classify_upload(ev)
        assert outcome == PASS
        assert "文件选择=130ms" in reason and "反馈=400ms" in reason


class TestContractDeviation:
    """复核意见：这两项不是"未验证"，而是可复现的**契约偏差**，必须单独识别与登记。"""

    def test_size_boundary_rejected_is_flagged(self):
        ev = _ev(evidence_kind="size", expect="accept",
                 sample={"name": "upload_1048576.xlsx", "bytes": 1_048_576,
                         "kind": "boundary", "threshold_confirmed": True},
                 messages_after=["文件大小不能超过1M"])
        dev = contract_deviation(ev)
        assert dev and dev["code"] == "size_boundary_rejected"
        assert dev["bytes"] == 1_048_576 and dev["threshold_confirmed"] is True
        item = upload_case_items("UPLOAD_EVIDENCE " + json.dumps(ev, ensure_ascii=False))[0]
        assert item["observed"].startswith("⚠️[契约偏差:size_boundary_rejected]")
        assert item["outcome"] == UNVERIFIED          # 偏差绝不算通过

    def test_format_not_accepted_is_flagged(self):
        ev = _ev(evidence_kind="format", expect="accept",
                 sample={"name": "upload_valid.csv", "bytes": 52, "kind": "valid_csv"},
                 messages_after=["文件格式仅支持：xlsx、xls"])
        dev = contract_deviation(ev)
        assert dev and dev["code"] == "format_not_accepted"
        assert contract_deviation(_ev(evidence_kind="format", expect="reject",
                                      messages_after=["文件格式仅支持：xlsx、xls"])) is None
        assert contract_deviation(_ev(evidence_kind="size", expect="accept",
                                      messages_after=[])) is None

    def test_within_limit_accept_is_not_a_deviation(self):
        """边界内未被拒（只有隔离造成的上传失败）→ 不是契约偏差。"""
        ev = _ev(evidence_kind="size", expect="accept",
                 sample={"name": "upload_1048575.xlsx", "bytes": 1_048_575,
                         "kind": "boundary_minus", "threshold_confirmed": True},
                 messages_after=["upload_1048575.xlsx 上传失败"],
                 isolation_blocked_kinds={"upload_blocked": 1}, isolation_blocks=1)
        assert contract_deviation(ev) is None
        assert classify_upload(ev)[0] == UNVERIFIED


class TestEvidenceParsing:
    def test_parse_and_convert_to_case_items(self):
        stdout = "\n".join([
            "noise",
            "UPLOAD_EVIDENCE " + json.dumps(_ev(messages_after=["文件格式仅支持：xlsx、xls"])),
            "UPLOAD_EVIDENCE {broken",
            "COMPILED_ASSERTION_SUMMARY {}",
        ])
        parsed = parse_upload_evidence(stdout)
        assert len(parsed) == 1
        items = upload_case_items(stdout)
        assert items[0]["case_id"] == "TC-1"
        assert items[0]["outcome"] == PASS
        assert items[0]["name"].startswith("upload_file:")

    def test_missing_control_is_unverified(self):
        stdout = "UPLOAD_EVIDENCE " + json.dumps(
            _ev(sample={"name": "", "error": "no_file_input"}))
        items = upload_case_items(stdout)
        assert items and items[0]["outcome"] == UNVERIFIED

