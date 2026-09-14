"""
意图模型 Schema

定义自然语言驱动 API 测试的统一输入契约：
- ApiIntent: 单接口测试意图
- ScenarioIntent: 多步场景测试意图
- TestCaseIntent: 测试用例生成意图

统一字段规范：
- base_url: API 基础地址
- auth: 认证配置（支持 bearer/basic/api_key/oauth2/aes/rsa）
- data: 请求数据（body/params/headers）
- mock: Mock 规则
- assertions: 断言配置
- teardown: 清理配置
- variables: 运行时变量（支持 ${stepN.field} 引用和 ${random_*} 生成）
"""

from __future__ import annotations

from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator


# =============================================================================
# 通用子模型
# =============================================================================

class AuthConfig(BaseModel):
    """认证配置"""
    type: Literal["bearer", "basic", "api_key", "oauth2", "none"] = Field(
        "none", description="认证类型"
    )
    # bearer: token 或从环境变量读取
    token: Optional[str] = Field(None, description="Bearer token 值")
    token_env_var: Optional[str] = Field(
        None, description="从环境变量读取 token 的变量名"
    )
    # basic: username + password
    username: Optional[str] = None
    password: Optional[str] = None
    password_env_var: Optional[str] = Field(
        None, description="从环境变量读取密码的变量名"
    )
    # api_key: key + value
    api_key_name: Optional[str] = Field(None, description="API Key 名称")
    api_key_value: Optional[str] = None
    api_key_in: Literal["header", "query"] = "header"
    # oauth2: client_credentials / refresh_token
    client_id: Optional[str] = None
    client_secret: Optional[str] = None
    token_url: Optional[str] = None
    scope: Optional[str] = None
    refresh_token: Optional[str] = None
    # 加密解密
    encrypt_type: Optional[Literal["aes", "rsa", "none"]] = "none"
    encrypt_key_env_var: Optional[str] = Field(
        None, description="加密密钥环境变量名"
    )
    decrypt_before_use: bool = Field(
        False, description="使用前是否需要解密"
    )
    # 401 自动刷新
    auto_refresh_on_401: bool = Field(
        True, description="401 时是否自动刷新 token"
    )
    refresh_max_retries: int = Field(1, ge=0, le=3)

    model_config = ConfigDict(extra="allow")


class DataConfig(BaseModel):
    """请求数据配置"""
    method: Optional[str] = Field(
        None, description="HTTP 方法: GET, POST, PUT, DELETE, PATCH"
    )
    url: Optional[str] = Field(
        None, description="请求 URL（相对路径或完整 URL）"
    )
    body: Optional[Dict[str, Any]] = Field(
        None, description="请求体（支持 ${variable} 和 ${random_*}）"
    )
    params: Optional[Dict[str, Any]] = Field(
        None, description="URL 查询参数"
    )
    headers: Optional[Dict[str, str]] = Field(
        None, description="请求头"
    )
    path_vars: Optional[Dict[str, Any]] = Field(
        None, description="路径参数（如 /users/{id}）"
    )

    model_config = ConfigDict(extra="allow")


class MockRule(BaseModel):
    """单条 Mock 规则"""
    name: str = Field(..., description="规则名称")
    enabled: bool = True
    # 匹配条件
    match_url_pattern: Optional[str] = Field(
        None, description="URL 匹配模式（支持 glob）"
    )
    match_method: Optional[str] = Field(
        None, description="HTTP 方法匹配"
    )
    match_headers: Optional[Dict[str, str]] = None
    # 响应配置
    response_status: int = 200
    response_headers: Optional[Dict[str, str]] = None
    response_body: Optional[Any] = None
    response_delay_ms: int = 0
    # 透传选项
    passthrough: bool = Field(
        False, description="不匹配时是否透传真实请求"
    )

    model_config = ConfigDict(extra="allow")


class MockConfig(BaseModel):
    """Mock 配置"""
    enabled: bool = False
    base_url: Optional[str] = Field(
        None, description="Mock 服务器地址"
    )
    rules: List[MockRule] = Field(default_factory=list)

    model_config = ConfigDict(extra="allow")


class AssertionConfig(BaseModel):
    """断言配置"""
    type: Literal["status", "jsonpath", "header", "body_contains", "response_time"] = Field(
        ..., description="断言类型"
    )
    path: Optional[str] = Field(
        None, description="JSONPath 或 header 名称"
    )
    expected: Any = Field(..., description="期望值")
    operator: Literal["eq", "ne", "gt", "lt", "ge", "le", "contains", "regex"] = "eq"
    message: Optional[str] = Field(None, description="断言失败时的自定义消息")

    model_config = ConfigDict(extra="allow")


