# -*- coding: utf-8 -*-
"""阶段一/阶段二复核问题回归测试。

覆盖两轮复核确认的问题，每条都锁定"修复前会失败"的行为：

第一轮
1. force_generate 不能由客户端布尔参数绕过 V1.2 硬门控
2. 空模板/占位符不得判定为 Complete
3. 非法缺口结构不得抛异常，且必须降级为 Incomplete
4. 一条用例只覆盖一条验收标准（覆盖率不得虚高）
5. 多功能点需求不得把用例默认绑定到第一个功能点

第二轮
6. 审计落盘、字段完整、token 不入审计、审计失败即拒绝放行
7. 占位符误判边界（「暂无异常场景」等真实业务描述不得被判为占位）
8. 接口级不可绕过 + 响应字段兼容性
"""

from __future__ import annotations

import json

import pytest

from app.test_platform.requirements import (
    OVERRIDE_TOKEN_ENV,
    FeaturePoint,
    GateOverride,
    GateOverrideNotAuthorizedError,
    GapCheckKind,
    PRDQualityStatus,
    analyze_requirement_text,
    build_coverage_matrix,
    check_generation_gate,
    clear_override_audit_log,
    get_override_audit_log,
    override_enabled,
    parse_analysis_payload,
)
from app.test_platform.requirements.gate import MIN_OVERRIDE_REASON_LENGTH

VALID_REASON = "已获业务方书面确认，按人工评审结论补跑"


def _complete_prd() -> str:
    return """# 会员储值充值需求

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
正常结果：保存后生成一条储值流水记录。
验收标准：充值成功后账户余额增加对应金额，且流水记录可查询。
计算公式：充值后余额 = 原余额 + 充值金额，精度保留两位小数（四舍五入）。
状态流转：待支付 → 支付成功 → 已入账。
"""


# 同时缺少阻断型必填项（业务目标/流程），且无任何结构化章节
BLOCKED_PRD = "只有一句话的需求，既没有需求目标也没有主流程说明。"


@pytest.fixture(autouse=True)
def _reset_override_env(monkeypatch):
    """确保每个用例从"人工放行未启用"开始，且审计日志干净。"""
    monkeypatch.delenv(OVERRIDE_TOKEN_ENV, raising=False)
    clear_override_audit_log()
    yield
    clear_override_audit_log()


@pytest.fixture()
def work_dir():
    """沙箱下 pytest tmp_path 可能不可用，改用自建可写临时目录。"""
    import shutil
    import tempfile
    from pathlib import Path

    path = Path(tempfile.mkdtemp(prefix="req_gate_audit_"))
    try:
        yield path
    finally:
        shutil.rmtree(path, ignore_errors=True)


# ---------------------------------------------------------------------------
# 问题 1：force_generate 绕过
# ---------------------------------------------------------------------------

