# -*- coding: utf-8 -*-
"""需求结构化（章节过滤 + 表格结构化）回归测试。

覆盖：
1. 约束抽取：针对**真实文档原句**（小杨生煎-B端储值充值）的区间/位数/长度/数量/格式/大小/枚举/条件；
2. 章节标题过滤：真实标题被判为标题，真实规则句不被误杀；
3. docx：字段说明表 → 字段规则（含跨页续行合并）；
4. pdf：真实需求 PDF（存在时）→ 字段规则；
5. 生成结果：无章节标题碎片用例；边界用例引用文档真实值；含条件显示用例。
"""

from __future__ import annotations

import io
from pathlib import Path

import pytest

from app.services.requirement_structure import (
    extract_condition,
    extract_constraints,
    is_heading_line,
    parse_requirement_document,
)

REAL_PDF = Path(
    r"D:\code\Pyproject\ai-test-agent-system-platform\backend\workspace\uploads"
    r"\小杨生煎-B端储值充值.pdf"
)

# ---- 真实文档原句（摘自 小杨生煎-B端储值充值.pdf 的字段说明表） -------------
DESC_AMOUNT = "当变动类型选择：企业储值充值）且变更方式选中：手动输入，显示该字段(0,99999]之间的数字，小数点后最多保留2位有效数字；"
DESC_PHONE = "最多输入140个字；提示：最多输入10个手机号，若有多个，请以英文逗号隔如:13672432212，18743211223"
DESC_UPLOAD = "当变动类型选择：企业储值充值）且变更方式选中：批量导入，显示该字段1、下载模板2、文件格式仅支持：xlsx、xls、csv3、文件大小1M"
DESC_CHANGE_TYPE = "选项范围：企业储值充值、企业储值退款（仅扣减储值不退款）默认选中：企业储值充值"


class TestConstraintExtraction:
    def test_range_and_decimals(self):
        kinds = {c.kind: c for c in extract_constraints(DESC_AMOUNT)}
        assert "range" in kinds and "decimal_places" in kinds
        rng = kinds["range"]
        assert rng.numbers == [0.0, 99999.0]
        assert rng.detail == {"min_inclusive": False, "max_inclusive": True}
        assert rng.raw == "(0,99999]"
        assert kinds["decimal_places"].numbers == [2.0]

    def test_length_and_count(self):
        kinds = {c.kind: c for c in extract_constraints(DESC_PHONE)}
        assert kinds["max_length"].numbers == [140.0]
        assert kinds["max_count"].numbers == [10.0]

    def test_formats_and_file_size(self):
        kinds = {c.kind: c for c in extract_constraints(DESC_UPLOAD)}
        assert kinds["formats"].detail["formats"] == ["xlsx", "xls", "csv"]
        assert kinds["file_size"].numbers == [1.0]

    def test_options_and_default(self):
        kinds = {c.kind: c for c in extract_constraints(DESC_CHANGE_TYPE)}
        assert kinds["options"].detail["options"][0] == "企业储值充值"
        assert kinds["default"].detail["value"] == "企业储值充值"

    def test_condition_clauses(self):
        cond = extract_condition(DESC_UPLOAD)
        assert "变动类型=企业储值充值" in cond
        assert "变更方式=批量导入" in cond

    def test_no_constraint_no_noise(self):
        assert extract_constraints("企业充值流水、企业退款流水；") == []


class TestHeadingFilter:
    @pytest.mark.parametrize("line", [
        "1. 需求说明", "1.1 背景说明", "2. 产品方案", "2.1 企业充值/退款",
        "2.1.1 原型图", "2.1.2 字段说明", "3. 待确认事项", "2.3 导入结果",
    ])
    def test_real_headings_are_filtered(self, line):
        assert is_heading_line(line) is True

    @pytest.mark.parametrize("line", [
        "B2B储值订单不能退款，储值订单不在C端显示",
        "主要支持单个储值新增及批量储值，支持优先新增保存功能，需要校验是否会员",
        "如不是小杨会员，新增和批量储值时需要提示不是会员的清单，其它用户完成保存",
        "需要提供储值的储值标签类型配置",
        "2、企业账户余额不足时，扣到0为止。",
    ])
    def test_real_rules_are_kept(self, line):
        assert is_heading_line(line) is False


def _build_docx(paragraphs: list[str], tables: list[list[list[str]]]) -> bytes:
    from docx import Document

    doc = Document()
    for text in paragraphs:
        doc.add_paragraph(text)
    for rows in tables:
        table = doc.add_table(rows=len(rows), cols=len(rows[0]))
        for r, row in enumerate(rows):
            for c, value in enumerate(row):
                table.cell(r, c).text = value
    buf = io.BytesIO()
    doc.save(buf)
    return buf.getvalue()


