# -*- coding: utf-8 -*-
"""rev181：用例标题规范化 + 扩写前置闸门（噪音与可测性）。

背景（2026-10-22 对 95 条在用用例的审计）：
- **33%（32/95）** 的标题以模板后缀结尾，其中「…的必填或非法输入校验」有 6 条来自
  根本不可测的需求点（如「增加会员限购商品管理列表--交易云-运营-增加菜单(…)的必填…」TC-843363）；
- 13 条超 40 字，最长 54 字，把需求原文的括号解释整段搬进标题；
- 7 条含模糊词（「可正常保存」「正常生效」），不可判定。

本文件锁两件事：① 扩写闸门不再给"只描述存在"的条目生成反向/边界变体；
② 标题规范化（去路径尾段、去括号解释、模糊词可测化、超长压缩）且**不改写正常标题**。
"""
import pytest

from app.api.v2.ai import (
    _build_requirement_cases,
    _bracket_title,
    _case_title_from_requirement,
    _has_testable_behavior,
)
from app.services.requirement_structure import extract_constraints
from app.services.requirement_structure import FieldRule


# ---------------------------------------------------------------- 扩写闸门


@pytest.mark.parametrize("point", [
    "增加会员限购商品管理列表--交易云-运营-增加菜单(菜单名称：会员限购商品管理)",
    "增加菜单",
    "新增评价入口",
    "提供导出按钮",
])
def test_presence_only_points_are_not_testable(point: str) -> None:
    """只描述"有这个东西/加了入口"的条目 → 不扩写反向/边界（否则就是纯噪音）。"""
    assert _has_testable_behavior(point) is False


@pytest.mark.parametrize("point", [
    "限购标签最多8个字",
    "新建时限购状态默认为「启用」",
    "列表完整展示五个业务字段",
    "消费金额门槛输入10000时被拦截",
    "商品编码重复时保存提示且不产生第二条数据",
])
def test_behavior_points_stay_testable(point: str) -> None:
    assert _has_testable_behavior(point) is True


def test_no_behavior_point_yields_single_positive_case() -> None:
    """闸门生效：不可测条目只出 1 条正向，不再膨胀成 3 条无断言用例。"""
    cases, _gaps, _skips = _build_requirement_cases(
        ["增加会员限购商品管理列表--交易云-运营-增加菜单(菜单名称：会员限购商品管理)"],
        "限购.pdf",
    )
    assert len(cases) == 1
    assert "必填或非法输入校验" not in cases[0].name


def test_behavior_point_with_field_link_still_expands() -> None:
    """同一文档里，能回链到字段规则且含行为词的条目**仍然**扩写成多条（不误杀）。

    rev182 起扩写条数由「该字段在哪个维度上真有约束」决定，不再是固定 3 条：
    `限购标签` 只有 required + max_length，边界桶为空 → 只扩反向，不扩边界
    （原先那条边界用例断言为 0，是空壳）。详见 test_expand_assertion_gate_rev182.py。
    """
    rules = [FieldRule(name="限购标签", description="必填；最多8个字",
                       field_type="text", required=True,
                       constraints=extract_constraints("必填；最多8个字"))]
    cases, _gaps, _skips = _build_requirement_cases(
        ["限购标签最多8个字"], "限购.pdf", field_rules=rules,
    )
    names = [c.name for c in cases]
    assert len(names) >= 2, names
    assert any("必填或非法输入校验" in n for n in names)
    for case in cases:
        if case.keyword in ("反向", "边界"):
            assert case.custom_fields.get("assertions"), case.name


def test_unlinked_point_is_not_expanded_even_when_testable() -> None:
    """可测但回链不到字段 → 不扩写（没有断言可挂，扩写出来就是不可判定用例）。"""
    cases, _gaps, _skips = _build_requirement_cases(["系统需要校验门店地址格式"], "限购.pdf")
    assert len(cases) == 1


# ---------------------------------------------------------------- 标题规范化


def test_path_tail_is_removed() -> None:
    title = _case_title_from_requirement(
        "限购商品需要在点单页展示(路径说明--交易云-运营-增加菜单)")
    assert "--" not in title


