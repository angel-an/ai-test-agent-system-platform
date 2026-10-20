# -*- coding: utf-8 -*-
"""地圖組件交互 + 只讀（不可編輯）字段用例（rev109 / P1）回歸測試。

背景（YL 主數據人工用例對比缺口）：
- 「輸入詳細地址，從地圖組件搜索詳細的位置；搜索的範圍在店鋪行政區範圍」
  與「通過詳細地址在谷歌地圖中搜索返回具體的經度/緯度，不可編輯」都在
  字段說明列的**敘述文字**裡——表格行不進敘事通道、約束抽取不覆蓋地圖語義，
  此前無任何用例承接；
- 文檔無「是否可編輯」列，編輯態證據僅經度/緯度的「不可編輯」「禁用狀態」。
本測試鎖三點：
1. 解析层抽出 read_only 約束（含「禁用狀態」，不含裸「禁用」誤命中）；
2. 只讀字段每字段一條用例，證據引用文檔原文；
3. 地圖組件按 desc 命中「地圖」路由，無證據時不生產。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from app.api.v2.ai import _build_map_interaction_cases, _build_read_only_field_cases
from app.services.requirement_structure import (
    FieldRule,
    _split_tables,
    extract_constraints,
    parse_requirement_document,
)

YL_PDF = Path(r"C:/Users/admin/WorkBuddy/2026-09-29-15-29-56/.workbuddy/tmp/yulon_md/requirement.pdf")

# 模拟 YL 店鋪字段說明表的三行（真實原文）
RULE_ADDRESS = FieldRule(
    name="詳細地址", field_type="輸入框", required=True,
    description="輸入詳細地址，從地圖組件搜索詳細的位置；搜索的範圍在店鋪行政區範圍",
    constraints=extract_constraints(
        "輸入詳細地址，從地圖組件搜索詳細的位置；搜索的範圍在店鋪行政區範圍"),
)
RULE_LNG = FieldRule(
    name="經度", field_type="輸入框", required=False,
    description="禁用狀態：示例數據：22.307961通過詳細地址在谷歌地圖中搜索返回具體的經度，不可編輯",
    constraints=extract_constraints(
        "禁用狀態：示例數據：22.307961通過詳細地址在谷歌地圖中搜索返回具體的經度，不可編輯"),
)
RULE_LAT = FieldRule(
    name="緯度", field_type="輸入框", required=False,
    description="禁用狀態：示例數據：114.255868通過詳細地址在谷歌地圖中搜索返回具體的緯度，不可編輯",
    constraints=extract_constraints(
        "禁用狀態：示例數據：114.255868通過詳細地址在谷歌地圖中搜索返回具體的緯度，不可編輯"),
)
RULE_PLAIN = FieldRule(
    name="店鋪名稱", field_type="輸入框", required=True,
    description="必填；最多輸入50個字",
    constraints=extract_constraints("必填；最多輸入50個字"),
)


class TestReadOnlyConstraintExtraction:
    def test_bukeshou_editable(self):
        kinds = {c.kind: c for c in extract_constraints(
            "通過詳細地址在谷歌地圖中搜索返回具體的經度，不可編輯")}
        assert "read_only" in kinds
        assert kinds["read_only"].raw == "不可編輯"
        assert kinds["read_only"].detail["state"] == "disabled"

    def test_jinyong_zhuangtai(self):
        kinds = {c.kind: c for c in extract_constraints(
            "禁用狀態：示例數據：22.307961")}
        assert "read_only" in kinds
        assert kinds["read_only"].raw == "禁用狀態"

    @pytest.mark.parametrize("text", [
        "企業充值流水、企業退款流水；",       # 普通敘述
        "禁用該功能後列表不再展示該欄位",         # 裸「禁用」是行為規則，不是字段狀態
        "選項範圍：全部、營業、不營業",           # 其他約束不受影響
    ])
    def test_no_false_positive(self, text):
        assert "read_only" not in {c.kind for c in extract_constraints(text)}


class TestReadOnlyFieldCases:
    def test_one_case_per_readonly_field(self):
        cases = _build_read_only_field_cases(
            [RULE_LNG, RULE_LAT, RULE_PLAIN], "YL.pdf")
        assert [c.name for c in cases] == [
            "業務-經度-只讀（不可編輯）校驗", "業務-緯度-只讀（不可編輯）校驗"]
        case = cases[0]
        assert case.custom_fields["read_only_case"] is True
        assert case.custom_fields["executable_track"] == "manual"
        assert case.custom_fields["requirement_rule_id"].startswith("RO-")
        # 規則體引用文檔原文（禁用狀態 / 不可編輯均可作為依據）
        rule = case.custom_fields["requirement_rule"]["rule"]
        assert "禁用狀態" in rule or "不可編輯" in rule
        # 步驟覆蓋：狀態檢查 → 嘗試修改 → 保存後復查
        results = " ".join(s.result for s in case.test_case_steps)
        assert "禁用/只讀態" in results and "一致" in results

    def test_editable_column_value_triggers_case(self):
        # 「是否可編輯=否」列證據（無 desc 敘述）也產只讀用例
        rule = FieldRule(name="精度", editable="否")
        cases = _build_read_only_field_cases([rule], "YL.pdf")
        assert len(cases) == 1
        assert "是否可編輯=否" in cases[0].custom_fields["requirement_rule"]["rule"]

    def test_editable_yes_does_not_trigger(self):
        rule = FieldRule(name="店鋪名稱", editable="是")
        assert _build_read_only_field_cases([rule], "YL.pdf") == []

    def test_module_hint_prefix(self):
        cases = _build_read_only_field_cases([RULE_LNG], "YL.pdf", module_hint="店鋪")
        assert cases[0].name.startswith("店鋪-")

    def test_no_evidence_no_cases(self):
        assert _build_read_only_field_cases([RULE_PLAIN], "YL.pdf") == []
        assert _build_read_only_field_cases([], "YL.pdf") == []

    def test_rule_id_stable(self):
        a = _build_read_only_field_cases([RULE_LNG], "YL.pdf")[0]
        b = _build_read_only_field_cases([RULE_LNG], "YL.pdf")[0]
        assert a.custom_fields["requirement_rule_id"] == b.custom_fields["requirement_rule_id"]


class TestMapInteractionCases:
    def test_map_evidence_produces_one_case(self):
        cases = _build_map_interaction_cases(
            [RULE_ADDRESS, RULE_LNG, RULE_LAT, RULE_PLAIN], "YL.pdf")
        assert len(cases) == 1
        case = cases[0]
        assert "地圖組件" in case.name
        assert case.custom_fields["map_interaction_case"] is True
        assert case.custom_fields["executable_track"] == "manual"
        assert case.custom_fields["requirement_rule_id"].startswith("MAP-")
        steps = case.test_case_steps
        assert len(steps) == 4
        # 步驟引用真實字段名：地址觸發、經緯度回填、只讀、保存復查
        all_text = " ".join(s.step + s.result for s in steps)
        assert "詳細地址" in all_text and "經度" in all_text and "緯度" in all_text
        # 規則體引用文檔原文（地圖組件搜索 + 行政區範圍限定）
        rule = case.custom_fields["requirement_rule"]["rule"]
        assert "地圖組件" in rule and "行政區" in rule

    def test_no_map_evidence_no_case(self):
        assert _build_map_interaction_cases([RULE_PLAIN], "YL.pdf") == []
        assert _build_map_interaction_cases([], "YL.pdf") == []

    def test_coord_without_map_keyword_no_case(self):
        # 坐標欄位但通篇無「地圖」語義 → 不猜测、不生產
        coord_only = FieldRule(name="經度", description="示例數據：22.307961",
                               constraints=[])
        assert _build_map_interaction_cases([coord_only], "YL.pdf") == []

    def test_module_hint_prefix(self):
        cases = _build_map_interaction_cases(
            [RULE_ADDRESS, RULE_LNG, RULE_LAT], "YL.pdf", module_hint="店鋪")
        assert cases[0].name.startswith("店鋪-")


class TestEditableColumnCapture:
    """「是否可編輯」列捕獲 + 寬續表偏移對齊（合成表，模擬 YL 店鋪字段表形態）。"""

    STORE_HEADER = ["字段", "字段類型", "是否必填", "是否可編輯", "字段描述"]
    FIRST_ROWS = [
        ["店鋪名稱", "輸入框", "是", "是", "小於等於30個字符"],
    ]
    # 9 列寬續表：前導空列 + 5 字段列 + 3 批注列（fitz 被 PDF 評論列撐寬的真實形態）
    WIDE_CONT = [
        ["", "所屬品牌", "下拉單選", "是", "是", "選項範圍：品牌", "", "", ""],
        ["", "店鋪詳細地址", "輸入框", "是", "是",
         "輸入詳細地址，從地圖組件搜索詳細的位置；搜索的範圍在店鋪行政區範圍", "", "", ""],
        ["", "精度", "數字", "系統自動生成", "否",
         "禁用狀態：示例數據：22.307961通過詳細地址在谷歌地圖中搜索返回具體的精度，不可編輯", "", "", ""],
        ["", "維度", "數字", "系統自動生成", "否",
         "禁用狀態：示例數據：114.255868通過詳細地址在谷歌地圖中搜索返回具體的維度，不可編輯", "", "", ""],
    ]

    def test_editable_column_captured(self):
        rules, _t, _rej, _reb, _disp = _split_tables(
            [[self.STORE_HEADER] + self.FIRST_ROWS])
        assert rules[0].editable == "是"
        assert rules[0].to_dict()["editable"] == "是"

    def test_wide_continuation_aligned_by_offset(self):
        rules, _t, _rej, _reb, _disp = _split_tables(
            [[self.STORE_HEADER] + self.FIRST_ROWS, self.WIDE_CONT])
        by_name = {r.name: r for r in rules}
        # 寬續表 4 行全部回收
        assert "店鋪詳細地址" in by_name and "精度" in by_name and "維度" in by_name
        # 可編輯列對齊到正確列位
        assert by_name["精度"].editable == "否"
        assert by_name["店鋪詳細地址"].editable == "是"
        # 說明列完整（地圖語義 + 不可編輯都存活）
        assert "地圖組件" in by_name["店鋪詳細地址"].description
        assert "不可編輯" in by_name["維度"].description
        # read_only 約束同步抽出
        assert by_name["精度"].constraint("read_only") is not None

    def test_unrelated_wide_table_not_hijacked(self):
        # 無關寬表（名稱列/必填列不可信）不得被偏移對齊劫持
        noise = [
            ["", "任務名稱", "取導入文件的名稱", "已完成", "2026-09-29", "", "", "", ""],
            ["", "導入結果", "導入成功：200條", "導入失敗：10條", "創建人", "", "", "", ""],
        ]
        rules, _t, _rej, _reb, _disp = _split_tables(
            [[self.STORE_HEADER] + self.FIRST_ROWS, noise])
        # noise 表的行不得變成字段規則
        assert all(r.name not in ("任務名稱", "導入結果") for r in rules)


@pytest.mark.skipif(not YL_PDF.exists(), reason="YL 需求 PDF 不在本機")
class TestRealYlPdf:
    def test_end_to_end_map_and_readonly(self):
        content = YL_PDF.read_bytes()
        s = parse_requirement_document(YL_PDF.name, "application/pdf", content)
        ro_cases = _build_read_only_field_cases(s.field_rules, YL_PDF.name)
        names = [c.name for c in ro_cases]
        # 列證據（是否可編輯=否/系統自動生成）+ 說明列證據（不可編輯/禁用狀態）雙通道：
        # 精度/維度必有；店鋪編號/商品編碼等按文檔實際標注擴展，不固定數量
        assert any("精度" in n for n in names), names
        assert any("維度" in n for n in names), names
        # 每條用例都帶文檔依據（列值或說明列原文），不得憑空產生
        for case in ro_cases:
            rule = case.custom_fields["requirement_rule"]["rule"]
            assert "是否可編輯=" in rule or "說明列：" in rule, rule

        map_cases = _build_map_interaction_cases(s.field_rules, YL_PDF.name)
        assert len(map_cases) == 1
        assert "店鋪詳細地址" in map_cases[0].test_case_steps[0].step
        assert "精度" in map_cases[0].test_case_steps[1].result

    def test_store_field_table_recovered_with_editable_column(self):
        """寬續表對齊（9列→5列偏移1）：店鋪字段表 20 行殘段整段回收，
        「是否可編輯」列（否/系統自動生成）一併捕獲。"""
        content = YL_PDF.read_bytes()
        s = parse_requirement_document(YL_PDF.name, "application/pdf", content)
        by_name = {r.name: r for r in s.field_rules}
        # 續表殘段字段全部回收
        for expected in ("店鋪詳細地址", "精度", "維度", "電話區號", "店鋪電話",
                         "搜索關鍵詞", "營業時間"):
            assert expected in by_name, f"字段未回收: {expected}（現有 {len(s.field_rules)} 條）"
        # 可編輯列捕獲：精度=否、詳細地址=是
        assert by_name["精度"].editable == "否", by_name["精度"].editable
        assert by_name["店鋪詳細地址"].editable == "是", by_name["店鋪詳細地址"].editable
        # 地圖與不可編輯語義在 desc 存活
        assert "地圖組件" in by_name["店鋪詳細地址"].description
        assert "不可編輯" in by_name["精度"].description
