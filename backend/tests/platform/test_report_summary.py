# -*- coding: utf-8 -*-
"""报告摘要解析（report_summary）单元测试。

覆盖点：
1. 从 Playwright HTML 报告提取内嵌 base64 zip / report.json；
2. outcome → 前端 status 的映射（含 flaky / skipped / timedOut 回退）；
3. 归一化后的键与前端 `parseResultSummary` 期望一致；
4. 步骤与错误的提取；
5. 不可解析时的确定行为（返回 None / 抛 ReportParseError）；
6. 从报告 ZIP 中定位 index.html 的选路规则。

测试用**合成报告**，不依赖 workspace 下的真实报告文件。
"""

from __future__ import annotations

import base64
import io
import json
import zipfile
from pathlib import Path

import pytest

from app.services.report_summary import (
    EMBEDDED_ZIP_PREFIX,
    OUTCOME_TO_STATUS,
    ReportParseError,
    _build_analysis,
    _classify_failure,
    _extract_api_steps,
    _extract_assertions,
    build_result_summary,
    extract_embedded_zip,
    extract_report_json,
    summarize_html_report,
    summarize_report_archive,
)


# ---------------------------------------------------------------------------
# 夹具：合成一份 Playwright report.json 与其 HTML 外壳
# ---------------------------------------------------------------------------

def sample_report() -> dict:
    return {
        "metadata": {},
        "startTime": 1787727715137,
        "duration": 1234.5,
        "projectNames": ["chromium"],
        "stats": {
            "total": 4,
            "expected": 1,
            "unexpected": 1,
            "flaky": 1,
            "skipped": 1,
            "ok": False,
        },
        "files": [
            {
                "fileId": "file-1",
                "fileName": "PR-2/order.spec.ts",
                "stats": {},
                "tests": [
                    {
                        "testId": "t-pass",
                        "title": "查询订单成功",
                        "outcome": "expected",
                        "duration": 120,
                        "results": [{
                            "status": "passed",
                            "duration": 120,
                            "errors": [],
                            "steps": [
                                {"title": "发送 GET 请求", "duration": 80},
                                {"title": "校验响应体", "duration": 40},
                            ],
                        }],
                    },
                    {
                        "testId": "t-fail",
                        "title": "缺少必填参数应拒绝",
                        "outcome": "unexpected",
                        "duration": 210,
                        "results": [{
                            "status": "failed",
                            "duration": 210,
                            "errors": [{"message": "expect(received).toBe(expected)"}],
                            "steps": [
                                {"title": "发送请求", "duration": 100, "error": {"message": "断言失败"}},
                            ],
                        }],
                    },
                    {
                        "testId": "t-flaky",
                        "title": "删除后重试通过",
                        "outcome": "flaky",
                        "duration": 300,
                        "results": [
                            {"status": "failed", "duration": 150, "errors": []},
                            {"status": "passed", "duration": 150, "errors": []},
                        ],
                    },
                    {
                        "testId": "t-skip",
                        "title": "live 未开启则跳过",
                        "outcome": "skipped",
                        "duration": 0,
                        "results": [],
                    },
                ],
            }
        ],
    }


def embed_in_html(report: dict, *, prefix: str = "") -> bytes:
    """把 report.json 打包成 zip、base64 内嵌进一份 HTML，模拟 Playwright 报告。"""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("report.json", json.dumps(report, ensure_ascii=False))
    payload = base64.b64encode(buf.getvalue()).decode("ascii")
    html = (
        "<html><body>"
        f'<script id="playwrightReportBase64" type="application/zip">{prefix}'
        f"{EMBEDDED_ZIP_PREFIX}{payload}</script>"
        "</body></html>"
    )
    return html.encode("utf-8")


# ---------------------------------------------------------------------------
# 1. 提取
# ---------------------------------------------------------------------------

class TestExtraction:
    def test_extract_embedded_zip_roundtrip(self):
        blob = extract_embedded_zip(embed_in_html(sample_report()))
        assert blob is not None
        with zipfile.ZipFile(io.BytesIO(blob)) as archive:
            assert "report.json" in archive.namelist()

    def test_extract_report_json(self):
        report = extract_report_json(embed_in_html(sample_report()))
        assert report is not None
        assert report["stats"]["total"] == 4

    def test_returns_none_when_no_embedded_payload(self):
        plain = b"<html><body>not a playwright report</body></html>"
        assert extract_embedded_zip(plain) is None
        assert extract_report_json(plain) is None

    def test_returns_none_on_corrupt_base64(self):
        html = f'<script>{EMBEDDED_ZIP_PREFIX}!!!!not-base64!!!!</script>'.encode()
        assert extract_embedded_zip(html) is None

    def test_accepts_str_input(self):
        html = embed_in_html(sample_report()).decode("utf-8")
        assert extract_report_json(html) is not None