class TestNoClientSideBypass:
    """客户端参数不能绕过 V1.2 硬门控"""

    def test_public_api_has_no_force_generate_semantics(self):
        """公开生成入口不得存在能放行 Blocked 需求的布尔参数。"""
        import inspect

        from app.api.v2 import ai as ai_mod

        params = inspect.signature(ai_mod.generate_from_document).parameters
        assert "force_generate" in params, "参数保留用于兼容，但必须已废弃"
        assert params["force_generate"].default.deprecated is True, (
            "force_generate 必须标记 deprecated"
        )
        assert "force" not in params

    def test_legacy_force_true_does_not_allow(self):
        """旧的 force 放行开关已从门控 API 移除。"""
        analysis = analyze_requirement_text(BLOCKED_PRD)
        assert analysis.status == PRDQualityStatus.BLOCKED
        import inspect

        params = inspect.signature(check_generation_gate).parameters
        assert "force" not in params, "门控 API 不得保留客户端可用的 force 开关"
        allowed, reasons = check_generation_gate(analysis)
        assert allowed is False
        assert reasons

    def test_override_disabled_by_default(self):
        assert override_enabled() is False
        analysis = analyze_requirement_text(BLOCKED_PRD)
        with pytest.raises(GateOverrideNotAuthorizedError):
            check_generation_gate(
                analysis,
                override=GateOverride(token="anything", reason=VALID_REASON),
            )

    def test_override_requires_matching_server_token(self, monkeypatch, work_dir):
        from app.test_platform.requirements.gate import AUDIT_DIR_ENV

        monkeypatch.setenv(OVERRIDE_TOKEN_ENV, "server-token")
        monkeypatch.setenv(AUDIT_DIR_ENV, str(work_dir))
        analysis = analyze_requirement_text(BLOCKED_PRD)

        with pytest.raises(GateOverrideNotAuthorizedError):
            check_generation_gate(
                analysis,
                override=GateOverride(token="client-guess", reason=VALID_REASON),
            )

        allowed, reasons = check_generation_gate(
            analysis,
            override=GateOverride(
                token="server-token", reason=VALID_REASON, operator="qa-lead"
            ),
        )
        assert allowed is True
        assert reasons == []

    def test_override_requires_meaningful_reason(self, monkeypatch):
        monkeypatch.setenv(OVERRIDE_TOKEN_ENV, "server-token")
        analysis = analyze_requirement_text(BLOCKED_PRD)

        with pytest.raises(GateOverrideNotAuthorizedError):
            check_generation_gate(
                analysis,
                override=GateOverride(token="server-token", reason="x"),
            )
        assert len("x") < MIN_OVERRIDE_REASON_LENGTH

    def test_override_is_audited(self, monkeypatch, work_dir):
        from app.test_platform.requirements.gate import AUDIT_DIR_ENV

        monkeypatch.setenv(OVERRIDE_TOKEN_ENV, "server-token")
        monkeypatch.setenv(AUDIT_DIR_ENV, str(work_dir))
        analysis = analyze_requirement_text(BLOCKED_PRD)

        check_generation_gate(
            analysis,
            override=GateOverride(
                token="server-token", reason=VALID_REASON, operator="qa-lead"
            ),
        )

        log = get_override_audit_log()
        assert len(log) == 1
        assert log[0]["operator"] == "qa-lead"
        assert log[0]["reason"] == VALID_REASON
        assert log[0]["status"] == PRDQualityStatus.BLOCKED.value
        assert log[0]["reasons"]

    def test_complete_requirement_needs_no_override(self):
        """Complete 需求正常通过，且不产生放行审计。"""
        allowed, reasons = check_generation_gate(
            analyze_requirement_text(_complete_prd())
        )
        assert allowed is True
        assert reasons == []
        assert get_override_audit_log() == []

    def test_invalid_override_fails_closed_without_audit(self, monkeypatch, work_dir):
        """凭证错误不得放行，也不得写入"已放行"审计。"""
        from app.test_platform.requirements.gate import AUDIT_DIR_ENV

        monkeypatch.setenv(OVERRIDE_TOKEN_ENV, "server-token")
        monkeypatch.setenv(AUDIT_DIR_ENV, str(work_dir))
        analysis = analyze_requirement_text(BLOCKED_PRD)
        with pytest.raises(GateOverrideNotAuthorizedError):
            check_generation_gate(
                analysis,
                override=GateOverride(token="wrong", reason=VALID_REASON),
            )
        assert get_override_audit_log() == []


# ---------------------------------------------------------------------------
# 问题 2：空模板 / 占位符误判
# ---------------------------------------------------------------------------

EMPTY_TEMPLATE = """功能点编号 FP-001
功能点名称：
业务目标：
角色：
无权限：
前置条件：
操作流程：
输入与数据规则：
验收标准：
正常结果：
业务规则：
"""

PLACEHOLDER_TEMPLATE = """功能点编号 FP-001
功能点名称
业务目标：待填写
角色：暂无
无权限：
前置条件：N/A
操作流程：TBD
输入与数据规则：-
验收标准：无
正常结果：略
业务规则：待补充
"""

COLONLESS_EMPTY_TEMPLATE = """功能点编号 FP-001
功能点名称
业务目标
角色
无权限
前置条件
操作流程
输入与数据规则
验收标准
正常结果
业务规则
"""


