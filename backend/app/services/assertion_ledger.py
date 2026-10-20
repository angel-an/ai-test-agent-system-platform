# -*- coding: utf-8 -*-
"""执行账目（assertion ledger）：把 stdout 里的断言账目变成**可查询的结构化数据**。

背景（2026-09-29 复核 P3）：一次批次跑完后，想知道"哪条断言 pass / unverified / 谁没被归属"
只能 grep stdout；TR-149 实测 `plan_checks=15 / attributed_checks=14`——有一条计划级检查
执行了却**不归属任何用例**，summary 里也没有任何提示（证据黑洞）。

本模块只做**纯解析与汇总**（无业务词、无项目信息），供 API 层查询：
- 逐条断言：name / outcome / observed / rule_id / case_id；
- 按用例汇总：total / pass / unverified / blocked；
- 汇总告警：未归属检查、unverified/blocked、环境阻断、上传证据未分类等。

两个断言来源都要收（2026-10-12 补齐第二个，此前是证据黑洞）：
1. ``COMPILED_ASSERTION`` —— 浏览器脚本直接给出结论的断言；
2. ``UPLOAD_EVIDENCE`` —— 上传类断言由浏览器只输出原始证据、服务端
   ``web_upload_evidence.classify_upload`` 判定后转成同构明细（与
   ``web_case_result_mapper`` 同源）。漏掉这一来源会让这类断言在账目里消失。
"""

from __future__ import annotations

import json
from typing import Any, Iterable

from app.services.web_upload_evidence import upload_case_items

_ASSERTION_PREFIX = "COMPILED_ASSERTION "
_SUMMARY_PREFIX = "COMPILED_ASSERTION_SUMMARY "
_COVERAGE_PREFIX = "COMPILED_COVERAGE "
_OBSERVATION_PREFIX = "[observation] "
_PLAN_SKIP_PREFIX = "[plan-skip] "
_ENV_BLOCKED_MARKER = "ENVIRONMENT_BLOCKED"

#: 配置驱动检查的来源标记（编译器对 defaults / list_checks 已显式标注）。
CONFIG_SOURCE = "locator_config"

#: 旧脚本里的配置驱动检查没有 ``source`` 标记（编译器只在部分类型上打了），
#: 但检查名是稳定的类型前缀。R5 归属时据此**兼容推断**，并在结果里标
#: ``source_inferred``，让"推断"与"显式声明"始终可区分。
#: 实测 TR-697：``visibility:排隊=on`` / ``visibility:外賣=on`` 两条即属此类。
CONFIG_CHECK_PREFIXES = (
    "default_selected:",
    "visibility:",
    "visible_now:",
    "list_visible:",
    "visible:",
)

_METRIC_OUTCOMES = ("pass", "fail", "unverified", "blocked")


def _json_after(line: str, prefix: str) -> Any:
    try:
        return json.loads(line.split(prefix, 1)[1])
    except Exception:  # noqa: BLE001 - 单行损坏不影响其余账目
        return None


