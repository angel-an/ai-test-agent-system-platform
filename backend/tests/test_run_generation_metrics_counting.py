# -*- coding: utf-8 -*-
"""运行级四指标的**计数口径**回归（盘点对不上问题的根因修复）。

实测背景（2026-10-07 复核）：
  * 同一批次编译进 2 个资产时，`rule_case_count` / `compiled_assertion_count` 被**求和翻倍**
    （36 条用例的批次 → 规则用例 20 报成 40、编译断言 50 报成 100），
    而同一函数里的用例状态分类用的是 `max` 去重 → 四指标自相矛盾；
  * 作业引用的资产在资产表中已不存在时（全库 253 个作业里 87 个），
    执行资产数仍把它算进去，但资产台账查不到 → 两处数字永远对不上，且**没有任何提示**。
"""

from __future__ import annotations

from types import SimpleNamespace

from app.services.run_generation_metrics import build_generation_metrics


def _asset(asset_id: str, *, case_ids: list[str], case_count: int, rule: int, assertions: int,
           status: dict | None = None, with_ids: bool = True) -> SimpleNamespace:
    summary: dict = {
        "case_count": case_count,
        "rule_case_count": rule,
        "assertion_count": assertions,
        "case_status_counts": status or {"compiled": rule, "narrative": case_count - rule,
                                         "dangling_rule": 0},
        "uncompiled_cases": [],
    }
    if with_ids:
        summary["case_identifiers"] = case_ids
    return SimpleNamespace(id=asset_id, identifier=asset_id,
                           generation_params={"compiled_cases": summary})


def _cases(n: int) -> list[SimpleNamespace]:
    return [SimpleNamespace(identifier=f"TC-{i}") for i in range(n)]


class TestBatchDedup:
    def test_same_batch_in_two_assets_is_counted_once(self):
        """同一批次编译进 2 个资产 → 规则用例数/断言数不得翻倍（状态分类也不得翻倍）。"""
        ids = [f"TC-{i}" for i in range(36)]
        metrics = build_generation_metrics(
            batch_cases=_cases(36),
            web_tests=[_asset("WT-A", case_ids=ids, case_count=36, rule=20, assertions=50),
                       _asset("WT-B", case_ids=ids, case_count=36, rule=20, assertions=50)],
            jobs=[], run_cases=[],
        )
        assert metrics["generated_case_count"] == 36
        assert metrics["rule_case_count"] == 20, "同一批次被求和翻倍"
        assert metrics["compiled_assertion_count"] == 50, "同一批次被求和翻倍"
        assert metrics["compiled_case_count"] == 20
        assert metrics["case_status_counts"]["narrative"] == 16

    def test_different_batches_are_summed(self):
        """不同批次仍应相加（去重只针对同一批次）。"""
        metrics = build_generation_metrics(
            batch_cases=_cases(10),
            web_tests=[_asset("WT-A", case_ids=[f"TC-{i}" for i in range(6)],
                              case_count=6, rule=3, assertions=9),
                       _asset("WT-B", case_ids=[f"TC-{i}" for i in range(6, 10)],
                              case_count=4, rule=2, assertions=5)],
            jobs=[], run_cases=[],
        )
        assert metrics["rule_case_count"] == 5
        assert metrics["compiled_assertion_count"] == 14

    def test_legacy_asset_without_case_ids_falls_back_to_per_asset(self):
        """缺 case_identifiers 的历史资产无法判重 → 退化为逐个计数（不猜、不静默丢）。"""
        metrics = build_generation_metrics(
            batch_cases=_cases(4),
            web_tests=[_asset("WT-OLD-1", case_ids=[], case_count=4, rule=2, assertions=6,
                              with_ids=False),
                       _asset("WT-OLD-2", case_ids=[], case_count=4, rule=2, assertions=6,
                              with_ids=False)],
            jobs=[], run_cases=[],
        )
        assert metrics["rule_case_count"] == 4
        assert metrics["compiled_assertion_count"] == 12

    def test_conflicting_summaries_for_same_batch_take_max(self):
        """同一批次两个资产摘要不一致（如需求变更前后）→ 取最大值，不叠加。"""
        ids = [f"TC-{i}" for i in range(8)]
        metrics = build_generation_metrics(
            batch_cases=_cases(8),
            web_tests=[_asset("WT-A", case_ids=ids, case_count=8, rule=3, assertions=7),
                       _asset("WT-B", case_ids=ids, case_count=8, rule=5, assertions=12)],
            jobs=[], run_cases=[],
        )
        assert metrics["rule_case_count"] == 5
        assert metrics["compiled_assertion_count"] == 12


class TestOrphanJobWarning:
    def test_job_referencing_missing_asset_is_reported(self):
        """作业引用的资产不在本次清单里 → 必须显式告警（否则盘点点差异无从解释）。"""
        asset = _asset("WT-A", case_ids=["TC-0"], case_count=1, rule=1, assertions=2)
        job = SimpleNamespace(script_id="00000000-0000-0000-0000-0000000000ff",
                              script_identifier="WT-GONE", started_at="2026-10-07T00:00:00",
                              status="completed")
        metrics = build_generation_metrics(
            batch_cases=_cases(1), web_tests=[asset], jobs=[job], run_cases=[],
        )
        assert metrics["executed_asset_count"] == 1, "历史作业仍计入执行资产数（口径未变）"
        assert any("不在本次资产清单中" in w for w in metrics["warnings"]), metrics["warnings"]
        assert any("WT-GONE" in w for w in metrics["warnings"])

    def test_all_jobs_matching_assets_produce_no_orphan_warning(self):
        asset = _asset("WT-A", case_ids=["TC-0"], case_count=1, rule=1, assertions=2)
        job = SimpleNamespace(script_id="WT-A", script_identifier="WT-A",
                              started_at="2026-10-07T00:00:00", status="completed")
        metrics = build_generation_metrics(
            batch_cases=_cases(1), web_tests=[asset], jobs=[job], run_cases=[],
        )
        assert not any("不在本次资产清单中" in w for w in metrics["warnings"])
