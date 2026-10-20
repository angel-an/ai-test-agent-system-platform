# -*- coding: utf-8 -*-
"""R5 指标模型回归（2026-10-17）。

要证明的：
1. **两类分别算通过率**，合并即错（配置检查再多也不改变用例通过率）；
2. 一个检查带 N 个 owner 时，run 级 ``total`` **只算一次**（否则分母虚高）；
3. 同名的两条**不同**检查（观测值不同）不得被合并；
4. 跨脚本的同名检查不得被合并；
5. 配置驱动但旧脚本没标 ``source`` 时按检查名推断，且必须标 ``source_inferred``；
6. 既不归属、又非配置驱动的检查 → 只告警，不进任何通过率。
"""

from __future__ import annotations

from app.services.assertion_ledger import build_metric_model


def _a(name: str, outcome: str, *, case_id: str = "", source: str = "",
       observed: str = "o", script: str = "WT-1") -> dict:
    return {
        "script_identifier": script,
        "name": name,
        "outcome": outcome,
        "observed": observed,
        "rule_id": "",
        "case_id": case_id,
        "source": source,
    }


def _five(bucket: dict) -> dict:
    return {k: bucket[k] for k in ("total", "pass", "fail", "unverified", "blocked")}


class TestTwoCategories:
    def test_pass_rates_are_not_merged(self):
        """1 条用例检查通过 + 5 条配置检查通过 → 用例通过率仍是 1/1，不是 6/6。"""
        metrics = build_metric_model([
            _a("required_error:名称", "pass", case_id="TC-1"),
            *[_a(f"default_selected:f{i}", "pass", source="locator_config") for i in range(5)],
        ])
        assert _five(metrics["case_attributed"]) == {
            "total": 1, "pass": 1, "fail": 0, "unverified": 0, "blocked": 0}
        assert metrics["case_attributed"]["pass_rate"] == 1.0
        assert _five(metrics["config_only"]) == {
            "total": 5, "pass": 5, "fail": 0, "unverified": 0, "blocked": 0}
        assert metrics["config_only"]["pass_rate"] == 1.0
        assert metrics["unattributed_checks"] == 0

    def test_unverified_owner_check_lowers_only_case_rate(self):
        metrics = build_metric_model([
            _a("required_error:名称", "unverified", case_id="TC-1", observed="未出现必填提示"),
            _a("visibility:排隊=on", "pass", source="locator_config"),
        ])
        assert metrics["case_attributed"]["pass_rate"] == 0.0
        assert metrics["config_only"]["pass_rate"] == 1.0


class TestDeduplication:
    def test_multi_owner_check_counts_once(self):
        """同一检查对 3 个 owner 各输出一行：run 级 total 仍是 1，owner_bindings=3。"""
        rows = [_a("default_selected:状态=启用", "pass", case_id=f"TC-{i}") for i in (1, 2, 3)]
        metrics = build_metric_model(rows)
        assert metrics["case_attributed"]["total"] == 1
        assert metrics["case_attributed"]["pass"] == 1
        assert metrics["owner_bindings"] == 3

    def test_same_name_with_different_observed_are_two_checks(self):
        """TR-697 真实形态：同名检查分"填值清空"与"提交层"两次观测，必须各算一条。"""
        metrics = build_metric_model([
            _a("required_error:店鋪名稱(EN)", "unverified", case_id="TC-122613",
               observed="填值后清空仍未出现必填提示"),
            _a("required_error:店鋪名稱(EN)", "unverified", case_id="TC-122613",
               observed="提交层：页面无拒绝证据"),
        ])
        assert metrics["case_attributed"]["total"] == 2

    def test_same_check_name_across_scripts_are_not_merged(self):
        metrics = build_metric_model([
            _a("default_selected:狀態=開啟", "pass", source="locator_config", script="WT-A"),
            _a("default_selected:狀態=開啟", "pass", source="locator_config", script="WT-B"),
        ])
        assert metrics["config_only"]["total"] == 2


class TestConfigInference:
    def test_explicit_source_is_not_inferred(self):
        metrics = build_metric_model([
            _a("default_selected:狀態=開啟", "pass", source="locator_config"),
        ])
        assert metrics["config_only"]["total"] == 1
        assert metrics["source_inferred"] == 0

    def test_legacy_visibility_without_source_is_inferred(self):
        """TR-697 的两条 visibility 检查在旧脚本里没有 source 标记。"""
        metrics = build_metric_model([
            _a("visibility:排隊=on", "pass"),
            _a("visibility:外賣=on", "pass"),
        ])
        assert metrics["config_only"]["total"] == 2
        assert metrics["source_inferred"] == 2
        assert metrics["unattributed_checks"] == 0


class TestUnattributed:
    def test_unknown_check_is_warned_not_counted(self):
        metrics = build_metric_model([_a("随手写的检查", "pass")])
        assert metrics["unattributed_checks"] == 1
        assert metrics["unattributed"]["pass_rate"] is None
        # 不得混进任何一类
        assert metrics["case_attributed"]["total"] == 0
        assert metrics["config_only"]["total"] == 0


class TestRealShapeTR697:
    """用 TR-697 的真实 8 条断言形态复算（stdout 实录）。"""

    def test_tr697_recount(self):
        rows = [
            _a(f"default_selected:{n}", "pass", source="locator_config",
               observed=f"开关默认状态={n}") for n in ("營業狀態=開啟", "排隊=關閉", "訂位=關閉", "外賣=關閉")
        ] + [
            _a("required_error:店鋪名稱(EN)", "unverified", case_id="TC-122613",
               observed="填值后清空仍未出现必填提示（可能仅提交时校验）"),
            _a("required_error:店鋪名稱(EN)", "unverified", case_id="TC-122613",
               observed="提交层：页面无拒绝证据，捕获到 1 条业务写请求但均无拒绝语义"),
            _a("visibility:排隊=on", "pass", observed="切换后字段出现：Queue 等"),
            _a("visibility:外賣=on", "pass", observed="切换后字段出现：Takeout Redirect Link"),
        ]
        metrics = build_metric_model(rows)
        assert _five(metrics["case_attributed"]) == {
            "total": 2, "pass": 0, "fail": 0, "unverified": 2, "blocked": 0}
        assert _five(metrics["config_only"]) == {
            "total": 6, "pass": 6, "fail": 0, "unverified": 0, "blocked": 0}
        assert metrics["unattributed_checks"] == 0
        assert metrics["source_inferred"] == 2
