"""
MCP 代理路由

为前端提供 MCP 工具的代理访问，解决浏览器 CORS 限制。
后端通过 langchain_mcp_adapters 连接 MCP 服务器，前端只需访问同域后端 API。
"""
"""
andan
"""


from datetime import datetime
from uuid import UUID
from fastapi import APIRouter, Depends
from pydantic import BaseModel
from typing import Literal, Any
import json
import logging
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from langchain_mcp_adapters.client import MultiServerMCPClient
from langchain_mcp_adapters.tools import load_mcp_tools
# type: ignore  MC80OmFIVnBZMlhscm9ua3VMazZWRU00TXc9PTo2Zjc4ZjA2YQ==

from app.agents.tool_guard import wrap_tool_with_guard
from app.agents.tool_policy import is_tool_denied
from app.config.database import get_db
from app.config.minio_client import MinIOClient
from app.models.folder import Folder
from app.models.folder_type import FolderType
from app.services.api_test_service import APITestService
from app.services.web_test_service import WebTestService

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/mcp", tags=["MCP 代理"])


class McpServerConfig(BaseModel):
    id: str
    name: str
    url: str = ""
    enabled: bool = True
    transport: Literal["sse", "streamableHttp", "stdio"] = "sse"
    command: str | None = None
    args: list[str] | None = None


class McpToolsRequest(BaseModel):
    servers: list[McpServerConfig]


class McpToolDef(BaseModel):
    name: str
    description: str
    schema: dict[str, Any]
    server_id: str
# type: ignore  MS80OmFIVnBZMlhscm9ua3VMazZWRU00TXc9PTo2Zjc4ZjA2YQ==


class McpToolsResponse(BaseModel):
    tools: list[McpToolDef]
    errors: list[str]


class McpCallRequest(BaseModel):
    server: McpServerConfig
    tool_name: str
    args: dict[str, Any]


class McpCallResponse(BaseModel):
    result: str = ""
    error: str | None = None
    final: bool = False  # True = 安全策略终局拒绝，调用方不得重试


def _result_is_final(result: str) -> bool:
    """检测工具返回内容是否为终局拒绝（guard 返回的 {final:true} JSON）。

    P0-1 复审修正：让 /mcp/call 的 final 语义显式透传给调用方，
    而不是混在普通 error 字符串里（否则调用方会当普通错误触发重试）。
    """
    if not isinstance(result, str):
        return False
    try:
        parsed = json.loads(result)
    except (ValueError, TypeError):
        return False
    return isinstance(parsed, dict) and parsed.get("final") is True


# ========== 内部 MCP 工具定义（无需外部 MCP 服务器，直接操作平台数据） ==========

_INTERNAL_TOOLS: list[McpToolDef] = [
    McpToolDef(
        name="save_api_test_script",
        description="将 AI 生成的 API 测试脚本保存到平台。会自动按 模块/功能 创建文件夹层级。",
        schema={
            "type": "object",
            "properties": {
                "project_id": {"type": "string", "description": "项目 UUID"},
                "test_type": {"type": "string", "enum": ["endpoint", "scenario"], "description": "endpoint=API单接口测试, scenario=API场景测试"},
                "module_name": {"type": "string", "description": "一级文件夹名称（模块/业务域）"},
                "function_name": {"type": "string", "description": "二级文件夹名称（功能/子业务）"},
                "script_name": {"type": "string", "description": "脚本文件名称（不含扩展名）"},
                "script_content": {"type": "string", "description": "完整的脚本内容文本"},
                "language": {"type": "string", "enum": ["typescript", "python", "javascript"], "description": "脚本语言，默认 typescript"},
            },
            "required": ["project_id", "test_type", "module_name", "function_name", "script_name", "script_content"],
        },
        server_id="internal-test-tools",
    ),
    McpToolDef(
        name="save_web_test_script",
        description=(
            "将 AI 生成的 Web 测试脚本保存到平台并登记脚本来源（执行治理层 2a："
            "真实项目 + 子功能附件 + 内容哈希 三要素绑定）。必须提供 sub_function_id；"
            "保存成功后脚本方可被执行（execute_web_script / 测试运行页面）。"
        ),
        schema={
            "type": "object",
            "properties": {
                "sub_function_id": {"type": "string", "description": "Web 子功能 UUID（2a 严格绑定必需）"},
                "script_content": {"type": "string", "description": "完整的脚本内容文本"},
                "language": {"type": "string", "enum": ["typescript", "python", "javascript"], "description": "脚本语言，默认 typescript"},
                # 以下字段为兼容旧调用保留（2a 以 sub_function 真实归属为准，不再使用）
                "project_id": {"type": "string", "description": "[已废弃] 项目 UUID（由子功能归属反查）"},
                "module_name": {"type": "string", "description": "[已废弃] 一级文件夹名称"},
                "function_name": {"type": "string", "description": "[已废弃] 二级文件夹名称"},
                "script_name": {"type": "string", "description": "[已废弃] 脚本文件名称"},
            },
            "required": ["sub_function_id", "script_content"],
        },
        server_id="internal-test-tools",
    ),
]


