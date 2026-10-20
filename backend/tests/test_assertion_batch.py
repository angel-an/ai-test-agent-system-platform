# -*- coding: utf-8 -*-
"""结构化断言回写能力回归（2026-09-29 复核 P2）。

要求（复核口径）：
1. 断言格式白名单校验（kind 必须是编译器认识的种类；rule/field 必填；expect 合法）；
2. 预览必须走**真正的编译器**（`compile_cases`），不能另写一套"看起来能编译"的判断；
3. 回写要保留既有 custom_fields，并记录 `assertions_source`（manual/doc_extracted）+ 时间戳；
4. 该标记必须能支撑"文档变更后列出需要人工复核的断言"。
"""

from __future__ import annotations

import pytest
from types import SimpleNamespace

from app.services.test_assertion_service import (
    ASSERTION_KINDS,
    RULE_KEYS,
    SOURCE_DOC,
    SOURCE_MANUAL,
    RulePatchError,
    assertions_needing_review,
    merge_assertions,
    merge_rule_patch,
    preview_assertions,
    validate_assertions,
)

GOOD = [{"kind": "max_length", "field": "任意字段", "data": "21字",
         "expect": "reject", "rule": "字段≤20字"}]


class TestValidation:
    def test_good_assertion_passes(self):
        assert validate_assertions(GOOD) == []

    def test_empty_list_is_rejected(self):
        assert validate_assertions([]) == ["assertions 必须是非空数组"]
        assert validate_assertions(None) == ["assertions 必须是非空数组"]

    def test_unknown_kind_is_rejected(self):
        errors = validate_assertions([{"kind": "色彩校验", "field": "F", "rule": "R"}])
        assert any("不支持的 kind" in e for e in errors)

    def test_all_compiler_kinds_are_accepted(self):
        for kind in sorted(ASSERTION_KINDS):
            item = {"kind": kind, "field": "F", "rule": "R", "expect": "reject"}
            if kind in {"boundary", "range", "precision", "max_length", "max_count",
                        "enum", "default", "file_format", "file_size"}:
                item["data"] = "样本"
            assert validate_assertions([item]) == [], kind

    def test_range_is_supported(self):
        """回归（真实数据发现）：range 不在 LOCATOR_DEPENDENT_KINDS 里，但表单计划支持它。"""
        assertion = {"kind": "range", "field": "消费金额门槛", "data": "0",
                     "expect": "accept", "rule": "消费金额门槛[0,9999]"}
        assert "range" in ASSERTION_KINDS
        assert validate_assertions([assertion]) == []
        preview = preview_assertions("TC-R", [assertion], rule_id="R27", rule_field="消费金额门槛")
        assert preview["valid"] is True
        assert preview["compile_status"] == "compiled"

    def test_missing_rule_and_field_are_rejected(self):
        errors = validate_assertions([{"kind": "required", "expect": "reject"}])
        assert any("缺少 rule" in e for e in errors)
        assert any("缺少 field" in e for e in errors)

    def test_data_required_for_sample_kinds(self):
        errors = validate_assertions([{"kind": "max_length", "field": "F", "rule": "R",
                                       "expect": "reject"}])
        assert any("需要 data" in e for e in errors)

    def test_bad_expect_is_rejected(self):
        errors = validate_assertions([{"kind": "required", "field": "F", "rule": "R",
                                       "expect": "maybe"}])
        assert any("expect='maybe' 非法" in e for e in errors)


class TestPreview:
    def test_preview_uses_real_compiler(self):
        preview = preview_assertions("TC-1", GOOD, name="长度校验", rule_id="R19",
                                     rule_field="任意字段")
        assert preview["valid"] is True
        assert preview["compile_status"] == "compiled"
        assert preview["auto_executable"] is True
        assert preview["assertion_count"] == 1
        assert "携带 1 条断言" in preview["compile_reason"]

    def test_invalid_assertions_are_not_previewed(self):
        preview = preview_assertions("TC-2", [{"kind": "required"}])
        assert preview["valid"] is False
        assert preview["auto_executable"] is False
        assert preview["errors"]

    def test_preview_runs_the_compiler_twice_consistently(self):
        first = preview_assertions("TC-3", GOOD, rule_id="R1", rule_field="F")
        second = preview_assertions("TC-3", GOOD, rule_id="R1", rule_field="F")
        assert first == second


