"""P2：导入子项继承、换行恢复及人工补充来源隔离。"""
from pathlib import Path

import pytest

from app.api.v2.ai import (
    _build_import_clause_cases,
    _build_import_supplement_cases,
    _build_minimal_cases,
    _extract_import_clauses,
)

PARENT = "特殊字段：同一个字段支持传多组值时，第一組為必填；每組數據單獨檢驗完整性，缺失時導入失敗"
TEXT = (
    "1.2.3.2 導入規則：\n9. " + PARENT + "\n"
    "a. 店鋪電話1-區號、店鋪電話1；\n"
    "b. 搜索關鍵詞1、搜索關鍵詞2、搜索關鍵詞3；\n"
    "c. 營業時間1-周幾？、營業時間1-時間1；\n"
)


def test_children_inherit_rule_and_have_distinct_ids():
    clauses = _extract_import_clauses(TEXT)
    assert len(clauses) == 4
    assert all(PARENT in c for c in clauses)
    cases = _build_import_clause_cases(TEXT, "需求.pdf")
    assert len({c.custom_fields["requirement_rule_id"] for c in cases}) == 4
    assert any("搜索關鍵詞3" in c.description for c in cases)
    assert len(_extract_import_clauses(TEXT, limit=2)) == 2
    assert _extract_import_clauses(TEXT, limit=0) == []


def test_wrapped_rule_preserves_complete_result():
    clauses = _extract_import_clauses(
        "3. 如果導入的excel中存在多條店鋪編號相同的數據，\n"
        "===== PAGE 3 =====\n則店鋪編號相同的數據全部導入失敗；\n"
    )
    assert len(clauses) == 1
    assert clauses[0].endswith("數據全部導入失敗；")


def test_no_orphan_or_cross_section_children():
    assert _extract_import_clauses("a. 店鋪電話1-區號、店鋪電話1；") == []
    clauses = _extract_import_clauses(TEXT + "2. 商品主數據\na. 商品名稱填寫完整；")
    assert len(clauses) == 4


def test_full_text_dedup_does_not_collapse_shared_prefix():
    prefix = "必填字段校验：" + "同一组数据" * 15
    clauses = _extract_import_clauses(f"导入规则\n1. {prefix}电话不可为空\n2. {prefix}区号不可为空")
    assert len(clauses) == 2
    assert len(_extract_import_clauses(TEXT + TEXT)) == 4


def test_pending_overlap_has_no_requirement_rule_or_assertions():
    case, = _build_import_supplement_cases(TEXT, "需求.pdf")
    assert case.custom_fields["manual_supplement"] is True
    assert case.custom_fields["confirmation_status"] == "pending"
    assert "requirement_rule_id" not in case.custom_fields
    assert "requirement_rule" not in case.custom_fields
    assert "assertions" not in case.custom_fields
    assert _build_import_supplement_cases(TEXT + "時間段重疊時拒絕導入", "需求.pdf") == []
    assert _build_import_supplement_cases("普通字段导入规则", "需求.pdf") == []


def test_import_only_document_reaches_generation():
    cases, _ = _build_minimal_cases(TEXT, "需求.txt")
    assert sum(bool(c.custom_fields.get("import_clause_case")) for c in cases) == 4
    assert sum(bool(c.custom_fields.get("manual_supplement")) for c in cases) == 1


YL_PDF = Path("C:/Users/admin/WorkBuddy/2026-09-29-15-29-56/.workbuddy/tmp/yulon_md/requirement.pdf")


@pytest.mark.skipif(not YL_PDF.exists(), reason="真实 YL PDF 不在本机")
def test_real_yl_import_children():
    from app.services.requirement_structure import parse_requirement_document

    structure = parse_requirement_document("YL.pdf", "application/pdf", YL_PDF.read_bytes())
    clauses = _extract_import_clauses(structure.full_text)
    for field in ("店鋪電話1", "搜索關鍵詞1", "營業時間1"):
        assert any("子项：" in c and field in c and "第一組為必填" in c for c in clauses)
    cases = _build_import_clause_cases(structure.full_text, "YL.pdf")
    assert len({c.custom_fields["requirement_rule_id"] for c in cases}) == len(cases)
    assert len(_build_import_supplement_cases(structure.full_text, "YL.pdf")) == 1
