# -*- coding: utf-8 -*-
"""API 覆盖度评审（api_coverage_review）单元测试。

覆盖点：
1. 平台规则自洽（权重合计 100、非法权重被拒）；
2. 契约面解析（状态码/参数/枚举/类型/请求体/鉴权，含 $ref 与缺失数据的未知态）；
3. 未知 ≠ 未覆盖：unknown / not_applicable 从分母剔除并重归一化；
4. 扣分不重复计算：单维度扣分恒 ≤ 该维度满分；
5. 非法覆盖声明被确定性拦截且不计分；
6. 两层门槛（逐接口硬门槛 + 集合均值）与 core 接口更严门槛；
7. 数据不足判为 insufficient_data 且转人工确认项；
8. 报告渲染（JSON / Markdown）可产出且含口径声明。
"""

from __future__ import annotations

import pytest

from app.test_platform.api_coverage_review import (
    AUTH_SCENARIOS,
    DIMENSION_WEIGHTS,
    PLATFORM_RULES,
    ClaimIssue,
    ContractSurface,
    CoverageClaim,
    CoverageRules,
    build_contract_surface,
    review_api_coverage,
    review_endpoint,
    validate_claims,
)


# ---------------------------------------------------------------------------
# 夹具：一份"信息完整"的契约
# ---------------------------------------------------------------------------

def full_endpoint() -> dict:
    return {
        "id": "ep-1",
        "display_name": "POST /pets",
        "method": "POST",
        "path": "/pets",
        "parameters": [
            {"name": "x-tenant", "in": "header", "required": True, "schema": {"type": "string"}},
            {"name": "trace", "in": "query", "required": False, "schema": {"type": "string"}},
        ],
        "request_body": {
            "content": {
                "application/json": {
                    "schema": {
                        "type": "object",
                        "required": ["name"],
                        "properties": {
                            "name": {"type": "string"},
                            "status": {"type": "string", "enum": ["a", "b"]},
                        },
                    }
                }
            }
        },
        "responses": {"200": {}, "400": {}, "401": {}},
        "security": [{"bearerAuth": []}],
        "custom_config": {"security_source": "global"},
    }


def full_claim(case_id: str = "TC-1") -> dict:
    return {
        "case_id": case_id,
        "status_codes": ["200", "400", "401"],
        "parameters": ["x-tenant", "trace"],
        "enum_values": {"body:status": ["a", "b"]},
        "body_fields": ["name", "status"],
        "auth_scenarios": ["with_token", "without_token"],
    }


def bare_endpoint() -> dict:
    """只有状态码可判定，其余维度都无覆盖项或未知的契约。"""
    return {
        "id": "ep-2",
        "display_name": "GET /x",
        "method": "GET",
        "path": "/x",
        "parameters": [],
        "request_body": None,
        "responses": {"200": {}},
        "security": [],
        "custom_config": {"security_source": "global"},
    }


# ---------------------------------------------------------------------------
# 1. 平台规则
# ---------------------------------------------------------------------------

class TestPlatformRules:
    def test_weights_sum_to_100(self):
        assert sum(DIMENSION_WEIGHTS.values()) == 100
        assert sum(PLATFORM_RULES.weights.values()) == 100

    def test_invalid_weights_rejected(self):
        with pytest.raises(ValueError):
            CoverageRules(weights={"status_code": 50, "required_param": 40})

    def test_rules_are_labeled_as_platform_rules(self):
        """阈值必须声明为平台规则，不得冒充行业标准原文要求。"""
        provenance = PLATFORM_RULES.provenance
        assert "platform_rule" in provenance
        assert "ISTQB" in provenance  # 明确声明标准只作参考方向

    def test_to_dict_marks_provenance(self):
        d = PLATFORM_RULES.to_dict()
        assert d["provenance"] == PLATFORM_RULES.provenance
        assert d["per_endpoint_floor"] == 70.0
        assert d["core_endpoint_floor"] == 85.0
        assert d["average_floor"] == 80.0


# ---------------------------------------------------------------------------
# 2. 契约面解析
# ---------------------------------------------------------------------------

