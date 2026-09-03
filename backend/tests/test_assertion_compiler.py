"""rev56：Web 断言编译测试。

- compile_expected_results：明确断言 → compiled；模糊 → human_oracle；
- evaluate_compiled_assertions：执行后只校验 compiled（不现场发明）。
"""

import pytest

from app.agents.tools.web.assertion_compiler import (
    build_evidence_from_self_reflect,
    compile_expected_results,
    evaluate_compiled_assertions,
    evaluate_run_assertions,
)


class TestCompileExpectedResults:
    def test_clear_assertions_compiled(self):
        """明确可机检断言 → compiled。"""
        out = compile_expected_results(["订单状态为已发货", "页面显示配送中"])
        assert out["mode"] == "compiled"
        types = {a["type"] for a in out["assertions"]}
        assert types == {"field", "contains"}
        assert out["human_oracle"] == []

    def test_numeric_compiled(self):
        out = compile_expected_results(["余额=735.51"])
        assert out["mode"] == "compiled"
        a = out["assertions"][0]
        assert a["type"] == "numeric"
        assert a["key"] == "余额"
        assert a["value"] == 735.51

    def test_vague_becomes_human_oracle(self):
        """纯模糊（成功/正常）→ human_oracle（不进入自动校验）。"""
        out = compile_expected_results(["成功", "验证通过"])
        assert out["mode"] == "human_oracle"
        assert out["assertions"] == []
        assert len(out["human_oracle"]) == 2

    def test_mixed_mode(self):
        out = compile_expected_results(["订单状态为已发货", "结果正常"])
        assert out["mode"] == "mixed"
        assert len(out["assertions"]) == 1
        assert len(out["human_oracle"]) == 1

    def test_list_and_numbered_input(self):
        """列表输入 + 编号前缀剥离。"""
        out = compile_expected_results(["1. 状态为生效", "2. 页面包含会员日名称"])
        assert out["mode"] == "compiled"
        assert len(out["assertions"]) == 2

    def test_none_empty(self):
        out = compile_expected_results(None)
        assert out["mode"] == "human_oracle"
        assert out["assertions"] == []
        out2 = compile_expected_results([])
        assert out2["assertions"] == []


class TestEvaluateCompiledAssertions:
    def test_field_match(self):
        asserts = [{"type": "field", "key": "状态", "op": "=", "value": "已发货"}]
        ev = evaluate_compiled_assertions(asserts, {"fields": {"状态": "已发货"}})
        assert ev["passed"] == 1 and ev["failed"] == 0

    def test_field_mismatch(self):
        asserts = [{"type": "field", "key": "状态", "op": "=", "value": "已发货"}]
        ev = evaluate_compiled_assertions(asserts, {"fields": {"状态": "待审核"}})
        assert ev["failed"] == 1

    def test_contains_text(self):
        asserts = [{"type": "contains", "target": "配送中"}]
        ev = evaluate_compiled_assertions(asserts, {"page_text": "订单当前状态：配送中"})
        assert ev["passed"] == 1

    def test_numeric_field(self):
        asserts = [{"type": "numeric", "key": "余额", "op": "=", "value": 735.51}]
        ev = evaluate_compiled_assertions(asserts, {"fields": {"余额": "735.51"}})
        assert ev["passed"] == 1
        ev2 = evaluate_compiled_assertions(asserts, {"fields": {"余额": "100"}})
        assert ev2["failed"] == 1

    def test_status_text(self):
        asserts = [{"type": "status", "expected": "保存成功"}]
        ev = evaluate_compiled_assertions(asserts, {"status_text": "保存成功"})
        assert ev["passed"] == 1

    def test_no_invention_empty_assertions(self):
        """无 compiled 断言（human_oracle）→ 执行只校验空集，不现场发明。"""
        ev = evaluate_compiled_assertions([], {"page_text": "anything"})
        assert ev["total"] == 0 and ev["passed"] == 0 and ev["failed"] == 0

    def test_missing_evidence_fails_cleanly(self):
        asserts = [{"type": "contains", "target": "会员日"}]
        ev = evaluate_compiled_assertions(asserts, {"page_text": ""})
        assert ev["failed"] == 1


class TestEvidenceClosure:
    """rev58（执行证据闭环）：self_reflect 证据构建 + 执行后评估。"""

    def test_evidence_from_structured_self_reflect(self):
        sr = {
            "execution_status": "passed",
            "evidence": {
                "status_text": "保存成功",
                "page_text": "订单详情页显示配送中",
                "fields": {"订单状态": "已发货", "余额": "735.51"},
            },
        }
        ev = build_evidence_from_self_reflect(sr)
        assert ev["status_text"] == "保存成功"
        assert "配送中" in ev["page_text"]
        assert ev["fields"]["余额"] == "735.51"

    def test_evidence_degraded_from_steps(self):
        """无 evidence 字段 → status_text 由 execution_status 映射，page_text 由步骤拼接。"""
        sr = {
            "execution_status": "passed",
            "steps": [{"detail": "步骤1: 点击保存"}, {"detail": "步骤2: 见配送中"}],
        }
        ev = build_evidence_from_self_reflect(sr)
        assert ev["status_text"] == "成功"  # passed → 成功
        assert "配送中" in ev["page_text"]

    def test_evaluate_run_assertions_uses_self_reflect(self):
        """执行后评估：证据直接取自 self_reflect_result.json。"""
        asserts = [
            {"type": "field", "key": "订单状态", "op": "=", "value": "已发货"},
            {"type": "contains", "target": "配送中"},
        ]
        sr = {
            "execution_status": "passed",
            "evidence": {
                "page_text": "订单详情页显示配送中",
                "fields": {"订单状态": "已发货"},
            },
        }
        ev = evaluate_run_assertions(asserts, sr)
        assert ev["passed"] == 2 and ev["failed"] == 0

    def test_evaluate_run_assertions_none_sr(self):
        """无 self_reflect 数据 → 证据为空，compiled 断言评估失败不崩溃。"""
        asserts = [{"type": "contains", "target": "会员日"}]
        ev = evaluate_run_assertions(asserts, None)
        assert ev["failed"] == 1
        assert ev["total"] == 1
