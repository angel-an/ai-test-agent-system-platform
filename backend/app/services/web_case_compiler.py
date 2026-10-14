# -*- coding: utf-8 -*-
"""用例 → 执行脚本 编译层（v1，最小实现）。

职责：把**已生成的用例**编译成执行脚本可直接消费的清单，并记录血缘：

- 用例 → 规则编号（``requirement_rule_id``）→ 断言（``custom_fields.assertions``）
- 断言按"能否在没有定位器的情况下执行"分级：本版把需要页面定位器的断言标为
  ``pending_locator``（**不伪装成通过**），只把文档字段名做**软校验**并输出覆盖度。
- 产出片段内嵌进 Playwright 脚本（执行器只下载单个脚本文件，因此清单必须内嵌）。

刻意不做的事：不猜选择器、不伪造通过、不引入模型。
"""

from __future__ import annotations

import base64
import json
import re
from dataclasses import dataclass, field as dc_field
from typing import Any, Iterable, Mapping

from app.services.web_upload_fixtures import (
    XLSX_MIME,
    bad_extension_bytes,
    minimal_xlsx_bytes,
    sample_spec,
    xls_base64,
    xlsx_parts_base64,
)

#: 需要页面定位器才能执行断言的类型（本版不执行，仅统计）
LOCATOR_DEPENDENT_KINDS = {"required", "enum", "default", "boundary", "precision",
                           "max_length", "max_count", "file_format", "file_size"}
#: 无需定位器、可做软校验的类型
SOFT_CHECK_KINDS = {"visibility"}

#: ``build_form_plan`` **实际能生成检查**的全部断言种类（单一事实来源）。
#: 注意 ``range`` 不在 LOCATOR_DEPENDENT_KINDS 里（后者只用于"需定位器断言"的统计口径），
#: 但表单计划明确支持它（见下面的 ``elif kind in {"boundary", "range", ...}`` 分支）——
#: 断言校验/回写接口必须以本集合为准，否则会把合法的 range 断言误判为不支持。
FORM_CHECK_KINDS = frozenset(LOCATOR_DEPENDENT_KINDS | SOFT_CHECK_KINDS | {"range"})

# ==========================================================================
# P1-3 阶段 B：**规则级负路径场景**（``extra_config.negative_scenarios``）
# ==========================================================================
# 背景（实测 DEFECT-001）：现有负路径层（submitQueue）的入队条件**全部是"单个字段
# 的值非法"**（空必填 / 非法探针值）。而"编辑时追加一个已被其他活动占用的商品 →
# 期望服务端拒绝跨记录唯一性冲突"这类断言，违规主体是**字段值与外部状态的冲突**，
# 需要先构造前置条件（多步交互），现有框架完全没有入口 —— 因此这类缺陷
# "自动执行必然发现不了"（TR-160/TR-161 只能靠工作区手写 demo2_script.ts 完成）。
#
# 本节把那条手写链路**模板化**：声明式场景 → 渲染进执行器 → 复用既有的
# 点击提交 / 网络响应捕获 / 写隔离原语执行。
#
# ★ 实现口径差异（与设计文档 §2.2 的一处偏离，此处如实记录）：
#   设计文档写的是"把 NegativeScenario 展开为 ``submitQueue`` 里 ``kind: "rule_reject"``
#   的条目"。实现改为**独立阶段**（``runNegativeScenarios``），理由：
#   现有那个 for 循环的每一步都围绕 ``check.field``（复位该字段、定位它的输入框、
#   兜底填充时跳过它）；规则级场景**没有单一字段**，硬塞进去需要在循环内加约十处
#   ``if (item.kind === 'rule_reject')`` 分叉，很容易破坏已覆盖 149 个用例的既有路径。
#   阶段 B 复用它的**原语**（``clickSubmit`` / ``netLog`` / ``isolationDecision``），
#   但不复用它的循环体。功能等价，风险更低。

#: setup 允许的**动作原语闭集**。未在此集合中的动作一律**拒绝该场景**——
#: 静默忽略未知动作会变成"配置了但从未执行"，比直接拒绝更危险。
SETUP_ACTIONS: frozenset[str] = frozenset({"click", "select_row", "append", "fill", "wait", "select_option", "click_selector"})

#: 允许的目标入口（仅作审计标注与文案，平台不据此猜步骤）。
#: 唯一的**行为差异**：``create`` 表示"就用平台已经打开的那个新建表单"——
#: 因此执行器不会在进入场景前把它关掉（``edit`` / ``add`` / ``custom`` 会关掉，
#: 因为它们的 setup 通常要从列表页自己的入口重新进）。
NEGATIVE_TARGET_ENTRIES: frozenset[str] = frozenset({"edit", "create", "add", "custom"})


@dataclass(frozen=True)
class SetupStep:
    """构造前置条件的一步（**业务无关**的动作原语）。"""

    action: str
    labels: tuple[str, ...] = ()
    selector: str = ""
    row_index: int = 0
    field: str = ""
    value: str = ""
    ms: int = 0

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {"action": self.action, "labels": list(self.labels),
                               "selector": self.selector, "row_index": self.row_index,
                               "field": self.field, "value": self.value, "ms": self.ms}
        return out


@dataclass(frozen=True)
class WriteAllow:
    """写隔离的**放行规则**（收窄到单接口）。

    ★ 为什么不能从 ``api_path_hint`` 直接推出来（实测 DEFECT-001）：

    | 用途 | 匹配语义 | DEFECT-001 的真实值 |
    |---|---|---|
    | ``api_path_hint``（响应匹配） | URL **子串** | ``/v1/purchaseLimit/modify`` |
    | 写隔离放行 | host **后缀** + path **前缀** | host ``imarketing-uat.xysjg.com``、path ``/yundt-saas-gateway/api/mini-mgmt-app/v1/purchaseLimit/modify`` |

    API 请求打到的是**网关主机**（不是当前页面 host），且路径带网关前缀，
    因此子串 `/v1/purchaseLimit/modify` 既不是 host、也不是路径前缀。
    早先把 hint 直接当成路径前缀写进放行规则，会让真实提交**永远被阻断**；
    这里改成显式声明，缺省值只对"同源直连、路径等于 hint"的简单情形成立。

    缺省值（都由计划期填好，执行器不再做推断）：
    - ``host_suffix``：当前页面 host；
    - ``path_prefix``：``api_path_hint``；
    - ``methods``：``POST / PUT / PATCH``。
    """

    host_suffix: str = ""
    path_prefix: str = ""
    methods: tuple[str, ...] = ("POST", "PUT", "PATCH")

    def to_dict(self) -> dict[str, Any]:
        return {"host_suffix": self.host_suffix, "path_prefix": self.path_prefix,
                "methods": list(self.methods)}


@dataclass(frozen=True)
class RejectExpect:
    """期望服务端拒绝的判定条件。

    ★ ``api_path_hint`` **必填**：规则级场景必须真实提交，而写隔离策略默认阻断
    所有非 GET 写请求。拿不到目标接口就无法匹配服务端响应、也无法把放行白名单
    收窄到单接口 —— 与其产出一个"配了但永远 unverified"的场景，
    不如在计划期就拒绝并说明原因（fail-loud）。
    """

    api_path_hint: str
    result_code_not: str = "0"
    message_contains: tuple[str, ...] = ()
    #: 期望服务端以何种**方式**拒绝。``True``（默认）= HTTP 2xx + 业务信封
    #: ``resultCode``≠``result_code_not``（业务层拒绝）；``False`` = 直接以 HTTP 非 2xx
    #: （如 400/403）拒绝。**运行期读取**（见 ``_FORM_EXECUTOR_TS`` 的负路径判定）。
    #: 注：HTTP 5xx 一律判 blocked（服务端崩溃），**不受本字段影响**。
    #: 历史：2026-10-13 前本字段只序列化、运行期从不读取（死配置），5xx 因此被误判成 pass。
    http_ok: bool = True
    allow: WriteAllow = WriteAllow()

    def to_dict(self) -> dict[str, Any]:
        return {"api_path_hint": self.api_path_hint,
                "result_code_not": self.result_code_not,
                "message_contains": list(self.message_contains),
                "http_ok": self.http_ok,
                "allow": self.allow.to_dict()}


@dataclass(frozen=True)
class AcceptExpect:
    """期望服务端**受理**的判定条件（正路径业务规则场景，rev99）。

    与 :class:`RejectExpect` **互斥**：一个场景要么「期望拒绝」要么「期望受理」
    （计划期强制，配置同时给两个直接拒绝）。动机（67197 正向对照）：负路径通道
    原本只有拒绝极性，单收件人发送**应成功**的规则会被判成「受理了应被拒绝的请求」
    ——业务规则两种极性都真实存在，通道必须是双向的。
    判定口径：HTTP 5xx ⇒ blocked（疑似服务端缺陷，与拒绝极性同判，不受本配置影响）；
    HTTP 2xx/3xx 且业务信封 ``resultCode == result_code`` ⇒ pass；
    其余（业务拒绝 / HTTP 4xx 拒绝）⇒ blocked「服务端拒绝了应受理的请求」。
    """

    api_path_hint: str
    #: 期望的**成功**业务码（信封 resultCode 等于该值即视为按期望受理）。
    result_code: str = "0"
    allow: WriteAllow = WriteAllow()

    def to_dict(self) -> dict[str, Any]:
        return {"api_path_hint": self.api_path_hint,
                "result_code": self.result_code,
                "allow": self.allow.to_dict()}


@dataclass(frozen=True)
class NegativeScenario:
    """一个业务规则级负路径场景（声明式）。"""

    scenario_id: str
    rule_ref: str = ""
    target_entry: str = "custom"
    setup: tuple[SetupStep, ...] = ()
    submit_label: tuple[str, ...] = ()
    expect_reject: RejectExpect = RejectExpect(api_path_hint="")
    expect_accept: AcceptExpect = AcceptExpect(api_path_hint="")
    case_id: str = ""
    rule_id: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "scenario_id": self.scenario_id,
            "rule_ref": self.rule_ref,
            "target_entry": self.target_entry,
            "setup": [s.to_dict() for s in self.setup],
            "submit_label": list(self.submit_label),
            "expect_reject": self.expect_reject.to_dict(),
            # rev99 正路径极性：与 expect_reject 互斥（计划期强制），运行期按
            # expect_accept.api_path_hint 是否非空选择判定分支。
            "expect_accept": self.expect_accept.to_dict(),
            # 归属：场景把结论记到哪条用例/需求规则上。留空则只输出证据、不归属，
            # 由台账按 `source=negative_scenario` 单独归类（不冒充"用例已执行"）。
            "case_id": self.case_id,
            "rule_id": self.rule_id,
            "source": "negative_scenario",
        }


def _as_str_list(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value] if value.strip() else []
    if isinstance(value, (list, tuple)):
        return [str(v) for v in value if str(v or "").strip()]
    return []


def _as_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def parse_negative_scenarios(raw: Any) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """解析 ``extra_config.negative_scenarios``，返回 ``(可用场景, 被拒场景)``。

    被拒场景**不静默丢弃**：每条带 ``scenario_id`` 与具体 ``reason``，由执行器
    以 ``[plan-skip]`` 同款方式输出，调用方也能写进资产审计。理由与
    ``_suggest_fields`` 一致 —— "配了但没跑"若不可见，排查成本远高于当场报错。

    非法配置**不阻断编译**：最外层 try/except 保证任何畸形输入都只产生一条拒绝记录。
    """
    if raw is None or raw == "":
        return [], []
    if not isinstance(raw, list):
        return [], [{"scenario_id": "", "reason": "negative_scenarios 必须是数组",
                     "detail": type(raw).__name__}]

    scenarios: list[dict[str, Any]] = []
    rejects: list[dict[str, Any]] = []
    seen: set[str] = set()
    for index, entry in enumerate(raw):
        sid = ""
        try:
            if not isinstance(entry, dict):
                rejects.append({"scenario_id": "", "reason": "场景必须是对象",
                                "detail": f"index={index}"})
                continue
            sid = str(entry.get("scenario_id") or "").strip()
            if not sid:
                rejects.append({"scenario_id": "", "reason": "缺少 scenario_id",
                                "detail": f"index={index}"})
                continue
            if sid in seen:
                # 重复 id 会让断言归属歧义（同一 id 出现两次时不知道该记哪条）
                rejects.append({"scenario_id": sid, "reason": "scenario_id 重复",
                                "detail": f"index={index}"})
                continue

            target_entry = str(entry.get("target_entry") or "custom").strip().lower()
            if target_entry not in NEGATIVE_TARGET_ENTRIES:
                rejects.append({
                    "scenario_id": sid, "reason": "target_entry 非法",
                    "detail": f"{target_entry!r}，允许：{sorted(NEGATIVE_TARGET_ENTRIES)}",
                })
                continue

            raw_setup = entry.get("setup") or []
            if not isinstance(raw_setup, list):
                rejects.append({"scenario_id": sid, "reason": "setup 必须是数组",
                                "detail": type(raw_setup).__name__})
                continue
            steps: list[SetupStep] = []
            bad_step = ""
            for step in raw_setup:
                if not isinstance(step, dict):
                    bad_step = "步骤必须是对象"
                    break
                action = str(step.get("action") or "").strip().lower()
                if action not in SETUP_ACTIONS:
                    bad_step = f"action {action!r} 不在 {sorted(SETUP_ACTIONS)}"
                    break
                steps.append(SetupStep(
                    action=action,
                    labels=tuple(_as_str_list(step.get("labels"))),
                    selector=str(step.get("selector") or ""),
                    row_index=_as_int(step.get("row_index"), 0),
                    field=str(step.get("field") or ""),
                    value=str(step.get("value") or ""),
                    ms=_as_int(step.get("ms"), 0),
                ))
            if bad_step:
                rejects.append({"scenario_id": sid, "reason": "setup 步骤非法",
                                "detail": bad_step})
                continue

            raw_accept = entry.get("expect_accept")
            raw_expect = entry.get("expect_reject") or {}
            if isinstance(raw_accept, dict) and raw_accept:
                # rev99 正路径极性：与 expect_reject 互斥（两个都配 = 语义矛盾，宁可拒绝）
                if raw_expect:
                    rejects.append({"scenario_id": sid,
                                    "reason": "expect_accept 与 expect_reject 互斥",
                                    "detail": "一个场景只能声明一种极性（受理或拒绝）"})
                    continue
                hint = str(raw_accept.get("api_path_hint") or "").strip()
                if not hint:
                    rejects.append({"scenario_id": sid,
                                    "reason": "缺少 expect_accept.api_path_hint",
                                    "detail": "同 RejectExpect：匹配服务端响应 + 收窄写隔离白名单"})
                    continue
                raw_allow = raw_accept.get("allow") or {}
                if not isinstance(raw_allow, dict):
                    rejects.append({"scenario_id": sid,
                                    "reason": "expect_accept.allow 必须是对象",
                                    "detail": type(raw_allow).__name__})
                    continue
                path_prefix = str(raw_allow.get("path_prefix") or hint).strip()
                if not path_prefix.startswith("/"):
                    rejects.append({
                        "scenario_id": sid, "reason": "allow.path_prefix 必须是绝对路径前缀",
                        "detail": f"{path_prefix!r}（写隔离按 path.startsWith 匹配）",
                    })
                    continue
                methods = tuple(m.upper() for m in _as_str_list(raw_allow.get("methods"))) \
                    or ("POST", "PUT", "PATCH")
                scenarios.append(NegativeScenario(
                    scenario_id=sid,
                    rule_ref=str(entry.get("rule_ref") or ""),
                    target_entry=target_entry,
                    setup=tuple(steps),
                    submit_label=tuple(_as_str_list(entry.get("submit_label"))),
                    expect_reject=RejectExpect(api_path_hint=""),
                    expect_accept=AcceptExpect(
                        api_path_hint=hint,
                        result_code=str(raw_accept.get("result_code", "0")),
                        allow=WriteAllow(
                            host_suffix=str(raw_allow.get("host_suffix") or "").strip(),
                            path_prefix=path_prefix,
                            methods=methods,
                        ),
                    ),
                    case_id=str(entry.get("case_id") or ""),
                    rule_id=str(entry.get("rule_id") or ""),
                ).to_dict())
                seen.add(sid)
                continue
            if not isinstance(raw_expect, dict):
                rejects.append({"scenario_id": sid, "reason": "expect_reject 必须是对象",
                                "detail": type(raw_expect).__name__})
                continue
            hint = str(raw_expect.get("api_path_hint") or "").strip()
            if not hint:
                # 没有接口标识 → 无法匹配服务端响应、也无法收窄放行白名单
                #（见 RejectExpect docstring）
                rejects.append({
                    "scenario_id": sid, "reason": "缺少 expect_reject.api_path_hint",
                    "detail": "规则级场景必须真实提交；需要它匹配服务端响应、并把写隔离白名单"
                              "收窄到单接口，否则提交会被阻断（宁可在计划期拒绝，也"
                              "不产出永远 unverified 的场景；正路径场景请改用 expect_accept）",
                })
                continue
            code_not = str(raw_expect.get("result_code_not", "0"))
            msgs = tuple(_as_str_list(raw_expect.get("message_contains")))
            if not code_not.strip() and not msgs:
                # 两个判据都空 → "期望拒绝"无从判定；此时任何结果都能被解释成"拒绝"，属臆造
                rejects.append({
                    "scenario_id": sid, "reason": "expect_reject 无有效判据",
                    "detail": "result_code_not 为空且 message_contains 为空（至少要有一个）",
                })
                continue
            raw_allow = raw_expect.get("allow") or {}
            if not isinstance(raw_allow, dict):
                rejects.append({"scenario_id": sid, "reason": "expect_reject.allow 必须是对象",
                                "detail": type(raw_allow).__name__})
                continue
            path_prefix = str(raw_allow.get("path_prefix") or hint).strip()
            if not path_prefix.startswith("/"):
                # 放行规则用的是 path **startsWith**，不写绝对路径必然永远匹配不上
                rejects.append({
                    "scenario_id": sid, "reason": "allow.path_prefix 必须是绝对路径前缀",
                    "detail": f"{path_prefix!r}（写隔离按 path.startsWith 匹配）",
                })
                continue
            methods = tuple(m.upper() for m in _as_str_list(raw_allow.get("methods"))) \
                or ("POST", "PUT", "PATCH")

            scenarios.append(NegativeScenario(
                scenario_id=sid,
                rule_ref=str(entry.get("rule_ref") or ""),
                target_entry=target_entry,
                setup=tuple(steps),
                submit_label=tuple(_as_str_list(entry.get("submit_label"))),
                expect_reject=RejectExpect(
                    api_path_hint=hint, result_code_not=code_not,
                    message_contains=msgs,
                    http_ok=bool(raw_expect.get("http_ok", True)),
                    allow=WriteAllow(
                        host_suffix=str(raw_allow.get("host_suffix") or "").strip(),
                        path_prefix=path_prefix,
                        methods=methods,
                    ),
                ),
                case_id=str(entry.get("case_id") or ""),
                rule_id=str(entry.get("rule_id") or ""),
            ).to_dict())
            seen.add(sid)
        except Exception as exc:  # noqa: BLE001  配置畸形不得阻断编译
            rejects.append({"scenario_id": sid, "reason": "场景解析异常",
                            "detail": f"{type(exc).__name__}: {exc}"[:200]})
    return scenarios, rejects


@dataclass
class CompiledCase:
    identifier: str
    name: str
    rule_id: str = ""
    kind: str = "narrative"          # rule | narrative
    field: str = ""
    steps: list[dict[str, str]] = dc_field(default_factory=list)
    assertions: list[dict[str, Any]] = dc_field(default_factory=list)
    compile_status: str = "narrative"  # compiled | dangling_rule | narrative
    compile_reason: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "identifier": self.identifier,
            "name": self.name,
            "rule_id": self.rule_id,
            "kind": self.kind,
            "field": self.field,
            "steps": self.steps,
            "assertions": self.assertions,
            # 逐条用例的**编译状态**：区分"可编译执行"与两类不可编译用例，
            # 避免把"生成了用例"读成"已被自动验证"（见交付说明四指标口径）。
            "compile_status": self.compile_status,
            "compile_reason": self.compile_reason,
        }


def _steps_of(case: Any) -> list[dict[str, str]]:
    out: list[dict[str, str]] = []
    for step in getattr(case, "test_case_steps", None) or []:
        out.append({
            "action": getattr(step, "step", "") or "",
            "expected": getattr(step, "result", "") or "",
        })
    return out


def compile_cases(cases: Iterable[Any]) -> dict[str, Any]:
    """把用例编译成执行清单（血缘 + 断言分级 + 覆盖度统计 + **逐条编译状态**）。

    编译状态（``compile_status``）分三类，表达的是**自动化可执行性**——
    用例本身全部来源于需求文档的可测试需求点、全部是可执行的测试用例
    （人工一定可执行）；状态**不**表达用例有效性：

    - ``compiled``：提取到规则体/断言 → 可自动执行（进入脚本断言执行）；
    - ``dangling_rule``：有 ``requirement_rule_id`` 但无可提取的规则体/断言 →
      无法自动执行，需人工执行（或补齐需求规则后重生成转自动）；
    - ``narrative``：无可提取规则编号与断言 → 无法自动执行，需人工执行。

    这三类的总数恒等于 ``case_count``（与四指标口径一致：生成用例数 ≠ 可自动执行用例数）。
    """
    compiled: list[CompiledCase] = []
    for case in cases:
        fields = getattr(case, "custom_fields", None) or {}
        rule = fields.get("requirement_rule") or {}
        assertions = list(fields.get("assertions") or [])
        rule_id = fields.get("requirement_rule_id") or ""
        kind = "rule" if assertions or rule else "narrative"
        if kind == "rule":
            compile_status = "compiled"
            compile_reason = f"携带 {len(assertions)} 条断言" if assertions else "携带规则体"
        elif rule_id:
            compile_status = "dangling_rule"
            # 措辞（rev64b）：用例本身可执行，缺的是可自动判定的断言
            compile_reason = "有规则编号但无可提取的规则体/断言 → 无法自动执行，需人工执行"
        else:
            compile_status = "narrative"
            compile_reason = "无可提取规则编号与断言 → 无法自动执行，需人工执行"
        compiled.append(CompiledCase(
            identifier=getattr(case, "identifier", "") or "",
            name=getattr(case, "name", "") or "",
            rule_id=rule_id,
            kind=kind,
            field=str(rule.get("field") or ""),
            steps=_steps_of(case),
            assertions=assertions,
            compile_status=compile_status,
            compile_reason=compile_reason,
        ))

    locator_assertions = sum(
        1 for c in compiled for a in c.assertions if a.get("kind") in LOCATOR_DEPENDENT_KINDS
    )
    soft_assertions = sum(
        1 for c in compiled for a in c.assertions if a.get("kind") in SOFT_CHECK_KINDS
    )
    field_labels: list[str] = []
    for c in compiled:
        if c.field and c.field not in field_labels:
            field_labels.append(c.field)
    rule_ids = [c.rule_id for c in compiled if c.rule_id]
    # 悬空规则号：有编号、无可提取规则体（无 rule 也无断言）。这类用例本身
    # 可执行（人工），但无法自动判定，**不能**充当自动覆盖度。实测两个批次各有 36 条。
    dangling_rule_ids = [
        c.rule_id for c in compiled
        if c.rule_id and c.kind != "rule"
    ]
    uncompiled = [c for c in compiled if c.compile_status != "compiled"]
    case_status_counts = {
        "compiled": sum(1 for c in compiled if c.compile_status == "compiled"),
        "dangling_rule": sum(1 for c in compiled if c.compile_status == "dangling_rule"),
        "narrative": sum(1 for c in compiled if c.compile_status == "narrative"),
    }

    return {
        "cases": [c.to_dict() for c in compiled],
        "case_count": len(compiled),
        "rule_case_count": sum(1 for c in compiled if c.kind == "rule"),
        "narrative_case_count": sum(1 for c in compiled if c.kind != "rule"),
        # ---- 四指标口径（生成用例数 / 规则用例数 / 编译断言数 / 实际执行资产数）----
        # 这里只负责前三个中的"可编译"口径；"实际执行资产数"由运行侧聚合
        # （见 services/run_generation_metrics.py）。四个数**互不等价**，不得合并成单一覆盖率。
        "compiled_case_count": case_status_counts["compiled"],
        "uncompiled_case_count": len(uncompiled),
        "case_status_counts": case_status_counts,
        "uncompiled_cases": [
            {"identifier": c.identifier, "status": c.compile_status, "reason": c.compile_reason}
            for c in uncompiled
        ],
        "rule_ids": rule_ids,
        "dangling_rule_ids": dangling_rule_ids,
        "field_labels": field_labels,
        "assertion_count": locator_assertions + soft_assertions,
        "soft_assertion_count": soft_assertions,
        "pending_locator_count": locator_assertions,
    }


