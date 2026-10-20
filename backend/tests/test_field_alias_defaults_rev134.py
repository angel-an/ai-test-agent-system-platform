"""rev134 回归：字段别名 / 默认值声明**确实生效**，且不做模糊匹配。

背景（2026-10-20 问卷批次实测）：
- 需求规则名叫「标题」「选项内容」，定位器键叫「题目标题」「选项1」；
  编译器旧行为是**精确匹配**，于是 8 条断言全部进 `pending_locator`，
  而回执只写「无定位器」，配置方会去补定位器——真正要改的其实是别名表。
- `aliases` / `defaults` 是**行为配置**，不是选择器；它们出现在定位器键清单里
  会让"配了 9 个定位器"这种数字失真。
- 别名配错（目标键不存在）在旧实现里与"没配别名"完全同形，都是无定位器跳过。
  本版单列 `field_alias_dangling`，让"配错"和"没配"可区分。

铁律：**不做模糊自动匹配**。`_suggest_fields` 对「标题」给出的第一个候选是
「问卷标题」，自动挑最相似会把"导出用例挂上必填断言"的假通过重新引回来
（rev133 刚修掉的那类）。别名必须由配置显式声明。
"""

from __future__ import annotations

import pytest

from app.services.web_case_compiler import (
    NON_LOCATOR_CONFIG_KEYS,
    build_form_plan,
)


def _loc(**extra):
    base = {
        "fields": {
            "题目标题": {"kind": "input", "max_length": 50},
            "选项1": {"kind": "input", "max_length": 20},
            "问卷标题": {"kind": "input", "max_length": 20},
            "适用门店": {"kind": "radio", "options": ["全部门店", "指定门店", "排除门店"]},
        }
    }
    base.update(extra)
    return base


def _manifest(*cases):
    return {"assertion_count": sum(len(c["assertions"]) for c in cases), "cases": list(cases)}


class TestAliasApplied:
    def test_alias_resolves_rule_field_to_locator_key(self):
        loc = _loc(aliases={"标题": "题目标题", "选项内容": "选项1"})
        plan = build_form_plan(_manifest(
            {"identifier": "TC-A1", "rule_id": "FR-1", "field": "标题",
             "assertions": [{"kind": "required", "field": "标题"}]},
            {"identifier": "TC-A2", "rule_id": "FR-2", "field": "选项内容",
             "assertions": [{"kind": "required", "field": "选项内容"}]},
        ), loc)
        fields_hit = {c["field"] for c in plan["checks"]}
        assert fields_hit == {"题目标题", "选项1"}, fields_hit
        assert plan["skipped_count"] == 0

    def test_applied_audit_records_every_hit(self):
        """别名"配了"不等于"命中"——回执必须给出真实改写次数。"""
        loc = _loc(aliases={"标题": "题目标题"})
        plan = build_form_plan(_manifest(
            {"identifier": "TC-A1", "rule_id": "FR-1", "field": "标题",
             "assertions": [
                 {"kind": "required", "field": "标题"},
                 {"kind": "max_length", "field": "标题", "data": "51字", "expect": "reject"},
             ]},
        ), loc)
        applied = plan["field_alias_applied"]
        assert applied == [{"from": "标题", "to": "题目标题", "resolved": applied[0]["resolved"]}]
        assert applied[0]["resolved"] >= 2

    def test_unused_alias_stays_out_of_applied_audit(self):
        loc = _loc(aliases={"标题": "题目标题", "根本没用到的名字": "选项1"})
        plan = build_form_plan(_manifest(
            {"identifier": "TC-A1", "rule_id": "FR-1", "field": "标题",
             "assertions": [{"kind": "required", "field": "标题"}]},
        ), loc)
        assert [a["from"] for a in plan["field_alias_applied"]] == ["标题"]

    def test_dangling_alias_is_reported_not_silent(self):
        loc = _loc(aliases={"标题": "题目标题v2"})       # 目标键拼错
        plan = build_form_plan(_manifest(
            {"identifier": "TC-A1", "rule_id": "FR-1", "field": "标题",
             "assertions": [{"kind": "required", "field": "标题"}]},
        ), loc)
        assert plan["field_alias_applied"] == []
        assert plan["field_alias_dangling"] == [{"from": "标题", "to": "题目标题v2"}]
        assert plan["skipped_count"] == 1               # 仍然没落地，如实记跳过


