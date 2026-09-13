# -*- coding: utf-8 -*-
"""兼容适配层：把 ai-test-agent-system 旧格式用例转换为平台格式。

旧格式（历史生成脚本输出，不改动原脚本）::

    {
      "id": "TC-001",
      "title": "会员列表查询",
      "steps": [
        {"seq": 1, "action": "进入会员列表", "target": "会员管理", "data": "无"},
        {"seq": 2, "action": "输入手机号", "target": "搜索框", "data": "13800000000"},
      ],
      "expected_results": ["1. 列表展示对应会员", "2. 提示无匹配结果"]
    }

平台格式::

    {
      "id": "TC-001",
      "title": "会员列表查询",
      "steps": [{"step": "...", "result": "...", "data": "..."}],
      "expected_results": "1. ...\\n2. ..."
    }

转换规则：
- ``action``（+ ``target`` / ``操作对象``）→ ``step``；
- ``expected_results``（按序）+ 每步内嵌 ``expected``/``result`` → 每步 ``result``；
- ``seq`` 保留为步骤顺序（当前契约以数组顺序表达，故仅用于对齐 expected_results）；
- 旧格式没有 feature_id 等追踪字段时标记为 legacy，允许读取与执行。
"""

from __future__ import annotations

from typing import Any, Optional

from app.test_platform.requirements.coverage import is_legacy_case
from app.test_platform.testcase_contract import normalize_case_fields


_STEP_TEXT_KEYS = ("action", "操作描述", "操作", "step", "step_desc")
_STEP_TARGET_KEYS = ("target", "操作对象", "对象")
_STEP_DATA_KEYS = ("data", "test_data", "数据", "输入")
_STEP_RESULT_KEYS = ("result", "expected", "expected_result", "预期结果", "预期", "期望")


def _get_first(d: dict[str, Any], keys: tuple[str, ...]) -> Optional[Any]:
    for key in keys:
        value = d.get(key)
        if value is not None and str(value).strip():
            return value
    return None


def _normalize_expected_results(expected: Any) -> list[str]:
    """把 expected_results（列表/字符串/JSON 字符串）拆成条目列表。"""
    if expected is None:
        return []
    if isinstance(expected, str):
        text = expected.strip()
        if not text:
            return []
        # 已按行分隔
        lines = [line.strip() for line in text.split("\n") if line.strip()]
        if len(lines) > 1:
            return [line.lstrip("0123456789.、)） ").strip() or line for line in lines]
        return [text]
    if isinstance(expected, (list, tuple)):
        return [str(item).strip() for item in expected if str(item).strip()]
    return [str(expected).strip()]


def is_legacy_step(step: Any) -> bool:
    """旧格式步骤判定：存在 action/target，但没有 step。"""
    if not isinstance(step, dict):
        return False
    has_legacy = any(step.get(k) for k in ("action", "操作描述", "target", "操作对象"))
    has_platform = bool(step.get("step"))
    return has_legacy and not has_platform


def convert_legacy_step(
    step: dict[str, Any],
    fallback_result: str = "",
) -> dict[str, str]:
    """单步旧格式 → 平台格式 ``{"step", "result", "data"?}``。"""
    action = _get_first(step, _STEP_TEXT_KEYS)
    target = _get_first(step, _STEP_TARGET_KEYS)
    data = _get_first(step, _STEP_DATA_KEYS)
    result = _get_first(step, _STEP_RESULT_KEYS)

    step_text = str(action or "").strip()
    if target:
        step_text = f"{step_text} [{str(target).strip()}]" if step_text else str(target).strip()

    converted: dict[str, str] = {
        "step": step_text,
        "result": str(result).strip() if result else str(fallback_result or "").strip(),
    }
    if data:
        converted["data"] = str(data).strip()
    return converted


def convert_legacy_case(case: dict[str, Any]) -> dict[str, Any]:
    """把一条旧格式用例转换为平台格式用例（不修改入参）。

    - 已经是平台格式的部分（step/result）原样保留；
    - ``expected_results`` 按序号对齐到各步 ``result``；
    - 追踪字段缺失时保留为空（由调用方决定是否补 feature_id）。
    """
    if not isinstance(case, dict):
        raise TypeError(f"用例必须是 dict，收到 {type(case).__name__}")

    converted = normalize_case_fields(dict(case))
    raw_steps = converted.get("steps")
    if raw_steps is None:
        raw_steps = converted.get("test_case_steps")

    results = _normalize_expected_results(converted.get("expected_results"))

    steps: list[dict[str, Any]] = []
    if isinstance(raw_steps, list):
        for idx, step in enumerate(raw_steps):
            if is_legacy_step(step):
                fallback = results[idx] if idx < len(results) else ""
                steps.append(convert_legacy_step(step, fallback))
            elif isinstance(step, dict):
                item = dict(step)
                if not item.get("step"):
                    item["step"] = str(
                        _get_first(item, _STEP_TEXT_KEYS) or ""
                    ).strip()
                if not item.get("result") and idx < len(results):
                    item["result"] = results[idx]
                steps.append(item)
            else:
                steps.append({"step": str(step), "result": results[idx] if idx < len(results) else ""})
    elif isinstance(raw_steps, str) and raw_steps.strip():
        steps.append({"step": raw_steps.strip(), "result": results[0] if results else ""})

    if steps:
        converted["steps"] = steps
        converted.pop("test_case_steps", None)
    elif results:
        # 无步骤但有用例级预期结果：保留用例级字段，步骤留空由契约校验提示
        converted["expected_results"] = "\n".join(
            f"{i}. {r}" if not r[:1].isdigit() else r for i, r in enumerate(results, start=1)
        )
    elif converted.get("expected_results") is not None and not isinstance(
        converted.get("expected_results"), str
    ):
        converted["expected_results"] = "\n".join(results)

    return converted


def convert_legacy_cases(cases: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """批量转换旧格式用例。"""
    return [convert_legacy_case(case) for case in cases or []]


def attach_traceability(
    case: dict[str, Any],
    *,
    feature_id: str = "",
    requirement_id: str = "",
    acceptance_id: str = "",
    source_location: str = "",
) -> dict[str, Any]:
    """为新生成用例补齐追踪字段（阶段二要求：新用例必须带 feature_id）。"""
    result = dict(case)
    if feature_id:
        result["feature_id"] = feature_id
    if requirement_id:
        result["requirement_id"] = requirement_id
    if acceptance_id:
        result["acceptance_id"] = acceptance_id
    if source_location:
        result["source_location"] = source_location
    return result


def classify_cases(cases: list[dict[str, Any]]) -> dict[str, list[str]]:
    """按追踪情况给用例分类，便于兼容性报告。"""
    from app.test_platform.requirements.coverage import case_feature_id

    bucket: dict[str, list[str]] = {"legacy": [], "traced": [], "untraced": []}
    for case in cases or []:
        case_id = str(case.get("id") or case.get("用例编号") or "(无编号)")
        if is_legacy_case(case):
            bucket["legacy"].append(case_id)
        elif case_feature_id(case):
            bucket["traced"].append(case_id)
        else:
            bucket["untraced"].append(case_id)
    return bucket
