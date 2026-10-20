# -*- coding: utf-8 -*-
"""历史批次绑定回填脚本（backfill_case_asset_bindings）回归。

口径（与 rev95 一致）：
- 批次内**每条**用例都绑定（含无断言的叙述型），``auto_executable`` 如实表达可执行性；
- 幂等：同资产同覆盖跳过，重复执行不重复改写（bound_at 不参与同 coverage 判定）；
- 主用例判定确定性：已锚定本资产的用例 → WebTest.test_case_id → 批次首条。
"""

from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace
from uuid import uuid4

from app.scripts.backfill_case_asset_bindings import (
    _partition_include,
    _resolve_binding_params,
    _select_primary,
    compute_group_plan,
)

NOW = datetime(2026, 9, 29, tzinfo=timezone.utc)
BOUND_AT = "2026-09-29T00:00:00"


def _case(identifier: str, *, assertions: list | None = None, rule_id: str = "",
          fields: dict | None = None, created: datetime | None = None):
    custom_fields = dict(fields or {})
    if rule_id:
        custom_fields["requirement_rule_id"] = rule_id
    if assertions is not None:
        custom_fields["assertions"] = assertions
    return SimpleNamespace(
        id=uuid4(), identifier=identifier, name=f"用例-{identifier}",
        custom_fields=custom_fields, created_at=created or NOW,
        test_case_steps=[],
    )


def _web_test(*, test_case_id=None, generation_params=None):
    return SimpleNamespace(
        id=uuid4(), identifier="WT-TEST-001", test_case_id=test_case_id,
        generation_params=generation_params or {},
    )


_ASSERTS = [{"kind": "field_value", "field": "Case Title", "expect": "required"}]


def _plan(cases, primary, web_test=None):
    web_test = web_test or _web_test()
    return compute_group_plan(
        web_test_id=web_test.id,
        web_test_identifier=web_test.identifier,
        batch_cases=cases,
        primary_case=primary,
        navigation_path=None, path_template_id=None, path_match_status="pending_confirmation",
        base_url_source=None, login_profile=None, sub_function_id=None,
        bound_at=BOUND_AT,
    ), web_test


class TestComputeGroupPlan:
    def test_binds_all_batch_cases_with_truthful_auto_executable(self):
        compiled = _case("TC-C1", assertions=_ASSERTS, rule_id="FR-1")
        narrative = _case("TC-N1")
        dangling = _case("TC-D1", rule_id="FR-9")  # 有规则号无断言
        primary = compiled
        plan, wt = _plan([compiled, narrative, dangling], primary)

        by_id = {c.identifier: f for c, f in plan}
        assert set(by_id) == {"TC-C1", "TC-N1", "TC-D1"}  # 逐条都绑（血缘可追溯）

        cov_c = by_id["TC-C1"]["web_coverage"]
        assert cov_c["role"] == "primary"
        assert cov_c["compile_status"] == "compiled"
        assert cov_c["assertion_count"] == 1
        assert cov_c["auto_executable"] is True

        cov_n = by_id["TC-N1"]["web_coverage"]
        assert cov_n["role"] == "batch"
        assert cov_n["compile_status"] == "narrative"
        assert cov_n["assertion_count"] == 0
        assert cov_n["auto_executable"] is False  # 如实：绑定了 ≠ 可自动覆盖

        cov_d = by_id["TC-D1"]["web_coverage"]
        assert cov_d["compile_status"] == "dangling_rule"
        assert cov_d["auto_executable"] is False

        # 绑定字段带资产身份与路径字段
        assert by_id["TC-C1"]["web_test_id"] == str(wt.id)
        assert by_id["TC-C1"]["web_test_identifier"] == wt.identifier
        assert by_id["TC-C1"]["web_primary_case"] is True
        assert by_id["TC-N1"]["web_primary_case"] is False

    def test_idempotent_second_run_returns_empty(self):
        compiled = _case("TC-C1", assertions=_ASSERTS, rule_id="FR-1")
        narrative = _case("TC-N1")
        cases = [compiled, narrative]
        plan1, wt = _plan(cases, compiled)
        assert len(plan1) == 2
        for case, fields in plan1:
            case.custom_fields = fields  # 模拟 apply 落库
        plan2, _ = _plan(cases, compiled, web_test=wt)
        assert plan2 == []  # 同资产同覆盖 → 全部跳过，bound_at 差异不触发重写

    def test_roles_stable_regardless_of_input_order(self):
        """复核要求：同一输入乱序执行两次 → primary/batch 角色完全一致
        （历史 bug：主用例依赖库返回顺序，导致 288/290 计数漂移）。"""
        cases = [
            _case("TC-B1", assertions=_ASSERTS, rule_id="FR-2"),
            _case("TC-A1", assertions=_ASSERTS, rule_id="FR-1"),
        ]
        roles_runs = []
        for _ in range(2):
            plan, wt = _plan(list(reversed(cases)), cases[0])
            roles_runs.append({
                f["web_coverage"]["role"] for _, f in plan
            })
            for case, fields in plan:
                case.custom_fields = fields
        assert roles_runs[0] == roles_runs[1] == {"primary", "batch"}

    def test_compile_status_refresh_picks_up_new_assertions(self):
        """回填的价值：用例补了断言后重跑，compile_status 从 narrative 升 compiled。"""
        case = _case("TC-C1")  # 首次：无断言
        plan1, wt = _plan([case], case)
        assert plan1[0][1]["web_coverage"]["compile_status"] == "narrative"
        case.custom_fields = plan1[0][1]
        case.custom_fields["assertions"] = _ASSERTS  # 补断言（如重生成）
        case.custom_fields.pop("web_coverage")
        plan2, _ = _plan([case], case, web_test=wt)
        assert plan2[0][1]["web_coverage"]["compile_status"] == "compiled"
        assert plan2[0][1]["web_coverage"]["auto_executable"] is True