class TestNoFuzzyMatching:
    def test_near_name_does_not_auto_bind(self):
        """「标题」不得自动绑到「问卷标题」——那正是 rev133 修掉的假通过。"""
        loc = _loc()                                    # 有 问卷标题，无 题目标题，且没配别名
        loc["fields"].pop("题目标题")
        plan = build_form_plan(_manifest(
            {"identifier": "TC-B1", "rule_id": "FR-9", "field": "标题",
             "assertions": [{"kind": "required", "field": "标题"}]},
        ), loc)
        assert plan["checks"] == []
        assert plan["skipped"][0]["field"] == "标题"
        # 只给候选，不替用户决定
        assert "问卷标题" in plan["skipped"][0]["suggestions"]

    def test_blank_alias_target_is_ignored(self):
        loc = _loc(aliases={"标题": ""})
        plan = build_form_plan(_manifest(
            {"identifier": "TC-B2", "rule_id": "FR-9", "field": "标题",
             "assertions": [{"kind": "required", "field": "标题"}]},
        ), loc)
        assert plan["checks"] == []
        assert plan["field_alias_applied"] == []


class TestDefaultsDeclaration:
    def test_defaults_produce_observation_check(self):
        loc = _loc(defaults=[{"field": "适用门店", "option": "全部门店"}])
        plan = build_form_plan(_manifest(
            {"identifier": "TC-C0", "rule_id": "FR-0", "field": "业务类型",
             "assertions": [{"kind": "required", "field": "问卷标题"}]},
        ), loc)
        defaults = [c for c in plan["checks"] if c["type"] == "default_selected"]
        assert [(c["field"], c["option"]) for c in defaults] == [("适用门店", "全部门店")]

    def test_defaults_cover_matching_default_assertion(self):
        """声明了默认观测项后，同字段同选项的 `default` 断言不再进 skipped。"""
        loc = _loc(defaults=[{"field": "适用门店", "option": "全部门店"}])
        plan = build_form_plan(_manifest(
            {"identifier": "TC-C1", "rule_id": "FR-1", "field": "适用门店",
             "assertions": [{"kind": "default", "field": "适用门店", "expect": "全部门店"}]},
        ), loc)
        assert plan["skipped_count"] == 0
        observation = [c for c in plan["checks"] if c["type"] == "default_selected"][0]
        # 归属必须挂在用例上，否则"检查执行了但没人为它取证"
        assert [o["case_id"] for o in observation["owners"]] == ["TC-C1"]

    def test_default_assertion_still_skipped_without_declaration(self):
        loc = _loc()
        plan = build_form_plan(_manifest(
            {"identifier": "TC-C2", "rule_id": "FR-1", "field": "适用门店",
             "assertions": [{"kind": "default", "field": "适用门店", "expect": "全部门店"}]},
        ), loc)
        assert plan["skipped_count"] == 1
        assert "默认观测项" in plan["skipped"][0]["reason"]


class TestBehaviourConfigNotCountedAsLocator:
    @pytest.mark.parametrize("key", ["aliases", "defaults", "upload_allow", "negative_scenarios"])
    def test_behaviour_keys_excluded_from_locator_list(self, key):
        assert key in NON_LOCATOR_CONFIG_KEYS


class TestReconcileAccountsForConfigOnlyChecks:
    """rev136：对账行必须把"配置驱动的检查"排除在断言恒等式之外。

    TR-752 实测：3 条用例共 4 条断言，`defaults` 又产生 2 条默认选中观测
    ⇒ 旧公式 `planned - (executable + skipped)` = 4 - 6 = **-2**，
    `balanced=false` 报了一个并不存在的缺口。这类假缺口会让人去追"丢了的断言"，
    而真正要看的结论（4 条断言有没有全部落地）反而被淹没。
    """

    def _script(self):
        from app.services.web_case_compiler import render_form_executor

        plan = build_form_plan(_manifest(
            {"identifier": "TC-R1", "rule_id": "FR-1", "field": "适用门店",
             "assertions": [{"kind": "default", "field": "适用门店", "expect": "全部门店"}]},
        ), _loc(defaults=[{"field": "适用门店", "option": "全部门店"}]))
        return render_form_executor(plan)

    def test_formula_subtracts_config_only(self):
        script = self._script()
        assert "config_only_checks" in script
        assert "(__execA - __configA)" in script

    def test_config_only_is_reported_so_reader_can_verify(self):
        script = self._script()
        assert "config_only: __configA" in script

