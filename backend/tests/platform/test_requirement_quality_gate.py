# -*- coding: utf-8 -*-
"""阶段一 + 阶段二 测试：需求质量门控、追踪链路、历史数据兼容。

对应计划《最小测试清单》：

门控测试
- 完整需求允许生成用例
- 缺少验收标准时判定 Incomplete
- 缺少业务目标时判定 Blocked
- 缺少菜单路径时仍允许继续分析
- 优先级缺失时标记"待确认"，不能自动补 P1
- 解析失败时禁止生成正式用例

追踪测试
- 新用例必须包含 feature_id
- 一个功能点可以关联多个用例
- 无覆盖功能点能被识别
- 旧用例无追踪字段时仍能正常读取和执行

兼容测试
- 旧格式用例可以转换
- 旧 Excel 导出不受影响
- 现有 API/Web 执行链路不受影响
- 增量追加不会产生重复编号
- 现有质量门禁继续生效
"""

from __future__ import annotations

import json

import pytest

from app.test_platform.requirements import (
    FeaturePoint,
    FeaturePriority,
    GapCheckKind,
    PRDAnalysisResult,
    PRDQualityStatus,
    TestCaseGenerationStatus,
    analyze_requirement_text,
    assert_unique_feature_ids,
    build_coverage_matrix,
    build_feature_criteria,
    check_generation_gate,
    classify_cases,
    convert_legacy_case,
    convert_legacy_cases,
    extract_feature_points,
    generate_feature_id,
    generate_testcases_with_gate,
    identify_coverage_gaps,
    is_legacy_case,
    normalize_feature_id,
    parse_analysis_payload,
    render_analysis_markdown,
    render_coverage_report,
    run_requirement_pipeline,
    validate_case_traceability,
)
from app.test_platform.requirements.gate import RequirementNotReadyError
from app.test_platform.requirements.legacy_adapter import attach_traceability


# ---------------------------------------------------------------------------
# 需求样本
# ---------------------------------------------------------------------------

CASE_ID = "TC-439001"

COMPLETE_PRD = """# 会员储值充值需求

优先级：P1

## 功能点清单

| 编号 | 名称 |
| --- | --- |
| FP-001 | 企业储值充值 |

### FP-001 企业储值充值

业务目标：企业客户可在后台为企业账户充值余额，替代线下转账。
角色权限：企业管理员可操作；无权限用户访问时提示权限不足并拒绝访问。
前置条件：已登录企业控制台并开通储值账户。
操作流程：进入储值账户列表，点击创建储值充值，输入手机号与充值金额后保存。
输入与数据规则：手机号必填且为 11 位；金额必填，最多两位小数。
业务规则：单笔充值金额上限 50000 元，超出时提示金额超限。
正常结果：保存成功并生成一条储值流水记录。
验收标准：充值成功后账户余额增加对应金额，且流水记录可查询。
计算公式：充值后余额 = 原余额 + 充值金额，精度保留两位小数（四舍五入）。
状态流转：待支付 → 支付成功 → 已入账。
"""

# 缺少「验收标准」（Incomplete），其余必填项齐全
INCOMPLETE_PRD_NO_ACCEPTANCE = """# 会员积分查询需求

## 功能点清单

| 编号 | 名称 |
| --- | --- |
| FP-001 | 会员积分查询 |

### FP-001 会员积分查询

业务目标：门店店员可查询会员当前可用积分，便于线下核销。
角色权限：门店店员可操作；无权限用户访问时提示权限不足。
前置条件：已登录门店端。
操作流程：进入会员中心，输入手机号，点击查询。
输入与数据规则：手机号必填。
业务规则：积分按整数显示。
正常结果：页面展示会员当前可用积分。
"""

BLOCKED_PRD_NO_GOAL = """# 标签资源管理

## 功能点清单

| 编号 | 名称 |
| --- | --- |
| FP-001 | 标签管理 |


### FP-001 标签管理

角色权限：运营人员可操作；无权限用户访问时提示权限不足。
前置条件：已登录后台。
操作流程：进入标签列表，点击新建标签，填写名称后保存。
输入与数据规则：标签名称必填，最长 20 字符。
业务规则：标签名称不可重复。
正常结果：列表展示新建标签。
验收标准：新建标签后可在列表查询到。
"""