class TestContractSurface:
    def test_status_codes_exclude_default(self):
        ep = full_endpoint()
        ep["responses"] = {"200": {}, "400": {}, "default": {}}
        surface = build_contract_surface(ep)
        assert set(surface.status_codes) == {"200", "400"}
        assert any("default" in n for n in surface.notes)

    def test_required_and_optional_params(self):
        surface = build_contract_surface(full_endpoint())
        assert surface.required_params == ("x-tenant",)
        assert surface.optional_params == ("trace",)

    def test_enum_and_typed_fields_extracted(self):
        surface = build_contract_surface(full_endpoint())
        assert surface.enums == {"body:status": ("a", "b")}
        assert "param:x-tenant" in surface.typed_fields
        assert "body:name" in surface.typed_fields

    def test_body_required_fields(self):
        surface = build_contract_surface(full_endpoint())
        assert surface.body_required_fields == ("name",)
        assert surface.body_optional_fields == ("status",)

    def test_ref_schema_marked_unknown(self):
        ep = full_endpoint()
        ep["request_body"] = {
            "content": {"application/json": {"schema": {"$ref": "#/components/schemas/Pet"}}}
        }
        surface = build_contract_surface(ep)
        assert surface.body_unknown_reason is not None
        assert surface.body_required_fields == ()

    def test_missing_responses_marked_unknown(self):
        ep = {"id": "e", "method": "GET", "path": "/a"}
        surface = build_contract_surface(ep)
        assert surface.status_unknown_reason is not None
        assert surface.param_unknown_reason is not None

    def test_write_method_without_body_marked_unknown(self):
        ep = {"id": "e", "method": "POST", "path": "/a", "responses": {"200": {}}}
        surface = build_contract_surface(ep)
        assert surface.body_unknown_reason is not None

    # ---- 鉴权三态（P0-3 修复后的语义） ----

    def test_security_undeclared_is_unknown_not_missing(self):
        """三级都未声明鉴权时不能当"公开接口"算——那是未知。"""
        ep = full_endpoint()
        ep["security"] = None
        ep["custom_config"] = {"security_source": "none"}
        surface = build_contract_surface(ep)
        assert surface.auth_unknown_reason is not None
        assert surface.auth_required is None

    def test_explicit_empty_security_means_no_auth(self):
        ep = full_endpoint()
        ep["security"] = []
        ep["custom_config"] = {"security_source": "global"}
        surface = build_contract_surface(ep)
        assert surface.auth_required is False
        assert surface.auth_unknown_reason is None

    def test_global_security_marks_auth_required(self):
        surface = build_contract_surface(full_endpoint())
        assert surface.auth_required is True
        assert surface.auth_schemes == ("bearerAuth",)


# ---------------------------------------------------------------------------
# 3. 覆盖声明校验
# ---------------------------------------------------------------------------

class TestClaimValidation:
    def test_valid_claim_passes(self):
        surface = build_contract_surface(full_endpoint())
        valid, issues = validate_claims(surface, [CoverageClaim.from_dict(full_claim())])
        assert len(valid) == 1
        assert issues == []

    @pytest.mark.parametrize(
        "mutate,kind",
        [
            (lambda c: c.update(status_codes=["999"]), "unknown_status_code"),
            (lambda c: c.update(parameters=["nope"]), "unknown_parameter"),
            (lambda c: c.update(body_fields=["nope"]), "unknown_body_field"),
            (lambda c: c.update(enum_values={"body:status": ["z"]}), "enum_value_out_of_range"),
            (lambda c: c.update(enum_values={"param:trace": ["a"]}), "unknown_enum_field"),
            (lambda c: c.update(auth_scenarios=["root"]), "invalid_auth_scenario"),
        ],
    )
    def test_invalid_claims_caught(self, mutate, kind):
        surface = build_contract_surface(full_endpoint())
        raw = full_claim()
        mutate(raw)
        valid, issues = validate_claims(surface, [CoverageClaim.from_dict(raw)])
        assert valid == [], "非法声明不得计入覆盖"
        assert any(i.kind == kind for i in issues)

    def test_auth_scenario_vocabulary_is_closed(self):
        assert "with_token" in AUTH_SCENARIOS
        assert "without_token" in AUTH_SCENARIOS


# ---------------------------------------------------------------------------
# 4. 评分与扣分口径
# ---------------------------------------------------------------------------