class TeardownConfig(BaseModel):
    """清理配置"""
    enabled: bool = False
    # 清理方式
    cleanup_type: Literal["api_call", "db_query", "script", "none"] = "none"
    # API 调用清理
    endpoint_id: Optional[str] = None
    request_override: Optional[Dict[str, Any]] = None
    # 脚本清理
    script_content: Optional[str] = None
    script_language: Literal["python", "javascript", "shell"] = "python"
    # 数据库清理
    db_query: Optional[str] = None
    # 执行时机
    run_on: Literal["always", "success", "failure"] = "always"

    model_config = ConfigDict(extra="allow")


class SetupConfig(BaseModel):
    """前置准备配置"""
    enabled: bool = False
    setup_type: Literal["api_call", "db_query", "script", "none"] = "none"
    endpoint_id: Optional[str] = None
    request_override: Optional[Dict[str, Any]] = None
    script_content: Optional[str] = None
    script_language: Literal["python", "javascript", "shell"] = "python"
    db_query: Optional[str] = None

    model_config = ConfigDict(extra="allow")


class VariableConfig(BaseModel):
    """变量定义"""
    name: str
    value: Any
    type: Literal["string", "number", "boolean", "object", "array"] = "string"
    is_secret: bool = False
    description: Optional[str] = None

    model_config = ConfigDict(extra="allow")


# =============================================================================
# 意图主模型
# =============================================================================

class ApiIntent(BaseModel):
    """
    单接口测试意图

    用户用自然语言描述的单接口测试需求，经 AI 编译后的结构化配置。

    示例：
        {
            "natural_language": "测试登录接口，用 admin/123456，期望返回 200 和 token",
            "endpoint_id": "xxx",
            "base_url": "https://api.example.com",
            "auth": {"type": "basic", "username": "admin", "password": "123456"},
            "data": {"body": {"username": "admin", "password": "123456"}},
            "assertions": [
                {"type": "status", "expected": 200},
                {"type": "jsonpath", "path": "$.data.token", "expected": "__exists__"}
            ]
        }
    """
    natural_language: str = Field(
        ..., description="用户原始自然语言输入", min_length=1
    )
    endpoint_id: Optional[str] = Field(
        None, description="关联的 API 端点 ID"
    )
    base_url: Optional[str] = Field(
        None, description="API 基础 URL（覆盖端点默认值）"
    )
    auth: Optional[AuthConfig] = Field(
        None, description="认证配置"
    )
    data: Optional[DataConfig] = Field(
        None, description="请求数据配置"
    )
    mock: Optional[MockConfig] = Field(
        None, description="Mock 配置"
    )
    assertions: List[AssertionConfig] = Field(
        default_factory=list, description="断言列表"
    )
    teardown: Optional[TeardownConfig] = Field(
        None, description="测试后清理配置"
    )
    variables: Dict[str, Any] = Field(
        default_factory=dict,
        description="运行时变量（支持 ${random_email} 等）"
    )
    # 元数据
    data_profile: Optional[str] = Field(
        None, description="数据环境 profile（如 XYSJG_UAT_READONLY）"
    )
    tags: List[str] = Field(default_factory=list)
    description: Optional[str] = None

    model_config = ConfigDict(extra="allow")

    @field_validator("assertions", mode="before")
    @classmethod
    def validate_assertions(cls, v):
        if v is None:
            return []
        return v


class StepIntent(BaseModel):
    """
    场景步骤意图

    场景中的单步，支持引用前序步骤数据：${step1.token}
    """
    natural_language: Optional[str] = Field(
        None, description="该步骤的自然语言描述"
    )
    endpoint_id: Optional[str] = None
    name: str = Field(..., min_length=1)
    description: Optional[str] = None
    base_url: Optional[str] = None
    auth: Optional[AuthConfig] = None
    data: Optional[DataConfig] = None
    mock: Optional[MockConfig] = None
    assertions: List[AssertionConfig] = Field(default_factory=list)
    extractors: List[Dict[str, Any]] = Field(
        default_factory=list,
        description="数据提取器 [{name, path, type}]"
    )
    # 步骤间引用
    depends_on: Optional[List[int]] = Field(
        None, description="依赖的步骤序号（1-based）"
    )
    continue_on_failure: bool = False
    delay_ms: int = 0
    retry_count: int = 0

    model_config = ConfigDict(extra="allow")


