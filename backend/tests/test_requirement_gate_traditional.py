# -*- coding: utf-8 -*-
"""门控章节别名：繁體支持（独立问题，非 3a 范围）。

实测背景（2026-09-24）：`CS Module方案説明2.0.pdf` 是**繁體**文档，而
`REQUIRED_SECTIONS` 等检查项的别名**全是简体**（`字段说明`/`验收标准`/`业务目标`…），
于是 `欄位説明`、`權限` 这些真实章节一个都命不中，多个检查项被误判为"缺失"。

本文件钉住：
1. 繁體别名能被命中（修复点）；
2. 简体行为不回归；
3. **一个必须说清楚的事实**：该文档被判 Blocked 的两个硬阻断项
   （业务目标 / 操作流程）在**任何字体下都不存在** —— 補别名不能、也不该让它们变成"通过"，
   那是文档类型/门控策略问题，需要单独决策。
"""

from __future__ import annotations

from app.test_platform.requirements.analysis import analyze_requirement_text


def _kinds(result) -> dict[str, str]:
    return {check.item: str(getattr(check.kind, "value", check.kind)) for check in result.checks}


def _details(result) -> dict[str, str]:
    return {check.item: (check.detail or "") for check in result.checks}


TRADITIONAL_PRD = """
一、業務目標
本需求讓客服能在 CRM 內建立與追蹤工單，減少跨系統切換。
二、功能點
功能點編號：FP-001
功能點名稱：工單建立
三、角色與權限
角色：客服主管、客服專員；無權限者應提示拒絕訪問。
四、前置條件
已登入 CRM 並具備客服角色。
五、操作流程
進入 Cases 列表 → 點擊新建 → 填寫表單 → 保存。
六、輸入與數據規則
欄位説明：Case Title 必填，限制輸入50個字符。
七、驗收標準
保存後工單出現在列表且狀態為 In Progress。
八、正常結果
列表顯示新工單。
"""

TRADITIONAL_FIELD_SPEC_ONLY = """
CS Module 方案説明
一、欄位説明
欄位名稱    類型    必填    説明
Case Title  Text    是      限制輸入50個字符
二、權限
客服專員可查看，無權限時拒絕訪問。
"""


class TestTraditionalSectionAliases:
    def test_traditional_prd_sections_are_matched(self):
        result = analyze_requirement_text(TRADITIONAL_PRD)
        kinds = _kinds(result)
        for item in ("业务目标", "操作流程", "角色/权限", "前置条件",
                     "输入与数据规则", "验收标准", "正常结果"):
            assert kinds[item] != "required_missing", f"{item} 仍被判缺失（繁體别名未生效）"
        assert kinds["业务目标"] == "flagged"
        assert kinds["操作流程"] == "flagged"

    def test_chinese_numeral_heading_is_recognized(self):
        """`一、業務目標` 这类中文数字标题必须能被当作章节标题（原先只剥离阿拉伯数字序号）。"""
        text = "一、業務目標\n讓客服在 CRM 內建單。\n二、操作流程\n進入列表 → 新建 → 保存。"
        kinds = _kinds(analyze_requirement_text(text))
        assert kinds["业务目标"] == "flagged", kinds
        assert kinds["操作流程"] == "flagged", kinds

    def test_traditional_field_spec_matches_input_rules_alias(self):
        """`欄位説明` 必须能被别名命中（原来的别名只有简体 `字段说明`）。"""
        result = analyze_requirement_text(TRADITIONAL_FIELD_SPEC_ONLY)
        detail = _details(result)["输入与数据规则"]
        # 命中别名但内容是表格 → 报"内容为空"，而不是"缺少输入与数据规则"
        assert "缺少输入与数据规则" not in detail, detail

    def test_traditional_permission_section_is_matched(self):
        result = analyze_requirement_text(TRADITIONAL_FIELD_SPEC_ONLY)
        assert _kinds(result)["角色/权限"] != "required_missing"

    def test_simplified_still_works(self):
        simplified = TRADITIONAL_PRD.replace("業務目標", "业务目标").replace("操作流程", "操作流程")
        kinds = _kinds(analyze_requirement_text(simplified))
        assert kinds["业务目标"] == "flagged"
        assert kinds["操作流程"] == "flagged"

    def test_field_spec_document_is_still_blocked_by_prd_only_items(self):
        """**必须保留的事实**：補繁體别名不能救这类文档。

        该文档没有"业务目标/操作流程"是**事实**（任何字体下 0 次出现），
        而这两项是 PRD 模版的硬阻断项 → 仍然 Blocked。
        要让"字段规格类文档"可自动生成，需要改门控策略（文档类型感知 / 结构化证据），
        属单独决策，不在本修复内。
        """
        result = analyze_requirement_text(TRADITIONAL_FIELD_SPEC_ONLY)
        assert result.can_generate_testcases is False
        blocked = [
            check.item for check in result.checks
            if getattr(check, "blocking", False)
        ]
        assert set(blocked) == {"业务目标", "操作流程"}
