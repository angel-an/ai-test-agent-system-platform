"""
Captcha OCR Module - 图形验证码自动识别模块

支持多种 OCR 引擎：
1. ddddocr (推荐) - 轻量高效，专为验证码优化
2. paddleocr - 百度开源 OCR，准确率高
3. easyocr - 多语言支持
4. tesseract - 传统 OCR

适用场景：
- 纯字母验证码（4-6位）
- 数字验证码
- 字母+数字混合验证码
- 简单干扰线验证码

安装依赖：
    pip install ddddocr
    # 或
    pip install paddleocr
    # 或
    pip install easyocr
"""
import logging
import tempfile
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Optional, Union

logger = logging.getLogger(__name__)


class BaseOCREngine(ABC):
    """OCR 引擎基类"""

    @abstractmethod
    def recognize(self, image_path: Union[str, Path]) -> Optional[str]:
        """
        识别验证码图片

        Args:
            image_path: 图片文件路径

        Returns:
            识别结果字符串，失败返回 None
        """
        pass

    @abstractmethod
    def is_available(self) -> bool:
        """检查引擎是否可用"""
        pass


class DdddOCREngine(BaseOCREngine):
    """
    ddddocr 引擎 - 专为验证码优化的轻量 OCR

    特点：
    - 模型轻量，CPU 推理快
    - 对验证码的扭曲、干扰线有较好鲁棒性
    - 支持纯字母、数字、混合
    """

    def __init__(self):
        self._ocr = None
        self._initialized = False

    def is_available(self) -> bool:
        try:
            import ddddocr
            return True
        except ImportError:
            return False

    def _init(self):
        if self._initialized:
            return
        try:
            import ddddocr
            # 使用 OCR 模式（识别标准字符）
            self._ocr = ddddocr.DdddOcr(show_ad=False)
            self._initialized = True
            logger.info("ddddocr 引擎初始化成功")
        except Exception as e:
            logger.error(f"ddddocr 初始化失败: {e}")
            raise

    def recognize(self, image_path: Union[str, Path]) -> Optional[str]:
        try:
            self._init()

            with open(image_path, 'rb') as f:
                image_bytes = f.read()

            result = self._ocr.classification(image_bytes)

            # 清理结果：只保留字母和数字
            cleaned = self._clean_result(result)
            logger.info(f"ddddocr 识别结果: '{result}' -> 清理后: '{cleaned}'")

            return cleaned if cleaned else None

        except Exception as e:
            logger.error(f"ddddocr 识别失败: {e}")
            return None

    @staticmethod
    def _clean_result(text: str) -> str:
        """清理识别结果，只保留字母和数字"""
        import re
        # 移除非字母数字字符，转大写
        cleaned = re.sub(r'[^a-zA-Z0-9]', '', text).upper()
        return cleaned


class PaddleOCREngine(BaseOCREngine):
    """
    PaddleOCR 引擎 - 百度开源 OCR

    特点：
    - 准确率高
    - 支持多语言
    - 模型较大，首次加载慢
    """

    def __init__(self, use_angle_cls: bool = True, lang: str = 'en'):
        self.use_angle_cls = use_angle_cls
        self.lang = lang
        self._ocr = None
        self._initialized = False

    def is_available(self) -> bool:
        try:
            from paddleocr import PaddleOCR
            return True
        except ImportError:
            return False

    def _init(self):
        if self._initialized:
            return
        try:
            from paddleocr import PaddleOCR
            self._ocr = PaddleOCR(
                use_angle_cls=self.use_angle_cls,
                lang=self.lang,
                show_log=False
            )
            self._initialized = True
            logger.info("PaddleOCR 引擎初始化成功")
        except Exception as e:
            logger.error(f"PaddleOCR 初始化失败: {e}")
            raise

    def recognize(self, image_path: Union[str, Path]) -> Optional[str]:
        try:
            self._init()

            result = self._ocr.ocr(str(image_path), cls=True)

            if not result or not result[0]:
                return None

            # 提取文本
            texts = []
            for line in result[0]:
                if line:
                    texts.append(line[1][0])  # text content

            full_text = ''.join(texts)
            cleaned = self._clean_result(full_text)
            logger.info(f"PaddleOCR 识别结果: '{full_text}' -> 清理后: '{cleaned}'")

            return cleaned if cleaned else None

        except Exception as e:
            logger.error(f"PaddleOCR 识别失败: {e}")
            return None

    @staticmethod
    def _clean_result(text: str) -> str:
        import re
        cleaned = re.sub(r'[^a-zA-Z0-9]', '', text).upper()
        return cleaned