# ---------------------------------------------------------------------------
# 门控测试
# ---------------------------------------------------------------------------

class TestQualityGateComplete:
    """完整需求允许生成用例"""

    def test_status_complete_and_can_generate(self):
        result = analyze_requirement_text(COMPLETE_PRD)
        assert result.status == PRDQualityStatus.COMPLETE
        assert result.can_generate_testcases is True
        assert result.missing_items == []
        assert result.blocked_reasons == []

    def test_gate_allows(self):
        result = analyze_requirement_text(COMPLETE_PRD)
        allowed, reasons = check_generation_gate(result)
        assert allowed is True
        assert reasons == []

    def test_pipeline_continues_to_generation(self):
        pipeline = run_requirement_pipeline(COMPLETE_PRD, requirement_id="REQ-1")
        assert pipeline.paused is False
        assert "testcase_generation" in [s.value for s in pipeline.next_stages]
        assert pipeline.feature_points


class TestQualityGateIncomplete:
    """缺少验收标准时判定 Incomplete"""

    def test_missing_acceptance_criteria(self):
        result = analyze_requirement_text(INCOMPLETE_PRD_NO_ACCEPTANCE)
        assert result.status == PRDQualityStatus.INCOMPLETE
        assert result.can_generate_testcases is False
        assert any(g.item == "验收标准" for g in result.missing_items)

    def test_gate_blocks_with_reason(self):
        result = analyze_requirement_text(INCOMPLETE_PRD_NO_ACCEPTANCE)
        allowed, reasons = check_generation_gate(result)
        assert allowed is False
        assert any("验收标准" in r for r in reasons)

    def test_generation_blocked_response(self):
        response = generate_testcases_with_gate(INCOMPLETE_PRD_NO_ACCEPTANCE)
        assert response["prd_quality"]["status"] == "Incomplete"
        assert response["testcase_generation"]["status"] == "blocked"
        assert response["prd_quality"]["can_generate_testcases"] is False


class TestQualityGateBlocked:
    """缺少业务目标时判定 Blocked"""

    def test_missing_business_goal_blocks(self):
        result = analyze_requirement_text(BLOCKED_PRD_NO_GOAL)
        assert result.status == PRDQualityStatus.BLOCKED
        assert result.can_generate_testcases is False
        assert any("业务目标" in reason for reason in result.blocked_reasons)

    def test_blocked_items_are_marked(self):
        result = analyze_requirement_text(BLOCKED_PRD_NO_GOAL)
        blocking = [g for g in result.missing_items if g.blocking]
        assert any(g.item == "业务目标" for g in blocking)

    def test_missing_operation_flow_also_blocks(self):
        text = BLOCKED_PRD_NO_GOAL.replace("操作流程", "功能说明")
        result = analyze_requirement_text(text.replace("功能说明：进入标签列表，点击新建标签，填写名称后保存。", ""))
        assert result.status == PRDQualityStatus.BLOCKED


class TestQualityGateNonBlocking:
    """缺少菜单路径时仍允许继续分析"""

    def test_menu_path_missing_is_ignored(self):
        # COMPLETE_PRD 不含"菜单路径"，依旧 Complete
        result = analyze_requirement_text(COMPLETE_PRD)
        assert "菜单路径" not in [g.item for g in result.missing_items]
        assert result.status == PRDQualityStatus.COMPLETE

    def test_menu_path_keyword_does_not_create_gap(self):
        text = COMPLETE_PRD + "\n菜单路径：忠诚度 → 储值管理\n"
        result = analyze_requirement_text(text)
        assert result.status == PRDQualityStatus.COMPLETE