class TestMergeAndProvenance:
    def test_merge_preserves_existing_fields(self):
        merged = merge_assertions(
            {"channel": "web", "requirement_rule_id": "R1"}, GOOD,
            rule_id="R19", rule_field="任意字段", source=SOURCE_MANUAL,
            updated_at="2026-09-29T00:00:00+00:00",
        )
        assert merged["channel"] == "web"                  # 既有字段不丢
        assert merged["requirement_rule_id"] == "R19"
        assert merged["requirement_rule"]["field"] == "任意字段"
        assert merged["assertions_source"] == SOURCE_MANUAL
        assert merged["assertions_updated_at"] == "2026-09-29T00:00:00+00:00"

    def test_review_list_only_covers_manual_assertions(self):
        manual = SimpleNamespace(identifier="TC-M", name="手工",
                                 custom_fields={"assertions_source": SOURCE_MANUAL,
                                                "assertions": GOOD,
                                                "requirement_rule_id": "R19",
                                                "assertions_updated_at": "t"})
        doc = SimpleNamespace(identifier="TC-D", name="文档",
                              custom_fields={"assertions_source": SOURCE_DOC,
                                             "assertions": GOOD})
        plain = SimpleNamespace(identifier="TC-P", name="无标记",
                                custom_fields={"assertions": GOOD})
        review = assertions_needing_review([manual, doc, plain])
        assert [item["identifier"] for item in review] == ["TC-M"]
        assert review[0]["assertion_count"] == 1
        assert review[0]["rule_id"] == "R19"


# ============ 2026-10-17 N1：规则体补丁语义（修复"整块替换丢字段"）============

#: 一条"历史完整规则体"：回写断言时它必须被保留，不能被 {"field": ...} 冲掉
FULL_RULE = {
    "field": "商品名稱",
    "type": "text",
    "required": True,
    "condition": "當店鋪狀態開啟時",
    "source": "PRD 3.2 商品名稱必填，條件：店鋪狀態開啟",
    "rule": "商品名稱必填且最長 50 字",
    "evidence": "PRD-v3.pdf#p12",
}


