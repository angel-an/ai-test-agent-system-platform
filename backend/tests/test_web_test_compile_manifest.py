# -*- coding: utf-8 -*-
"""N7 编译清单派生规则单测。

重点不是"能跑通"，而是**四种状态互不冒充**——尤其是
``compiled_check_count is None``（不知道）与 ``== 0``（确实零检查）必须分开，
否则"一条 SQL 列出零检查资产"会把 111 条从未编译的历史资产混进来。
"""

from __future__ import annotations

from datetime import datetime, timezone

from app.services.web_test_compile_manifest import (
    COMPILE_STATUS_COMPILED,
    COMPILE_STATUS_NOT_COMPILED,
    COMPILE_STATUS_PLAN_MISSING,
    COMPILE_STATUS_ZERO_CHECKS,
    COMPILE_STATUS_NOTES,
    apply_compile_manifest,
    compile_manifest_view,
    derive_compile_manifest,
    is_legacy_asset,
)


class _Asset:
    """最小资产替身：只带 apply/view 需要的属性。"""

    def __init__(self, **kwargs):
        self.generation_params = kwargs.get("generation_params")
        self.generated_by_agent = kwargs.get("generated_by_agent", "web_case_link_service")
        self.compile_status = kwargs.get("compile_status")
        self.compiled_check_count = kwargs.get("compiled_check_count")
        self.compiled_skipped_count = kwargs.get("compiled_skipped_count")
        self.compiled_at = kwargs.get("compiled_at")
        self.is_legacy_asset = kwargs.get("is_legacy_asset", False)


def _params(**summary):
    return {"mode": "minimal_loop", "compiled_cases": summary}


class TestDeriveStatus:
    def test_no_generation_params_is_not_compiled(self):
        derived = derive_compile_manifest(None, "web_agent")
        assert derived["compile_status"] == COMPILE_STATUS_NOT_COMPILED
        assert derived["compiled_check_count"] is None
        assert derived["compiled_skipped_count"] is None
        assert derived["is_legacy_asset"] is True

    def test_empty_manifest_object_is_not_compiled(self):
        # 键存在但为空对象＝没有编译产物，不能当成"零检查"
        derived = derive_compile_manifest({"compiled_cases": {}}, "web_case_link_service")
        assert derived["compile_status"] == COMPILE_STATUS_NOT_COMPILED
        assert derived["compiled_check_count"] is None
        assert derived["is_legacy_asset"] is False

    def test_missing_check_key_is_plan_missing_not_zero(self):
        """回归点（2026-10-17 首轮回填踩到）：缺键必须落到 check_plan_missing。

        迁移里的 SQL 最初写成 ``jsonb_typeof(...) <> 'number'``，键缺失时
        ``jsonb_typeof`` 返回 NULL、``NULL <> 'number'`` 也是 NULL（非真），
        CASE 一路落到 ELSE → 34 条"没生成检查计划"的资产被写成 zero_checks
        且计数为 NULL，自相矛盾。这里把这个判定钉死。
        """
        derived = derive_compile_manifest(
            _params(case_count=1, assertion_count=10), "web_case_link_service"
        )
        assert derived["compile_status"] == COMPILE_STATUS_PLAN_MISSING
        assert derived["compiled_check_count"] is None

    def test_explicit_null_check_key_is_plan_missing(self):
        derived = derive_compile_manifest(
            _params(form_executable_checks=None), "web_case_link_service"
        )
        assert derived["compile_status"] == COMPILE_STATUS_PLAN_MISSING
        assert derived["compiled_check_count"] is None

    def test_zero_checks_is_zero_not_none(self):
        derived = derive_compile_manifest(
            _params(form_executable_checks=0, form_skipped_no_locator=7),
            "web_case_link_service",
        )
        assert derived["compile_status"] == COMPILE_STATUS_ZERO_CHECKS
        assert derived["compiled_check_count"] == 0
        assert derived["compiled_skipped_count"] == 7

    def test_positive_checks_is_compiled(self):
        derived = derive_compile_manifest(
            _params(form_executable_checks=6, form_skipped_no_locator=1),
            "web_case_link_service",
        )
        assert derived["compile_status"] == COMPILE_STATUS_COMPILED
        assert derived["compiled_check_count"] == 6
        assert derived["compiled_skipped_count"] == 1

    def test_numeric_strings_are_accepted(self):
        # 历史摘要里计数可能是字符串（JSON 手改/旧版本序列化差异）
        derived = derive_compile_manifest(
            _params(form_executable_checks="12", form_skipped_no_locator="3"),
            "web_case_link_service",
        )
        assert derived["compiled_check_count"] == 12
        assert derived["compiled_skipped_count"] == 3

    def test_boolean_is_not_a_count(self):
        # JSON true 在 Python 里是 int 子类，不能读成"1 条检查"
        derived = derive_compile_manifest(
            _params(form_executable_checks=True), "web_case_link_service"
        )
        assert derived["compile_status"] == COMPILE_STATUS_PLAN_MISSING
        assert derived["compiled_check_count"] is None

    def test_garbage_count_is_plan_missing(self):
        derived = derive_compile_manifest(
            _params(form_executable_checks="n/a"), "web_case_link_service"
        )
        assert derived["compile_status"] == COMPILE_STATUS_PLAN_MISSING