class TestPriorityHandling:
    """优先级缺失时标记"待确认"，不能自动补 P1"""

    def test_priority_missing_is_flagged_not_inferred(self):
        result = analyze_requirement_text(INCOMPLETE_PRD_NO_ACCEPTANCE)
        flagged = {g.item: g for g in result.flagged_items}
        assert "优先级" in flagged
        assert "待确认" in flagged["优先级"].detail
        assert "P1" in flagged["优先级"].detail  # 明确说明不自动推断 P1
        assert result.status != PRDQualityStatus.COMPLETE  # 优先级缺失不阻断，但也不能凭空 Complete

    def test_feature_point_priority_pending_when_missing(self):
        features = extract_feature_points(INCOMPLETE_PRD_NO_ACCEPTANCE)
        assert features[0].priority == FeaturePriority.PENDING
        assert features[0].priority.value == "待确认"

    def test_feature_point_priority_parsed_when_present(self):
        features = extract_feature_points(COMPLETE_PRD)
        assert features[0].priority == FeaturePriority.P1


class TestParseFailure:
    """解析失败时禁止生成正式用例"""

    @pytest.mark.parametrize(
        "payload",
        [
            "不是 JSON：{}",
            "",
            "{bad json",
            None,
        ],
    )
    def test_unparseable_payload_defaults_incomplete(self, payload):
        result = parse_analysis_payload(payload)
        assert result.status == PRDQualityStatus.INCOMPLETE
        assert result.can_generate_testcases is False
        assert result.parse_error

    def test_missing_status_field_defaults_incomplete(self):
        result = parse_analysis_payload({"can_generate_testcases": True})
        assert result.status == PRDQualityStatus.INCOMPLETE
        assert result.can_generate_testcases is False

    def test_can_generate_true_without_complete_status_is_overridden(self):
        """不变量：非 Complete 状态一律禁止生成"""
        result = parse_analysis_payload(
            {"status": "Incomplete", "can_generate_testcases": True}
        )
        assert result.can_generate_testcases is False

    def test_fenced_json_block_is_parsed(self):
        payload = "```json\n" + json.dumps(
            {"status": "Complete", "can_generate_testcases": True}, ensure_ascii=False
        ) + "\n```"
        result = parse_analysis_payload(payload)
        assert result.status == PRDQualityStatus.COMPLETE
        assert result.can_generate_testcases is True

    def test_incomplete_payload_blocks_generation_end_to_end(self):
        response = generate_testcases_with_gate(
            COMPLETE_PRD, analysis_payload="{ this is not json"
        )
        assert response["testcase_generation"]["status"] == "blocked"
        assert response["prd_quality"]["status"] == "Incomplete"


class TestGateInvariants:
    """门控不变量与拒绝语义"""

    def test_none_analysis_blocks(self):
        allowed, reasons = check_generation_gate(None)
        assert allowed is False
        assert reasons

    def test_force_flag_is_gone(self):
        """force 放行开关已被移除：客户端布尔参数不能绕过 V1.2 硬门控。

        人工放行改为服务端受控凭证（REQUIREMENT_GATE_OVERRIDE_TOKEN + 审计），
        详见 tests/platform/test_requirement_quality_review_fixes.py。
        """
        import inspect

        params = inspect.signature(check_generation_gate).parameters
        assert "force" not in params

        allowed, reasons = check_generation_gate(
            analyze_requirement_text(BLOCKED_PRD_NO_GOAL)
        )
        assert allowed is False
        assert reasons

    def test_require_complete_raises(self):
        from app.test_platform.services import TestCaseGenerationService

        service = TestCaseGenerationService()
        with pytest.raises(RequirementNotReadyError):
            service.assert_ready(analyze_requirement_text(BLOCKED_PRD_NO_GOAL))

    def test_blocked_response_contract_fields(self):
        response = generate_testcases_with_gate(BLOCKED_PRD_NO_GOAL)
        assert set(["prd_quality", "analysis_report", "testcase_generation"]).issubset(
            response.keys()
        )
        assert response["analysis_report"]["blocked_reasons"]
        assert response["testcase_generation"]["status"] == "blocked"

    def test_markdown_report_contains_reasons_and_missing(self):
        result = analyze_requirement_text(BLOCKED_PRD_NO_GOAL)
        markdown = render_analysis_markdown(result, requirement_id="REQ-9")
        assert "需求质量分析报告" in markdown
        assert "业务目标" in markdown
        assert "不生成正式测试用例" in markdown


