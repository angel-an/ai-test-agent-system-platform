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


def validate_staged_case(case: dict[str, Any]) -> tuple[list[str], list[dict[str, str]] | None]:
    """暂存/导出与 DB 创建共用的统一门禁（rev59 闭环）。

    对一条用例同时执行：
    - 步骤归一化门禁（normalize_steps：每步 result 必填、步骤文本去引号/结果模糊双查），
      与 _create_test_case_impl（HTTP 创建轨）完全一致；
    - 用例级 expected_results 模糊扫描（完整词表）。

    Returns:
        (problems, normalized_steps)：problems 非空即拒绝；normalized_steps 为合规时的
        [{"step","result"}]（可能为 None，当未提供步骤或不合规）。
    """
    problems: list[str] = []
    steps = case.get("steps")
    if steps is None:
        steps = case.get("test_case_steps")
    normalized, step_problems = normalize_steps(steps)
    if step_problems:
        problems.extend(step_problems)
    hit = check_expected_results_fuzzy(case.get("expected_results"))
    if hit:
        problems.append(f"用例级预期结果含模糊断言词「{hit}」，请改为具体可核验描述")
    return problems, normalized


def validate_steps_payload(case: dict[str, Any]) -> tuple[list[dict[str, str]] | None, list[str]]:
    """对单条待落库用例（DB 风格，键 test_case_steps）执行步骤门禁。"""
    steps = case.get("test_case_steps")
    if steps is None:
        # 兼容 Excel 风格步骤键
        steps = case.get("steps")
    return normalize_steps(steps)


# ---------------------------------------------------------------------------
# P1-2 扩展：重复场景检查、覆盖缺口检查、质量报告汇总
# ---------------------------------------------------------------------------

import difflib
from collections import Counter


def check_duplicate_scenarios(cases: list[dict[str, Any]], threshold: float = 0.85) -> list[dict[str, Any]]:
    """检查重复场景（基于标题相似度）。

    Args:
        cases: 用例列表
        threshold: 相似度阈值（默认 0.85，即 85% 相似视为重复）

    Returns:
        重复用例对列表，每项为 {"index_a", "id_a", "title_a", "index_b", "id_b", "title_b", "similarity"}
    """
    duplicates: list[dict[str, Any]] = []
    n = len(cases)
    for i in range(n):
        title_i = str(cases[i].get("title", "") or cases[i].get("name", "")).strip()
        if not title_i:
            continue
        for j in range(i + 1, n):
            title_j = str(cases[j].get("title", "") or cases[j].get("name", "")).strip()
            if not title_j:
                continue
            sim = difflib.SequenceMatcher(None, title_i, title_j).ratio()
            if sim >= threshold:
                duplicates.append({
                    "index_a": i + 1,
                    "id_a": cases[i].get("id", "unknown"),
                    "title_a": title_i,
                    "index_b": j + 1,
                    "id_b": cases[j].get("id", "unknown"),
                    "title_b": title_j,
                    "similarity": round(sim, 3),
                })
    return duplicates


def check_coverage_gaps(cases: list[dict[str, Any]]) -> list[str]:
    """检查覆盖缺口（正向/反向/边界比例、DB/并发/安全维度）。

    返回问题清单（空列表表示覆盖完整）。
    """
    problems: list[str] = []
    if not cases:
        problems.append("用例列表为空")
        return problems

    total = len(cases)

    # 关键词统计
    keywords = Counter()
    for c in cases:
        kw = c.get("keyword")
        if kw:
            keywords[str(kw)] += 1

    zhengxiang = keywords.get("正向", 0)
    fanxiang = keywords.get("反向", 0)
    bianjie = keywords.get("边界", 0)

    # 比例检查（与 export_all_testcases 中的门禁对齐）
    if zhengxiang / total > 0.45:
        problems.append(f"正向用例占比 {zhengxiang/total*100:.1f}% > 45%，需削减正向或补充反向/边界")
    if fanxiang / total < 0.30:
        problems.append(f"反向用例占比 {fanxiang/total*100:.1f}% < 30%，需补充反向用例")
    if bianjie / total < 0.10:
        problems.append(f"边界用例占比 {bianjie/total*100:.1f}% < 10%，需补充边界用例")

    # 维度检查（有即可）
    remarks_text = " ".join(str(c.get("remarks", "")) for c in cases)
    if "DB" not in remarks_text and "数据库" not in remarks_text:
        problems.append("缺少 DB 异常类用例标记")
    if "并发" not in remarks_text and "幂等" not in remarks_text:
        problems.append("缺少并发安全类用例标记")
    if "安全" not in remarks_text and "SQL" not in remarks_text and "XSS" not in remarks_text:
        problems.append("缺少字段级安全类用例标记")

    return problems


class QualityReport:
    """质量门禁汇总报告。"""

    def __init__(self):
        self.structural_errors: list[str] = []
        self.fuzzy_cases: list[tuple[str, list[str]]] = []
        self.duplicates: list[dict[str, Any]] = []
        self.coverage_gaps: list[str] = []
        self.total_cases: int = 0

    @property
    def passed(self) -> bool:
        return (
            len(self.structural_errors) == 0
            and len(self.fuzzy_cases) == 0
            and len(self.duplicates) == 0
            and len(self.coverage_gaps) == 0
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "passed": self.passed,
            "total_cases": self.total_cases,
            "structural_errors": self.structural_errors,
            "fuzzy_cases_count": len(self.fuzzy_cases),
            "fuzzy_cases": [{"id": fid, "reasons": reasons} for fid, reasons in self.fuzzy_cases],
            "duplicates_count": len(self.duplicates),
            "duplicates": self.duplicates,
            "coverage_gaps": self.coverage_gaps,
        }


def run_quality_gates(cases: list[dict[str, Any]]) -> QualityReport:
    """对一批用例执行完整质量门禁检查。

    包括：
    1. 结构性检查（每步 result 必填）
    2. 模糊词扫描
    3. 重复场景检查
    4. 覆盖缺口检查
    """
    report = QualityReport()
    report.total_cases = len(cases)

    if not cases:
        report.structural_errors.append("用例列表为空")
        return report

    # 1. 结构性检查 + 模糊词扫描
    for c in cases:
        problems, _ = validate_staged_case(c)
        fuzzy = [p for p in problems if "模糊" in p]
        structural = [p for p in problems if "模糊" not in p]
        if fuzzy:
            report.fuzzy_cases.append((str(c.get("id", "unknown")), fuzzy))
        if structural:
            report.structural_errors.append(f"{c.get('id', 'unknown')}: {'; '.join(structural)}")

    # 2. 重复场景检查
    report.duplicates = check_duplicate_scenarios(cases)

    # 3. 覆盖缺口检查
    report.coverage_gaps = check_coverage_gaps(cases)

    return report
