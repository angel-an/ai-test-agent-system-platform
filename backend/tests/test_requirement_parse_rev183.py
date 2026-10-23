# -*- coding: utf-8 -*-
"""rev183：约束解析补齐 + 模糊词生成期告警。

两件事分属两层，不得混为一谈：

① `extract_constraints` / `extract_condition` 的**解析层**缺口补齐——
   此前三种真实写法（「仅支持jpg，png格式」「最多2位小数」「仅当…时显示」）
   全判「无约束」，对应用例不扩写、断言为 0，被登记为解析缺口。

② 模糊词**不做自动替换**（用户 2026-10-21 裁决），降级为生成期告警。
   替换会把「适当限制」凭空变成「最多20字」，用例跑绿也只验证了自编的数；
   告警把问题退回给写文档的人。
"""
import pytest

from app.api.v2.ai import (
    _build_vague_word_warning,
    scan_vague_words,
)
from app.services.requirement_structure import (
    extract_condition,
    extract_constraints,
)


# ---------------------------------------------------------------- ① 格式声明


@pytest.mark.parametrize("desc,expected", [
    ("文件格式仅支持jpg，png格式", ["jpg", "png"]),
    ("1. 仅支持jpg、png格式", ["jpg", "png"]),
    ("仅支持 jpg/png 格式", ["jpg", "png"]),
    ("支持jpg、png格式上传", ["jpg", "png"]),
    ("上传格式支持：jpg，png", ["jpg", "png"]),
    ("支持的文件类型：jpg/png", ["jpg", "png"]),
    ("文件格式仅支持：jpg、png", ["jpg", "png"]),
])
def test_format_declarations_are_extracted(desc, expected) -> None:
    """七种真实写法都要抽出同一份格式清单（此前只有带冒号的规范句式能抽到）。"""
    got = [c for c in extract_constraints(desc) if c.kind == "formats"]
    assert got, f"{desc!r} 未抽出 formats"
    assert got[0].detail["formats"] == expected, got[0].detail


def test_format_list_stops_at_next_numbered_clause() -> None:
    """「…jpg、png4. 单个文件大小不超过2M」：编号不能和最后一个格式粘在一起。

    实测这条的真实后果：格式清单只剩 ['jpg']，png 被吞掉 → 只校验一半合法值。
    """
    desc = "1. 非必填2. 最多支持上传5张图片3. 文件格式仅支持：jpg、png4. 单个文件大小不超过2M"
    got = [c for c in extract_constraints(desc) if c.kind == "formats"]
    assert got and got[0].detail["formats"] == ["jpg", "png"], got


@pytest.mark.parametrize("desc", [
    "格式要求：参照附件说明",
    "暂不支持jpg格式",
    "不支持上传pdf文件",
])
def test_negated_or_vague_format_is_rejected(desc) -> None:
    """被否定的格式 / 指向别处的措辞不是"可用格式清单"。

    当成清单会生成「上传 jpg 应成功」的反向断言——拿文档里被否定的例子当合法值。
    """
    assert not [c for c in extract_constraints(desc) if c.kind == "formats"], desc


# ---------------------------------------------------------------- ① 小数位数


@pytest.mark.parametrize("desc,digits", [
    ("最多2位小数", 2),
    ("最多保留2位小数", 2),
    ("保留两位小数", 2),
    ("小数点后最多保留2位小数", 2),
    ("最多保留3位小数", 3),
])
def test_decimal_place_forms(desc, digits) -> None:
    got = [c for c in extract_constraints(desc) if c.kind == "decimal_places"]
    assert got and got[0].numbers == [float(digits)], got


def test_max_length_is_not_mistaken_for_decimal_places() -> None:
    """「最多输入20个字」是长度上限，不是精度——补正则时最容易踩的误判。"""
    kinds = {c.kind for c in extract_constraints("最多输入20个字")}
    assert "decimal_places" not in kinds, kinds
    assert "max_length" in kinds, kinds


# ---------------------------------------------------------------- ① 条件显示


@pytest.mark.parametrize("desc,expect", [
    ("仅当订单类型选择外卖时显示该字段", "订单类型=外卖"),
    ("仅当选择外卖时，显示该字段", "选择外卖"),
    ("只有当门店类型=直营店时才显示该字段", "门店类型=直营店"),
    ("如果订单来源选择：APP，则显示该字段", "订单来源选择=APP"),
    ("当店鋪狀態開啟時，顯示該字段", "店鋪狀態開啟"),
    ("变动类型选择：企业储值充值 且 变更方式选择：批量导入时显示该字段",
     "变动类型=企业储值充值 且 变更方式=批量导入"),
])
def test_condition_forms(desc, expect) -> None:
    """前置语式「仅当…时（才）显示」此前整条判"无条件"，条件显示用例数为 0。"""
    assert extract_condition(desc) == expect


