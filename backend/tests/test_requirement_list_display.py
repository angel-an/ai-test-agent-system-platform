# -*- coding: utf-8 -*-
"""列表顯示字段表結構化（rev108）回歸測試。

背景（YL 主數據人工用例對比缺口）：文檔三張「列表顯示字段」表
（店鋪 15 列/商品 7 列/支付 5 列）因表頭「說明」不在 _DESC_HINTS 詞表，
全部落入 template_tables（只存表頭+行數），列清單結構性丟失、無用例承接。
rev108 起 _split_tables 單獨捕獲列表表 → display_columns，生成層每表一條
「欄位完整性校驗」用例。本測試鎖三點：
1. 兩種真實表頭形態（字段名稱|業務規則說明、列表欄位|欄位說明）都被捕獲；
2. 列表列**不進** field_rules（防定位器綁定 0 可執行回歸）；
3. 表單字段說明表不被誤判為列表表；生成用例含完整列清單。
"""

from __future__ import annotations

from app.api.v2.ai import _build_list_display_cases
from app.services.requirement_structure import _split_tables, parse_requirement_document

# 店鋪列表（1.2.2.2）：表頭「字段名稱|業務規則說明」（說明 = U+8AAA，非简体「说明」/繁体「説明」）
STORE_TABLE = [
    ["字段名稱", "業務規則說明"],
    ["店鋪編號", "唯一標識店鋪的編號"],
    ["店鋪名稱", ""],
    ["店鋪名稱(EN)", ""],
    ["所屬菜係", ""],
    ["所屬組織", ""],
    ["操作人", ""],
    ["更新時間", "列表排序依據，由近到遠"],
]

# 商品列表（3.2.1.2）/支付列表（4.2.1.2）：表頭「列表欄位|欄位說明」
GOODS_TABLE = [
    ["列表欄位", "欄位說明"],
    ["商品編號", "唯一標識商品的編號"],
    ["商品名稱(CN)", ""],
    ["商品名稱(EN)", ""],
    ["創建人", ""],
    ["創建時間", ""],
    ["更新人", ""],
]

# 表單字段說明表（不應被誤判為列表表）
FORM_TABLE = [
    ["字段", "字段類型", "是否必填", "字段説明"],
    ["店鋪名稱", "文字輸入框", "是", "必填；最多輸入50個字"],
    ["店鋪電話", "數字輸入框", "是", "最多填寫2個電話"],
]


class TestListDisplayCapture:
    def test_store_style_header_captured(self):
        rules, templates, _rej, _reb, display = _split_tables([STORE_TABLE])
        assert len(display) == 1
        names = [c["name"] for c in display[0]["columns"]]
        assert names == ["店鋪編號", "店鋪名稱", "店鋪名稱(EN)", "所屬菜係",
                         "所屬組織", "操作人", "更新時間"], names
        # 業務規則說明進入 desc，可追溯表号/行号
        first = display[0]["columns"][0]
        assert first["desc"] == "唯一標識店鋪的編號"
        assert first["source"]["row"] == 2
        # 列表列不進 field_rules / 不進 template_tables
        assert rules == []
        assert templates == []

    def test_goods_style_header_captured(self):
        _rules, _tpl, _rej, _reb, display = _split_tables([GOODS_TABLE])
        assert len(display) == 1
        names = [c["name"] for c in display[0]["columns"]]
        assert "商品編號" in names and "更新人" in names
        assert len(names) == 6

    def test_form_field_table_not_misclassified(self):
        rules, _tpl, _rej, _reb, display = _split_tables([FORM_TABLE])
        # 表單字段表仍走 field_rules 通道
        assert display == []
        assert [r.name for r in rules] == ["店鋪名稱", "店鋪電話"]
        assert rules[0].required is True

    def test_continuation_rows_merge_into_same_table(self):
        # 跨頁續表：第二段無表頭、列數與表頭一致 → 併入同一 display 表
        cont = [["創建時間", ""], ["更新人", ""], ["更新時間", ""]]
        _r, _t, _rej, _reb, display = _split_tables([STORE_TABLE, cont])
        assert len(display) == 1
        names = [c["name"] for c in display[0]["columns"]]
        assert "更新時間" in names and "創建時間" in names
        # 續表行号从 1 起（无表头）
        assert display[0]["columns"][-1]["source"]["row"] == 3

    def test_multiple_list_tables_kept_separately(self):
        _r, _t, _rej, _reb, display = _split_tables([STORE_TABLE, GOODS_TABLE])
        assert len(display) == 2

    def test_desc_text_not_taken_as_column_name(self):
        # 說明列的整段文字不得被當成欄位名（_looks_like_field_label 防線）
        table = [
            ["列表欄位", "欄位說明"],
            ["商品編號", "商品編號由外部系統生成，導入時保持不變，超過長度會校驗失敗並記錄錯誤原因"],
            ["商品名稱(CN)", ""],
        ]
        _r, _t, _rej, _reb, display = _split_tables([table])
        names = [c["name"] for c in display[0]["columns"]]
        assert names == ["商品編號", "商品名稱(CN)"], names

    def test_glued_blob_header_does_not_hijack_list_continuation(self):
        # 实测（YL p3）：fitz 把列表殘段+排序規則+篩選表+導入規則粘成一個單元格，
        # 該行含「字段/校驗/必填」會被誤判成字段表表頭，把後續真正的列表續段
        # 當字段表續行吞掉 → 列表列全丟。膠水表頭必須被拒絕。
        blob = ("店鋪內部名稱\n所屬品牌\n所屬菜係\n優先級\n是"
                "是否在APP顯示營業狀態1.2.2.3 列表排序規則根據更新時間由近到遠；"
                "1.2.2.4 篩選查詢區字段 字段類型 字段校驗 篩選規則店鋪編號 輸入框")
        rules, _tpl, _rej, _reb, display = _split_tables([
            STORE_TABLE,
            [[blob, "", "", "", ""], ["", "", "", "", ""]],
            [["店鋪內部名稱", ""], ["所屬品牌", ""], ["所屬菜係", ""],
             ["所屬組織", ""], ["操作人", ""], ["更新時間", ""]],
        ])
        assert display and len(display) == 1
        names = [c["name"] for c in display[0]["columns"]]
        assert "店鋪內部名稱" in names and "更新時間" in names, names
        # 膠水行不得產出 field_rules
        assert all("所屬菜係優先級" not in r.name for r in rules)
        assert all("店鋪內部名稱" != r.name for r in rules)


