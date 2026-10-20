# -*- coding: utf-8 -*-
"""回归测试：用例漂移与"假覆盖"的三道门禁。

1. 需求条目抽取必须拦住 PDF 协作痕迹/导出样例名/页码残片（漂移根因）；
2. 字段名必须是短标签，段落文本不得产出可编译规则（且被拒项要显式记录）；
3. 悬空规则号（有编号、无规则体）不得计入覆盖度，并在产物里显式暴露。
"""

from __future__ import annotations

from app.api.v2.ai import _extract_requirement_points, _is_doc_noise
from app.services.requirement_structure import _looks_like_field_label, _merge_wrapped_lines
from app.services.web_case_compiler import compile_cases, render_coverage_snippet


class TestPdfLineMerging:
    """PDF 硬换行会把一条需求切成多条，既产生噪音又吃掉 12 条叙述配额。"""

    RAW = (
        "===== PAGE 1 =====\n"
        "搭建 CS Module 客户服务管理系统，统一归集多渠道客户诉求，实现 Case 工单的指\n"
        "派、进度追踪、历史记录查询\n"
        "支持Retro Claim、Goodwill、多级审核、消息通知、客\n"
        "戶滿意度調研\n"
        "功能介紹：支持對Case進行查詢/篩選/導出\n"
        "1. 待審批記錄顯示\n"
        "2. 點擊Case Subcategory Level1名稱\n"
    )

    def test_fragments_are_joined_into_complete_sentences(self):
        merged = _merge_wrapped_lines(self.RAW)
        lines = merged.splitlines()
        # 断裂的句子被合成一条完整需求
        assert any("实现 Case 工单的指派、进度追踪、历史记录查询" in l for l in lines), lines
        # 跨行截断的尾巴也被接回去（此前是 "…消息通知、客"）
        assert any("消息通知、客戶滿意度調研" in l for l in lines), lines
        # 页码标记既不保留也不参与合并
        assert not any("=====" in l for l in lines), lines

    def test_numbered_items_are_not_merged_together(self):
        merged = _merge_wrapped_lines(self.RAW)
        lines = [l for l in merged.splitlines() if l.strip()]
        assert "1. 待審批記錄顯示" in lines
        assert "2. 點擊Case Subcategory Level1名稱" in lines

    def test_short_labels_do_not_swallow_the_next_sentence(self):
        merged = _merge_wrapped_lines("一、背景\n搭建 CS Module 系统，支持查询\n")
        lines = merged.splitlines()
        assert lines[0] == "一、背景", lines
        assert lines[1].startswith("搭建"), lines

    def test_merging_frees_slots_for_real_rules(self):
        """合并前碎片会占满 12 条配额；合并后应抽出更完整、更多的真条目。"""
        before = _extract_requirement_points(self.RAW, filename="x.pdf")
        after = _extract_requirement_points(_merge_wrapped_lines(self.RAW), filename="x.pdf")
        # 合并前 "搭建…的指" 与 "派、进度…" 是两条；合并后合成一条
        assert len(after) < len(before)
        assert any("历史记录查询" in p for p in after)

    def test_table_rows_are_dropped_before_merging(self):
        """真实链路顺序是 先剔表格行 → 再合并；否则表格内容会被粘进需求正文。

        （CS 规格书是表格型文档，"字段(EN)/字段(CN)/Case Title…" 这些行必须先被剔除。）
        """
        from app.services.requirement_structure import _drop_table_lines

        text = (
            "篩選邏輯：支持多條件組合模糊篩選​\n"
            "字段(EN)\n字段(CN)\n字段類型\nCase Title\n工單名稱\n"
            "下拉篩選：枚舉選項只支持單選，不可同時選中多个狀態查詢​\n"
        )
        blob = {"字段(EN)", "字段(CN)", "字段類型", "Case Title", "工單名稱"}
        merged = _merge_wrapped_lines(_drop_table_lines(text, blob))
        assert "字段(EN)" not in merged
        assert "工單名稱" not in merged
        # 正文仍然完好
        assert "篩選邏輯" in merged and "下拉篩選" in merged