# ---------------------------------------------------------------------------
# 2. 归一化
# ---------------------------------------------------------------------------

class TestBuildResultSummary:
    def test_counts_by_outcome(self):
        summary = build_result_summary(sample_report())
        assert summary["total"] == 4
        assert summary["passed"] == 2   # expected + flaky
        assert summary["failed"] == 1
        assert summary["skipped"] == 1
        assert summary["timed_out"] == 0
        assert summary["flaky"] == 1

    def test_keys_match_frontend_contract(self):
        """前端 parseResultSummary 读取的键必须齐全。"""
        summary = build_result_summary(sample_report())
        for key in ("total", "passed", "failed", "skipped", "duration_ms", "tests"):
            assert key in summary, f"缺少前端所需键: {key}"
        for test in summary["tests"]:
            for key in ("title", "status", "duration_ms", "errors", "steps"):
                assert key in test

    def test_status_values_are_frontend_whitelist(self):
        allowed = {"passed", "failed", "skipped", "timedOut"}
        summary = build_result_summary(sample_report())
        assert {t["status"] for t in summary["tests"]} <= allowed

    def test_duration_and_meta(self):
        summary = build_result_summary(sample_report())
        assert summary["duration_ms"] == 1234
        assert summary["browser"] == "chromium"
        assert summary["start_time"].startswith("2026-")

    def test_errors_and_steps_extracted(self):
        summary = build_result_summary(sample_report())
        by_title = {t["title"]: t for t in summary["tests"]}

        passed = by_title["查询订单成功"]
        assert [s["title"] for s in passed["steps"]] == ["发送 GET 请求", "校验响应体"]
        assert all(s["status"] == "passed" for s in passed["steps"])
        assert passed["errors"] == []

        failed = by_title["缺少必填参数应拒绝"]
        assert failed["errors"] == ["expect(received).toBe(expected)"]
        assert failed["steps"][0]["status"] == "failed"

    def test_flaky_duration_sums_all_attempts(self):
        summary = build_result_summary(sample_report())
        flaky = next(t for t in summary["tests"] if t["title"] == "删除后重试通过")
        assert flaky["duration_ms"] == 300
        assert flaky["status"] == "passed"

    def test_outcome_to_status_map_is_total_for_playwright_outcomes(self):
        assert OUTCOME_TO_STATUS["expected"] == "passed"
        assert OUTCOME_TO_STATUS["unexpected"] == "failed"
        assert OUTCOME_TO_STATUS["flaky"] == "passed"
        assert OUTCOME_TO_STATUS["skipped"] == "skipped"

    def test_timed_out_falls_back_to_result_status(self):
        report = {
            "stats": {"total": 1},
            "files": [{
                "fileName": "a.spec.ts",
                "tests": [{
                    "title": "超时用例",
                    "outcome": "",
                    "results": [{"status": "timedOut", "duration": 60000, "errors": []}],
                }],
            }],
        }
        summary = build_result_summary(report)
        assert summary["tests"][0]["status"] == "timedOut"
        assert summary["timed_out"] == 1

    def test_empty_report_does_not_crash(self):
        summary = build_result_summary({})
        assert summary["total"] == 0
        assert summary["tests"] == []
        assert summary["duration_ms"] == 0

    def test_total_falls_back_to_test_count(self):
        report = {
            "files": [{"fileName": "a.spec.ts", "tests": [
                {"title": "t1", "outcome": "expected", "results": []},
                {"title": "t2", "outcome": "expected", "results": []},
            ]}],
        }
        assert build_result_summary(report)["total"] == 2

    def test_multiple_files_flattened_with_file_name(self):
        report = {
            "stats": {"total": 2},
            "files": [
                {"fileName": "a.spec.ts", "tests": [{"title": "A", "outcome": "expected"}]},
                {"fileName": "b.spec.ts", "tests": [{"title": "B", "outcome": "expected"}]},
            ],
        }
        summary = build_result_summary(report)
        assert [t["file"] for t in summary["tests"]] == ["a.spec.ts", "b.spec.ts"]


# ---------------------------------------------------------------------------
# 3. 端到端：HTML / ZIP
# ---------------------------------------------------------------------------