class TestListDisplayCaseGeneration:
    def test_one_case_per_table_with_full_column_list(self):
        cases = _build_list_display_cases(
            [{"table": 0, "columns": [
                {"name": n, "desc": ""} for n in
                ["店鋪編號", "店鋪名稱", "所屬菜係", "所屬組織", "操作人", "更新時間"]
            ]}], "YL.pdf")
        assert len(cases) == 1
        case = cases[0]
        assert "店鋪" in case.name and "欄位完整性校驗" in case.name
        # 規則體攜帶完整列清單（人工執行核對依據）
        rule = case.custom_fields["requirement_rule"]["rule"]
        assert "6列" in rule and "店鋪編號" in rule and "更新時間" in rule
        assert case.custom_fields["list_display_case"] is True
        assert case.custom_fields["executable_track"] == "manual"
        assert case.custom_fields["requirement_rule_id"].startswith("LST-")
        # 步驟含逐列核對與多餘/缺失檢查
        step_text = " ".join(s.step for s in case.test_case_steps)
        assert "核對" in step_text and "多餘" in step_text

    def test_module_detected_per_table(self):
        goods = [{"table": 0, "columns": [
            {"name": n, "desc": ""} for n in ["商品編號", "商品名稱(CN)"]]}]
        pay = [{"table": 1, "columns": [
            {"name": n, "desc": ""} for n in ["支付方式編號", "支付方式名稱"]]}]
        cases = _build_list_display_cases(goods + pay, "YL.pdf")
        assert [c.name for c in cases] == [
            "列表顯示-商品-欄位完整性校驗", "列表顯示-支付方式-欄位完整性校驗"]

    def test_empty_and_blank_tables_skipped(self):
        assert _build_list_display_cases([], "YL.pdf") == []
        assert _build_list_display_cases([{"table": 0, "columns": []}], "YL.pdf") == []
        # 無關鍵詞時回落通用命名，不報錯
        cases = _build_list_display_cases(
            [{"table": 0, "columns": [{"name": "編號", "desc": ""}]}], "X.pdf")
        assert len(cases) == 1 and "業務" in cases[0].name

    def test_rule_id_stable(self):
        tables = [{"table": 0, "columns": [{"name": "店鋪編號", "desc": ""}]}]
        a = _build_list_display_cases(tables, "YL.pdf")[0]
        b = _build_list_display_cases(tables, "YL.pdf")[0]
        assert a.custom_fields["requirement_rule_id"] == b.custom_fields["requirement_rule_id"]
        c = _build_list_display_cases(tables, "OTHER.pdf")[0]
        assert c.custom_fields["requirement_rule_id"] != a.custom_fields["requirement_rule_id"]