def render_manifest_block(manifest: dict[str, Any], *, var_name: str = "COMPILED_CASES") -> str:
    """渲染成内嵌 TypeScript 常量（执行器只下载单个脚本文件）。"""
    payload = json.dumps(manifest, ensure_ascii=False, indent=2)
    return (
        f"// === 编译自需求用例（自动生成，请勿手改）：{manifest.get('case_count', 0)} 条用例 / "
        f"{manifest.get('assertion_count', 0)} 条断言 ===\n"
        f"const {var_name} = {payload} as const;\n"
    )


def render_coverage_snippet(*, scope_var: str = "appScope",
                            manifest_var: str = "COMPILED_CASES",
                            form_executable_checks: int = 0,
                            assertion_checks: int = 0) -> str:
    """渲染"编译覆盖度"输出片段。

    - 文档字段名做**软校验**（只统计、不判定失败，避免措辞差异造成假失败）；
    - ``pending_locator`` 扣掉表单执行器**真正执行**的那部分断言，避免把已执行的
      断言继续算作"未执行"；软校验单独记名，不冒充"已执行断言"。
    """
    return f"""
  // ---- 编译覆盖度（来自文档生成的用例/规则；软校验不判定失败）----
  let __labelHits = 0;
  for (const label of {manifest_var}.field_labels) {{
    try {{
      if (await {scope_var}.getByText(label, {{ exact: false }}).first().count()) {{
        __labelHits += 1;
      }}
    }} catch {{
      // 单个标签查询失败不影响整体
    }}
  }}
  console.log('COMPILED_COVERAGE ' + JSON.stringify({{
    cases: {manifest_var}.case_count,
    rule_cases: {manifest_var}.rule_case_count,
    narrative_cases: {manifest_var}.narrative_case_count,
    dangling_rule_ids: {manifest_var}.dangling_rule_ids.length,
    assertions: {manifest_var}.assertion_count,
    form_executable_checks: {int(form_executable_checks)},
    pending_locator: Math.max(0, {manifest_var}.pending_locator_count - {int(assertion_checks)}),
    soft_label_checks: {manifest_var}.soft_assertion_count,
    field_labels_found: __labelHits,
    field_labels_total: {manifest_var}.field_labels.length,
    sample_rule_ids: {manifest_var}.rule_ids.slice(0, 5),
  }}));
"""


__all__ = [
    "CompiledCase",
    "LOCATOR_DEPENDENT_KINDS",
    "RECHARGE_FORM_LOCATORS",
    "SOFT_CHECK_KINDS",
    "build_form_plan",
    "compile_cases",
    "render_coverage_snippet",
    "render_form_executor",
    "render_manifest_block",
]


# ---------------------------------------------------------------------------
# 定位器层 + 表单校验执行器（v2：客户端层只读校验 + 「期望拒绝」项的负路径提交层）
# ---------------------------------------------------------------------------

#: 企业储值充值表单的定位器（来自 2026-09-22 对 UAT 的只读探测：
#: 列表页搜索手机号 → 点「企业充值/退款」→ 弹窗表单）。
#: 支持按子功能覆盖：WebSubFunction.custom_config["field_locators"]。
RECHARGE_FORM_LOCATORS: dict[str, Any] = {
    "name": "enterprise_recharge",
    "flow": "recharge_modal",
    "form_button": ["企业充值/退款", "创建储值充值"],
    "filter_phone_placeholder": "请输入手机号码",
    "search_button": "搜索",
    "modal": ".ant-modal",
    "fields": {
        "变动类型": {"kind": "radio"},
        "变更方式": {"kind": "radio"},
        "会员手机号": {"kind": "textarea", "placeholder_hint": "最多输入10个手机号"},
        "企业账户充值金额": {"kind": "input", "placeholder_hint": "请输入金额"},
        "企业账户退款金额": {"kind": "input", "placeholder_hint": "请输入金额"},
        "备注": {"kind": "textarea", "placeholder_hint": "请输入备注"},
    },
    #: 上传文件控件（「批量导入」模式下才出现）。
    #: **默认不执行**：附文件可能触发向 UAT 的上传请求（服务端副作用），
    #: 只有显式开启 ``allow_upload_checks`` 才会生成校验，且执行器会拦截任何上传请求。
    "upload_fields": {
        "上传文件（企业充值）": {
            "row_label": "上传文件",
            "mode": {"变动类型": "企业储值充值", "变更方式": "批量导入"},
        },
        "上传文件（企业退款）": {
            "row_label": "上传文件",
            "mode": {"变动类型": "企业储值退款（仅扣减储值不退款）", "变更方式": "批量导入"},
        },
    },
    #: 字段所在的条件模式：执行该字段的校验前先把单选切到指定选项。
    #: 金额字段在「批量导入」下会被上传控件替代，所以显式要求「手动输入」。
    "field_modes": {
        "企业账户充值金额": {"变动类型": "企业储值充值", "变更方式": "手动输入"},
        "企业账户退款金额": {"变动类型": "企业储值退款（仅扣减储值不退款）", "变更方式": "手动输入"},
    },
    "default_field_mode": {"变动类型": "企业储值充值", "变更方式": "手动输入"},
    #: 上传大小上限的**字节口径**（用户 2026-09-23 确认：1M = 1,048,576 字节，
    #: 支持格式含 CSV，前后端口径一致）。配置后大小断言会按 N−1 / N / N+1 展开验证。
    "upload_size_limit_bytes": 1_048_576,
    #: 文档声明的允许格式（用于给"格式应被接受"断言逐一构造合法样本）。
    "upload_allowed_formats": ["xlsx", "xls", "csv"],
    #: 声明了但**仍无法**构造有效样本的格式：必须显式登记为未执行，不得用假样本充数。
    #: （.xls 已提供有效 BIFF 样本，故不在此列。）
    "upload_unsupported_sample_formats": {},
    #: 条件显示断言：切换到 option 后，expect_labels 里的字段行应出现。
    #: 标签取自 2026-09-22 对 UAT 的只读 DOM 探测（批量导入下表单行就叫「上传文件」）。
    "toggles": [
        {"field": "变动类型", "option": "企业储值退款（仅扣减储值不退款）",
         "expect_labels": ["企业账户退款金额"],
         "mode": {"变更方式": "手动输入"}},
        # 另一维必须**显式钉住**：否则"切换后的状态"取决于前序检查留下了什么，
        # 归属给用例就会变成猜测（宁可多切一次，也不猜）。
        {"field": "变更方式", "option": "批量导入",
         "expect_labels": ["上传文件"],
         "mode": {"变动类型": "企业储值充值"}},
        {"field": "变更方式", "option": "批量导入",
         "expect_labels": ["上传文件"],
         "mode": {"变动类型": "企业储值退款（仅扣减储值不退款）"}},
    ],
    #: 默认选中断言（文档「默认选中」）
    "defaults": [
        {"field": "变动类型", "option": "企业储值充值"},
        {"field": "变更方式", "option": "手动输入"},
    ],
}


def _norm(text: Any) -> str:
    """归一化文本：去掉所有空白（文档"当…且…"条件串排版差异很大）。"""
    return re.sub(r"\s+", "", str(text or ""))


def _option_head(value: Any) -> str:
    """取选项/字段名的主干（去掉括号说明），用于条件串的包含匹配。

    例：``企业储值退款（仅扣减储值不退款`` → ``企业储值退款``；
    ``上传文件（企业充值）`` → ``上传文件``。
    """
    return re.split(r"[（(]", str(value or ""))[0].strip()


def _when_matches(when: Any, conditions: Iterable[tuple[str, str]]) -> bool:
    """条件串是否包含给定状态的全部 ``字段=取值``（做不到就返回 False，宁可不归属）。"""
    text = _norm(when)
    if not text:
        return False
    pairs = [(str(f), _option_head(v)) for f, v in conditions if f and _option_head(v)]
    if not pairs:
        return False
    return all(f"{_norm(f)}={head}" in text for f, head in pairs)


def _label_matches(case_field: Any, labels: Iterable[Any]) -> bool:
    """用例目标字段是否与"切换后应出现的标签"对应（允许括号说明差异）。"""
    head = _option_head(case_field)
    if not head:
        return False
    for label in labels:
        other = _option_head(label)
        if other and (head == other or head.startswith(other) or other.startswith(head)):
            return True
    return False


def _default_state(loc: Mapping[str, Any]) -> dict[str, str]:
    """定位器声明的默认态（未做任何点击时的单选状态）。"""
    state: dict[str, str] = {}
    for default in loc.get("defaults") or []:
        if default.get("field") and default.get("option"):
            state[str(default["field"])] = str(default["option"])
    return state


def _visibility_wants(manifest: Mapping[str, Any]) -> list[dict[str, str]]:
    """清单里所有"应显示"的条件显示诉求（带归属用例）。"""
    wants: list[dict[str, str]] = []
    for case in manifest.get("cases") or []:
        for assertion in case.get("assertions") or []:
            if assertion.get("kind") != "visibility":
                continue
            if str(assertion.get("expect") or "") != "visible":
                continue
            wants.append({
                "case_id": str(case.get("identifier") or ""),
                "rule_id": str(case.get("rule_id") or ""),
                "field": str(assertion.get("field") or case.get("field") or ""),
                "when": str(assertion.get("when") or ""),
                "expect": str(assertion.get("expect") or ""),
            })
    return wants


def _default_wants(manifest: Mapping[str, Any], field: str, option: str
                   ) -> list[dict[str, str]]:
    """哪些用例的 ``default`` 断言恰好等于该默认观测项（字段+选项完全一致）。"""
    owners: list[dict[str, str]] = []
    for case in manifest.get("cases") or []:
        for assertion in case.get("assertions") or []:
            if assertion.get("kind") != "default":
                continue
            if str(assertion.get("field") or case.get("field") or "") != field:
                continue
            expect = str(assertion.get("expect") or "")
            rule = str(assertion.get("rule") or "")
            if not (option and (option in expect or option in rule)):
                continue
            owners.append({"case_id": str(case.get("identifier") or ""),
                           "rule_id": str(case.get("rule_id") or "")})
    return owners


#: 长度/条数上限的文档措辞（简繁）
_LENGTH_HINT_RE = re.compile(
    r"(?:限制|最多|上限|不超過|不超过)\s*(?:輸入|输入|填寫|填写)?\s*([0-9]+)\s*(?:個|个)?\s*[字字符]"
)
#: 条数类上限措辞（"最多输入10个手机号"）——仅用于说明文字，不影响探针长度
_COUNT_HINT_RE = re.compile(
    r"(?:限制|最多|上限|不超過|不超过)\s*(?:輸入|输入|填寫|填写)?\s*([0-9]+)\s*(?:個|个)?\s*"
    r"(?:手机号|手機號|条|條|附件|附件數|附件数)"
)
_LEADING_NUMBER_RE = re.compile(r"^\s*([0-9]+)")


def probe_value_for(assertion: Mapping[str, Any]) -> tuple[str, str]:
    """为长度/条数类断言合成**可输入的探针值**，返回 ``(值, 说明)``。

    实测问题（CS 规格书）：断言的 ``data`` 是**人类可读描述**而不是输入值——
    `{"kind": "max_length", "data": "500字", "expect": "accept"}` /
    `{"data": "501字", "expect": "reject"}`。执行器原来直接 `fill(data)`，
    于是往输入框里输入了字面量 `500字`（3 个字符）：
    **边界根本没有被验证**，却会产出"通过/未验证"的结论（典型的假验证）。
    这里按文档上限换算成真实长度的样本：accept → N 字符，reject → N+1 字符。
    """
    kind = str(assertion.get("kind") or "")
    if kind not in {"max_length", "max_count"}:
        return "", ""
    data = str(assertion.get("data") or "")
    rule = str(assertion.get("rule") or "")
    expect = str(assertion.get("expect") or "")

    # data 里的数字是**样本长度描述**（"500字"= 500 字符的样本、"501字"= 超限样本、
    # "11个手机号"= 11 个手机号的样本），不是上限；上限来自 rule（"限制500個字"）。
    sample: int | None = None
    match = _LEADING_NUMBER_RE.match(data)
    if match:
        sample = int(match.group(1))

    limit: int | None = None
    for pattern in (_LENGTH_HINT_RE, _COUNT_HINT_RE):
        for source in (rule, data):
            hit = pattern.search(source)
            if hit:
                limit = int(hit.group(1))
                break
        if limit is not None:
            break

    if sample:
        target = sample
    elif limit and expect.startswith("accept"):
        target = limit
    elif limit and expect.startswith("reject"):
        target = limit + 1
    else:
        return "", ""
    note = (
        f"样本 {target} 字符（文档上限 {limit}）" if limit
        else f"样本 {target} 字符"
    )
    return "A" * target, note


#: 默认（内置）表单定位器表 → **只允许它所属的模板**使用。
#:
#: 反例（实测 2026-09-29 限购批次）：新业务模板在没有自身定位器的情况下回退到内置表，
#: 执行器会去操作内置表里的字段（如"请输入手机号码"），而目标页面根本没有该控件 →
#: 定位等待超时、整批 unverified。因此"用哪张定位器表"必须是**显式决定**：
#:   1) 子功能 ``custom_config.field_locators``（业务侧配置，推荐且零代码）；
#:   2) 本模板自带的定位器表（登记在下表中）；
#:   都没有 → **不生成表单检查**，断言如实留在 ``pending_locator``。
#: 本映射不含任何"项目/客户"信息，只按模板键登记平台自带的表。
TEMPLATE_FORM_LOCATORS: dict[str, dict[str, Any]] = {
    "enterprise_storage_recharge": RECHARGE_FORM_LOCATORS,
}


def resolve_form_locators(
    template_key: Any,
    override: Any = None,
    registered: Any = None,
) -> tuple[dict[str, Any] | None, str]:
    """决定本次表单检查用哪张定位器表，返回 ``(定位器表, 来源)``。

    来源取值（供审计写进 ``generation_params``），**优先级从具体到通用**：
    - ``sub_function_override``：子功能配置了 ``custom_config.field_locators``（最具体）；
    - ``project_registration``：项目内按模板键注册的表（P6 数据化注册，可在线维护、跨子功能复用）；
    - ``template_default``：平台内置表（老项目零改动兜底）；
    - ``none``：都没有 → 不生成表单检查（**不回退到其它业务的表**）。
    """
    if isinstance(override, dict) and override.get("fields"):
        return override, "sub_function_override"
    if isinstance(registered, dict) and registered.get("fields"):
        return registered, "project_registration"
    table = TEMPLATE_FORM_LOCATORS.get(str(template_key or ""))
    if isinstance(table, dict) and table.get("fields"):
        return table, "template_default"
    return None, "none"


#: 注册行合并成定位器表时附带的**来源元信息**键（不是定位器配置本身）。
#: 预览接口据此把它们从"定位器键"列表里剔除，避免看起来像多配了定位器。
REGISTRATION_META_KEYS: tuple[str, ...] = ("template_key", "registration_id", "source_ref")

#: ``extra_config`` 里**不是定位器**的键：它们是**行为配置**（目前是规则级负路径场景）。
#: 预览接口的"定位器键"列表必须把这些也剔掉——把 ``negative_scenarios`` 列进
#: ``locator_keys`` 会让配置方以为多注册了一个叫这个名字的定位器（实测于
#: ``test_resolve_preview_exposes_negative_scenarios_and_rejects``）。
#: 执行侧做同类剔除时必须用**这个**集合，否则两端又会分叉。
NON_LOCATOR_CONFIG_KEYS: tuple[str, ...] = REGISTRATION_META_KEYS + ("negative_scenarios",)


def merge_registered_locators(row: Any) -> dict[str, Any]:
    """把 ``web_form_templates`` 注册行还原成**完整**定位器表。

    实测（TR-163，2026-09-30）：只登记 ``fields`` 时，执行器在打开表单处报
    ``FORM_PLAN.locators.form_button is not iterable`` 整条运行硬失败，且从报错
    看不出是"注册时少带了 ``extra_config``"造成的。

    故这里把 ``extra_config`` 的其余键与 ``fields`` 合并；
    ``extra_config`` 里的 ``fields`` 一律忽略（**字段表只有一处真相**）。

    本函数是执行链路与预览接口的**唯一实现**——两处若各写一份，很容易出现
    "预览说没问题、执行却崩"的分叉（TR-163 正是这么发生的）。
    """
    extra = getattr(row, "extra_config", None)
    extra = extra if isinstance(extra, dict) else {}
    table: dict[str, Any] = {k: v for k, v in extra.items() if k != "fields"}
    table["fields"] = row.fields
    return {
        **table,
        "template_key": getattr(row, "template_key", None),
        "registration_id": str(getattr(row, "id", "")) or None,
        "source_ref": getattr(row, "source_ref", None),
    }


def build_form_plan_for_template(
    template_key: Any,
    manifest: dict[str, Any],
    override: Any = None,
    registered: Any = None,
    *,
    allow_upload: bool = False,
) -> tuple[dict[str, Any] | None, str]:
    """按**显式来源**生成表单检查计划，返回 ``(计划, 来源)``。

    来源见 :func:`resolve_form_locators`；无来源时返回 ``(None, "none")``，
    **不会**跨业务回退到别的定位器表。
    """
    locators, source = resolve_form_locators(template_key, override, registered)
    if not locators:
        return None, source
    return build_form_plan(manifest, locators, allow_upload=allow_upload), source


def _normalize_field_name(name: Any) -> str:
    """字段名归一化：去空白/全角空格/括号后缀（"上传文件（企业充值）"→"上传文件"）。"""
    text = str(name or "")
    text = re.sub(r"[\s\u3000]+", "", text)
    text = re.sub(r"[（(][^）)]*[）)]\s*$", "", text)
    return text.strip()


def _suggest_fields(needle: Any, candidates: Iterable[Any], limit: int = 3) -> list[str]:
    """给"取不到定位器的字段名"找**近似候选**（纯字符串相似度，不含任何业务词）。

    实测动机（2026-09-29 P4 验收）：断言字段与定位器表里的名字差一点（多一个词/后缀不同）时，
    该断言会在**计划期**被静默跳过（只留一个 `无定位器`），既没有运行期现场、
    也只能靠人去比对两张表。这里直接给出候选，让"一次就能改对"。
    """
    target = _normalize_field_name(needle)
    if not target:
        return []
    scored: list[tuple[float, str]] = []
    for candidate in candidates:
        name = str(candidate or "")
        key = _normalize_field_name(name)
        if not key:
            continue
        if key == target:
            score = 1.0
        elif key in target or target in key:
            score = 0.9
        else:  # 双字组重合度（对中文友好，避免引入第三方库）
            pairs = {target[i:i + 2] for i in range(max(len(target) - 1, 1))}
            other = {key[i:i + 2] for i in range(max(len(key) - 1, 1))}
            shared = pairs & other
            score = len(shared) / max(len(pairs), 1)
        if score >= 0.4:
            scored.append((score, name))
    scored.sort(key=lambda item: (-item[0], item[1]))
    return [name for _score, name in scored[:limit]]


