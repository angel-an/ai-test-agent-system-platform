"""
知识检索服务

负责按项目/业务线/文件夹检索知识，支持降级策略
"""

import logging
import math
from typing import Optional
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func

from app.models.knowledge_space import KnowledgeSpace
from app.models.knowledge_document import KnowledgeDocument
from app.models.knowledge_chunk import KnowledgeChunk
from app.models.knowledge_retrieval_log import KnowledgeRetrievalLog
from app.schemas.knowledge_base import (
    KnowledgeRetrievalRequest,
    KnowledgeRetrievalResponse,
    KnowledgeRetrievalResult,
)
from app.config.settings import settings

logger = logging.getLogger(__name__)


class KnowledgeRetriever:
    """
    知识检索器

    支持按空间检索，带降级策略
    """

    def __init__(self, session: AsyncSession):
        self.session = session
        self.degradation_enabled = getattr(settings, 'rag_degradation_enabled', True)
        self.top_k = getattr(settings, 'rag_top_k', 5)
        self.similarity_threshold = getattr(settings, 'rag_similarity_threshold', 0.7)

    async def retrieve(
        self,
        request: KnowledgeRetrievalRequest,
        project_id: Optional[UUID] = None,
    ) -> KnowledgeRetrievalResponse:
        """
        执行知识检索

        检索链路：
        1. 优先尝试向量检索（需要有效的 embedding）
        2. 如果 embedding 不可用（零向量/API失败），自动降级到关键词匹配
        3. 如果关键词匹配也失败，返回空结果 + 降级标记

        Args:
            request: 检索请求
            project_id: 项目 ID（用于按项目检索）

        Returns:
            KnowledgeRetrievalResponse: 检索结果
        """
        import time
        start_time = time.time()

        query = request.query
        space_id = request.space_id
        top_k = request.top_k or self.top_k
        threshold = request.similarity_threshold or self.similarity_threshold

        degraded = False
        degraded_reason = None
        results = []

        try:
            # 策略 1: 向量检索
            if space_id:
                results = await self._retrieve_by_space(
                    UUID(space_id), query, top_k, threshold
                )
            elif project_id:
                results = await self._retrieve_by_project(
                    project_id, query, top_k, threshold
                )
            else:
                degraded = True
                degraded_reason = "未指定知识空间或项目"

            # 策略 2: 如果向量检索结果为空，检查是否是零向量导致的（降级到关键词）
            if not results and not degraded and self.degradation_enabled:
                query_embedding = await self._get_query_embedding(query)
                if all(v == 0.0 for v in query_embedding):
                    logger.info("Embedding 为零向量，降级到关键词匹配")
                    degraded = True
                    degraded_reason = "Embedding 服务未配置，降级到关键词匹配"
                    results = await self._keyword_fallback(query, space_id, project_id, top_k)

        except Exception as e:
            logger.error(f"检索失败: {e}", exc_info=True)
            if self.degradation_enabled:
                degraded = True
                degraded_reason = f"检索服务异常: {str(e)[:200]}"
                # 尝试降级到关键词匹配
                try:
                    results = await self._keyword_fallback(query, space_id, project_id, top_k)
                except Exception as fallback_e:
                    logger.error(f"降级检索也失败: {fallback_e}")
            else:
                raise

        response_time_ms = int((time.time() - start_time) * 1000)

        # 记录检索日志（外键可能不存在时静默跳过）
        try:
            # 校验 space_id 是否真实存在（避免外键冲突）
            space_exists = False
            if space_id:
                from sqlalchemy import select as sa_select
                space_check = await self.session.execute(
                    sa_select(KnowledgeSpace.id).where(KnowledgeSpace.id == UUID(space_id))
                )
                space_exists = space_check.scalar_one_or_none() is not None

            log = KnowledgeRetrievalLog(
                space_id=UUID(space_id) if space_id and space_exists else None,
                project_id=project_id,
                query=query,
                results_count=len(results),
                top_score=results[0].score if results else None,
                degraded=degraded,
                degraded_reason=degraded_reason,
                response_time_ms=response_time_ms,
            )
            self.session.add(log)
            await self.session.flush()
        except Exception as e:
            logger.warning(f"记录检索日志失败: {e}")

        return KnowledgeRetrievalResponse(
            success=True,
            results=results,
            total=len(results),
            degraded=degraded,
            degraded_reason=degraded_reason,
            query=query,
        )

    async def _retrieve_by_space(
        self,
        space_id: UUID,
        query: str,
        top_k: int,
        threshold: float,
    ) -> list[KnowledgeRetrievalResult]:
        """按知识空间检索"""
        # 获取空间的切片
        result = await self.session.execute(
            select(KnowledgeChunk, KnowledgeDocument.file_name)
            .join(KnowledgeDocument, KnowledgeChunk.document_id == KnowledgeDocument.id)
            .where(KnowledgeChunk.space_id == space_id)
            .where(KnowledgeDocument.status == "indexed")
        )
        rows = result.all()

        if not rows:
            return []

        # 计算相似度并排序
        scored_results = []
        query_embedding = await self._get_query_embedding(query)

        for chunk, file_name in rows:
            if chunk.embedding:
                score = self._cosine_similarity(query_embedding, chunk.embedding)
                if score >= threshold:
                    scored_results.append((score, chunk, file_name))

        # 按分数排序，取 top_k
        scored_results.sort(key=lambda x: x[0], reverse=True)

        return [
            KnowledgeRetrievalResult(
                chunk_id=str(chunk.id),
                document_id=str(chunk.document_id),
                document_name=file_name,
                content=chunk.content[:2000],  # 限制返回长度
                score=round(score, 4),
                meta_data=chunk.meta_data,
            )
            for score, chunk, file_name in scored_results[:top_k]
        ]

    async def _retrieve_by_project(
        self,
        project_id: UUID,
        query: str,
        top_k: int,
        threshold: float,
    ) -> list[KnowledgeRetrievalResult]:
        """按项目检索（跨所有空间）"""
        # 获取项目下的所有空间
        spaces_result = await self.session.execute(
            select(KnowledgeSpace.id).where(KnowledgeSpace.project_id == project_id)
        )
        space_ids = [row[0] for row in spaces_result.all()]

        if not space_ids:
            return []

        # 获取所有空间的切片
        result = await self.session.execute(
            select(KnowledgeChunk, KnowledgeDocument.file_name)
            .join(KnowledgeDocument, KnowledgeChunk.document_id == KnowledgeDocument.id)
            .where(KnowledgeChunk.space_id.in_(space_ids))
            .where(KnowledgeDocument.status == "indexed")
        )
        rows = result.all()

        if not rows:
            return []

        scored_results = []
        query_embedding = await self._get_query_embedding(query)

        for chunk, file_name in rows:
            if chunk.embedding:
                score = self._cosine_similarity(query_embedding, chunk.embedding)
                if score >= threshold:
                    scored_results.append((score, chunk, file_name))

        scored_results.sort(key=lambda x: x[0], reverse=True)

        return [
            KnowledgeRetrievalResult(
                chunk_id=str(chunk.id),
                document_id=str(chunk.document_id),
                document_name=file_name,
                content=chunk.content[:2000],
                score=round(score, 4),
                meta_data=chunk.meta_data,
            )
            for score, chunk, file_name in scored_results[:top_k]
        ]

    async def _keyword_fallback(
        self,
        query: str,
        space_id: Optional[str],
        project_id: Optional[UUID],
        top_k: int,
    ) -> list[KnowledgeRetrievalResult]:
        """关键词匹配降级方案"""
        keywords = [k for k in query.split() if len(k) > 1]
        if not keywords:
            return []

        # 构建查询
        base_query = (
            select(KnowledgeChunk, KnowledgeDocument.file_name)
            .join(KnowledgeDocument, KnowledgeChunk.document_id == KnowledgeDocument.id)
            .where(KnowledgeDocument.status == "indexed")
        )

        if space_id:
            base_query = base_query.where(KnowledgeChunk.space_id == UUID(space_id))
        elif project_id:
            space_ids_result = await self.session.execute(
                select(KnowledgeSpace.id).where(KnowledgeSpace.project_id == project_id)
            )
            space_ids = [row[0] for row in space_ids_result.all()]
            if space_ids:
                base_query = base_query.where(KnowledgeChunk.space_id.in_(space_ids))

        result = await self.session.execute(base_query)
        rows = result.all()

        # 简单关键词匹配评分
        scored_results = []
        query_lower = query.lower()

        for chunk, file_name in rows:
            content_lower = chunk.content.lower()
            # 计算匹配度：包含关键词的比例
            match_count = sum(1 for kw in keywords if kw.lower() in content_lower)
            score = match_count / len(keywords) if keywords else 0

            # 额外加分：完整查询串匹配
            if query_lower in content_lower:
                score += 0.5

            if score > 0:
                scored_results.append((score, chunk, file_name))

        scored_results.sort(key=lambda x: x[0], reverse=True)

        return [
            KnowledgeRetrievalResult(
                chunk_id=str(chunk.id),
                document_id=str(chunk.document_id),
                document_name=file_name,
                content=chunk.content[:2000],
                score=round(min(score, 1.0), 4),  # 限制最高分为 1.0
                meta_data={**chunk.meta_data, "fallback": "keyword"},
            )
            for score, chunk, file_name in scored_results[:top_k]
        ]

    async def _get_query_embedding(self, query: str) -> list[float]:
        """获取查询文本的 embedding 向量（与 anything-chat-rag 框架保持一致）"""
        # 优先使用独立的 embedding_binding_api_key，其次复用 deepseek_api_key
        api_key = getattr(settings, 'embedding_binding_api_key', None) or getattr(settings, 'deepseek_api_key', None)

        if not api_key:
            # 无 API Key，返回零向量（触发关键词降级）
            dimensions = getattr(settings, 'embedding_dim', 1024)
            return [0.0] * dimensions

        try:
            import httpx

            # 使用与 anything-chat-rag 框架一致的模型配置
            model = getattr(settings, 'embedding_model', 'Qwen/Qwen3-Embedding-0.6B')
            api_base = getattr(settings, 'embedding_binding_host', 'https://llmapi.dtyunxi.cn/v1')

            async with httpx.AsyncClient() as client:
                response = await client.post(
                    f"{api_base}/embeddings",
                    headers={
                        "Authorization": f"Bearer {api_key}",
                        "Content-Type": "application/json",
                    },
                    json={
                        "model": model,
                        "input": query[:settings.embedding_token_limit],  # 使用配置的 token 限制
                    },
                    timeout=30.0,
                )
                response.raise_for_status()
                data = response.json()
                return data["data"][0]["embedding"]

        except Exception as e:
            logger.error(f"查询 embedding 生成失败: {e}")
            # 返回零向量，触发关键词降级
            dimensions = getattr(settings, 'embedding_dim', 1024)
            return [0.0] * dimensions

    def _cosine_similarity(self, vec1: list[float], vec2_str: str) -> float:
        """计算两个向量的余弦相似度"""
        try:
            # 解析字符串向量
            if isinstance(vec2_str, str):
                vec2 = [float(x) for x in vec2_str.split(",")]
            else:
                vec2 = vec2_str

            if len(vec1) != len(vec2):
                return 0.0

            dot_product = sum(a * b for a, b in zip(vec1, vec2))
            norm1 = math.sqrt(sum(a * a for a in vec1))
            norm2 = math.sqrt(sum(b * b for b in vec2))

            if norm1 == 0 or norm2 == 0:
                return 0.0

            return dot_product / (norm1 * norm2)

        except Exception as e:
            logger.warning(f"相似度计算失败: {e}")
            return 0.0