# ---------------------------------------------------------------------------
# 追踪测试
# ---------------------------------------------------------------------------

def _build_cases() -> list[dict]:
    return [
        {
            "id": "TC-001",
            "title": "企业储值充值-正向",
            "feature_id": "FP-001",
            "requirement_id": "REQ-1",
            "acceptance_id": "FP-001-AC-01",
            "source_location": "doc.md#FP-001",
            "tp_code": "TP-001",
            "steps": [{"step": "输入手机号与金额", "result": "页面提示已保存"}],
        },
        {
            "id": "TC-002",
            "title": "企业储值充值-金额超限",
            "feature_id": "FP-001",
            "requirement_id": "REQ-1",
            "tp_code": "TP-002",
            "steps": [{"step": "输入 60000 元", "result": "提示金额超限"}],
        },
    ]


class TestFeaturePointModel:
    """功能点模型与唯一编号"""

    def test_generate_feature_id(self):
        assert generate_feature_id(1) == "FP-001"
        assert generate_feature_id(42) == "FP-042"

    def test_generate_feature_id_rejects_invalid(self):
        with pytest.raises(ValueError):
            generate_feature_id(0)
        with pytest.raises(ValueError):
            generate_feature_id("abc")

    def test_normalize_feature_id(self):
        assert normalize_feature_id("fp-7") == "FP-007"
        assert normalize_feature_id("功能点 12") == "FP-012"
        assert normalize_feature_id("12") == "FP-012"

    def test_feature_point_to_from_dict_roundtrip(self):
        fp = FeaturePoint(
            feature_id="FP-001",
            feature_name="企业储值充值",
            business_goal="企业可在后台充值",
            acceptance_criteria=["余额增加"],
            priority=FeaturePriority.P1,
        )
        restored = FeaturePoint.from_dict(fp.to_dict())
        assert restored.feature_id == "FP-001"
        assert restored.priority == FeaturePriority.P1
        assert restored.acceptance_criteria == ["余额增加"]

    def test_extracted_feature_ids_are_unique(self):
        features = extract_feature_points(COMPLETE_PRD)
        assert features
        assert assert_unique_feature_ids(features) == []

    def test_unique_id_fallback_on_conflict(self):
        text = "1.1 相同功能\n\n1.2 相同功能\n\n1.3 相同功能"
        features = extract_feature_points(text)
        assert assert_unique_feature_ids(features) == []
        assert len({f.feature_id for f in features}) == len(features)

    def test_acceptance_criteria_ids_are_stable(self):
        feature = FeaturePoint(
            feature_id="FP-001",
            feature_name="充值",
            acceptance_criteria=["余额增加", "流水可查"],
        )
        criteria = build_feature_criteria(feature)
        assert [c["acceptance_id"] for c in criteria] == ["FP-001-AC-01", "FP-001-AC-02"]


class TestTraceability:
    """新用例必须包含 feature_id；一个功能点可关联多个用例"""

    def test_new_case_requires_feature_id(self):
        problems = validate_case_traceability(
            {"id": "TC-100", "title": "无追踪字段", "requirement_id": "REQ-1"}
        )
        assert any("feature_id" in p for p in problems)

    def test_traced_case_passes(self):
        assert validate_case_traceability(_build_cases()[0]) == []

    def test_legacy_case_is_allowed(self):
        legacy = {"id": "TC-OLD", "title": "旧用例", "steps": [{"step": "a", "result": "b"}]}
        assert is_legacy_case(legacy) is True
        assert validate_case_traceability(legacy) == []

    def test_multiple_cases_per_feature(self):
        features = extract_feature_points(COMPLETE_PRD)
        matrix = build_coverage_matrix(features, _build_cases(), requirement_id="REQ-1")
        assert matrix.features[0].case_ids == ["TC-001", "TC-002"]
        assert matrix.features[0].case_count == 2

    def test_coverage_matrix_counts_execution_results(self):
        features = extract_feature_points(COMPLETE_PRD)
        matrix = build_coverage_matrix(
            features,
            _build_cases(),
            requirement_id="REQ-1",
            results_by_case={"TC-001": "passed"},
        )
        assert matrix.results_by_case == {"TC-001": "passed"}