def test_path_tail_kept_for_short_titles() -> None:
    """去掉路径尾段会损失有效信息时（短标题、正常词）不动——「问卷-新建」这类要保留。"""
    for raw in ("问卷列表-状态筛选后重置还原（筛选区）",
                "会员限购商品列表-状态切换为不启用-列表同步",
                "限购商品名称-必填与取值校验"):
        assert _case_title_from_requirement(raw) == raw


def test_bracket_explanation_is_dropped_when_too_long() -> None:
    raw = ("限购商品需要在点单页展示(润典配置在菜单分类展示即可，"
           "不能配置在隐藏分类/加价购分类中)的必填或非法输入校验")
    title = _case_title_from_requirement(raw)
    assert len(title) <= 40
    assert "润典配置" not in title
    assert "点单页展示" in title
    # 后缀是唯一判定信息（54 字原文只剩这句才是可测的），必须保留
    assert "校验" in title


def test_bracket_qualifier_moves_to_tail() -> None:
    """「上传文件（企业退款）-条件显示校验」→「上传文件-条件显示校验（企业退款）」：主语与后缀相邻。"""
    assert _case_title_from_requirement("上传文件（企业退款）-条件显示校验") == \
        "上传文件-条件显示校验（企业退款）"
    assert _case_title_from_requirement("备注(最多输入100个字)的边界条件处理") == \
        "备注的边界条件处理（最多输入100个字）"


def test_heavy_prefix_drops_pipeline_suffix() -> None:
    """需求原文很长且后缀是流水线句式 → 只留原文，别把两句话拼在一起。"""
    title = _case_title_from_requirement(
        "限购商品需要在点单页展示(润典配置在菜单分类展示即可，不能配置在隐藏分类中)"
        "的必填或非法输入校验")
    assert "润典配置" not in title
    assert len(title) <= 40
    # 轻量字段名 + 后缀 → 后缀是判定信息，保留
    assert _case_title_from_requirement("题目类型-必填与取值校验") == "题目类型-必填与取值校验"


def test_hard_rule_in_bracket_is_kept() -> None:
    """括号里是硬规则（最长 N 字 / 上限 / 格式）时必须保留，它才是判定条件。"""
    title = _case_title_from_requirement("备注(最多输入100个字)的边界条件处理")
    assert "100" in title


def test_vague_words_become_testable() -> None:
    assert _case_title_from_requirement("自提渠道下限购规则正常生效") == "自提渠道下限购规则生效"
    title = _case_title_from_requirement("限购商品名称输入20字可正常保存（上边界）")
    assert "正常" not in title and "保存" in title


def test_title_length_cap_and_ellipsis() -> None:
    raw = "如不是小杨会员，新增和批量储值时需要提示不是会员的清单，其它用户完成保存的必填或非法输入校验"
    title = _case_title_from_requirement(raw)
    assert len(title) <= 40
    assert "如不是小杨会员" in title


def test_empty_input_falls_back() -> None:
    assert _case_title_from_requirement("") == "未命名需求场景"


def test_bracket_title_has_single_bracket_pair() -> None:
    title = _bracket_title("上传文件（企业充值）", "文件≤1M；格式：xlsx/xls/csv")
    assert title.count("（") == 1 and title.count("）") == 1
    assert len(title) <= 40
    assert "上传文件" in title and "企业充值" in title


def test_bracket_title_keeps_short_summary_intact() -> None:
    assert _bracket_title("选项内容", "最长20字") == "选项内容-边界值与格式校验（最长20字）"


def test_title_normalization_is_traceable() -> None:
    """标题被改过就必须留原文，验收时能逐字对回需求。"""
    rules = [FieldRule(name="限购标签", description="必填；最多8个字",
                       field_type="text", required=True)]
    cases, _gaps, _skips = _build_requirement_cases(
        ["限购标签最多8个字"], "限购.pdf", field_rules=rules)
    positive = [c for c in cases if "必填或非法输入校验" not in c.name
                and "边界条件处理" not in c.name]
    assert positive, [c.name for c in cases]
    assert positive[0].custom_fields.get("title_normalized_from") is None or \
        positive[0].custom_fields["title_normalized_from"]