class TestDocNoiseFilter:
    """用线上真实噪音样例做夹具（PR-9 CS 批次）。"""

    NOISE = [
        "一郎9月8日 14:39",              # TC-768564：纯批注头
        "曾志强8月31日 14:24",            # TC-624948
        "曾志强8月31日 14:24的边界条件处理",  # 由上一行膨胀出来的用例
        "Mike 9月7日 14:06 （编辑过）",
        "一郎9月7日 17:23",
        "件標題：CS_Cases_YYYYMMDD_hhmmss",
        "@Mike  已補充",
        "===== PAGE 3 =====",
        "b.",
        "8.",
    ]

    REAL = [
        "企业账户充值金额范围为(0,99999]，最多保留2位小数",
        "会员手机号最多输入10个手机号，超过部分需校验",
        "上传文件仅支持 xlsx 格式，单个文件不超过 1M",
        "如不是小杨会员，新增和批量储值时需要提示不是会员的清单",
    ]

    def test_noise_lines_are_rejected(self):
        for line in self.NOISE:
            assert _is_doc_noise(line) is True, line
            assert _extract_requirement_points(line) == [], line

    def test_real_requirements_survive(self):
        for line in self.REAL:
            assert _is_doc_noise(line) is False, line
            assert _extract_requirement_points(line) == [line[:120]], line

    def test_batch_extraction_keeps_only_real_rules(self):
        """整段混排时，噪音不进需求条目（12 条上限内不浪费名额）。"""
        text = "\n".join(self.NOISE + self.REAL)
        points = _extract_requirement_points(text)
        assert points == [line[:120] for line in self.REAL]


class TestFieldLabelGuard:
    GARBAGE = [
        "b. 已通過：員工操作已通過情況下，則顯示“已通過”c. 已駁回：員工操作已駁回情況下",
        "Member 是 必填，選取會員名稱Case  是 1. 如在GoodWill模塊創建的，必選選擇case",
        "h. 分頁展示，10條/頁4. 消息彈窗提示交互：a. 有新消息時",
        "3. 點擊Case Subcategory Level1名稱，跳轉到詳情",
        "2. 最新修改時間（包括創建時間）3. 狀態（創建完成后為“Active”",
        "件標題：CS_Cases_YYYYMMDD_hhmmss",
        "",
    ]
    LABELS = ["企业账户充值金额", "会员手机号", "备注", "变动类型", "变更方式",
              "上传文件（企业充值）", "Modified On", "Case Category"]

    def test_paragraphs_are_not_field_labels(self):
        for bad in self.GARBAGE:
            assert _looks_like_field_label(bad) is False, bad

    def test_real_labels_are_kept(self):
        for good in self.LABELS:
            assert _looks_like_field_label(good) is True, good


class _Case:
    def __init__(self, identifier, name, custom_fields):
        self.identifier = identifier
        self.name = name
        self.custom_fields = custom_fields
        self.test_case_steps = []


class TestDanglingRuleIdGate:
    def _manifest(self):
        return compile_cases([
            # 悬空：有规则号、无规则体（两个批次各 36 条的真实形态）
            _Case("TC-1", "一郎9月8日 14:39", {"requirement_rule_id": "RQ-aaa"}),
            _Case("TC-2", "一郎9月8日 14:39的边界条件处理", {"requirement_rule_id": "RQ-bbb"}),
            # 真实规则：有规则体 + 断言
            _Case("TC-3", "企业账户充值金额-边界值", {
                "requirement_rule_id": "FR-ccc",
                "requirement_rule": {"field": "企业账户充值金额", "kind": "range"},
                "assertions": [{"kind": "boundary", "data": "0", "expect": "reject"}],
            }),
        ])

    def test_dangling_ids_are_not_counted_as_rule_cases(self):
        m = self._manifest()
        assert m["case_count"] == 3
        assert m["rule_case_count"] == 1          # 只有 1 条真正带规则体
        assert m["narrative_case_count"] == 2     # 36 条这类"陪跑用例"的真实身份
        assert sorted(m["dangling_rule_ids"]) == ["RQ-aaa", "RQ-bbb"]

    def test_coverage_output_exposes_dangling_ids(self):
        snippet = render_coverage_snippet(manifest_var="COMPILED_CASES")
        assert "narrative_cases" in snippet
        assert "dangling_rule_ids" in snippet
        # 不得再用"全部用例"充当覆盖度分子
        assert "rule_cases: COMPILED_CASES.rule_case_count" in snippet