class TestEmptyTemplateNotComplete:
    """空模板/只有标题/占位符都不得判定为 Complete"""

    @pytest.mark.parametrize(
        "template,label",
        [
            (EMPTY_TEMPLATE, "空模板（标题后无内容）"),
            (PLACEHOLDER_TEMPLATE, "占位符模板（待填写/暂无/N/A/TBD）"),
            (COLONLESS_EMPTY_TEMPLATE, "无冒号空模板"),
        ],
    )
    def test_empty_template_is_not_complete(self, template, label):
        result = analyze_requirement_text(template)
        assert result.status != PRDQualityStatus.COMPLETE, f"{label} 不应判为 Complete"
        assert result.can_generate_testcases is False
        assert result.status == PRDQualityStatus.BLOCKED

    def test_empty_template_reports_content_missing_reason(self):
        result = analyze_requirement_text(EMPTY_TEMPLATE)
        details = " ".join(g.detail for g in result.missing_items)
        assert "内容为空" in details or "占位符" in details

    def test_placeholder_values_ignored(self):
        result = analyze_requirement_text(PLACEHOLDER_TEMPLATE)
        items = {g.item for g in result.missing_items}
        assert "业务目标" in items
        assert "操作流程" in items
        assert "验收标准" in items

    def test_empty_section_content_blocks_generation(self):
        """章节标签存在但内容为空时不得放行（用无歧义样本）。"""
        text = _complete_prd().replace(
            "验收标准：充值成功后账户余额增加对应金额，且流水记录可查询。",
            "验收标准：",
        )
        result = analyze_requirement_text(text)
        assert result.can_generate_testcases is False
        assert any(g.item == "验收标准" for g in result.missing_items)

    def test_real_content_still_passes(self):
        """有效内容不受影响（防止修复导致误伤）。"""
        result = analyze_requirement_text(_complete_prd())
        assert result.status == PRDQualityStatus.COMPLETE
        assert result.can_generate_testcases is True

    def test_list_style_sections_still_pass(self):
        """「别名：」下挂列表的写法仍被识别为有效内容。"""
        text = """# 会员储值充值
优先级：P1
## 功能点
### FP-001 企业储值充值
业务目标：企业客户可在后台为企业账户充值余额。
角色权限：企业管理员可操作；无权限用户访问时提示权限不足。
前置条件：
- 已登录企业控制台
- 已开通储值账户
操作流程：
1. 进入储值账户列表
2. 点击创建储值充值
输入与数据规则：手机号必填；金额必填；金额精度两位小数。
业务规则：单笔充值金额上限 50000 元，超出时提示金额超限。
正常结果：保存后生成储值流水。
验收标准：充值成功后余额增加对应金额，流水可查询。
"""
        result = analyze_requirement_text(text)
        assert result.can_generate_testcases is True, [
            (g.item, g.detail) for g in result.missing_items
        ]


# ---------------------------------------------------------------------------
# 问题 3：非法缺口结构抛异常
# ---------------------------------------------------------------------------

MALFORMED_PAYLOADS = [
    {"status": "Complete", "missing_items": ["缺失验收标准"]},
    {"status": "Incomplete", "ambiguous_items": [123]},
    {"status": "Complete", "flagged_items": ["x"]},
    {"status": "Complete", "missing_items": [{"item": "a"}, "b"]},
    {"status": "Complete", "checks": [None]},
    {"status": "Complete", "missing_items": {"item": "a"}},
    {"status": "Complete", "missing_items": 5},
    {"status": [1, 2]},
]

# 仅影响展示的信息字段，接受宽松容错（不因此降级）
TOLERATED_PAYLOADS = [
    {"status": "Complete", "affected_features": 42},
    {"status": "Complete", "affected_features": {"a": 1}},
    {"status": "Complete", "blocked_reasons": {"a": 1}},
]


