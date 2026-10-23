# -*- coding: utf-8 -*-
"""關聯對象「配置保真與回顯一致性」場景族（rev185）回歸測試。

背景（3194/3195 類缺陷的通用形態）：創建/編輯時選了特定門店/商品，保存後
重開，回顯與配置不一致。字段級校驗覆蓋不了「保存→落庫→重開→回顯」鏈路，
此前生成器對關聯選擇器字段整族缺失該場景。

本測試鎖四點：
1. 通用識別：字段名動詞/類型列選擇器詞 + 實體名詞 → 命中；普通文本字段不誤命中；
2. 場景族：每個可配置關聯字段一條端到端用例（選 N→保存→重開→逐字段比對回顯
   →列表回顯→增刪再驗），echo_spec/推断标注齊備，executable_track=manual；
3. 邊界：篩選區字段、只讀/系統生成字段不生產；
4. 類型标注補齊：_field_rule_cases 的 description 與 requirement_rule.inferred_type
   帶推断標注（不再永遠"未標注類型"）。
"""

from __future__ import annotations

from app.api.v2.ai import (
    _build_persistence_echo_cases,
    _field_rule_cases,
    _relation_field_profile,
)
from app.services.requirement_structure import FieldRule, extract_constraints


def _rule(name, ftype="", desc="", required=False, editable="", table="t1", row=1):
    return FieldRule(
        name=name, field_type=ftype, required=required,
        description=desc, editable=editable,
        constraints=extract_constraints(desc),
        source={"table": table, "row": row},
    )


class TestRelationFieldProfile:
    def test_verb_plus_entity(self):
        # 适用/关联/所属/指定/选择 + 实体名词 → 命中
        for name in ("適用門店", "适用商品", "關聯標籤", "所属部门", "指定倉庫"):
            p = _relation_field_profile(_rule(name))
            assert p is not None, name
            assert p["evidence"], name

    def test_type_column_signal(self):
        # 字段名无动词，但类型列明确是选择器 + 名含实体 → 命中
        p = _relation_field_profile(_rule("門店", ftype="下拉多選"))
        assert p is not None
        assert p["multi"] is True

    def test_multi_signal(self):
        p = _relation_field_profile(_rule("適用商品", desc="支持多選，最多10個"))
        assert p is not None and p["multi"] is True
        p2 = _relation_field_profile(_rule("適用商品"))
        assert p2 is not None and p2["multi"] is False

    def test_plain_text_field_no_false_positive(self):
        # 普通文本/数值字段不命中：含实体名词但无动词、无选择器控件词
        for name, ftype in (("商品名稱", "輸入框"), ("店鋪地址", "輸入框"),
                            ("商品描述", "文本域"), ("活動名稱", "輸入框")):
            assert _relation_field_profile(_rule(name, ftype=ftype)) is None, name

    def test_inferred_type_label(self):
        p = _relation_field_profile(_rule("適用門店", desc="可多選"))
        assert "多选" in p["inferred_type"] and "門店" in p["inferred_type"]


class TestPersistenceEchoCases:
    def test_multi_field_generates_e2e_case(self):
        cases = _build_persistence_echo_cases(
            [_rule("適用門店", desc="支持多選")], "需求.pdf")
        assert len(cases) == 1
        c = cases[0]
        assert "配置保真与回显一致性校验" in c.name
        assert c.preconditions and "至少 3 个" in c.preconditions  # N=2 + 1 备选
        steps_text = " ".join(s.step for s in (c.test_case_steps or []))
        results_text = " ".join(s.result for s in (c.test_case_steps or []))
        # 端到端链路五要素：选择→保存→重開→回顯比對→列表回顯→增刪再驗
        assert "选择" in steps_text and "保存" in steps_text
        assert "重新打开" in steps_text
        assert "回显" in results_text
        assert "列表" in steps_text
        assert "移除" in steps_text and "追加" in steps_text
        cf = c.custom_fields
        assert cf["persistence_echo_case"] is True
        assert cf["echo_spec"] == {"field": "適用門店", "entity": "門店",
                                   "multi": True, "sample_count": 2}
        assert cf["requirement_rule"]["inferred_type"]
        assert cf["executable_track"] == "manual"

    def test_single_field_sample_count_one(self):
        cases = _build_persistence_echo_cases([_rule("適用商品")], "需求.pdf")
        assert len(cases) == 1
        assert cases[0].custom_fields["echo_spec"]["sample_count"] == 1
        steps_text = " ".join(s.step for s in (cases[0].test_case_steps or []))
        assert "改选" in steps_text

    def test_plain_field_not_generated(self):
        cases = _build_persistence_echo_cases(
            [_rule("商品名稱", ftype="輸入框", desc="必填；最多50個字")], "需求.pdf")
        assert cases == []

    def test_filter_field_excluded(self):
        # 篩選區字段（模糊/匹配/查詢語義）不做配置保真
        cases = _build_persistence_echo_cases(
            [_rule("適用門店", desc="支持模糊查詢")], "需求.pdf")
        assert cases == []

    def test_readonly_field_excluded(self):
        cases = _build_persistence_echo_cases(
            [_rule("適用門店", editable="系統自動生成")], "需求.pdf")
        assert cases == []

    def test_dedupe_same_rule(self):
        # 同名同表（desc 前 30 字相同）视为同一规则的续行 → 去重成 1 条
        cases = _build_persistence_echo_cases(
            [_rule("適用門店"), _rule("適用門店", row=2)], "需求.pdf")
        assert len(cases) == 1
        # 不同表的同名字段是独立规则，各自成条（rev110 同口径）
        cases2 = _build_persistence_echo_cases(
            [_rule("適用門店", table="t1"), _rule("適用門店", table="t2")], "需求.pdf")
        assert len(cases2) == 2
        ids = {c.custom_fields["requirement_rule_id"] for c in cases2}
        assert len(ids) == 2

    def test_limit_cap(self):
        rules = [_rule(f"適用門店{ i }" if False else f"關聯標籤", row=i + 1)
                 for i in range(20)]
        # 同名去重後只會 1 條；換不同字段名驗證 limit 上限
        rules = [_rule(f"關聯標籤{i}", row=i + 1, table=f"t{i}") for i in range(20)]
        cases = _build_persistence_echo_cases(rules, "需求.pdf")
        assert len(cases) <= 12


class TestTypeAnnotationFallback:
    def test_field_rule_case_description_carries_inference(self):
        c = _field_rule_cases(
            _rule("適用門店", desc="支持多選", required=True),
            "需求.pdf", module="", case_kind="sit", test_mode="sit")
        assert c, "必填關聯字段應產出必填校驗用例"
        target = next(x for x in c if "必填與取值校驗" in x.name or "必填与取值校验" in x.name)
        # 诚实标注：文档未标注 + 框架推断补齐，两者并存
        assert "未标注类型" in target.description
        assert "推断" in target.description
        assert target.custom_fields["requirement_rule"]["inferred_type"]

    def test_typeless_plain_field_keeps_original_label(self):
        c = _field_rule_cases(
            _rule("備註", desc="選填", required=False),
            "需求.pdf", module="", case_kind="sit", test_mode="sit")
        # 無斷言（選填無約束）→ 不產用例；若有則保持"未標注類型"
        for x in c:
            assert "推断" not in x.description
