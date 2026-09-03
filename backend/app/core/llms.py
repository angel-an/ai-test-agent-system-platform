"""
版权所有 连翘|安丹 所有
"""

import logging

from app.config.settings import settings as _settings

# 配置日志
logger = logging.getLogger(__name__)
# pragma: no cover  MC8yOmFIVnBZMlhscm9ua3VMazZkSE5FUkE9PTowNTI3ZjliNQ==


# 创建图片处理模型
def create_image_model():
    """创建图片处理模型（豆包多模态）"""
    from langchain_openai import ChatOpenAI

    try:
        api_key = _settings.image_parser_api_key
        if not api_key:
            logger.warning(
                "image_parser_api_key 未配置，多模态功能将不可用。"
                "请在 .env 文件中设置 IMAGE_PARSER_API_KEY=your_doubao_api_key"
            )
            return None

        return ChatOpenAI(
            base_url=_settings.image_parser_api_base,
            api_key=api_key,
            model=_settings.image_parser_model,
            streaming=True,
        )
    except Exception as e:
        logger.error(f"Failed to create image model: {e}")
        return None


# 创建文本处理模型
def create_text_model():
    """创建文本处理模型"""
    from langchain_deepseek import ChatDeepSeek

    try:
        return ChatDeepSeek(
            api_key=_settings.deepseek_api_key if hasattr(_settings, "deepseek_api_key") else "",
            model="deepseek-chat",
            temperature=0.3,
            streaming=True,
        )
    except ImportError:
        logger.warning("langchain_deepseek not available")
        return None
    except Exception as e:
        logger.error(f"Failed to create text model: {e}")
        return None


# noqa  MS8yOmFIVnBZMlhscm9ua3VMazZkSE5FUkE9PTowNTI3ZjliNQ==

image_model = create_image_model()
text_model = create_text_model()
image_llm_model = image_model
deepseek_model = text_model