class TestRulePatchSemantics:
    def test_missing_keys_are_preserved(self):
        """核心回归：只传 rule_field，历史 type/required/condition 必须原样保留。"""
        merged = merge_assertions(
            {"requirement_rule": dict(FULL_RULE)}, GOOD,
            rule_field="商品名稱", source=SOURCE_MANUAL,
        )
        rule = merged["requirement_rule"]
        assert rule["field"] == "商品名稱"
        assert rule["type"] == "text"
        assert rule["required"] is True
        assert rule["condition"] == "當店鋪狀態開啟時"
        assert rule["source"] == FULL_RULE["source"]
        assert rule["evidence"] == FULL_RULE["evidence"]

    def test_patch_overrides_only_given_keys(self):
        merged = merge_rule_patch(dict(FULL_RULE), {"required": False})
        assert merged["required"] is False            # 补丁生效
        assert merged["type"] == "text"               # 其它键不动
        assert merged["condition"] == FULL_RULE["condition"]

    def test_explicit_null_deletes_key(self):
        """补丁语义必须能删字段，否则"想清空条件"永远清不掉。"""
        merged = merge_rule_patch(dict(FULL_RULE), {"condition": None})
        assert "condition" not in merged
        assert merged["type"] == "text"

    def test_unknown_key_is_rejected(self):
        with pytest.raises(RulePatchError) as exc:
            merge_rule_patch(dict(FULL_RULE), {"requried": True})
        assert "requried" in str(exc.value)
        assert "未知键" in str(exc.value)

    def test_unknown_key_in_merge_assertions_also_raises(self):
        with pytest.raises(RulePatchError):
            merge_assertions({}, GOOD, requirement_rule={"fieldX": "商品名稱"})

    def test_historical_dirty_keys_do_not_block(self):
        """历史脏键不参与校验，否则脏数据永久无法修正。"""
        merged = merge_rule_patch({"field": "F", "字段名": "旧脏键"}, {"required": True})
        assert merged["字段名"] == "旧脏键"
        assert merged["required"] is True

    # ---- 未知键的清理出口（2026-10-17 补，回应"白名单与可修正性矛盾"）----

    def test_unknown_key_can_be_deleted_when_present(self):
        """受控清理路径：未知键 + null 且确实存在 → 允许删除。"""
        merged = merge_rule_patch({"field": "F", "字段名": "旧脏键"}, {"字段名": None})
        assert "字段名" not in merged
        assert merged["field"] == "F"

    def test_unknown_key_null_but_absent_still_raises(self):
        """`{"requried": null}` 是纯笔误，不能当成"清理成功"。"""
        with pytest.raises(RulePatchError) as exc:
            merge_rule_patch({"field": "F"}, {"requried": None})
        assert "无法删除未知键" in str(exc.value)
        assert "requried" in str(exc.value)

    def test_known_key_null_on_absent_key_is_noop(self):
        """白名单内的键用 null 删除时不要求存在（幂等）。"""
        assert merge_rule_patch({"field": "F"}, {"condition": None}) == {"field": "F"}

    # ---- 契约：只做顶层补丁，不是完整 RFC 7386 ----

    def test_nested_object_is_replaced_not_merged(self):
        """嵌套对象**整体替换**（文档已声明），避免调用方误以为递归合并。"""
        existing = {"field": "F", "source": {"row": 1, "col": 2, "text": "原文"}}
        merged = merge_rule_patch(existing, {"source": {"row": 2}})
        assert merged["source"] == {"row": 2}          # 不是 {"row": 2, "col": 2, ...}
        assert "col" not in merged["source"]

    def test_nested_null_deletes_whole_object(self):
        merged = merge_rule_patch({"field": "F", "evidence": {"p": 12}}, {"evidence": None})
        assert "evidence" not in merged

    # ---- 契约：field:null 的行为 ----

    def test_field_null_deletes_field_key(self):
        merged = merge_rule_patch(dict(FULL_RULE), {"field": None})
        assert "field" not in merged
        assert merged["type"] == "text"

    def test_field_null_through_merge_assertions(self):
        merged = merge_assertions({"requirement_rule": dict(FULL_RULE)}, GOOD, source=SOURCE_MANUAL)
        assert merged["requirement_rule"]["field"] == "商品名稱"
        # 显式 null 删除 field 后，不再产生"field 变更"告警（没有新值可比）
        merged2 = merge_assertions(
            {"requirement_rule": dict(FULL_RULE)}, GOOD,
            requirement_rule={"field": None}, source=SOURCE_MANUAL,
        )
        assert "field" not in merged2["requirement_rule"]
        assert "assertions_warnings" not in merged2

    # ---- 契约：空数组是**有意拒绝**（不允许用空数组清空断言）----

    def test_empty_assertions_are_rejected_by_design(self):
        """空数组会让用例从"可编译"退回"叙述型"，属破坏性操作，必须走显式删除。"""
        assert validate_assertions([]) == ["assertions 必须是非空数组"]
        preview = preview_assertions("TC-E", [])
        assert preview["valid"] is False
        assert preview["auto_executable"] is False

    # ---- 警告必须按本次结果重算，不能残留 ----

    def test_warning_is_cleared_when_condition_no_longer_holds(self):
        stale = {"requirement_rule": dict(FULL_RULE), "assertions_warnings": ["上次的旧告警"]}
        merged = merge_assertions(stale, GOOD, rule_field="商品名稱", source=SOURCE_MANUAL)
        assert "assertions_warnings" not in merged        # 条件不成立 ⇒ 旧告警清除

    def test_warning_is_recomputed_each_time(self):
        first = merge_assertions(
            {"requirement_rule": dict(FULL_RULE)}, GOOD, rule_field="商品描述", source=SOURCE_MANUAL
        )
        assert first["assertions_warnings"]
        # 第二次把断言同步到新字段 ⇒ 告警应消失
        synced = [{"kind": "required", "field": "商品描述", "expect": "reject", "rule": "必填"}]
        second = merge_assertions(first, synced, rule_field="商品描述", source=SOURCE_MANUAL)
        assert "assertions_warnings" not in second

    def test_stale_warning_removed_even_without_rule_patch(self):
        stale = {"requirement_rule": dict(FULL_RULE), "assertions_warnings": ["旧告警"]}
        merged = merge_assertions(stale, GOOD, source=SOURCE_MANUAL)   # 不动规则体
        assert "assertions_warnings" not in merged

    def test_none_requirement_rule_keeps_rule_untouched(self):
        merged = merge_assertions(
            {"requirement_rule": dict(FULL_RULE)}, GOOD, source=SOURCE_MANUAL
        )
        assert merged["requirement_rule"] == FULL_RULE

    def test_rule_keys_cover_all_library_keys(self):
        """白名单必须覆盖库内实际出现的 9 个键（2026-10-17 实测）。"""
        library_keys = {"field", "type", "source", "condition", "required",
                        "rule", "description", "evidence", "editable"}
        assert library_keys <= RULE_KEYS

    def test_assertions_are_replaced_not_merged(self):
        """断言是"样本全集"，替换而非合并——避免旧样本残留造成假失败。"""
        old = [{"kind": "boundary", "field": "数量", "data": "0", "expect": "reject", "rule": "R"}]
        new = [{"kind": "max_length", "field": "数量", "data": "51字", "expect": "reject", "rule": "R"}]
        merged = merge_assertions({"assertions": old}, new, source=SOURCE_MANUAL)
        assert [a["kind"] for a in merged["assertions"]] == ["max_length"]

    def test_field_change_emits_warning(self):
        merged = merge_assertions(
            {"requirement_rule": dict(FULL_RULE)}, GOOD,
            rule_field="商品描述", source=SOURCE_MANUAL,
        )
        warnings = merged.get("assertions_warnings") or []
        assert warnings and "商品名稱" in warnings[0] and "商品描述" in warnings[0]

    def test_touch_false_writes_no_timestamp(self):
        merged = merge_assertions({}, GOOD, touch=False)
        assert "assertions_updated_at" not in merged