def build_form_plan(
    manifest: dict[str, Any],
    locators: dict[str, Any] | None = None,
    *,
    allow_upload: bool = False,
) -> dict[str, Any] | None:
    """把编译清单里的断言映射成**可执行**的表单校验计划。

    只有"字段名能在定位器表里找到"的断言才会进入计划；其余仍留在 pending_locator。
    计划里的检查以**不提交**的客户端校验为主；「期望拒绝」但客户端层无法验证的项
    （submit_only/required_error/input_validation-reject）进入**负路径提交层**重试
    （空必填/非法探针值真实点击提交，口径经用户 2026-09-25 确认）。

    定位器表**必须显式给出**（2026-09-29 复核 P1）：历史实现不传就回退到内置的储值表，
    于是任何未配置定位器的新业务模板都会去操作储值页面的控件（实测 CS Module、
    会员限购商品管理两次同一根因）。现在 ``locators`` 为空 → 直接返回 ``None``
    （调用方据此把断言如实留在 pending_locator，而不是跨业务套用别人的表）。
    生产路径请用 :func:`build_form_plan_for_template` 或 ``resolve_form_locators``。

    归属原则：计划级校验（默认选中/条件显示）**不是无主的**——它验证的就是文档里
    对应字段的规则，因此必须带 ``owners``（用例标识 + 规则号），否则会出现
    "断言执行并通过了，却没有任何用例拿到证据"的证据黑洞（且用例被低报为未执行）。
    """
    loc = locators if isinstance(locators, dict) else {}
    fields = loc.get("fields") or {}
    if not fields:
        return None

    checks: list[dict[str, Any]] = []
    toggle_checks: list[dict[str, Any]] = []   # 顺序上排在字段校验之后
    skipped: list[dict[str, Any]] = []
    assertion_checks = 0          # 由文档断言直接生成的校验数（不含默认值/条件显示）
    field_modes = loc.get("field_modes") or {}
    default_mode = loc.get("default_field_mode") or {}
    upload_fields = loc.get("upload_fields") or {}

    wants = _visibility_wants(manifest)
    default_state = _default_state(loc)
    covered_visibility: set[tuple[str, str, str]] = set()
    covered_defaults: set[tuple[str, str]] = set()

    def owners_for(state: Mapping[str, str], labels: Iterable[Any]) -> list[dict[str, str]]:
        out: list[dict[str, str]] = []
        pairs = [(k, v) for k, v in state.items() if k and v]
        for want in wants:
            key = (want["case_id"], want["field"], want["expect"])
            if key in covered_visibility:
                continue
            if not _label_matches(want["field"], labels):
                continue
            if not _when_matches(want["when"], pairs):
                continue
            covered_visibility.add(key)
            out.append({"case_id": want["case_id"], "rule_id": want["rule_id"]})
        return out

    # 顺序很重要：默认选中必须在任何点击之前观测（点击会改变单选状态），
    # 条件显示切换放最后（它会改变表单模式）。
    for default in loc.get("defaults") or []:
        field = str(default.get("field") or "")
        option = str(default.get("option") or "")
        check: dict[str, Any] = {"type": "default_selected", "field": field,
                                 "option": option, "rule": "默认选中",
                                 # 来源=定位器配置：这类检查由"配置里声明的默认值"驱动，
                                 # 不一定有对应用例断言 → 台账据此与"本该有归属却没有"区分开
                                 "source": "locator_config"}
        owners = _default_wants(manifest, field, option)
        if owners:
            check["owners"] = owners
            for owner in owners:
                covered_defaults.add((owner["case_id"], field))
        checks.append(check)

    # 默认态的条件显示：在任何点击之前，先把"默认态就该可见"的字段判定掉
    for want in wants:
        key = (want["case_id"], want["field"])
        if key in covered_visibility:
            continue
        if not _when_matches(want["when"], [(k, v) for k, v in default_state.items() if v]):
            continue
        covered_visibility.add(key)
        checks.append({"type": "visible_now", "field": want["field"],
                       "expect_labels": [want["field"]], "rule": "条件显示（默认态）",
                       "rule_id": want["rule_id"], "case_id": want["case_id"]})

    # 条件显示切换：把每个切换后的**完整状态**算出来，再归属给条件完全匹配的用例
    for toggle in loc.get("toggles") or []:
        field = str(toggle.get("field") or "")
        option = str(toggle.get("option") or "")
        mode = dict(toggle.get("mode") or {})
        state: dict[str, str] = dict(default_state)
        state.update({k: v for k, v in mode.items() if k and v})
        if field and option:
            state[field] = option
        check = {"type": "visibility", "field": field, "option": option,
                 "expect_labels": toggle.get("expect_labels") or [], "mode": mode,
                 "rule": "条件显示"}
        owners = owners_for(state, check["expect_labels"])
        if owners:
            check["owners"] = owners
        toggle_checks.append(check)

    for case in manifest.get("cases") or []:
        field = case.get("field") or ""
        if not field:
            continue
        is_upload = field in upload_fields
        if field not in fields and not is_upload:
            # 计划期跳过也必须**可诊断**（实测：字段名差一点时断言会静默不执行，
            # 运行期又没有现场 —— 三次验收都卡在这里）。给出用例/规则号、
            # 已声明的字段名与近似候选，一次就能改对。
            skipped.append({
                "field": field,
                "reason": "无定位器",
                "case_id": case.get("identifier") or "",
                "rule_id": case.get("rule_id") or "",
                "kinds": sorted({
                    str(a.get("kind") or "")
                    for a in (case.get("assertions") or []) if a.get("kind")
                }),
                "declared_fields": list(fields.keys())[:20],
                "suggestions": _suggest_fields(field, fields.keys()),
            })
            continue
        for assertion in case.get("assertions") or []:
            kind = assertion.get("kind")
            if kind == "required":
                # 单选组无法 fill()：其"必填"只能在提交时校验 → **显式记为未验证**，
                # 不能静默跳过（否则该用例会因为没有证据反而被算作"全部通过"）。
                if is_upload:
                    checks.append({"type": "submit_only", "field": field,
                                   "rule": assertion.get("rule", "是否必填=是"),
                                   "reason": "上传控件必填只在提交时校验（本版不提交）",
                                   "rule_id": case.get("rule_id") or "",
                                   "case_id": case.get("identifier") or ""})
                    assertion_checks += 1
                    continue
                if (fields.get(field) or {}).get("kind") == "radio":
                    checks.append({"type": "submit_only", "field": field,
                                   "rule": assertion.get("rule", "是否必填=是"),
                                   "reason": "单选组无自由输入：必填只在提交时校验（本版不提交）",
                                   "rule_id": case.get("rule_id") or "",
                                   "case_id": case.get("identifier") or ""})
                    assertion_checks += 1
                    continue
                checks.append({"type": "required_error", "field": field,
                               "rule": assertion.get("rule", "是否必填=是"),
                               "mode": field_modes.get(field, default_mode),
                               "rule_id": case.get("rule_id") or "",
                               # 精确归属：rule_id 会被同字段的多条用例共享（实测 5 个 id 覆盖 14 条），
                               # 只用 rule_id 归属会把兄弟用例误标"未执行"
                               "case_id": case.get("identifier") or ""})
                assertion_checks += 1
            elif kind in {"boundary", "range", "precision", "max_length", "max_count"}:
                probe_value, probe_note = probe_value_for(assertion)
                check: dict[str, Any] = {
                    "type": "input_validation", "field": field,
                    "data": str(assertion.get("data", "")),
                    "expect": assertion.get("expect", ""),
                    "rule": assertion.get("rule", ""),
                    "mode": field_modes.get(field, default_mode),
                    # 归属：用于把执行结果落到**具体用例**上（而非只有锚点用例）
                    "rule_id": case.get("rule_id") or "",
                    "case_id": case.get("identifier") or "",
                }
                if probe_value:
                    # data 是人类可读描述（"500字"）时必须换算成真实输入值，否则边界未被验证
                    check["probe_value"] = probe_value
                    check["probe_note"] = probe_note
                checks.append(check)
                assertion_checks += 1
            elif kind == "enum" and (fields.get(field) or {}).get("kind") == "radio":
                # 单选组的"选项范围"是**只读可验证**的：读选项标签即可判定，
                # 且不存在"输入非法值"的路径（用户根本无法选到不存在的项）。
                checks.append({"type": "radio_options", "field": field,
                               "allowed": assertion.get("allowed") or [],
                               "data": str(assertion.get("data") or ""),
                               "expect": assertion.get("expect", ""),
                               "rule": assertion.get("rule", ""),
                               "rule_id": case.get("rule_id") or "",
                               "case_id": case.get("identifier") or ""})
                assertion_checks += 1
            elif kind == "enum" and (fields.get(field) or {}).get("kind") == "select":
                # 下拉（antd Select）的"选项范围"同样**只读可验证**：展开下拉读选项文本。
                # 注意：Select 的选项渲染在 portal 里（可能挂到 body），执行器需在同一作用域内查。
                checks.append({"type": "select_options", "field": field,
                               "allowed": assertion.get("allowed") or [],
                               "data": str(assertion.get("data") or ""),
                               "expect": assertion.get("expect", ""),
                               "rule": assertion.get("rule", ""),
                               "rule_id": case.get("rule_id") or "",
                               "case_id": case.get("identifier") or ""})
                assertion_checks += 1
            elif kind == "default":
                # 默认选中：若定位器里已有同字段同选项的默认观测项，证据已在
                # default_selected 检查上（归属见 owners），这里不重复、也不算"不支持"
                if (case.get("identifier") or "", field) in covered_defaults:
                    continue
                skipped.append({"field": field,
                                "reason": "默认值须在未点击状态下观测，且未配置该默认观测项"})
            elif kind in {"file_format", "file_size"}:
                if not is_upload:
                    skipped.append({"field": field, "reason": "需上传文件（该字段无上传控件配置）",
                                    "kind": kind})
                    continue
                if not allow_upload:
                    # 默认关闭：附文件可能触发向 UAT 的上传请求（服务端副作用），
                    # 必须显式开启才执行；关闭时**显式登记**，不静默丢弃。
                    skipped.append({"field": field, "kind": kind,
                                    "reason": "需上传文件（allow_upload_checks=false，默认关闭）"})
                    continue
                upload_cfg = upload_fields.get(field) or {}
                expect = str(assertion.get("expect") or "")
                threshold = loc.get("upload_size_limit_bytes")
                ev_kind = "format" if kind == "file_format" else "size"
                samples: list[dict[str, Any]] = []
                if ev_kind == "format":
                    if expect == "accept":
                        # 文档说"仅支持 xlsx、xls、csv"→ 对**可构造有效样本**的格式逐个验证；
                        # 无法构造有效样本的格式（如 BIFF .xls）显式登记为未执行，绝不用假样本充数
                        for fmt in (loc.get("upload_allowed_formats") or ["xlsx"]):
                            if fmt == "xlsx":
                                samples.append(sample_spec(
                                    name="upload_valid.xlsx", mime=XLSX_MIME,
                                    source="workbook_stored", kind="valid_workbook"))
                            elif fmt == "csv":
                                samples.append(sample_spec(
                                    name="upload_valid.csv", mime="text/csv",
                                    source="csv_text", kind="valid_csv"))
                            elif fmt == "xls":
                                samples.append(sample_spec(
                                    name="upload_valid.xls", mime="application/vnd.ms-excel",
                                    source="xls_bytes", kind="valid_xls"))
                            else:
                                reason = (loc.get("upload_unsupported_sample_formats") or {}).get(fmt)
                                skipped.append({
                                    "field": field, "kind": kind, "format": fmt,
                                    "reason": "无可用的有效样本：%s" % (reason or "未支持构造"),
                                })
                    else:
                        samples.append(sample_spec(
                            name="upload_bad.exe", mime="application/octet-stream",
                            source="bad_extension", byte_size=256,
                            kind="disallowed_extension"))
                else:
                    # 大小：阈值字节数已由用户确认（1M = 1,048,576）→ 按 N−1 / N / N+1 三个边界样本验证。
                    # 未确认时**不自行假设**，改用明显超大样本并标注"临界点未验证"。
                    confirmed = isinstance(threshold, int) and threshold > 0
                    if confirmed and expect == "accept":
                        samples.append(sample_spec(name="upload_%d.xlsx" % (threshold - 1),
                                                   mime=XLSX_MIME, source="workbook_stored",
                                                   byte_size=threshold - 1, kind="boundary_minus",
                                                   threshold_confirmed=True))
                        samples.append(sample_spec(name="upload_%d.xlsx" % threshold,
                                                   mime=XLSX_MIME, source="workbook_stored",
                                                   byte_size=threshold, kind="boundary",
                                                   threshold_confirmed=True))
                    elif confirmed:
                        samples.append(sample_spec(name="upload_%d.xlsx" % (threshold + 1),
                                                   mime=XLSX_MIME, source="workbook_stored",
                                                   byte_size=threshold + 1, kind="boundary_plus",
                                                   threshold_confirmed=True))
                    elif expect == "reject":
                        samples.append(sample_spec(name="upload_oversize.xlsx", mime=XLSX_MIME,
                                                   source="workbook_stored",
                                                   byte_size=2 * 1024 * 1024,
                                                   kind="oversize_obvious",
                                                   threshold_confirmed=False))
                    else:
                        samples.append(sample_spec(name="upload_valid.xlsx", mime=XLSX_MIME,
                                                   source="workbook_stored",
                                                   kind="valid_workbook",
                                                   threshold_confirmed=False))
                for sample in samples:
                    checks.append({"type": "upload_file", "field": field,
                                   "row_label": upload_cfg.get("row_label") or "上传文件",
                                   "mode": upload_cfg.get("mode") or {},
                                   "data": str(assertion.get("data", "")),
                                   "expect": expect,
                                   "evidence_kind": ev_kind,
                                   "sample": sample,
                                   "rule": assertion.get("rule", ""),
                                   "rule_id": case.get("rule_id") or "",
                                   "case_id": case.get("identifier") or ""})
                    assertion_checks += 1
            elif kind == "visibility":
                # 条件显示由 visible_now / toggles 统一执行；能归属的已经归属，
                # 剩下的（如"不满足条件时应隐藏"）必须显式登记，绝不静默丢弃
                key = (case.get("identifier") or "", str(assertion.get("field") or field),
                       str(assertion.get("expect") or ""))
                if key in covered_visibility:
                    continue
                skipped.append({"field": field,
                                "reason": "条件显示组合未在计划中构建：%s" % (
                                    str(assertion.get("when") or "")[:48])})
            else:
                # 兜底：任何未支持的断言类型都必须显式登记，绝不静默丢弃
                # （否则会出现"既没执行、也没报告为未执行"的黑洞）
                skipped.append({"field": field,
                                "reason": f"本版不支持类型 {kind or '(none)'}"})

    checks.extend(toggle_checks)

    # 规则级负路径场景（P1-3 阶段 B）：与字段级检查**互相独立**——场景挂在"业务规则"上，
    # 不要求有对应字段定位器；配置由注册表 ``extra_config.negative_scenarios`` 提供。
    negative_scenarios, negative_scenario_rejects = parse_negative_scenarios(
        loc.get("negative_scenarios")
    )

    if not checks and not negative_scenarios:
        # 没有可执行检查时**不返回 None**：那样会把 skipped 现场一并丢掉
        #（实测：断言字段名与定位器表差一点 → 全部跳过 → 既没有 [plan-skip] 行，
        #  资产审计里也没有 skipped_assertions，只剩一句"无定位器"无从下手）。
        # 这里返回一个 executable=False 的计划：调用方据此**不注入执行器**（避免为 0 条检查
        # 打开表单、把环境问题变成硬失败），但审计与运行期现场仍然完整。
        if not skipped and not negative_scenario_rejects:
            return None
        return {"locators": loc, "checks": [], "skipped": skipped,
                "check_count": 0, "skipped_count": len(skipped),
                "assertion_check_count": 0, "executable": False,
                "negative_scenarios": [], "negative_scenario_rejects": negative_scenario_rejects}
    return {"locators": loc, "checks": checks, "skipped": skipped,
            "check_count": len(checks), "skipped_count": len(skipped),
            "assertion_check_count": assertion_checks, "executable": True,
            # 只有场景、没有字段级检查时同样可执行：调用方据此注入执行器（见上面分支口径）
            "negative_scenarios": negative_scenarios,
            "negative_scenario_rejects": negative_scenario_rejects}