class TestScoring:
    def test_full_coverage_scores_100_and_passes(self):
        review, issues = review_endpoint(full_endpoint(), [CoverageClaim.from_dict(full_claim())])
        assert issues == []
        assert review.score == pytest.approx(100.0)
        assert review.status == "pass"
        assert review.fair == []

    def test_partial_coverage_score_and_gates(self):
        claim = full_claim()
        claim["status_codes"] = ["200"]  # 只覆盖 1/3
        claim["parameters"] = []
        claim["body_fields"] = []
        claim["enum_values"] = {}
        claim["auth_scenarios"] = []
        review, _ = review_endpoint(full_endpoint(), [CoverageClaim.from_dict(claim)])

        assert review.score == pytest.approx(25 * (1 / 3), abs=0.01)
        assert review.status == "fail"
        # 必填参数硬门槛
        assert any("必填参数未 100% 覆盖" in x for x in review.fair)
        status_dim = next(d for d in review.dimensions if d.key == "status_code")
        assert set(status_dim.missing) == {"400", "401"}

    def test_deduction_never_exceeds_dimension_weight(self):
        """扣分与加权分不得重复计算：单维度扣分恒 ≤ 该维度满分。"""
        claim = full_claim()
        claim["status_codes"] = []
        review, _ = review_endpoint(full_endpoint(), [CoverageClaim.from_dict(claim)])
        for dim in review.dimensions:
            assert dim.deduction <= dim.weight + 1e-9
            if dim.state == "scored":
                assert dim.score is not None
                assert dim.score + dim.deduction == pytest.approx(dim.weight)

    def test_unknown_dimensions_excluded_and_renormalized(self):
        """未知维度从分母剔除，分数在已知维度上重归一化（而非按 0 计）。"""
        ep = full_endpoint()
        ep["responses"] = {}  # 状态码未知
        ep["security"] = None
        ep["custom_config"] = {"security_source": "none"}  # 鉴权未知
        claim = full_claim()
        review, _ = review_endpoint(ep, [CoverageClaim.from_dict(claim)])

        status_dim = next(d for d in review.dimensions if d.key == "status_code")
        auth_dim = next(d for d in review.dimensions if d.key == "auth")
        assert status_dim.state == "unknown"
        assert auth_dim.state == "unknown"
        # 其余维度全覆盖 -> 重归一化后仍应为 100
        assert review.score == pytest.approx(100.0)
        assert review.known_weight_ratio == pytest.approx(0.65)  # 100-25-10

    def test_not_applicable_dimension_excluded(self):
        review, _ = review_endpoint(bare_endpoint(), [])
        param_dim = next(d for d in review.dimensions if d.key == "required_param")
        auth_dim = next(d for d in review.dimensions if d.key == "auth")
        assert param_dim.state == "not_applicable"
        assert auth_dim.state == "not_applicable"
        assert param_dim.deduction == 0.0

    def test_insufficient_data_when_contract_too_thin(self):
        review, _ = review_endpoint(bare_endpoint(), [])
        assert review.status == "insufficient_data"
        assert review.score is None
        assert review.known_weight_ratio < PLATFORM_RULES.min_known_weight_ratio

    def test_core_endpoint_uses_stricter_floor(self):
        # 构造一个总分落在 70~85 之间的契约
        ep = full_endpoint()
        claim = full_claim()
        claim["status_codes"] = ["200", "400"]  # 2/3 -> 16.67 / 25
        claim["enum_values"] = {"body:status": ["a"]}  # 1/2 -> 7.5
        claim["parameters"] = ["x-tenant"]  # type_format 少一个
        claim["auth_scenarios"] = ["with_token"]  # 1/2 -> 5

        normal, _ = review_endpoint(ep, [CoverageClaim.from_dict(claim)], is_core=False)
        core, _ = review_endpoint(ep, [CoverageClaim.from_dict(claim)], is_core=True)

        assert normal.score == pytest.approx(core.score)
        assert normal.status == "pass", normal.fair
        assert core.status == "fail"
        assert any("核心接口" in x for x in core.fair)


# ---------------------------------------------------------------------------
# 5. 集合汇总与门槛
# ---------------------------------------------------------------------------

class TestReportAggregation:
    def test_blocked_on_hard_gate(self):
        report = review_api_coverage([full_endpoint()], {"ep-1": []})
        assert report.blocked is True
        assert any("必填参数" in r for r in report.block_reasons)

    def test_not_blocked_when_all_covered(self):
        report = review_api_coverage([full_endpoint()], {"ep-1": [full_claim()]})
        assert report.blocked is False
        assert report.block_reasons == []
        assert report.average_score == pytest.approx(100.0)

    def test_average_floor_blocks_balanced_low_scores(self):
        """均值指标必须叠加在逐接口硬门槛之上：这里把硬门槛调低，验证均值仍能拦住。"""
        rules = CoverageRules(per_endpoint_floor=0.0, required_param_hard_gate=False)
        claim = full_claim()
        claim["status_codes"] = ["200"]
        claim["parameters"] = []
        claim["body_fields"] = []
        claim["enum_values"] = {}
        claim["auth_scenarios"] = []
        report = review_api_coverage(
            [full_endpoint(), full_endpoint()],
            {"ep-1": [claim]},
            rules=rules,
        )
        assert report.blocked is True
        assert any("集合平均分" in r for r in report.block_reasons)

    def test_invalid_claims_block_and_report(self):
        claim = full_claim()
        claim["status_codes"] = ["200", "400", "401", "418"]  # 418 契约未定义
        report = review_api_coverage([full_endpoint()], {"ep-1": [claim]})
        assert report.claim_issues
        assert all(isinstance(i, ClaimIssue) for i in report.claim_issues)
        assert any("非法覆盖声明" in r for r in report.block_reasons)

    def test_insufficient_data_listed_as_manual_review(self):
        report = review_api_coverage([bare_endpoint()], {"ep-2": []})
        assert report.insufficient_reviews
        items = report.manual_review_items
        # 逐接口未知项 + 第一阶段固定的人工确认维度
        assert any(i["endpoint"] == "GET /x" for i in items)
        dims = {i["dimension"] for i in items if i["endpoint"] == "*"}
        assert "业务规则" in dims
        assert "安全 payload（SQL 注入/XSS 等）" in dims
        assert "权限矩阵" in dims

    def test_wildcard_claims_apply_to_all(self):
        report = review_api_coverage([full_endpoint()], {"*": [full_claim()]})
        assert report.reviews[0].score == pytest.approx(100.0)

    def test_core_endpoint_floor_applied_by_id(self):
        report = review_api_coverage(
            [full_endpoint()],
            {"ep-1": [full_claim()]},
            core_endpoint_ids=["ep-1"],
        )
        assert report.reviews[0].is_core is True