class TestCoverageGaps:
    """无覆盖功能点能被识别"""

    def test_uncovered_feature_detected(self):
        features = [
            FeaturePoint(feature_id="FP-001", feature_name="已覆盖"),
            FeaturePoint(feature_id="FP-002", feature_name="未覆盖"),
        ]
        cases = [
            {
                "id": "TC-001",
                "title": "t",
                "feature_id": "FP-001",
                "requirement_id": "REQ-1",
                "steps": [{"step": "a", "result": "b"}],
            }
        ]
        matrix = build_coverage_matrix(features, cases)
        gaps = identify_coverage_gaps(matrix)
        assert any("FP-002" in g for g in gaps)
        assert matrix.covered_feature_count == 1
        assert matrix.feature_coverage_rate == pytest.approx(0.5)

    def test_uncovered_acceptance_criteria_detected(self):
        features = extract_feature_points(COMPLETE_PRD)
        cases = [{
            "id": "TC-001",
            "title": "不覆盖验收标准的用例",
            "feature_id": "FP-001",
            "steps": [{"step": "a", "result": "b"}],
        }]
        matrix = build_coverage_matrix(features, cases)
        report = render_coverage_report(matrix)
        assert report["uncovered_acceptance_criteria"]
        assert any("未覆盖验收标准" in g for g in report["gaps"])

    def test_acceptance_criteria_covered_by_id(self):
        features = extract_feature_points(COMPLETE_PRD)
        # COMPLETE_PRD 的验收标准位于「### FP-001 企业储值充值」章节内，编号为 FP-001-AC-01
        cases = [{
            "id": "TC-001",
            "title": "覆盖验收标准",
            "feature_id": features[0].feature_id,
            "acceptance_id": "FP-001-AC-01",
            "steps": [{"step": "a", "result": "b"}],
        }]
        matrix = build_coverage_matrix(features, cases)
        covered = matrix.features[0]
        assert covered.acceptance_total == 1
        assert covered.acceptance_covered == 1

    def test_acceptance_criteria_covered_by_text_fallback(self):
        """用例未填 acceptance_id 时，按验收标准原文兜底匹配。"""
        features = extract_feature_points(COMPLETE_PRD)
        cases = [{
            "id": "TC-001",
            "title": "充值成功后账户余额增加对应金额",
            "feature_id": features[0].feature_id,
            "requirement_id": "REQ-1",
            "steps": [{"step": "提交充值", "result": "账户余额增加对应金额，流水记录可查询"}],
        }]
        matrix = build_coverage_matrix(features, cases)
        assert matrix.features[0].acceptance_covered == 1

    def test_untraced_case_is_reported(self):
        features = extract_feature_points(COMPLETE_PRD)
        cases = [{
            "id": "TC-NO-FP",
            "title": "缺功能点用例",
            "requirement_id": "REQ-1",
            "steps": [{"step": "a", "result": "b"}],
        }]
        report = render_coverage_report(build_coverage_matrix(features, cases))
        assert "TC-NO-FP" in report["untraced_info"]["untraced_case_ids"]

    def test_legacy_cases_do_not_count_as_gaps(self):
        features = extract_feature_points(COMPLETE_PRD)
        cases = [{"id": "TC-OLD", "title": "旧用例", "steps": [{"step": "a", "result": "b"}]}]
        matrix = build_coverage_matrix(features, cases)
        assert matrix.legacy_case_ids == ["TC-OLD"]
        assert matrix.untraced_case_ids == []