#: 只读表单校验执行器（TS）。占位符 @@FORM_PLAN@@ 由 render_form_executor 替换。
_FORM_EXECUTOR_TS = r"""
  // ===== 编译执行：表单规则断言（客户端层只读；「期望拒绝」未验证项进负路径提交层，2026-09-25 用户确认）=====
  // 编译断言数量随文档规则增长，需放宽单测超时（默认 30s 会中断）
  // rev71：全量批次（CS 96 条）单 test 断言执行实测需要 ~10 分钟，600s 不够
  //（TR-105 在 Member 定位等待上吃满 600s）。放宽到 20 分钟。
  test.setTimeout(1800000);
  const FORM_PLAN = @@FORM_PLAN@@;
  const FORM_PHONE = process.env.PR2_STORAGE_ACCOUNT_NO || '@@DEFAULT_PHONE@@';
  //: 计划期被跳过的断言（例如字段在定位器表里找不到）**同时**输出可机读现场：
  //: 这类断言不会进入执行，运行期不会有 [observation]；没有这一行就只能看到
  //: summary 里的 `skipped_by_reason: {无定位器: 1}`，无法判断该怎么改配置。
  for (const skip of (FORM_PLAN.skipped || []).slice(0, 20)) {
    console.log('[plan-skip] ' + JSON.stringify(skip));
  }
  type CheckOutcome = 'pass' | 'unverified' | 'blocked';
  const formResults: Array<{name: string; outcome: CheckOutcome; observed: string}> = [];
  //: 当前检查所属用例的规则号与用例标识（由循环逐条设置），用于把结果落到具体用例上
  let currentRuleId = '';
  let currentCaseId = '';
  //: 检查来源（如 locator_config=来自定位器配置）——台账据此把"配置驱动、本就没有对应用例"
  //: 与"本该有归属却缺失"区分开，避免每次运行都报一条假告警。
  let currentSource = '';
  const rec = (name: string, outcome: CheckOutcome, observed: string) => {
    formResults.push({name, outcome, observed, ruleId: currentRuleId, caseId: currentCaseId});
    console.log('COMPILED_ASSERTION ' + JSON.stringify({
      name, outcome, observed, rule_id: currentRuleId, case_id: currentCaseId,
      source: currentSource,
    }));
  };
  //: 一条计划级检查可能同时是**多条用例**的证据（例如"默认选中=企业储值充值"
  //  同时验证了变动类型用例的默认值 + 上传文件用例的前提条件）。此时按 owner 逐条输出，
  //  避免出现"执行了并通过了，却没有任何用例拿到证据"的黑洞。
  const ownersOf = (check: any): Array<{case_id: string; rule_id: string}> => {
    const list = Array.isArray(check.owners) ? check.owners : [];
    if (list.length) return list;
    return [{case_id: check.case_id || '', rule_id: check.rule_id || ''}];
  };
  const recAll = (check: any, name: string, outcome: CheckOutcome, observed: string) => {
    currentSource = String((check && check.source) || '');
    for (const owner of ownersOf(check)) {
      currentRuleId = owner.rule_id || '';
      currentCaseId = owner.case_id || '';
      rec(name, outcome, observed);
    }
  };

  //: P4：定位失败的「确定性现场」——不依赖 Playwright 报错快照（复核实测：快照的
  //: 页归属可能错位，URL 显示 A 应用、快照却是 B 页面，按图索骥会被带偏）。
  //: 定位落空时把「试过的定位器 + 当前作用域可见控件清单（tag/type/placeholder/
  //: maxlength/aria/text，截断脱敏）+ page/frame 清单 + activeElement」落成一行
  //: [observation] JSON，排障一次定位，不需要复跑。零业务词。
  async function collectLocatorObservation(modal: any, tried: string[]): Promise<any> {
    const observation: any = { tried, pages: [], frames: [], active: null, controls: [] };
    try {
      const pgs = page.context().pages();
      for (let i = 0; i < pgs.length; i += 1) {
        observation.pages.push({
          index: i,
          url: String(pgs[i].url() || '').slice(0, 80),
          title: String(await pgs[i].title().catch(() => '') || '').slice(0, 40),
        });
      }
    } catch { /* pages 清单不可用时留空 */ }
    try {
      const frs = page.frames();
      for (let i = 0; i < frs.length; i += 1) {
        observation.frames.push({ index: i, url: String(frs[i].url() || '').slice(0, 80) });
      }
    } catch { /* frames 清单不可用时留空 */ }
    try {
      observation.active = await page.evaluate(() => {
        const el = document.activeElement as any;
        if (!el || el === document.body) return null;
        const cut = (s: any, n: number) => String(s == null ? '' : s).slice(0, n);
        return {
          tag: el.tagName, type: cut(el.getAttribute && el.getAttribute('type'), 20),
          placeholder: cut(el.getAttribute && el.getAttribute('placeholder'), 40),
          aria: cut(el.getAttribute && el.getAttribute('aria-label'), 40),
        };
      }).catch(() => null);
    } catch { /* activeElement 不可用时留空 */ }
    const dumpFn = (root: any, limit: number) => {
      const cut = (s: any, n: number) => {
        const t = String(s == null ? '' : s);
        return t.length > n ? t.slice(0, n) + '…' : t;
      };
      const els = Array.from(root.querySelectorAll('input, textarea, select, button, [contenteditable="true"]'));
      const out: any[] = [];
      for (const el of els as any[]) {
        if (out.length >= limit) break;
        const visible = !!(el.offsetWidth || el.offsetHeight) && el.getClientRects().length > 0;
        if (!visible) continue;
        const row: any = {
          tag: String(el.tagName || '').toLowerCase(),
          type: cut(el.getAttribute && el.getAttribute('type'), 20),
          placeholder: cut(el.getAttribute && el.getAttribute('placeholder'), 40),
          aria: cut(el.getAttribute && el.getAttribute('aria-label'), 40),
        };
        const ml = typeof el.maxLength === 'number' && el.maxLength >= 0 ? el.maxLength : undefined;
        if (ml !== undefined) row.maxLength = ml;
        if (String(el.tagName || '').toLowerCase() === 'button') row.text = cut((el.textContent || '').trim(), 20);
        out.push(row);
      }
      return out;
    };
    try {
      if (modal && (await modal.count().catch(() => 0))) {
        observation.controls = await modal.evaluate(dumpFn, 30).catch(() => []);
      }
      if (!observation.controls.length) {
        observation.controls = await page.evaluate(dumpFn, 30).catch(() => []);
      }
    } catch { /* 控件清单不可用时留空 */ }
    return observation;
  }
  //: 定位落空统一出口：采集现场并输出一行 JSON（供台账/排障直接消费）。
  async function reportLocatorMiss(where: string, check: any, modal: any): Promise<void> {
    const def = (FORM_PLAN.locators.fields || {})[(check && check.field) || ''] || {};
    const tried = [
      JSON.stringify({ locator: 'placeholder_hint', field: check.field, kind: def.kind || '', hint: def.placeholder_hint || '' }),
      JSON.stringify({ locator: 'form_item_label', field: check.field }),
    ];
    const observation = await collectLocatorObservation(modal, tried);
    console.log('[observation] ' + JSON.stringify({
      where, field: (check && check.field) || '', rule_id: (check && check.rule_id) || '',
      case_id: (check && check.case_id) || '', ...observation,
    }));
  }
  const anyScope = appScope as any;

  //: rev71e：单项检查的**操作级超时**。实测 TR-106：required_error:Member 的 fill
  //: 在 frame 解析阶段卡满整个用例预算（1200s），级联把后续 7 项全部拖成 blocked。
  //: 任何 fill/click/blur 超 30s 即按当前检查记 blocked（带诊断），不再拖垮整批。
  const OP_T = 30000;
  //: blocked 现场诊断：列出上下文全部 page 的 URL 与 iframe 数量——
  //: 用于定位"作用域根在另一个 page（CRM 新开页签）导致 frame 永不解析"这类跨页问题。
  async function pageDiag(pg: any): Promise<string> {
    try {
      const pages: any[] = pg.context().pages();
      const parts: string[] = [];
      for (let i = 0; i < pages.length; i++) {
        const p = pages[i];
        let desc = `#${i} ${String(p.url()).slice(0, 90)}`;
        try { desc += ` iframes=${await p.locator('iframe').count()}`; } catch { desc += ' iframes=?'; }
        try { if (p.isClosed()) desc += ' [closed]'; } catch { /* ignore */ }
        parts.push(desc);
      }
      return parts.join(' ;; ');
    } catch (e: any) {
      return `diag-failed: ${String(e).slice(0, 80)}`;
    }
  }

  // 打开表单：**由定位器表的 flow 驱动**，不再硬编码储值弹窗流程。
  // - recharge_modal（默认）：先按手机号筛选 → 点「企业充值/退款」→ 等弹窗
  // - 其他（如 CS 的整页 Create 表单）：直接点 form_button → 等页面容器可见
  async function openForm(pg: any = page, scope: any = anyScope) {
    const flow = String(FORM_PLAN.locators.flow || 'recharge_modal');
    // T-04 修复（2026-10-07，实测 TR-172 / 单号 jf-20260122392-01-2963）：
    // 交互与定位都发生在 `scope` 所属文档，但 `direct_url` 原先固定导航 `pg`。
    // 当应用在**独立标签页**时（如"门户卡片 → 新开标签页"的交易云），`pg` 与 `scope` 属不同文档：
    // 于是"导航到 A 页、却在 B 页等元素" ⇒ 确定性 15s 超时；且失败快照取自 `pg`（显示页面是对的），
    // 极易被误判成被测系统缺陷。
    // 规则：scope 本身是 Page（有 goto）→ direct_url 与后续等待都用它；
    //       scope 是 FrameLocator（CS 的 CRM iframe 流程）→ 保持原样用 pg，不动既有可用路径。
    // 可用 `direct_url_page: "primary" | "app"` 显式覆盖（缺省按上述规则自动判定）。
    const scopePage = String((FORM_PLAN.locators as any).direct_url_page || '') === 'primary'
      ? pg
      : ((typeof (scope as any).goto === 'function') ? scope : pg);
    if (flow === 'recharge_modal' && FORM_PLAN.locators.filter_phone_placeholder) {
      const phoneInput = scope.getByPlaceholder(FORM_PLAN.locators.filter_phone_placeholder).first();
      await phoneInput.fill(FORM_PHONE, {timeout: OP_T});
      await scope.getByRole('button', {name: FORM_PLAN.locators.search_button}).first().click({timeout: 15000});
      await pg.waitForTimeout(3500);
    }

    let opened = false;

    // 可选：direct_url（rev73：RetroClaim/GoodWill 等独立整页表单）——
    // 直接驱动 CRM iframe 跳到目标 hash 路由（免菜单逐层导航），随后按 modal 等待渲染。
    if ((FORM_PLAN.locators as any).direct_url) {
      const frames = scopePage.frames();
      const fr = frames.find((f: any) => f.parentFrame() &&
        /mkt\.uat|eatizen|saas-/.test(f.url())) || null;
      const base = fr ? fr.url() : scopePage.url();
      let target = String((FORM_PLAN.locators as any).direct_url);
      if (target.startsWith('#')) {
        const u = new URL(base);
        target = u.origin + u.pathname + target;
      }
      if (fr) {
        await fr.goto(target, {timeout: 30000, waitUntil: 'domcontentloaded'}).catch(() => undefined);
      } else {
        await scopePage.goto(target, {timeout: 30000, waitUntil: 'domcontentloaded'}).catch(() => undefined);
      }
      await scopePage.waitForTimeout(5000);
      // T-04：把"导航所用页面"与"定位所在页面"同时留证，避免下次再被快照误导。
      console.log('DIRECT_URL_DIAG ' + JSON.stringify({
        target: String(target).slice(0, 130),
        navigated_page: String(scopePage.url()).slice(0, 130),
        primary_page: String(pg.url()).slice(0, 130),
        same_page: scopePage === pg,
        scope_kind: (typeof (scope as any).goto === 'function') ? 'Page' : 'FrameLocator',
      }));
      opened = true;
    }

    // 可选：list_row_link（rev79：timeline 子页流程从列表点首行链接进详情——
    // 行链接文本是工单标题（不固定），无法用语义入口名匹配）
    //
    // rev96：**必须等数据行真的渲染出来再点**。实测（2026-10-13 CS timeline）：
    // 列表就绪判据只判"表格/Total 可见"（实测 130ms 即通过），此时 tbody 可能还没有
    // 数据行 ⇒ `rowLink.count()` = 0 ⇒ 静默不点 ⇒ 后续 Add 在列表页找不到 ⇒
    // `opened=false` 硬失败「无法打开表单」。
    // 对照证据：同一 `timeline_subpage` flow 的 28 条资产里 24 条正常完成，
    // 只有 4 条报该错（偶发）；只读探针证明选择器链路本身可用
    //（列表 20 行 / Total 105 → 点行链接进 `#/cs/case/edit` → Add 按钮可见 → 菜单含 Note）。
    // rev96b：留证行改读 **frame 级 URL**。实测（TR-275/280/282）：CS 的 CRM 跑在 iframe 内，
    // `pg.url()` / `scopePage.url()` 恒为外层企业控制台 URL，于是 `navigated` **恒 false**
    //—— 无论表单到底开没开。这条"看起来像证据、实则永远一个样"的行会误导排障，
    // 因此改为遍历 `pg.frames()` 取带 CS 路由的那一帧（`#/cs/…`）来做前后对比。
    // 纯诊断改动：不改变任何交互，只改这行 console.log 的取值来源。
    if ((FORM_PLAN.locators as any).list_row_link) {
      const appFrameUrls = (): string[] => {
        try {
          return (pg.frames() as any[])
            .map((f: any) => String(f.url()))
            .filter((u: string) => /(#\/cs\/|\/cs\/case\/|case\/(list|edit))/.test(u))
            .map((u: string) => u.slice(0, 150));
        } catch { return []; }
      };
      const beforeFrames = appFrameUrls();
      for (let attempt = 1; attempt <= 3; attempt++) {
        try {
          await scope.locator('.ant-table-tbody tr:has(a)').first()
            .waitFor({state: 'visible', timeout: attempt === 1 ? 10000 : 5000});
        } catch { /* 本轮仍未见到带链接的数据行 → 下一轮重试 */ }
        try {
          const rowLink = scope.locator('.ant-table-tbody tr:first-child a, .ant-table-row a').first();
          if (await rowLink.count()) {
            await rowLink.click({timeout: 12000});
            await pg.waitForTimeout(4000);
            break;
          }
        } catch { /* 行链接点击失败由后续表单打开失败统一兜底 */ }
        await pg.waitForTimeout(1500);
      }
      // 现场留证：进入详情页会把 frame 路由从 `#/cs/case/list` 换成 `#/cs/case/edit?id=…`。
      // 下次再出现"无法打开表单"时，这一行能直接区分"没进详情"与"进了但 Add 找不到"。
      const afterFrames = appFrameUrls();
      console.log('[rowlink] ' + JSON.stringify({
        before: beforeFrames, after: afterFrames,
        navigated: JSON.stringify(beforeFrames) !== JSON.stringify(afterFrames),
        reached_edit: afterFrames.some((u) => /case\/edit/.test(u)),
        frames_total: (() => { try { return (pg.frames() as any[]).length; } catch { return -1; } })(),
      }));
    }

    // 可选：toolbar_modal（O10/rev98：工具条按钮 → 弹窗字段 → 提交——
    // 与 create_page 同构但**入口不同**：必须先选中列表行，工具条动作才有目标。
    // 形态实例（67197，WSF-48576977/cs-case-email）：
    //   勾选列表行 → 工具条「Email a Link」→ 弹窗 #emails/#remark → Send → POST /cs/cases/send-email。
    // 新增定位器键（均挂在定位器表上，与 fields/form_button 平级）：
    //   toolbar_button : 工具条按钮名候选数组（button → link → 精确文本三级兜底，同 form_button）；
    //   row_selector   : 数据行选择器（缺省 .ant-table-tbody tr）；
    //   row_pick       : "checkbox"（缺省：勾选行内 input[type=checkbox]，找不到则点行兜底）| "row_click"；
    //   row_index      : 行下标（缺省 0）。
    // 行就绪判据沿用 rev96 教训：必须等数据行**真实渲染**（只判表头/Total 会点到空表）。
    if (flow === 'toolbar_modal') {
      const rowSel = String((FORM_PLAN.locators as any).row_selector || '.ant-table-tbody tr');
      let rowReady = false;
      for (let attempt = 1; attempt <= 3 && !rowReady; attempt++) {
        try {
          await scope.locator(rowSel).first()
            .waitFor({state: 'visible', timeout: attempt === 1 ? 10000 : 5000});
          rowReady = (await scope.locator(rowSel).count()) > 0;
        } catch { /* 本轮仍未见到数据行 → 下一轮重试 */ }
        if (!rowReady) await pg.waitForTimeout(1500);
      }
      if (rowReady) {
        const rowIdx = Math.max(0, Number((FORM_PLAN.locators as any).row_index ?? 0) || 0);
        try {
          if (String((FORM_PLAN.locators as any).row_pick || 'checkbox') === 'row_click') {
            await scope.locator(rowSel).nth(rowIdx).click({timeout: 8000});
          } else {
            // rev98c（实测 TR-329）：**不能直接点 input**——antd 的 checkbox input 被
            // .ant-checkbox-inner 方框覆盖（opacity:0 且在底层），Playwright 命中检测
            // （elementFromPoint 必须是元素或其子孙）永远不过 ⇒ click 超时后被静默吞掉、
            // 行没选上，点 Email a Link 只会弹"请先选择工单"toast。
            // 正解（与 cs_login 实战辅助同思路）：点可见的 .ant-checkbox 容器 →
            // isChecked 自证 → 未选中再用合成 click 兜底（js el.click 不走过顶命中）。
            const rowLoc = scope.locator(rowSel).nth(rowIdx);
            const boxInput = rowLoc.locator('input[type=checkbox]').first();
            if (await boxInput.count()) {
              if (!(await boxInput.isChecked().catch(() => false))) {
                const wrap = rowLoc.locator('.ant-checkbox').first();
                if (await wrap.count()) {
                  await wrap.click({timeout: 8000}).catch(() => undefined);
                }
                if (!(await boxInput.isChecked().catch(() => false))) {
                  await boxInput.evaluate((el: any) => el.click()).catch(() => undefined);
                }
              }
            } else {
              await rowLoc.click({timeout: 8000});
            }
          }
          await pg.waitForTimeout(800);
        } catch { /* 选行失败由后续「表单打不开」统一兜底（如实报错，不装作通过） */ }
      } else {
        console.log(`CS_FORM_DIAG toolbar_modal: 未等到数据行（row_selector=${rowSel}）`);
      }
      for (const name of ((FORM_PLAN.locators as any).toolbar_button || [])) {
        try {
          const btn = scope.getByRole('button', {name: new RegExp(name)}).first();
          if (await btn.count()) {
            await btn.click({timeout: 12000});
            opened = true;
            break;
          }
        } catch { /* 尝试下一个候选名 */ }
      }
      if (!opened) {
        for (const name of ((FORM_PLAN.locators as any).toolbar_button || [])) {
          try {
            const lnk = scope.getByRole('link', {name: new RegExp(name)}).first();
            if (await lnk.count()) {
              await lnk.click({timeout: 12000});
              opened = true;
              break;
            }
          } catch { /* 尝试下一个候选名 */ }
        }
      }
      if (!opened) {
        for (const name of ((FORM_PLAN.locators as any).toolbar_button || [])) {
          try {
            const txt = scope.getByText(new RegExp(`^\\s*${name}\\s*$`)).first();
            if (await txt.count() && await txt.isVisible().catch(() => false)) {
              await txt.click({timeout: 12000});
              opened = true;
              break;
            }
          } catch { /* 尝试下一个候选名 */ }
        }
      }
    }

    // 可选：先进入「详情/编辑」页（timeline 类流程要从列表点进某一单）
    for (const name of (FORM_PLAN.locators.entry_labels || [])) {      try {
        const entry = scope.getByRole('button', {name: new RegExp(name)}).first();
        if (await entry.count()) {
          await entry.click({timeout: 12000});
          await pg.waitForTimeout(3000);
          break;
        }
        const link = scope.getByRole('link', {name: new RegExp(name)}).first();
        if (await link.count()) {
          await link.click({timeout: 12000});
          await pg.waitForTimeout(3000);
          break;
        }
      } catch { /* 尝试下一个入口候选 */ }
    }

    // 说明：form_button 缺失时**不得**直接崩溃（实测 TR-163：注册表只带 fields →
    // `FORM_PLAN.locators.form_button is not iterable` 让整条运行硬失败，且看不出是配置缺失）。
    // 这里容错为"没有候选入口"，由后续 opened 判定给出可读的未验证结论。
    const formButtonNames = FORM_PLAN.locators.form_button || [];
    const toolbarNames = (FORM_PLAN.locators as any).toolbar_button || [];
    for (const name of formButtonNames) {
      try {
        const btn = scope.getByRole('button', {name: new RegExp(name)}).first();
        if (await btn.count()) {
          await btn.click({timeout: 12000});
          opened = true;
          break;
        }
      } catch { /* 尝试下一个候选名 */ }
    }
    // rev73："+ Add" 这类入口是 link/span 而非 button——role 匹配落空时按文本兜底
    if (!opened) {
      for (const name of formButtonNames) {
        try {
          const lnk = scope.getByRole('link', {name: new RegExp(name)}).first();
          if (await lnk.count()) {
            await lnk.click({timeout: 12000});
            opened = true;
            break;
          }
        } catch { /* 尝试下一个候选名 */ }
      }
    }
    if (!opened) {
      for (const name of formButtonNames) {
        try {
          const txt = scope.getByText(new RegExp(`^\\s*\\+?\\s*${name}\\s*$`)).first();
          if (await txt.count() && await txt.isVisible().catch(() => false)) {
            await txt.click({timeout: 12000});
            opened = true;
            break;
          }
        } catch { /* 尝试下一个候选名 */ }
      }
    }
    // rev73：menu_item——点开 Add 下拉后选择具体条目（Timeline 的 Post/Task/Phone Call/Note）
    for (const mi of ((FORM_PLAN.locators as any).menu_item || [])) {
      try {
        const item = scope.locator(
          `.ant-dropdown-menu-item:has-text("${mi}"), .ant-dropdown-menu-submenu-title:has-text("${mi}")`,
        ).first();
        if (await item.count()) {
          await item.click({timeout: 10000});
          await pg.waitForTimeout(3000);
          break;
        }
      } catch { /* 尝试下一个菜单项 */ }
    }
    // 表单打不开属定位器/页面结构问题 → 硬失败（不装作通过）
    expect(
      opened,
      (formButtonNames.length + toolbarNames.length) === 0
        ? `无法打开表单：定位器未配置表单入口（form_button/toolbar_button 均为空）——请检查该模板注册/配置，`
          + `而不是把结论算成"页面没有入口"（flow=${flow}）`
        : `无法打开表单（flow=${flow}，定位器可能失效）`,
    ).toBeTruthy();
    const modal = scope.locator(FORM_PLAN.locators.modal).first();
    try {
      await modal.waitFor({state: 'visible', timeout: 15000});
    } catch (e) {
      // T-04：原报错只有"等待某个选择器超时"，而失败快照取自 `pg`——
      // 当导航页与定位页不是同一个文档时，快照会显示"页面明明是对的"，误导排障。
      // 这里把两个页面的 URL 与上下文页面清单一并写进错误信息。
      const scopeUrl = (typeof (scope as any).goto === 'function')
        ? String((scope as any).url())
        : 'FrameLocator(非 Page)';
      throw new Error(
        `无法打开表单容器：等待 ${String(FORM_PLAN.locators.modal)} 可见超时（flow=${flow}，定位器可能失效）。`
        + `定位所在文档=${scopeUrl.slice(0, 130)}；`
        + `direct_url 导航所用页面=${String(scopePage.url()).slice(0, 130)}；`
        + `测试主页面 pg=${String(pg.url()).slice(0, 130)}；`
        + `上下文页面=[${await pageDiag(pg)}]`,
      );
    }
    await pg.waitForTimeout(700);
    console.log(`CS_FORM_DIAG flow=${flow} pg=${String(pg.url()).slice(0, 90)} pages=[${await pageDiag(pg)}]`);
    return modal;
  }

  //: 读取控件当前值。**实测坑**：contenteditable 上的 `inputValue()` 不可靠
  //: （合成页实测返回长度 226，而 textContent 是 500），富文本必须读 textContent。
  //: 同 fillableDeep 的理由：先 `count()` 做无等待判定——控件不存在时不要挂到默认超时。
  async function controlValue(locator: any): Promise<string> {
    try {
      if ((await locator.count().catch(() => 0)) === 0) return '';
      const tag = String(await locator.evaluate((el: any) => (el.tagName || '').toLowerCase()));
      if (tag === 'input' || tag === 'textarea') {
        return String(await locator.inputValue({timeout: 5000}));
      }
      return String(await locator.evaluate((el: any) => el.textContent || ''));
    } catch {
      return '';
    }
  }

  function fieldInput(modal: any, field: string, tag: string) {
    return modal.locator(`.ant-form-item:has-text("${field}") ${tag}`).first();
  }

  //: rev71：定位器表里 9/24 probe 实测的 selector_hint（如 CS New Case 表单的
  //: #memberLabel/#title/#brand）是唯一可信的控件定位——该表单不是标准
  //: .ant-form-item 结构，has-text 匹配永远落空，fill 会等满整个用例预算
  //:（实测 TR-105 Member 项吃满 600s 后级联 blocked 8 项）。hint 优先，缺失时回退。
  //: 定位覆盖**只有 `selector_hint` 生效**（值就是 CSS 选择器）。
  //:   * `placeholder_hint` 仅作诊断信息（会出现在 [observation] 里），**不参与定位**——
  //:     实测 2026-09-29：把 placeholder_hint 改成不存在的文本，断言照样通过，
  //:     因为 kind=input 走的是 `.ant-form-item:has-text("<字段名>") input` 兜底路径。
  //:   * 想制造/修复"定位不到"：改 `selector_hint`（给个不匹配的选择器）或改 `kind`
  //:     （例如页面是普通 input 却写成 richtext → 只有在 [contenteditable] 里找 → 落空）。
  function hintSel(def: any): string | null {
    const h = String((def || {}).selector_hint || '').trim();
    return h ? h : null;
  }
  function hintLocator(modal: any, def: any) {
    const h = hintSel(def);
    return h ? modal.locator(h).first() : null;
  }

  //: rev71f：hint 元素未必是可输入控件——CS 表单的 Member 字段 hint 是 `#memberLabel`
  //:（实测 TR-106/TR-107：那是标签/容器 div，fill 等待 editability 永不满足，
  //: 无超时版把整批拖死）。可输入性判定 + 自动下钻找内部 input/textarea/contenteditable。
  //:
  //: 2026-09-29（TR-154 实测）：`locator.evaluate(fn, {timeout: 5000})` 的第二个参数是
  //: **传给页面函数的实参**，不是超时选项；而 evaluate 会按默认超时（本用例 30 分钟）等元素出现。
  //: 于是"selector_hint 写成不存在的选择器"这种最常见的配置错误，会把作业一直挂到 runner 的
  //: 600s 上限，**stdout 全空**（连 [observation] 都来不及输出）——现场反而丢了。
  //: 现在先做**无等待的存在性判定**：count() 不自动等待，为 0 立即返回，
  //: 由调用方按"未定位到输入框"记 unverified 并输出 [observation]。
  const FILLABLE = ['input', 'textarea'];
  async function fillableDeep(loc: any): Promise<any> {
    try {
      if ((await loc.count().catch(() => 0)) === 0) return loc;
      const tag = String(await loc.evaluate((el: any) => (el.tagName || '').toLowerCase()).catch(() => ''));
      const editable = await loc.getAttribute('contenteditable', {timeout: 3000}).catch(() => null);
      if (FILLABLE.includes(tag) || editable === 'true') return loc;
      const inner = loc.locator('input:not([type=hidden]), textarea, [contenteditable="true"]').first();
      if (await inner.count()) return inner;
    } catch { /* 保持原定位器，由调用方按 blocked 处理 */ }
    return loc;
  }

  //: 字段控件定位：按定位器表里的 kind 选择 input / textarea / 富文本编辑器。
  //: timeline 的 Subject/Description 常是富文本（[contenteditable]），
  //: 用 input/textarea 定位会落空 → 会被判"未定位到输入框"。
  function fieldControl(modal: any, field: string, def: any) {
    const byHint = hintLocator(modal, def);
    // hint 命中但可能是容器（标签/包装 div）→ 下钻找真正可输入的控件
    if (byHint) return fillableDeep(byHint) as any;
    const kind = String((def || {}).kind || 'input');
    if (kind === 'richtext') {
      const item = modal.locator(`.ant-form-item:has-text("${field}")`).first();
      const editable = item.locator('[contenteditable="true"], .ql-editor, .public-DraftEditor-content').first();
      if (editable) return editable;
      return item.locator('[contenteditable="true"]').first();
    }
    const tag = kind === 'textarea' ? 'textarea' : 'input';
    return fieldInput(modal, field, tag);
  }

  // 单选组必须在**所属表单项**内定位：整页 :has-text 容易命中其它行。
  function radioOption(modal: any, field: string, option: string) {
    const item = modal.locator(`.ant-form-item:has-text("${field}")`).first();
    return item.locator('.ant-radio-wrapper').filter({hasText: option}).first();
  }

  // ---- antd Select（下拉）--------------------------------------------------
  // 实测（CS 工单表单）：Brand/Service Mode 等是 antd Select，控件结构为
  // `.ant-form-item > .ant-select > .ant-select-selector`，已选项文本在
  // `.ant-select-selection-item`，未选中时只有 `.ant-select-selection-placeholder`。
  function selectRoot(modal: any, field: string, def: any) {
    const byHint = hintLocator(modal, def);
    if (byHint) return byHint;
    return modal.locator(`.ant-form-item:has-text("${field}") .ant-select`).first();
  }

  async function selectDisplayValue(modal: any, field: string, def: any) {
    const root = selectRoot(modal, field, def);
    if (!(await root.count())) return '';
    // rev92：兼容 antd v3（.ant-select-selection-selected-value）与 v4/v5（.ant-select-selection-item）
    const item = root.locator('.ant-select-selection-item, .ant-select-selection-selected-value').first();
    if (!(await item.count())) return '';
    return ((await item.innerText({timeout: 5000}).catch(() => '')) || '').replace(/\s+/g, ' ').trim();
  }

  //: 展开下拉并读取选项文本。
  //: 两个实测要点：
  //: 1) 下拉渲染在 portal 里（可能挂到 body），必须用**同一作用域**查询
  //:    （CS 表单在 iframe 内，用 page 查不到）；
  //: 2) 页面上可能同时存在多个 `.ant-select-dropdown`（每个 Select 一个，隐藏态），
  //:    因此只取**当前可见**的那个——实测取"最后一个"会读到隔壁字段的选项。
  //: 未读到选项时重试一次（有的实现再次点击是"展开"而非"收起"）。
  async function selectOptionLabels(pg: any, scope: any, modal: any, field: string, def: any): Promise<string[]> {
    const root = selectRoot(modal, field, def);
    if (!(await root.count())) return [];
    let labels: string[] = [];
    for (let attempt = 0; attempt < 2 && labels.length === 0; attempt += 1) {
      await root.click({ timeout: 8000 }).catch(() => undefined);
      await pg.waitForTimeout(800);
      const dropdowns = scope.locator('.ant-select-dropdown:visible');
      const n = await dropdowns.count().catch(() => 0);
      if (n) {
        const popup = dropdowns.nth(n - 1);
        // rev92：选项类名兼容——本系统为 antd v3（.ant-select-dropdown-menu-item），
        // 旧实现只查 v4/v5 的 .ant-select-item-option，实测展开后读到 0 条选项
        const items = popup.locator('.ant-select-item-option, .ant-select-dropdown-menu-item, [role="option"]');
        const count = Math.min(await items.count().catch(() => 0), 100);
        const seen = new Set<string>();
        for (let i = 0; i < count; i += 1) {
          const text = ((await items.nth(i).innerText({timeout: 5000}).catch(() => '')) || '').replace(/\\s+/g, ' ').trim();
          if (text && !seen.has(text)) { seen.add(text); labels.push(text); }
        }
      }
      if (labels.length === 0) await pg.waitForTimeout(700);
    }
    if (typeof pg.keyboard?.press === 'function') {
      await pg.keyboard.press('Escape').catch(() => undefined);
    }
    await pg.waitForTimeout(300);
    return labels;
  }

  // rev92：DatePicker 必填触发——选一个日期再点清除（都是真实 onChange，会触发
  // antd 校验规则）。兼容 antd v3（.ant-calendar-*）与 v4/v5（.ant-picker-*）。
  // 返回 ''=流程跑完；否则为诊断信息（调用方记 unverified）。
  async function datePickThenClear(pg: any, scope: any, modal: any, field: string, def: any): Promise<string> {
    const root = hintLocator(modal, def) || modal.locator(
      `.ant-form-item:has-text("${field}") .ant-picker, .ant-form-item:has-text("${field}") .ant-calendar-picker`,
    ).first();
    if (!(await root.count())) return '未定位到日期控件';
    await root.click({timeout: OP_T}).catch(() => undefined);
    await pg.waitForTimeout(900);
    const cell = scope.locator(
      '.ant-picker-dropdown:visible .ant-picker-cell-inner, .ant-calendar-picker-container:visible .ant-calendar-date',
    ).first();
    if (!(await cell.count())) return '日历面板未展开';
    await cell.click({timeout: 8000}).catch(() => undefined);
    await pg.waitForTimeout(600);
    const clearIcon = root.locator('.ant-picker-clear, .ant-calendar-picker-clear').first();
    if (!(await clearIcon.count())) return '已选日期但未出现清除图标';
    await root.hover({timeout: 5000}).catch(() => undefined);
    await clearIcon.click({timeout: 8000}).catch(() => undefined);
    await pg.waitForTimeout(400);
    return '';
  }

  async function isRadioChecked(wrap: any): Promise<boolean> {
    if (!(await wrap.count())) return false;
    const cls = (await wrap.getAttribute('class', {timeout: 5000}).catch(() => '')) || '';
    if (cls.includes('ant-radio-wrapper-checked')) return true;
    return await wrap.locator('input[type=radio]').first().isChecked().catch(() => false);
  }

  // 把表单项切到期望模式（金额字段在「批量导入」下不存在，必须先切回「手动输入」）
  async function ensureMode(pg: any, modal: any, mode: any) {
    if (!mode) return;
    for (const field of Object.keys(mode)) {
      const wrap = radioOption(modal, field, mode[field]);
      if (!(await wrap.count())) continue;
      if (!(await isRadioChecked(wrap))) {
        await wrap.click({timeout: 8000});
        await pg.waitForTimeout(600);
      }
    }
  }

  async function itemHasError(pg: any, modal: any, field: string, waitMs = 600): Promise<boolean> {
    const item = modal.locator(`.ant-form-item:has-text("${field}")`).first();
    await pg.waitForTimeout(waitMs);
    // 注意：getAttribute/innerText 在元素不存在时会等满默认 30s —— 全部给短超时，
    // 否则弹窗关闭/页面死亡时单次调用就挂 30s（实测累计吃满 600s 测试超时）。
    if (await item.locator('.ant-form-item-explain-error').count()) return true;
    const cls = String(await item.getAttribute('class', {timeout: 1500}).catch(() => '') || '');
    if (cls.includes('has-error')) return true;
    // antd v3：explain 文案 + has-error 挂在 control 上
    if (await item.locator('.ant-form-explain').count()) return true;
    if (await item.locator('.has-error').count()) return true;
    return false;
  }

  //: 读取某个单选组当前渲染出来的所有选项标签（只读，用于"选项范围"断言）
  async function optionLabels(modal: any, field: string): Promise<string[]> {
    const item = modal.locator(`.ant-form-item:has-text("${field}")`).first();
    const wraps = item.locator('.ant-radio-wrapper');
    const out: string[] = [];
    const n = await wraps.count();
    for (let i = 0; i < n; i++) {
      out.push(((await wraps.nth(i).innerText({timeout: 5000}).catch(() => '')) || '').trim());
    }
    return out;
  }

  async function runFormChecks() {
    // rev101：纯场景计划（零字段检查、无 create 语义场景）**跳过主流程 openForm**——
    // list 类功能点（导出/排序/搜索）根本没有"表单"可开，硬开只会误点工具条按钮
    // （实测：toolbar_modal 的 openForm 会真的点 Export/Email a Link 并发请求）。
    // 场景的 setup 自带入口；create 语义的场景（要用平台已打开的表单）仍预开。
    const planChecks: any[] = FORM_PLAN.checks || [];
    const planScenarios: any[] = FORM_PLAN.negative_scenarios || [];
    const needsCanvas = planScenarios.some(
      (s: any) => String((s && s.target_entry) || 'custom') === 'create');
    let modal: any = null;
    if (planChecks.length > 0 || planScenarios.length === 0 || needsCanvas) {
      modal = await openForm();
    } else {
      console.log('CS_FORM_DIAG scenario-only plan: 跳过主流程 openForm（场景自带入口，'
        + '无 create 语义场景）');
    }
    const fieldDefs: Record<string, any> = (FORM_PLAN.locators || {}).fields || {};
    // 负路径提交队列（rev67）：客户端层拿不到证据的「期望拒绝」断言，进提交层验证。
    // 口径（2026-09-25 用户确认）：只做负路径提交（空必填/非法探针值），正常不会写入合法数据；
    // 被测端若竟然接受 → 记 blocked（疑似缺陷），由末尾硬失败暴露。
    const submitQueue: Array<{check: any; name: string; kind: 'probe' | 'required_empty'; probeValue?: string}> = [];

    // ===== 网络隔离（放行清单 + 默认拒绝）=====
    // 复核意见指出：按"URL 含 upload/oss/file"做特征匹配既漏（不含关键词的地址照样能收文件）
    // 又误（同源含 file 的查询类 POST 会被误拦）。这里改为**默认拒绝**：
    //   * 非 GET 请求：只放行 allowlist 内的主机，其余一律 abort 并逐条记账；
    //   * GET 请求：放行（只读，不产生服务端副作用），但同样记账便于审计。
    // 说明：这是**策略级**收敛，仍不能证明"所有潜在副作用通道"都被封死；报告里必须按此口径描述。
    const isolationBlocked: Array<{method: string; url: string; kind: string}> = [];
    // 安全边界（复核意见 1）：**写请求不再默认放行同源**。
    //   * multipart/form-data：任何情况下阻断（上传传输特征）；
    //   * GET：放行（只读，无副作用）；
    //   * 其他写请求：必须命中**显式放行规则**（host + 可选路径前缀 + 可选方法），否则阻断。
    //     规则为空（默认）＝所有写请求都被阻断，宁可再生环境失败，也不产生未知业务写副作用。
    const policyRules: Array<{host?: string; path_prefix?: string; methods?: string[]}> =
      (FORM_PLAN.upload_policy && FORM_PLAN.upload_policy.allow_rules) || [];
    const legacyHosts: string[] = (FORM_PLAN.upload_policy && FORM_PLAN.upload_policy.allow_hosts) || [];
    const pageHost = (() => {
      try { return new URL(page.url()).host; } catch { return ''; }
    })();
    // 本段可被本地测试原样执行：test_web_upload_local_node.py
    // >>> UPLOAD_ISOLATION_POLICY_BEGIN
    function isolationDecision(method: string, url: string,
                               rules: Array<{host?: string; path_prefix?: string; methods?: string[]}>,
                               legacyHosts: string[], contentType: string): string {
      const verb = String(method).toUpperCase();
      // 1) 文件上传的传输特征：一律阻断（与放行清单无关）
      if (String(contentType).toLowerCase().includes('multipart/form-data')) return 'abort';
      // 2) 只读请求放行
      if (verb === 'GET') return 'continue';
      // 3) 写请求：必须命中显式规则；legacyHosts 仅作为"该主机 GET 放行"的兼容写法
      let parsed: URL | null = null;
      try { parsed = new URL(url); } catch { parsed = null; }
      const host = parsed ? parsed.host : '';
      const path = parsed ? parsed.pathname : '';
      const matched = (rules || []).some((rule) => {
        if (rule.host && !(host && host.endsWith(rule.host))) return false;
        if (rule.path_prefix && !path.startsWith(rule.path_prefix)) return false;
        const methods = (rule.methods || []).map((m) => String(m).toUpperCase());
        if (methods.length && !methods.includes(verb)) return false;
        return true;
      });
      if (matched) return 'continue';
      void legacyHosts;                 // 兼容字段：不再用于放行写请求（只放行 GET）
      return 'abort';
    }
    function blockedKind(url: string, contentType: string, selfHost: string): string {
      const ct = String(contentType).toLowerCase();
      if (ct.includes('multipart/form-data') || /upload|oss|minio|attachment/i.test(url)) {
        return 'upload_blocked';
      }
      try {
        if (new URL(url).host === selfHost) return 'non_upload_api_blocked';
      } catch { /* 非法 URL */ }
      if (/\/api\/|\/rest\/|\/gateway\//i.test(url)) return 'non_upload_api_blocked';
      return 'unknown_request_blocked';
    }
    // <<< UPLOAD_ISOLATION_POLICY_END
    async function installIsolation(target: any) {
      if (!target) return;
      await target.route('**/*', async (route: any) => {
        try {
          const request = route.request();
          const method = request.method().toUpperCase();
          const url = request.url();
          const contentType = String((request.headers() || {})['content-type'] || '');
          if (isolationDecision(method, url, policyRules, legacyHosts, contentType) === 'continue') {
            await route.continue();
            return;
          }
          const kind = blockedKind(url, contentType, pageHost);
          isolationBlocked.push({method, url: url.slice(0, 200), kind});
          console.log('ISOLATION_BLOCKED ' + JSON.stringify(
            {method, url: url.slice(0, 200), kind}));
          await route.abort();
        } catch {
          await route.abort();
        }
      });
    }

    //: 夹具：有效工作簿（Python 侧构造的部件 + STORED 拼装，字节精确到目标大小）。
    const FIX = FORM_PLAN.upload_fixtures || {};
    // 本段可被本地测试原样执行：test_web_upload_local_node.py
    // >>> UPLOAD_ZIP_BUILDER_BEGIN
    const crcTable = (() => {
      const table = new Uint32Array(256);
      for (let n = 0; n < 256; n++) {
        let c = n;
        for (let k = 0; k < 8; k++) c = (c & 1) ? (0xEDB88320 ^ (c >>> 1)) : (c >>> 1);
        table[n] = c >>> 0;
      }
      return table;
    })();
    function crc32(buf: Buffer): number {
      let c = 0xFFFFFFFF;
      for (const byte of buf) c = crcTable[(c ^ byte) & 0xFF] ^ (c >>> 8);
      return (c ^ 0xFFFFFFFF) >>> 0;
    }
    //: 用 STORED（不压缩）拼装 ZIP：先放工作簿必需部件，再用 padding 部件把总字节数补齐到目标值。
    function workbookOfExactBytes(target: number): Buffer | null {
      const parts: Array<{name: string; data: Buffer}> = [];
      for (const name of Object.keys(FIX.workbook_parts || {})) {
        parts.push({name, data: Buffer.from(FIX.workbook_parts[name], 'base64')});
      }
      if (!parts.length) return null;
      const overheadOf = (p: Array<{name: string; data: Buffer}>) => p.reduce(
        (sum, e) => sum + 30 + Buffer.byteLength(e.name, 'utf8') + e.data.length
                     + 46 + Buffer.byteLength(e.name, 'utf8'), 22);
      const padName = 'xl/padding.bin';
      let base = overheadOf(parts) + 30 + Buffer.byteLength(padName, 'utf8')
                 + 46 + Buffer.byteLength(padName, 'utf8');
      if (target < base + 1) return null;
      parts.push({name: padName, data: Buffer.alloc(target - base, 0x20)});
      const chunks: Buffer[] = [];
      const central: Buffer[] = [];
      let offset = 0;                       // 各条目本地头在文件中的偏移（中央目录必须记录）
      for (const entry of parts) {
        const entryOffset = offset;       // 本条目本地头偏移
        const nameBuf = Buffer.from(entry.name, 'utf8');
        const crc = crc32(entry.data);
        const local = Buffer.alloc(30);
        local.writeUInt32LE(0x04034b50, 0);
        local.writeUInt16LE(20, 4);
        local.writeUInt16LE(0, 6);
        local.writeUInt16LE(0, 8);
        local.writeUInt16LE(0, 10);
        local.writeUInt16LE(0, 12);
        local.writeUInt32LE(crc, 14);
        local.writeUInt32LE(entry.data.length, 18);
        local.writeUInt32LE(entry.data.length, 22);
        local.writeUInt16LE(nameBuf.length, 26);
        local.writeUInt16LE(0, 28);
        chunks.push(local, nameBuf, entry.data);
        offset += local.length + nameBuf.length + entry.data.length;
        const dir = Buffer.alloc(46);
        dir.writeUInt32LE(0x02014b50, 0);
        dir.writeUInt16LE(20, 4);
        dir.writeUInt16LE(20, 6);
        dir.writeUInt16LE(0, 8);
        dir.writeUInt16LE(0, 10);
        dir.writeUInt16LE(0, 12);
        dir.writeUInt16LE(0, 14);
        dir.writeUInt32LE(crc, 16);
        dir.writeUInt32LE(entry.data.length, 20);
        dir.writeUInt32LE(entry.data.length, 24);
        dir.writeUInt16LE(nameBuf.length, 28);
        dir.writeUInt16LE(0, 30);
        dir.writeUInt16LE(0, 32);
        dir.writeUInt16LE(0, 34);
        dir.writeUInt16LE(0, 36);
        dir.writeUInt32LE(0, 38);
        dir.writeUInt32LE(entryOffset, 42);
        central.push(dir, nameBuf);
      }
      const body = Buffer.concat(chunks);
      const dirBuf = Buffer.concat(central);
      const eocd = Buffer.alloc(22);
      eocd.writeUInt32LE(0x06054b50, 0);
      eocd.writeUInt16LE(parts.length, 8);
      eocd.writeUInt16LE(parts.length, 10);
      eocd.writeUInt32LE(dirBuf.length, 12);
      eocd.writeUInt32LE(body.length, 16);
      eocd.writeUInt16LE(0, 20);
      return Buffer.concat([body, dirBuf, eocd]);
    }
    // <<< UPLOAD_ZIP_BUILDER_END
    function uploadFixture(spec: any): {name: string; mimeType: string; buffer: Buffer} | null {
      const s = spec || {};
      const target = typeof s.bytes === 'number' ? s.bytes : null;
      if (s.source === 'workbook_stored') {
        if (target) {
          const buf = workbookOfExactBytes(target);
          return buf ? {name: s.name, mimeType: s.mime, buffer: buf} : null;
        }
        // 未指定目标字节数 → 直接用 Python 侧构造的真实最小工作簿（ZIP 压缩版）
        const fileBytes = Buffer.from((FIX.workbook_file || ''), 'base64');
        return fileBytes.length ? {name: s.name, mimeType: s.mime, buffer: fileBytes} : null;
      }
      if (s.source === 'bad_extension') {
        const head = Buffer.from(FIX.bad_extension_part || '', 'base64');
        const size = target || head.length;
        const buf = Buffer.alloc(size, 0);
        head.copy(buf, 0, 0, Math.min(head.length, size));
        return {name: s.name, mimeType: s.mime, buffer: buf};
      }
      if (s.source === 'csv_text') {
        // 合法 CSV：文本内容即有效样本（与"格式错误样本"严格区分）
        const rows = ['手机号,金额', '18320101265,100', '18320101266,200'];
        return {name: s.name, mimeType: s.mime || 'text/csv',
                buffer: Buffer.from(rows.join('\\n') + '\\n', 'utf8')};
      }
      if (s.source === 'xls_bytes') {
        const bytes = Buffer.from((FIX.xls_file || ''), 'base64');
        return bytes.length ? {name: s.name, mimeType: s.mime || 'application/vnd.ms-excel',
                               buffer: bytes} : null;
      }
      return null;
    }

    let checkIndex = 0;
    let uploadEvidenceCount = 0;
    //: 分阶段观测：把"文件选择 → 客户端反馈 → 上传请求 → 提示文本"拆开计时，
    //  否则"没有提示"这一个结果会混着多种原因（控件没收到文件 / 客户端没反应 / 提示一闪而过）。
    const installStageRecorder = async (scope: any) => {
      try {
        await scope.locator('body').first().evaluate(() => {
          const w = window as any;
          if (w.__uploadStages) return;
          w.__uploadStages = [];
          const push = (stage: string, extra: Record<string, unknown> = {}) => {
            w.__uploadStages.push({stage, at: Date.now(), ...extra});
          };
          document.addEventListener('change', (event) => {
            const target = event.target as any;
            if (target && target.type === 'file') {
              push('file_selected', {name: (target.files && target.files[0] && target.files[0].name) || '',
                                     size: (target.files && target.files[0] && target.files[0].size) || 0});
            }
          }, true);
          const origFetch = w.fetch;
          if (origFetch) {
            w.fetch = function (...args: any[]) {
              const url = String(args[0] || '').slice(0, 160);
              push('request_started', {url});
              const result = origFetch.apply(this, args as any);
              if (result && typeof result.then === 'function') {
                result.then(() => push('request_settled', {url}))
                      .catch(() => push('request_failed', {url}));
              }
              return result;
            };
          }
          const origOpen = w.XMLHttpRequest && w.XMLHttpRequest.prototype.open;
          if (origOpen) {
            w.XMLHttpRequest.prototype.open = function (method: string, url: string) {
              push('request_started', {url: String(url).slice(0, 160), method});
              return origOpen.apply(this, arguments as any);
            };
          }
        });
      } catch { /* 记录器装不上不影响主流程，只是少了阶段信息 */ }
    };
    const readStages = async (scope: any): Promise<Array<Record<string, any>>> => {
      try {
        return (await scope.locator('body').first()
          .evaluate(() => ((window as any).__uploadStages || []).slice(-40))) || [];
      } catch {
        return [];
      }
    };

    // ===== 上传校验阶段：可在**独立上下文**中执行，与业务执行上下文隔离 =====
    async function runUploadPhase(pg: any, scope: any, modal: any, checks: any[],
                                  contextLabel: string) {
      for (const check of checks) {
        const checkIndex = FORM_PLAN.checks.indexOf(check) + 1;
        const name = `upload_file:${check.field}`;
      // 说明：本分支**只采集原始事实**（样本、提示、附件块数量、拦截计数、是否超时），
      // **不做通过/未验证判定**。判定在服务端 `web_upload_evidence.classify_upload`，
      // 以便用单元测试覆盖"假通过"分支（复核意见 2/3/5）。
      await ensureMode(pg, modal, check.mode);
      const rowLabel = (check as any).row_label || '上传文件';
      const item = modal.locator(`.ant-form-item:has-text("${rowLabel}")`).first();
      let input = item.locator('input[type=file]').first();
      if (!(await input.count())) {
        input = modal.locator('input[type=file]').first();   // 退一步：整个弹窗里的文件输入
      }
      const baseEvidence: Record<string, any> = {
        check_index: checkIndex,
        case_id: (check as any).case_id || '',
        rule_id: (check as any).rule_id || '',
        field: check.field,
        evidence_kind: (check as any).evidence_kind || '',
        expect: (check as any).expect || '',
        scope: 'client_only',
        context_label: contextLabel,
        rule: check.rule || '',
        sample: {...((check as any).sample || {})},
        control_reset: false,
        chips_before: 0,
        chips_after: 0,
        messages_before: [] as string[],
        messages_after: [] as string[],
        accept_attr: '',
        waited_ms: 0,
        timed_out: false,
        timed_out_stage: '',
        stages: {
          change_ms: 0, feedback_ms: 0, saw_file_selected: false,
          saw_request: false, request_count: 0, event_count: 0,
        },
        isolation_blocks: 0,
        isolation_blocked_kinds: {
          upload_blocked: 0, non_upload_api_blocked: 0, unknown_request_blocked: 0,
        },
      };
      const emitEvidence = () => {
        uploadEvidenceCount += 1;
        console.log('UPLOAD_EVIDENCE ' + JSON.stringify(baseEvidence));
      };
      if (!(await input.count())) {
        baseEvidence.sample = {...(baseEvidence.sample || {}), error: 'no_file_input'};
        emitEvidence();
        continue;
      }
      baseEvidence.accept_attr = (await input.getAttribute('accept')) || '';

      // 状态重置：① 清空文件输入（同一文件再次选择时，浏览器/组件可能不再触发校验——
      // 这正是"大小拒绝提示偶发未观测到"的怀疑原因）；② 删掉上一次留下的附件块。
      let reset = false;
      try {
        await input.setInputFiles([], {timeout: 3000});
        reset = true;
      } catch { /* 清空失败不致命，继续尝试删除附件块 */ }
      try {
        const chipCount = await item.locator('.ant-upload-list-item').count();
        for (let i = chipCount - 1; i >= 0; i--) {
          const del = item.locator('.ant-upload-list-item .anticon-close, .ant-upload-list-item .anticon-delete').nth(i);
          if (await del.count()) {
            await del.click({timeout: 3000}).catch(() => undefined);
            reset = true;
          }
        }
        if (reset) await pg.waitForTimeout(400);
      } catch { /* 重置失败也不影响：分类器会看到 control_reset=false */ }
      baseEvidence.control_reset = reset;

      const fixture = uploadFixture((check as any).sample);
      if (!fixture) {
        baseEvidence.sample = {...(baseEvidence.sample || {}), error: 'fixture_unavailable'};
        emitEvidence();
        continue;
      }
      baseEvidence.sample = {...(baseEvidence.sample || {}), bytes: fixture.buffer.length};

      const messageTexts = async (scope: any) => {
        const inline = await item.locator('.ant-form-item-explain-error')
          .allInnerTexts().catch(() => []);
        const toasts = await scope.locator('.ant-message-error, .ant-message-warning, .ant-message-info')
          .allInnerTexts().catch(() => []);
        return inline.concat(toasts).map((t: string) => (t || '').trim()).filter(Boolean);
      };
      baseEvidence.chips_before = await item.locator('.ant-upload-list-item').count();
      baseEvidence.messages_before = await messageTexts(scope);
      const isoBefore = isolationBlocked.length;
      const blockedKinds = () => {
        const slice = isolationBlocked.slice(isoBefore);
        return {
          upload_blocked: slice.filter((b) => b.kind === 'upload_blocked').length,
          non_upload_api_blocked: slice.filter((b) => b.kind === 'non_upload_api_blocked').length,
          unknown_request_blocked: slice.filter((b) => b.kind === 'unknown_request_blocked').length,
        };
      };
      await installStageRecorder(scope);
      const stagesBefore = (await readStages(scope)).length;

      try {
        await input.setInputFiles(fixture, {timeout: 8000});
      } catch (error) {
        baseEvidence.sample = {...(baseEvidence.sample || {}),
                                error: 'set_input_failed: ' + String(error).slice(0, 60)};
        baseEvidence.isolation_blocks = isolationBlocked.length - isoBefore;
        emitEvidence();
        continue;
      }

      // 阶段 1：**文件选择事件**是否真的发生（控件收到文件才算进入校验阶段）
      const changeStart = Date.now();
      let sawChange = false;
      while (Date.now() - changeStart < 5000) {
        const stages = (await readStages(scope)).slice(stagesBefore);
        if (stages.some((s) => s.stage === 'file_selected')) { sawChange = true; break; }
        await pg.waitForTimeout(150);
      }
      const changeMs = Date.now() - changeStart;

      // 阶段 2：客户端反馈（新提示 / 上传请求 / 附件块变化），有上限等待。
      // 提示按**出现次数**比较：同一文本重复出现时集合差看不出变化（实测会白等到超时）。
      const countBy = (list: string[]) => {
        const map: Record<string, number> = {};
        for (const t of list) map[t] = (map[t] || 0) + 1;
        return map;
      };
      const beforeCounts = countBy(baseEvidence.messages_before);
      const hasNewMessage = (now: string[]) => {
        const nowCounts = countBy(now);
        return Object.keys(nowCounts).some((k) => nowCounts[k] > (beforeCounts[k] || 0));
      };
      const feedbackStart = Date.now();
      const budgetMs = sawChange ? 12000 : 4000;   // 已看到文件选择 → 给客户端校验更充裕的时间
      let feedbackSeen = false;
      while (Date.now() - feedbackStart < budgetMs) {
        await pg.waitForTimeout(200);
        const stages = (await readStages(scope)).slice(stagesBefore);
        const nowMessages = await messageTexts(scope);
        const nowChips = await item.locator('.ant-upload-list-item').count();
        if (hasNewMessage(nowMessages) || nowChips !== baseEvidence.chips_before
            || stages.some((s) => s.stage === 'request_started')) {
          feedbackSeen = true;
          break;
        }
      }
      const feedbackMs = Date.now() - feedbackStart;
      const stages = (await readStages(scope)).slice(stagesBefore);
      baseEvidence.waited_ms = changeMs + feedbackMs;
      baseEvidence.timed_out = !(sawChange && feedbackSeen);
      baseEvidence.timed_out_stage = !sawChange ? 'file_selected'
        : (feedbackSeen ? '' : 'client_feedback');
      baseEvidence.stages = {
        change_ms: changeMs,
        feedback_ms: feedbackMs,
        saw_file_selected: sawChange,
        saw_request: stages.some((s) => s.stage === 'request_started'),
        request_count: stages.filter((s) => s.stage === 'request_started').length,
        event_count: stages.length,
      };
      baseEvidence.chips_after = await item.locator('.ant-upload-list-item').count();
      baseEvidence.messages_after = await messageTexts(scope);
      baseEvidence.isolation_blocks = isolationBlocked.length - isoBefore;
      baseEvidence.isolation_blocked_kinds = blockedKinds();
      emitEvidence();
      continue;
    
      }
    }

    // rev94：客户端层整体限时预算（10 分钟）——与提交层 5 分钟预算配套，保证
    // "客户端 + 提交层 + 登录"总时长恒定低于 runner 上限（1500s），任何环境性挂起
    // 都转化为如实 unverified，而不是 stdout 被杀、全批证据丢失（实测 retro 偶发挂起）。
    const clientDeadline = Date.now() + 600000;
    for (const check of FORM_PLAN.checks) {
      checkIndex += 1;
      if (check.type === 'upload_file') continue;   // 由独立阶段处理（见下）
      const name = `${check.type}:${check.field}${check.option ? '=' + check.option : ''}${check.data ? ' [' + check.data + ']' : ''}`;
      currentRuleId = (check as any).rule_id || '';
      currentCaseId = (check as any).case_id || '';
      if (Date.now() > clientDeadline) {
        recAll(check, name, 'unverified', '客户端层整体超时预算（10分钟）耗尽，该项未验证（环境性挂起，需重跑或 API 层验证）');
        continue;
      }

      try {
        if (check.type === 'default_selected') {
          const def0 = fieldDefs[check.field] || {};
          if (def0.kind === 'select') {
            // 下拉默认值：读当前显示值（未选中时为空 → 未验证，不冒充通过）
            const value = await selectDisplayValue(modal, check.field, def0);
            const normV = (s: string) => (s || '').replace(/\s+/g, '');
            recAll(check, name, value && normV(value).includes(normV(check.option)) ? 'pass' : 'unverified',
                   value ? `默认显示值=${value}（期望 ${check.option}）` : '未读到下拉当前值（可能未选中）');
            continue;
          }
          const wrap = radioOption(modal, check.field, check.option);
          const checked = await isRadioChecked(wrap);
          recAll(check, name, checked ? 'pass' : 'unverified',
                 checked ? `默认选中=${check.option}` : `未观测到默认选中 ${check.option}`);
          continue;
        }
        if (check.type === 'visible_now') {
          // 默认态下的条件显示：不做任何点击，只读当前表单
          const missing: string[] = [];
          for (const label of check.expect_labels || []) {
            if (!(await modal.locator(`.ant-form-item:has-text("${label}")`).count())) missing.push(label);
          }
          recAll(check, name, missing.length === 0 ? 'pass' : 'unverified',
                 missing.length === 0 ? `默认态下字段已显示：${(check.expect_labels || []).join('、')}`
                                      : `默认态下未显示：${missing.join('、')}`);
          continue;
        }
        if (check.type === 'submit_only') {
          const reason = String((check as any).reason || '');
          if (reason.includes('单选组')) {
            // rev69 口径：单选组默认选中是被测端防漏填的设计选择（非缺陷）。UI 层负路径
            // 物理上不可构造 → 标注「不适用」并指明服务端空值校验的覆盖路径，避免被当漏测。
            recAll(check, name, 'unverified', `${reason}；UI 层负路径不适用：单选组有默认选中，无法构造空值提交（如需覆盖服务端空值校验，需走 API 层测试）`);
          } else {
            // 上传控件必填：不带文件点「确定」→ 期望提交时校验拦截并给出该字段提示
            recAll(check, name, 'unverified', `${reason}；进入负路径提交层重试`);
            submitQueue.push({check, name, kind: 'required_empty'});
          }
          continue;
        }
        if (check.type === 'radio_options') {
          // 单选组的选项范围：读选项标签即可判定（无自由输入路径）
          const labels = await optionLabels(modal, check.field);
          const allowed: string[] = check.allowed || [];
          const norm = (s: string) => (s || '').replace(/\s+/g, '');
          if (!labels.length) {
            recAll(check, name, 'unverified', '未读到单选选项（可能未展开或该组无选项）');
            continue;
          }
          if (String(check.expect).startsWith('reject')) {
            // 同 select：reject 只判"非法值能否被选到"，不被 allowed 缺失改判（见 select 分支说明）
            const exists = labels.some((l) => norm(l).includes(norm(check.data)));
            recAll(check, name, exists ? 'unverified' : 'pass',
                   exists ? `非法值 ${check.data} 竟出现在选项里（${check.rule}）`
                          : `单选组中不存在 ${check.data || '(空)'}，用户无法选择非法值（${check.rule}）`);
          } else if (allowed.some((a) => !labels.some((l) => norm(l).includes(norm(a))))) {
            const missing = allowed.filter((a) => !labels.some((l) => norm(l).includes(norm(a))));
            recAll(check, name, 'unverified', `未观测到选项：${missing.join('、')}（实际：${labels.join('/')}）`);
          } else if (check.data) {
            const exists = labels.some((l) => norm(l).includes(norm(check.data)));
            recAll(check, name, exists ? 'pass' : 'unverified',
                   exists ? `合法选项 ${check.data} 存在（${check.rule}）`
                          : `未观测到合法选项 ${check.data}（${check.rule}）`);
          } else {
            recAll(check, name, 'pass', `选项范围齐全：${labels.join('/')}`);
          }
          continue;
        }
        if (check.type === 'select_options') {
          // 下拉的选项范围：展开读取选项文本即可判定（无自由输入路径）
          const labels = await selectOptionLabels(page, anyScope, modal, check.field, fieldDefs[check.field] || {});
          const allowed: string[] = check.allowed || [];
          const norm = (s: string) => (s || '').replace(/\s+/g, '');
          if (!labels.length) {
            recAll(check, name, 'unverified', '未读到下拉选项（可能未成功展开或无选项）');
            continue;
          }
          if (String(check.expect).startsWith('reject')) {
            // reject 分支**只**判"非法值能否被选到"，不因 allowed 里声明的选项缺失而改判：
            // 实测（Brand）：断言 allowed=['ETZ'] 而系统实际无 ETZ，若先查 allowed，
            // 这条 reject 会被判 unverified，把"文档与系统不一致"这条真发现埋掉。
            // 声明选项的缺失由 accept 那条承载（它会带实际选项列表报 unverified）。
            const exists = labels.some((l) => norm(l).includes(norm(check.data)));
            recAll(check, name, exists ? 'unverified' : 'pass',
                   exists ? `非法值 ${check.data} 竟出现在选项里（${check.rule}）`
                          : `下拉中不存在 ${check.data || '(空)'}，用户无法选择非法值（${check.rule}）`);
          } else if (allowed.some((a) => !labels.some((l) => norm(l).includes(norm(a))))) {
            const missing = allowed.filter((a) => !labels.some((l) => norm(l).includes(norm(a))));
            recAll(check, name, 'unverified', `未观测到选项：${missing.join('、')}（实际：${labels.join('/')}）`);
          } else if (check.data) {
            const exists = labels.some((l) => norm(l).includes(norm(check.data)));
            recAll(check, name, exists ? 'pass' : 'unverified',
                   exists ? `合法选项 ${check.data} 存在（${check.rule}）`
                          : `未观测到合法选项 ${check.data}（${check.rule}）`);
          } else {
            recAll(check, name, 'pass', `选项范围齐全：${labels.join('/')}`);
          }
          continue;
        }
        if (check.type === 'visibility') {
          await ensureMode(page, modal, check.mode);
          const option = radioOption(modal, check.field, check.option);
          if (!(await option.count())) {
            recAll(check, name, 'unverified', `未找到选项 ${check.option}`); continue;
          }
          await option.click({timeout: 8000});
          await page.waitForTimeout(900);
          const missing: string[] = [];
          for (const label of check.expect_labels || []) {
            if (!(await modal.locator(`.ant-form-item:has-text("${label}")`).count())) missing.push(label);
          }
          recAll(check, name, missing.length === 0 ? 'pass' : 'unverified',
                 missing.length === 0 ? `切换后字段出现：${(check.expect_labels || []).join('、')}`
                                      : `切换后未出现：${missing.join('、')}`);
          continue;
        }
        if (check.type === 'required_error') {
          const def = fieldDefs[check.field] || {};
          await ensureMode(page, modal, check.mode);
          const input = await fieldControl(modal, check.field, def);
          if (!(await input.count())) { await reportLocatorMiss('form:required_error', check, modal); recAll(check, name, 'unverified', '未定位到输入框（定位现场见 [observation] 行）'); continue; }
          // rev92：antd Select 必填——展开下拉后 Esc 取消（无选择变更），再 blur
          // 尝试触发校验；无提示则如实 unverified 并交负路径提交层（真实提交验证）。
          if (String(def.kind || '') === 'select') {
            const root0 = selectRoot(modal, check.field, def);
            if (!(await root0.count())) { recAll(check, name, 'unverified', '未定位到下拉控件'); continue; }
            await root0.click({timeout: OP_T}).catch(() => undefined);
            await page.waitForTimeout(800);
            await page.keyboard.press('Escape').catch(() => undefined);
            await page.waitForTimeout(300);
            await modal.locator('.ant-form-item-label, label').first().click({timeout: 5000}).catch(() => undefined);
            const selErr = await itemHasError(page, modal, check.field);
            recAll(check, name, selErr ? 'pass' : 'unverified',
                   selErr ? '展开下拉后取消出现必填校验提示'
                          : '展开/取消下拉未触发必填提示（无变更事件，进入负路径提交层重试）');
            if (!selErr) submitQueue.push({check, name, kind: 'required_empty'});
            continue;
          }
          // rev92：DatePicker 必填——选一个日期再点清除（真实 onChange）触发校验。
          if (String(def.kind || '') === 'date') {
            const diag = await datePickThenClear(page, anyScope, modal, check.field, def);
            if (diag) { recAll(check, name, 'unverified', `日期控件交互失败：${diag}`); continue; }
            await modal.locator('.ant-form-item-label, label').first().click({timeout: 5000}).catch(() => undefined);
            const dateErr = await itemHasError(page, modal, check.field);
            recAll(check, name, dateErr ? 'pass' : 'unverified',
                   dateErr ? '选日期后清除出现必填校验提示' : '选/清日期后未出现必填提示（可能仅提交时校验）');
            if (!dateErr) submitQueue.push({check, name, kind: 'required_empty'});
            continue;
          }
          // rev71f：只读选择器（如 CS 的 Member，input[readonly] 由弹窗回填）——
          // fill 等待 editability 永不满足（TR-106 实测挂满 20 分钟根因）。
          // 这类字段 UI 层无法经"输入→清空"触发必填校验，如实记 unverified。
          const roAttr = await input.getAttribute('readonly', {timeout: 3000}).catch(() => null);
          if (roAttr !== null) {
            recAll(check, name, 'unverified', '字段为只读选择器（readonly input，值由选择弹窗回填），UI 层无法经输入触发必填校验（需选择器路径或 API 层验证）');
            continue;
          }
          // rev85：可输入性预检——UEditor 富文本等控件的同步 textarea 是 hidden/readonly，
          // 直接 fill 会等满 OP_T（实测 Task/Phone 的 Description blocked×2）。
          const editableChk = await input.evaluate((el: any) => {
            const ro = el.getAttribute && el.getAttribute('readonly');
            const dis = el.getAttribute && el.getAttribute('disabled');
            const t = String((el.getAttribute && el.getAttribute('type')) || '').toLowerCase();
            const visible = !!(el.offsetWidth || el.offsetHeight);
            return ro === null && dis === null && t !== 'hidden' && visible;
          }, {timeout: 5000}).catch(() => true);
          if (!editableChk) {
            recAll(check, name, 'unverified', '控件不可编程输入（隐藏同步域/富文本编辑器/不可见），UI 输入层不适用（需富文本交互路径或 API 层验证）');
            continue;
          }
          // 先填再清：antd 的 required 规则默认 onChange 触发，对空字段 fill('') 不产生事件
          await input.fill('1', {timeout: OP_T});
          await page.waitForTimeout(200);
          await input.fill('', {timeout: OP_T});
          await input.blur({timeout: OP_T});
          const hasError = await itemHasError(page, modal, check.field);
          recAll(check, name, hasError ? 'pass' : 'unverified',
                 hasError ? '填值后清空出现必填校验提示' : '填值后清空仍未出现必填提示（可能仅提交时校验）');
          if (!hasError) submitQueue.push({check, name, kind: 'required_empty'});
          continue;
        }
        if (check.type === 'input_validation') {
          const def = fieldDefs[check.field] || {};
          await ensureMode(page, modal, check.mode);
          const input = await fieldControl(modal, check.field, def);
          if (!(await input.count())) { await reportLocatorMiss('form:input_validation', check, modal); recAll(check, name, 'unverified', '未定位到输入框（定位现场见 [observation] 行）'); continue; }
          // rev79：antd Select 下拉控件的取值/边界校验需下拉交互路径，如实记 unverified
          if (String(def.kind || '') === 'select') {
            recAll(check, name, 'unverified', 'antd Select 下拉控件：输入校验需下拉交互路径，UI 输入层不适用（需下拉选择器路径或 API 层验证）');
            continue;
          }
          // rev71f：只读选择器同样无法注入探针值（同 required_error 守卫）
          const roAttr2 = await input.getAttribute('readonly', {timeout: 3000}).catch(() => null);
          if (roAttr2 !== null) {
            recAll(check, name, 'unverified', '字段为只读选择器（readonly input），UI 层无法注入边界探针值（需选择器路径或 API 层验证）');
            continue;
          }
          // rev85：可输入性预检（同 required_error），避免 fill 等满 OP_T 变 blocked
          const editableChk2 = await input.evaluate((el: any) => {
            const ro = el.getAttribute && el.getAttribute('readonly');
            const dis = el.getAttribute && el.getAttribute('disabled');
            const t = String((el.getAttribute && el.getAttribute('type')) || '').toLowerCase();
            const visible = !!(el.offsetWidth || el.offsetHeight);
            return ro === null && dis === null && t !== 'hidden' && visible;
          }, {timeout: 5000}).catch(() => true);
          if (!editableChk2) {
            recAll(check, name, 'unverified', '控件不可编程输入（隐藏同步域/富文本编辑器/不可见），UI 层无法注入边界探针值（需富文本交互路径或 API 层验证）');
            continue;
          }
          // data 可能是人类可读描述（"500字"）→ 用服务端合成的**真实探针值**（500/501 字符）
          const probeValue = String((check as any).probe_value || check.data || '');
          const probeNote = String((check as any).probe_note || '');
          const label = probeNote ? `${probeNote}` : `输入 ${probeValue}`;
          await input.fill(probeValue, {timeout: OP_T});
          await input.blur({timeout: OP_T});
          const invalid = await itemHasError(page, modal, check.field);
          const actual = await controlValue(input);
          // rev93（2026-10-07 实测 artifacts/b_limit_period_input.json）：部分控件"先显示后回滚"——
          // antd RangePicker 键入 2026-10-01 时 inputValue 可读到该文本（面板未确认），**失焦后回滚为空**。
          // 旧逻辑用 `actual !== probeValue` 判"被截断"，会把"控件根本没接受输入"记成 pass（假通过）。
          // 故把"值丢失（探针值非空、实际为空）"单独成类：只记 unverified，并交提交层做真实提交验证。
          const valueLost = probeValue !== '' && actual === '';
          const truncated = !valueLost && actual !== probeValue;
          const expectReject = String(check.expect).startsWith('reject');
          if (expectReject) {
            recAll(check, name, (invalid || truncated) ? 'pass' : 'unverified',
                   invalid ? `${label} 出现校验提示（${check.rule}）`
                           : (valueLost
                              ? `${label} 控件未保留输入（失焦后为空），未观测到拒绝证据（需控件专用交互路径或 API 层验证）`
                              : (truncated ? `${label} 被截断为 ${actual.length} 字符（${check.rule}）`
                                           : `${label} 未观测到客户端校验：实际长度 ${actual.length}，无校验提示（可能仅服务端校验）`)));
            if (!invalid && !truncated) submitQueue.push({check, name, kind: 'probe', probeValue});
          } else {
            recAll(check, name, (invalid || valueLost) ? 'unverified' : 'pass',
                   invalid ? `${label} 被判为非法（${check.rule}）`
                           : (valueLost
                              ? `${label} 控件未保留输入（失焦后为空），无法判定"被接受"（需控件专用交互路径或 API 层验证）`
                              : `${label} 通过（${check.rule}）`));
          }
          continue;
        }
        recAll(check, name, 'unverified', `未支持的检查类型 ${check.type}`);
      } catch (error) {
        console.log(`CS_PAGE_DIAG check=${name} ${await pageDiag(page)}`);
        recAll(check, name, 'blocked', `执行异常：${String(error).slice(0, 300)}`);
      }
    }
    // ===== 负路径提交层校验（rev67，2026-09-25 用户确认口径）=====
    // 只对客户端层未验证的「期望拒绝」断言执行真实提交。顺序：先 probe（把非法探针值
    // 提交到服务端，其余空必填用兜底值填充以触达服务端），后 required_empty（客户端必填拦截即可判定）。
    if (submitQueue.length) {
      let curModal: any = modal;
      // antd v3 中文 locale 的确认按钮渲染为「确 定」（带空格）→ 正则必须容忍空白；
      // 文本匹配不到时兜底用 footer 主按钮（取消在前、主操作在后）。
      // O1c/rev100：提交点击**有界降级**。实测（TR-282/283/288，WT-6C6D1A99 整页表单）：
      // 点提交 8s 硬超时抛 TimeoutError → 提交层单项 blocked → summary.blocked=1 硬失败
      // 拖死整批（与 rev90 预算闸同源的「单 item 拖死整批」第二实例，§11 待办 O1c）。
      // 口径：先**无等待**探测按钮存在性——footer 与顶栏候选**都空**才是结构性失效
      //（记 blocked，定位器真失效必须见光）；按钮在但点不动（遮罩/竞态/页面已迁移）
      // ⇒ 该项记 unverified（无服务端证据，如实）+ 诊断输出，**队列继续**。
      const findSubmitBtn = async (): Promise<any | null> => {
        const footer = curModal.locator('.ant-modal-footer button');
        // rev98：补 发送/Send（O10 toolbar_modal 的 email 弹窗形态，实测 67197 入口）。
        // 只扩展候选集合，既有页面仍由排在前面的关键词命中，不影响已覆盖资产。
        let btn = footer.filter({hasText: /确\s*定|提\s*交|保\s*存|确\s*认|发\s*送|Send|ok/i}).last();
        if (!(await btn.count())) btn = footer.locator('.ant-btn-primary').last();
        if (!(await btn.count())) {
          // rev79：整页表单（direct_url 流程，如 CS RetroClaim/GoodWill 创建页）
          // 没有 .ant-modal-footer——顶栏 Save 按钮直接在页面作用域里找
          btn = anyScope.locator('button:visible').filter({hasText: /保\s*存|Save|Submit/i}).first();
        }
        return (await btn.count()) ? btn : null;
      };
      const clickSubmit = async (): Promise<{ok: boolean; reason: string}> => {
        const btn = await findSubmitBtn();
        if (!btn) return {ok: false, reason: '未找到提交按钮（modal footer 与顶栏候选均为空）'};
        try {
          await btn.click({timeout: 8000});
          return {ok: true, reason: ''};
        } catch (e) {
          return {ok: false, reason: String(e).slice(0, 140)};
        }
      };
      // antd v3/v4 兼容的错误项选择器
      const ERRORED_ITEM = '.ant-form-item.has-error, .ant-form-item:has(.ant-form-item-explain-error)';
      // rev69 网络响应捕获层：页面证据为主、网络证据为辅。收集提交窗口内的写请求响应
      //（状态码 + 响应体摘要），用于给「页面无拒绝证据」的负路径项补上服务端侧确定性结论。
      // tk 令牌隔离每个用例项的收集窗口，防止上一项的迟到响应污染下一项判定。
      // requestId（P1-3 阶段 A 新增）：缺陷单必须携带的定位字段（实测 DEFECT-001 工单
      // 用的就是 requestId）。此前只存 url/status/body，取证时还得回页面手工翻——
      // 这里直接从响应头取，覆盖常见的三种命名（大小写不敏感读取由 Playwright 保证）。
      const netLog: Array<{tk: number; url: string; status: number; body: string; requestId: string}> = [];
      let netToken = 0;
      // rev69 诊断：request 级捕获（区分「请求从未发出」vs「发出但无响应」）
      const reqLog: Array<{tk: number; m: string; url: string}> = [];
      const onNetReq = (r: any) => {
        try {
          if (String(r.method()).toUpperCase() === 'GET') return;
          reqLog.push({tk: netToken, m: String(r.method()), url: String(r.url()).slice(0, 90)});
        } catch { /* ignore */ }
      };
      page.on('request', onNetReq);
      // rev69 诊断：JS 报错捕获（提交点击若触发未处理异常会在这里现形）
      const errLog: Array<{tk: number; kind: string; text: string}> = [];
      const onPageErr = (e: any) => {
        errLog.push({tk: netToken, kind: 'pageerror', text: String((e && e.message) || e).slice(0, 150)});
      };
      const onConsoleMsg = (m: any) => {
        try {
          if (String(m.type()) === 'error') errLog.push({tk: netToken, kind: 'console', text: String(m.text()).slice(0, 150)});
        } catch { /* ignore */ }
      };
      page.on('pageerror', onPageErr);
      page.on('console', onConsoleMsg);
      const onNetResp = (r: any) => {
        try {
          const req = r.request();
          if (String(req.method()).toUpperCase() === 'GET') return;
          const rt = req.resourceType();
          if (rt !== 'xhr' && rt !== 'fetch') return;
          const url = String(r.url());
          const tk = netToken;
          // requestId 提取（P1-3 阶段 A）：响应头命名在不同网关下不一致，
          // 逐个候选取第一个非空值；取不到留空串（不伪造）。
          const pickRequestId = (resp: any): string => {
            try {
              const h = resp.headers() || {};
              for (const name of ['requestid', 'x-request-id', 'request-id', 'traceid', 'x-trace-id']) {
                const v = h[name];
                if (v) return String(v);
              }
            } catch { /* 头读取失败按取不到处理 */ }
            return '';
          };
          const requestId = pickRequestId(r);
          r.text().then((t: any) => {
            netLog.push({tk, url: url.slice(0, 150), status: r.status(), requestId,
                         body: String(t ?? '').replace(/\s+/g, ' ').slice(0, 200)});
          }).catch(() => {
            netLog.push({tk, url: url.slice(0, 150), status: r.status(), requestId, body: '<body不可读>'});
          });
        } catch { /* 监听失败不致命 */ }
      };
      page.on('response', onNetResp);
      // 把"非目标字段"的空必填用兜底值填上（手机号用默认号码，其余用 '1'），
      // 让非法探针值能穿过客户端校验触达服务端（负路径：服务端应拒绝）。
      const fillEmptyErrored = async () => {
        const items = curModal.locator(ERRORED_ITEM);
        const n = await items.count();
        for (let i = 0; i < n; i++) {
          const it = items.nth(i);
          // rev69：跳过被标记的「目标字段」form-item，保证负路径「仅目标字段违规」成立
          if (await it.evaluate((el: any) => !!(el as any).dataset && (el as any).dataset.rev69ut === '1').catch(() => false)) continue;
          const label = String(await it.locator('label').first().innerText({timeout: 1000}).catch(() => ''));
          // rev69：上传类必填用合成文件填充（Buffer 不落盘），让探针请求能触达服务端
          const fileInput = it.locator('input[type=file]').first();
          if (await fileInput.count()) {
            await fileInput.setInputFiles(
              {name: 'probe.txt', mimeType: 'text/plain', buffer: Buffer.from('rev69 negative-path probe')},
              {timeout: 2000}).catch(() => undefined);
            continue;
          }
          const input = it.locator('input:visible, textarea:visible').first();
          if (!(await input.count())) continue;   // 单选类无法程序化填充
          const val = String(await input.inputValue({timeout: 500}).catch(() => ''));
          if (val) continue;                       // 已有值（可能是目标探针字段）
          const v = /手机|电话|phone/i.test(label) ? FORM_PHONE : '1';
          await input.fill(v, {timeout: 1000}).catch(() => undefined);
          await input.blur({timeout: 1000}).catch(() => undefined);
        }
        await page.waitForTimeout(600);
      };
      const ordered = [...submitQueue].sort(
        (a, b) => (a.kind === 'probe' ? 0 : 1) - (b.kind === 'probe' ? 0 : 1));
      // rev90：提交层整体限时预算（5 分钟）——实测 RetroClaim 负路径提交在 Save 后
      // 挂起 20+ 分钟撞 runner 上限且 stdout 被杀丢失。预算耗尽后剩余项如实记
      // unverified（归 API 层），绝不让单个提交项拖死整批。
      const submitDeadline = Date.now() + 300000;
      for (const item of ordered) {
        const check = item.check;
        if (Date.now() > submitDeadline) {
          recAll(check, item.name, 'unverified', '提交层整体超时预算（5分钟）耗尽，该项未验证（需 API 层验证）');
          continue;
        }
        try {
          // 韧性：页面/弹窗中途死亡时降级为 unverified 并跳过，不让整轮挂死
          if (typeof (page as any).isClosed === 'function' && (page as any).isClosed()) {
            recAll(check, item.name, 'unverified', '提交层：页面已关闭，无法继续验证'); continue;
          }
          if (!(await curModal.isVisible().catch(() => false))) {
            try { curModal = await openForm(); }
            catch { recAll(check, item.name, 'unverified', '提交层：表单弹窗无法重开，跳过该项'); continue; }
          }
          const def = fieldDefs[check.field] || {};
          // 1) 复位目标字段状态：required_empty 清空；probe 填非法探针值
          await ensureMode(page, curModal, check.mode);
          const input = await fieldControl(curModal, check.field, def);
          // rev69：给「目标字段」的 form-item 打标记，兜底填充(fillEmptyErrored)跳过它，
          // 保证负路径「仅目标字段违规、其余字段合法」成立（否则空必填兜底会把目标字段也填上）
          const underItem = (await input.count())
            ? input.locator('xpath=ancestor::div[contains(@class,"ant-form-item")][1]') : null;
          if (underItem && (await underItem.count())) {
            await underItem.evaluate((el: any) => { (el as any).dataset.rev69ut = '1'; }).catch(() => undefined);
          }
          // rev71f：只读选择器在提交层同样无法注入（fill 等待 editability 超时）→ 如实记 unverified
          if (await input.count()) {
            const roSub = await input.getAttribute('readonly', {timeout: 3000}).catch(() => null);
            if (roSub !== null) {
              recAll(check, item.name, 'unverified', '提交层：字段为只读选择器（readonly input），无法注入探针/清空触发必填校验');
              continue;
            }
          }
          // rev79：antd Select 下拉控件在提交层同样无法 fill（组件内部伪 input 会抛异常）→ 如实记 unverified
          if (String(def.kind || '') === 'select') {
            recAll(check, item.name, 'unverified', '提交层：antd Select 下拉控件无法注入探针值，需下拉交互路径或 API 层验证');
            continue;
          }
          if (item.kind === 'probe') {
            if (!(await input.count())) { await reportLocatorMiss('submit_phase', item.check || check, modal); recAll(check, item.name, 'unverified', '提交层：未定位到输入框（定位现场见 [observation] 行）'); continue; }
            await input.fill(String(item.probeValue || check.data || ''), {timeout: OP_T});
            await input.blur({timeout: OP_T});
          } else if (await input.count()) {
            // rev69：上传类目标字段用 setInputFiles([]) 清空（fill 对 file input 无效）并清残留附件块
            const ftype = await input.getAttribute('type', {timeout: 1000}).catch(() => null);
            if (ftype === 'file') {
              await input.setInputFiles([], {timeout: 2000}).catch(() => undefined);
              const chips = curModal.locator('.ant-upload-list-item');
              const cn = await chips.count();
              for (let ci = cn - 1; ci >= 0; ci--) {
                const del = chips.nth(ci).locator('.anticon-close, .anticon-delete');
                if (await del.count()) await del.click({timeout: 1000}).catch(() => undefined);
              }
            } else {
              await input.fill('', {timeout: OP_T});
              await input.blur({timeout: OP_T});
            }
          }
          // 2) 点击提交并**轮询**判定信号（字段提示/错误toast/弹窗关闭任一命中即停）；
          //    probe/required_empty 若被其他空必填拦截 → 兜底填充（跳过目标字段）后重试；
          //    确认层出现 → 点确认放行到服务端。
          const TOAST_SEL = '.ant-message-notice-error, .ant-notification-notice-error, .ant-message .anticon-close-circle';
          // rev69 关键修复：confirm 层和 toast 都渲染在应用 iframe 内（appScope=frameLocator），
          // 用 page.locator（主 frame）永远找不到——此前负路径提交被确认层卡死、请求发不出去。
          const confirmClick = async () => {
            const cb = anyScope.locator('.ant-modal-confirm .ant-btn-primary').first();
            if ((await cb.count()) && (await cb.isVisible().catch(() => false))) {
              await cb.click({timeout: 4000}).catch(() => undefined);
            }
          };
          const observe = async () => ({
            fieldError: await itemHasError(page, curModal, check.field, 250),
            toastError: await anyScope.locator(TOAST_SEL).count(),
            stillOpen: await curModal.isVisible().catch(() => false),
          });
          const settleRound = async (rounds: number) => {
            for (let i = 0; i < rounds; i++) {
              await page.waitForTimeout(400);
              const s = await observe();
              if (s.fieldError || s.toastError || !s.stillOpen) return s;
              await confirmClick();
            }
            return null;
          };
          netToken += 1; netLog.length = 0;           // rev69：本用例项开启新的收集窗口
          const sig = await (async () => {
            const first = await clickSubmit();
            // O1c/rev100：首次点击即失败 = 请求未发出、无可等证据 → 带标记返回，
            // 由外层记 unverified 并跳过观察窗（不空等 12s 网络轮询）
            if (!first.ok) return {__submitFailed: first.reason};
            const s1 = await settleRound(6);          // 第一轮 ~2.4s：页面证据优先
            if (s1) return s1;
            // rev69 守卫：兜底填充前确认目标字段状态——若兜底会污染目标字段（probe 值丢失/
            // 目标字段被填充）则放弃重试，避免把「正路径提交」误判成负路径结论
            let guardOk = true;
            if (item.kind === 'probe') {
              const cur = String(await input.inputValue({timeout: 500}).catch(() => ''));
              guardOk = cur === String(item.probeValue || check.data || '');
            } else {
              guardOk = await itemHasError(page, curModal, check.field, 250);
            }
            if (!guardOk) return await observe();
            await fillEmptyErrored();                 // 其他空必填拦截 → 兜底填充重试
            netToken += 1; netLog.length = 0;         // 重试窗口重新收集，排除首轮干扰
            const second = await clickSubmit();
            // 首轮已点过、重试点不动：页面可能已迁移/被遮罩——带上诊断走常规分类
            if (!second.ok) return {...(await observe()), __secondSubmitFailed: second.reason};
            const s2 = await settleRound(8);          // 第二轮 ~3.2s + 确认层放行
            return s2 || (await observe());
          })();
          // O1c/rev100：首次点击失败 → 无服务端结论可下，如实记 unverified，队列继续
          if (sig && (sig as any).__submitFailed) {
            recAll(check, item.name, 'unverified',
              `提交层：点击提交失败（${(sig as any).__submitFailed}）——未提交，无法取得服务端结论；队列继续`);
            continue;
          }
          // rev69：扩展网络观察窗（~12s）——兜底填充的上传任务可能较慢，业务提交请求可能晚到；
          // 期间网络证据与页面证据任一命中即停，最终用最新页面状态判定。
          const isBizUrl = (u: string) => !/upload|file|oss|media|image|\.png|\.jpg|\.xlsx|\.xls/i.test(u);
          let late: any = null;
          let synthetic = false;
          for (let i = 0; i < 30; i++) {
            await page.waitForTimeout(400);
            if (netLog.some((n) => n.tk === netToken && isBizUrl(n.url))) break;
            // rev69：~6s 仍无证据 → 用合成点击(el.click)试探一次，排除可信点击被拦截/失联的可能
            if (i === 15 && !synthetic) {
              synthetic = true;
              await curModal.locator('.ant-modal-footer button').last()
                .evaluate((el: any) => (el as any).click()).catch(() => undefined);
            }
            late = await observe();
            if (late.fieldError || late.toastError || !late.stillOpen) break;
            await confirmClick();
          }
          if (late) { sig.fieldError = late.fieldError; sig.toastError = late.toastError; sig.stillOpen = late.stillOpen; }
          const nb = netLog.filter((n) => n.tk === netToken);
          const nbBiz = nb.filter((n) => isBizUrl(n.url));
          const nbUp = nb.length - nbBiz.length;
          const hit4xx = nbBiz.find((n) => n.status >= 400);
          const ok2xx = nbBiz.filter((n) => n.status >= 200 && n.status < 300);
          const err2xx = ok2xx.find((n) =>
            /"success"\s*:\s*false|"code"\s*:\s*(?!200|0\b)\d+|"code"\s*:\s*"(?!200|0)[^"]*"|错误|失败|无效|不能为空|不存在|格式不/.test(n.body));
          // requestId（P1-3 阶段 A）：证据串里带上 requestId，负路径失败时无需回页面手工翻网络面板
          const summarize = (n: any) => `HTTP ${n.status} ${n.url.slice(0, 80)} requestId:${n.requestId || '-'} body:${n.body.slice(0, 120)}`;
          const fieldError = sig.fieldError, toastError = sig.toastError, stillOpen = sig.stillOpen;
          if (fieldError) {
            recAll(check, item.name, 'pass', `提交层验证：提交后该字段出现校验提示（${check.rule}）`);
          } else if (toastError) {
            recAll(check, item.name, 'pass', `提交层验证：提交被拒绝，页面出现错误提示（${check.rule}）`);
          } else if (hit4xx) {
            recAll(check, item.name, 'pass', `提交层验证：服务端拒绝 ${summarize(hit4xx)}（${check.rule}）`);
          } else if (err2xx) {
            recAll(check, item.name, 'pass', `提交层验证：服务端业务层拒绝 ${summarize(err2xx)}（${check.rule}）`);
          } else if (!stillOpen) {
            const acc = ok2xx.length ? ok2xx[ok2xx.length - 1] : null;
            // requestId（P1-3 阶段 A）：坐实缺陷的分支，requestId 是提单必需项，显式列出
            // 而不只藏在 summarize 里，便于日志检索与工单直接引用
            const accRid = acc ? (acc.requestId || '未在响应头暴露') : '';
            recAll(check, item.name, 'blocked',
                   acc ? `疑似缺陷坐实：服务端受理非法请求 ${summarize(acc)}（约束「${check.rule}」未生效）；建议 UAT 数据佐证并登记缺陷。定位凭据：requestId=${accRid}`
                       : `期望拒绝但提交成功（弹窗已关闭，疑似缺陷：约束「${check.rule}」未生效）`);
          } else {
            const remaining = await curModal.locator(ERRORED_ITEM).count().catch(() => -1);
            // rev69 调试：零捕获时输出地面真相——提交按钮状态（是否 disabled）、
            // 整个监听期捕获的全部请求、弹窗内错误项数、页面快照
            const btnInfo = await (async () => {
              try {
                const footer = curModal.locator('.ant-modal-footer button');
                const cnt = await footer.count();
                const out: any[] = [];
                for (let bi = 0; bi < cnt; bi++) {
                  const b = footer.nth(bi);
                  out.push({
                    txt: String(await b.innerText({timeout: 500}).catch(() => '')).slice(0, 12),
                    disabled: await b.isDisabled().catch(() => null),
                    primary: String(await b.getAttribute('class', {timeout: 500}).catch(() => '')).includes('ant-btn-primary'),
                  });
                }
                return out;
              } catch { return 'btn_err'; }
            })();
            console.log('NET_DEBUG ' + JSON.stringify({
              item: item.name, kind: item.kind, remaining, nbBiz: nbBiz.length, nbUp,
              // O1c/rev100：重试点击失败的原因（首轮已点过、重试点不动时存在）
              secondSubmitFailed: (sig as any).__secondSubmitFailed || '',
              allNet: netLog.map((n) => ({u: n.url.slice(0, 90), s: n.status, r: n.requestId || ''})).slice(0, 25),
              btns: btnInfo, stillOpen,
              curModalCount: await curModal.count().catch(() => -1),
              // rev69 终极探针：目标元素究竟活在哪个 frame / 长什么样
              ownerFrame: await (async () => {
                try {
                  const h = await curModal.elementHandle();
                  if (!h) return 'no_handle';
                  const f = await h.ownerFrame();
                  return f ? String(f.url()).slice(0, 80) : 'null_frame';
                } catch (e) { return 'err:' + String(e).slice(0, 60); }
              })(),
              modalHtml: await curModal.evaluate((el: any) => String(el.outerHTML).slice(0, 200)).catch(() => 'html_err'),
              pageUrl: page.url(),
              framesCount: page.frames().length,
              bodyChildren: await page.evaluate(() =>
                Array.from(document.body.children).map((e: any) =>
                  e.tagName + '.' + String(e.className || '').slice(0, 24)).slice(0, 15)).catch(() => 'body_err'),
              reqs: reqLog.filter((r) => r.tk === netToken).slice(0, 12),
              allReqs: reqLog.slice(0, 25),
              errors: errLog.filter((e) => e.tk === netToken).slice(0, 8),
              shadowAudit: await page.evaluate(() => {
                let shadows = 0, modalInShadow = 0;
                const walk = (root: any, depth: number) => {
                  if (depth > 12) return;
                  const all = root.querySelectorAll('*');
                  for (const el of all) {
                    if (el.shadowRoot) {
                      shadows++;
                      if (el.shadowRoot.querySelector('.ant-modal-wrap, .ant-modal')) modalInShadow++;
                      walk(el.shadowRoot, depth + 1);
                    }
                  }
                };
                walk(document, 0);
                return {shadows, modalInShadow, iframes: document.querySelectorAll('iframe').length};
              }).catch(() => 'shadow_err'),
              // rev69：按 frame 审计——应用 UI 在 iframe#systemIframe 内，主 frame 查不到是正常的
              frameAudit: await (async () => {
                const out: any[] = [];
                try {
                  for (const f of page.frames()) {
                    out.push({
                      url: String(f.url()).slice(0, 60),
                      modals: await f.evaluate(() => document.querySelectorAll('.ant-modal-wrap').length).catch(() => -1),
                      confirms: await f.evaluate(() => document.querySelectorAll('.ant-modal-confirm').length).catch(() => -1),
                    });
                  }
                } catch { return 'frame_err'; }
                return out;
              })(),
              modalAudit: await page.evaluate(() => {
                const wraps = Array.from(document.querySelectorAll('.ant-modal-wrap'));
                return wraps.map((w) => ({
                  display: getComputedStyle(w).display,
                  modals: w.querySelectorAll('.ant-modal').length,
                  btns: w.querySelectorAll('.ant-modal-footer button').length,
                  inputs: w.querySelectorAll('.ant-modal input').length,
                }));
              }).catch(() => 'audit_err'),
            }));
            await page.screenshot({path: `submit_debug_${item.kind}_${Date.now()}.png`, fullPage: false}).catch(() => undefined);
            recAll(check, item.name, 'unverified',
                   nbBiz.length
                     ? `提交层：页面无拒绝证据，捕获到 ${nbBiz.length} 条业务写请求但均无拒绝语义（${check.rule}）`
                     : (nbUp > 0
                          ? `提交层：仅捕获到 ${nbUp} 条上传类请求，业务提交请求未出现（可能上传未完成或被前端拦截）（${check.rule}）`
                          : (remaining > 0
                               ? `提交层：无网络请求发出，仍被 ${remaining} 个必填项拦截（含目标字段为空的预期项）（${check.rule}）`
                               : `提交层：未观测到拒绝证据，也未捕获到提交请求（${check.rule}）`)));
          }
          // rev69：清理目标字段标记，避免影响后续用例项
          if (underItem) await underItem.evaluate((el: any) => { delete (el as any).dataset.rev69ut; }).catch(() => undefined);
          // 4) 提交成功会关掉表单弹窗 → 重开以继续后续检查
          if (!stillOpen) curModal = await openForm();
        } catch (error) {
          const msg = String(error);
          recAll(check, item.name, 'blocked', `提交层校验执行异常：${msg.slice(0, 120)}`);
          if (msg.includes('has been closed') || msg.includes('Target closed')) break;  // 页面已死，后续项无意义
        }
      }
      page.off('response', onNetResp);            // rev69：提交层结束，卸载网络监听
      console.log('SUBMIT_PHASE ' + JSON.stringify({
        queued: submitQueue.length,
        probe: submitQueue.filter((i: any) => i.kind === 'probe').length,
        required_empty: submitQueue.filter((i: any) => i.kind === 'required_empty').length,
      }));
    }

    // ===== 规则级负路径场景（P1-3 阶段 B）=====
    // 与上面的字段级 submitQueue 的**根本差别**（DEFECT-001 的形态）：
    //   违规主体不是"某个字段的值非法"，而是"字段值与**外部状态**的冲突"
    //   （编辑时追加一个已被其他活动占用的商品）。因此需要三段：
    //     setup（多步交互构造前置条件）→ submit（真实提交）→ assert（**服务端**是否拒绝）。
    // 判定只看服务端响应，不看页面提示：DEFECT-001 的页面表现是"提交成功"，
    // 服务端也返回 resultCode=0 —— 只有拿到响应体才能坐实规则未生效。
    // 只读阶段不产生写请求；本阶段是**唯一**会真实写库的段，因此写隔离必须收窄到
    // 场景自己声明的那个接口（见 negRules），并把"装不上隔离"处理成 fail-closed。
    const negativeStat = {configured: 0, executed: 0, pass: 0, blocked: 0, unverified: 0};
    async function runNegativeScenarios(): Promise<void> {
      const scenarios: any[] = Array.isArray(FORM_PLAN.negative_scenarios)
        ? FORM_PLAN.negative_scenarios : [];
      negativeStat.configured = scenarios.length;
      // 配置期被拒的场景必须**运行期可见**：否则"配了但从未执行"只能靠人比对配置与日志
      //（与 [plan-skip] 同一口径）。
      for (const rej of (FORM_PLAN.negative_scenario_rejects || []).slice(0, 20)) {
        console.log('[negative-skip] ' + JSON.stringify(rej));
      }
      if (!scenarios.length) return;
      const ownerOf = (sc: any) => ({
        case_id: String(sc.case_id || ''), rule_id: String(sc.rule_id || ''),
        source: 'negative_scenario',
      });
      const labelOf = (sc: any) => `[${sc.scenario_id}] ${sc.rule_ref || '规则级负路径'}`;
      const record = (sc: any, outcome: CheckOutcome, observed: string) => {
        negativeStat.executed += 1;
        negativeStat[outcome] += 1;
        recAll(ownerOf(sc), labelOf(sc), outcome, observed);
      };
      const escRe = (t: string) => t.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
      //: 按文本候选逐个尝试点击（菜单项 / 按钮 / 链接 / 任意可见文本）。
      //: 与手写脚本 demo2_script.ts 的 clickByText 同形——那份脚本已实测跑通该流程，
      //: 这里把它固化进执行器，使同类场景不必再手写。
      const clickByLabels = async (labels: string[]): Promise<boolean> => {
        for (const text of labels) {
          if (!text) continue;
          const rx = new RegExp(escRe(text));
          const candidates = [
            anyScope.locator(`li[role="menuitem"]:has-text("${text}")`).first(),
            anyScope.getByRole('button', {name: rx}).first(),
            anyScope.getByRole('link', {name: rx}).first(),
            anyScope.getByText(text, {exact: false}).last(),
          ];
          for (const candidate of candidates) {
            if (!(await candidate.count().catch(() => 0))) continue;
            try {
              await candidate.waitFor({state: 'visible', timeout: 2500});
              await candidate.click({timeout: 5000});
              return true;
            } catch { /* 该候选不可点，试下一个 */ }
          }
        }
        return false;
      };
      const closeAnyForm = async () => {
        // 字段级阶段打开的是"新建"表单；规则级场景通常从**列表页**自己的入口进入
        //（如点「编辑」）。先尽力关掉当前容器，关不掉也**不阻断**——由 setup 步骤
        // 自己去点目标入口（配置负责，平台不替用户猜）。
        try {
          const cancel = anyScope.locator('.ant-modal-footer button')
            .filter({hasText: /取\s*消|关\s*闭|Cancel/i}).first();
          if ((await cancel.count().catch(() => 0)) && await cancel.isVisible().catch(() => false)) {
            await cancel.click({timeout: 3000}).catch(() => undefined);
            await page.waitForTimeout(800);
          }
        } catch { /* 忽略 */ }
        try { await page.keyboard.press('Escape'); } catch { /* 忽略 */ }
        await page.waitForTimeout(400);
      };
      const runSetup = async (sc: any): Promise<string> => {
        const steps: any[] = Array.isArray(sc.setup) ? sc.setup : [];
        for (let i = 0; i < steps.length; i += 1) {
          const step = steps[i] || {};
          const action = String(step.action || '');
          const where = `${sc.scenario_id}#setup[${i}] ${action}`;
          if (action === 'wait') {
            await page.waitForTimeout(Math.max(0, Number(step.ms) || 1000));
            continue;
          }
          if (action === 'click' || action === 'append') {
            const labels = Array.isArray(step.labels) ? step.labels.map((s: any) => String(s)) : [];
            if (!(await clickByLabels(labels))) {
              return `${where}：未找到或无法点击 ${JSON.stringify(labels)}`;
            }
            await page.waitForTimeout(1000);
            continue;
          }
          if (action === 'select_row') {
            // 第 0 个可见 checkbox 通常是"全选"，row_index 由配置给出——
            // 平台**不**替用户加减一（两套约定必然出错），只做越界校验并如实报错。
            const boxes = anyScope.locator('input[type=checkbox]:visible');
            const total = await boxes.count().catch(() => 0);
            const idx = Number(step.row_index);
            if (!total) return `${where}：当前作用域没有可见的 checkbox`;
            if (!(idx >= 0 && idx < total)) {
              return `${where}：row_index=${idx} 越界（可见 checkbox 共 ${total} 个）`;
            }
            // rev98c（实测 TR-329）：antd checkbox input 被 inner 方框覆盖，
            // 直接点 input 过不了 Playwright 命中检测 ⇒ 超时静默、行未选中。
            // 改点其 .ant-checkbox 容器，isChecked 自证，未选中则合成 click 兜底。
            const boxInput = boxes.nth(idx);
            if (!(await boxInput.isChecked().catch(() => false))) {
              const wrap = boxInput.locator('xpath=..');
              const wrapCls = String(await wrap.getAttribute('class').catch(() => ''));
              if (wrapCls.includes('ant-checkbox')) {
                await wrap.click({timeout: 8000}).catch(() => undefined);
              } else {
                await boxInput.click({timeout: 8000}).catch(() => undefined);
              }
              if (!(await boxInput.isChecked().catch(() => false))) {
                await boxInput.evaluate((el: any) => el.click()).catch(() => undefined);
              }
            }
            if (!(await boxInput.isChecked().catch(() => false))) {
              return `${where}：checkbox 点击后仍未选中（antd 结构可能改版）`;
            }
            await page.waitForTimeout(1000);
            continue;
          }
          if (action === 'fill') {
            const field = String(step.field || '');
            if (!field) return `${where}：fill 缺少 field`;
            // rev98：fill 与字段级阶段同权使用定位器表的 selector_hint——
            // 实测（CS email 弹窗 #emails/#remark）弹窗控件不在 .ant-form-item 结构里，
            // 空 def 走 has-text 兜底永远落空 ⇒ 场景在 setup 期就 unverified。
            const setupDefs = ((FORM_PLAN.locators || {}).fields) || {};
            const input = await fieldControl(anyScope, field, setupDefs[field] || {});
            if (!(await input.count().catch(() => 0))) return `${where}：未定位到字段「${field}」`;
            await input.fill(String(step.value || ''), {timeout: OP_T});
            await input.blur({timeout: OP_T}).catch(() => undefined);
            continue;
          }
          if (action === 'click_selector') {
            // rev103：按原始 CSS 点击（无文本的图标按钮等，如列表工具条 filter 图标）。
            // selector 支持 Playwright 扩展（:visible / >> nth=-1 等）。
            const selCs = String(step.selector || '').trim();
            if (!selCs) return `${where}：click_selector 缺少 selector`;
            const elCs = anyScope.locator(selCs).first();
            if (!(await elCs.count())) return `${where}：未定位到 ${selCs}`;
            await elCs.click({timeout: 8000});
            await page.waitForTimeout(Math.max(300, Number(step.ms) || 800));
            continue;
          }
          if (action === 'select_option') {
            // rev103：antd Select 交互原语（filter 面板等 list 功能点必需）。
            // selector=下拉根 CSS；labels=选项文本候选。下拉渲染在 portal，
            // 必须用 anyScope 同一文档查询（rev92 的既有教训）。
            const sel = String(step.selector || '').trim();
            if (!sel) return `${where}：select_option 缺少 selector（下拉根 CSS）`;
            const root = anyScope.locator(sel).first();
            if (!(await root.count())) return `${where}：未定位到下拉根 ${sel}`;
            await root.click({timeout: 8000}).catch(() => undefined);
            await page.waitForTimeout(600);
            const optLabels = Array.isArray(step.labels)
              ? step.labels.map((s: any) => String(s)) : [];
            let picked = false;
            for (const optText of optLabels) {
              const opt = anyScope.locator(
                `.ant-select-dropdown:visible .ant-select-item:has-text("${optText}"),`
                + ` .ant-select-dropdown:visible li:has-text("${optText}")`,
              ).first();
              if (!(await opt.count())) continue;
              await opt.click({timeout: 5000}).catch(() => undefined);
              picked = true;
              break;
            }
            if (!picked) return `${where}：下拉选项 ${JSON.stringify(optLabels)} 未命中`;
            await page.waitForTimeout(400);
            continue;
          }
          return `${where}：未知动作（解析器本应在计划期拒绝该场景）`;
        }
        return '';
      };
      // ---- 写隔离：规则只来自场景声明的目标接口 ----
      // 设计文档 §四要求"白名单收窄到单接口"：规则级场景需要真实提交，
      // 但绝不允许它顺带产生其它未知写副作用。
      // 注意语义差异（实测 DEFECT-001）：`api_path_hint` 是**响应匹配用的 URL 子串**，
      // 而放行规则按 `host 后缀 + path 前缀` 匹配——真实请求打在**网关主机**上、
      // 路径还带网关前缀，子串当不了前缀。因此以 `allow` 为准，缺省才退回
      // "同源直连、路径等于 hint"的简单情形。
      const negRules: Array<{host?: string; path_prefix?: string; methods?: string[]}> = [];
      for (const sc of scenarios) {
        // rev99：写隔离放行规则两种极性同构——accept 场景同样收窄到声明的单接口。
        const expectX = ((sc.expect_accept || {}).api_path_hint)
          ? sc.expect_accept : (sc.expect_reject || {});
        const allow = expectX.allow || {};
        const prefix = String(allow.path_prefix || expectX.api_path_hint || '');
        if (!prefix) continue;
        const allowMethods = Array.isArray(allow.methods) ? allow.methods : [];
        negRules.push({
          host: String(allow.host_suffix || pageHost),
          path_prefix: prefix,
          methods: allowMethods.length
            ? allowMethods.map((m: any) => String(m).toUpperCase())
            : ['POST', 'PUT', 'PATCH'],
        });
      }
      const negBlocked: Array<{method: string; url: string; kind: string}> = [];
      const onNegRoute = async (route: any) => {
        try {
          const request = route.request();
          const method = String(request.method()).toUpperCase();
          const url = String(request.url());
          const contentType = String((request.headers() || {})['content-type'] || '');
          if (isolationDecision(method, url, negRules, [], contentType) === 'continue') {
            await route.continue();
            return;
          }
          const kind = blockedKind(url, contentType, pageHost);
          negBlocked.push({method, url: url.slice(0, 200), kind});
          console.log('ISOLATION_BLOCKED ' + JSON.stringify(
            {method, url: url.slice(0, 200), kind, phase: 'negative_scenario'}));
          await route.abort();
        } catch {
          await route.abort();
        }
      };
      let routeInstalled = false;
      try {
        if (typeof context.route === 'function') {
          await context.route('**/*', onNegRoute);
          routeInstalled = true;
        }
      } catch (error) {
        console.log('[negative-skip] ' + JSON.stringify(
          {scenario_id: '', reason: '写隔离安装失败', detail: String(error).slice(0, 160)}));
      }
      try {
        if (!routeInstalled) {
          // fail-closed：装不上隔离就**不提交**。规则级场景是要真实写库的，
          // "无隔离照样提交"会把一次断言失败升级成 UAT 数据污染。
          for (const sc of scenarios) {
            record(sc, 'unverified',
              `规则级负路径：写隔离策略未安装成功，为避免未知写副作用拒绝执行（${labelOf(sc)}）`);
          }
          return;
        }
        for (const sc of scenarios) {
          const label = labelOf(sc);
          const expect = sc.expect_reject || {};
          // rev99 正路径极性：expect_accept 与 expect_reject 互斥（计划期强制），
          // 这里按 accept 优先取目标接口标识——否则 accept 场景的 hint 为空串会
          // 让 includes('') 匹配到**每一个**非 GET 响应，证据被噪声淹没。
          const acceptCfg = (sc.expect_accept && sc.expect_accept.api_path_hint)
            ? sc.expect_accept : null;
          const hint = String((acceptCfg || expect).api_path_hint || '');
          const codeNot = String(expect.result_code_not == null ? '0' : expect.result_code_not);
          const msgs: string[] = Array.isArray(expect.message_contains)
            ? expect.message_contains.map((s: any) => String(s)) : [];
          if (typeof (page as any).isClosed === 'function' && (page as any).isClosed()) {
            record(sc, 'unverified', `规则级负路径：页面已关闭，未能执行（${label}）`);
            continue;
          }
          try {
            // 进入场景前先关掉字段级阶段打开的那个"新建"表单：
            // setup 通常从**列表页**自己的入口进（如点「编辑」），表单会遮住列表页。
            // 但 ``target_entry=create`` 的场景**就是**要在这个已打开的新建表单里操作，
            // 关掉反而错——平台不替用户决定，由配置语义说了算。
            if (String(sc.target_entry || 'custom') !== 'create') await closeAnyForm();
            // rev102：场景间**行选中状态隔离**——清掉上一轮残留的列表勾选。
            // 实测（TR-337）：场景共享页面状态，"未选中应阻止"类场景继承了上一轮的
            // 勾选，请求真实发出且被受理 ⇒ 判定层如实判「受理了应被拒绝的请求」——
            // 这是对 SUT 的**假缺陷指控**。每个场景自声明 setup，残留选择是执行假象。
            // 注意：每次点击会触发 React 重渲染、后续复选框 detach——必须每轮**重新查询**
            // 且给每个小操作带上显式短超时（默认 30s 超时 × N 个 = 挂死整轮，实测 TR-339）。
            // 注意 2（实测探针 2026-10-14）：`:checked` 会同时命中**隐藏固定列克隆表**的
            // input（antd fixed-left clone 排在 DOM 前面）——必须加 `:visible`，否则点到
            // 隐藏克隆、可见勾选原封不动，清选形同虚设（TR-340 假缺陷指控的根源）。
            try {
              for (let guard = 0; guard < 30; guard++) {
                const checkedNow = anyScope.locator(
                  '.ant-table-tbody input[type=checkbox]:checked:visible');
                if (!(await checkedNow.count().catch(() => 0))) break;
                const cb = checkedNow.first();
                const wrap = cb.locator('xpath=..');
                const wrapCls = String(await wrap.getAttribute('class', {timeout: 800}).catch(() => ''));
                await (wrapCls.includes('ant-checkbox') ? wrap : cb)
                  .click({timeout: 3000}).catch(() => undefined);
                await page.waitForTimeout(300);
              }
              await page.waitForTimeout(400);
            } catch { /* 清选失败不阻断——由场景 setup/判定自然暴露 */ }
            const setupErr = await runSetup(sc);
            if (setupErr) {
              record(sc, 'unverified',
                `规则级负路径：前置条件未构造成功 —— ${setupErr}（${label}）`);
              continue;
            }
            // 提交窗口取证：request 级（区分"请求没发出"）+ response 级（服务端结论）
            const hits: Array<{url: string; status: number; requestId: string; body: string; method: string}> = [];
            const reqHits: Array<{url: string; method: string; body: string}> = [];
            const blockedBefore = negBlocked.length;
            const onResp = (resp: any) => {
              try {
                const rurl = String(resp.url());
                const method = String(resp.request().method()).toUpperCase();
                if (!rurl.includes(hint) || method === 'GET') return;
                const headers = resp.headers() || {};
                const requestId = String(headers['requestid'] || headers['x-request-id'] || '');
                hits.push({url: rurl.slice(0, 160), status: resp.status(), requestId,
                           body: '', method});
                const slot = hits[hits.length - 1];
                resp.text().then((t: string) => { slot.body = String(t || '').slice(0, 600); })
                  .catch(() => undefined);
              } catch { /* 取证异常不得干扰判定 */ }
            };
            const onReq = (req: any) => {
              try {
                const rurl = String(req.url());
                const method = String(req.method()).toUpperCase();
                if (!rurl.includes(hint) || method === 'GET') return;
                reqHits.push({url: rurl.slice(0, 160), method,
                              body: String(req.postData() || '').slice(0, 300)});
              } catch { /* 同上 */ }
            };
            page.on('response', onResp);
            page.on('request', onReq);
            try {
              const labels = (Array.isArray(sc.submit_label) && sc.submit_label.length)
                ? sc.submit_label.map((s: any) => String(s))
                : ['提 交', '提交', '保 存', '确 定'];
              if (!(await clickByLabels(labels))) {
                record(sc, 'unverified',
                  `规则级负路径：未找到提交按钮 ${JSON.stringify(labels)}（${label}）`);
                continue;
              }
              // 提交窗口：给服务端往返 + 响应体落地留时间（手写脚本实测 8s 足够）
              await page.waitForTimeout(8000);
            } finally {
              page.off('response', onResp);
              page.off('request', onReq);
            }
            const blockedNow = negBlocked.length - blockedBefore;
            const hit = hits.length ? hits[hits.length - 1] : null;
            if (!hit) {
              const why = blockedNow
                ? `该场景的写请求被写隔离策略阻断（${blockedNow} 条未到达服务端）`
                : (reqHits.length
                    ? `请求已发出（${reqHits[0].method} ${reqHits[0].url}）但在窗口内未收到响应`
                    : '提交后窗口内未捕获到目标接口的请求');
              record(sc, 'unverified',
                `规则级负路径：未能取得服务端结论 —— ${why}；期望规则「${sc.rule_ref || sc.scenario_id}」未被验证（${label}）`);
              continue;
            }
            // [O9-NEG-JUDGE-BEGIN] 负路径判定（契约测试逐字抽取此区间；勿删标记）
            const parsed = (() => {
              try { return JSON.parse(hit.body || '{}'); } catch { return null; }
            })();
            const bodyCode = parsed && parsed.resultCode != null ? String(parsed.resultCode) : null;
            const bodyMsg = parsed ? String(parsed.resultMsg || parsed.message || '') : '';
            const statusNum = Number(hit.status);
            const httpOk = statusNum >= 200 && statusNum < 400;   // HTTP 层"受理"（2xx/3xx）
            const serverFault = statusNum >= 500;                 // 服务端故障：非"规则拒绝"形态
            const envelopeRejected = bodyCode !== null && bodyCode !== codeNot;
            //: ★ 接线 ``RejectExpect.http_ok``（此前**只序列化、运行期从不读取**的死配置）。
            //:   true（默认）= 期望服务端以「HTTP 2xx + 业务信封 resultCode≠期望」的方式拒绝；
            //:   false       = 期望服务端直接以 HTTP 非 2xx（如 400/403）拒绝。
            const expectHttpOk = expect.http_ok !== false;
            const rejectedByHttp = !httpOk && !serverFault;       // 4xx：HTTP 层拒绝
            const msgHit = msgs.find((m) => m && hit.body.includes(m)) || '';
            const evidence = `HTTP ${hit.status} ${hit.url} requestId:${hit.requestId || '-'}`
              + ` body:${String(hit.body || '').slice(0, 160)}`;
            if ((sc.expect_accept || {}).api_path_hint) {
              // [rev99] 期望受理极性（与期望拒绝互斥，计划期强制）：
              //   5xx ⇒ 疑似服务端缺陷（与拒绝极性同判——期望受理的请求也崩了，同样是缺陷）；
              //   2xx/3xx + resultCode==期望 ⇒ pass「按期望受理」；
              //   2xx + resultCode≠期望 ⇒ blocked「拒绝了应受理的请求」（规则未生效）；
              //   4xx ⇒ blocked「以 HTTP 非 2xx 拒绝了应受理的请求」。
              const expectCode = String((sc.expect_accept as any).result_code != null
                ? (sc.expect_accept as any).result_code : '0');
              if (serverFault) {
                record(sc, 'blocked',
                  `疑似服务端缺陷：目标接口返回 HTTP ${statusNum}（服务端故障），期望受理（expect_accept）的请求也未被正常处理 —— `
                  + `规则「${sc.rule_ref || sc.scenario_id}」无法验证；${evidence}；`
                  + `定位凭据：requestId=${hit.requestId || '未在响应头暴露'}`);
              } else if (httpOk && bodyCode !== null && bodyCode === expectCode) {
                record(sc, 'pass',
                  `服务端按期望受理（配置 expect_accept，resultCode=${bodyCode}）；${evidence}（${label}）`);
              } else if (httpOk) {
                record(sc, 'blocked',
                  `疑似缺陷坐实：服务端拒绝了应受理的请求 —— 规则「${sc.rule_ref || sc.scenario_id}」未生效`
                  + `（实际 resultCode=${bodyCode === null ? '无（响应无业务信封）' : bodyCode}，期望 ${expectCode}）；`
                  + `${evidence}；定位凭据：requestId=${hit.requestId || '未在响应头暴露'}`);
              } else {
                record(sc, 'blocked',
                  `疑似缺陷坐实：服务端以 HTTP ${statusNum} 拒绝了应受理的请求 —— 规则「${sc.rule_ref || sc.scenario_id}」未生效；`
                  + `${evidence}；定位凭据：requestId=${hit.requestId || '未在响应头暴露'}`);
              }
            } else if (serverFault) {
              // ① 5xx = 服务端崩溃：既非"按规则拒绝"、也非"受理了非法请求"。
              //    旧实现把 ``httpOk`` 硬编码成 2xx/3xx，5xx 会一路穿透到最后一个 else，
              //    被记成 pass「服务端按规则拒绝」⇒ **"服务端崩溃"类缺陷永远绿**（67197 的形态）。
              record(sc, 'blocked',
                `疑似服务端缺陷：目标接口返回 HTTP ${statusNum}（服务端故障），既非正常受理也非规则拒绝 —— `
                + `规则「${sc.rule_ref || sc.scenario_id}」是否生效无法从该响应判定；`
                + `期望 resultCode≠${codeNot}。${evidence}；`
                + `定位凭据：requestId=${hit.requestId || '未在响应头暴露'}`);
            } else if (!envelopeRejected && httpOk) {
              // 服务端受理了"应被拒绝"的请求 → 该业务规则未生效（DEFECT-001 的形态）
              record(sc, 'blocked',
                `疑似缺陷坐实：服务端受理了应被拒绝的请求 —— 规则「${sc.rule_ref || sc.scenario_id}」未生效；`
                + `${evidence}；建议 UAT 数据佐证并登记缺陷。`
                + `定位凭据：requestId=${hit.requestId || '未在响应头暴露'}`);
            } else if (rejectedByHttp && !expectHttpOk && (!msgs.length || msgHit)) {
              // ③ 配置声明期望「HTTP 层拒绝」，实际确实以非 2xx 拒绝 ⇒ 按期望拒绝。
              record(sc, 'pass',
                `服务端按期望以 HTTP ${statusNum} 拒绝（配置 http_ok=false）；`
                + (msgHit ? `文案命中「${msgHit}」` : '；配置未给出文案关键词')
                + `；${evidence}（${label}）`);
            } else if (msgs.length && !msgHit) {
              // 确实被拒了，但**驳回原因与期望规则不符**（可能是权限/参数等其它原因）：
              // 既不能声称"本规则已生效"，也不构成"本规则未生效"——如实记 unverified，
              // 正是设计文档 §四"误判为缺陷"风险的对策。
              record(sc, 'unverified',
                `规则级负路径：服务端已拒绝，但驳回文案未命中期望关键词 ${JSON.stringify(msgs)}`
                + `（实际 resultCode=${bodyCode === null ? '无' : bodyCode}`
                + ` resultMsg=${bodyMsg.slice(0, 60) || '无'}）—— 无法确认为本规则生效（${label}）；${evidence}`);
            } else if (!httpOk) {
              // ④ 服务端以 HTTP 非 2xx（4xx）拒绝，但配置期望的是「HTTP 2xx + 业务码≠期望」
              //    ⇒ 驳回**方式**与期望不符，无法确认是本规则生效（鉴权/权限/参数错同样 4xx）
              //    ⇒ 保守记 unverified，不冒充"本规则已生效"。
              record(sc, 'unverified',
                `规则级负路径：服务端以 HTTP ${statusNum} 拒绝，与期望的拒绝方式不符`
                + `（配置期望 HTTP 2xx 且 resultCode≠${codeNot}，http_ok=${String(expectHttpOk)}）`
                + `（实际 resultCode=${bodyCode === null ? '无' : bodyCode}）`
                + ` —— 无法确认为本规则生效（${label}）；${evidence}`);
            } else {
              record(sc, 'pass',
                `服务端按规则拒绝：判据 resultCode=${bodyCode === null ? `HTTP ${hit.status}` : `${bodyCode}≠${codeNot}`}`
                + (msgHit ? `；文案命中「${msgHit}」` : '；配置未给出文案关键词')
                + `；${evidence}（${label}）`);
            }
            // [O9-NEG-JUDGE-END]
          } catch (error) {
            const msg = String(error);
            record(sc, 'blocked', `规则级负路径执行异常：${msg.slice(0, 200)}（${label}）`);
            if (msg.includes('has been closed') || msg.includes('Target closed')) return;
          }
        }
      } finally {
        try { await (context as any).unroute('**/*', onNegRoute); } catch { /* 忽略 */ }
        console.log('NEGATIVE_PHASE ' + JSON.stringify(
          {...negativeStat, blocked_requests: negBlocked.length,
           isolation_kinds: {
             upload_blocked: negBlocked.filter((b) => b.kind === 'upload_blocked').length,
             non_upload_api_blocked: negBlocked.filter((b) => b.kind === 'non_upload_api_blocked').length,
             unknown_request_blocked: negBlocked.filter((b) => b.kind === 'unknown_request_blocked').length,
           }}));
      }
    }
    await runNegativeScenarios();

    // ===== 上传校验：优先独立上下文（复核意见：隔离上传与业务上下文）=====
    const uploadChecks = FORM_PLAN.checks.filter((c: any) => c.type === 'upload_file');
    if (uploadChecks.length) {
      let isoContext: any = null;
      let isoLabel = 'main_fallback';
      let isoReason = '';
      const browser = context.browser();
      try {
        if (!browser) throw new Error('browser 不可用');
        const state = await context.storageState();
        isoContext = await browser.newContext({storageState: state});
        const isoPage = await isoContext.newPage();
        // 先完成进入与开表单（登录/加载本身需要写请求），**再**安装隔离策略
        const isoScope = await enterStorageAccountList(isoPage, isoContext);
        const isoModal = await openForm(isoPage, isoScope);
        await installIsolation(isoContext);
        isoLabel = 'isolated';
        await runUploadPhase(isoPage, isoScope, isoModal, uploadChecks, isoLabel);
      } catch (error) {
        isoReason = String(error).slice(0, 160);
        // 回退：主上下文（并如实记录"未隔离"）
        await installIsolation(context);
        await runUploadPhase(page, anyScope, modal, uploadChecks, isoLabel);
      } finally {
        if (isoContext) { await isoContext.close().catch(() => undefined); }
      }
      console.log('UPLOAD_CONTEXT ' + JSON.stringify({label: isoLabel, reason: isoReason}));
    }

    const summary = {
      total: formResults.length,
      pass: formResults.filter((r) => r.outcome === 'pass').length,
      unverified: formResults.filter((r) => r.outcome === 'unverified').length,
      blocked: formResults.filter((r) => r.outcome === 'blocked').length,
      submit_phase: {
        queued: submitQueue.length,
        pass: formResults.filter((r) => String(r.observed).startsWith('提交层验证') && r.outcome === 'pass').length,
        suspected_defect: formResults.filter(
          (r) => r.outcome === 'blocked' && String(r.observed).includes('疑似缺陷')).length,
      },
      // 规则级负路径（P1-3 阶段 B）：与字段级 submit_phase **分开计数**——
      // 两者的入队条件、写风险与判定口径都不同，混在一起会让"到底跑了几条规则级场景"看不出来。
      negative_scenario_phase: {
        ...negativeStat,
        rejected_at_plan: (FORM_PLAN.negative_scenario_rejects || []).length,
      },
      skipped_no_locator: FORM_PLAN.skipped_count,
      // 只有一个数字无法复核："跳过 9 条"到底是哪几类原因
      skipped_by_reason: (() => {
        const counts: Record<string, number> = {};
        for (const s of (FORM_PLAN.skipped || [])) {
          const key = String((s as any).reason || 'unknown').slice(0, 28);
          counts[key] = (counts[key] || 0) + 1;
        }
        return counts;
      })(),
      plan_checks: FORM_PLAN.checks.length,
      attributed_checks: FORM_PLAN.checks.filter(
        (c: any) => c.case_id || (Array.isArray(c.owners) && c.owners.length)).length,
      // 配置驱动的计划级检查：来自定位器配置（如 default_selected 的默认值声明），
      // 本身没有对应用例断言——因此**不算**"本该有归属却没有"，由台账单独归类。
      config_only_checks: FORM_PLAN.checks.filter(
        (c: any) => c.source === 'locator_config'
          && !(c.case_id || (Array.isArray(c.owners) && c.owners.some((o: any) => o && o.case_id))),
      ).length,
      // 上传类检查**不计入 total**：它们只输出原始证据（UPLOAD_EVIDENCE），
      // 由服务端分类器判定后才写入用例结果（避免浏览器侧假通过）。
      upload_evidence: uploadEvidenceCount,
      isolation_blocked: isolationBlocked.length,
      isolation_blocked_kinds: {
        upload_blocked: isolationBlocked.filter((b) => b.kind === 'upload_blocked').length,
        non_upload_api_blocked: isolationBlocked.filter((b) => b.kind === 'non_upload_api_blocked').length,
        unknown_request_blocked: isolationBlocked.filter((b) => b.kind === 'unknown_request_blocked').length,
      },
    };
    console.log('COMPILED_ASSERTION_SUMMARY ' + JSON.stringify(summary));
    // 结构性失败（blocked）视为硬失败：定位器失效必须暴露，而不是静默通过
    expect(summary.blocked, `编译断言执行受阻：${summary.blocked} 项`).toBe(0);
  }
  // 说明：客户端层断言全程只读；「期望拒绝」未验证项进入负路径提交层（口径经用户 2026-09-25 确认），
  // 只提交空必填/非法探针值，正常不产生合法业务数据；被测端若接受则按疑似缺陷暴露。
  await runFormChecks();
"""