FIELD_TABLE = [
    ["字段", "字段类型", "是否必填", "字段说明"],
    ["变动类型", "单选组", "是", DESC_CHANGE_TYPE],
    ["变更方式", "单选组", "是", "选项范围：手动输入、批量导入默认选中：手动输入"],
    # 跨页续行：字段名为空，说明属于上一字段（真实 PDF 的表格就是这么裂开的）
    ["", "", "是", "最多输入140个字；"],
    ["会员手机号", "多行文本", "", "提示：最多输入10个手机号，若有多个，请以英文逗号隔如:13672432212"],
    ["企业账户充值金额", "数字输入框", "是", DESC_AMOUNT],
    ["上传文件（企业充值）", "上传文件组件", "是", DESC_UPLOAD],
]


class TestDocxStructuring:
    def test_field_rules_with_cross_row_merge(self):
        content = _build_docx(
            ["1. 需求说明", "1.1 背景说明", "B2B储值订单不能退款，储值订单不在C端显示；", "2.1.2 字段说明"],
            [FIELD_TABLE],
        )
        result = parse_requirement_document("需求.docx", None, content)

        names = [r.name for r in result.field_rules]
        assert names == ["变动类型", "变更方式", "会员手机号", "企业账户充值金额", "上传文件（企业充值）"], names

        phone = next(r for r in result.field_rules if r.name == "会员手机号")
        # 续行「是 / 最多输入140个字」应并入会员手机号
        assert phone.required is True
        assert phone.constraint("max_length").numbers == [140.0]
        assert phone.constraint("max_count").numbers == [10.0]

        amount = next(r for r in result.field_rules if r.name == "企业账户充值金额")
        assert amount.constraint("range").numbers == [0.0, 99999.0]
        assert amount.constraint("decimal_places").numbers == [2.0]
        assert "变动类型=企业储值充值" in amount.condition

        # 叙述文本不含表格内容
        assert "选项范围" not in result.narrative_text
        assert "B2B储值订单不能退款" in result.narrative_text


@pytest.mark.skipif(not REAL_PDF.exists(), reason="真实需求 PDF 不在本机")
class TestRealPdfStructuring:
    def test_real_document_fields(self):
        content = REAL_PDF.read_bytes()
        result = parse_requirement_document(REAL_PDF.name, "application/pdf", content)

        assert result.source_type == "pdf"
        names = [r.name for r in result.field_rules]
        for expected in ("变动类型", "变更方式", "企业账户充值金额", "企业账户退款金额",
                         "上传文件（企业充值）", "上传文件（企业退款）"):
            assert expected in names, f"缺少字段规则：{expected}（实际：{names}）"

        amount = next(r for r in result.field_rules if r.name == "企业账户充值金额")
        assert amount.constraint("range").raw == "(0,99999]"
        assert amount.constraint("decimal_places").numbers == [2.0]
        assert amount.required is True

        upload = next(r for r in result.field_rules if r.name == "上传文件（企业充值）")
        assert upload.constraint("formats").detail["formats"] == ["xlsx", "xls", "csv"]
        assert upload.constraint("file_size").numbers == [1.0]

        # PDF 表格文本不应残留在叙述文本里（否则会重复生成噪音用例）
        assert "文件格式仅支持" not in result.narrative_text
        # 模板表头表（手机号/充值金额）被单独保留
        headers = [t["header"] for t in result.template_tables]
        assert any("手机号" in h for h in headers)


class TestGeneratedCases:
    def _cases(self, content: bytes, name: str):
        from app.api.v2.ai import _build_minimal_cases

        structure = parse_requirement_document(name, None, content)
        # 现口径：_build_minimal_cases 返回 (cases, assertion_gaps)，用例列表取第一个元素
        cases, gaps_gaps, _gapsskips = _build_minimal_cases(
            structure.full_text, name, test_mode="sit", structure=structure
        )
        return cases, structure

    def test_no_heading_fragment_case_and_real_boundaries(self):
        content = _build_docx(
            ["1. 需求说明", "1.1 背景说明", "2.1 企业充值/退款", "2.1.2 字段说明",
             "主要支持单个储值新增及批量储值，支持优先新增保存功能，需要校验是否会员；"],
            [FIELD_TABLE],
        )
        cases, structure = self._cases(content, "小杨生煎-B端储值充值.docx")
        names = [c.name for c in cases]

        # 1) 章节标题不得成为用例（"1 企业充值/退款" 这类碎片）
        for bad in ("1 企业充值/退款", "2.1 企业充值/退款", "字段说明", "背景说明"):
            assert not any(bad == n or n.startswith(bad + "的") for n in names), f"标题碎片进了用例：{bad}"

        # 2) 边界用例引用文档真实值
        boundary = next(c for c in cases if "企业账户充值金额-边界值" in c.name)
        assert "(0,99999]" in boundary.name
        steps_text = " ".join(s.step + " " + s.result for s in boundary.test_case_steps)
        assert "99999" in steps_text and "0.01" in steps_text and "99999.01" in steps_text

        # 3) 文件规则用例（格式 + 大小）
        # rev181：字段名自带括号时标题改为「主语-后缀（限定）」，限定词仍在标题里
        upload = next(
            c for c in cases
            if "上传文件" in c.name and "企业充值" in c.name and "边界值" in c.name
        )
        upload_text = " ".join(s.step + " " + s.result for s in upload.test_case_steps)
        assert "xlsx" in upload_text and "1M" in upload_text

        # 3b) 上传字段的必填用例同样存在（文档标注"是否必填=是"）
        assert any("上传文件" in c.name and "企业充值" in c.name and "必填" in c.name
                   for c in cases)

        # 4) 条件显示用例
        assert any("条件显示校验" in n for n in names)

        # 5) 每个字段规则最多 3 条，且总量受限
        assert len(cases) <= 80

        # 6) 字段规则用例带原文位置（可追溯）
        assert all(c.source_location for c in cases if "字段规则" in (c.tags or []))

    def test_plain_text_path_unchanged(self):
        """没有表格时行为与原来一致（叙述 3 场景/条）。"""
        content = "B2B储值订单不能退款，储值订单不在C端显示；\n需要提供储值的储值标签类型配置；".encode("utf-8")
        cases, structure = self._cases(content, "需求.txt")
        assert structure.field_rules == []
        assert len(cases) == 2  # 无字段证据的业务条款不扩写虚构必填/边界场景
        assert all(c.test_case_steps for c in cases)