def parse_job_ledger(stdout: str | None) -> dict[str, Any]:
    """解析单个作业的 stdout，返回逐条断言 + 摘要 + 覆盖率。"""
    assertions: list[dict[str, Any]] = []
    observations: list[dict[str, Any]] = []
    plan_skips: list[dict[str, Any]] = []
    summary: dict[str, Any] | None = None
    coverage: dict[str, Any] | None = None
    environment_blocked = False

    for line in (stdout or "").splitlines():
        if _ASSERTION_PREFIX in line:
            record = _json_after(line, _ASSERTION_PREFIX)
            if isinstance(record, dict):
                assertions.append({
                    "name": str(record.get("name") or ""),
                    "outcome": str(record.get("outcome") or ""),
                    "observed": str(record.get("observed") or ""),
                    "rule_id": str(record.get("rule_id") or ""),
                    "case_id": str(record.get("case_id") or ""),
                    "source": str(record.get("source") or ""),
                })
            continue
        if _SUMMARY_PREFIX in line:
            summary = _json_after(line, _SUMMARY_PREFIX)
            continue
        if _COVERAGE_PREFIX in line:
            coverage = _json_after(line, _COVERAGE_PREFIX)
            continue
        if _OBSERVATION_PREFIX in line:
            record = _json_after(line, _OBSERVATION_PREFIX)
            if isinstance(record, dict):
                observations.append(record)
            continue
        if _PLAN_SKIP_PREFIX in line:
            record = _json_after(line, _PLAN_SKIP_PREFIX)
            if isinstance(record, dict):
                plan_skips.append(record)
            continue
        if _ENV_BLOCKED_MARKER in line:
            environment_blocked = True

    # 上传类断言：浏览器脚本对这类断言的约定是**只输出原始证据**（``UPLOAD_EVIDENCE`` 行，
    # 不做通过/未验证判定），判定在服务端 ``web_upload_evidence.classify_upload`` 完成——
    # 与 ``web_case_result_mapper`` 同源。本模块此前只解析 ``COMPILED_ASSERTION``，会让这类
    # 断言在账目里整条消失（2026-10-12 TR-267 实测：6 条资产 coverage.assertions=2 而账目归集
    # 0 条、summary.total=0），报告据此把"压根没验证"显示成"已完成"——是证据黑洞而非业务结论。
    upload_items = upload_case_items(stdout or "")
    upload_deviations: list[dict[str, Any]] = []
    for item in upload_items:
        assertions.append({
            "name": str(item.get("name") or ""),
            "outcome": str(item.get("outcome") or ""),
            "observed": str(item.get("observed") or ""),
            "rule_id": str(item.get("rule_id") or ""),
            "case_id": str(item.get("case_id") or ""),
            "source": "upload_evidence",
        })
        deviation = item.get("deviation")
        if isinstance(deviation, dict):
            upload_deviations.append({
                "name": str(item.get("name") or ""),
                "case_id": str(item.get("case_id") or ""),
                **deviation,
            })

    by_outcome: dict[str, int] = {}
    for item in assertions:
        by_outcome[item["outcome"]] = by_outcome.get(item["outcome"], 0) + 1

    return {
        "assertions": assertions,
        "upload_deviations": upload_deviations,
        "observations": observations,
        "observation_count": len(observations),
        "plan_skips": plan_skips,
        "plan_skip_count": len(plan_skips),
        "assertion_count": len(assertions),
        "by_outcome": by_outcome,
        "summary": summary if isinstance(summary, dict) else None,
        "coverage": coverage if isinstance(coverage, dict) else None,
        "environment_blocked": environment_blocked,
    }


def _empty_metric_bucket() -> dict[str, int]:
    return {key: 0 for key in ("total",) + _METRIC_OUTCOMES}


def _dedup_key(item: dict[str, Any]) -> tuple[str, str, str, str]:
    """同一条计划级检查对多个 owner 会**重复输出**（name/outcome/observed 完全相同）。

    R5 要求 "一个检查带 N 个 owners → 各 owner 各记一次，但 total 按检查计一次"，
    所以 run 级计数必须先按此键去重。键里带 ``script_identifier``：不同资产/脚本
    完全可能有同名检查（如多个表单都有"營業狀態=開啟"），跨脚本合并会把分母压小。
    用 ``observed`` 参与键：同名的两条**不同**检查观测值必然不同（实测 TR-697 的
    ``required_error:店鋪名稱(EN)`` 有"填值清空"与"提交层"两次观测，必须各算一条）。
    """
    return (
        str(item.get("script_identifier") or ""),
        str(item.get("name") or ""),
        str(item.get("outcome") or ""),
        str(item.get("observed") or ""),
    )


def _is_config_driven(item: dict[str, Any]) -> tuple[bool, bool]:
    """(是否配置驱动, 是否为推断)。显式 ``source`` 优先；旧脚本按检查名前缀推断。"""
    if str(item.get("source") or "") == CONFIG_SOURCE:
        return True, False
    name = str(item.get("name") or "")
    if name.startswith(CONFIG_CHECK_PREFIXES):
        return True, True
    return False, False


