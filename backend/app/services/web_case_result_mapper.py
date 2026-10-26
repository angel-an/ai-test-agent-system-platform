# -*- coding: utf-8 -*-
"""把编译断言的执行输出，映射成**用例级**执行结果。

背景（实测问题）：一个批次 43/53 条用例只编译成**一个**脚本执行，运行记录里
只有"锚点用例"一行 `test_run_test_cases`，其余用例在页面上永远显示"新建"——
执行结论实际只存在于脚本 stdout 的 `COMPILED_ASSERTION` 行里，没有按用例落库。

本模块只做纯计算（无 IO，便于回归测试）：
stdout + (规则号 → 用例) → 每条用例的状态与明细。
状态语义（沿用平台既有取值，不发明新值）：
- PASSED        ：该用例的全部断言都观测到通过
- SKIPPED       ：断言存在但**没能验证**（unverified，如仅服务端校验）——不得算通过
- BLOCKED       ：断言执行受阻（定位器失效等结构性失败）
- FAILED        ：无通过且出现过 unverified/blocked 的混合情形
- NOT_EXECUTED  ：该用例没有任何可执行断言（模板类用例的真实状态）
"""

from __future__ import annotations

import json
from typing import Any, Iterable, Mapping, Optional

from app.services.web_upload_evidence import upload_case_items

PASSED = "PASSED"
FAILED = "FAILED"
SKIPPED = "SKIPPED"
BLOCKED = "BLOCKED"
NOT_EXECUTED = "NOT_EXECUTED"

_MARKER = "COMPILED_ASSERTION "


def parse_compiled_assertions(stdout: str) -> list[dict[str, Any]]:
    """从脚本 stdout 里解析逐条断言行（旧脚本可能没有 rule_id/case_id）。"""
    out: list[dict[str, Any]] = []
    for line in (stdout or "").splitlines():
        idx = line.find(_MARKER)
        if idx < 0:
            continue
        try:
            payload = json.loads(line[idx + len(_MARKER):])
        except (ValueError, TypeError):
            continue
        if isinstance(payload, dict) and payload.get("name"):
            out.append({
                "name": str(payload.get("name")),
                "outcome": str(payload.get("outcome") or ""),
                "observed": str(payload.get("observed") or ""),
                "rule_id": str(payload.get("rule_id") or ""),
                "case_id": str(payload.get("case_id") or ""),
            })
    return out


def stdout_has_executable_assertions(stdout: str) -> bool:
    """脚本 stdout 里是否产出过**任何**可判定的断言证据。

    判据与用例级落库**同源**：``COMPILED_ASSERTION`` 行或上传证据（UPLOAD_EVIDENCE）。
    零断言脚本即使 playwright 汇总 ``1 passed``（只验证了"入口能打开"），
    也不得据此把用例判成 PASSED —— 实测 TR-485/486（商品/支付
    ``form_executable_checks=0``、无 COMPILED_ASSERTION 行仍 ``1 passed``，
    用例被判通过，即"空跑绿"）。
    """
    if not (stdout or "").strip():
        return False
    if parse_compiled_assertions(stdout):
        return True
    return bool(upload_case_items(stdout))


def aggregate_status(outcomes: Iterable[str]) -> str:
    """把一组断言语义聚合成一个用例状态（宁可不判通过，也不虚报通过/失败）。

    执行层目前产出 ``pass`` / ``fail`` / ``unverified`` / ``blocked`` 四种结果。

    ⚠️ rev203（2026-10-26）修正：旧实现只认 ``blocked``，把 ``fail`` 落进 else 分支
    ⇒ 判成 **SKIPPED**。后果是**用例级永远不会出现 FAILED**：真实失败被记成
    "存在断言但没能验证"，与"没跑"混为一谈。实测 TR-830（店铺列契约 12/15、
    缺 3 列、脚本 expect 抛出）五条用例全被记成 SKIPPED，run 级 failed=0。

    语义边界要分清：
    - ``SKIPPED`` = 断言存在但**没能验证**（环境/定位/取值不可比）；
    - ``FAILED``  = 断言**验过且不成立** —— 这才是在指控产品缺陷，必须有 ``fail`` 才成立。
    """
    items = [o for o in outcomes if o]
    if not items:
        return NOT_EXECUTED
    if all(o == "pass" for o in items):
        return PASSED
    # 有任何一条断言明确不成立 → 该用例失败（混合 pass/unverified 不算，见下）
    if any(o == "fail" for o in items):
        return FAILED
    if any(o == "blocked" for o in items):
        return BLOCKED
    # 其余（纯 unverified，或 pass + unverified 混合）：不得判通过，也不得判失败
    return SKIPPED


