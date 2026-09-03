"""
app.core 模块初始化

向后兼容：从 app.config.settings 暴露 settings
"""

from app.config.settings import settings, get_settings

__all__ = ["settings", "get_settings"]