def _prune_chart_schema(schema: dict[str, Any]) -> dict[str, Any]:
    """精简 mcp-server-chart 的工具 schema，减少 description 长度和冗余字段。"""
    if not isinstance(schema, dict):
        return schema
    pruned: dict[str, Any] = {}
    for key, value in schema.items():
        if key == "description" and isinstance(value, str):
            # 截断过长的 description，保留核心信息
            pruned[key] = value[:120] + "..." if len(value) > 120 else value
        elif key == "$defs":
            # 保留 $defs 但递归精简
            pruned[key] = {
                k: _prune_chart_schema(v) if isinstance(v, dict) else v
                for k, v in value.items()
            }
        elif isinstance(value, dict):
            pruned[key] = _prune_chart_schema(value)
        elif isinstance(value, list):
            pruned[key] = [
                _prune_chart_schema(item) if isinstance(item, dict) else item
                for item in value
            ]
        else:
            pruned[key] = value
    return pruned


def _build_connection_config(server: McpServerConfig) -> dict[str, Any]:
    """将前端传来的 MCP 服务器配置转换为 MultiServerMCPClient 连接配置。"""
    if server.transport == "stdio":
        return {
            "transport": "stdio",
            "command": server.command or "",
            "args": server.args or [],
        }
    else:
        transport_type = "http" if server.transport == "streamableHttp" else "sse"
        return {
            "transport": transport_type,
            "url": server.url,
        }
# fmt: off  Mi80OmFIVnBZMlhscm9ua3VMazZWRU00TXc9PTo2Zjc4ZjA2YQ==


# ========== 内部工具辅助函数 ==========

async def _get_or_create_folder(
    db: AsyncSession,
    project_id: UUID,
    name: str,
    folder_type: FolderType,
    parent_id: UUID | None = None,
) -> Folder:
    """根据名称、类型、父文件夹查找或创建文件夹。"""
    result = await db.execute(
        select(Folder).where(
            Folder.project_id == project_id,
            Folder.name == name,
            Folder.folder_type == folder_type,
            Folder.parent_id == parent_id,
        )
    )
    folder = result.scalar_one_or_none()
    if folder:
        return folder

    folder = Folder(
        project_id=project_id,
        name=name,
        folder_type=folder_type,
        parent_id=parent_id,
    )
    db.add(folder)
    await db.flush()
    await db.refresh(folder)
    return folder


