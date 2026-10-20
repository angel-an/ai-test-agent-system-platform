# -*- coding: utf-8 -*-
"""P4：定位失败的「确定性现场」——执行器模板接线 + 台账解析。"""
from types import SimpleNamespace

from app.services import assertion_ledger
from app.services.web_case_compiler import _FORM_EXECUTOR_TS


class TestExecutorTemplateObservationWiring:
    def test_helpers_present(self):
        assert "collectLocatorObservation" in _FORM_EXECUTOR_TS
        assert "reportLocatorMiss" in _FORM_EXECUTOR_TS

    def test_observation_dump_is_business_agnostic(self):
        # 控件清单只按 tag/type/placeholder/maxlength/aria/text 采集，不含任何业务词
        forbidden = ["限购", "储值", "储值充值", "工单", "CS Module", "免单", "会员"]
        dump_region = _FORM_EXECUTOR_TS.split("collectLocatorObservation", 1)[1].split("reportLocatorMiss", 1)[0]
        for word in forbidden:
            assert word not in dump_region, f"现场采集段不应包含业务词: {word}"

    def test_miss_sites_wired_to_observation(self):
        # 不变量：凡是文案声称「定位现场见 [observation] 行」的失效点，
        # 都必须**真的**调用 reportLocatorMiss —— 否则文案把排查者指向一个永不存在的现场。
        # 2026-10-16 实测踩到：开关未定位分支（form:default_selected）漏了该调用，
        # 文案 4 处 vs 留痕 3 处，单测以硬编码 3 掩盖了这个不一致。
        calls = _FORM_EXECUTOR_TS.count("await reportLocatorMiss(")
        claims = _FORM_EXECUTOR_TS.count("定位现场见 [observation] 行")
        assert calls == claims, f"落空留痕({calls}) 与指向现场的文案({claims}) 数量必须一致"
        assert calls >= 4
        for where in ("form:required_error", "form:input_validation", "submit_phase",
                      "form:default_selected"):
            assert where in _FORM_EXECUTOR_TS

    def test_truncate_and_scope_handling(self):
        # 截断脱敏 + modal 不可用时降级到 page 级清单
        assert "slice(0, 80)" in _FORM_EXECUTOR_TS
        assert "page.evaluate(dumpFn, 30)" in _FORM_EXECUTOR_TS
        # evaluate 内的函数必须自包含（浏览器上下文没有闭包）
        dump_fn = _FORM_EXECUTOR_TS.split("const dumpFn = (root: any, limit: number) =>", 1)[1].split("};", 1)[0]
        assert "truncate(" not in dump_fn.split("const cut =")[1]  # 只用内部 cut


class TestLedgerObservationParsing:
    def test_parse_observation_lines(self):
        stdout = "\n".join([
            "COMPILED_ASSERTION " + '{"name":"input_validation:字段A [51字]","outcome":"unverified","observed":"未定位到输入框（定位现场见 [observation] 行）","rule_id":"R1","case_id":"TC-1"}',
            '[observation] {"where":"form:input_validation","field":"字段A","rule_id":"R1","case_id":"TC-1","tried":["{\\"hint\\":\\"xyz\\"}"],"pages":[{"index":0,"url":"https://example.test/page","title":"T"}],"frames":[],"active":null,"controls":[{"tag":"input","type":"text","placeholder":"请输入字段A","maxLength":50}]}',
            "COMPILED_ASSERTION_SUMMARY " + '{"total":1,"pass":0,"unverified":1,"blocked":0,"plan_checks":1,"attributed_checks":1}',
        ])
        ledger = assertion_ledger.parse_job_ledger(stdout)
        assert ledger["observation_count"] == 1
        obs = ledger["observations"][0]
        assert obs["field"] == "字段A"
        assert obs["controls"][0]["maxLength"] == 50
        # 坏行不炸
        ledger2 = assertion_ledger.parse_job_ledger("[observation] {not-json")
        assert ledger2["observation_count"] == 0

    def test_build_ledger_aggregates_observations(self):
        job = SimpleNamespace(
            script_identifier="WT-X", status="completed",
            stdout='[observation] {"where":"form:input_validation","field":"F","tried":[],"pages":[],"frames":[],"active":null,"controls":[]}',
        )
        result = assertion_ledger.build_assertion_ledger([job])
        assert len(result["observations"]) == 1
        assert result["observations"][0]["script_identifier"] == "WT-X"
        assert result["jobs"][0]["observation_count"] == 1