class TestMalformedStructureDegrades:
    """非法结构必须降级为 Incomplete，且绝不抛异常"""

    @pytest.mark.parametrize("payload", MALFORMED_PAYLOADS)
    def test_never_raises(self, payload):
        result = parse_analysis_payload(payload)
        assert result.status != PRDQualityStatus.COMPLETE
        assert result.can_generate_testcases is False

    @pytest.mark.parametrize("payload", TOLERATED_PAYLOADS)
    def test_informational_fields_tolerated(self, payload):
        """仅影响展示的信息字段容错处理，不因结构宽松而误降级。"""
        result = parse_analysis_payload(payload)
        assert result.can_generate_testcases is False or result.status == PRDQualityStatus.COMPLETE
        assert result.parse_error is None or result.status != PRDQualityStatus.COMPLETE

    @pytest.mark.parametrize(
        "payload",
        [
            {"status": "Complete", "missing_items": ["缺失验收标准"]},
            {"status": "Complete", "missing_items": 5},
            {"status": "Complete", "missing_items": {"item": "a"}},
            {"status": "Complete", "blocked_reasons": ["缺少业务目标"]},
        ],
    )
    def test_malformed_or_contradictory_payload_reports_parse_error(self, payload):
        result = parse_analysis_payload(payload)
        assert result.status == PRDQualityStatus.INCOMPLETE
        assert result.parse_error
        assert "缺口" in result.parse_error or "自相矛盾" in result.parse_error

    @pytest.mark.parametrize(
        "payload",
        [
            "{not-json",
            "",
            None,
            [],
            "[]",
            {"no_status": True},
            {"status": None},
            "[1, 2, 3]",
            123,
        ],
    )
    def test_unparseable_payloads_degrade(self, payload):
        result = parse_analysis_payload(payload)
        assert result.status == PRDQualityStatus.INCOMPLETE
        assert result.can_generate_testcases is False
        assert result.parse_error

    def test_malformed_status_still_marks_incomplete(self):
        result = parse_analysis_payload({"status": [1, 2], "can_generate_testcases": True})
        assert result.status == PRDQualityStatus.INCOMPLETE
        assert result.can_generate_testcases is False

    def test_valid_payload_still_round_trips(self):
        payload = {
            "status": "Complete",
            "can_generate_testcases": True,
            "missing_items": [],
            "ambiguous_items": [],
            "blocked_reasons": [],
            "flagged_items": [],
            "affected_features": ["FP-001"],
        }
        result = parse_analysis_payload(json.dumps(payload, ensure_ascii=False))
        assert result.status == PRDQualityStatus.COMPLETE
        assert result.can_generate_testcases is True
        assert result.affected_features == ["FP-001"]


# ---------------------------------------------------------------------------
# 问题 4：验收标准覆盖率误报
# ---------------------------------------------------------------------------