async def _save_api_test_script(args: dict[str, Any], db: AsyncSession) -> str:
    """保存 API 测试脚本到平台（自动创建模块/功能文件夹）。"""
    project_id = UUID(args["project_id"])
    test_type = args["test_type"]  # "endpoint" or "scenario"
    module_name = args["module_name"]
    function_name = args["function_name"]
    script_name = args["script_name"]
    script_content = args["script_content"]
    language = args.get("language", "typescript")

    folder_type = FolderType.API_TEST if test_type == "endpoint" else FolderType.SCENARIO_TEST

    # 1. 获取/创建模块文件夹（一级）
    module_folder = await _get_or_create_folder(db, project_id, module_name, folder_type, parent_id=None)

    # 2. 获取/创建功能文件夹（二级）
    function_folder = await _get_or_create_folder(db, project_id, function_name, folder_type, parent_id=module_folder.id)

    # 3. 上传脚本到 MinIO
    timestamp = datetime.now().strftime("%Y%m%d%H%M%S")
    safe_name = script_name.replace(" ", "_").replace("/", "_")
    object_name = f"api-tests/{project_id}/{timestamp}-{safe_name}"
    MinIOClient.upload_bytes(object_name, script_content.encode("utf-8"), "text/plain")

    # 4. 创建 API 测试记录
    service = APITestService(db)
    result = await service.create_api_test(
        project_identifier=str(project_id),
        name=script_name,
        schema_path="",
        script_path=object_name,
        script_format="playwright",
        script_language=language,
        description=f"AI generated {test_type} test for {module_name}/{function_name}",
        total_endpoints=1 if test_type == "endpoint" else 0,
        total_scenarios=1 if test_type == "scenario" else 0,
        folder_id=str(function_folder.id),
    )

    return f"✅ API test script saved successfully. ID: {result['id']}, Identifier: {result['identifier']}"


async def _save_web_test_script(args: dict[str, Any], db: AsyncSession) -> str:
    """保存 Web 测试脚本到平台（执行治理层 2a：复用 agents 工具的实现，
    原子登记脚本来源——真实项目 + 子功能附件 + 内容哈希 三要素绑定）。

    rev31（HTTP 执行面纳入 2a）：不再走旧的"直接 MinIO + create_web_test"
    路径（无来源登记，导致 execute_web_script / 测试运行页面对同一脚本授权失败）；
    必须提供 sub_function_id，缺失 → 终局拒绝。
    """
    from app.agents.tools.web.artifacts_tools import (
        save_web_test_script as _raw_save_web_test_script,
    )

    sub_function_id = (args.get("sub_function_id") or "").strip()
    if not sub_function_id:
        return json.dumps({
            "success": False,
            "final": True,
            "guard": "script_provenance",
            "reason": "save_web_test_script 必须提供 sub_function_id（2a 严格三要素绑定：项目+附件+哈希）",
            "message": "缺少 sub_function_id，终局拒绝。",
        }, ensure_ascii=False)
    if not (args.get("script_content") or "").strip():
        return json.dumps({
            "success": False,
            "final": True,
            "guard": "script_provenance",
            "reason": "script_content 为空",
            "message": "脚本内容为空，拒绝保存。",
        }, ensure_ascii=False)

    result = await _raw_save_web_test_script.coroutine(
        sub_function_id=sub_function_id,
        script_content=args.get("script_content"),
        script_language=args.get("language", "typescript"),
        script_format="playwright",
        project_identifier="",  # 2a：真实项目由 sub_function 反查，不信任调用方参数
    )
    if isinstance(result, dict) and result.get("error"):
        return json.dumps({
            "success": False,
            "error": result.get("error"),
            "message": f"保存失败：{result.get('error')}",
        }, ensure_ascii=False)
    if isinstance(result, dict) and result.get("success"):
        return (
            f"✅ Web test script saved & registered (2a). "
            f"WebTest: {result.get('web_test_identifier')}, "
            f"Attachment: {result.get('attachment_id')}, Path: {result.get('file_path')}"
        )
    return json.dumps({
        "success": False,
        "error": "unknown",
        "message": f"保存结果异常: {result}",
    }, ensure_ascii=False)


async def _execute_internal_tool(request: McpCallRequest, db: AsyncSession) -> str:
    """执行内部 MCP 工具（不连接外部服务器）。"""
    if request.tool_name == "save_api_test_script":
        return await _save_api_test_script(request.args, db)
    elif request.tool_name == "save_web_test_script":
        return await _save_web_test_script(request.args, db)
    else:
        return f"Unknown internal tool: {request.tool_name}"


# ========== 路由 ==========