class TestSummarizeEndToEnd:
    def test_summarize_html_report(self):
        summary = summarize_html_report(embed_in_html(sample_report()))
        assert summary["total"] == 4
        assert summary["source"] == "playwright_html_report"

    def test_summarize_html_report_raises_on_plain_html(self):
        with pytest.raises(ReportParseError):
            summarize_html_report(b"<html>plain</html>")

    def test_summarize_archive_prefers_shallowest_index(self):
        """ZIP 里存在多个 index.html 时应取层级最浅的那个。"""
        html = embed_in_html(sample_report())
        archive_bytes = io.BytesIO()
        with zipfile.ZipFile(archive_bytes, "w") as archive:
            archive.writestr("playwright-report/index.html", html)
            archive.writestr("nested/deeper/index.html", b"<html>decoy</html>")
        summary = summarize_report_archive(archive_bytes.getvalue())
        assert summary["total"] == 4

    def test_summarize_archive_raises_without_index(self):
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as archive:
            archive.writestr("readme.txt", "no report here")
        with pytest.raises(ReportParseError):
            summarize_report_archive(buf.getvalue())

    def test_summarize_archive_raises_on_non_playwright_index(self):
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as archive:
            archive.writestr("index.html", "<html>plain</html>")
        with pytest.raises(ReportParseError):
            summarize_report_archive(buf.getvalue())

    def test_summarize_archive_raises_on_bad_zip(self):
        with pytest.raises(ReportParseError):
            summarize_report_archive(b"definitely-not-a-zip")


# ---------------------------------------------------------------------------
# 4. 断言提取（Expected/Received 解析）
# ---------------------------------------------------------------------------

class TestExtractAssertions:
    EXPECT_RECEIVE_ERROR = (
        "expect(received).toBe(expected) // Object.is equality\n"
        "Expected value: 200\n"
        "Received value: 500\n"
        "    at /tests/api.spec.ts:42:15"
    )

    def test_expected_received_pair(self):
        assertions = _extract_assertions(self.EXPECT_RECEIVE_ERROR)
        assert len(assertions) == 1
        a = assertions[0]
        assert a["expected"] == "200"
        assert a["received"] == "500"
        assert a["passed"] is False
        assert "toBe" in a["matcher"]
        assert "expect(" in a["expression"]

    def test_matcher_description_mapping(self):
        cases = {
            "expect(data.code).toBe(0)": "toBe",
            "expect(body.list).toHaveLength(3)": "toHaveLength",
            "expect(text).toContain(\"ok\")": "toContain",
            "expect(resp).toBeOK()": "toBeOK",
        }
        for snippet, matcher in cases.items():
            extracted = _extract_assertions(f"{snippet}\nExpected value: 1\nReceived value: 2")
            assert extracted, f"未提取到断言: {snippet}"
            assert matcher in extracted[0]["matcher"]

    def test_empty_and_non_assertion_errors(self):
        assert _extract_assertions("") == []
        assert _extract_assertions("getaddrinfo ENOTFOUND host") == []

    def test_dedup_same_expression(self):
        text = self.EXPECT_RECEIVE_ERROR + "\n" + self.EXPECT_RECEIVE_ERROR
        assert len(_extract_assertions(text)) == 1

    def test_long_values_truncated(self):
        long_value = "x" * 500
        text = f"expect(a).toBe(b)\nExpected value: {long_value}\nReceived value: 1"
        a = _extract_assertions(text)[0]
        assert a["expected"] is not None and len(a["expected"]) <= 301


# ---------------------------------------------------------------------------
# 5. API 请求步骤提取
# ---------------------------------------------------------------------------

class TestExtractApiSteps:
    def test_filters_non_request_steps_and_shortens_url(self):
        test = {
            "outcome": "expected",
            "results": [{
                "status": "passed",
                "steps": [
                    {"title": "beforeEach hook", "duration": 5},
                    {
                        "title": "POST http://crm.example.com/api/json/api_memRegister?key=abc",
                        "duration": 120,
                        "location": {"file": "a.spec.ts", "line": 42},
                    },
                    {
                        "title": "GET https://host/api/v1/users?page=1",
                        "duration": 80,
                        "error": {"message": "expect(received).toBe(expected)\nExpected value: 200\nReceived value: 404 Not Found"},
                    },
                ],
            }],
        }
        steps = _extract_api_steps(test)
        assert len(steps) == 2  # hook 被过滤
        assert steps[0]["title"] == "POST /api/json/api_memRegister?key=abc"
        assert steps[0]["location"] == "a.spec.ts:42"
        assert steps[0]["status"] == "passed"
        assert steps[1]["status"] == "failed"
        assert steps[1]["assertions"][0]["received"] == "404 Not Found"

    def test_no_steps_returns_empty(self):
        assert _extract_api_steps({"results": []}) == []
        assert _extract_api_steps({}) == []