# ---------------------------------------------------------------------------
# 6. 报告渲染
# ---------------------------------------------------------------------------

class TestRendering:
    def test_to_dict_shape(self):
        report = review_api_coverage([full_endpoint()], {"ep-1": [full_claim()]})
        d = report.to_dict()
        assert set(d) == {"rules", "summary", "endpoints", "invalid_claims", "manual_review_items"}
        assert d["summary"]["total_endpoints"] == 1
        assert d["rules"]["provenance"].startswith("platform_rule")

    def test_json_serializable(self):
        import json

        report = review_api_coverage([full_endpoint()], {"ep-1": [full_claim()]})
        json.loads(report.to_json())

    def test_markdown_contains_verdict_and_caliber(self):
        report = review_api_coverage([full_endpoint()], {"ep-1": []})
        md = report.to_markdown()
        assert "阻止进入执行阶段" in md
        assert "平台规则" in md or "platform_rule" in md
        assert "未知项处理" in md
        assert "扣分口径" in md

    def test_markdown_pass_verdict(self):
        report = review_api_coverage([full_endpoint()], {"ep-1": [full_claim()]})
        md = report.to_markdown()
        assert "通过，可进入脚本生成与执行阶段" in md


# ---------------------------------------------------------------------------
# 7. 无契约数据时不得把未知算成未覆盖
# ---------------------------------------------------------------------------

def test_empty_contract_never_scores_zero():
    """契约完全缺失信息时，绝不能给出 0 分（那会把未知说成未覆盖）。"""
    ep = {"id": "ep-x", "display_name": "GET /unknown", "method": "GET", "path": "/unknown"}
    review, _ = review_endpoint(ep, [])
    assert review.score is None
    assert review.status == "insufficient_data"
    assert all(d.state in {"unknown", "not_applicable"} for d in review.dimensions)


# ---------------------------------------------------------------------------
# 8. 执行门禁决策（执行路径拦截逻辑）
# ---------------------------------------------------------------------------

class TestGateDecision:
    """门禁决策是纯函数，必须单独可测——它是"低门槛阻止执行"的落点。"""

    def _decide(self, gate, strict):
        from app.agents.api.tools.coverage_review_tools import decide_gate_action

        return decide_gate_action(gate, strict=strict)

    def test_allows_when_never_reviewed(self):
        assert self._decide(None, strict=True) is None
        assert self._decide({}, strict=True) is None

    def test_allows_when_gate_passed(self):
        gate = {"blocked": False, "score": 92.5}
        assert self._decide(gate, strict=True) is None

    def test_warn_mode_does_not_block(self):
        gate = {"blocked": True, "score": 40.0, "block_reasons": ["必填参数未覆盖"]}
        assert self._decide(gate, strict=False) is None

    def test_strict_mode_blocks_with_reason_code(self):
        gate = {"blocked": True, "score": 40.0, "block_reasons": ["必填参数未覆盖"]}
        action = self._decide(gate, strict=True)
        assert action is not None
        assert action["status"] == "blocked"
        assert action["reason_code"] == "COVERAGE_GATE_BLOCKED"
        assert action["coverage_gate"] is gate

    def test_strict_mode_is_env_driven_and_defaults_to_warn(self, monkeypatch):
        from app.agents.api.tools.coverage_review_tools import (
            COVERAGE_GATE_STRICT_ENV,
            is_strict_mode,
        )

        monkeypatch.delenv(COVERAGE_GATE_STRICT_ENV, raising=False)
        assert is_strict_mode() is False, "默认必须是 warn，避免影响既有流水线"

        for value in ("strict", "1", "true", "on"):
            monkeypatch.setenv(COVERAGE_GATE_STRICT_ENV, value)
            assert is_strict_mode() is True

        for value in ("warn", "0", "false", "off"):
            monkeypatch.setenv(COVERAGE_GATE_STRICT_ENV, value)
            assert is_strict_mode() is False