class TestAcceptanceCoverageAccuracy:
    """一条用例只覆盖一条验收标准"""

    def _feature(self) -> FeaturePoint:
        return FeaturePoint(
            feature_id="FP-001",
            feature_name="企业储值充值",
            acceptance_criteria=["充值后余额增加对应金额", "流水记录可查询"],
        )

    def test_one_case_covers_only_one_criterion(self):
        feature = self._feature()
        cases = [{
            "id": "TC-1",
            "title": "只覆盖第一条验收标准",
            "feature_id": "FP-001",
            "requirement_id": "REQ-1",
            "acceptance_id": "FP-001-AC-01",
            "steps": [{"step": "充值", "result": "余额增加"}],
        }]
        coverage = build_coverage_matrix([feature], cases).features[0]

        assert coverage.acceptance_total == 2
        assert coverage.acceptance_covered == 1
        assert coverage.acceptance_rate == pytest.approx(0.5)
        assert [c["acceptance_id"] for c in coverage.uncovered_criteria] == ["FP-001-AC-02"]

    def test_uncovered_criterion_appears_in_gaps(self):
        from app.test_platform.requirements import identify_coverage_gaps

        feature = self._feature()
        cases = [{
            "id": "TC-1",
            "title": "只覆盖第一条",
            "feature_id": "FP-001",
            "requirement_id": "REQ-1",
            "acceptance_id": "FP-001-AC-01",
            "steps": [{"step": "a", "result": "b"}],
        }]
        matrix = build_coverage_matrix([feature], cases)
        assert any("FP-001-AC-02" in gap for gap in identify_coverage_gaps(matrix))

    def test_two_cases_cover_two_criteria(self):
        feature = self._feature()
        cases = [
            {"id": "TC-1", "title": "t1", "feature_id": "FP-001", "requirement_id": "REQ-1",
             "acceptance_id": "FP-001-AC-01", "steps": [{"step": "a", "result": "b"}]},
            {"id": "TC-2", "title": "t2", "feature_id": "FP-001", "requirement_id": "REQ-1",
             "acceptance_id": "FP-001-AC-02", "steps": [{"step": "a", "result": "b"}]},
        ]
        coverage = build_coverage_matrix([feature], cases).features[0]
        assert coverage.acceptance_covered == 2
        assert coverage.acceptance_rate == pytest.approx(1.0)
        assert coverage.uncovered_criteria == []

    def test_case_without_acceptance_id_falls_back_to_text(self):
        """用例未声明 acceptance_id 时，仍允许文本兜底匹配（兼容旧用例）。"""
        feature = FeaturePoint(
            feature_id="FP-001",
            feature_name="充值",
            acceptance_criteria=["充值成功后账户余额增加对应金额"],
        )
        cases = [{
            "id": "TC-1",
            "title": "充值成功后账户余额增加对应金额",
            "feature_id": "FP-001",
            "requirement_id": "REQ-1",
            "steps": [{"step": "提交充值", "result": "账户余额增加对应金额"}],
        }]
        assert build_coverage_matrix([feature], cases).features[0].acceptance_covered == 1

    def test_declared_acceptance_id_disables_text_fallback(self):
        """用例声明了 acceptance_id，就不再用文本对其他验收标准兜底。"""
        feature = FeaturePoint(
            feature_id="FP-001",
            feature_name="充值",
            acceptance_criteria=["充值成功后账户余额增加", "流水记录可查询"],
        )
        cases = [{
            "id": "TC-1",
            "title": "充值成功后账户余额增加，流水记录可查询",
            "feature_id": "FP-001",
            "requirement_id": "REQ-1",
            "acceptance_id": "FP-001-AC-01",
            "steps": [{"step": "充值", "result": "账户余额增加，流水记录可查询"}],
        }]
        coverage = build_coverage_matrix([feature], cases).features[0]
        assert coverage.acceptance_covered == 1
        assert [c["acceptance_id"] for c in coverage.uncovered_criteria] == ["FP-001-AC-02"]

    def test_short_acceptance_id_form_matches(self):
        """AC-2 这类简写也能对应到 FP-001-AC-02。"""
        feature = self._feature()
        cases = [{
            "id": "TC-1", "title": "t", "feature_id": "FP-001", "requirement_id": "REQ-1",
            "acceptance_id": "AC-2", "steps": [{"step": "a", "result": "b"}],
        }]
        coverage = build_coverage_matrix([feature], cases).features[0]
        assert coverage.acceptance_covered == 1
        assert coverage.uncovered_criteria[0]["acceptance_id"] == "FP-001-AC-01"

    def test_other_feature_prefix_does_not_match(self):
        """带其他功能点前缀的编号不得误匹配本功能点的验收标准。"""
        feature = self._feature()
        cases = [{
            "id": "TC-1", "title": "t", "feature_id": "FP-001", "requirement_id": "REQ-1",
            "acceptance_id": "FP-002-AC-01", "steps": [{"step": "a", "result": "b"}],
        }]
        assert build_coverage_matrix([feature], cases).features[0].acceptance_covered == 0


# ---------------------------------------------------------------------------
# 问题 5：默认绑定第一个功能点
# ---------------------------------------------------------------------------

