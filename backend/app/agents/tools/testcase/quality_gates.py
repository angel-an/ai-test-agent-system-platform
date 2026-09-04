# -*- coding: utf-8 -*-
"""
测试用例生成质量门禁（rev59）—— 纯函数，供 stage / batch_create 等工具复用。

目标（评审口径 rev59）：
1. 步骤级预期结果闭环：普通模板用例每步必须同时给出 step 与 result（预期结果），
   步骤文本中不得混写"预期/期望"段落；
2. 模糊断言词零容忍：result / expected_results 严格扫描（不去引号）；
   步骤文本在去除「」引号内容后同样扫描，避免把"点击「绑定成功」"误伤的同时
   拦截"点击保存成功"这类把结果写进步骤的模糊表述；
3. 所有判定确定性、可解释，拒绝原因可直接回给 Agent 重写（不改主流程，
   降级路径由调用方按环境变量/阈值决定）。
"""

from __future__ import annotations

import re
from typing import Any

# 模糊断言词：预期/结果 中零容忍；步骤文本去引号后同样零容忍
FUZZY_WORDS: tuple[str, ...] = (
    "验证成功", "功能正常", "页面正确", "查询正确", "保存成功", "提交成功",
    "删除成功", "更新成功", "创建成功", "绑定成功",
    "正确", "成功", "正常", "通过", "无误", "无异常", "符合预期",
)

_QUOTE_PATTERNS = [
    re.compile(r"「[^」]*」"),
    re.compile(r"“[^”]*”"),
    re.compile(r"‘[^’]*’"),
    re.compile(r"\"[^\"]*\""),
    re.compile(r"'[^']*'"),
]

# 步骤/结果字段的常见键名（兼容 Excel 风格与 DB 风格）
_STEP_KEYS = ("step", "action", "操作", "步骤", "step_desc")
_RESULT_KEYS = ("result", "expected", "expected_result", "预期", "预期结果", "期望", "期望结果")
_SPLIT_RESULT_RE = re.compile(
    r"(?:——|--|[-—])\s*(?:预期结果|预期|期望结果|期望|期望值)\s*[:：]\s*(.+)$"
)


def strip_quoted(text: str) -> str:
    """去除常见引号包裹内容（用于步骤文本扫描，避免误伤需求原文文案）。"""
    out = text or ""
    for pat in _QUOTE_PATTERNS:
        out = pat.sub("", out)
    return out


def find_fuzzy(text: str | None, strip_quotes: bool = False) -> str | None:
    """返回首个命中模糊词；无命中返回 None。"""
    t = str(text or "")
    if strip_quotes:
        t = strip_quoted(t)
    for w in FUZZY_WORDS:
        if w in t:
            return w
    return None


def _get_first(d: dict[str, Any], keys: tuple[str, ...]) -> Any:
    for k in keys:
        if k in d and d[k] is not None and str(d[k]).strip():
            return d[k]
    return None


def split_trailing_expectation(step_text: str) -> tuple[str, str | None]:
    """若步骤文本以 '—— 预期结果：xxx' 收尾，拆出 result（引导 Agent 正确落字段）。"""
    m = _SPLIT_RESULT_RE.search(str(step_text))
    if not m:
        return step_text, None
    return step_text[: m.start()].strip(), m.group(1).strip()


def normalize_steps(steps: Any) -> tuple[list[dict[str, str]] | None, list[str]]:
    """把工具入参的 steps 归一化为 [{"step","result"}]；不满足质量门禁返回问题清单。

    - steps 元素支持 dict（含 step/result 别名键）或 str（Excel 风格单文本步骤）；
    - 每步 result 必填（缺失时尝试从步骤文本尾部"预期结果："自动拆出，拆不出即拒绝）；
    - result 严格模糊扫描；步骤文本去引号后模糊扫描。
    """
    problems: list[str] = []
    if not isinstance(steps, list) or len(steps) == 0:
        return None, ["steps 不能为空（普通模板用例必须提供 test_case_steps）"]

    normalized: list[dict[str, str]] = []
    for idx, raw in enumerate(steps, start=1):
        if isinstance(raw, str):
            step_text = raw.strip()
            result_text = None
        elif isinstance(raw, dict):
            step_text = str(_get_first(raw, _STEP_KEYS) or "").strip()
            result_text = _get_first(raw, _RESULT_KEYS)
            result_text = None if result_text is None else str(result_text).strip()
        else:
            problems.append(f"第 {idx} 步格式非法（应为字符串或字典）")
            continue

        if not step_text:
            problems.append(f"第 {idx} 步缺少步骤文本")
            continue

        # 尝试从步骤文本尾部拆出预期结果（兼容"步骤 —— 预期结果：xxx"写法）
        if not result_text:
            step_text, result_text = split_trailing_expectation(step_text)
            if result_text == "":
                result_text = None
        if not step_text:
            problems.append(f"第 {idx} 步拆分后步骤文本为空")

        # 结果严格扫描（零容忍）
        if result_text:
            hit = find_fuzzy(result_text, strip_quotes=False)
            if hit:
                problems.append(f"第 {idx} 步预期结果含模糊断言词「{hit}」，请改为具体可核验描述")
        else:
            problems.append(f"第 {idx} 步缺少预期结果（result 必填；不要写在步骤文本里）")

        # 步骤文本去引号扫描
        hit = find_fuzzy(step_text, strip_quotes=True)
        if hit:
            problems.append(
                f"第 {idx} 步步骤文本含模糊断言词「{hit}」（结果请写入该步 result 并量化）"
            )

        normalized.append({"step": step_text, "result": result_text or ""})

    if problems:
        return None, problems
    return normalized, []


def check_expected_results_fuzzy(expected: Any) -> str | None:
    """用例级 expected_results（Excel 暂存风格）严格模糊扫描。"""
    return find_fuzzy(str(expected or ""), strip_quotes=False)


def validate_steps_payload(case: dict[str, Any]) -> tuple[list[dict[str, str]] | None, list[str]]:
    """对单条待落库用例（DB 风格，键 test_case_steps）执行步骤门禁。"""
    steps = case.get("test_case_steps")
    if steps is None:
        # 兼容 Excel 风格步骤键
        steps = case.get("steps")
    return normalize_steps(steps)
