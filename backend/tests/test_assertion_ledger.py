# -*- coding: utf-8 -*-
"""执行账目（assertion ledger）回归（2026-09-29 复核 P3）。

要求：
1. 逐条断言可结构化查询（名称/结果/观察值/规则号/用例号）——不必再 grep stdout；
2. **未归属检查必须显式告警**（TR-149 实测 plan_checks=15 / attributed_checks=14）；
3. 按用例汇总与总体分布一致（不重不漏）；
4. 环境阻断/阻塞要有独立告警，避免被当作普通失败。
"""

from __future__ import annotations

import json
from types import SimpleNamespace

from app.services.assertion_ledger import build_assertion_ledger, parse_job_ledger


def _job(identifier: str, stdout: str, status: str = "completed"):
    return SimpleNamespace(script_identifier=identifier, status=status, stdout=stdout)


def _stdout(*, plan_checks: int, attributed: int, assertions: list[dict], env_blocked: bool = False) -> str:
    lines = [json.dumps(item, ensure_ascii=False) for item in assertions]
    body = "\n".join(f"COMPILED_ASSERTION {line}" for line in lines)
    summary = json.dumps({
        "total": len(assertions), "pass": sum(1 for a in assertions if a["outcome"] == "pass"),
        "unverified": sum(1 for a in assertions if a["outcome"] == "unverified"),
        "blocked": sum(1 for a in assertions if a["outcome"] == "blocked"),
        "plan_checks": plan_checks, "attributed_checks": attributed,
    })
    # coverage.assertions 是脚本"编译期声明的断言数"，应与本作业的用例断言数一致；
    # 写成固定值会让"声明>归集"的缺口告警在正常用例上误触发。
    coverage = json.dumps({"cases": 35, "rule_cases": 14, "assertions": len(assertions)})
    tail = "\nCOMPILED_ASSERTION_SUMMARY " + summary + "\nCOMPILED_COVERAGE " + coverage
    if env_blocked:
        tail += "\nENVIRONMENT_BLOCKED request blocked by WAF"
    return body + tail


class TestParseJobLedger:
    def test_rows_are_structured(self):
        ledger = parse_job_ledger(_stdout(plan_checks=2, attributed=2, assertions=[
            {"name": "默认选中=启用", "outcome": "pass", "observed": "默认选中=启用",
             "rule_id": "", "case_id": ""},
            {"name": "input_validation:名称 [21字]", "outcome": "pass",
             "observed": "样本 21 字符 被截断为 20 字符", "rule_id": "R19", "case_id": "TC-1"},
        ]))
        assert ledger["assertion_count"] == 2
        assert ledger["by_outcome"] == {"pass": 2}
        assert ledger["summary"]["plan_checks"] == 2
        assert ledger["coverage"]["cases"] == 35

    def test_broken_line_does_not_break_the_rest(self):
        stdout = 'COMPILED_ASSERTION {bad json}\n' + _stdout(
            plan_checks=1, attributed=1,
            assertions=[{"name": "n", "outcome": "pass", "observed": "o",
                         "rule_id": "R1", "case_id": "TC-1"}])
        assert parse_job_ledger(stdout)["assertion_count"] == 1