# ---------------------------------------------------------------------------
# 6. 失败原因分类
# ---------------------------------------------------------------------------

class TestClassifyFailure:
    def _failed_test(self, message: str) -> dict:
        return {
            "outcome": "unexpected",
            "results": [{"status": "failed", "errors": [{"message": message}]}],
        }

    def test_server_error_is_product_defect(self):
        fa = _classify_failure(self._failed_test(
            "expect(received).toBe(expected)\nExpected value: 200\nReceived value: 500\n 500 Internal Server Error"
        ))
        assert fa["category"] == "server_error"
        assert fa["is_product_defect"] is True
        assert fa["http_status_code"] == 500

    def test_assertion_mismatch_not_defect(self):
        fa = _classify_failure(self._failed_test(
            "expect(received).toBe(expected)\nExpected value: 0\nReceived value: -1"
        ))
        assert fa["category"] == "assertion"
        assert fa["is_product_defect"] is False

    def test_network_error(self):
        fa = _classify_failure(self._failed_test("getaddrinfo ENOTFOUND api.example.com"))
        assert fa["category"] == "network"
        assert fa["is_product_defect"] is False

    def test_script_error(self):
        fa = _classify_failure(self._failed_test("TypeError: Cannot read properties of undefined (reading 'code')"))
        assert fa["category"] == "script_error"

    def test_passed_test_has_no_analysis(self):
        fa = _classify_failure({"outcome": "expected", "results": [{"status": "passed", "errors": []}]})
        assert fa["category"] == "none"

    def test_status_code_401(self):
        fa = _classify_failure(self._failed_test("401 Unauthorized"))
        assert fa["category"] == "auth"
        assert fa["http_status_code"] == 401


# ---------------------------------------------------------------------------
# 7. 报告级分析结论
# ---------------------------------------------------------------------------

class TestBuildAnalysis:
    def test_mixed_failures_conclusion(self):
        server_err = {
            "outcome": "unexpected",
            "results": [{"status": "failed", "errors": [{"message": "500 Internal Server Error"}]}],
        }
        assertion_err = {
            "outcome": "unexpected",
            "results": [{"status": "failed", "errors": [{"message": "expect(x).toBe(0)\nExpected value: 0\nReceived value: 9"}]}],
        }
        passed = {"outcome": "expected", "results": [{"status": "passed", "errors": []}]}
        report = {"stats": {"total": 3}, "files": [{"fileName": "a.spec.ts", "tests": [server_err, assertion_err, passed]}]}
        summary = build_result_summary(report)

        analysis = summary["analysis"]
        assert analysis["total"] == 3
        assert analysis["passed"] == 1
        assert analysis["failed"] == 2
        assert analysis["product_defect_count"] == 1
        assert analysis["env_issue_count"] == 1
        assert analysis["failed_by_category"] == {"server_error": 1, "assertion": 1}
        assert "疑似产品缺陷" in analysis["conclusion"]

        # 每条用例都带详细字段
        for t in summary["tests"]:
            assert "failure_analysis" in t
            assert "api_steps" in t
            assert "assertions" in t
            assert "location" in t

    def test_all_passed_conclusion(self):
        passed = {"outcome": "expected", "results": [{"status": "passed", "errors": []}]}
        report = {"stats": {"total": 2}, "files": [{"fileName": "a.spec.ts", "tests": [passed, passed]}]}
        analysis = build_result_summary(report)["analysis"]
        assert analysis["pass_rate"] == 1.0
        assert "100%" in analysis["conclusion"]


# ---------------------------------------------------------------------------
# 7. 入参/出参：单文件明细（<fileId>.json）合并 + testInfo.attach 留证
# ---------------------------------------------------------------------------

def embed_files_in_html(files: dict) -> bytes:
    """把多个 JSON 打包成 zip、base64 内嵌进 HTML（含单文件明细的真实产物形状）。"""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, payload in files.items():
            archive.writestr(name, json.dumps(payload, ensure_ascii=False))
    payload = base64.b64encode(buf.getvalue()).decode("ascii")
    html = (
        "<html><body>"
        "<script id=\"playwrightReportBase64\" type=\"application/zip\">"
        f"{EMBEDDED_ZIP_PREFIX}{payload}</script>"
        "</body></html>"
    )
    return html.encode("utf-8")