@pytest.mark.parametrize("desc", [
    "字段名称",
    "最多输入20个字",
    "仅支持jpg、png格式",
    "该字段用于内部区分问卷",
])
def test_plain_description_is_not_a_condition(desc) -> None:
    assert extract_condition(desc) == ""


@pytest.mark.parametrize("desc", [
    # ---- rev184：真实文档（小杨生煎·订单评价问卷）里 8 个只读字段的原文 ----
    # rev183 放宽裸「顯示|显示」后，这些**展示方式/兜底值**描述全被判成条件显示，
    # 8 个字段的 condition 从 '' 变成 '固定'、'也不需要'，会直接生成假的
    # 「条件显示校验」用例（断言验的是根本不存在的开关）。
    "固定显示，包括支付宝小程序/微信小程序",
    "固定显示，获取用户昵称，为空时显示“-”",
    "固定显示，获取用户手机号，若无则为“-”",
    "固定显示，获取会员卡号",
    "固定显示，获取该用户触发问卷的门店名称，为空时显示“-”",
    "固定显示，获取该用户触发问卷的门店编码，为空时显示“-”",
    "固定显示，获取触发该问卷的订单号，为空时显示“-”",
    "固定显示，获取用户填写提交问卷时间",
    "B端不需要生成二维码，也不需要展示路径，但如果业务操作新建问卷，"
    "这个问券地址，前端要能通过技术方式拿到，并在路径后拼接门店编码（为空时显示“-”）",
])
def test_display_wording_is_not_a_condition(desc) -> None:
    """「固定显示」「不需要展示」「为空时显示"-"」讲的是展示方式，不是条件显示。

    这是 rev183 补齐「仅当…时显示」时引入的**回归**：裸「显示」分支把
    「固定显示」整个吃成条件短语。修法 = 裸「顯示/显示/展示」必须带字段宾语
    （该字段/该栏位/此项…）+ 条件语义门槛 + 兜底值显示守卫。
    """
    assert extract_condition(desc) == "", f"{desc[:24]!r} 被误判成条件"


# ---------------------------------------------------------------- ② 模糊词告警


def test_vague_words_are_named_with_reason_and_suggestion() -> None:
    text = (
        "问卷标题：必填，最多输入20个字，尽量控制在简短范围内。\n"
        "业务类型：支持多种类型，如外卖、自提等等。\n"
        "备注：适当限制长度，视情况而定。\n"
        "图片：最多支持上传5张图片，界面要美观流畅。"
    )
    hits = scan_vague_words(text)
    words = {h["word"] for h in hits}
    assert {"尽量", "支持多种", "适当", "优化"} <= words, hits
    for hit in hits:
        assert hit["reason"], hit      # 必须说清"为什么判不了"
        assert hit["suggestion"], hit  # 必须给改写方向
        assert hit["point"], hit


def test_vague_scan_skips_sentences_with_concrete_values() -> None:
    """「尽量控制在3秒以内」已经可判定了——有具体数值就不该报模糊。"""
    hits = scan_vague_words("刷新频率：尽量控制在3秒以内。")
    assert not hits, hits


def test_vague_warning_counts_and_remedy() -> None:
    hits = scan_vague_words("备注：适当限制。\n标题：尽量简短。")
    text = _build_vague_word_warning(hits)
    assert text and f"{len(hits)} 处" in text
    assert "只能人工执行" in text
    assert "换成具体阈值" in text


def test_vague_warning_is_none_when_clean() -> None:
    assert _build_vague_word_warning(scan_vague_words("标题：必填，最多输入20个字。")) is None


def test_vague_scan_does_not_flag_plain_deng() -> None:
    """裸「等」不能当模糊词：「等待」「等于」「等级」会大面积误伤。"""
    hits = scan_vague_words("等待页面加载完成；金额等于订单总额；等级字段必填。")
    assert not hits, hits


@pytest.mark.parametrize("text", [
    # rev184：真实文档（小杨生煎·问卷调查）原文——讲的是**分隔规则**，完全可判定，
    # 却被「多个」命中并报「枚举校验无法穷举」。告警指向错方向比不告警更糟：
    # 写文档的人会去"补闭集"，而其实这里根本没有枚举问题。
    "有多个用英文逗号分开",
    "多个选项之间用逗号分隔",
    "多条记录之间以分号隔开",
])
def test_multi_is_not_vague_when_it_describes_delimiters(text) -> None:
    assert not scan_vague_words(text), scan_vague_words(text)


def test_multi_still_flagged_when_it_really_means_no_closed_set() -> None:
    """真·没列完的「多个」仍要报——收紧误报不能顺手把真问题放过。"""
    assert [h["word"] for h in scan_vague_words("支持多个门店")] == ["支持多种"]
