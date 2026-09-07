# -*- coding: utf-8 -*-
"""P1-1: 固化生成提示词 — 步骤与预期成对、禁止模糊断言、引用知识库来源。

目标：让 AI 生成的用例稳定成型，步骤和预期不缺对。
"""

from __future__ import annotations

from typing import Any, Optional


# ---------------------------------------------------------------------------
# 提示词模板
# ---------------------------------------------------------------------------

TESTCASE_GENERATION_PROMPT = """你是一位专业的测试用例设计工程师。请根据以下需求生成测试用例。

【生成约束】
1. 每步必须同时给出 操作步骤(step) 和 预期结果(result)，成对出现
2. 预期结果必须具体可核验，禁止使用模糊词（如"成功"、"正常"、"正确"等）
3. 如涉及知识库内容，必须标注来源文档和段落
4. 用例必须覆盖正向、反向、边界三类场景
5. 步骤文本中不得混写预期结果（预期结果必须单独放在 result 字段）

【输出格式】
每条用例必须包含以下字段：
- id: 用例编号（如 TC-001）
- title: 用例标题
- module: 所属模块
- keyword: 关键词（正向/反向/边界）
- priority: 优先级（critical/high/medium/low）
- preconditions: 前置条件
- steps: 步骤列表，每步为 dict 含 step 和 result 字段
- risk_level: 风险等级（high/medium/low）
- remarks: 备注（用于标记 DB/并发/安全等覆盖维度）

【模糊词禁止表】
{fuzzy_words}

【知识库引用格式】
如涉及知识库，在 remarks 中标注：来源《文档名》第 X 节"""


STEP_FORMAT_TEMPLATE = "{step} —— 预期结果：{result}"

KNOWLEDGE_BASE_CITATION_TEMPLATE = "来源《{doc_name}》第 {section} 节"


# ---------------------------------------------------------------------------
# 提示词约束检查
# ---------------------------------------------------------------------------

def check_prompt_compliance(case: dict[str, Any]) -> list[str]:
    """检查单条用例是否符合生成提示词约束。

    检查项：
    1. 步骤与预期结果是否成对出现
    2. 是否包含模糊断言词
    3. 是否标注知识库来源（如涉及）
    4. 步骤文本是否混写了预期结果

    Returns:
        问题清单（空列表表示全部通过）
    """
    problems: list[str] = []

    steps = case.get("steps") or case.get("test_case_steps") or []

    # 1. 成对检查
    if not steps:
        problems.append("缺少测试步骤")
    else:
        for idx, raw in enumerate(steps, start=1):
            if isinstance(raw, dict):
                step_text = str(raw.get("step", "")).strip()
                result_text = str(raw.get("result", "")).strip()
            elif isinstance(raw, str):
                # 尝试拆分
                from app.agents.tools.testcase.quality_gates import split_trailing_expectation
                step_text, result_text = split_trailing_expectation(raw)
                result_text = result_text or ""
            else:
                problems.append(f"第 {idx} 步格式非法")
                continue

            if not step_text:
                problems.append(f"第 {idx} 步缺少步骤文本")
            if not result_text:
                problems.append(f"第 {idx} 步缺少预期结果（步骤与预期必须成对）")

    # 2. 模糊断言检查（复用 quality_gates）
    from app.agents.tools.testcase.quality_gates import validate_staged_case

    v_problems, _ = validate_staged_case(case)
    fuzzy_problems = [p for p in v_problems if "模糊" in p]
    if fuzzy_problems:
        problems.extend(fuzzy_problems)

    # 3. 知识库引用检查（如果涉及知识库相关标记）
    remarks = str(case.get("remarks", ""))
    has_kb_marker = any(k in remarks for k in ("来源《", "知识库", "KB-", "文档"))
    # 不强制要求，只检查格式是否正确
    if has_kb_marker and "来源《" not in remarks:
        problems.append("知识库引用格式不正确，应使用：来源《文档名》第 X 节")

    # 4. 步骤文本混写预期结果检查
    if steps:
        for idx, raw in enumerate(steps, start=1):
            if isinstance(raw, dict):
                step_text = str(raw.get("step", ""))
                # 步骤文本中不应包含"预期结果"、"期望"等词
                if any(k in step_text for k in ("预期结果", "期望结果", "期望值")):
                    problems.append(f"第 {idx} 步步骤文本混写了预期结果（应放入 result 字段）")

    return problems


def build_generation_prompt(
    requirement: str,
    fuzzy_words: Optional[list[str]] = None,
    knowledge_base_docs: Optional[list[str]] = None,
) -> str:
    """构建完整的测试用例生成提示词。

    Args:
        requirement: 需求描述
        fuzzy_words: 模糊词列表（默认使用 quality_gates 的词表）
        knowledge_base_docs: 可用知识库文档列表
    """
    from app.agents.tools.testcase.quality_gates import FUZZY_WORDS

    words = fuzzy_words or list(FUZZY_WORDS)
    fuzzy_words_str = ", ".join(words[:20])  # 最多显示 20 个
    if len(words) > 20:
        fuzzy_words_str += f" 等（共 {len(words)} 个）"

    prompt = TESTCASE_GENERATION_PROMPT.format(fuzzy_words=fuzzy_words_str)

    if knowledge_base_docs:
        docs_str = "\n".join(f"- {d}" for d in knowledge_base_docs)
        prompt += f"\n\n【可用知识库文档】\n{docs_str}"

    prompt += f"\n\n【需求】\n{requirement}\n"

    return prompt


# ---------------------------------------------------------------------------
# 步骤格式化
# ---------------------------------------------------------------------------

def format_step(step_text: str, result_text: str) -> str:
    """按统一格式输出步骤：步骤 —— 预期结果：结果"""
    return STEP_FORMAT_TEMPLATE.format(step=step_text, result=result_text)


def format_kb_citation(doc_name: str, section: str) -> str:
    """格式化知识库引用"""
    return KNOWLEDGE_BASE_CITATION_TEMPLATE.format(doc_name=doc_name, section=section)