def report_without_steps() -> dict:
    """真实产物形状：report.json 的 results 只有骨架，没有 steps/errors。"""
    return {
        "metadata": {},
        "startTime": 1700000000000,
        "duration": 300,
        "stats": {"total": 1, "expected": 1, "unexpected": 0, "flaky": 0, "skipped": 0, "ok": True},
        "files": [{
            "fileId": "abc123",
            "fileName": "memRegister.spec.ts",
            "stats": {},
            "tests": [{
                "testId": "t-1",
                "title": "01 正常调用",
                "outcome": "expected",
                "duration": 300,
                "location": {"file": "memRegister.spec.ts", "line": 74},
                "results": [{
                    "attachments": [],
                    "startTime": "2026-09-18T03:37:13.000Z",
                    "workerIndex": 0,
                }],
            }],
        }],
        "projectNames": ["chromium"],
    }


def detail_file(with_io: bool = True) -> dict:
    attachments = []
    if with_io:
        attachments = [
            {
                "name": "api-request",
                "contentType": "application/json",
                "body": json.dumps({
                    "method": "POST",
                    "url": "http://crm/cop/json",
                    "headers": {"appid": "***", "ts": "20260918113314", "sign": "***"},
                    "body": {"MobilePhone": "12345678", "CountryCode": "852"},
                }),
            },
            {
                "name": "api-response",
                "contentType": "application/json",
                "body": json.dumps({"status": 200, "body": {"Code": "0"}}),
            },
        ]
    return {
        "fileId": "abc123",
        "fileName": "memRegister.spec.ts",
        "tests": [{
            "testId": "t-1",
            "title": "01 正常调用",
            "outcome": "expected",
            "location": {"file": "memRegister.spec.ts", "line": 74},
            "results": [{
                "duration": 300,
                "status": "passed",
                "errors": [],
                "attachments": attachments,
                "steps": [
                    {"title": "Before Hooks", "duration": 12, "steps": []},
                    {
                        "title": 'POST "/cop/json"',
                        "duration": 200,
                        "location": {"file": "memRegister.spec.ts", "line": 74},
                    },
                    {
                        "title": 'Expect "toBe"',
                        "duration": 1,
                        "location": {"file": "memRegister.spec.ts", "line": 84},
                    },
                ],
            }],
        }],
    }


class TestDetailMergeAndIo:
    def test_report_json_alone_has_no_steps(self):
        """仅 report.json 时明细为空——PR-12 报告此前「请求明细」为空的根因。"""
        summary = summarize_html_report(embed_in_html(report_without_steps()))
        assert summary["tests"][0]["steps"] == []
        assert summary["tests"][0]["api_steps"] == []
        assert summary["tests"][0]["request"] is None
        assert summary["tests"][0]["response"] is None

    def test_detail_file_restores_steps(self):
        html = embed_files_in_html({
            "report.json": report_without_steps(),
            "abc123.json": detail_file(),
        })
        test = summarize_html_report(html)["tests"][0]
        assert len(test["steps"]) == 3
        first = test["api_steps"][0]["title"]
        assert first.startswith("POST"), first
        assert "cop/json" in first, first

    def test_io_attachments_exposed_as_request_response(self):
        html = embed_files_in_html({
            "report.json": report_without_steps(),
            "abc123.json": detail_file(),
        })
        test = summarize_html_report(html)["tests"][0]
        assert test["request"]["method"] == "POST"
        assert test["request"]["body"]["MobilePhone"] == "12345678"
        assert test["request"]["headers"]["sign"] == "***"
        assert test["response"]["status"] == 200
        assert test["response"]["body"]["Code"] == "0"

    def test_missing_detail_file_is_tolerated(self):
        summary = summarize_html_report(
            embed_files_in_html({"report.json": report_without_steps()})
        )
        assert summary["total"] == 1
        assert summary["tests"][0]["status"] == "passed"

    def test_detail_errors_drive_failure_analysis(self):
        detail = detail_file(with_io=False)
        detail["tests"][0]["outcome"] = "unexpected"
        detail["tests"][0]["results"][0]["status"] = "failed"
        detail["tests"][0]["results"][0]["errors"] = [{
            "message": (
                "Error: expect(received).toBe(expected)\n\n"
                "Expected: 200\nReceived: 500"
            )
        }]
        report = report_without_steps()
        report["stats"]["expected"] = 0
        report["stats"]["unexpected"] = 1
        report["stats"]["ok"] = False
        report["files"][0]["tests"][0]["outcome"] = "unexpected"
        html = embed_files_in_html({"report.json": report, "abc123.json": detail})
        test = summarize_html_report(html)["tests"][0]
        assert test["status"] == "failed"
        assert test["errors"], "单文件明细里的错误必须合并进摘要"
        assert test["failure_analysis"]["category"] not in (None, "", "none")