@router.post("/tools", response_model=McpToolsResponse)
async def list_mcp_tools(request: McpToolsRequest):
    """
    连接 MCP 服务器并返回可用工具列表。

    每个工具附带 server_id，以便前端在调用时知道该通过哪个服务器路由。
    对于 server.id 以 internal- 开头的服务器，直接返回预定义的内部工具，不建立外部连接。
    """
    enabled_servers = [s for s in request.servers if s.enabled]
    if not enabled_servers:
        return McpToolsResponse(tools=[], errors=[])

    all_tools: list[McpToolDef] = []
    errors: list[str] = []

    for server in enabled_servers:
        # 内部 MCP 服务器：无需外部连接
        if server.id.startswith("internal-"):
            for tool in _INTERNAL_TOOLS:
                if tool.server_id == server.id:
                    all_tools.append(tool)
            continue

        try:
            connection = _build_connection_config(server)
            client = MultiServerMCPClient({server.id: connection})
            tools = await client.get_tools(server_name=server.id)

            for tool in tools:
                # 安全策略（P0-1）：危险工具不暴露给前端（"看不到"优先于"拦得住"）
                if is_tool_denied(tool.name):
                    logger.warning(
                        "[mcp_proxy] 过滤危险工具 '%s'（server=%s）",
                        tool.name, server.id,
                    )
                    continue
                schema: dict[str, Any] = {}
                if hasattr(tool, "args_schema") and tool.args_schema is not None:
                    if hasattr(tool.args_schema, "model_json_schema"):
                        schema = tool.args_schema.model_json_schema()
                    elif isinstance(tool.args_schema, dict):
                        schema = tool.args_schema
                    elif hasattr(tool.args_schema, "schema"):
                        schema = getattr(tool.args_schema, "schema", {})

                # 对 mcp-server-chart 的 schema 进行精简，避免过大的工具定义
                if server.id == "mcp-server-chart" or server.name == "mcp-server-chart":
                    schema = _prune_chart_schema(schema)

                all_tools.append(
                    McpToolDef(
                        name=tool.name,
                        description=tool.description or "",
                        schema=schema,
                        server_id=server.id,
                    )
                )
        except Exception as e:
            errors.append(
                f"Failed to load tools from '{server.name}' ({server.url}): {str(e)}"
            )

    return McpToolsResponse(tools=all_tools, errors=errors)


@router.post("/call", response_model=McpCallResponse)
async def call_mcp_tool(request: McpCallRequest, db: AsyncSession = Depends(get_db)):
    """
    调用指定的 MCP 工具。

    后端创建临时连接，执行工具调用后立刻断开。
    对于 server.id 以 internal- 开头的服务器，直接执行内部逻辑，不建立外部连接。
    """
    # 安全策略（P0-1）：黑名单工具终局拒绝，不建立任何连接
    if is_tool_denied(request.tool_name):
        return McpCallResponse(
            result="",
            error=f"[tool_guard] 工具 '{request.tool_name}' 被安全策略禁用（final denial）",
            final=True,
        )

    # 内部 MCP 工具：直接本地执行
    if request.server.id.startswith("internal-"):
        try:
            result = await _execute_internal_tool(request, db)
            # rev31：内部工具同样透传 final（_save_web_test_script 等返回
            # {final:true} 终局拒绝 JSON 时，调用方必须停止重试）
            return McpCallResponse(
                result=result,
                error=None,
                final=_result_is_final(result),
            )
        except Exception as e:
            return McpCallResponse(result="", error=str(e))

    # 外部 MCP 工具：建立临时连接
    try:
        connection = _build_connection_config(request.server)
        client = MultiServerMCPClient({request.server.id: connection})
        tools = await client.get_tools(server_name=request.server.id)

        target_tool = next(
            (t for t in tools if t.name == request.tool_name), None
        )
        if target_tool is None:
            return McpCallResponse(
                result="",
                error=f"Tool '{request.tool_name}' not found in server '{request.server.id}'",
            )
# pragma: no cover  My80OmFIVnBZMlhscm9ua3VMazZWRU00TXc9PTo2Zjc4ZjA2YQ==

        result = await wrap_tool_with_guard(target_tool).ainvoke(request.args)
        return McpCallResponse(
            result=result if isinstance(result, str) else str(result),
            error=None,
            final=_result_is_final(result),
        )
    except Exception as e:
        return McpCallResponse(result="", error=str(e))
