"""
文档解析工具

提供从 URL 下载并解析文档内容的功能，支持 PDF、Word、图片、TXT 等格式。
"""

import base64
import logging
from typing import Optional

import httpx
from langchain_core.tools import tool

from app.agents.tools.testcase.pdf_processor import PDFProcessor
from app.config.settings import settings

logger = logging.getLogger(__name__)

_pdf_processor = PDFProcessor(enable_cache=True)


def _detect_document_format(content_data: bytes, url: str = "") -> tuple[str, str]:
    """
    检测文档实际格式

    Args:
        content_data: 文档内容字节
        url: 原始 URL（用于辅助判断）

    Returns:
        tuple: (format_type, description)
            format_type: "docx" | "doc" | "pdf" | "html" | "empty" | "unknown"
            description: 人类可读的描述
    """
    if len(content_data) == 0:
        return "empty", "空文件"

    # 检查文件头
    header = content_data[:8]

    # ZIP 格式 (docx)
    if header[:4] == b'PK\x03\x04':
        # 进一步检查是否是真正的 docx
        if b'word/document.xml' in content_data[:5000] or b'[Content_Types].xml' in content_data[:5000]:
            return "docx", "有效的 Word 文档 (.docx)"
        return "zip", "ZIP 文件（可能不是 Word 文档）"

    # 旧版 .doc 格式 (OLE2)
    if header[:4] == b'\xd0\xcf\x11\xe0':
        return "doc", "旧版 Word 文档 (.doc)"

    # PDF
    if header[:5] == b'%PDF-':
        return "pdf", "PDF 文档"

    # HTML/XML
    try:
        preview = content_data[:200].decode('utf-8', errors='ignore').strip()
        if preview.startswith('<!') or preview.startswith('<?xml') or preview.lower().startswith('<html'):
            # 检查是否是错误页面
            if any(kw in preview for kw in ['<!DOCTYPE', '<html', 'error', '404', '403', '500']):
                return "html", "HTML 页面（可能是错误页面）"
            return "html", "HTML/XML 内容"
    except Exception:
        pass

    # JSON 错误响应
    try:
        preview = content_data[:200].decode('utf-8', errors='ignore').strip()
        if preview.startswith('{') or preview.startswith('['):
            return "json", "JSON 数据（可能是 API 错误响应）"
    except Exception:
        pass

    # 尝试文本
    try:
        preview = content_data[:100].decode('utf-8')
        if preview.isprintable() or preview.startswith('\n'):
            return "text", "纯文本内容"
    except Exception:
        pass

    return "unknown", f"未知格式，文件头: {header.hex()}"


def _extract_old_doc_text(content_data: bytes) -> str:
    """尝试从旧版 .doc 格式提取文本。使用 textract 或 antiword。"""
    import subprocess
    import tempfile
    import os

    # 尝试使用 textract (最可靠)
    try:
        import textract
        with tempfile.NamedTemporaryFile(suffix='.doc', delete=False) as f:
            f.write(content_data)
            temp_path = f.name
        try:
            text = textract.process(temp_path).decode('utf-8', errors='ignore')
            if text.strip():
                return text
        finally:
            os.unlink(temp_path)
    except ImportError:
        pass
    except Exception as e:
        logger.warning(f"textract 解析失败: {e}")

    # 尝试使用 antiword
    with tempfile.NamedTemporaryFile(suffix='.doc', delete=False) as f:
        f.write(content_data)
        temp_path = f.name

    try:
        result = subprocess.run(
            ['antiword', temp_path],
            capture_output=True,
            text=True,
            timeout=30
        )
        if result.returncode == 0 and result.stdout.strip():
            return result.stdout

        # 尝试 catdoc
        result = subprocess.run(
            ['catdoc', temp_path],
            capture_output=True,
            text=True,
            timeout=30
        )
        if result.returncode == 0 and result.stdout.strip():
            return result.stdout

        raise ValueError(
            "无法解析旧版 .doc 格式。"
            "请使用 Microsoft Word 或 WPS 将文档另存为 .docx 格式后重新上传。"
        )
    finally:
        os.unlink(temp_path)