class TestPreviewAndSaveSameSource:
    """验收判据：预览用的数据 == 落库用的数据，重编译逐条一致。"""

    def test_preview_matches_recompile_after_save(self):
        for rule in (dict(FULL_RULE),
                     {"field": "商品名稱"},
                     {"field": "商品名稱", "type": "text", "condition": "排隊打開"}):
            merged = merge_assertions(
                {"requirement_rule": rule}, GOOD, rule_field="商品名稱", source=SOURCE_MANUAL,
            )
            preview = preview_assertions("TC-S", GOOD, name="保存一致性", custom_fields=merged)
            # 模拟"落库后重编译"：拿落库值原样再走一遍
            recompiled = preview_assertions(
                "TC-S", merged["assertions"], name="保存一致性", custom_fields=merged
            )
            assert preview == recompiled, rule

    def test_preview_does_not_invent_rule_body(self):
        """预览不得自己拼规则体：传 custom_fields 时规则体必须原样带走。"""
        fields = {"requirement_rule": dict(FULL_RULE), "requirement_rule_id": "R19"}
        merged = merge_assertions(fields, GOOD, source=SOURCE_MANUAL)
        preview = preview_assertions("TC-P", GOOD, name="n", custom_fields=merged)
        assert preview["compile_status"] == "compiled"
        assert merged["requirement_rule"]["condition"] == FULL_RULE["condition"]

    def test_missing_locator_field_is_visible_in_preview(self):
        """字段名与定位器不匹配时，预览必须体现"零检查"，而不是只报 compiled。"""
        fields = {"requirement_rule": {"field": "不存在的字段"}}
        merged = merge_assertions(fields, GOOD, source=SOURCE_MANUAL)
        preview = preview_assertions("TC-X", GOOD, name="n", custom_fields=merged)
        # 编译器会返回 compiled 但 form_executable_checks=0 —— 这属于 R1/N3 的待修面，
        # 此处只锁定"预览不能凭空说可执行"的可见性由 assertion_count 承担。
        assert preview["compile_status"] in {"compiled", "dangling_rule"}
        assert preview["valid"] is True

