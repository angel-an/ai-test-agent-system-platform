# -*- coding: utf-8 -*-
"""rev133：叙述条目 → 字段规则回链必须满足「该字段是本句主语」。

实测背景（小杨生煎-订单评价(问卷调查) PDF，复验7 批次）：原实现只要句子里
**出现**字段名就回链，于是

- "文件名格式：调查问卷-{问卷标题+问卷编码}-{导出时间，具体到时分秒}"
- "查询条件：问卷标题---支持模糊查询"
- "调查问卷-这里是问卷标题名称1234567890-20260101100000.xlsx"

全部被挂上「问卷标题=必填」断言 —— 标题讲的是导出/查询行为，断言却在验必填，
执行通过也不代表需求被验证（假通过）。5 条错配全部属于这一类。

本文件锁住两个方向：
① 提到 ≠ 主语：占位符、标签前缀、示例产物、括号注释里的字段名不回链；
② 真主语不能漏：字段名在主语区的条目仍必须回链（否则可自动执行率会被人为压掉）。
"""
from __future__ import annotations

import pytest

from app.api.v2.ai import _distinctive_field_aliases, _match_field_for_point, _subject_zone
from app.services.requirement_structure import FieldRule


def _rules() -> list[FieldRule]:
    return [
        FieldRule(name="问卷标题", field_type="input", required=True),
        FieldRule(name="业务类型", field_type="select", required=True),
        FieldRule(name="标题", field_type="input", required=True),
    ]


def _match(point: str):
    return _match_field_for_point(point, _distinctive_field_aliases(_rules()))


class TestNotSubjectMention:
    """字段名只是被提到 —— 不得回链（否则产生假通过断言）。"""

    @pytest.mark.parametrize(
        "point",
        [
            # 占位符里的字段名：讲的是文件名构成
            "文件名格式：调查问卷-{问卷标题+问卷编码}-{导出时间，具体到时分秒}",
            # 标签前缀：讲的是查询能力
            "查询条件：问卷标题---支持模糊查询",
            # 标签前缀 + 并列回显：讲的是列表列
            "列表信息：回显所配置的问卷编码（系统自动生成10位数值）、问卷标题、业务类型、适用门店",
            # 示例产物（带扩展名）：是样例不是条款
            "调查问卷-这里是问卷标题名称1234567890-20260101100000.xlsx",
            # 方括号里的按钮名只是顺带提到
            "限制最多设置10道，超过[新增题目]按钮禁用，不可继续新增题目",
        ],
    )
    def test_no_link(self, point: str) -> None:
        assert _match(point) is None, f"不应回链：{point}"


class TestRealSubjectStillLinks:
    """字段名在主语区 —— 必须回链，不能因为收紧而漏掉真需求。"""

    @pytest.mark.parametrize(
        ("point", "expect_field"),
        [
            ("问卷标题必填，最多20个字", "问卷标题"),
            ("业务类型：外卖、自提、堂食", "业务类型"),
            ("1.2.1 标题必填", "标题"),
            ("（3）标题限制输入50个字符", "标题"),
            ("问卷标题（最多20字）", "问卷标题"),
        ],
    )
    def test_link(self, point: str, expect_field: str) -> None:
        linked = _match(point)
        assert linked is not None, f"应回链：{point}"
        assert linked[0].name == expect_field


class TestSubjectZone:
    def test_strips_placeholder_and_label(self) -> None:
        assert _subject_zone("文件名格式：调查问卷-{问卷标题+问卷编码}") == "文件名格式"

    def test_keeps_whole_when_no_delimiter(self) -> None:
        assert _subject_zone("1.2.1 标题必填") == "标题必填"

    def test_stops_at_comma(self) -> None:
        assert _subject_zone("限制最多设置10道，超过按钮禁用") == "限制最多设置10道"

    def test_empty(self) -> None:
        assert _subject_zone("") == ""
