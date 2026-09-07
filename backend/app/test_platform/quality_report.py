# -*- coding: utf-8 -*-
"""P1-4: 输出生成质量报告 — 场景数量、覆盖比例、缺失项、可执行率。

目标：让生成质量可以评估，能一眼看出生成质量，能定位缺口。
"""

from __future__ import annotations

from collections import Counter
from typing import Any, Optional

from app.test_platform.testcase_contract import TestCaseKeyword


# ---------------------------------------------------------------------------
# 质量指标计算
# ---------------------------------------------------------------------------

def compute_quality_metrics(cases: list[dict[str, Any]]) -> dict[str, Any]:
    """计算生成质量指标。

    Returns:
        {
            "total_cases": 总场景数,
            "keyword_distribution": 关键词分布,
            "keyword_ratios": 关键词比例,
            "result_completeness_rate": result 完整率,
            "fuzzy_word_rate": 模糊词占比,
            "coverage_dimensions": 覆盖维度（DB/并发/安全）,
            "executable_rate": 可执行率,
            "module_distribution": 模块分布,
        }
    """
    if not cases:
        return {
            "total_cases": 0,
            "keyword_distribution": {},
            "keyword_ratios": {},
            "result_completeness_rate": 0.0,
            "fuzzy_word_rate": 0.0,
            "coverage_dimensions": {},
            "executable_rate": 0.0,
            "module_distribution": {},
        }

    total = len(cases)

    # 关键词分布
    keywords = Counter()
    for c in cases:
        kw = c.get("keyword")
        if kw:
            keywords[str(kw)] += 1

    keyword_distribution = dict(keywords)
    keyword_ratios = {
        "正向": keywords.get("正向", 0) / total,
        "反向": keywords.get("反向", 0) / total,
        "边界": keywords.get("边界", 0) / total,
    }

    # result 完整率
    from app.agents.tools.testcase.quality_gates import normalize_steps

    complete_count = 0
    fuzzy_count = 0
    for c in cases:
        steps = c.get("steps") or c.get("test_case_steps") or []
        if steps:
            normalized, problems = normalize_steps(steps)
            if normalized and not problems:
                complete_count += 1
            # 模糊词检查
            from app.agents.tools.testcase.quality_gates import find_fuzzy
            for p in problems:
                if "模糊" in p:
                    fuzzy_count += 1
                    break

    result_completeness_rate = complete_count / total if total > 0 else 0.0
    fuzzy_word_rate = fuzzy_count / total if total > 0 else 0.0

    # 覆盖维度
    remarks_text = " ".join(str(c.get("remarks", "")) for c in cases)
    coverage_dimensions = {
        "db": "DB" in remarks_text or "数据库" in remarks_text,
        "concurrent": "并发" in remarks_text or "幂等" in remarks_text,
        "security": "安全" in remarks_text or "SQL" in remarks_text or "XSS" in remarks_text,
    }

    # 可执行率（标记了 executable_flag 的比例）
    executable_count = sum(1 for c in cases if c.get("executable_flag"))
    executable_rate = executable_count / total if total > 0 else 0.0

    # 模块分布
    modules = Counter(str(c.get("module", "")) for c in cases if c.get("module"))
    module_distribution = dict(modules)

    return {
        "total_cases": total,
        "keyword_distribution": keyword_distribution,
        "keyword_ratios": keyword_ratios,
        "result_completeness_rate": round(result_completeness_rate, 4),
        "fuzzy_word_rate": round(fuzzy_word_rate, 4),
        "coverage_dimensions": coverage_dimensions,
        "executable_rate": round(executable_rate, 4),
        "module_distribution": module_distribution,
    }


# ---------------------------------------------------------------------------
# 待确认/待复核项标记
# ---------------------------------------------------------------------------