def render_form_executor(plan: dict[str, Any], *, default_phone: str = "18320101265",
                         upload_allow_hosts: list[str] | None = None,
                         upload_allow_rules: list[dict[str, Any]] | None = None) -> str:
    """渲染表单校验执行器（TS 片段）：客户端层只读 + 「期望拒绝」项负路径提交层。

    上传隔离策略（安全边界）：
    - ``multipart/form-data`` 一律阻断；
    - GET 放行；
    - 其他写请求**必须命中** ``upload_allow_rules``（host / path_prefix / methods），
      默认空 → 所有写请求都阻断（不默认放行同源写，避免产生未知业务写副作用）。
    - ``upload_allow_hosts`` 仅作兼容：表示"这些主机的 GET 放行"，不再放行写请求。
    """
    payload = json.dumps(
        {"locators": plan.get("locators"), "checks": plan.get("checks"),
         "skipped": plan.get("skipped"), "skipped_count": plan.get("skipped_count", 0),
         # P1-3 阶段 B：规则级负路径场景随计划下发给执行器。
         # ``negative_scenario_rejects`` 一并下发——配置期被拒的场景必须在运行期可见，
         # 否则"配了但没跑"只能靠人去比对配置与日志。
         "negative_scenarios": plan.get("negative_scenarios") or [],
         "negative_scenario_rejects": plan.get("negative_scenario_rejects") or [],
         "upload_policy": {
             "allow_rules": list(upload_allow_rules or []),
             "allow_hosts": list(upload_allow_hosts or []),
         },
         "upload_fixtures": {
             "workbook_parts": xlsx_parts_base64(),
             "workbook_file": base64.b64encode(minimal_xlsx_bytes()).decode("ascii"),
             "xls_file": xls_base64(),
             "bad_extension_part": base64.b64encode(bad_extension_bytes()).decode("ascii"),
         }},
        ensure_ascii=False,
    )
    return (
        _FORM_EXECUTOR_TS
        .replace("@@FORM_PLAN@@", payload)
        .replace("@@DEFAULT_PHONE@@", default_phone)
    )