class TestLegacyFlag:
    def test_legacy_generators(self):
        assert is_legacy_asset("web_agent") is True
        assert is_legacy_asset("web-agent") is True
        assert is_legacy_asset(" WEB_AGENT ") is True
        assert is_legacy_asset("web_case_link_service") is False
        assert is_legacy_asset(None) is False

    def test_legacy_asset_with_manifest_still_marked(self):
        # 标记与"有没有产物"是两件事：旧生成者若事后被重编译，仍要能看出它的血缘
        derived = derive_compile_manifest(
            _params(form_executable_checks=3), "web_agent"
        )
        assert derived["is_legacy_asset"] is True
        assert derived["compile_status"] == COMPILE_STATUS_COMPILED


class TestApply:
    def test_sets_columns_and_compiled_at_when_manifest_present(self):
        asset = _Asset(
            generation_params=_params(form_executable_checks=2),
            generated_by_agent="web_case_link_service",
        )
        now = datetime(2026, 10, 18, 2, 0, tzinfo=timezone.utc)
        apply_compile_manifest(asset, now=now)
        assert asset.compile_status == COMPILE_STATUS_COMPILED
        assert asset.compiled_check_count == 2
        assert asset.compiled_at == now

    def test_does_not_stamp_compiled_at_without_manifest(self):
        """无产物时不得写 compiled_at，也不得把已有值清掉。"""
        old = datetime(2026, 10, 1, tzinfo=timezone.utc)
        asset = _Asset(generation_params={"mode": "minimal_loop"}, compiled_at=old)
        apply_compile_manifest(asset)
        assert asset.compile_status == COMPILE_STATUS_NOT_COMPILED
        assert asset.compiled_at == old

    def test_reads_generation_params_from_asset_by_default(self):
        asset = _Asset(
            generation_params=_params(form_executable_checks=0),
            generated_by_agent="web_case_link_service",
        )
        apply_compile_manifest(asset)
        assert asset.compile_status == COMPILE_STATUS_ZERO_CHECKS
        assert asset.compiled_check_count == 0

    def test_explicit_params_override_asset_value(self):
        asset = _Asset(generation_params={"compiled_cases": {}})
        apply_compile_manifest(asset, generation_params=_params(form_executable_checks=5))
        assert asset.compiled_check_count == 5


class TestView:
    def test_view_distinguishes_unknown_from_zero(self):
        zero = compile_manifest_view(
            _Asset(compile_status=COMPILE_STATUS_ZERO_CHECKS, compiled_check_count=0)
        )
        unknown = compile_manifest_view(
            _Asset(compile_status=COMPILE_STATUS_NOT_COMPILED, compiled_check_count=None)
        )
        assert zero["compiled_check_count"] == 0
        assert unknown["compiled_check_count"] is None
        assert zero["compile_status_note"] != unknown["compile_status_note"]

    def test_view_serializes_time_and_legacy(self):
        asset = _Asset(
            compile_status=COMPILE_STATUS_COMPILED,
            compiled_check_count=4,
            compiled_at=datetime(2026, 10, 18, 3, 0, tzinfo=timezone.utc),
            is_legacy_asset=True,
        )
        view = compile_manifest_view(asset)
        assert view["compiled_at"] == "2026-10-18T03:00:00+00:00"
        assert view["is_legacy_asset"] is True

    def test_every_status_has_a_note(self):
        for status in (COMPILE_STATUS_COMPILED, COMPILE_STATUS_ZERO_CHECKS,
                       COMPILE_STATUS_PLAN_MISSING, COMPILE_STATUS_NOT_COMPILED):
            assert COMPILE_STATUS_NOTES[status]

    def test_unknown_status_does_not_crash(self):
        view = compile_manifest_view(_Asset())
        assert view["compile_status"] is None
        assert view["compile_status_note"] == "编译状态未知"
