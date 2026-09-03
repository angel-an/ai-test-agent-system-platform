"""
LLM 配置服务

读取/写入 GitNexus 全栈分析模块的 LLM 提供商配置。
存储在 backend/workspace/llm-settings.json，全局单文件（个人阶段）。
"""

import json
import os
from pathlib import Path
from typing import Any

_DEFAULT_SETTINGS: dict[str, Any] = {
    "activeProvider": "gemini",
    "intelligentClustering": False,
    "hasSeenClusteringPrompt": False,
    "useSameModelForClustering": True,
    "openai": {"apiKey": "", "model": "gpt-4o", "temperature": 0.1},
    "gemini": {"apiKey": "", "model": "gemini-2.0-flash", "temperature": 0.1},
    "azureOpenAI": {
        "apiKey": "",
        "endpoint": "",
        "deploymentName": "",
        "model": "gpt-4o",
        "apiVersion": "2024-08-01-preview",
        "temperature": 0.1,
    },
    "anthropic": {"apiKey": "", "model": "claude-sonnet-4-20250514", "temperature": 0.1},
    "ollama": {"baseUrl": "http://localhost:11434", "model": "llama3.2", "temperature": 0.1},
    "openrouter": {
        "apiKey": "",
        "model": "",
        "baseUrl": "https://openrouter.ai/api/v1",
        "temperature": 0.1,
    },
    "minimax": {"apiKey": "", "model": "MiniMax-M2.5", "temperature": 0.1},
    "glm": {
        "apiKey": "",
        "model": "GLM-5",
        "baseUrl": "https://api.z.ai/api/coding/paas/v4",
        "temperature": 0.1,
    },
}


def _resolve_storage_path() -> Path:
    """
    解析配置文件存放路径。

    优先使用环境变量 GITNEXUS_LLM_SETTINGS_PATH，否则落到 backend/workspace/llm-settings.json。
    """
    override = os.environ.get("GITNEXUS_LLM_SETTINGS_PATH")
    if override:
        return Path(override)

    backend_root = Path(__file__).resolve().parents[2]
    return backend_root / "workspace" / "llm-settings.json"


class LLMSettingsService:
    """LLM 配置读写服务"""

    def __init__(self, storage_path: Path | None = None) -> None:
        self._storage_path = storage_path or _resolve_storage_path()

    @property
    def storage_path(self) -> Path:
        return self._storage_path

    def load(self) -> dict[str, Any]:
        """读取当前配置；文件不存在时返回默认配置。"""
        if not self._storage_path.exists():
            return dict(_DEFAULT_SETTINGS)
        try:
            with self._storage_path.open("r", encoding="utf-8") as fh:
                data = json.load(fh)
        except (OSError, json.JSONDecodeError):
            return dict(_DEFAULT_SETTINGS)

        merged = dict(_DEFAULT_SETTINGS)
        if isinstance(data, dict):
            merged.update(data)
        return merged

    def save(self, settings: dict[str, Any]) -> dict[str, Any]:
        """整体覆盖保存配置，返回保存后的内容。"""
        self._storage_path.parent.mkdir(parents=True, exist_ok=True)
        with self._storage_path.open("w", encoding="utf-8") as fh:
            json.dump(settings, fh, ensure_ascii=False, indent=2)
        return settings


_service_singleton: LLMSettingsService | None = None


def get_llm_settings_service() -> LLMSettingsService:
    global _service_singleton
    if _service_singleton is None:
        _service_singleton = LLMSettingsService()
    return _service_singleton