class TestBuildLedger:
    def test_unattributed_check_is_warned(self):
        """TR-149 真实形态：15 条计划检查、只归属 14 条。"""
        jobs = [_job("WT-A", _stdout(
            plan_checks=15, attributed=14,
            assertions=[{"name": "默认选中=启用", "outcome": "pass", "observed": "o",
                         "rule_id": "", "case_id": ""}],
        ))]
        ledger = build_assertion_ledger(jobs)
        assert ledger["totals"]["assertions"] == 1
        # 2026-10-17（R5）：totals.unattributed 改为**检查口径**、与 metrics 一致；
        # 断言行口径保留在 unattributed_rows。
        assert ledger["totals"]["unattributed"] == 1
        assert ledger["totals"]["unattributed_rows"] == 1
        assert ledger["metrics"]["unattributed_checks"] == 1
        assert any("未归属任何用例" in w for w in ledger["warnings"])

    def test_attributed_checks_do_not_warn(self):
        jobs = [_job("WT-B", _stdout(
            plan_checks=2, attributed=2,
            assertions=[{"name": "n", "outcome": "pass", "observed": "o",
                         "rule_id": "R1", "case_id": "TC-1"}],
        ))]
        ledger = build_assertion_ledger(jobs)
        assert ledger["warnings"] == []
        assert ledger["totals"]["unattributed"] == 0

    def test_config_only_check_is_not_a_warning(self):
        """④ 优化：来自定位器配置、没有对应用例断言的计划级检查 → 归类为 notes，不报缺陷。"""
        stdout = _stdout(plan_checks=2, attributed=1, assertions=[
            {"name": "input_validation:名称 [21字]", "outcome": "pass", "observed": "o",
             "rule_id": "R19", "case_id": "TC-1"}])
        # 手工注入 config_only_checks（由执行器汇总输出）
        stdout = stdout.replace(
            '"plan_checks": 2, "attributed_checks": 1',
            '"plan_checks": 2, "attributed_checks": 1, "config_only_checks": 1',
        )
        ledger = build_assertion_ledger([_job("WT-CFG", stdout)])
        assert ledger["warnings"] == []
        assert ledger["notes"] and "配置驱动" in ledger["notes"][0]

    def test_missing_attribution_still_warns_when_not_config_driven(self):
        """计划 3 条、只归属 1 条，且另 2 条**确实执行了但没有 owner** → 必须告警。

        2026-10-17（R5）修正 fixture：旧版本只写 1 条断言行却声明 plan_checks=3，
        这种"summary 与台账不一致"的形态在真实输出里不存在（无 owner 的检查执行时
        同样会输出断言行），用它做判据会把"编译器没给配置类检查标 source"误判成
        未归属（TR-697 实测的假告警）。
        """
        jobs = [_job("WT-CFG2", _stdout(plan_checks=3, attributed=1, assertions=[
            {"name": "n", "outcome": "pass", "observed": "o", "rule_id": "R1", "case_id": "TC-1"},
            {"name": "n2", "outcome": "pass", "observed": "o2", "rule_id": "", "case_id": ""},
            {"name": "n3", "outcome": "pass", "observed": "o3", "rule_id": "", "case_id": ""},
        ]))]
        ledger = build_assertion_ledger(jobs)
        assert any("未归属任何用例" in w for w in ledger["warnings"])
        assert ledger["metrics"]["unattributed_checks"] == 2
        assert ledger["metrics"]["config_only"]["total"] == 0
        assert not ledger["notes"]

    def test_config_driven_assertion_is_not_counted_as_unattributed(self):
        """断言记录带 source=locator_config 时，不再计入"未归属断言"告警。"""
        stdout = "\n".join([
            'COMPILED_ASSERTION {"name":"default_selected:状态=启用","outcome":"pass",'
            '"observed":"默认选中=启用","rule_id":"","case_id":"","source":"locator_config"}',
            'COMPILED_ASSERTION_SUMMARY {"total":1,"pass":1,"plan_checks":1,'
            '"attributed_checks":0,"config_only_checks":1}',
        ])
        ledger = build_assertion_ledger([_job("WT-CFG3", stdout)])
        assert not any("未归属用例" in w for w in ledger["warnings"]), ledger["warnings"]
        assert any("配置驱动" in n for n in ledger["notes"]), ledger["notes"]
        # R5 口径：配置驱动检查不计入 unattributed（旧口径曾把它算成"未归属断言"）；
        # 行级计数仍保留在 unattributed_rows 里，便于与历史报表对账。
        assert ledger["metrics"]["config_only"]["total"] == 1
        assert ledger["totals"]["unattributed"] == 0
        assert ledger["totals"]["unattributed_rows"] == 1

    def test_per_case_rollup_matches_totals(self):
        jobs = [_job("WT-C", _stdout(
            plan_checks=4, attributed=4,
            assertions=[
                {"name": "a", "outcome": "pass", "observed": "o", "rule_id": "R1", "case_id": "TC-1"},
                {"name": "b", "outcome": "unverified", "observed": "o", "rule_id": "R1", "case_id": "TC-1"},
                {"name": "c", "outcome": "blocked", "observed": "o", "rule_id": "R2", "case_id": "TC-2"},
            ],
        ))]
        ledger = build_assertion_ledger(jobs)
        per_case = {row["case_id"]: row for row in ledger["per_case"]}
        assert per_case["TC-1"] == {"case_id": "TC-1", "rule_id": "R1",
                                     "total": 2, "pass": 1, "unverified": 1, "blocked": 0}
        assert per_case["TC-2"]["blocked"] == 1
        assert sum(row["total"] for row in ledger["per_case"]) == ledger["totals"]["assertions"]

    def test_environment_block_and_blocked_are_flagged(self):
        jobs = [_job("WT-D", _stdout(
            plan_checks=1, attributed=1,
            assertions=[{"name": "a", "outcome": "blocked", "observed": "o",
                         "rule_id": "R1", "case_id": "TC-1"}],
            env_blocked=True,
        ))]
        ledger = build_assertion_ledger(jobs)
        assert any("环境阻断" in w for w in ledger["warnings"])
        assert any("被阻塞" in w for w in ledger["warnings"])

    def test_multiple_jobs_are_aggregated(self):
        jobs = [
            _job("WT-E1", _stdout(plan_checks=1, attributed=1, assertions=[
                {"name": "a", "outcome": "pass", "observed": "o", "rule_id": "R1", "case_id": "TC-1"}])),
            _job("WT-E2", _stdout(plan_checks=1, attributed=1, assertions=[
                {"name": "b", "outcome": "unverified", "observed": "o", "rule_id": "R2", "case_id": "TC-2"}])),
        ]
        ledger = build_assertion_ledger(jobs)
        assert ledger["totals"] == {
            "jobs": 2, "assertions": 2, "unattributed": 0, "unattributed_rows": 0,
            "observations": 0, "plan_skips": 0,
            "by_outcome": {"pass": 1, "unverified": 1},
        }
        assert [row["script_identifier"] for row in ledger["jobs"]] == ["WT-E1", "WT-E2"]


