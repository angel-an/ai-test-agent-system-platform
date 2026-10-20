# -*- coding: utf-8 -*-
"""用例级执行结果映射的回归测试。

用线上真实形态的数据：
- 两个批次各有 36 条"模板用例"（无断言）→ 必须显式为 NOT_EXECUTED，不能留空/冒充通过；
- 断言结果里既有 pass 也有 unverified → 不能算 PASSED（实测 16 pass / 5 unverified）。
"""

from __future__ import annotations

import json
from types import SimpleNamespace

from app.services.web_case_result_mapper import (
    BLOCKED,
    FAILED,
    NOT_EXECUTED,
    PASSED,
    SKIPPED,
    aggregate_status,
    build_identifier_to_case,
    build_rule_to_case,
    map_case_results,
    parse_compiled_assertions,
    summarize_report,
)

STDOUT = "\n".join([
    "Running 1 test using 1 worker",
    'COMPILED_ASSERTION ' + json.dumps(
        {"name": "input_validation:企业账户充值金额 [0]", "outcome": "pass",
         "observed": "输入被截断为 0.01", "rule_id": "FR-aaa"}, ensure_ascii=False),
    'COMPILED_ASSERTION ' + json.dumps(
        {"name": "input_validation:企业账户充值金额 [99999]", "outcome": "pass",
         "observed": "合法值通过", "rule_id": "FR-aaa"}, ensure_ascii=False),
    'COMPILED_ASSERTION ' + json.dumps(
        {"name": "required_error:会员手机号", "outcome": "unverified",
         "observed": "填值后清空仍未出现必填提示", "rule_id": "FR-bbb"}, ensure_ascii=False),
    'COMPILED_ASSERTION ' + json.dumps(
        {"name": "input_validation:企业账户退款金额 [0]", "outcome": "blocked",
         "observed": "未定位到输入框", "rule_id": "FR-ccc"}, ensure_ascii=False),
    "COMPILED_ASSERTION_SUMMARY {\"total\":4}",
    "  1 passed (32.1s)",
])


class TestParsing:
    def test_assertions_are_parsed_with_rule_id(self):
        items = parse_compiled_assertions(STDOUT)
        assert len(items) == 4
        assert items[0]["rule_id"] == "FR-aaa"
        assert items[0]["outcome"] == "pass"

    def test_summary_line_is_ignored(self):
        assert all("total" not in i["name"] for i in parse_compiled_assertions(STDOUT))

    def test_old_script_without_rule_id_is_tolerated(self):
        legacy = 'COMPILED_ASSERTION ' + json.dumps(
            {"name": "x", "outcome": "pass", "observed": "y"}, ensure_ascii=False)
        items = parse_compiled_assertions(legacy)
        assert items and items[0]["rule_id"] == ""

    def test_garbage_lines_do_not_raise(self):
        assert parse_compiled_assertions("COMPILED_ASSERTION {oops\nnot json") == []


class TestAggregation:
    def test_all_pass_is_passed(self):
        assert aggregate_status(["pass", "pass"]) == PASSED

    def test_partial_pass_is_not_failed(self):
        """16 pass / 5 unverified 的真实形态：既不能算通过，也不能指控产品失败。"""
        assert aggregate_status(["pass", "unverified"]) == SKIPPED
        assert aggregate_status(["unverified"]) == SKIPPED

    def test_blocked_is_blocked(self):
        assert aggregate_status(["pass", "blocked"]) == BLOCKED

    def test_no_assertions_is_not_executed(self):
        assert aggregate_status([]) == NOT_EXECUTED


class TestCaseMapping:
    RULE_TO_CASE = {"FR-aaa": "case-1", "FR-bbb": "case-2", "FR-ccc": "case-3",
                    "RQ-dangling": "case-4", "FR-unrun": "case-5"}

    def test_per_case_status(self):
        got = map_case_results(
            STDOUT, self.RULE_TO_CASE,
            case_ids=["case-1", "case-2", "case-3", "case-4", "case-5"],
        )
        assert got["case-1"]["status"] == PASSED
        assert got["case-1"]["assertions"] == 2
        assert got["case-2"]["status"] == SKIPPED
        assert got["case-3"]["status"] == BLOCKED

    def test_cases_without_assertions_are_marked_not_executed(self):
        """模板类用例（36 条）的真实状态必须显式可见，不能留空。"""
        got = map_case_results(STDOUT, self.RULE_TO_CASE,
                               case_ids=["case-4", "case-5"])
        assert got["case-4"]["status"] == NOT_EXECUTED
        assert got["case-5"]["status"] == NOT_EXECUTED
        assert got["case-4"]["assertions"] == 0

    def test_details_keep_readable_summary(self):
        got = map_case_results(STDOUT, self.RULE_TO_CASE, case_ids=["case-1"])
        assert "输入被截断为 0.01" in got["case-1"]["summary"]

    def test_build_rule_to_case_from_cases(self):
        cases = [
            SimpleNamespace(id="id-1", custom_fields={"requirement_rule_id": "FR-x"}),
            SimpleNamespace(id="id-2", custom_fields={}),          # 无规则号 → 跳过
        ]
        assert build_rule_to_case(cases) == {"FR-x": "id-1"}


