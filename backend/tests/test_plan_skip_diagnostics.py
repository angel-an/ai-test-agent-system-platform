# -*- coding: utf-8 -*-
"""P4 补齐：计划期跳过（断言根本没进入执行）必须可诊断 —— 回归用例。

背景（2026-09-29 现场）：P4 的三次实测验收都拿不到 `[observation]`，因为注入的"破损"
发生在**计划期**而不是运行期，其中一种就是把断言的字段名改成了定位器表里不存在的名字：
  * 该断言不会生成任何检查 → 运行期不存在"未定位到输入框"这条路径；
  * stdout 里只剩 `skipped_by_reason: {"无定位器": 1}`（一个词），无法判断该怎么改配置。
本用例锁定"跳过也要给出用例/规则号 + 已声明字段 + 近似候选"这一口径。
"""

from __future__ import annotations

import json
from types import SimpleNamespace

from app.services.assertion_ledger import build_assertion_ledger, parse_job_ledger
from app.services.web_case_compiler import _suggest_fields, build_form_plan


def _case(identifier: str, field: str, rule_id: str = "R1", kinds=("max_length",)):
    return SimpleNamespace(
        identifier=identifier,
        name=f"{identifier} 校验",
        custom_fields={
            "requirement_rule_id": rule_id,
            "requirement_rule": {"field": field},
            "assertions": [
                {"kind": kind, "field": field, "data": "50字", "expect": "accept",
                 "rule": f"{field}≤50字"} for kind in kinds
            ],
        },
    )


def _manifest(*cases):
    from app.services.web_case_compiler import compile_cases

    return compile_cases(list(cases))


LOCATORS = {"fields": {"限购描述": {"kind": "input", "placeholder_hint": "描述"},
                       "限购标签": {"kind": "input"}}}


class TestSuggestFields:
    def test_exact_and_containment_candidates(self):
        assert _suggest_fields("限购描述", ["限购描述", "限购标签", "状态"]) == ["限购描述"]
        assert "限购描述" in _suggest_fields("限购描述文案", ["限购描述", "限购标签"])

    def test_parenthesis_suffix_is_normalized(self):
        # "上传文件（企业充值）" 与 "上传文件" 视为同一字段
        assert "上传文件" in _suggest_fields("上传文件（企业充值）", ["上传文件", "金额"])

    def test_unrelated_names_get_no_suggestion(self):
        assert _suggest_fields("完全不相关的名字", ["限购描述", "限购标签"]) == []

    def test_empty_needle_is_safe(self):
        assert _suggest_fields("", ["限购描述"]) == []
        assert _suggest_fields(None, []) == []


class TestPlanSkipDiagnostics:
    def test_unknown_field_is_skipped_with_context(self):
        manifest = _manifest(_case("TC-1", "限购描述文案"))
        plan = build_form_plan(manifest, LOCATORS)
        skipped = plan["skipped"]
        assert len(skipped) == 1
        record = skipped[0]
        assert record["field"] == "限购描述文案"
        assert record["reason"] == "无定位器"
        assert record["case_id"] == "TC-1"
        assert record["rule_id"] == "R1"
        assert record["kinds"] == ["max_length"]
        assert set(record["declared_fields"]) == {"限购描述", "限购标签"}
        assert "限购描述" in record["suggestions"]     # 一次就能改对

    def test_known_field_is_not_skipped(self):
        manifest = _manifest(_case("TC-2", "限购描述"))
        plan = build_form_plan(manifest, LOCATORS)
        assert plan["skipped"] == []
        assert plan["checks"]


class TestLedgerPlanSkips:
    def test_plan_skip_lines_are_parsed_and_warned(self):
        stdout = "\n".join([
            "[plan-skip] " + json.dumps({
                "field": "限购描述文案", "reason": "无定位器", "case_id": "TC-1",
                "rule_id": "R1", "kinds": ["max_length"],
                "declared_fields": ["限购描述"], "suggestions": ["限购描述"],
            }, ensure_ascii=False),
            'COMPILED_ASSERTION {"name":"default_selected:状态=启用","outcome":"pass",'
            '"observed":"默认选中=启用","rule_id":"","case_id":""}',
        ])
        ledger = parse_job_ledger(stdout)
        assert ledger["plan_skip_count"] == 1
        assert ledger["plan_skips"][0]["field"] == "限购描述文案"

        jobs = [SimpleNamespace(script_identifier="WT-X", status="completed", stdout=stdout)]
        built = build_assertion_ledger(jobs)
        assert built["totals"]["plan_skips"] == 1
        assert any("未进入执行" in w and "候选" in w for w in built["warnings"])
        assert built["jobs"][0]["plan_skip_count"] == 1

    def test_no_plan_skip_means_no_warning(self):
        jobs = [SimpleNamespace(script_identifier="WT-Y", status="completed", stdout="")]
        built = build_assertion_ledger(jobs)
        assert built["totals"]["plan_skips"] == 0
        assert not any("未进入执行" in w for w in built["warnings"])
