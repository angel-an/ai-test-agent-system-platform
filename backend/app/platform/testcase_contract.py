# -*- coding: utf-8 -*-
"""P0-1: 统一测试用例生成契约 — 平台级 Schema 与校验。

目标：把测试用例生成字段固定下来，不同项目按同一字段输出，
生成结果可直接进入执行层（API / Web）。
"""

from __future__ import annotations

from enum import Enum
from typing import Any, Optional


# ---------------------------------------------------------------------------
# 枚举定义
# ---------------------------------------------------------------------------

class TestCaseType(str, Enum):
    """用例类型"""
    FUNCTIONAL = "functional"
    API = "api"
    UI = "ui"
    PERFORMANCE = "performance"
    SECURITY = "security"


class TestCasePriority(str, Enum):
    """优先级"""
    CRITICAL = "critical"
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


class TestCaseKeyword(str, Enum):
    """关键词：正向 / 反向 / 边界"""
    POSITIVE = "正向"
    NEGATIVE = "反向"
    BOUNDARY = "边界"


class TestCaseStatus(str, Enum):
    """用例状态"""
    NEW = "new"
    READY = "ready"
    REVIEW = "review"
    APPROVED = "approved"


class RiskLevel(str, Enum):
    """风险等级"""
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


class CaseKind(str, Enum):
    """用例种类"""
    SIT = "sit"
    UAT = "uat"
    SMOKE = "smoke"
    API = "api"


# ---------------------------------------------------------------------------
# 数据类（兼容 Python 3.9+，不用 dataclasses 减少依赖）
# ---------------------------------------------------------------------------

class TestCaseStep:
    """单步步骤：步骤描述 + 预期结果（成对出现）"""

    def __init__(
        self,
        step: str,
        result: str,
        data: Optional[str] = None,
    ):
        self.step = step.strip() if step else ""
        self.result = result.strip() if result else ""
        self.data = data.strip() if data else None

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {"step": self.step, "result": self.result}
        if self.data:
            d["data"] = self.data
        return d

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "TestCaseStep":
        return cls(
            step=d.get("step", ""),
            result=d.get("result", ""),
            data=d.get("data"),
        )


class TestCaseContract:
    """测试用例生成契约 — 平台统一字段。

    字段说明：
    - id: 用例编号（如 TC-001）
    - title: 用例标题
    - module: 所属模块
    - type: 用例类型（functional/api/ui/performance/security）
    - keyword: 关键词（正向/反向/边界）
    - priority: 优先级（critical/high/medium/low）
    - preconditions: 前置条件
    - steps: 测试步骤列表（每步含 step + result）
    - test_data: 测试数据
    - expected_results: 用例级预期结果（可选，优先使用 steps 中每步 result）
    - risk_level: 风险等级
    - data_dependencies: 数据依赖说明
    - executable_flag: 可执行性标记（True=可直接进入执行层）
    - remarks: 备注（用于标记 DB/并发/安全等覆盖维度）
    """

    def __init__(
        self,
        id: str,
        title: str,
        module: Optional[str] = None,
        type: TestCaseType = TestCaseType.FUNCTIONAL,
        keyword: Optional[TestCaseKeyword] = None,
        priority: TestCasePriority = TestCasePriority.MEDIUM,
        preconditions: Optional[str] = None,
        steps: Optional[list[TestCaseStep]] = None,
        test_data: Optional[dict[str, Any]] = None,
        expected_results: Optional[str] = None,
        risk_level: Optional[RiskLevel] = None,
        data_dependencies: Optional[list[str]] = None,
        executable_flag: bool = False,
        remarks: Optional[str] = None,
    ):
        self.id = id
        self.title = title
        self.module = module
        self.type = type
        self.keyword = keyword
        self.priority = priority
        self.preconditions = preconditions
        self.steps = steps or []
        self.test_data = test_data
        self.expected_results = expected_results
        self.risk_level = risk_level
        self.data_dependencies = data_dependencies or []
        self.executable_flag = executable_flag
        self.remarks = remarks

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "title": self.title,
            "module": self.module,
            "type": self.type.value,
            "keyword": self.keyword.value if self.keyword else None,
            "priority": self.priority.value,
            "preconditions": self.preconditions,
            "steps": [s.to_dict() for s in self.steps],
            "test_data": self.test_data,
            "expected_results": self.expected_results,
            "risk_level": self.risk_level.value if self.risk_level else None,
            "data_dependencies": self.data_dependencies,
            "executable_flag": self.executable_flag,
            "remarks": self.remarks,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "TestCaseContract":
        d = normalize_case_fields(d)
        steps_raw = d.get("steps") or d.get("test_case_steps") or []
        steps = []
        for s in steps_raw:
            if isinstance(s, dict):
                steps.append(TestCaseStep.from_dict(s))
            elif isinstance(s, str):
                # 兼容字符串步骤（尝试拆分）
                from app.agents.tools.testcase.quality_gates import split_trailing_expectation
                step_text, result_text = split_trailing_expectation(s)
                steps.append(TestCaseStep(step=step_text, result=result_text or ""))
        return cls(
            id=d.get("id", ""),
            title=d.get("title", ""),
            module=d.get("module"),
            type=TestCaseType(d.get("type", "functional")),
            keyword=TestCaseKeyword(d["keyword"]) if d.get("keyword") else None,
            priority=TestCasePriority(d.get("priority", "medium")),
            preconditions=d.get("preconditions"),
            steps=steps,
            test_data=d.get("test_data"),
            expected_results=d.get("expected_results"),
            risk_level=RiskLevel(d["risk_level"]) if d.get("risk_level") else None,
            data_dependencies=d.get("data_dependencies") or [],
            executable_flag=bool(d.get("executable_flag", False)),
            remarks=d.get("remarks"),
        )