def mark_pending_items(cases: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """标记待确认与待复核项。

    标记规则：
    - 待复核：含模糊词的用例
    - 待确认：未标记 executable_flag 的用例
    - 缺陷：步骤结构有问题的用例

    Returns:
        每条用例附加 status 字段后的列表
    """
    from app.agents.tools.testcase.quality_gates import validate_staged_case

    result = []
    for c in cases:
        case_copy = dict(c)
        problems, _ = validate_staged_case(c)

        if any("模糊" in p for p in problems):
            case_copy["_status"] = "待复核"
            case_copy["_status_reason"] = "含模糊断言词"
        elif any("缺少" in p or "必填" in p for p in problems):
            case_copy["_status"] = "缺陷"
            case_copy["_status_reason"] = "结构不完整"
        elif not c.get("executable_flag"):
            case_copy["_status"] = "待确认"
            case_copy["_status_reason"] = "未标记可执行"
        else:
            case_copy["_status"] = "通过"
            case_copy["_status_reason"] = ""

        result.append(case_copy)

    return result


# ---------------------------------------------------------------------------
# 质量报告生成
# ---------------------------------------------------------------------------

class GenerationQualityReport:
    """生成质量报告。"""

    def __init__(
        self,
        project: str,
        version: str,
        metrics: dict[str, Any],
        pending_items: list[dict[str, Any]],
        gaps: list[str],
    ):
        self.project = project
        self.version = version
        self.metrics = metrics
        self.pending_items = pending_items
        self.gaps = gaps

    def to_dict(self) -> dict[str, Any]:
        return {
            "project": self.project,
            "version": self.version,
            "metrics": self.metrics,
            "pending_summary": {
                "total": len(self.pending_items),
                "passed": sum(1 for i in self.pending_items if i.get("_status") == "通过"),
                "pending_review": sum(1 for i in self.pending_items if i.get("_status") == "待复核"),
                "pending_confirm": sum(1 for i in self.pending_items if i.get("_status") == "待确认"),
                "defect": sum(1 for i in self.pending_items if i.get("_status") == "缺陷"),
            },
            "gaps": self.gaps,
        }

    def to_markdown(self) -> str:
        """渲染为 Markdown 格式"""
        lines: list[str] = []
        lines.append(f"# 生成质量报告 — {self.project}")
        lines.append(f"**版本**: {self.version}")
        lines.append("")

        # 指标摘要
        m = self.metrics
        lines.append("## 质量指标")
        lines.append("")
        lines.append(f"- **总场景数**: {m['total_cases']}")
        lines.append(f"- **Result 完整率**: {m['result_completeness_rate']*100:.1f}%")
        lines.append(f"- **模糊词占比**: {m['fuzzy_word_rate']*100:.1f}%")
        lines.append(f"- **可执行率**: {m['executable_rate']*100:.1f}%")
        lines.append("")

        # 关键词分布
        lines.append("## 关键词分布")
        lines.append("")
        for kw, ratio in m.get("keyword_ratios", {}).items():
            lines.append(f"- {kw}: {ratio*100:.1f}%")
        lines.append("")

        # 覆盖维度
        lines.append("## 覆盖维度")
        lines.append("")
        dims = m.get("coverage_dimensions", {})
        for dim, covered in dims.items():
            icon = "✅" if covered else "❌"
            lines.append(f"- {icon} {dim}")
        lines.append("")

        # 缺口
        if self.gaps:
            lines.append("## 覆盖缺口")
            lines.append("")
            for gap in self.gaps:
                lines.append(f"- ⚠️ {gap}")
            lines.append("")

        # 待处理项摘要
        summary = self.to_dict()["pending_summary"]
        lines.append("## 状态分布")
        lines.append("")
        lines.append(f"- ✅ 通过: {summary['passed']}")
        lines.append(f"- ⏳ 待复核: {summary['pending_review']}")
        lines.append(f"- ❓ 待确认: {summary['pending_confirm']}")
        lines.append(f"- ❌ 缺陷: {summary['defect']}")
        lines.append("")

        return "\n".join(lines)


def build_generation_quality_report(
    project: str,
    version: str,
    cases: list[dict[str, Any]],
) -> GenerationQualityReport:
    """构建生成质量报告。

    Args:
        project: 项目标识符
        version: 版本号
        cases: 用例列表
    """
    metrics = compute_quality_metrics(cases)
    pending_items = mark_pending_items(cases)

    # 计算缺口
    gaps: list[str] = []
    from app.agents.tools.testcase.quality_gates import check_coverage_gaps
    gaps = check_coverage_gaps(cases)

    # 模糊词占比超标
    if metrics["fuzzy_word_rate"] > 0.10:
        gaps.append(f"模糊词占比 {metrics['fuzzy_word_rate']*100:.1f}% > 10%，需重写")

    # result 完整率不足
    if metrics["result_completeness_rate"] < 1.0:
        gaps.append(f"Result 完整率 {metrics['result_completeness_rate']*100:.1f}% < 100%，需补齐")

    return GenerationQualityReport(
        project=project,
        version=version,
        metrics=metrics,
        pending_items=pending_items,
        gaps=gaps,
    )