def _extract_word_text(content_data: bytes) -> str:
    """从 Word 文档 (.docx) 中提取文本内容。"""
    from io import BytesIO
    from docx import Document

    # 先检测实际格式
    fmt, desc = _detect_document_format(content_data)

    if fmt == "docx":
        # 有效的 .docx 格式
        doc = Document(BytesIO(content_data))
        paragraphs = []
        for para in doc.paragraphs:
            if para.text.strip():
                paragraphs.append(para.text.strip())
        return "\n".join(paragraphs)

    elif fmt == "doc":
        # 旧版 .doc 格式
        logger.info("检测到旧版 .doc 格式，尝试使用备用解析器")
        return _extract_old_doc_text(content_data)

    elif fmt == "html":
        # HTML 错误页面
        try:
            preview = content_data[:500].decode('utf-8', errors='ignore').strip()
            raise ValueError(
                f"下载的内容是 HTML 页面而非文档。"
                f"这可能是访问被拒绝或文件不存在。"
                f"预览: {preview[:200]}..."
            )
        except ValueError:
            raise

    elif fmt == "json":
        # JSON 错误响应
        try:
            preview = content_data[:500].decode('utf-8', errors='ignore').strip()
            raise ValueError(
                f"下载的内容是 JSON 数据（可能是 API 错误响应）。"
                f"内容: {preview[:200]}..."
            )
        except ValueError:
            raise

    elif fmt == "empty":
        raise ValueError("下载的文档内容为空")

    else:
        # 未知格式
        raise ValueError(
            f"文件不是有效的 .docx 格式。"
            f"检测到格式: {desc}。"
            f"请确认上传的是 .docx 格式的 Word 文档。"
        )


def _download_with_retry(
    url: str,
    max_retries: int = 2,
    timeout: float = 60.0,
    follow_redirects: bool = True,
) -> httpx.Response:
    """
    下载文档，支持重试和重定向

    Args:
        url: 文档 URL
        max_retries: 最大重试次数
        timeout: 超时时间（秒）
        follow_redirects: 是否跟随重定向

    Returns:
        httpx.Response: 响应对象

    Raises:
        httpx.HTTPError: 下载失败
    """
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
    }

    last_error = None
    for attempt in range(max_retries + 1):
        client = None
        try:
            client = httpx.Client(
                follow_redirects=follow_redirects,
                timeout=httpx.Timeout(timeout),
            )
            response = client.get(url, headers=headers)
            response.raise_for_status()
            return response
        except httpx.HTTPStatusError as e:
            last_error = e
            status_code = e.response.status_code
            if status_code in (301, 302, 307, 308) and not follow_redirects:
                # 需要跟随重定向
                logger.warning(f"下载遇到重定向 (HTTP {status_code})，尝试跟随重定向")
                follow_redirects = True
                continue
            if status_code in (404, 410):
                # 文件不存在，不需要重试
                raise
            if attempt < max_retries:
                logger.warning(f"下载失败 (HTTP {status_code})，第 {attempt + 1} 次重试...")
                continue
            raise
        except httpx.RequestError as e:
            last_error = e
            if attempt < max_retries:
                logger.warning(f"下载请求失败: {e}，第 {attempt + 1} 次重试...")
                continue
            raise
        finally:
            if client is not None:
                client.close()

    if last_error:
        raise last_error