def map_case_results(
    stdout: str,
    rule_to_case: Mapping[str, Any],
    *,
    case_ids: Iterable[str] = (),
    identifier_to_case: Optional[Mapping[str, str]] = None,
) -> dict[str, dict[str, Any]]:
    """输出 → {case_id: {"status", "assertions", "details"}}。

    归因规则（精确优先）：
    1. 断言行里的 ``case_id``（用例标识，如 TC-xxxxxx）→ 直接命中该用例；
    2. 没有 case_id（旧脚本）时回落到 ``rule_id``；此时 ``rule_to_case`` 的值
       **可以是列表**（一个规则号被多条用例共享，实测 5 个 id 覆盖 14 条用例）
       —— 共享的用例都会拿到同一结论，而不是只记到其中一条、把兄弟用例误标未执行。

    ``case_ids`` 用于把"没有任何断言的用例"显式标成 NOT_EXECUTED。
    """
    assertions = parse_compiled_assertions(stdout)
    # 上传类断言：浏览器只输出原始证据（UPLOAD_EVIDENCE），判定在这里完成
    # （见 web_upload_evidence.classify_upload：只认对应类型的客户端提示）。
    assertions.extend(upload_case_items(stdout))
    results: dict[str, dict[str, Any]] = {
        cid: {"status": NOT_EXECUTED, "assertions": 0, "details": []}
        for cid in case_ids
    }
    ident_map = identifier_to_case or {}

    def targets(item: dict[str, Any]) -> list[str]:
        if item["case_id"] and item["case_id"] in ident_map:
            return [ident_map[item["case_id"]]]
        raw = rule_to_case.get(item["rule_id"]) if item["rule_id"] else None
        if not raw:
            return []
        return [str(x) for x in (raw if isinstance(raw, (list, tuple, set)) else [raw])]

    for item in assertions:
        for case_id in targets(item):
            bucket = results.setdefault(
                case_id, {"status": NOT_EXECUTED, "assertions": 0, "details": []}
            )
            bucket["assertions"] += 1
            bucket["details"].append(item)

    for case_id, bucket in results.items():
        outcomes = [d["outcome"] for d in bucket["details"]]
        bucket["status"] = aggregate_status(outcomes)
        # 只留可读摘要，避免把整段 stdout 塞进数据库
        detail_text = "; ".join(
            "%s=%s%s" % (d["name"][:60], d["outcome"],
                         ("（%s）" % d["observed"][:60]) if d["observed"] else "")
            for d in bucket["details"][:5]
        )[:900]
        bucket["unverified_count"] = sum(1 for o in outcomes if o == "unverified")
        bucket["pass_count"] = sum(1 for o in outcomes if o == "pass")
        # 每条用例都必须有**可解释的明细**：没有断言时也要说明为什么未执行，
        # 否则状态列只剩一个标签，等于新的"灰色通过"。
        bucket["summary"] = detail_text or _explain_without_assertions(bucket["status"])
    return results


def summarize_report(
    case_results: Mapping[str, Mapping[str, Any]],
    *,
    plan_skipped: Iterable[Mapping[str, Any]] = (),
) -> dict[str, int]:
    """固定报告口径（复核意见 P1-9）。

    字段语义（**必须按此解读，不得互相替代**）：

    - ``rules_total``：有结果记录的用例数（= 规则用例分母；"有记录"≠"规则验证通过"）
    - ``passed``：全部断言都观测到通过的用例
    - ``failed``：判定为失败的用例
    - ``unverified``：**至少有一条断言未验证**的用例（客户端模式下接受类上传必然落在这里）
    - ``skipped_cases``：状态为 SKIPPED 的用例（当前语义下与 ``unverified`` 同集，保留分列以便将来区分）
    - ``blocked``：执行受阻（定位器失效 / 环境隔离失败等）
    - ``not_executed``：没有任何可执行断言的用例
    - ``skipped_assertions``：**计划层**跳过、根本没执行的断言条数（诊断信息，不计入通过率）

    恒等式：``passed + failed + skipped_cases + blocked + not_executed == rules_total``。
    """
    results = list(case_results.values())
    counts = {
        "rules_total": len(results),
        "passed": sum(1 for r in results if r.get("status") == PASSED),
        "failed": sum(1 for r in results if r.get("status") == FAILED),
        "skipped_cases": sum(1 for r in results if r.get("status") == SKIPPED),
        "blocked": sum(1 for r in results if r.get("status") == BLOCKED),
        "not_executed": sum(1 for r in results if r.get("status") == NOT_EXECUTED),
        "unverified": sum(
            1 for r in results
            if any(str(d.get("outcome")) == "unverified" for d in (r.get("details") or []))
        ),
        "skipped_assertions": len(list(plan_skipped)),
    }
    partition = (counts["passed"] + counts["failed"] + counts["skipped_cases"]
                 + counts["blocked"] + counts["not_executed"])
    counts["partition_ok"] = int(partition == counts["rules_total"])
    return counts


def _explain_without_assertions(status: str) -> str:
    """没有可执行断言时也要给出明确解释（而不是空明细）。"""
    if status == NOT_EXECUTED:
        # rev64：措辞明确"无法自动执行 ≠ 用例错误"，指向人工执行
        return "该用例没有可执行断言（叙述/模板类，或断言全部无法编译）→ 无法自动执行，需人工执行（非用例错误）"
    if status == SKIPPED:
        return "断言存在但均未能验证（未观测到可判定的客户端行为）"
    if status == BLOCKED:
        return "断言执行受阻（定位器失效或页面结构不符）"
    return "无可判读的断言结果"


def build_rule_to_case(cases: Iterable[Any]) -> dict[str, Any]:
    """(用例对象序列) → {规则号: 用例 id 或 [用例 id, ...]}。

    注意：一个规则号**可能被多条用例共享**（同一字段派生出的"边界值/必填/条件显示"等），
    因此这里返回列表——只留一条会把兄弟用例误判成"未执行"（实测 5 个 id 覆盖 14 条用例）。
    """
    mapping: dict[str, list[str]] = {}
    for case in cases:
        fields = getattr(case, "custom_fields", None) or {}
        rule_id = fields.get("requirement_rule_id") if isinstance(fields, Mapping) else None
        case_id = getattr(case, "id", None)
        if rule_id and case_id:
            mapping.setdefault(str(rule_id), []).append(str(case_id))
    return {k: (v[0] if len(v) == 1 else v) for k, v in mapping.items()}


def build_identifier_to_case(cases: Iterable[Any]) -> dict[str, str]:
    """{用例标识(TC-xxxxxx): 用例 id}——用于按标识精确归因。"""
    out: dict[str, str] = {}
    for case in cases:
        identifier = getattr(case, "identifier", None)
        case_id = getattr(case, "id", None)
        if identifier and case_id:
            out[str(identifier)] = str(case_id)
    return out