class TestQueryByFeature:
    """可以查询某功能点对应的全部测试用例"""

    def test_query_returns_matching_cases(self):
        from app.test_platform.services import query_cases_by_feature

        cases = _build_cases()
        matched = query_cases_by_feature("FP-001", cases)
        assert [c["id"] for c in matched] == ["TC-001", "TC-002"]

    def test_query_unknown_feature_returns_empty(self):
        from app.test_platform.services import query_cases_by_feature

        assert query_cases_by_feature("FP-999", _build_cases()) == []


# ---------------------------------------------------------------------------
# 兼容测试
# ---------------------------------------------------------------------------

LEGACY_CASE = {
    "id": "TC-186482",
    "title": "B端储值免单活动新建入口冒烟",
    "module": "B端-活动管理",
    "keyword": "正向",
    "steps": [
        {"seq": 1, "action": "进入营销活动管理", "target": "活动管理菜单", "data": "无"},
        {"seq": 2, "action": "点击新建活动", "target": "新建按钮"},
    ],
    "expected_results": ["1. 打开新建活动页面", "2. 展示储值免单活动类型"],
}


class TestLegacyConversion:
    """旧格式用例可以转换"""

    def test_action_target_to_step_result(self):
        converted = convert_legacy_case(LEGACY_CASE)
        assert converted["steps"][0]["step"] == "进入营销活动管理 [活动管理菜单]"
        assert converted["steps"][0]["result"] == "1. 打开新建活动页面"
        assert converted["steps"][1]["result"] == "2. 展示储值免单活动类型"

    def test_input_dict_not_mutated(self):
        original = json.loads(json.dumps(LEGACY_CASE))
        convert_legacy_case(LEGACY_CASE)
        assert LEGACY_CASE == original

    def test_platform_format_is_preserved(self):
        case = {"id": "TC-1", "title": "t", "steps": [{"step": "a", "result": "b"}]}
        converted = convert_legacy_case(case)
        assert converted["steps"] == [{"step": "a", "result": "b"}]

    def test_attach_traceability(self):
        converted = attach_traceability(
            convert_legacy_case(LEGACY_CASE),
            feature_id="FP-001",
            requirement_id="REQ-1",
            source_location="req.md",
        )
        assert converted["feature_id"] == "FP-001"
        assert validate_case_traceability(converted) == []

    def test_batch_conversion_and_classification(self):
        cases = convert_legacy_cases([LEGACY_CASE, _build_cases()[0]])
        buckets = classify_cases(cases)
        assert buckets["legacy"] == ["TC-186482"]
        assert buckets["traced"] == ["TC-001"]

    def test_to_platform_case_helper(self):
        from app.test_platform.services import to_platform_case

        converted = to_platform_case(LEGACY_CASE, feature_id="FP-001", requirement_id="REQ-1")
        assert converted["steps"][0]["result"]
        assert converted["feature_id"] == "FP-001"


class TestLegacyExportCompatibility:
    """旧 Excel 导出不受影响（含旧格式与带追踪字段的新格式）"""

    @staticmethod
    def _run_export(monkeypatch, cases, sheet_name="测试用例"):
        """在沙箱禁止落盘的前提下，完整执行导出链路并捕获写入内容。"""
        from openpyxl import Workbook

        from app.agents.tools.testcase import testcase_tools

        saved: dict = {}

        def _capture(self, filename):  # noqa: ANN001
            saved["path"] = str(filename)
            saved["rows"] = [
                [cell.value for cell in row]
                for row in self.active.iter_rows()
            ]

        monkeypatch.setattr(Workbook, "save", _capture)
        path = testcase_tools._export_test_cases_to_excel_impl(
            cases, "export_probe.xlsx", sheet_name=sheet_name
        )
        return path, saved

    def test_excel_export_accepts_legacy_cases(self, monkeypatch):
        path, saved = self._run_export(monkeypatch, [LEGACY_CASE], sheet_name="旧用例")
        assert isinstance(path, str) and path
        assert saved["rows"][0][0] == "用例编号"
        # 旧格式步骤与预期结果仍被正确展开
        assert "进入营销活动管理" in str(saved["rows"][1][7])
        assert "打开新建活动页面" in str(saved["rows"][1][9])

    def test_excel_export_accepts_platform_cases_with_trace_fields(self, monkeypatch):
        case = attach_traceability(
            convert_legacy_case(_build_cases()[0]),
            feature_id="FP-001",
            requirement_id="REQ-1",
        )
        path, saved = self._run_export(monkeypatch, [case])
        assert isinstance(path, str) and path
        assert saved["rows"][1][0] == "TC-001"

    def test_excel_flatten_helpers_handle_both_formats(self):
        """旧格式 flatten 与新格式（step/result）共用同一导出映射，不受改造影响。"""
        from app.agents.tools.testcase.testcase_tools import _flatten_steps

        legacy_text = _flatten_steps(LEGACY_CASE["steps"])
        assert "进入营销活动管理" in legacy_text
        assert "活动管理菜单" in legacy_text

        new_text = _flatten_steps([{"step": "点击查询", "result": "展示结果"}])
        assert "点击查询" in new_text