@tool
async def parse_document_from_url(
    url: str,
    document_type: Optional[str] = None,
) -> dict[str, any]:
    """
    从 URL 下载并解析文档内容。

    支持的文档类型:
    - PDF: 使用 PyMuPDF4LLM (支持表格) 或 PyPDF2 (备用)
    - Word (.docx): 使用 python-docx 解析
    - 图片: 返回图片信息，需要配合视觉模型使用
    - TXT: 纯文本解析

    Args:
        url: 文档的 URL (通常是 MinIO 预签名 URL)
        document_type: 文档 MIME 类型 (可选，用于优化解析策略)

    Returns:
        dict: 包含解析结果的字典
            - success: bool, 是否成功
            - content: str, 解析的文本内容
            - document_type: str, 文档类型
            - error: str, 错误信息 (如果失败)
    """
    try:
        logger.info(f"开始解析文档: {url} (类型: {document_type})")

        # 使用增强的下载函数（支持重试和重定向）
        response = await _download_with_retry(url, max_retries=2, timeout=60.0)

        content_data = response.content
        detected_type = document_type or response.headers.get("content-type", "")

        logger.info(f"文档下载完成，大小: {len(content_data)} 字节，类型: {detected_type}")

        # 验证下载内容是否有效
        if len(content_data) == 0:
            return {"success": False, "error": "下载的文档内容为空"}

        # 记录文件头用于调试
        fmt, desc = _detect_document_format(content_data, url)
        logger.info(f"文档格式检测: {fmt} ({desc})")
        logger.debug(f"文件头: {content_data[:8].hex()}")

        if detected_type == "application/pdf" or url.lower().endswith(".pdf"):
            # 验证实际格式
            if fmt not in ("pdf",):
                logger.warning(f"URL 指示为 PDF，但实际格式为 {fmt}")
            text_content = _pdf_processor.extract_text(content_data, filename="document.pdf")
            return {
                "success": True,
                "content": text_content,
                "document_type": "pdf",
                "size_bytes": len(content_data),
            }

        elif (
            detected_type
            in (
                "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                "application/msword",
            )
            or url.lower().endswith(".docx")
            or url.lower().endswith(".doc")
        ):
            # 验证实际格式
            if fmt not in ("docx", "doc"):
                logger.warning(f"URL 指示为 Word，但实际格式为 {fmt} ({desc})")
                # 如果实际格式是 HTML/JSON，可能是访问错误
                if fmt in ("html", "json"):
                    return {
                        "success": False,
                        "error": f"下载的内容不是 Word 文档: {desc}。"
                                f"请检查 URL 是否有效，或文件是否已被删除。",
                        "document_type": fmt,
                    }

            text_content = _extract_word_text(content_data)
            return {
                "success": True,
                "content": text_content,
                "document_type": "word",
                "size_bytes": len(content_data),
            }

        elif detected_type.startswith("image/") or any(
            url.lower().endswith(ext) for ext in [".jpg", ".jpeg", ".png", ".gif", ".webp"]
        ):
            # 图片类型：尝试使用多模态模型分析
            try:
                api_key = settings.image_parser_api_key
                if api_key:
                    from langchain_openai import ChatOpenAI
                    from langchain_core.messages import HumanMessage

                    # 下载图片数据
                    image_data = content_data
                    if not image_data:
                        # 如果之前没有下载内容，重新下载
                        img_response = await _download_with_retry(url, max_retries=1, timeout=30.0)
                        image_data = img_response.content

                    b64_data = base64.b64encode(image_data).decode("utf-8")
                    image_url = f"data:{detected_type or 'image/png'};base64,{b64_data}"

                    model = ChatOpenAI(
                        base_url=settings.image_parser_api_base,
                        api_key=api_key,
                        model=settings.image_parser_model,
                    )

                    prompt = (
                        "请详细描述这张图片的内容。如果是界面截图，请提取所有可见的文字、"
                        "按钮、输入框、列表等元素，并描述页面布局和交互逻辑。"
                    )

                    message = HumanMessage(
                        content=[
                            {"type": "text", "text": prompt},
                            {"type": "image_url", "image_url": {"url": image_url}},
                        ]
                    )

                    response = model.invoke([message])
                    analysis = (
                        response.content
                        if isinstance(response.content, str)
                        else str(response.content)
                    )

                    return {
                        "success": True,
                        "content": f"[图片内容分析]\n\n{analysis}",
                        "document_type": "image",
                        "image_url": url,
                        "size_bytes": len(image_data),
                    }
                else:
                    logger.warning("image_parser_api_key not configured, returning image URL only")
            except Exception as e:
                logger.warning("Image analysis failed, falling back to URL mode: %s", e)

            # 降级：返回图片URL提示
            return {
                "success": True,
                "content": f"这是一张图片文件。\n\n图片URL: {url}\n\n请使用支持视觉的模型来分析这张图片的内容。",
                "document_type": "image",
                "image_url": url,
                "size_bytes": len(content_data),
            }

        elif detected_type == "text/plain" or url.lower().endswith(".txt"):
            try:
                text = content_data.decode('utf-8')
            except UnicodeDecodeError:
                text = content_data.decode('gbk', errors='ignore')

            return {
                "success": True,
                "content": text,
                "document_type": "text",
                "size_bytes": len(content_data),
            }

        else:
            # 尝试根据实际格式处理
            if fmt == "docx":
                text_content = _extract_word_text(content_data)
                return {
                    "success": True,
                    "content": text_content,
                    "document_type": "word",
                    "size_bytes": len(content_data),
                }
            elif fmt == "pdf":
                text_content = _pdf_processor.extract_text(content_data, filename="document.pdf")
                return {
                    "success": True,
                    "content": text_content,
                    "document_type": "pdf",
                    "size_bytes": len(content_data),
                }

            return {
                "success": False,
                "error": f"不支持的文档类型: {detected_type} (实际格式: {fmt})。建议将文档转换为 PDF 或 TXT 格式。",
                "document_type": detected_type,
            }

    except httpx.HTTPError as e:
        logger.error(f"下载文档失败: {e}")
        return {"success": False, "error": f"文档下载失败: {str(e)}"}
    except Exception as e:
        logger.error(f"文档解析失败: {e}", exc_info=True)
        return {"success": False, "error": f"文档解析失败: {str(e)}"}


async def get_rag_tools() -> list:
    """获取 MCP RAG 工具（作为本地 KB 工具的补充/后备）。

    注意：本地 KB 工具（query_knowledge_base_tool / get_knowledge_spaces_tool）
    已由 get_local_tools() 统一加载，此处不再重复加载，避免 tool name 冲突。

    Returns:
        MCP RAG 工具列表（可能为空）
    """
    tools = []

    # 仅加载 MCP RAG 工具（外部 rag-server 提供的补充能力）
    try:
        from langchain_mcp_adapters.client import MultiServerMCPClient

        client = MultiServerMCPClient({
            "rag-server": {
                "url": "http://127.0.0.1:8002/sse",
                "transport": "sse",
            }
        })

        mcp_tools = await client.get_tools()
        tools.extend(mcp_tools)
        logger.info(f"MCP RAG 工具加载成功: {len(mcp_tools)} 个")
    except Exception as e:
        logger.warning(f"MCP RAG 工具加载失败: {e}")

    return tools