class TestEveryCaseIsExplained:
    """状态列不能只有一个标签：每条用例都要有可读原因（避免新的"灰色通过"）。"""

    def test_case_without_assertions_gets_an_explanation(self):
        got = map_case_results(STDOUT, {}, case_ids=["case-empty"])
        assert got["case-empty"]["status"] == NOT_EXECUTED
        assert got["case-empty"]["summary"]                     # 必须非空
        assert "没有可执行断言" in got["case-empty"]["summary"]

    def test_unverified_count_is_reported(self):
        got = map_case_results(STDOUT, {"FR-aaa": "c1", "FR-bbb": "c2"},
                               case_ids=["c1", "c2"])
        assert got["c1"]["status"] == PASSED
        assert got["c1"]["pass_count"] == 2
        assert got["c1"]["unverified_count"] == 0
        assert got["c2"]["status"] == SKIPPED
        assert got["c2"]["unverified_count"] == 1

    def test_all_cases_have_non_empty_summary(self):
        got = map_case_results(STDOUT, TestCaseMapping.RULE_TO_CASE,
                               case_ids=["case-1", "case-3", "case-4", "case-5"])
        assert all(v["summary"] for v in got.values()), got


class TestCoarseSyncYieldsToFineGrained:
    """统一写入路径：已有明细的用例，粗粒度作业同步不得覆盖。"""

    def test_skip_when_detail_exists(self):
        from types import SimpleNamespace as NS

        from app.services.execution.engine import ScriptExecutionEngine

        with_detail = NS(latest_result_id="result-1", latest_status="NOT_EXECUTED")
        without_detail = NS(latest_result_id=None, latest_status="NOT_EXECUTED")

        assert ScriptExecutionEngine._should_skip_coarse_case_sync(with_detail) is True
        # 无明细时仍允许粗粒度写入（如没有用例级断言的脚本）
        assert ScriptExecutionEngine._should_skip_coarse_case_sync(without_detail) is False
        # 缺字段的对象不应抛异常
        assert ScriptExecutionEngine._should_skip_coarse_case_sync(NS()) is False


SHARED_STDOUT = "\n".join([
    'COMPILED_ASSERTION ' + json.dumps(
        {"name": "input_validation:充值金额 [0]", "outcome": "pass",
         "observed": "截断为 0.01", "rule_id": "FR-shared", "case_id": "TC-101"},
        ensure_ascii=False),
    'COMPILED_ASSERTION ' + json.dumps(
        {"name": "input_validation:充值金额 [99999]", "outcome": "unverified",
         "observed": "未观测到客户端行为", "rule_id": "FR-shared", "case_id": "TC-102"},
        ensure_ascii=False),
])


class TestPreciseAttributionByCaseId:
    """归因精确化：断言行自报 case_id 时按用例精确归因，不再靠规则号猜测。

    实测缺陷：``FR-b6d4daa587`` 等 5 个规则号被 2–3 条用例共享（覆盖 14 条用例），
    按规则号 1:1 归因时只有"第一条"拿到结论，兄弟用例被误标"未执行"。
    """

    def test_parse_keeps_case_id(self):
        items = parse_compiled_assertions(SHARED_STDOUT)
        assert [i["case_id"] for i in items] == ["TC-101", "TC-102"]

    def test_case_id_wins_over_rule_id(self):
        got = map_case_results(
            SHARED_STDOUT,
            {"FR-shared": ["id-a", "id-b"]},
            case_ids=["id-a", "id-b"],
            identifier_to_case={"TC-101": "id-a", "TC-102": "id-b"},
        )
        assert got["id-a"]["status"] == PASSED
        assert got["id-b"]["status"] == SKIPPED
        # 精确归因：每条用例只拿自己那一份断言，不再各拿一份全量结论
        assert got["id-a"]["assertions"] == 1
        assert got["id-b"]["assertions"] == 1
        assert got["id-b"]["unverified_count"] == 1

    def test_unknown_case_id_falls_back_to_rule_id_mapping(self):
        """脚本里的标识未登记时（旧脚本/标识漂移）仍要能归因，不能丢结论。"""
        got = map_case_results(SHARED_STDOUT, {"FR-shared": "id-a"},
                               case_ids=["id-a"], identifier_to_case={})
        assert got["id-a"]["assertions"] == 2
        assert got["id-a"]["status"] == SKIPPED   # pass + unverified
        assert got["id-a"]["pass_count"] == 1

    def test_shared_rule_id_marks_all_siblings(self):
        stdout = "COMPILED_ASSERTION " + json.dumps(
            {"name": "input_validation:上传充值凭证", "outcome": "pass",
             "observed": "ok", "rule_id": "FR-dup"}, ensure_ascii=False)
        siblings = ["s1", "s2", "s3"]
        got = map_case_results(stdout, {"FR-dup": siblings}, case_ids=siblings)
        assert [got[s]["status"] for s in siblings] == [PASSED, PASSED, PASSED]
        assert all(got[s]["assertions"] == 1 for s in siblings)

    def test_unrelated_case_still_not_executed(self):
        """精确归因不能让"没有断言的用例"沾光变成已执行。"""
        got = map_case_results(SHARED_STDOUT, {"FR-shared": "id-a"},
                               case_ids=["id-a", "id-bystander"],
                               identifier_to_case={"TC-101": "id-a", "TC-102": "id-a"})
        assert got["id-bystander"]["status"] == NOT_EXECUTED
        assert got["id-bystander"]["assertions"] == 0


