# -*- coding: utf-8 -*-
"""API 覆盖度评审（设计后、执行前）——确定性评分服务。

背景与定位
----------
本模块补上现有能力之间的空档：

- `quality-review`（.claude/skills/testcase/quality-review）评审的是**功能测试用例**，
  不是 API 接口契约覆盖度；
- `test-scenario-design` / `api/scenario` 负责多接口场景编排；
- `api/reporter` 在执行**后**给出端点/场景覆盖的**文字**报告；
- 而"用例设计完、脚本生成前，接口契约面到底覆盖了多少"**没有任何确定性计算**。

因此本模块提供纯函数式的覆盖度评审：输入接口契约 + 用例覆盖声明，
输出可复算的分数、缺口清单与是否放行。

三条设计红线（对应复核结论）
----------------------------
1. **未知 ≠ 未覆盖**：契约未提供某维度所需数据时，该维度标记为
   `unknown` / `not_applicable`，从分母中剔除并重归一化权重；绝不按 0 分计入。
   数据不足到无法评分时，该接口标记 `insufficient_data` 并进入人工确认项。
2. **不做二次扣分**：每个维度的扣分是"该维度满分 − 该维度得分"这一件事实的
   唯一表述，结构上恒有 `deduction <= weight`，因此不存在"加权分与扣分项重复计算"。
   扣分清单只作解释，不参与总分。
3. **阈值是平台规则**：权重与门槛均标注为 platform_rule，不冒充 ISTQB/OWASP/ISO
   等标准原文要求（见 `PLATFORM_RULES` 的 `provenance` 字段）。

第一阶段范围
------------
只计算可从 OpenAPI/接口契约**直接提取**的 6 个维度：
状态码、必填参数、枚举、类型与格式、请求体字段、鉴权。

业务规则、权限矩阵、状态机约束、限流/超时/依赖服务不可用、安全 payload、
响应"核心字段"定义等**无结构化来源**，一律不作自动评分，
统一进入 `manual_review_items`（人工确认项）。

本模块不做 I/O、不依赖数据库、不调用 LLM。
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Iterable, Literal, Mapping, Sequence

# ---------------------------------------------------------------------------
# 平台规则（注意：以下权重与门槛均为**平台自定义**，非行业标准原文要求）
# ---------------------------------------------------------------------------

#: 平台规则来源标注，供报告与文档如实声明。
PLATFORM_RULE_PROVENANCE = (
    "platform_rule（本项目自定义质量门槛）。"
    "ISTQB / OWASP / ISO / IEEE 仅作为方法学参考方向，"
    "不规定本模块中的任何具体权重与阈值。"
)

#: 六维权重（合计 100）。
DIMENSION_WEIGHTS: dict[str, int] = {
    "status_code": 25,
    "required_param": 20,
    "enum": 15,
    "type_format": 15,
    "request_body": 15,
    "auth": 10,
}

#: 维度中文名。
DIMENSION_LABELS: dict[str, str] = {
    "status_code": "状态码覆盖",
    "required_param": "必填参数覆盖",
    "enum": "枚举覆盖",
    "type_format": "类型与格式覆盖",
    "request_body": "请求体字段覆盖",
    "auth": "鉴权场景覆盖",
}


@dataclass(frozen=True)
class CoverageRules:
    """覆盖度评审的平台规则。

    Attributes:
        weights: 各维度权重，合计必须为 100。
        required_param_hard_gate: 必填参数覆盖率是否要求 100%（硬门槛）。
        per_endpoint_floor: 单接口总分硬门槛（任一接口低于此值即不放行）。
        core_endpoint_floor: 核心接口总分硬门槛。
        average_floor: 集合平均分门槛（均值指标，与上面的硬门槛叠加生效）。
        min_known_weight_ratio: 契约已知权重占比低于此值时，该接口判为数据不足、
            不给分（避免用残缺契约算出一个貌似精确的分数）。
    """

    weights: Mapping[str, int] = field(default_factory=lambda: dict(DIMENSION_WEIGHTS))
    required_param_hard_gate: bool = True
    per_endpoint_floor: float = 70.0
    core_endpoint_floor: float = 85.0
    average_floor: float = 80.0
    min_known_weight_ratio: float = 0.6
    provenance: str = PLATFORM_RULE_PROVENANCE

    def __post_init__(self) -> None:
        total = sum(self.weights.values())
        if total != 100:
            raise ValueError(f"维度权重合计必须为 100，当前为 {total}")

    def to_dict(self) -> dict[str, Any]:
        return {
            "provenance": self.provenance,
            "weights": dict(self.weights),
            "required_param_is_hard_gate": self.required_param_hard_gate,
            "per_endpoint_floor": self.per_endpoint_floor,
            "core_endpoint_floor": self.core_endpoint_floor,
            "average_floor": self.average_floor,
            "min_known_weight_ratio": self.min_known_weight_ratio,
        }


PLATFORM_RULES = CoverageRules()

#: 鉴权场景允许的取值。
AUTH_SCENARIOS: tuple[str, ...] = (
    "with_token",
    "without_token",
    "invalid_token",
    "expired_token",
    "insufficient_scope",
    "wrong_tenant",
)

DimensionState = Literal["scored", "unknown", "not_applicable"]

#: OpenAPI responses 中非具体状态码的键（不纳入状态码覆盖分母）。
_NON_CODE_RESPONSE_KEYS = {"default"}


# ---------------------------------------------------------------------------
# 契约解析
# ---------------------------------------------------------------------------

@dataclass
class ContractSurface:
    """从接口契约提取出的"待覆盖面"。"""

    endpoint_id: str
    display_name: str
    method: str
    path: str

    status_codes: tuple[str, ...] = ()
    status_unknown_reason: str | None = None

    required_params: tuple[str, ...] = ()
    optional_params: tuple[str, ...] = ()
    param_unknown_reason: str | None = None

    enums: dict[str, tuple[str, ...]] = field(default_factory=dict)
    enum_unknown_reason: str | None = None

    typed_fields: dict[str, tuple[str, str | None]] = field(default_factory=dict)
    type_unknown_reason: str | None = None

    body_required_fields: tuple[str, ...] = ()
    body_optional_fields: tuple[str, ...] = ()
    body_unknown_reason: str | None = None
    body_expected: bool = False

    auth_required: bool | None = None
    auth_schemes: tuple[str, ...] = ()
    auth_unknown_reason: str | None = None

    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "endpoint_id": self.endpoint_id,
            "display_name": self.display_name,
            "method": self.method,
            "path": self.path,
            "status_codes": list(self.status_codes),
            "status_unknown_reason": self.status_unknown_reason,
            "required_params": list(self.required_params),
            "optional_params": list(self.optional_params),
            "param_unknown_reason": self.param_unknown_reason,
            "enums": {k: list(v) for k, v in self.enums.items()},
            "enum_unknown_reason": self.enum_unknown_reason,
            "typed_fields": {k: list(v) for k, v in self.typed_fields.items()},
            "type_unknown_reason": self.type_unknown_reason,
            "body_required_fields": list(self.body_required_fields),
            "body_optional_fields": list(self.body_optional_fields),
            "body_unknown_reason": self.body_unknown_reason,
            "body_expected": self.body_expected,
            "auth_required": self.auth_required,
            "auth_schemes": list(self.auth_schemes),
            "auth_unknown_reason": self.auth_unknown_reason,
            "notes": list(self.notes),
        }


def _first_media_schema(request_body: Mapping[str, Any]) -> Mapping[str, Any] | None:
    """从 OpenAPI requestBody 中取出第一个 media type 的 schema。"""
    content = request_body.get("content")
    if isinstance(content, Mapping):
        for _media_type, media in content.items():
            if isinstance(media, Mapping) and isinstance(media.get("schema"), Mapping):
                return media["schema"]
        return None
    # 兼容已扁平化的写法 {content_type, schema, required}
    schema = request_body.get("schema")
    return schema if isinstance(schema, Mapping) else None


def _extract_fields(schema: Mapping[str, Any]) -> tuple[list[str], list[str], bool]:
    """从 JSON Schema 提取 (必填字段, 可选字段, 是否未展开)。

    ``$ref`` 未展开时无法判定字段，返回 ``unresolved=True``。
    """
    if "$ref" in schema or "allOf" in schema:
        return [], [], True
    properties = schema.get("properties")
    if not isinstance(properties, Mapping):
        return [], [], False
    required_raw = schema.get("required")
    required = [str(x) for x in required_raw] if isinstance(required_raw, Sequence) and not isinstance(required_raw, str) else []
    names = [str(k) for k in properties.keys()]
    optional = [n for n in names if n not in required]
    return required, optional, False


def _schema_type(schema: Mapping[str, Any]) -> tuple[str | None, str | None]:
    """取 (type, format)；$ref/组合结构返回 (None, None)。"""
    if not isinstance(schema, Mapping):
        return None, None
    type_ = schema.get("type")
    fmt = schema.get("format")
    return (str(type_) if type_ else None, str(fmt) if fmt else None)


def _auth_schemes(security: Any) -> tuple[str, ...]:
    """从 OpenAPI security 数组提取 scheme 名（如 bearerAuth）。"""
    if not isinstance(security, Sequence) or isinstance(security, str):
        return ()
    names: list[str] = []
    for requirement in security:
        if isinstance(requirement, Mapping):
            names.extend(str(k) for k in requirement.keys())
    return tuple(dict.fromkeys(names))


def build_contract_surface(endpoint: Mapping[str, Any]) -> ContractSurface:
    """把一条端点契约（DB 行 / get_endpoint_details 输出）解析为待覆盖面。"""
    endpoint_id = str(endpoint.get("id") or endpoint.get("endpoint_id") or "")
    method = str(endpoint.get("method") or "").upper()
    path = str(endpoint.get("path") or "")
    display_name = str(endpoint.get("display_name") or f"{method} {path}".strip())
    custom_config = endpoint.get("custom_config") or {}

    surface = ContractSurface(
        endpoint_id=endpoint_id, display_name=display_name, method=method, path=path
    )

    # ---- 状态码 ----
    responses = endpoint.get("responses")
    if not isinstance(responses, Mapping) or not responses:
        surface.status_unknown_reason = "契约未提供 responses 定义"
    else:
        codes = [
            str(code)
            for code in responses.keys()
            if str(code).lower() not in _NON_CODE_RESPONSE_KEYS
        ]
        if not codes:
            surface.status_unknown_reason = "responses 仅含 default，无具体状态码"
        else:
            surface.status_codes = tuple(sorted(codes))
            if any(str(c).lower() in _NON_CODE_RESPONSE_KEYS for c in responses.keys()):
                surface.notes.append("契约含 default 响应，未纳入状态码分母")

    # ---- 参数 ----
    parameters = endpoint.get("parameters")
    if parameters is None:
        surface.param_unknown_reason = "契约未提供 parameters 定义"
    elif not isinstance(parameters, Sequence) or isinstance(parameters, str):
        surface.param_unknown_reason = "parameters 结构非法（非数组）"
    else:
        required: list[str] = []
        optional: list[str] = []
        for param in parameters:
            if not isinstance(param, Mapping):
                continue
            name = param.get("name")
            if not name:
                continue
            name = str(name)
            if param.get("required") is True:
                required.append(name)
            else:
                optional.append(name)
            schema = param.get("schema")
            if isinstance(schema, Mapping):
                type_, fmt = _schema_type(schema)
                if type_ or fmt:
                    surface.typed_fields[f"param:{name}"] = (type_ or "unknown", fmt)
                enum = schema.get("enum")
                if isinstance(enum, Sequence) and not isinstance(enum, str) and enum:
                    surface.enums[f"param:{name}"] = tuple(str(v) for v in enum)
        surface.required_params = tuple(required)
        surface.optional_params = tuple(optional)

    # ---- 请求体 ----
    request_body = endpoint.get("request_body")
    surface.body_expected = method in {"POST", "PUT", "PATCH"}
    if request_body is None:
        if surface.body_expected:
            surface.body_unknown_reason = f"{method} 通常需要请求体，但契约未提供 request_body"
    elif not isinstance(request_body, Mapping):
        surface.body_unknown_reason = "request_body 结构非法（非对象）"
    else:
        schema = _first_media_schema(request_body)
        if schema is None:
            surface.body_unknown_reason = "request_body 未提供可用 schema"
        else:
            required, optional, unresolved = _extract_fields(schema)
            if unresolved:
                surface.body_unknown_reason = "request_body schema 为 $ref/组合结构，未展开，无法枚举字段"
            else:
                surface.body_required_fields = tuple(required)
                surface.body_optional_fields = tuple(optional)
                for name, prop in (schema.get("properties") or {}).items():
                    if not isinstance(prop, Mapping):
                        continue
                    type_, fmt = _schema_type(prop)
                    if type_ or fmt:
                        surface.typed_fields[f"body:{name}"] = (type_ or "unknown", fmt)
                    enum = prop.get("enum")
                    if isinstance(enum, Sequence) and not isinstance(enum, str) and enum:
                        surface.enums[f"body:{name}"] = tuple(str(v) for v in enum)

    # ---- 鉴权 ----
    security = endpoint.get("security")
    security_source = str(custom_config.get("security_source") or "").lower()
    if security is None:
        # security_source == "none" 表示 method/path/全局三级都未声明鉴权。
        # 这**不能**等同于"公开接口"：既可能是真的无需鉴权，也可能是契约漏写。
        surface.auth_unknown_reason = (
            "契约未声明鉴权（method/path/全局均未声明），无法区分"
            "'确实无需鉴权' 与 '契约漏写'，故不计入评分"
        )
    elif isinstance(security, Sequence) and not isinstance(security, str):
        if len(security) == 0:
            surface.auth_required = False
            surface.notes.append("契约显式声明 security: []，即无需鉴权")
        else:
            surface.auth_required = True
            surface.auth_schemes = _auth_schemes(security)
    else:
        surface.auth_unknown_reason = "security 结构非法"

    return surface


# ---------------------------------------------------------------------------
# 覆盖声明
# ---------------------------------------------------------------------------

@dataclass
class CoverageClaim:
    """单条用例对被覆盖面的声明。

    仅描述**用例实际断言/使用了什么**，由调用方（Agent）从生成的用例/脚本中整理。
    所有字段都会与契约面做一致性校验，声明契约中不存在的项会被判为非法声明。
    """

    case_id: str = ""
    status_codes: tuple[str, ...] = ()
    parameters: tuple[str, ...] = ()
    enum_values: dict[str, tuple[str, ...]] = field(default_factory=dict)
    body_fields: tuple[str, ...] = ()
    auth_scenarios: tuple[str, ...] = ()

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> "CoverageClaim":
        def _tup(value: Any) -> tuple[str, ...]:
            if value is None:
                return ()
            if isinstance(value, str):
                return (value,)
            if isinstance(value, Sequence):
                return tuple(str(v) for v in value)
            return ()

        enum_raw = raw.get("enum_values") or {}
        enum_values: dict[str, tuple[str, ...]] = {}
        if isinstance(enum_raw, Mapping):
            for key, vals in enum_raw.items():
                enum_values[str(key)] = _tup(vals)

        return cls(
            case_id=str(raw.get("case_id") or raw.get("id") or ""),
            status_codes=_tup(raw.get("status_codes")),
            parameters=_tup(raw.get("parameters")),
            enum_values=enum_values,
            body_fields=_tup(raw.get("body_fields")),
            auth_scenarios=_tup(raw.get("auth_scenarios")),
        )


@dataclass
class ClaimIssue:
    """一条非法覆盖声明。"""

    case_id: str
    kind: str
    detail: str

    def to_dict(self) -> dict[str, str]:
        return {"case_id": self.case_id, "kind": self.kind, "detail": self.detail}


def validate_claims(
    surface: ContractSurface, claims: Sequence[CoverageClaim]
) -> tuple[list[CoverageClaim], list[ClaimIssue]]:
    """校验覆盖声明是否只引用了契约中真实存在的项。

    合法的声明被保留（并按契约面裁剪无关值）；非法的记录为 ClaimIssue。
    这样"凭空声明覆盖了某个状态码/参数"会被确定性拦住，而不是默默加分。
    """
    issues: list[ClaimIssue] = []
    valid: list[CoverageClaim] = []

    known_codes = set(surface.status_codes)
    known_params = set(surface.required_params) | set(surface.optional_params)
    known_body = set(surface.body_required_fields) | set(surface.body_optional_fields)

    for claim in claims:
        bad = False

        if surface.status_unknown_reason is None:
            unknown_codes = [c for c in claim.status_codes if c not in known_codes]
            if unknown_codes:
                issues.append(ClaimIssue(
                    claim.case_id, "unknown_status_code",
                    f"声明了契约未定义的状态码：{', '.join(unknown_codes)}",
                ))
                bad = True

        if surface.param_unknown_reason is None:
            unknown_params = [p for p in claim.parameters if p not in known_params]
            if unknown_params:
                issues.append(ClaimIssue(
                    claim.case_id, "unknown_parameter",
                    f"声明了契约未定义的参数：{', '.join(unknown_params)}",
                ))
                bad = True

        if surface.body_unknown_reason is None:
            unknown_fields = [f for f in claim.body_fields if f not in known_body]
            if unknown_fields:
                issues.append(ClaimIssue(
                    claim.case_id, "unknown_body_field",
                    f"声明了契约未定义的请求体字段：{', '.join(unknown_fields)}",
                ))
                bad = True

        for key, values in claim.enum_values.items():
            if key not in surface.enums:
                issues.append(ClaimIssue(
                    claim.case_id, "unknown_enum_field",
                    f"声明了对非枚举字段 {key} 的枚举覆盖",
                ))
                bad = True
                continue
            allowed = set(surface.enums[key])
            outside = [v for v in values if v not in allowed]
            if outside:
                issues.append(ClaimIssue(
                    claim.case_id, "enum_value_out_of_range",
                    f"{key} 声明了枚举外的取值：{', '.join(outside)}",
                ))
                bad = True

        unknown_auth = [s for s in claim.auth_scenarios if s not in AUTH_SCENARIOS]
        if unknown_auth:
            issues.append(ClaimIssue(
                claim.case_id, "invalid_auth_scenario",
                f"非法鉴权场景：{', '.join(unknown_auth)}；允许值：{', '.join(AUTH_SCENARIOS)}",
            ))
            bad = True

        if not bad:
            valid.append(claim)

    return valid, issues


# ---------------------------------------------------------------------------
# 评分
# ---------------------------------------------------------------------------

@dataclass
class DimensionResult:
    """单维度评审结果。"""

    key: str
    label: str
    weight: int
    state: DimensionState
    covered: tuple[str, ...] = ()
    missing: tuple[str, ...] = ()
    total: int = 0
    score: float | None = None
    deduction: float = 0.0
    reason: str | None = None
    evidence_cases: tuple[str, ...] = ()

    @property
    def coverage_ratio(self) -> float | None:
        if self.state != "scored" or self.total == 0:
            return None
        return len(self.covered) / self.total

    def to_dict(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "label": self.label,
            "weight": self.weight,
            "state": self.state,
            "total": self.total,
            "covered_count": len(self.covered),
            "covered": list(self.covered),
            "missing": list(self.missing),
            "coverage_ratio": (
                round(self.coverage_ratio, 4) if self.coverage_ratio is not None else None
            ),
            "score": round(self.score, 2) if self.score is not None else None,
            "deduction": round(self.deduction, 2),
            "reason": self.reason,
            "evidence_cases": list(self.evidence_cases),
        }


def _score_dimension(
    key: str,
    weight: int,
    required: Iterable[str],
    claimed: set[str],
    claim_cases: Mapping[str, set[str]],
    *,
    unknown_reason: str | None,
) -> DimensionResult:
    """对单个维度打分。

    扣分 = 满分 − 得分 = weight × |missing| / |required|，
    结构上恒 ≤ weight，因此**不需要**也不允许再叠加一次扣分项。
    """
    label = DIMENSION_LABELS.get(key, key)
    required_t = tuple(sorted(set(required)))

    if unknown_reason is not None:
        return DimensionResult(
            key=key, label=label, weight=weight, state="unknown", reason=unknown_reason
        )
    if not required_t:
        return DimensionResult(
            key=key, label=label, weight=weight, state="not_applicable",
            total=0, reason="契约中该维度无需覆盖项",
        )

    covered = tuple(x for x in required_t if x in claimed)
    missing = tuple(x for x in required_t if x not in claimed)
    score = weight * len(covered) / len(required_t)
    evidence: list[str] = []
    for item in covered:
        evidence.extend(sorted(claim_cases.get(item, set())))

    return DimensionResult(
        key=key,
        label=label,
        weight=weight,
        state="scored",
        covered=covered,
        missing=missing,
        total=len(required_t),
        score=score,
        deduction=weight - score,
        evidence_cases=tuple(dict.fromkeys(evidence)),
    )


@dataclass
class EndpointReview:
    """单接口评审结果。"""

    surface: ContractSurface
    dimensions: list[DimensionResult] = field(default_factory=list)
    score: float | None = None
    status: Literal["pass", "fail", "insufficient_data"] = "pass"
    fair: list[str] = field(default_factory=list)
    is_core: bool = False

    @property
    def known_weight_ratio(self) -> float:
        known = sum(d.weight for d in self.dimensions if d.state == "scored")
        return known / 100.0

    @property
    def failed_gates(self) -> list[str]:
        return list(self.fair)

    def to_dict(self) -> dict[str, Any]:
        return {
            "endpoint_id": self.surface.endpoint_id,
            "display_name": self.surface.display_name,
            "method": self.surface.method,
            "path": self.surface.path,
            "is_core": self.is_core,
            "status": self.status,
            "score": round(self.score, 2) if self.score is not None else None,
            "known_weight_ratio": round(self.known_weight_ratio, 4),
            "failed_gates": list(self.fair),
            "dimensions": [d.to_dict() for d in self.dimensions],
            "surface": self.surface.to_dict(),
        }


def review_endpoint(
    endpoint: Mapping[str, Any],
    claims: Sequence[CoverageClaim] = (),
    *,
    rules: CoverageRules = PLATFORM_RULES,
    is_core: bool = False,
) -> tuple[EndpointReview, list[ClaimIssue]]:
    """评审单个接口。"""
    surface = build_contract_surface(endpoint)
    valid_claims, issues = validate_claims(surface, claims)

    claimed_codes = {c for cl in valid_claims for c in cl.status_codes}
    claimed_params = {p for cl in valid_claims for p in cl.parameters}
    claimed_body = {f for cl in valid_claims for f in cl.body_fields}

    claim_cases: dict[str, set[str]] = {}
    for cl in valid_claims:
        for item in [*cl.status_codes, *cl.parameters, *cl.body_fields]:
            claim_cases.setdefault(item, set()).add(cl.case_id)
        for key, values in cl.enum_values.items():
            for value in values:
                claim_cases.setdefault(f"{key}={value}", set()).add(cl.case_id)
        for scenario in cl.auth_scenarios:
            claim_cases.setdefault(f"auth:{scenario}", set()).add(cl.case_id)

    dims: list[DimensionResult] = [
        _score_dimension(
            "status_code", rules.weights["status_code"], surface.status_codes,
            claimed_codes, claim_cases, unknown_reason=surface.status_unknown_reason,
        ),
        _score_dimension(
            "required_param", rules.weights["required_param"], surface.required_params,
            claimed_params, claim_cases, unknown_reason=surface.param_unknown_reason,
        ),
    ]

    # 枚举：以 "字段=取值" 为覆盖项
    enum_items: list[str] = [
        f"{key}={value}" for key, values in surface.enums.items() for value in values
    ]
    claimed_enum: set[str] = set()
    for cl in valid_claims:
        for key, values in cl.enum_values.items():
            if key in surface.enums:
                allowed = set(surface.enums[key])
                for value in values:
                    if value in allowed:
                        claimed_enum.add(f"{key}={value}")
    dims.append(_score_dimension(
        "enum", rules.weights["enum"], enum_items, claimed_enum, claim_cases,
        unknown_reason=surface.enum_unknown_reason,
    ))

    # 类型与格式：覆盖项键为 "param:<name>" / "body:<name>"，
    # 因此声明侧必须做同样的前缀映射，否则会误判为 0 覆盖。
    claimed_typed = {f"param:{name}" for name in claimed_params} | {
        f"body:{name}" for name in claimed_body
    }
    dims.append(_score_dimension(
        "type_format", rules.weights["type_format"], surface.typed_fields.keys(),
        claimed_typed, claim_cases,
        unknown_reason=surface.type_unknown_reason,
    ))

    dims.append(_score_dimension(
        "request_body", rules.weights["request_body"], surface.body_required_fields,
        claimed_body, claim_cases, unknown_reason=surface.body_unknown_reason,
    ))

    # 鉴权：需要"正向有鉴权"与"缺失/非法鉴权被拒"两类证据。
    # 契约声明需要鉴权时，覆盖项固定为这两个必测场景（平台规则）。
    if surface.auth_unknown_reason is not None:
        dims.append(DimensionResult(
            key="auth", label=DIMENSION_LABELS["auth"], weight=rules.weights["auth"],
            state="unknown", reason=surface.auth_unknown_reason,
        ))
    elif not surface.auth_required:
        dims.append(DimensionResult(
            key="auth", label=DIMENSION_LABELS["auth"], weight=rules.weights["auth"],
            state="not_applicable", reason="契约声明该接口无需鉴权",
        ))
    else:
        required_auth = ("auth:with_token", "auth:without_token")
        claimed_auth = {
            f"auth:{s}" for cl in valid_claims for s in cl.auth_scenarios
        }
        dims.append(_score_dimension(
            "auth", rules.weights["auth"], required_auth, claimed_auth, claim_cases,
            unknown_reason=None,
        ))

    # ---- 总分：仅在已知维度上重归一化 ----
    scored = [d for d in dims if d.state == "scored"]
    known_weight = sum(d.weight for d in scored)
    known_ratio = known_weight / 100.0

    review = EndpointReview(surface=surface, dimensions=dims, is_core=is_core)

    if known_ratio < rules.min_known_weight_ratio:
        review.score = None
        review.status = "insufficient_data"
        review.fair.append(
            f"契约已知权重占比 {known_ratio:.0%} < {rules.min_known_weight_ratio:.0%}，"
            "数据不足，不予评分（转人工确认）"
        )
        return review, issues

    total_score = sum(d.score or 0.0 for d in scored) / known_weight * 100
    review.score = total_score

    floor = rules.core_endpoint_floor if is_core else rules.per_endpoint_floor
    if total_score < floor:
        review.fair.append(
            f"总分 {total_score:.1f} < {'核心接口' if is_core else '单接口'}门槛 {floor:.0f}"
        )

    if rules.required_param_hard_gate:
        for d in dims:
            if d.key == "required_param" and d.state == "scored" and d.missing:
                review.fair.append(
                    f"必填参数未 100% 覆盖，缺失：{', '.join(d.missing)}"
                )

    review.status = "fail" if review.fair else "pass"
    return review, issues


# ---------------------------------------------------------------------------
# 集合汇总
# ---------------------------------------------------------------------------

@dataclass
class ApiCoverageReport:
    """集合级覆盖度评审报告。"""

    reviews: list[EndpointReview] = field(default_factory=list)
    claim_issues: list[ClaimIssue] = field(default_factory=list)
    rules: CoverageRules = PLATFORM_RULES

    # ---- 汇总指标 ----
    @property
    def scored_reviews(self) -> list[EndpointReview]:
        return [r for r in self.reviews if r.score is not None]

    @property
    def insufficient_reviews(self) -> list[EndpointReview]:
        return [r for r in self.reviews if r.status == "insufficient_data"]

    @property
    def average_score(self) -> float | None:
        scored = self.scored_reviews
        if not scored:
            return None
        return sum(r.score or 0.0 for r in scored) / len(scored)

    @property
    def block_reasons(self) -> list[str]:
        reasons: list[str] = []
        for r in self.reviews:
            reasons.extend(f"{r.surface.display_name}: {x}" for x in r.fair)
        if self.claim_issues:
            reasons.append(
                f"存在 {len(self.claim_issues)} 条非法覆盖声明（声明了契约中不存在的项）"
            )
        avg = self.average_score
        if avg is not None and avg < self.rules.average_floor:
            reasons.append(
                f"集合平均分 {avg:.1f} < 均值门槛 {self.rules.average_floor:.0f}"
            )
        return reasons

    @property
    def blocked(self) -> bool:
        return bool(self.block_reasons)

    @property
    def manual_review_items(self) -> list[dict[str, str]]:
        """人工确认项：契约无结构化来源、或数据不足而无法自动评分的部分。"""
        items: list[dict[str, str]] = []

        for r in self.reviews:
            for d in r.dimensions:
                if d.state == "unknown":
                    items.append({
                        "endpoint": r.surface.display_name,
                        "dimension": d.label,
                        "reason": d.reason or "契约未提供该维度所需数据",
                    })

        # 契约数据不足以评分的接口：不给分，但必须让人看到，而不是静默通过
        for r in self.insufficient_reviews:
            items.append({
                "endpoint": r.surface.display_name,
                "dimension": "契约数据不足",
                "reason": r.fair[0] if r.fair else "契约已知信息不足以评分",
            })

        # 第一阶段明确不自动评分的外部维度（无结构化来源）
        for name in (
            "业务规则", "权限矩阵", "状态机约束",
            "限流/超时/依赖服务不可用", "安全 payload（SQL 注入/XSS 等）",
            "响应'核心字段'定义",
        ):
            items.append({
                "endpoint": "*",
                "dimension": name,
                "reason": "无结构化来源，第一阶段作为人工确认项，不参与自动评分",
            })

        return items

    def to_dict(self) -> dict[str, Any]:
        avg = self.average_score
        return {
            "rules": self.rules.to_dict(),
            "summary": {
                "total_endpoints": len(self.reviews),
                "scored_endpoints": len(self.scored_reviews),
                "insufficient_data_endpoints": len(self.insufficient_reviews),
                "average_score": round(avg, 2) if avg is not None else None,
                "blocked": self.blocked,
                "block_reasons": self.block_reasons,
                "invalid_claim_count": len(self.claim_issues),
            },
            "endpoints": [r.to_dict() for r in self.reviews],
            "invalid_claims": [i.to_dict() for i in self.claim_issues],
            "manual_review_items": self.manual_review_items,
        }

    def to_json(self, *, indent: int = 2) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, indent=indent)

    def to_markdown(self) -> str:
        s = self.to_dict()["summary"]
        lines: list[str] = ["# API 覆盖度评审报告（设计后 / 执行前）", ""]

        lines.append("## 一、是否放行")
        lines.append("")
        if s["blocked"]:
            lines.append("**结论：阻止进入执行阶段。**")
            lines.append("")
            for reason in s["block_reasons"]:
                lines.append(f"- ❌ {reason}")
        else:
            lines.append("**结论：通过，可进入脚本生成与执行阶段。**")
        lines.append("")

        lines.append("## 二、汇总指标")
        lines.append("")
        lines.append(f"- 接口总数：{s['total_endpoints']}")
        lines.append(f"- 已评分接口：{s['scored_endpoints']}")
        lines.append(f"- 数据不足（转人工）：{s['insufficient_data_endpoints']}")
        avg = s["average_score"]
        lines.append(f"- 平均分：{avg if avg is not None else '不可用（无可评分接口）'}")
        lines.append(f"- 非法覆盖声明：{s['invalid_claim_count']}")
        lines.append("")

        lines.append("## 三、逐接口明细")
        lines.append("")
        lines.append("| 接口 | 核心 | 总分 | 状态 | 已知权重 |")
        lines.append("|---|---|---|---|---|")
        for r in self.reviews:
            score = f"{r.score:.1f}" if r.score is not None else "—"
            core = "是" if r.is_core else ""
            lines.append(
                f"| {r.surface.display_name} | {core} | {score} | {r.status} | "
                f"{r.known_weight_ratio:.0%} |"
            )
        lines.append("")

        for r in self.reviews:
            lines.append(f"### {r.surface.display_name}")
            lines.append("")
            lines.append("| 维度 | 权重 | 状态 | 覆盖 | 缺失 | 得分 |")
            lines.append("|---|---|---|---|---|---|")
            for d in r.dimensions:
                cov = f"{len(d.covered)}/{d.total}" if d.state == "scored" else "—"
                miss = ", ".join(d.missing) if d.missing else "—"
                sc = f"{d.score:.1f}" if d.score is not None else "—"
                lines.append(
                    f"| {d.label} | {d.weight} | {d.state} | {cov} | {miss} | {sc} |"
                )
            lines.append("")
            for d in r.dimensions:
                if d.state == "scored" and d.missing:
                    lines.append(
                        f"- ⚠️ {d.label}：缺失 {', '.join(d.missing)}（扣 {d.deduction:.1f} 分）"
                    )
                elif d.state == "unknown":
                    lines.append(f"- ❔ {d.label}：{d.reason}（不计入评分）")
            if r.fair:
                for reason in r.fair:
                    lines.append(f"- ❌ {reason}")
            lines.append("")

        issues = self.claim_issues
        if issues:
            lines.append("## 四、非法覆盖声明")
            lines.append("")
            for i in issues:
                lines.append(f"- `{i.case_id}` {i.kind}：{i.detail}")
            lines.append("")

        lines.append("## 五、人工确认项（第一阶段不自动评分）")
        lines.append("")
        lines.append("| 接口 | 维度 | 原因 |")
        lines.append("|---|---|---|")
        seen: set[tuple[str, str]] = set()
        for item in self.manual_review_items:
            key = (item["endpoint"], item["dimension"])
            if key in seen:
                continue
            seen.add(key)
            lines.append(f"| {item['endpoint']} | {item['dimension']} | {item['reason']} |")
        lines.append("")

        lines.append("## 六、口径声明")
        lines.append("")
        lines.append(f"- 权重与门槛：{self.rules.provenance}")
        lines.append("- 未知项处理：契约未提供数据的维度标记为 unknown/not_applicable，"
                     "从分母剔除并重归一化，不按 0 分计入。")
        lines.append("- 扣分口径：单维度扣分 = 满分 − 得分，恒不超过该维度满分；"
                     "扣分清单仅作解释，不与总分重复计算。")
        lines.append("- 评分范围：仅覆盖可从 OpenAPI 契约直接提取的 6 个维度。")
        lines.append("")

        return "\n".join(lines)


def review_api_coverage(
    endpoints: Sequence[Mapping[str, Any]],
    claims_by_endpoint: Mapping[str, Sequence[Mapping[str, Any] | CoverageClaim]] | None = None,
    *,
    rules: CoverageRules = PLATFORM_RULES,
    core_endpoint_ids: Iterable[str] = (),
) -> ApiCoverageReport:
    """对一批接口做覆盖度评审。

    Args:
        endpoints: 端点契约列表（DB 行字典或 get_endpoint_details 输出）。
        claims_by_endpoint: {endpoint_id: [覆盖声明, ...]}；声明可为 dict 或 CoverageClaim。
            也支持键为 ``"*"`` 的兜底声明。
        rules: 平台规则（权重与门槛）。
        core_endpoint_ids: 核心接口 ID 集合，套用更严的核心接口门槛。

    Returns:
        ApiCoverageReport
    """
    claims_by_endpoint = claims_by_endpoint or {}
    core_ids = set(str(x) for x in core_endpoint_ids)
    wildcard = claims_by_endpoint.get("*", ())

    reviews: list[EndpointReview] = []
    all_issues: list[ClaimIssue] = []

    for endpoint in endpoints:
        surface_key = str(endpoint.get("id") or endpoint.get("endpoint_id") or "")
        raw_claims = list(claims_by_endpoint.get(surface_key, ())) or list(wildcard)
        parsed: list[CoverageClaim] = [
            c if isinstance(c, CoverageClaim) else CoverageClaim.from_dict(c)
            for c in raw_claims
        ]
        review, issues = review_endpoint(
            endpoint,
            parsed,
            rules=rules,
            is_core=surface_key in core_ids,
        )
        reviews.append(review)
        all_issues.extend(issues)

    return ApiCoverageReport(reviews=reviews, claim_issues=all_issues, rules=rules)