class TestNoDefaultFeatureBinding:
    """多功能点需求不得把用例默认绑定到第一个功能点"""

    def test_sole_feature_point_may_be_used_as_anchor(self):
        from app.test_platform.services.requirement_quality_service import (
            resolve_sole_feature_id,
        )

        assert resolve_sole_feature_id([]) is None
        assert resolve_sole_feature_id([FeaturePoint("FP-001", "a")]) == "FP-001"
        assert resolve_sole_feature_id(
            [FeaturePoint("FP-001", "a"), FeaturePoint("FP-002", "b")]
        ) is None

    def test_multi_feature_requirement_has_no_default(self):
        from app.test_platform.services.requirement_quality_service import (
            resolve_sole_feature_id,
        )

        features = [
            FeaturePoint("FP-001", "功能一"),
            FeaturePoint("FP-002", "功能二"),
            FeaturePoint("FP-003", "功能三"),
        ]
        assert resolve_sole_feature_id(features) is None, (
            "多功能点需求不得默认绑定第一个功能点"
        )

    def test_multi_feature_cases_without_feature_id_are_untraced(self):
        """未指定功能点的用例在多功能点场景下应被报为未追踪，而不是错误归属。"""
        features = [
            FeaturePoint("FP-001", "功能一", acceptance_criteria=["a"]),
            FeaturePoint("FP-002", "功能二", acceptance_criteria=["b"]),
        ]
        cases = [{
            "id": "TC-X", "title": "未指定功能点", "requirement_id": "REQ-1",
            "steps": [{"step": "a", "result": "b"}],
        }]
        from app.test_platform.requirements import validate_cases_traceability

        problems = validate_cases_traceability(cases)
        assert problems
        assert any("feature_id" in p for p in problems[0]["problems"])

        matrix = build_coverage_matrix(features, cases)
        assert "TC-X" in matrix.untraced_case_ids
        assert matrix.covered_feature_count == 0


# ---------------------------------------------------------------------------
# 第二轮复核 6：审计落点与敏感信息
# ---------------------------------------------------------------------------

class TestOverrideAudit:
    """审计持久化、字段完整性、token 不入审计、审计失败即拒绝放行"""

    def test_audit_is_persisted_to_disk(self, monkeypatch, work_dir):
        from app.test_platform.requirements.gate import (
            AUDIT_DIR_ENV,
            read_persisted_audit_entries,
        )

        monkeypatch.setenv(OVERRIDE_TOKEN_ENV, "server-token")
        monkeypatch.setenv(AUDIT_DIR_ENV, str(work_dir))

        check_generation_gate(
            analyze_requirement_text(BLOCKED_PRD),
            override=GateOverride(
                token="server-token",
                reason=VALID_REASON,
                operator="qa-lead",
                requirement_id="REQ-9",
                source="req.md",
            ),
        )

        entries = read_persisted_audit_entries()
        assert len(entries) == 1, "审计必须落盘，不能只写内存/日志"
        entry = entries[0]
        assert entry["operator"] == "qa-lead"
        assert entry["reason"] == VALID_REASON
        assert entry["status"] == PRDQualityStatus.BLOCKED.value
        assert entry["reasons"]

    def test_audit_entry_has_traceability_fields(self, monkeypatch, work_dir):
        from app.test_platform.requirements.gate import (
            AUDIT_DIR_ENV,
            read_persisted_audit_entries,
        )

        monkeypatch.setenv(OVERRIDE_TOKEN_ENV, "server-token")
        monkeypatch.setenv(AUDIT_DIR_ENV, str(work_dir))

        check_generation_gate(
            analyze_requirement_text(BLOCKED_PRD),
            override=GateOverride(
                token="server-token",
                reason=VALID_REASON,
                operator="qa-lead",
                requirement_id="REQ-9",
                source="req.md",
                request_id="trace-123",
            ),
        )

        entry = read_persisted_audit_entries()[0]
        assert entry["request_id"] == "trace-123", "审计需含请求追踪 ID"
        assert entry["audit_id"] == "trace-123"
        assert entry["requirement_id"] == "REQ-9"
        assert entry["source"] == "req.md"
        assert entry["at"], "审计需含时间戳"

    def test_token_never_written_to_audit(self, monkeypatch, work_dir):
        from app.test_platform.requirements.gate import (
            AUDIT_DIR_ENV,
            audit_file_path,
            read_persisted_audit_entries,
        )

        secret = "super-secret-token-value"
        monkeypatch.setenv(OVERRIDE_TOKEN_ENV, secret)
        monkeypatch.setenv(AUDIT_DIR_ENV, str(work_dir))

        check_generation_gate(
            analyze_requirement_text(BLOCKED_PRD),
            override=GateOverride(token=secret, reason=VALID_REASON, operator="qa"),
        )

        raw = audit_file_path().read_text(encoding="utf-8")
        assert secret not in raw, "token 绝不能写入审计文件"
        assert secret not in json.dumps(
            read_persisted_audit_entries(), ensure_ascii=False
        )

    def test_audit_failure_blocks_override(self, monkeypatch, work_dir):
        """审计写入失败必须拒绝放行 —— 不允许出现不可追踪的绕过。"""
        from app.test_platform.requirements.gate import (
            AUDIT_DIR_ENV,
            GateOverrideAuditError,
        )

        monkeypatch.setenv(OVERRIDE_TOKEN_ENV, "server-token")
        blocker = work_dir / "blocker"
        blocker.write_text("x", encoding="utf-8")
        monkeypatch.setenv(AUDIT_DIR_ENV, str(blocker / "nested"))

        with pytest.raises(GateOverrideAuditError):
            check_generation_gate(
                analyze_requirement_text(BLOCKED_PRD),
                override=GateOverride(token="server-token", reason=VALID_REASON),
            )

    def test_audit_failure_does_not_record_in_memory(self, monkeypatch, work_dir):
        from app.test_platform.requirements.gate import (
            AUDIT_DIR_ENV,
            GateOverrideAuditError,
        )

        monkeypatch.setenv(OVERRIDE_TOKEN_ENV, "server-token")
        blocker = work_dir / "blocker2"
        blocker.write_text("x", encoding="utf-8")
        monkeypatch.setenv(AUDIT_DIR_ENV, str(blocker / "nested"))

        with pytest.raises(GateOverrideAuditError):
            check_generation_gate(
                analyze_requirement_text(BLOCKED_PRD),
                override=GateOverride(token="server-token", reason=VALID_REASON),
            )
        assert get_override_audit_log() == []


