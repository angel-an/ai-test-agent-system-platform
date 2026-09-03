"""
LLM 配置 Schema

GitNexus 全栈分析模块的 LLM 提供商配置。
配置以 JSON 形式存储在文件系统中，前端通过 API 读写。
"""

from typing import Any, Optional

from pydantic import BaseModel, Field


class LLMSettingsPayload(BaseModel):
    """
    LLM 配置载荷。

    形状与前端 LLMSettings 完全对应（gitnexus-web/src/core/llm/types.ts）。
    后端不解析具体字段，只做透传与持久化，便于前端字段调整时无需后端联动。
    """

    model_config = {"extra": "allow"}

    activeProvider: str = Field(default="gemini", description="当前激活的 LLM 提供商")
    intelligentClustering: bool = Field(default=False, description="智能聚类开关")
    hasSeenClusteringPrompt: bool = Field(default=False, description="是否已看过聚类引导")
    useSameModelForClustering: bool = Field(default=True, description="聚类是否复用主模型")

    openai: Optional[dict[str, Any]] = None
    azureOpenAI: Optional[dict[str, Any]] = None
    gemini: Optional[dict[str, Any]] = None
    anthropic: Optional[dict[str, Any]] = None
    ollama: Optional[dict[str, Any]] = None
    openrouter: Optional[dict[str, Any]] = None
    minimax: Optional[dict[str, Any]] = None
    glm: Optional[dict[str, Any]] = None
    clusteringProvider: Optional[dict[str, Any]] = None
