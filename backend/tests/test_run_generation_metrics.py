# -*- coding: utf-8 -*-
"""四指标口径与未编译用例状态回归测试（复核意见第 3/4 条）。

核心断言：**四个数不能互相折算**，且不可编译用例必须显式列出、不得被当成"已自动验证"。
数据取自 2026-09-23/24 两批真实现场（PR-2 = 53/17/45/1，PR-9 = 36/0/0/1）。
"""

from __future__ import annotations

from types import SimpleNamespace
from uuid import uuid4

from app.schemas.enums import JobStatus, TestResultStatus
from app.services.run_generation_metrics import (
    METRIC_DEFINITIONS,
    build_generation_metrics,
)
from app.services.web_case_compiler import compile_cases


def _case(identifier: str, *, rule_id: str = "", assertions: list[dict] | None = None,
          field: str = "") -> SimpleNamespace:
    custom: dict = {}
    if rule_id:
        custom["requirement_rule_id"] = rule_id
    if field:
        custom["requirement_rule"] = {"field": field, "required": True}
    if assertions:
        custom["assertions"] = assertions
    return SimpleNamespace(
        identifier=identifier, name=f"用例 {identifier}", module="模块", description="",
        custom_fields=custom, test_case_steps=[],
    )


def _pr2_batch() -> list[SimpleNamespace]:
    """17 条带规则（共 45 条断言）+ 36 条叙述型 = 53 条（PR-2 现场口径）。"""
    cases: list[SimpleNamespace] = []
    for index in range(17):
        assertions = [
            {"kind": "required", "field": "企业账户充值金额", "expect": "required"},
            {"kind": "boundary", "field": "企业账户充值金额", "data": "0", "expect": "reject"},
        ]
        if index < 11:  # 17*2 + 11 = 45 条断言，与 TR-82 实测一致
            assertions.append(
                {"kind": "precision", "field": "企业账户充值金额", "data": "1.222", "expect": "reject"}
            )
        cases.append(_case(f"TC-R{index}", rule_id=f"FR-{index}",
                           field="企业账户充值金额", assertions=assertions))
    for index in range(36):
        # 真实批次形态：有 requirement_rule_id 但无规则体 → dangling_rule（不是 narrative）
        cases.append(_case(f"TC-N{index}", rule_id=f"RQ-{index:04d}"))
    return cases


def _compiled_summary(cases) -> dict:
    manifest = compile_cases(cases)
    return {
        "case_count": manifest["case_count"],
        "rule_case_count": manifest["rule_case_count"],
        "assertion_count": manifest["assertion_count"],
        "case_status_counts": manifest["case_status_counts"],
        "uncompiled_cases": manifest["uncompiled_cases"],
    }


def _asset(summary: dict, identifier: str = "WT-TEST") -> SimpleNamespace:
    return SimpleNamespace(
        id=uuid4(),
        identifier=identifier,
        generation_params={"compiled_cases": summary} if summary else {},
        test_case_id=uuid4(),
    )


def _job(script_id, *, status: JobStatus, started: bool = True) -> SimpleNamespace:
    return SimpleNamespace(
        script_id=script_id, script_type="web_test", status=status,
        started_at="2026-09-24T02:02:25Z" if started else None,
    )