def build_metric_model(
    assertions: Iterable[dict[str, Any]],
    *,
    summary: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """R5 指标模型：**两类 × 5 指标**，各自算通过率，不得合并。

    - ``case_attributed``：检查带 ``case_id``（或经 owners 展开后的任一 owner）
      —— 这是**用例结论的唯一依据**；
    - ``config_only``：无归属且来自定位器配置（``source=locator_config``，或旧脚本按
      检查名前缀推断）—— 只反映"配置声明与页面是否一致"，不改变任何用例状态；
    - ``unattributed``：既不归属用例、又不是配置驱动 —— **只告警**，不计入任何通过率
      （否则"没人认领的检查"会悄悄拉低或抬高通过率）。

    去重口径：同一检查的多 owner 副本合并为一条（见 ``_dedup_key``）。
    """
    groups: dict[tuple[str, ...], dict[str, Any]] = {}
    items = list(assertions)
    for item in items:
        key = _dedup_key(item)
        bucket = groups.setdefault(key, {"owners": set(), "sources": set(), "outcome": ""})
        bucket["outcome"] = str(item.get("outcome") or "")
        if item.get("case_id"):
            bucket["owners"].add(str(item["case_id"]))
        if item.get("source"):
            bucket["sources"].add(str(item["source"]))

    classified: dict[str, dict[str, Any]] = {
        "case_attributed": _empty_metric_bucket(),
        "config_only": _empty_metric_bucket(),
        "unattributed": _empty_metric_bucket(),
    }
    owner_bindings = 0
    inferred_count = 0
    for (_script, name, _outcome, _observed), bucket in groups.items():
        outcome = bucket["outcome"]
        item = {"name": name, "outcome": outcome, "source": next(iter(bucket["sources"]), "")}
        if bucket["owners"]:
            category = "case_attributed"
            owner_bindings += len(bucket["owners"])
        else:
            is_config, inferred = _is_config_driven(item)
            category = "config_only" if is_config else "unattributed"
            if is_config and inferred:
                inferred_count += 1
        row = classified[category]
        row["total"] += 1
        if outcome in _METRIC_OUTCOMES:
            row[outcome] += 1

    def _rate(row: dict[str, int]) -> float | None:
        # 分母为 0 时给 None 而不是 0.0：0% 会被读成"全没过"，而事实是"没有这类检查"。
        return round(row["pass"] / row["total"], 4) if row["total"] else None

    attributed = classified["case_attributed"]
    config_only = classified["config_only"]
    unattributed = classified["unattributed"]

    # 与脚本自报的 summary 交叉校验：两者口径不同（summary 的 plan_checks 不含提交层
    # 后置复查），不一致本身不是错误，但**必须有数可查**，否则指标漂移无人发现。
    summary = summary or {}
    cross_check = {
        "summary_plan_checks": summary.get("plan_checks"),
        "summary_attributed_checks": summary.get("attributed_checks"),
        "summary_config_only_checks": summary.get("config_only_checks"),
        "assertion_rows": len(items),
        "deduplicated_checks": len(groups),
        "matches_summary_plan_checks": (
            summary.get("plan_checks") == len(groups)
            if isinstance(summary.get("plan_checks"), int) else None
        ),
    }

    return {
        "case_attributed": {**attributed, "pass_rate": _rate(attributed)},
        "config_only": {**config_only, "pass_rate": _rate(config_only)},
        "unattributed": {**unattributed, "pass_rate": None},
        "unattributed_checks": unattributed["total"],
        "owner_bindings": owner_bindings,
        "source_inferred": inferred_count,
        "cross_check": cross_check,
        "notes": [
            "两类通过率分别计算、不得合并：用例结论只由 case_attributed 决定；"
            "配置检查（defaults/visibility/list）不改变任何用例状态",
        ],
    }


def build_assertion_ledger(jobs: Iterable[Any]) -> dict[str, Any]:
    """把一批作业（含 stdout）汇总成一份**可审计**的账目。

    ``jobs`` 只需提供 ``script_identifier`` / ``status`` / ``stdout`` 三个属性，
    因此本函数可被单测直接调用（不依赖 DB/ORM）。
    """
    job_rows: list[dict[str, Any]] = []
    all_assertions: list[dict[str, Any]] = []
    all_observations: list[dict[str, Any]] = []
    all_plan_skips: list[dict[str, Any]] = []
    totals: dict[str, int] = {}
    warnings: list[str] = []
    notes: list[str] = []
    summary_totals: dict[str, int] = {}
    summary_seen = False

    for job in jobs:
        ledger = parse_job_ledger(getattr(job, "stdout", None))
        identifier = str(getattr(job, "script_identifier", "") or "")
        status = str(getattr(job, "status", "") or "")
        job_rows.append({
            "script_identifier": identifier,
            "status": status,
            "assertion_count": ledger["assertion_count"],
            "observation_count": ledger["observation_count"],
            "plan_skip_count": ledger["plan_skip_count"],
            "by_outcome": ledger["by_outcome"],
            "summary": ledger["summary"],
            "coverage": ledger["coverage"],
            "environment_blocked": ledger["environment_blocked"],
        })
        for item in ledger["assertions"]:
            row = dict(item)
            row["script_identifier"] = identifier
            all_assertions.append(row)
        for record in ledger["observations"]:
            row = dict(record)
            row["script_identifier"] = identifier
            all_observations.append(row)
        for record in ledger["plan_skips"]:
            row = dict(record)
            row["script_identifier"] = identifier
            all_plan_skips.append(row)
        for outcome, count in ledger["by_outcome"].items():
            totals[outcome] = totals.get(outcome, 0) + count

        # 计划期跳过：断言**根本没进入执行**，运行期不会有 observation —— 必须显式提示，
        # 并带出近似候选（字段名差一点是最常见的根因）。
        if ledger["plan_skips"]:
            hints = []
            for record in ledger["plan_skips"][:3]:
                field = str(record.get("field") or "")
                suggestions = [str(s) for s in (record.get("suggestions") or [])]
                hints.append(f"{field}→候选[{'/'.join(suggestions)}]" if suggestions else field)
            warnings.append(
                f"{identifier}: {len(ledger['plan_skips'])} 条断言未进入执行"
                f"（原因多为字段无定位器）：{'；'.join(hints)}"
            )

        # 未归属检查：计划级检查执行了但没挂到任何用例 → 必须显式提示。
        # 例外：**配置驱动**的计划级检查（来自定位器配置的默认值/条件显示声明）本来就没有
        # 对应用例断言 → 单独归到 notes，不算证据黑洞（否则每次运行都报一条假告警）。
        #
        # 判据以**执行台账的实际分类**为准，而不是 summary 的三数相减：编译器并没有给
        # 所有配置类检查标 ``source``（实测 TR-697 的 2 条 ``visibility:*`` 就没标），
        # 用 summary 相减会算出"2 条未归属"的假告警，而台账里这两条其实是配置驱动。
        summary = ledger["summary"] or {}
        if summary:
            summary_seen = True
            for _key in ("plan_checks", "attributed_checks", "config_only_checks"):
                _value = summary.get(_key)
                if isinstance(_value, int):
                    summary_totals[_key] = summary_totals.get(_key, 0) + _value
        plan_checks = summary.get("plan_checks")
        attributed = summary.get("attributed_checks")
        config_only = summary.get("config_only_checks") or 0
        job_metrics = build_metric_model(ledger["assertions"])
        if job_metrics["unattributed_checks"] > 0:
            warnings.append(
                f"{identifier}: {job_metrics['unattributed_checks']} 条检查既未归属任何用例、"
                "也不属配置驱动——执行了却拿不到用例级证据，请检查 owners/case_id"
            )
        elif isinstance(plan_checks, int) and isinstance(attributed, int):
            expected_missing = plan_checks - attributed - int(config_only)
            if expected_missing > 0:
                notes.append(
                    f"{identifier}: 脚本自报 {expected_missing} 条计划级检查未归属"
                    f"（plan_checks={plan_checks} / attributed_checks={attributed} / "
                    f"config_only={config_only}），但台账里未发现未归属检查——"
                    f"其中 {job_metrics['config_only']['total']} 条已按配置驱动归类"
                    "（编译器对这类检查未标 source，按检查名推断）"
                )
        # 脚本编译期声明的断言数 vs 本次真正归集到的条数：差额说明"有断言但没证据"
        # （多为字段无定位器 → 计划期就没进执行）。必须让它可见，否则报告会把
        # "压根没验证"与"已验证通过"混为一谈——2026-10-12 TR-267 实测 4 条资产如此。
        coverage = ledger["coverage"] or {}
        declared = coverage.get("assertions")
        reported = ledger["assertion_count"]
        if isinstance(declared, int) and declared > reported:
            warnings.append(
                f"{identifier}: 脚本声明 {declared} 条断言，本次只归集到 {reported} 条"
                f"（差 {declared - reported} 条未取得任何执行证据，多为字段无定位器）"
                "—— 不要把该作业读作『已完成验证』"
            )
        # 契约偏差：客户端行为与**已确认口径**不一致（如"≤10M 应接受"却报超限）——既不是
        # 通过、也不是普通未验证，必须单独浮出，否则会沉在 unverified 里被当成"没测到"。
        for dev in ledger.get("upload_deviations") or []:
            warnings.append(
                f"{identifier}: 上传断言契约偏差[{dev.get('code')}] {dev.get('name')}"
                f"（{dev.get('message') or ''}）—— 需人工确认是否与文档口径一致"
            )
        if ledger["environment_blocked"]:
            warnings.append(f"{identifier}: 出现环境阻断标记（ENVIRONMENT_BLOCKED），结论应按环境性失败处理")
        if ledger["by_outcome"].get("blocked"):
            warnings.append(f"{identifier}: {ledger['by_outcome']['blocked']} 条断言被阻塞（定位器/页面结构问题）")

    # 按用例 / 规则号汇总
    per_case: dict[str, dict[str, Any]] = {}
    for item in all_assertions:
        key = item["case_id"] or "(未归属)"
        row = per_case.setdefault(key, {
            "case_id": item["case_id"], "rule_id": item["rule_id"],
            "total": 0, "pass": 0, "unverified": 0, "blocked": 0,
        })
        row["total"] += 1
        if item["outcome"] in row:
            row[item["outcome"]] += 1

    # R5 指标模型（两类 × 5 指标）：在去重后的**检查**口径上分类与计数。
    # 注意与上面 per_case 的区别：per_case 是"逐条断言行"视角（同一检查的多 owner
    # 副本会各占一行），metrics 是"检查"视角（同一检查只算一次）。
    metrics = build_metric_model(
        all_assertions,
        summary=summary_totals if summary_seen else {},
    )
    unattributed_rows = sum(1 for item in all_assertions if not item["case_id"])
    missing_case_driven = metrics["unattributed_checks"]
    config_only_checks = metrics["config_only"]["total"]
    if missing_case_driven:
        warnings.append(
            f"共 {missing_case_driven} 条检查未归属任何用例、且不属配置驱动——"
            "这部分证据不会体现在用例级结论里，也不计入任何通过率（需补 owners/case_id）"
        )
    # 配置驱动检查数：台账分类结果为准；脚本自报的 summary 值作兜底（两者口径不同，
    # 例如脚本把某检查算进 config_only、却没有输出对应断言行时，仍应让数字可见）。
    statement_config_only = max(
        config_only_checks, int(summary_totals.get("config_only_checks", 0) or 0)
    )
    if statement_config_only:
        notes.append(
            f"另有 {statement_config_only} 条检查来自定位器配置（无对应用例），已归类为配置驱动"
            + (
                f"，其中 {metrics['source_inferred']} 条按检查名推断来源"
                "（旧脚本未标 source）"
                if metrics.get("source_inferred") else ""
            )
        )
    return {
        "jobs": job_rows,
        "assertions": all_assertions,
        "observations": all_observations,
        "plan_skips": all_plan_skips,
        "per_case": sorted(per_case.values(), key=lambda r: r["case_id"]),
        "metrics": metrics,
        "totals": {
            "jobs": len(job_rows),
            "assertions": len(all_assertions),
            "observations": len(all_observations),
            "plan_skips": len(all_plan_skips),
            # 检查口径（去重后，与 metrics 一致）；断言行口径见 unattributed_rows
            "unattributed": metrics["unattributed_checks"],
            "unattributed_rows": unattributed_rows,
            "by_outcome": totals,
        },
        "warnings": warnings,
        "notes": notes,
    }
