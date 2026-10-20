# -*- coding: utf-8 -*-
"""B1：字段规格类文档适配（结构化证据 + 适用档案）回归测试。

背景：真件 `CS Module方案説明2.0.pdf` 是**字段规格/方案说明**文档，
能抽出 128 条字段规则、24 条可执行规则用例，却因"没有 PRD 章节体"
（业务目标/操作流程任何字体下都不存在）被判 **Blocked**，只能人工放行。

B1 的口径（必须一起成立，缺一不可）：

1. 结构化字段规则数 > 0 → 视为已提供「输入与数据规则」证据（不再误报缺失）；
2. 字段规则数 ≥ :data:`FIELD_SPEC_MIN_FIELD_RULES` → 按**字段规格档案**判定，
   PRD 专属章节转为「不适用」（不计缺失、不阻断），**允许生成**；
3. **质量状态仍如实保留 Incomplete** —— 不把"字段规格完整"谎报成"PRD 完整"；
4. 结构化证据不足（低于阈值）→ 仍按 PRD 口径 Blocked（不放宽）；
5. payload（LLM 结果）**不得**自行声明该档案来绕过门控。
"""

from __future__ import annotations

from app.test_platform.requirements.analysis import (
    FIELD_SPEC_MIN_FIELD_RULES,
    FIELD_SPEC_PROFILE,
    PRDAnalysisResult,
    analyze_requirement_text,
)
from app.test_platform.requirements.gate import check_generation_gate
from app.test_platform.requirements.pipeline import run_requirement_pipeline

#: 典型字段规格文档正文：有字段说明表、没有 PRD 章节
FIELD_SPEC_TEXT = """
CS Module 方案説明
一、欄位説明
欄位名稱    類型    必填    説明
Case Title  Text    是      限制輸入50個字符
Case Status Select  是      默認為In Progress
二、權限
客服專員可查看，無權限時拒絕訪問。
"""

#: 完整 PRD（用于确认老口径不回归）
COMPLETE_PRD_TEXT = """
业务目标：让运营能配置标签。
功能点编号：FP-001
功能点名称：标签管理
角色/权限：运营可编辑；无权限用户拒绝访问。
前置条件：已登录。
操作流程：进入标签列表 → 新建 → 填写名称 → 保存。
输入规则：名称为必填，最多输入20个字。
验收标准：保存后列表出现该标签。
正常结果：列表显示新标签。
基本业务规则：名称不可重复。
"""


class TestFieldSpecProfile:
    def test_structured_rules_satisfy_input_rules_check(self):
        """字段说明表本身即「输入与数据规则」证据：要么别名命中，要么由结构化规则计数命中。"""
        result = analyze_requirement_text(FIELD_SPEC_TEXT, field_rule_count=128)
        detail = {c.item: (c.detail or "") for c in result.checks}
        assert "缺少输入与数据规则" not in detail["输入与数据规则"]
        assert not any(g.item == "输入与数据规则" for g in result.missing_items)

    def test_structured_rules_used_when_no_section_alias(self):
        """正文里连「字段说明」字样都没有时，结构化规则数必须成为证据。"""
        text = "CS Module 説明\n本文件以表格描述欄位。\n"
        result = analyze_requirement_text(text, field_rule_count=128)
        detail = {c.item: (c.detail or "") for c in result.checks}
        assert "结构化字段规则 128 条" in detail["输入与数据规则"]

    def test_profile_allows_generation_but_keeps_incomplete_status(self):
        result = analyze_requirement_text(FIELD_SPEC_TEXT, field_rule_count=128)
        assert result.applicable_profile == FIELD_SPEC_PROFILE
        assert result.can_generate_testcases is True
        # 关键：不谎报 Complete
        assert result.status.value == "Incomplete"
        assert result.profile_reason and "字段规格" in result.profile_reason

    def test_prd_only_sections_become_not_applicable(self):
        result = analyze_requirement_text(FIELD_SPEC_TEXT, field_rule_count=128)
        items = {gap.item for gap in result.not_applicable_items}
        assert {"业务目标", "操作流程", "前置条件", "验收标准"} <= items
        # 不适用项不得同时出现在缺失/阻断里
        assert not (items & {gap.item for gap in result.missing_items})
        assert not result.blocked_reasons

    def test_gate_allows_field_spec_document(self):
        result = analyze_requirement_text(FIELD_SPEC_TEXT, field_rule_count=128)
        allowed, reasons = check_generation_gate(result)
        assert allowed is True and reasons == []

    def test_pipeline_proceeds_to_generation(self):
        pipeline = run_requirement_pipeline(FIELD_SPEC_TEXT, field_rule_count=128)
        assert pipeline.can_generate_testcases is True
        assert pipeline.paused is False

    def test_below_threshold_still_blocked(self):
        """结构化证据不足 → 仍按 PRD 口径拦下（B1 的牙齿）。"""
        result = analyze_requirement_text(
            FIELD_SPEC_TEXT, field_rule_count=FIELD_SPEC_MIN_FIELD_RULES - 1
        )
        assert result.applicable_profile == ""
        assert result.can_generate_testcases is False
        assert result.status.value == "Blocked"
        allowed, _ = check_generation_gate(result)
        assert allowed is False

    def test_zero_rules_behaves_like_before(self):
        result = analyze_requirement_text(FIELD_SPEC_TEXT, field_rule_count=0)
        assert result.applicable_profile == ""
        assert result.status.value == "Blocked"
        assert result.can_generate_testcases is False

    def test_complete_prd_unaffected(self):
        result = analyze_requirement_text(COMPLETE_PRD_TEXT, field_rule_count=0)
        assert result.status.value == "Complete"
        assert result.can_generate_testcases is True
        assert result.applicable_profile == ""
        allowed, _ = check_generation_gate(result)
        assert allowed is True

    def test_report_exposes_profile_and_not_applicable(self):
        from app.test_platform.requirements.analysis import (
            render_analysis_markdown,
            render_analysis_report,
        )

        result = analyze_requirement_text(FIELD_SPEC_TEXT, field_rule_count=128)
        report = render_analysis_report(result)
        assert report["applicable_profile"] == FIELD_SPEC_PROFILE
        assert report["profile_reason"]
        assert report["not_applicable_items"]
        markdown = render_analysis_markdown(result, source="CS Module方案説明2.0.pdf")
        assert "## 文档类型适配" in markdown
        assert "字段规格" in markdown

    def test_payload_cannot_self_declare_profile_to_bypass_gate(self):
        """payload 不得自行声明 field_spec 档案来绕过门控（fail-closed）。"""
        forged = PRDAnalysisResult.from_dict({
            "status": "Incomplete",
            "can_generate_testcases": True,
            "applicable_profile": FIELD_SPEC_PROFILE,
            "profile_reason": "我自己声明的",
        })
        assert forged.can_generate_testcases is False
        allowed, _ = check_generation_gate(forged)
        assert allowed is False