class TestBuildMappings:
    def test_rule_to_case_single_value_stays_scalar(self):
        cases = [SimpleNamespace(id="id-1", custom_fields={"requirement_rule_id": "FR-x"})]
        assert build_rule_to_case(cases) == {"FR-x": "id-1"}

    def test_rule_to_case_lists_shared_rule(self):
        cases = [
            SimpleNamespace(id="id-1", custom_fields={"requirement_rule_id": "FR-x"}),
            SimpleNamespace(id="id-2", custom_fields={"requirement_rule_id": "FR-x"}),
        ]
        assert build_rule_to_case(cases) == {"FR-x": ["id-1", "id-2"]}

    def test_identifier_to_case_skips_missing_identifier(self):
        cases = [SimpleNamespace(id="id-1", identifier="TC-101"),
                 SimpleNamespace(id="id-2", identifier=None),
                 SimpleNamespace(id=None, identifier="TC-103")]
        assert build_identifier_to_case(cases) == {"TC-101": "id-1"}


class TestFixedReportFields:
    """复核意见 P1-9：报告必须固定展示这几项，且语义不得互相替代。"""

    def test_partition_and_unverified_semantics(self):
        results = map_case_results(
            STDOUT, TestCaseMapping.RULE_TO_CASE,
            case_ids=["case-1", "case-2", "case-3", "case-4", "case-5"],
        )
        report = summarize_report(results, plan_skipped=[{"field": "上传文件（企业充值）"}])
        assert report["rules_total"] == 5
        assert report["passed"] == 1          # case-1 全通过
        assert report["unverified"] == 1      # case-2 含未验证断言
        assert report["skipped_cases"] == 1   # 未验证 → SKIPPED（当前语义下的同一集合）
        assert report["blocked"] == 1         # case-3
        assert report["not_executed"] == 2    # case-4/case-5 无断言
        assert report["failed"] == 0          # 未验证绝不算失败
        assert report["skipped_assertions"] == 1   # 计划层跳过属诊断信息
        assert report["partition_ok"] == 1    # 恒等式成立

    def test_unverified_is_not_counted_as_passed(self):
        """客户端模式下"接受类"永远只能是未验证：不得进入通过数。"""
        ev = ("UPLOAD_EVIDENCE " + json.dumps({
            "case_id": "TC-u", "rule_id": "FR-u", "field": "上传文件（企业充值）",
            "evidence_kind": "format", "expect": "accept",
            "sample": {"name": "upload_valid.xlsx", "bytes": 1634, "kind": "valid_workbook"},
            "messages_before": [], "messages_after": ["upload_valid.xlsx 上传失败"],
            "chips_before": 0, "chips_after": 1, "isolation_blocks": 1,
            "isolation_blocked_kinds": {"upload_blocked": 1},
        }, ensure_ascii=False))
        results = map_case_results(ev, {}, case_ids=[],
                                   identifier_to_case={"TC-u": "id-u"})
        report = summarize_report(results)
        assert report["passed"] == 0 and report["unverified"] == 1


class TestStdoutHasExecutableAssertions:
    """P1-① 零断言守卫（2026-10-17）：粗粒度同步判据与用例级落库同源。"""

    def test_zero_assertion_smoke_is_false(self):
        # TR-485 实测形态：只有 COMPILED_COVERAGE + playwright 入口冒烟 1 passed
        stdout = (
            'COMPILED_COVERAGE {"assertions":2,"form_executable_checks":0,"pending_locator":2}\n'
            "  ok 1 ... › YL商品主數據主數據管理入口冒烟 - TC-104552 (38.8s)\n"
            "  1 passed (50.7s)\n"
        )
        from app.services.web_case_result_mapper import stdout_has_executable_assertions
        assert stdout_has_executable_assertions(stdout) is False

    def test_empty_stdout_is_false(self):
        from app.services.web_case_result_mapper import stdout_has_executable_assertions
        assert stdout_has_executable_assertions("") is False
        assert stdout_has_executable_assertions(None) is False  # type: ignore[arg-type]

    def test_compiled_assertion_is_true(self):
        from app.services.web_case_result_mapper import stdout_has_executable_assertions
        stdout = (
            'COMPILED_ASSERTION {"name":"required_error:營業時間","outcome":"pass",'
            '"observed":"o","rule_id":"FR-x"}\n'
        )
        assert stdout_has_executable_assertions(stdout) is True

    def test_upload_evidence_only_is_true(self):
        from app.services.web_case_result_mapper import stdout_has_executable_assertions
        stdout = 'UPLOAD_EVIDENCE {"case":"TC-1","kind":"file_format","observed":"png"}\n'
        assert stdout_has_executable_assertions(stdout) is True