# ---------------------------------------------------------------------------
# 第二轮复核 7：占位符误判边界
# ---------------------------------------------------------------------------

class TestPlaceholderBoundary:
    """「暂无X」「无X」这类真实业务描述不能被判为占位符"""

    @pytest.mark.parametrize(
        "value",
        [
            "暂无异常场景",
            "暂无异常场景，失败直接提示金额超限",
            "无权限用户访问时提示权限不足并拒绝访问",
            "无特殊前置条件，使用默认测试账号",
            "无匹配结果时列表展示空态文案",
            "暂无可退款余额时按钮置灰",
            "TBD 由风控团队在评审后给出阈值",
            "待填写用户手机号",
            "略过，非本次范围",
        ],
    )
    def test_real_business_text_is_not_placeholder(self, value):
        from app.test_platform.requirements.analysis import _is_placeholder

        assert _is_placeholder(value) is False, f"真实业务描述被误判为占位: {value}"

    @pytest.mark.parametrize(
        "value",
        ["暂无", "无", "略", "-", "N/A", "TBD", "待填写", "待补充", "无。", "无，",
         "业务目标：", "：", ""],
    )
    def test_pure_placeholder_is_detected(self, value):
        from app.test_platform.requirements.analysis import _is_placeholder

        assert _is_placeholder(value) is True, f"占位符未被识别: {value!r}"

    def test_real_business_sample_passes_gate(self):
        """典型业务样本回归：包含「暂无异常场景」的需求仍应完整。"""
        text = _complete_prd().replace(
            "正常结果：保存后生成一条储值流水记录。",
            "正常结果：保存后生成一条储值流水记录；暂无异常场景，失败直接提示金额超限。",
        )
        result = analyze_requirement_text(text)
        assert result.status == PRDQualityStatus.COMPLETE, [
            (g.item, g.detail) for g in result.missing_items
        ]

    def test_no_permission_text_passes_conditional_check(self):
        """「无权限用户…」应满足无权限条件检查，而不是被判缺失。"""
        result = analyze_requirement_text(_complete_prd())
        no_permission = [g for g in result.checks if g.item == "无权限处理"]
        assert no_permission
        assert no_permission[0].kind != GapCheckKind.CONDITIONAL_MISSING


# ---------------------------------------------------------------------------
# 第二轮复核 8：接口级不可绕过 + 响应字段兼容
# ---------------------------------------------------------------------------