class TestCompileStatus:
    def test_three_statuses_are_exhaustive(self):
        cases = [
            _case("TC-1", rule_id="FR-1", field="金额", assertions=[{"kind": "required"}]),
            _case("TC-2", rule_id="FR-2"),          # 有规则号无规则体
            _case("TC-3"),                          # 叙述型
        ]
        manifest = compile_cases(cases)
        statuses = [c["compile_status"] for c in manifest["cases"]]
        assert statuses == ["compiled", "dangling_rule", "narrative"]
        counts = manifest["case_status_counts"]
        assert sum(counts.values()) == manifest["case_count"] == 3
        assert manifest["compiled_case_count"] == 1
        assert manifest["uncompiled_case_count"] == 2
        assert {c["identifier"] for c in manifest["uncompiled_cases"]} == {"TC-2", "TC-3"}

    def test_uncompiled_cases_carry_reason(self):
        manifest = compile_cases([_case("TC-9", rule_id="FR-9"), _case("TC-8")])
        reasons = {c["identifier"]: c for c in manifest["uncompiled_cases"]}
        # 文案以 compile_cases 现口径为准：有规则号无可提取规则体 / 无可提取规则编号
        assert "无可提取的规则体" in reasons["TC-9"]["reason"]
        assert reasons["TC-9"]["status"] == "dangling_rule"
        assert "无可提取规则编号" in reasons["TC-8"]["reason"]

    def test_compiled_reason_counts_assertions(self):
        manifest = compile_cases([
            _case("TC-1", rule_id="FR-1", assertions=[{"kind": "required"}, {"kind": "enum"}]),
        ])
        assert manifest["cases"][0]["compile_reason"] == "携带 2 条断言"