class TestSelectPrimary:
    def test_anchor_case_wins(self):
        wt = _web_test()
        anchored = _case("TC-A", fields={"web_test_id": str(wt.id)})
        other = _case("TC-B")
        assert _select_primary([other, anchored], wt).identifier == "TC-A"

    def test_marked_primary_wins_for_stability(self):
        """回填后批次内每条用例都带 web_test_id：主用例必须由 web_primary_case
        标记锁定，否则退化为依赖库返回顺序，role 会在两次执行间翻转（实测 bug）。"""
        wt = _web_test()
        marked = _case("TC-M", fields={"web_test_id": str(wt.id), "web_primary_case": True})
        anchored_new = _case("TC-N", fields={"web_test_id": str(wt.id)})
        assert _select_primary([anchored_new, marked], wt).identifier == "TC-M"

    def test_fallback_web_test_anchor_then_first_sorted(self):
        wt = _web_test(test_case_id=None)
        a = _case("TC-Z", created=NOW)
        b = _case("TC-A", created=NOW)
        assert _select_primary([a, b], wt).identifier == "TC-A"  # 确定性：identifier 排序首条

        wt2_id = uuid4()
        wt2 = _web_test(test_case_id=wt2_id)
        anchor = _case("TC-M")
        anchor.id = wt2_id
        assert _select_primary([b, anchor], wt2).identifier == "TC-M"


class TestPartitionInclude:
    """争议归属规则（2026-09-29 实测修复：多资产声明同一用例时互相抢写、重复执行永不归零）。"""

    ASSET_A = "aaaaaaaa-0000-0000-0000-000000000001"
    ASSET_B = "bbbbbbbb-0000-0000-0000-000000000002"

    def _case_with(self, identifier, web_test_id):
        return _case(identifier, fields={"web_test_id": web_test_id} if web_test_id else {})

    def test_owned_included_bound_elsewhere_excluded(self):
        owned = self._case_with("TC-1", self.ASSET_A)
        other = self._case_with("TC-2", self.ASSET_B)
        include, elsewhere, orphan = _partition_include(
            [owned, other],
            web_test_id=self.ASSET_A,
            valid_asset_ids={self.ASSET_A, self.ASSET_B},
            winner_id_by_case={},
        )
        assert [c.identifier for c in include] == ["TC-1"]
        assert [c.identifier for c in elsewhere] == ["TC-2"]  # 别人的绑定不动
        assert orphan == []

    def test_contested_unbound_only_winner_included(self):
        contested = self._case_with("TC-1", "")
        include, elsewhere, orphan = _partition_include(
            [contested],
            web_test_id=self.ASSET_A,
            valid_asset_ids={self.ASSET_A, self.ASSET_B},
            winner_id_by_case={contested.id: self.ASSET_B},  # 最新资产赢得归属
        )
        assert include == [] and [c.identifier for c in elsewhere] == ["TC-1"]
        assert orphan == []

    def test_widow_binding_treated_as_unbound(self):
        """绑定目标资产已不存在（孤儿绑定）→ 视同未绑定、单独计 orphan 供审计。"""
        orphan_case = self._case_with("TC-1", "deadbeef-0000-0000-0000-000000000009")
        include, elsewhere, orphan = _partition_include(
            [orphan_case],
            web_test_id=self.ASSET_A,
            valid_asset_ids={self.ASSET_A},
            winner_id_by_case={orphan_case.id: self.ASSET_A},
        )
        assert [c.identifier for c in include] == ["TC-1"] and elsewhere == []
        assert [c.identifier for c in orphan] == ["TC-1"]


class TestResolveBindingParams:
    def test_primary_fields_preferred_gp_fallback(self):
        wt = _web_test(generation_params={
            "web_path_template_id": "tpl-gp", "sub_function_id": "sf-gp",
            "web_path_match_status": "matched",
        })
        primary = _case("TC-P", fields={
            "web_validation_path": "/cs/create", "web_path_template_id": "tpl-primary",
            "web_path_match_status": "matched", "login_profile": "WEB_TEST_CS",
        })
        params = _resolve_binding_params(web_test=wt, primary_case=primary)
        assert params["navigation_path"] == "/cs/create"
        assert params["path_template_id"] == "tpl-primary"  # 主用例优先
        assert params["path_match_status"] == "matched"
        assert params["login_profile"] == "WEB_TEST_CS"
        assert params["sub_function_id"] == "sf-gp"  # 主用例缺 → gp 兜底

    def test_invalid_status_defaults_pending_confirmation(self):
        wt = _web_test(generation_params={})
        primary = _case("TC-P", fields={"web_path_match_status": "whatever"})
        params = _resolve_binding_params(web_test=wt, primary_case=primary)
        assert params["path_match_status"] == "pending_confirmation"