class TestCaseContract:
    """用例契约升级：规则编号 + 机器可判定断言。"""

    def _field_cases(self, filename: str = "小杨生煎-B端储值充值.docx"):
        content = _build_docx(["2.1.2 字段说明"], [FIELD_TABLE])
        from app.api.v2.ai import _build_minimal_cases

        structure = parse_requirement_document(filename, None, content)
        cases, gaps_gaps, _gapsskips = _build_minimal_cases(
            structure.full_text, filename, test_mode="sit", structure=structure
        )
        return [c for c in cases if "字段规则" in (c.tags or [])]

    def test_every_field_case_has_rule_id_and_source(self):
        cases = self._field_cases()
        assert cases, "未生成字段规则用例"
        for case in cases:
            rule_id = (case.custom_fields or {}).get("requirement_rule_id")
            assert rule_id, f"缺少规则编号: {case.name}"
            assert rule_id.startswith("FR-")
            assert case.tp_code == rule_id, "tp_code 应与规则编号一致"
            assert case.source_location and "字段说明表" in case.source_location

    def test_rule_id_is_stable_per_field(self):
        def by_field():
            mapping: dict[str, set[str]] = {}
            for case in self._field_cases():
                fields = case.custom_fields or {}
                field_name = fields["requirement_rule"]["field"]
                mapping.setdefault(field_name, set()).add(fields["requirement_rule_id"])
            return mapping

        first, second = by_field(), by_field()
        assert first == second, "同一文档同一字段的规则编号必须稳定"
        # 同一字段的多条用例共用同一个规则编号
        assert all(len(ids) == 1 for ids in first.values())
        # 不同字段不得同号
        ids = [next(iter(v)) for v in first.values()]
        assert len(ids) == len(set(ids))
        assert len(ids) >= 5, "本样例应覆盖 5 个以上字段"

    def test_boundary_case_assertions_cite_real_values(self):
        cases = self._field_cases()
        boundary = next(c for c in cases if "企业账户充值金额-边界值" in c.name)
        assertions = (boundary.custom_fields or {}).get("assertions") or []
        assert assertions, "边界用例必须带断言"
        pairs = {(a["data"], a["expect"]) for a in assertions if a["kind"] == "boundary"}
        assert ("0", "reject") in pairs
        assert ("0.01", "accept") in pairs
        assert ("99999", "accept") in pairs
        assert ("99999.01", "reject") in pairs
        assert all(a.get("rule") for a in assertions), "断言必须引用文档原文依据"

    def test_required_and_condition_assertions(self):
        cases = self._field_cases()
        required = next(c for c in cases if "会员手机号-必填" in c.name)
        kinds = {a["kind"] for a in (required.custom_fields or {}).get("assertions") or []}
        assert "required" in kinds
        condition = next(c for c in cases if "上传文件（企业充值）-条件显示" in c.name)
        cond_assertions = (condition.custom_fields or {}).get("assertions") or []
        assert {a["expect"] for a in cond_assertions} == {"visible", "hidden"}

    def test_narrative_cases_have_rule_id_but_no_fake_assertions(self):
        content = "B2B储值订单不能退款，储值订单不在C端显示；".encode("utf-8")
        from app.api.v2.ai import _build_minimal_cases

        structure = parse_requirement_document("需求.txt", None, content)
        cases, gaps_gaps, _gapsskips = _build_minimal_cases(
            structure.full_text, "需求.txt", test_mode="sit", structure=structure
        )
        assert cases
        for case in cases:
            fields = case.custom_fields or {}
            assert fields.get("requirement_rule_id", "").startswith("RQ-")
            assert "assertions" not in fields, "叙述类用例不得编造断言"