def _upload_evidence(**over) -> str:
    """构造一行上传证据（浏览器只输出原始事实，判定在服务端完成）。"""
    payload = {
        "field": "Attachments",
        "evidence_kind": "size",
        "expect": "reject",
        "sample": {"name": "upload_10485761.xlsx", "bytes": 10485761,
                   "kind": "boundary_plus", "threshold_confirmed": True},
        "messages_before": [],
        "messages_after": ["文件大小不能超过 10M"],
        "rule_id": "FR-1",
        "case_id": "TC-1",
        "chips_before": 0,
        "chips_after": 0,
        "timed_out": False,
    }
    payload.update(over)
    return "UPLOAD_EVIDENCE " + json.dumps(payload, ensure_ascii=False)


class TestUploadEvidenceIsLedgered:
    """2026-10-12 修复：上传类断言必须进账目，不能凭空消失。

    现状根因：浏览器脚本只对上传检查输出 ``UPLOAD_EVIDENCE``（原始事实），判定在服务端
    ``web_upload_evidence``；``web_case_result_mapper`` 接了这一步，``assertion_ledger`` 漏接，
    于是这类断言「定义在脚本里、账目 0 条、summary 也不记」——TR-267 实测 6 条资产如此，
    报告把「压根没验证」显示成「已完成」。
    """

    def test_upload_assertion_is_included(self):
        ledger = parse_job_ledger(_upload_evidence())
        assert ledger["assertion_count"] == 1
        item = ledger["assertions"][0]
        assert item["outcome"] == "pass"
        assert item["case_id"] == "TC-1"
        assert item["source"] == "upload_evidence"

    def test_upload_assertion_not_double_counted_with_compiled(self):
        stdout = _upload_evidence() + "\n" + _stdout(plan_checks=1, attributed=1, assertions=[
            {"name": "n", "outcome": "pass", "observed": "o", "rule_id": "R1", "case_id": "TC-1"}])
        ledger = parse_job_ledger(stdout)
        assert ledger["assertion_count"] == 2
        assert ledger["by_outcome"] == {"pass": 2}

    def test_upload_assertion_rolls_up_per_case(self):
        ledger = build_assertion_ledger([_job("WT-U1", _upload_evidence())])
        assert ledger["per_case"] == [{"case_id": "TC-1", "rule_id": "FR-1",
                                       "total": 1, "pass": 1, "unverified": 0, "blocked": 0}]
        assert sum(row["total"] for row in ledger["per_case"]) == ledger["totals"]["assertions"]

    def test_boundary_rejected_deviation_raises_warning(self):
        # 「≤10M 应接受」的边界内样本被客户端按大小拒绝 ⇒ 契约偏差，必须单独浮出
        stdout = _upload_evidence(
            expect="accept",
            sample={"name": "upload_10485759.xlsx", "bytes": 10485759,
                    "kind": "boundary_minus", "threshold_confirmed": True},
        )
        ledger = build_assertion_ledger([_job("WT-U2", stdout)])
        assert ledger["totals"]["assertions"] == 1
        assert any("契约偏差" in w and "size_boundary_rejected" in w for w in ledger["warnings"]), \
            ledger["warnings"]

    def test_within_limit_accept_is_not_a_deviation(self):
        stdout = _upload_evidence(
            expect="accept", messages_after=[],
            sample={"name": "upload_10485759.xlsx", "bytes": 10485759,
                    "kind": "boundary_minus", "threshold_confirmed": True},
        )
        ledger = build_assertion_ledger([_job("WT-U3", stdout)])
        assert not any("契约偏差" in w for w in ledger["warnings"]), ledger["warnings"]

    def test_declared_but_no_evidence_is_warned(self):
        """另一半：断言声明了、却一条证据都没采到（字段无定位器）→ 必须提示"未验证"。"""
        stdout = 'COMPILED_COVERAGE {"cases":1,"rule_cases":1,"assertions":2,"pending_locator":2}'
        ledger = build_assertion_ledger([_job("WT-M1", stdout)])
        assert ledger["totals"]["assertions"] == 0
        assert any("脚本声明 2 条断言" in w and "已完成验证" in w for w in ledger["warnings"]), \
            ledger["warnings"]

    def test_declared_with_full_evidence_does_not_warn(self):
        stdout = ('COMPILED_ASSERTION {"name":"a","outcome":"pass","observed":"o",'
                  '"rule_id":"R1","case_id":"TC-1"}\n'
                  'COMPILED_COVERAGE {"cases":1,"rule_cases":1,"assertions":1}')
        ledger = build_assertion_ledger([_job("WT-M2", stdout)])
        assert not any("脚本声明" in w for w in ledger["warnings"]), ledger["warnings"]