class ScenarioIntent(BaseModel):
    """
    多步场景测试意图

    用户用自然语言描述的完整业务流程，经 AI 编译为多步骤结构化配置。

    示例：
        {
            "natural_language": "用户登录后创建订单并支付",
            "steps": [
                {"name": "登录", "endpoint_id": "...", "data": {...}, "extractors": [{"name": "token", "path": "$.data.token"}]},
                {"name": "创建订单", "endpoint_id": "...", "data": {"headers": {"Authorization": "Bearer ${step1.token}"}}},
                {"name": "支付", "endpoint_id": "...", "data": {"body": {"orderId": "${step2.orderId}"}}}
            ]
        }
    """
    natural_language: str = Field(..., min_length=1)
    scenario_id: Optional[str] = Field(
        None, description="关联的已有场景 ID（更新时使用）"
    )
    base_url: Optional[str] = None
    auth: Optional[AuthConfig] = Field(
        None, description="场景级统一认证（可被步骤级覆盖）"
    )
    setup: Optional[SetupConfig] = None
    teardown: Optional[TeardownConfig] = None
    steps: List[StepIntent] = Field(default_factory=list)
    global_variables: Dict[str, Any] = Field(default_factory=dict)
    mock: Optional[MockConfig] = None
    # 执行配置
    retry_count: int = 0
    timeout_seconds: int = 300
    parallel_execution: bool = False
    # 元数据
    data_profile: Optional[str] = None
    tags: List[str] = Field(default_factory=list)
    description: Optional[str] = None

    model_config = ConfigDict(extra="allow")


class TestCaseIntent(BaseModel):
    """
    测试用例生成意图

    用户用自然语言描述的功能需求，经 AI 编译为测试用例。
    """
    natural_language: str = Field(..., min_length=1)
    knowledge_space_id: Optional[str] = Field(
        None, description="关联知识空间 ID"
    )
    template: Literal["test_case", "test_case_bdd"] = "test_case"
    endpoint_ids: Optional[List[str]] = Field(
        None, description="关联的端点 ID 列表"
    )
    folder_id: Optional[str] = Field(
        None, description="目标文件夹 ID"
    )
    # 生成控制
    priority: Literal["low", "medium", "high"] = "medium"
    case_kind: Literal["sit", "uat", "smoke", "api"] = "sit"
    max_cases: int = Field(10, ge=1, le=50)
    # 元数据
    tags: List[str] = Field(default_factory=list)

    model_config = ConfigDict(extra="allow")


# =============================================================================
# 编译结果模型
# =============================================================================

class IntentCompileResult(BaseModel):
    """意图编译结果"""
    success: bool
    intent_type: Literal["api", "scenario", "test_case"]
    intent: Optional[Dict[str, Any]] = None
    raw_response: Optional[str] = None
    errors: List[str] = Field(default_factory=list)
    warnings: List[str] = Field(default_factory=list)

    model_config = ConfigDict(extra="allow")


class IntentPreviewResult(BaseModel):
    """意图预览结果"""
    intent: Dict[str, Any]
    # 解析后的请求预览
    request_preview: Optional[Dict[str, Any]] = None
    # 变量解析报告
    variable_report: Dict[str, Any] = Field(default_factory=dict)
    # 认证解析报告
    auth_report: Optional[Dict[str, Any]] = None
    # Mock 匹配报告
    mock_report: Optional[Dict[str, Any]] = None
    # 断言预览
    assertion_preview: List[Dict[str, Any]] = Field(default_factory=list)
    # 执行计划
    execution_plan: Optional[str] = None

    model_config = ConfigDict(extra="allow")


class IntentExecuteResult(BaseModel):
    """意图执行结果"""
    success: bool
    run_id: Optional[str] = None
    status: str = "pending"
    duration_ms: Optional[int] = None
    # 请求/响应
    request: Optional[Dict[str, Any]] = None
    response: Optional[Dict[str, Any]] = None
    # 断言结果
    assertion_results: List[Dict[str, Any]] = Field(default_factory=list)
    # 提取的变量
    extracted_variables: Dict[str, Any] = Field(default_factory=dict)
    # 错误信息
    error_message: Optional[str] = None
    error_type: Optional[str] = None
    # 执行上下文
    execution_context: Optional[Dict[str, Any]] = None

    model_config = ConfigDict(extra="allow")