class EasyOCREngine(BaseOCREngine):
    """
    EasyOCR 引擎

    特点：
    - 支持 80+ 语言
    - 基于 PyTorch
    """

    def __init__(self, lang_list: list = None):
        self.lang_list = lang_list or ['en']
        self._reader = None
        self._initialized = False

    def is_available(self) -> bool:
        try:
            import easyocr
            return True
        except ImportError:
            return False

    def _init(self):
        if self._initialized:
            return
        try:
            import easyocr
            self._reader = easyocr.Reader(self.lang_list, gpu=False)
            self._initialized = True
            logger.info("EasyOCR 引擎初始化成功")
        except Exception as e:
            logger.error(f"EasyOCR 初始化失败: {e}")
            raise

    def recognize(self, image_path: Union[str, Path]) -> Optional[str]:
        try:
            self._init()

            result = self._reader.readtext(str(image_path), detail=0)
            full_text = ''.join(result)
            cleaned = self._clean_result(full_text)
            logger.info(f"EasyOCR 识别结果: '{full_text}' -> 清理后: '{cleaned}'")

            return cleaned if cleaned else None

        except Exception as e:
            logger.error(f"EasyOCR 识别失败: {e}")
            return None

    @staticmethod
    def _clean_result(text: str) -> str:
        import re
        cleaned = re.sub(r'[^a-zA-Z0-9]', '', text).upper()
        return cleaned


class TesseractOCREngine(BaseOCREngine):
    """
    Tesseract OCR 引擎

    需要系统安装 tesseract:
    - Windows: 下载安装包并添加到 PATH
    - Linux: apt-get install tesseract-ocr
    - macOS: brew install tesseract
    """

    def __init__(self, config: str = '--psm 7 -c tessedit_char_whitelist=ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789'):
        """
        Args:
            config: Tesseract 配置
                --psm 7: 将图像视为单行文本
                tessedit_char_whitelist: 只识别指定字符
        """
        self.config = config

    def is_available(self) -> bool:
        try:
            import pytesseract
            pytesseract.get_tesseract_version()
            return True
        except Exception:
            return False

    def recognize(self, image_path: Union[str, Path]) -> Optional[str]:
        try:
            import pytesseract
            from PIL import Image

            image = Image.open(image_path)
            # 预处理：灰度化、二值化
            image = image.convert('L')

            result = pytesseract.image_to_string(image, config=self.config)
            cleaned = self._clean_result(result)
            logger.info(f"Tesseract 识别结果: '{result}' -> 清理后: '{cleaned}'")

            return cleaned if cleaned else None

        except Exception as e:
            logger.error(f"Tesseract 识别失败: {e}")
            return None

    @staticmethod
    def _clean_result(text: str) -> str:
        import re
        cleaned = re.sub(r'[^a-zA-Z0-9]', '', text).upper()
        return cleaned


