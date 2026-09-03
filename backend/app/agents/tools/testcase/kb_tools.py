"""
知识库工具（Agent 使用）

为测试用例生成 Agent 提供知识库检索能力
"""

import logging
from typing import Optional

from langchain_core.tools import tool

from app.config.database import async_session_factory
from app.services.knowledge_retriever import KnowledgeRetriever
from app.schemas.knowledge_base import KnowledgeRetrievalRequest
from app.repositories.project_repo import ProjectRepository

logger = logging.getLogger(__name__)


@tool
async def query_knowledge_base_tool(
    query: str,
    project_identifier: str = "",
    space_id: Optional[str] = None,
    top_k: int = 5,
) -> dict:
    """
    查询知识库，检索与测试需求相关的知识文档内容。

    在生成测试用例前调用此工具，获取需求文档、历史用例、业务规则等背景知识。

    Args:
        query: 检索查询文本，应描述需要了解的业务场景或功能点
        project_identifier: 项目标识符（如 PRJ-001）
        space_id: 指定知识空间 ID（可选，不指定则检索项目下所有空间）
        top_k: 返回结果数量（默认 5）

    Returns:
        dict: 检索结果
            - success: bool, 是否成功
            - results: list, 检索到的知识片段
            - degraded: bool, 是否触发降级
            - degraded_reason: str, 降级原因
    """
    if not query or len(query.strip()) < 2:
        return {
            "success": False,
            "results": [],
            "degraded": True,
            "degraded_reason": "查询文本太短",
        }

    async with async_session_factory() as session:
        try:
            # 获取项目 ID
            project_repo = ProjectRepository(session)
            project = None
            if project_identifier:
                project = await project_repo.get_by_identifier(project_identifier)

            retriever = KnowledgeRetriever(session)
            request = KnowledgeRetrievalRequest(
                query=query,
                space_id=space_id,
                top_k=top_k,
            )

            response = await retriever.retrieve(
                request,
                project_id=project.id if project else None,
            )

            # 格式化结果
            results = []
            for r in response.results:
                results.append({
                    "content": r.content,
                    "document_name": r.document_name,
                    "score": r.score,
                    "meta_data": r.meta_data,
                })

            return {
                "success": response.success,
                "results": results,
                "total": response.total,
                "degraded": response.degraded,
                "degraded_reason": response.degraded_reason,
            }

        except Exception as e:
            logger.error(f"知识库检索失败: {e}", exc_info=True)
            return {
                "success": False,
                "results": [],
                "degraded": True,
                "degraded_reason": f"检索服务异常: {str(e)[:200]}",
            }


@tool
async def get_knowledge_spaces_tool(
    project_identifier: str,
) -> dict:
    """
    获取项目下的知识空间列表。

    在需要了解项目有哪些知识库时使用，帮助选择合适的空间进行检索。

    Args:
        project_identifier: 项目标识符（如 PRJ-001）

    Returns:
        dict: 知识空间列表
            - success: bool
            - spaces: list, 知识空间信息
    """
    async with async_session_factory() as session:
        try:
            from app.services.knowledge_base_service import KnowledgeBaseService

            service = KnowledgeBaseService(session)
            spaces, total = await service.list_spaces(
                project_identifier=project_identifier,
                offset=0,
                limit=100,
            )

            return {
                "success": True,
                "spaces": [
                    {
                        "id": str(s.id),
                        "name": s.name,
                        "description": s.description,
                        "business_line": s.business_line,
                        "document_count": s.document_count,
                    }
                    for s in spaces
                ],
                "total": total,
            }

        except Exception as e:
            logger.error(f"获取知识空间列表失败: {e}", exc_info=True)
            return {
                "success": False,
                "spaces": [],
                "error": str(e),
            }
