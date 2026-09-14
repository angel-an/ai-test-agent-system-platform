"""
意图编译工具

将用户的自然语言输入编译为结构化的 ApiIntent / ScenarioIntent / TestCaseIntent。

工具职责：
- compile_api_intent: 单接口 NL → ApiIntent
- compile_scenario_intent: 场景 NL → ScenarioIntent（含步骤推断）
- preview_intent_config: 预览意图解析结果

核心设计：工具函数本身做"基于规则的意图推断"，AI 模型在 agent 循环中
通过 system prompt 指导做更精细的填充。两者互补：规则推断保底，AI 精调提升。
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any, Dict, List, Optional

from langchain_core.tools import tool
from pydantic import BaseModel, Field

from app.schemas.intent import (
    ApiIntent,
    AssertionConfig,
    AuthConfig,
    DataConfig,
    IntentCompileResult,
    IntentPreviewResult,
    ScenarioIntent,
    StepIntent,
    TestCaseIntent,
)

logger = logging.getLogger(__name__)


# =============================================================================
# 内部辅助：从自然语言推断结构化配置
# =============================================================================

def _infer_auth_from_nl(nl: str) -> Optional[AuthConfig]:
    """从自然语言中推断认证配置"""
    nl_lower = nl.lower()
    auth = None

    # Bearer token
    if "token" in nl_lower or "bearer" in nl_lower:
        auth = AuthConfig(type="bearer")
    # Basic auth
    elif any(kw in nl_lower for kw in ["用户名", "密码", "username", "password", "basic"]):
        auth = AuthConfig(type="basic")
    # API Key
    elif "api key" in nl_lower or "api_key" in nl_lower:
        auth = AuthConfig(type="api_key")
    # OAuth2
    elif "oauth" in nl_lower or "client_id" in nl_lower:
        auth = AuthConfig(type="oauth2")

    return auth


def _infer_assertions_from_nl(nl: str) -> List[AssertionConfig]:
    """从自然语言中推断断言配置"""
    assertions = []
    nl_lower = nl.lower()

    # 状态码断言
    status_map = {
        "200": 200, "201": 201, "400": 400, "401": 401,
        "403": 403, "404": 404, "500": 500,
    }
    for code, expected in status_map.items():
        if code in nl or f"返回 {code}" in nl:
            assertions.append(AssertionConfig(type="status", expected=expected))
            break

    # 默认断言：如果用户没指定状态码，默认期望 200
    if not any(a.type == "status" for a in assertions):
        assertions.insert(0, AssertionConfig(type="status", expected=200))

    # Token 存在断言
    if "token" in nl_lower:
        assertions.append(
            AssertionConfig(
                type="jsonpath",
                path="$.data.token",
                expected="__exists__",
                operator="ne",
                message="响应中应包含 token 字段",
            )
        )

    # 成功标记断言
    if any(kw in nl_lower for kw in ["成功", "success"]):
        assertions.append(
            AssertionConfig(
                type="jsonpath",
                path="$.success",
                expected=True,
                message="success 字段应为 true",
            )
        )

    return assertions


def _infer_steps_from_nl(nl: str, endpoint_ids: Optional[List[str]] = None) -> List[StepIntent]:
    """
    从自然语言中推断场景步骤。

    基于中文/英文动作关键词拆分业务流程描述。
    例如："用户登录后创建订单并支付" → [登录, 创建订单, 支付]
    """
    steps = []
    nl_lower = nl.lower()

    # 定义步骤识别模式（动作关键词 → 步骤类型映射）
    step_patterns = [
        # (匹配关键词列表, 步骤名称, 默认 HTTP 方法)
        (["登录", "login", "signin", "sign in"], "用户登录", "POST"),
        (["注册", "register", "signup", "sign up"], "用户注册", "POST"),
        (["创建订单", "下单", "create order", "place order"], "创建订单", "POST"),
        (["支付", "付款", "pay", "checkout"], "支付订单", "POST"),
        (["查询", "获取", "列表", "list", "get", "fetch", "search"], "查询数据", "GET"),
        (["更新", "修改", "update", "edit", "modify"], "更新数据", "PUT"),
        (["删除", "remove", "delete"], "删除数据", "DELETE"),
        (["创建", "新建", "新增", "create", "add", "new"], "创建资源", "POST"),
        (["认证", "授权", "auth", "authorize"], "认证授权", "POST"),
        (["提交", "保存", "submit", "save"], "提交数据", "POST"),
    ]

    # 按顺序匹配步骤
    matched_keywords = set()
    step_order = 1

    for keywords, default_name, default_method in step_patterns:
        for kw in keywords:
            if kw in nl_lower and kw not in matched_keywords:
                # 检查是否有更精确的长匹配已经覆盖了这个短词
                if any(longer in nl_lower for longer in matched_keywords if len(longer) > len(kw)):
                    continue

                matched_keywords.add(kw)

                # 构建步骤
                step = StepIntent(
                    name=default_name,
                    natural_language=f"执行{default_name}",
                )

                # 如果有 endpoint_ids，按顺序分配
                if endpoint_ids and step_order <= len(endpoint_ids):
                    step.endpoint_id = endpoint_ids[step_order - 1]

                # 推断断言
                step.assertions = [AssertionConfig(type="status", expected=200)]

                # 登录步骤特殊处理：提取 token
                if "登录" in kw or "login" in kw:
                    step.extractors = [{"name": "token", "path": "$.data.token", "type": "jsonpath"}]
                    step.assertions.append(
                        AssertionConfig(type="jsonpath", path="$.data.token", expected="__exists__", operator="ne")
                    )

                steps.append(step)
                step_order += 1
                break  # 每个 pattern 只匹配一次

    # 如果没有匹配到任何步骤，创建一个通用步骤
    if not steps:
        steps.append(StepIntent(
            name="执行请求",
            natural_language=nl[:50],
            assertions=[AssertionConfig(type="status", expected=200)],
        ))

    return steps


def _build_data_config_from_nl(nl: str) -> Optional[DataConfig]:
    """从自然语言中推断请求数据配置"""
    data = DataConfig()

    # 尝试提取 username/password
    username_match = re.search(r'用户名[\s:：]+(\S+)', nl)
    password_match = re.search(r'密码[\s:：]+(\S+)', nl)

    if username_match or password_match:
        body = {}
        if username_match:
            body["username"] = username_match.group(1)
        if password_match:
            body["password"] = password_match.group(1)
        data.body = body

    # 尝试提取 page/size 分页参数
    page_match = re.search(r'page[=:：]?(\d+)', nl.lower())
    size_match = re.search(r'size[=:：]?(\d+)', nl.lower())
    if page_match or size_match:
        params = {}
        if page_match:
            params["page"] = int(page_match.group(1))
        if size_match:
            params["size"] = int(size_match.group(1))
        data.params = params

    # 如果有 body 或 params，返回 data，否则 None
    if data.body or data.params:
        return data
    return None


# =============================================================================
# LangChain 工具输入 Schema
# =============================================================================

class CompileApiIntentInput(BaseModel):
    """compile_api_intent 工具输入"""
    natural_language: str = Field(..., description="用户自然语言描述", min_length=1)
    endpoint_id: Optional[str] = Field(None, description="关联端点 ID")
    base_url: Optional[str] = Field(None, description="API 基础 URL")
    project_identifier: str = Field(..., description="项目标识符")


class CompileScenarioIntentInput(BaseModel):
    """compile_scenario_intent 工具输入"""
    natural_language: str = Field(..., description="用户自然语言描述", min_length=1)
    endpoint_ids: Optional[List[str]] = Field(
        None, description="关联端点 ID 列表"
    )
    base_url: Optional[str] = Field(None, description="API 基础 URL")
    project_identifier: str = Field(..., description="项目标识符")


class CompileTestCaseIntentInput(BaseModel):
    """compile_test_case_intent 工具输入"""
    natural_language: str = Field(..., description="用户自然语言描述", min_length=1)
    knowledge_space_id: Optional[str] = Field(None, description="知识空间 ID")
    template: str = Field("test_case", description="模板类型")
    project_identifier: str = Field(..., description="项目标识符")


class PreviewIntentInput(BaseModel):
    """preview_intent_config 工具输入"""
    intent_json: str = Field(..., description="意图 JSON 字符串")
    intent_type: str = Field(..., description="意图类型: api|scenario|test_case")


# =============================================================================
# 工具实现
# =============================================================================

@tool(args_schema=CompileApiIntentInput)
def compile_api_intent(
    natural_language: str,
    endpoint_id: Optional[str] = None,
    base_url: Optional[str] = None,
    project_identifier: str = "",
) -> str:
    """
    将用户的自然语言描述编译为单接口测试意图 (ApiIntent)。

    调用此工具时，AI 模型会分析自然语言并填充结构化字段。
    工具负责验证和包装结果。

    Args:
        natural_language: 用户描述，如"测试登录接口，用 admin/123456"
        endpoint_id: 关联的 API 端点 ID（可选）
        base_url: API 基础 URL（可选，覆盖端点默认值）
        project_identifier: 项目标识符

    Returns:
        JSON 格式的 IntentCompileResult，包含 success/intent_type/intent/errors/warnings

    示例：
        compile_api_intent(
            natural_language="测试登录接口，用 admin/123456，期望返回 200 和 token",
            endpoint_id="ep-001",
            base_url="https://api.example.com"
        )
    """
    logger.info("编译 API 意图")

    errors = []
    warnings = []

    # 推断认证配置
    auth = _infer_auth_from_nl(natural_language)
    if auth is None and any(kw in natural_language.lower() for kw in ["登录", "login", "认证", "auth"]):
        warnings.append("未明确指定认证方式，将使用无认证或默认配置")

    # 推断断言
    assertions = _infer_assertions_from_nl(natural_language)

    # 推断请求数据
    data = _build_data_config_from_nl(natural_language)

    # 构建意图
    intent = ApiIntent(
        natural_language=natural_language,
        endpoint_id=endpoint_id,
        base_url=base_url,
        auth=auth,
        data=data,
        assertions=assertions,
    )

    result = IntentCompileResult(
        success=True,
        intent_type="api",
        intent=intent.model_dump(mode="json", exclude_none=True),
        errors=errors,
        warnings=warnings,
    )

    return json.dumps(result.model_dump(mode="json"), ensure_ascii=False, indent=2)


@tool(args_schema=CompileScenarioIntentInput)
def compile_scenario_intent(
    natural_language: str,
    endpoint_ids: Optional[List[str]] = None,
    base_url: Optional[str] = None,
    project_identifier: str = "",
) -> str:
    """
    将用户的自然语言描述编译为多步场景测试意图 (ScenarioIntent)。

    AI 模型会分析业务流程描述，拆分为多个步骤并配置数据依赖。

    Args:
        natural_language: 用户描述，如"用户登录后创建订单并支付"
        endpoint_ids: 关联的端点 ID 列表（可选，按步骤顺序分配）
        base_url: API 基础 URL（可选）
        project_identifier: 项目标识符

    Returns:
        JSON 格式的 IntentCompileResult，包含 success/intent_type/intent/errors/warnings
        intent 字段内是 ScenarioIntent，包含推断出的 steps 列表

    示例：
        compile_scenario_intent(
            natural_language="用户登录后创建订单并支付",
            endpoint_ids=["ep-login", "ep-create-order", "ep-pay"]
        )
    """
    logger.info(f"编译场景意图: {natural_language[:50]}...")

    errors = []
    warnings = []

    # 推断认证
    auth = _infer_auth_from_nl(natural_language)

    # 从自然语言推断步骤（核心改进：不再返回空 steps）
    steps = _infer_steps_from_nl(natural_language, endpoint_ids)

    if not steps:
        warnings.append("未能从描述中识别出明确的步骤，请检查描述是否包含动作关键词")

    # 配置步骤间数据依赖
    for i, step in enumerate(steps):
        if i > 0:
            # 后续步骤依赖前序步骤的数据
            step.depends_on = [i]  # 1-based step index

            # 如果前一步是登录且当前步骤需要认证，自动配置 token 传递
            prev_step = steps[i - 1]
            if prev_step.extractors and any(e.get("name") == "token" for e in prev_step.extractors):
                if not step.data:
                    step.data = DataConfig()
                if not step.data.headers:
                    step.data.headers = {}
                step.data.headers["Authorization"] = f"Bearer ${{{i}.token}}"

    # 构建意图
    intent = ScenarioIntent(
        natural_language=natural_language,
        base_url=base_url,
        auth=auth,
        steps=steps,
        global_variables={},
    )

    result = IntentCompileResult(
        success=len(steps) > 0,
        intent_type="scenario",
        intent=intent.model_dump(mode="json", exclude_none=True),
        errors=errors,
        warnings=warnings,
    )

    return json.dumps(result.model_dump(mode="json"), ensure_ascii=False, indent=2)


@tool(args_schema=CompileTestCaseIntentInput)
def compile_test_case_intent(
    natural_language: str,
    knowledge_space_id: Optional[str] = None,
    template: str = "test_case",
    project_identifier: str = "",
) -> str:
    """
    将用户的自然语言描述编译为测试用例生成意图 (TestCaseIntent)。

    Args:
        natural_language: 功能需求描述
        knowledge_space_id: 知识空间 ID（可选）
        template: 模板类型
        project_identifier: 项目标识符

    Returns:
        JSON 格式的 IntentCompileResult
    """
    logger.info(f"编译测试用例意图: {natural_language[:50]}...")

    intent = TestCaseIntent(
        natural_language=natural_language,
        knowledge_space_id=knowledge_space_id,
        template=template,  # type: ignore[arg-type]
    )

    result = IntentCompileResult(
        success=True,
        intent_type="test_case",
        intent=intent.model_dump(mode="json", exclude_none=True),
    )

    return json.dumps(result.model_dump(mode="json"), ensure_ascii=False, indent=2)


@tool(args_schema=PreviewIntentInput)
def preview_intent_config(intent_json: str, intent_type: str) -> str:
    """
    预览意图配置，展示解析后的执行计划。

    Args:
        intent_json: 意图 JSON 字符串
        intent_type: 意图类型 (api|scenario|test_case)

    Returns:
        预览报告，包含请求预览、变量报告、断言预览等
    """
    try:
        intent_data = json.loads(intent_json)
    except json.JSONDecodeError as e:
        return json.dumps(
            {"success": False, "error": f"JSON 解析失败: {str(e)}"},
            ensure_ascii=False,
        )

    preview = IntentPreviewResult(intent=intent_data)

    # 根据类型生成预览
    if intent_type == "api":
        # 生成请求预览
        request_preview = {}
        if intent_data.get("base_url"):
            request_preview["base_url"] = intent_data["base_url"]
        if intent_data.get("auth"):
            auth = intent_data["auth"]
            request_preview["auth_type"] = auth.get("type", "none")
            # 不暴露敏感信息
            if auth.get("token"):
                request_preview["has_token"] = True
            if auth.get("username"):
                request_preview["has_basic_auth"] = True
        if intent_data.get("data"):
            request_preview["data"] = intent_data["data"]
        preview.request_preview = request_preview

        # 断言预览
        for assertion in intent_data.get("assertions", []):
            preview.assertion_preview.append({
                "type": assertion.get("type"),
                "expected": assertion.get("expected"),
                "description": assertion.get("message", ""),
            })

        preview.execution_plan = f"执行单接口测试: {intent_data.get('natural_language', '')[:50]}"

    elif intent_type == "scenario":
        steps = intent_data.get("steps", [])
        preview.execution_plan = f"执行 {len(steps)} 步场景测试"
        for i, step in enumerate(steps, 1):
            preview.assertion_preview.append({
                "step": i,
                "name": step.get("name", f"步骤 {i}"),
                "assertions_count": len(step.get("assertions", [])),
                "endpoint_id": step.get("endpoint_id"),
            })

    # 变量报告
    variables = intent_data.get("variables", {})
    if variables:
        preview.variable_report = {
            "defined_count": len(variables),
            "variables": list(variables.keys()),
        }

    return json.dumps(preview.model_dump(mode="json"), ensure_ascii=False, indent=2)