class CaptchaOCR:
    """
    验证码 OCR 识别器

    自动选择最佳可用引擎，支持多引擎投票。

    使用示例：
        ocr = CaptchaOCR()
        result = ocr.recognize("captcha.png")
        print(result)  # "ABCD"

        # 指定引擎
        ocr = CaptchaOCR(preferred_engine="ddddocr")

        # 多引擎投票
        ocr = CaptchaOCR(vote_mode=True, vote_engines=["ddddocr", "paddleocr"])
    """

    # 引擎优先级（按推荐程度）
    ENGINE_PRIORITY = ["ddddocr", "paddleocr", "easyocr", "tesseract"]

    def __init__(
        self,
        preferred_engine: Optional[str] = None,
        vote_mode: bool = False,
        vote_engines: Optional[list] = None,
        min_confidence: int = 2
    ):
        """
        Args:
            preferred_engine: 首选引擎名称
            vote_mode: 是否启用多引擎投票模式
            vote_engines: 投票使用的引擎列表
            min_confidence: 投票通过的最小一致数
        """
        self.preferred_engine = preferred_engine
        self.vote_mode = vote_mode
        self.vote_engines = vote_engines or self.ENGINE_PRIORITY
        self.min_confidence = min_confidence

        self._engines: dict[str, BaseOCREngine] = {}
        self._available_engines: list[str] = []

        self._init_engines()

    def _init_engines(self):
        """初始化所有可用引擎"""
        engine_classes = {
            "ddddocr": DdddOCREngine,
            "paddleocr": PaddleOCREngine,
            "easyocr": EasyOCREngine,
            "tesseract": TesseractOCREngine,
        }

        for name, cls in engine_classes.items():
            try:
                engine = cls()
                if engine.is_available():
                    self._engines[name] = engine
                    self._available_engines.append(name)
                    logger.info(f"✅ OCR 引擎可用: {name}")
                else:
                    logger.debug(f"❌ OCR 引擎不可用: {name}")
            except Exception as e:
                logger.debug(f"❌ OCR 引擎初始化失败: {name} - {e}")

        if not self._available_engines:
            logger.warning("⚠️ 没有可用的 OCR 引擎，请安装 ddddocr 或 paddleocr")

    def is_available(self) -> bool:
        """是否有可用引擎"""
        return len(self._available_engines) > 0

    def recognize(self, image_path: Union[str, Path]) -> Optional[str]:
        """
        识别验证码

        Args:
            image_path: 图片文件路径

        Returns:
            识别结果，失败返回 None
        """
        if not self._available_engines:
            logger.error("没有可用的 OCR 引擎")
            return None

        if self.vote_mode:
            return self._vote_recognize(image_path)
        else:
            return self._single_recognize(image_path)

    def _single_recognize(self, image_path: Union[str, Path]) -> Optional[str]:
        """单引擎识别"""
        # 确定使用哪个引擎
        engine_name = self._select_engine()
        if not engine_name:
            return None

        engine = self._engines[engine_name]
        result = engine.recognize(image_path)

        if result:
            logger.info(f"🎯 单引擎识别成功 [{engine_name}]: {result}")
        else:
            logger.warning(f"❌ 单引擎识别失败 [{engine_name}]")

        return result

    def _vote_recognize(self, image_path: Union[str, Path]) -> Optional[str]:
        """多引擎投票识别"""
        results = {}

        for name in self.vote_engines:
            if name not in self._available_engines:
                continue

            try:
                engine = self._engines[name]
                result = engine.recognize(image_path)
                if result:
                    results[name] = result
                    logger.info(f"投票 [{name}]: {result}")
            except Exception as e:
                logger.warning(f"投票引擎 {name} 失败: {e}")

        if not results:
            logger.warning("所有引擎识别失败")
            return None

        # 统计投票结果
        from collections import Counter
        vote_counts = Counter(results.values())
        most_common = vote_counts.most_common(1)[0]
        best_result, confidence = most_common

        logger.info(f"🗳️ 投票结果: {dict(vote_counts)}")

        if confidence >= self.min_confidence:
            logger.info(f"🎯 投票通过 [{confidence}/{len(results)}]: {best_result}")
            return best_result
        else:
            logger.warning(f"⚠️ 投票未通过 (置信度 {confidence} < {self.min_confidence})")
            # 返回置信度最高的结果
            return best_result

    def _select_engine(self) -> Optional[str]:
        """选择要使用的引擎"""
        # 1. 首选引擎
        if self.preferred_engine and self.preferred_engine in self._available_engines:
            return self.preferred_engine

        # 2. 按优先级选择第一个可用的
        for name in self.ENGINE_PRIORITY:
            if name in self._available_engines:
                return name

        # 3. 任意可用引擎
        return self._available_engines[0] if self._available_engines else None

    def get_available_engines(self) -> list:
        """获取所有可用引擎列表"""
        return self._available_engines.copy()


# =============================================================================
# 便捷函数
# =============================================================================

def recognize_captcha(image_path: Union[str, Path], engine: Optional[str] = None) -> Optional[str]:
    """
    快速识别验证码

    Args:
        image_path: 图片路径
        engine: 指定引擎，None 则自动选择

    Returns:
        识别结果

    示例：
        result = recognize_captcha("captcha.png")
        print(result)  # "ABCD"
    """
    ocr = CaptchaOCR(preferred_engine=engine)
    return ocr.recognize(image_path)


def recognize_captcha_bytes(image_bytes: bytes, engine: Optional[str] = None) -> Optional[str]:
    """
    从字节数据识别验证码

    Args:
        image_bytes: 图片二进制数据
        engine: 指定引擎

    Returns:
        识别结果
    """
    with tempfile.NamedTemporaryFile(suffix='.png', delete=False) as f:
        f.write(image_bytes)
        temp_path = f.name

    try:
        result = recognize_captcha(temp_path, engine)
        return result
    finally:
        Path(temp_path).unlink(missing_ok=True)


def get_recommended_engine() -> Optional[str]:
    """获取推荐引擎名称"""
    ocr = CaptchaOCR()
    available = ocr.get_available_engines()
    return available[0] if available else None


# =============================================================================
# 测试
# =============================================================================

if __name__ == "__main__":
    import sys

    # 检测可用引擎
    ocr = CaptchaOCR()
    print(f"可用引擎: {ocr.get_available_engines()}")
    print(f"推荐引擎: {get_recommended_engine()}")

    if len(sys.argv) > 1:
        image_path = sys.argv[1]
        print(f"识别图片: {image_path}")

        # 单引擎
        result = ocr.recognize(image_path)
        print(f"识别结果: {result}")

        # 多引擎投票
        vote_ocr = CaptchaOCR(vote_mode=True)
        vote_result = vote_ocr.recognize(image_path)
        print(f"投票结果: {vote_result}")
