# -*- coding: utf-8 -*-
"""用例漂移回归：评审/确认章节、变更清单、评审批注不得再变成需求点。

素材来自 2026-09-23 PR-2 重测批次的**真实文档片段**（`小杨生煎-B端储值充值.docx`，
见 `xysj_req_extract.txt`），其中：
- §2.4 会员详情：`新增：小程序账户变动金额、和企业账户变动金额，` / `删除：变动金额、`
- §3 待确认事项：`秋刀：原功能没有“导出原文件”按钮，跟产品确认，去掉` 等问答行
这些点曾被三场景扩写成 TC-405179 / TC-939415 / TC-384308 等漂移用例。
"""

from __future__ import annotations

from app.api.v2.ai import (
    _extract_requirement_points,
    _is_change_delta,
    _is_review_annotation,
    _section_kind,
)

#: 真实文档片段（保留原顺序与标题层级）
DOC = """小杨生煎-B端储值充值
2.1.2 字段说明
变动类型
单选组
是
选项范围：企业储值充值、企业储值退款（仅扣减储值不退款）
变更方式
单选组
是
选项范围：手动输入、批量导入
企业账户充值金额
数字输入框
是
(0,99999]之间的数字，小数点后最多保留2位有效数字；
上传文件（企业充值）
上传文件组件
是
文件格式仅支持：xlsx、xls、csv
2.4 会员详情
新增：小程序账户变动金额、和企业账户变动金额，
删除：变动金额、
3. 待确认事项
秋刀：原功能没有“导出原文件”按钮，跟产品确认，去掉
需要查看原文件，要不然导入成功的数据中有问题，无法进行查询
会员详情页，储值流水字段增加（待处理）；
已增加
优先扣企业账号的储值余额（待客户确认）；
已确认，使用时先扣小程序账户
小程序端储值样式；
小程序不展示toB储值；
4. 端到端业务场景（web 自动测试目标路径）
登录企业控制台 → 门户中心 → 数盈营销云 → 忠诚度 → 储值管理 → 储值账户列表
"""


class TestSectionKind:
    def test_review_sections_are_detected(self):
        assert _section_kind("3. 待确认事项") == "review"
        assert _section_kind("待确认事项") == "review"
        assert _section_kind("评审意见") == "review"
        assert _section_kind("修订记录") == "review"

    def test_change_sections_are_detected(self):
        assert _section_kind("变更说明") == "change"
        assert _section_kind("版本记录") == "change"

    def test_normal_field_rows_are_not_sections(self):
        assert _section_kind("选项范围：企业储值充值、企业储值退款（仅扣减储值不退款）") == "normal"
        assert _section_kind("企业账户充值金额") == "normal"


class TestAnnotationAndDelta:
    def test_reviewer_annotation_is_rejected(self):
        assert _is_review_annotation("秋刀：原功能没有“导出原文件”按钮，跟产品确认，去掉")
        assert _is_review_annotation("已确认，使用时先扣小程序账户")
        assert _is_review_annotation("小程序不展示toB储值")
        assert _is_review_annotation("优先扣企业账号的储值余额（待客户确认）")
        assert _is_review_annotation("需要查看原文件，要不然导入成功的数据中有问题")

    def test_normal_rule_is_not_treated_as_annotation(self):
        assert not _is_review_annotation("选项范围：企业储值充值、企业储值退款（仅扣减储值不退款）")
        assert not _is_review_annotation("文件格式仅支持：xlsx、xls、csv")
        assert not _is_review_annotation("(0,99999]之间的数字，小数点后最多保留2位有效数字")

    def test_change_delta_is_rejected(self):
        assert _is_change_delta("新增：小程序账户变动金额、和企业账户变动金额，")
        assert _is_change_delta("删除：变动金额、")
        assert not _is_change_delta("新增会员时需要校验手机号是否激活")


class TestExtractionDropsDrift:
    def _points(self, text: str) -> list[str]:
        return _extract_requirement_points(text, filename="小杨生煎-B端储值充值.docx")

    def test_review_section_is_entirely_skipped(self):
        points = self._points(DOC)
        for bad in ("秋刀", "原功能没有", "跟产品确认", "已确认", "已增加",
                    "小程序端储值样式", "不展示toB储值", "要不然"):
            assert not any(bad in p for p in points), (bad, points)

    def test_change_list_is_skipped(self):
        points = self._points(DOC)
        assert not any(p.startswith("新增：") or p.startswith("删除：") for p in points)

    def test_real_field_rules_are_kept(self):
        points = self._points(DOC)
        assert any("选项范围：企业储值充值" in p for p in points)
        assert any("文件格式仅支持" in p for p in points)
        assert any("99999" in p for p in points)

    def test_regression_of_reported_cases(self):
        """回归锁定被点名的两条漂移用例名：不得再出现在需求点里。"""
        points = self._points(DOC)
        joined = " || ".join(points)
        assert "导出原文件" not in joined
        assert "变动金额、的" not in joined


class TestTrailingAnnotationAndExportSamples:
    """2026-09-29：CS 规格书的批注附在**条目末尾**，导出模板样例也混进了需求点。

    实测（CS Module 方案説明2.0）：叙述侧 12 条需求点里，前 3 条是导出模板/命名样例，
    且条目末尾粘着「一郎9月7日 17:15」这类协作批注——三场景模板把它们膨胀成 9 条
    无断言的叙述用例，既污染用例名也占了 12 条上限里的名额。
    """

    def test_trailing_annotation_is_stripped(self):
        from app.api.v2.ai import _clean_point_text

        assert _clean_point_text(
            "導出標題格式：cs_cases_YYYYMMDD_hhmmss（后為導出日期時間）一郎9月7日 17:15"
        ) == "導出標題格式：cs_cases_YYYYMMDD_hhmmss（后為導出日期時間）"
        assert _clean_point_text("消息中心入口固定于右上角\u200b") == "消息中心入口固定于右上角"

    def test_pure_annotation_becomes_empty(self):
        from app.api.v2.ai import _clean_point_text

        assert _clean_point_text("一郎9月7日 17:23") == ""
        assert _extract_requirement_points("一郎9月7日 17:23") == []

    def test_export_template_samples_are_noise(self):
        from app.api.v2.ai import _is_doc_noise

        for line in (
            "【模板】cs_cases_20270101_100000.xlsx件標題：CS_Cases_YYYYMMDD_hhmmss",
            "導出標題格式：cs_task_YYYYMMDD_hhmmss（后為導出日期時間）cs_task_2026_0915_142345.xlsx",
            "導出標題格式：cs_cases_YYYYMMDD_hhmmss（后為導出日期時間）",
        ):
            assert _is_doc_noise(line) is True, line
            assert _extract_requirement_points(line) == [], line

    def test_real_requirement_with_appended_annotation_survives_cleaned(self):
        text = "消息中心入口固定于右上角，點擊可展開全部消息列表\u200b一郎9月7日 17:15"
        assert _extract_requirement_points(text) == ["消息中心入口固定于右上角，點擊可展開全部消息列表"]