class TestExistingQualityGatesStillApply:
    """现有质量门禁继续生效"""

    def test_fuzzy_word_still_rejected(self):
        from app.agents.tools.testcase.quality_gates import validate_staged_case

        problems, _ = validate_staged_case(
            {"id": "TC-1", "title": "t", "steps": [{"step": "点击保存", "result": "保存成功"}]}
        )
        assert any("模糊" in p for p in problems)

    def test_missing_result_still_rejected(self):
        from app.agents.tools.testcase.quality_gates import validate_staged_case

        problems, _ = validate_staged_case(
            {"id": "TC-1", "title": "t", "steps": [{"step": "点击保存"}]}
        )
        assert any("预期结果" in p for p in problems)

    def test_platform_contract_validation_unaffected(self):
        from app.test_platform.testcase_contract import validate_contract

        valid, problems = validate_contract(
            {"id": "TC-1", "title": "t", "steps": [{"step": "a", "result": "b"}]}
        )
        assert valid is True
        assert problems == []

    def test_contract_still_requires_id_and_title(self):
        from app.test_platform.testcase_contract import validate_contract

        valid, problems = validate_contract({"steps": [{"step": "a", "result": "b"}]})
        assert valid is False
        assert len(problems) >= 1


class TestIncrementalAppend:
    """增量追加不会产生重复编号"""

    def test_staging_dedupes_by_id(self):
        from app.agents.testcase import staging

        staging.clear("__test__")
        staging.add([{"id": "TC-001", "title": "第一批"}], "__test__")
        staging.add([{"id": "TC-001", "title": "修正后"}], "__test__")
        staging.add([{"id": "TC-002", "title": "第二批"}], "__test__")
        ids = staging.list_ids("__test__")
        assert ids == ["TC-001", "TC-002"]
        assert staging.list_all("__test__")[0]["title"] == "修正后"
        staging.clear("__test__")

    def test_case_contract_accepts_trace_fields_on_append(self):
        from app.test_platform.testcase_contract import TestCaseContract

        case = TestCaseContract.from_dict(
            attach_traceability(convert_legacy_case(LEGACY_CASE), feature_id="FP-001")
        )
        payload = case.to_dict()
        assert payload["feature_id"] == "FP-001"
        assert payload["steps"][0]["result"]