class GenerationResult:
    """生成结果：版本 + 用例列表 + 质量摘要"""

    def __init__(
        self,
        version: str,
        cases: list[TestCaseContract],
        quality_summary: Optional[dict[str, Any]] = None,
    ):
        self.version = version
        self.cases = cases
        self.quality_summary = quality_summary or {}

    def to_dict(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "cases": [c.to_dict() for c in self.cases],
            "quality_summary": self.quality_summary,
        }


# ---------------------------------------------------------------------------
# 契约校验
# ---------------------------------------------------------------------------

REQUIRED_FIELDS = ("id", "title")
STEP_REQUIRED_FIELDS = ("step", "result")

# 字段映射：真实项目常见命名 -> 平台契约命名
FIELD_ALIASES: dict[str, str] = {
    "identifier": "id",
    "name": "title",
    "test_case_steps": "steps",
    "test_case_step": "steps",
}


def normalize_case_fields(case: dict[str, Any]) -> dict[str, Any]:
    """将真实项目字段映射为平台契约字段。

    支持常见别名：identifier->id, name->title, test_case_steps->steps 等。
    不修改原始字典，返回新字典。
    """
    result = dict(case)
    for alias, canonical in FIELD_ALIASES.items():
        if alias in result and canonical not in result:
            result[canonical] = result.pop(alias)
    return result


def validate_contract(case: dict[str, Any]) -> tuple[bool, list[str]]:
    """校验单条用例是否符合生成契约。

    自动处理字段别名（identifier->id, name->title 等）。

    Returns:
        (is_valid, problems): is_valid=True 表示通过；problems 为问题清单。
    """
    case = normalize_case_fields(case)
    problems: list[str] = []

    # 1. 必填字段检查
    for field in REQUIRED_FIELDS:
        if not case.get(field):
            problems.append(f"缺少必填字段: {field}")

    # 2. 步骤检查（复用 quality_gates 的 normalize_steps）
    steps = case.get("steps") or case.get("test_case_steps") or []
    if steps:
        from app.agents.tools.testcase.quality_gates import normalize_steps

        normalized, step_problems = normalize_steps(steps)
        if step_problems:
            problems.extend(step_problems)
        # 步骤级预期结果完整性
        if normalized:
            for idx, st in enumerate(normalized, start=1):
                if not st.get("result"):
                    problems.append(f"第 {idx} 步缺少预期结果")
    else:
        # 无步骤时，检查用例级预期结果
        if not case.get("expected_results"):
            problems.append("缺少测试步骤或预期结果")

    # 3. 枚举值校验
    keyword = case.get("keyword")
    if keyword and keyword not in {e.value for e in TestCaseKeyword}:
        problems.append(f"关键词 '{keyword}' 不在允许值内（正向/反向/边界）")

    priority = case.get("priority", "medium")
    if priority not in {e.value for e in TestCasePriority}:
        problems.append(f"优先级 '{priority}' 不在允许值内（critical/high/medium/low）")

    # 4. 可执行性标记
    executable = case.get("executable_flag", False)
    if not executable and not problems:
        # 结构完整但未标记可执行 → 提示
        pass  # 不强制，由调用方决定

    return len(problems) == 0, problems


def validate_generation_result(result: dict[str, Any]) -> tuple[bool, list[str], list[dict]]:
    """校验整个生成结果。

    Returns:
        (is_valid, global_problems, case_problems_list)
    """
    global_problems: list[str] = []
    case_problems: list[dict] = []

    cases = result.get("cases") or result.get("test_cases") or []
    if not cases:
        global_problems.append("生成结果为空（无测试用例）")
        return False, global_problems, []

    if not result.get("version"):
        global_problems.append("缺少版本号")

    for idx, case in enumerate(cases, start=1):
        case = normalize_case_fields(case)
        valid, problems = validate_contract(case)
        if not valid:
            case_problems.append({
                "index": idx,
                "id": case.get("id", "unknown"),
                "title": str(case.get("title", ""))[:40],
                "problems": problems,
            })

    return len(global_problems) == 0 and len(case_problems) == 0, global_problems, case_problems