class TestFourMetricsAreIndependent:
    def test_pr2_shape(self):
        batch = _pr2_batch()
        summary = _compiled_summary(batch)
        asset = _asset(summary)
        metrics = build_generation_metrics(
            batch_cases=batch,
            web_tests=[asset],
            jobs=[_job(asset.test_case_id, status=JobStatus.COMPLETED)],
            run_cases=[
                SimpleNamespace(latest_status=TestResultStatus.PASSED),
                *[SimpleNamespace(latest_status=TestResultStatus.NOT_EXECUTED) for _ in range(52)],
            ],
        )
        assert metrics["generated_case_count"] == 53
        assert metrics["rule_case_count"] == 17
        assert metrics["compiled_assertion_count"] == 45
        assert metrics["executed_asset_count"] == 1
        # 四个数互不相等、不可折算
        assert len({
            metrics["generated_case_count"], metrics["rule_case_count"],
            metrics["compiled_assertion_count"], metrics["executed_asset_count"],
        }) == 4
        assert metrics["uncompiled_case_count"] == 36
        assert metrics["verified_case_count"] == 1
        assert metrics["metrics_are_independent"] is True

    def test_warns_when_asset_pass_is_mistaken_for_case_coverage(self):
        batch = _pr2_batch()
        asset = _asset(_compiled_summary(batch))
        metrics = build_generation_metrics(
            batch_cases=batch, web_tests=[asset],
            jobs=[_job(asset.test_case_id, status=JobStatus.COMPLETED)],
        )
        joined = " ".join(metrics["warnings"])
        assert "实际执行资产数" in joined and "不代表" in joined
        assert "条用例无法自动执行" in joined

    def test_zero_assertion_batch_warns(self):
        """PR-9 现场：36 条生成用例、0 条规则、0 条断言、1 个资产通过。"""
        batch = [_case(f"TC-CS{i}") for i in range(36)]
        asset = _asset(_compiled_summary(batch))
        metrics = build_generation_metrics(
            batch_cases=batch, web_tests=[asset],
            jobs=[_job(asset.test_case_id, status=JobStatus.COMPLETED)],
        )
        assert (metrics["generated_case_count"], metrics["rule_case_count"],
                metrics["compiled_assertion_count"], metrics["executed_asset_count"]) == (36, 0, 0, 1)
        assert any("编译断言数为 0" in w for w in metrics["warnings"])

    def test_attached_but_never_started_is_not_executed(self):
        batch = [_case("TC-1")]
        asset = _asset(_compiled_summary(batch))
        metrics = build_generation_metrics(
            batch_cases=batch, web_tests=[asset],
            jobs=[_job(asset.test_case_id, status=JobStatus.PENDING, started=False)],
        )
        assert metrics["attached_asset_count"] == 1
        assert metrics["executed_asset_count"] == 0
        assert any("没有任何作业真正启动" in w for w in metrics["warnings"])

    def test_skipped_job_is_not_executed(self):
        batch = [_case("TC-1")]
        asset = _asset(_compiled_summary(batch))
        metrics = build_generation_metrics(
            batch_cases=batch, web_tests=[asset],
            jobs=[_job(asset.test_case_id, status=JobStatus.SKIPPED, started=False)],
        )
        assert metrics["executed_asset_count"] == 0

    def test_definitions_cover_every_metric(self):
        batch = [_case("TC-1")]
        asset = _asset(_compiled_summary(batch))
        metrics = build_generation_metrics(batch_cases=batch, web_tests=[asset], jobs=[])
        for key in ("generated_case_count", "rule_case_count",
                    "compiled_assertion_count", "executed_asset_count"):
            assert key in metrics
            assert key in METRIC_DEFINITIONS
            assert key in metrics["metric_definitions"]
        assert metrics["scope_note"]

    def test_uncompiled_cases_deduplicated_across_assets(self):
        batch = [_case("TC-N1"), _case("TC-N2")]
        summary = _compiled_summary(batch)
        metrics = build_generation_metrics(
            batch_cases=batch,
            web_tests=[_asset(summary, "WT-A"), _asset(summary, "WT-B")],
            jobs=[],
        )
        assert metrics["uncompiled_case_count"] == 2
        assert [c["identifier"] for c in metrics["uncompiled_cases"]] == ["TC-N1", "TC-N2"]

    def test_no_assets_falls_back_to_batch_count(self):
        batch = [_case("TC-1"), _case("TC-2")]
        metrics = build_generation_metrics(batch_cases=batch, web_tests=[], jobs=[])
        assert metrics["generated_case_count"] == 2
        assert metrics["executed_asset_count"] == 0
        assert metrics["compiled_assertion_count"] == 0

    def test_legacy_asset_without_status_is_recomputed_not_reported_zero(self):
        """历史资产没有分类字段时，`缺失`不得静默报成"0 条未编译"。"""
        batch = _pr2_batch()
        legacy_summary = {  # 本版之前的摘要：只有计数，没有 case_status_counts
            "case_count": 53,
            "rule_case_count": 17,
            "assertion_count": 45,
        }
        asset = _asset(legacy_summary)
        metrics = build_generation_metrics(
            batch_cases=batch, web_tests=[asset],
            jobs=[_job(asset.id, status=JobStatus.COMPLETED)],
        )
        assert metrics["case_status_source"] == "recomputed_from_batch"
        assert metrics["uncompiled_case_count"] == 36
        assert metrics["case_status_counts"] == {
            "compiled": 17, "dangling_rule": 36, "narrative": 0,
        }
        # 生成时落库的计数仍然是主口径，不被重算覆盖
        assert metrics["rule_case_count"] == 17
        assert metrics["compiled_assertion_count"] == 45

    def test_new_asset_uses_stored_status(self):
        batch = _pr2_batch()
        asset = _asset(_compiled_summary(batch))
        metrics = build_generation_metrics(
            batch_cases=batch, web_tests=[asset],
            jobs=[_job(asset.id, status=JobStatus.COMPLETED)],
        )
        assert metrics["case_status_source"] == "asset_summary"
        assert metrics["uncompiled_case_count"] == 36

    def test_truncated_sample_does_not_shrink_the_count(self):
        """回归：落库摘要把 `uncompiled_cases` 截断到 20 条，计数**不得**跟着变成 20。

        实测（TR-85）：36 条不可编译用例被报成 20 条——因为聚合取了样本列表长度。
        计数必须以完整的 `case_status_counts` 为准。
        """
        batch = _pr2_batch()
        summary = _compiled_summary(batch)
        assert summary["case_status_counts"]["dangling_rule"] == 36
        summary["uncompiled_cases"] = summary["uncompiled_cases"][:20]  # 模拟落库摘要截断
        asset = _asset(summary)
        metrics = build_generation_metrics(
            batch_cases=batch, web_tests=[asset],
            jobs=[_job(asset.id, status=JobStatus.COMPLETED)],
        )
        assert metrics["uncompiled_case_count"] == 36
        assert len(metrics["uncompiled_cases"]) == 20  # 样本仍是 20 条
        assert metrics["uncompiled_cases_truncated"] is True
        assert any("36 条用例无法自动执行" in w for w in metrics["warnings"])