class TestGenerationServiceEndToEnd:
    """服务层：门控 → 生成 → 契约校验 → 覆盖追踪"""

    def test_complete_requirement_generates_and_validates(self):
        from app.test_platform.requirements.coverage import case_feature_id
        from app.test_platform.services import TestCaseGenerationService

        features = extract_feature_points(COMPLETE_PRD)
        feature_id = features[0].feature_id

        def generator(feature_points, analysis):  # noqa: ANN001
            return [
                attach_traceability(
                    {
                        "id": "TC-9001",
                        "title": "企业储值充值-正向",
                        "steps": [{"step": "输入手机号与金额", "result": "页面提示已保存并生成流水"}],
                    },
                    feature_id=feature_points[0].feature_id,
                    requirement_id="REQ-1",
                    source_location="req.md",
                )
            ]

        outcome = TestCaseGenerationService().generate(
            COMPLETE_PRD, requirement_id="REQ-1", generator=generator
        )
        payload = outcome.to_dict()
        assert payload["testcase_generation"]["status"] == "generated"
        assert payload["testcase_generation"]["created_count"] == 1
        assert payload["contract_validation"]["passed"] is True
        assert payload["coverage"]["covered_feature_count"] >= 1
        assert case_feature_id(payload["cases"][0]) == feature_id

    def test_blocked_requirement_never_calls_generator(self):
        from app.test_platform.services import TestCaseGenerationService

        called = {"n": 0}

        def generator(feature_points, analysis):  # noqa: ANN001
            called["n"] += 1
            return [{"id": "TC-1", "title": "不应生成"}]

        outcome = TestCaseGenerationService().generate(BLOCKED_PRD_NO_GOAL, generator=generator)
        assert called["n"] == 0
        assert outcome.blocked is True
        assert outcome.cases == []
        assert outcome.to_dict()["testcase_generation"]["status"] == "blocked"

    def test_untraceable_generated_case_is_flagged(self):
        from app.test_platform.services import TestCaseGenerationService

        def generator(feature_points, analysis):  # noqa: ANN001
            return [{"id": "TC-X", "title": "无追踪字段",
                     "steps": [{"step": "a", "result": "b"}]}]

        outcome = TestCaseGenerationService().generate(COMPLETE_PRD, generator=generator)
        assert outcome.traceability_problems
        assert "feature_id" in " ".join(outcome.traceability_problems[0]["problems"])


class TestAnalysisGapKinds:
    """检查项类型可解释"""

    def test_conditional_check_reports_missing_formula(self):
        text = COMPLETE_PRD.replace(
            "计算公式：充值后余额 = 原余额 + 充值金额，精度保留两位小数（四舍五入）。", ""
        )
        result = analyze_requirement_text(text)
        assert any(
            g.item == "计算公式与精度" and g.kind == GapCheckKind.CONDITIONAL_MISSING
            for g in result.checks
        )

    def test_conditional_check_skipped_when_not_involved(self):
        text = """# 简单需求
## 功能点名称
查询功能
业务目标：查询数据。
前置条件：已登录。
操作流程：点击查询。
输入与数据规则：关键字必填。
业务规则：无。
正常结果：展示结果。
验收标准：可查到结果。
"""
        result = analyze_requirement_text(text)
        conditional = [
            g for g in result.checks
            if g.item == "并发与幂等" and g.kind == GapCheckKind.FLAGGED
        ]
        assert conditional

    def test_flagged_items_do_not_block(self):
        result = analyze_requirement_text(COMPLETE_PRD + "\n补充：待确认项无\n")
        # 歧义项出现时不改变 Complete 判定为 Blocked；只进入 ambiguous_items
        assert result.status in (PRDQualityStatus.COMPLETE, PRDQualityStatus.INCOMPLETE)
        assert all(g.kind == GapCheckKind.FLAGGED for g in result.flagged_items)


class TestApiContractPayload:
    """接口响应字段契约"""

    def test_gated_payload_has_required_fields(self):
        payload = PRDAnalysisResult.from_dict(
            analyze_requirement_text(BLOCKED_PRD_NO_GOAL).to_dict()
        ).to_dict()
        assert payload["status"] in {"Complete", "Incomplete", "Blocked"}
        assert payload["can_generate_testcases"] is False
        assert payload["testcase_generation_status"] == (
            TestCaseGenerationStatus.NOT_STARTED.value
        )

    def test_generate_response_status_enum_values(self):
        allowed = {"not_started", "blocked", "generated", "rejected"}
        blocked = generate_testcases_with_gate(BLOCKED_PRD_NO_GOAL)
        assert blocked["testcase_generation"]["status"] in allowed
        ready = generate_testcases_with_gate(COMPLETE_PRD)
        assert ready["testcase_generation"]["status"] in allowed