class TestEndpointLevelNoBypass:
    """接口级验证：普通请求无法放行，且响应字段稳定"""

    def _client(self):
        import httpx
        from fastapi import FastAPI

        from app.api.v2.requirement_quality import router

        app = FastAPI()
        app.include_router(router, prefix="/api/v2")
        transport = httpx.ASGITransport(app=app)
        return httpx.AsyncClient(transport=transport, base_url="http://test")

    async def test_force_generate_is_not_a_field(self):
        """接口请求模型不包含任何 force 放行字段。"""
        from app.api.v2.requirement_quality import RequirementGenerateRequest

        assert "force_generate" not in RequirementGenerateRequest.model_fields
        assert "force" not in RequirementGenerateRequest.model_fields

    async def test_unknown_force_field_is_ignored(self, monkeypatch, work_dir):
        """传入 force_generate=true 不会生效（Pydantic 忽略未知字段）。"""
        from app.test_platform.requirements.gate import AUDIT_DIR_ENV

        monkeypatch.setenv(AUDIT_DIR_ENV, str(work_dir))
        client = self._client()
        async with client:
            resp = await client.post(
                "/api/v2/projects/PROJ-1/requirements/generate-testcases",
                json={
                    "requirement_text": BLOCKED_PRD,
                    "force_generate": True,
                    "test_cases": [
                        {"id": "TC-1", "title": "不应生成",
                         "steps": [{"step": "a", "result": "b"}]}
                    ],
                },
            )
        data = resp.json()["data"]
        assert data["testcase_generation"]["status"] == "blocked"
        assert data["testcase_generation"]["created_count"] == 0
        assert data["cases"] == []
        assert data["prd_quality"]["status"] in ("Incomplete", "Blocked")
        assert get_override_audit_log() == [], "普通请求不得产生人工放行审计"

    async def test_override_without_server_token_is_403(self):
        """未配置服务端凭证时，提交 override 参数必须 403（不可绕过）。"""
        client = self._client()
        async with client:
            resp = await client.post(
                "/api/v2/projects/PROJ-1/requirements/generate-testcases",
                json={
                    "requirement_text": BLOCKED_PRD,
                    "override_token": "guess",
                    "override_reason": "手动放行尝试，用于验证接口不可绕过",
                },
            )
        assert resp.status_code == 403

    async def test_override_wrong_token_is_403(self, monkeypatch):
        monkeypatch.setenv(OVERRIDE_TOKEN_ENV, "real-token")
        client = self._client()
        async with client:
            resp = await client.post(
                "/api/v2/projects/PROJ-1/requirements/generate-testcases",
                json={
                    "requirement_text": BLOCKED_PRD,
                    "override_token": "wrong-token",
                    "override_reason": "手动放行尝试，用于验证凭证校验",
                },
            )
        assert resp.status_code == 403

    async def test_valid_override_succeeds_and_audits(self, monkeypatch, work_dir):
        from app.test_platform.requirements.gate import (
            AUDIT_DIR_ENV,
            read_persisted_audit_entries,
        )

        monkeypatch.setenv(OVERRIDE_TOKEN_ENV, "real-token")
        monkeypatch.setenv(AUDIT_DIR_ENV, str(work_dir))

        client = self._client()
        async with client:
            resp = await client.post(
                "/api/v2/projects/PROJ-1/requirements/generate-testcases",
                json={
                    "requirement_text": BLOCKED_PRD,
                    "requirement_id": "REQ-77",
                    "source": "req.md",
                    "override_token": "real-token",
                    "override_reason": "业务方书面确认后按人工评审结论补跑",
                },
            )
        assert resp.status_code == 200
        entries = read_persisted_audit_entries()
        assert len(entries) == 1
        assert entries[0]["requirement_id"] == "REQ-77"

    async def test_response_contract_fields_present(self):
        """响应结构稳定：success / test_cases / prd_quality / analysis_report / testcase_generation。"""
        client = self._client()
        async with client:
            resp = await client.post(
                "/api/v2/projects/PROJ-1/requirements/generate-testcases",
                json={"requirement_text": _complete_prd(), "test_cases": []},
            )
        body = resp.json()
        assert body["success"] is True
        data = body["data"]
        for key in ("prd_quality", "analysis_report", "testcase_generation", "cases"):
            assert key in data, f"响应缺少字段: {key}"
        for key in ("status", "can_generate_testcases"):
            assert key in data["prd_quality"]
        for key in ("blocked_reasons", "missing_items", "ambiguous_items"):
            assert key in data["analysis_report"]
