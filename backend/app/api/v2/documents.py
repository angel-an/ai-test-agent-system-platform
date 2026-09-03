"""
文档上传 API

提供文档上传到 MinIO 的接口,用于 AI 智能体处理
"""

import io
import mimetypes
from typing import Annotated
from datetime import timedelta
# pylint: disable  MC80OmFIVnBZMlhscm9ua3VMazZjV1YzU0E9PToyODRjNTNmYg==

from fastapi import APIRouter, UploadFile, File, HTTPException, status
from fastapi.responses import StreamingResponse

from app.schemas.common import SuccessResponse
from app.config.minio_client import MinIOClient, MinIOError
from pydantic import BaseModel

router = APIRouter(prefix="/documents", tags=["文档管理"])

files_router = APIRouter(prefix="/files", tags=["文件管理"])


@files_router.get(
    "/{object_path:path}",
    summary="获取 MinIO 对象内容",
    description="按对象路径从 MinIO 流式返回文件内容（用于在浏览器中查看 HTML 报告等）",
)
async def get_file(object_path: str) -> StreamingResponse:
    if not object_path or object_path.startswith("/"):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not Found")

    try:
        content = MinIOClient.download_file(object_path)
    except MinIOError as e:
        if e.code in {"NoSuchKey", "NoSuchObject"}:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not Found")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"读取文件失败: {e}",
        )

    content_type, _ = mimetypes.guess_type(object_path)
    if not content_type:
        content_type = "application/octet-stream"

    return StreamingResponse(
        io.BytesIO(content),
        media_type=content_type,
        headers={"Content-Length": str(len(content))},
    )

# type: ignore  MS80OmFIVnBZMlhscm9ua3VMazZjV1YzU0E9PToyODRjNTNmYg==

class DocumentUploadResponse(BaseModel):
    """文档上传响应"""
    object_name: str
    file_name: str
    file_size: int
    content_type: str
    url: str

@router.post(
    "/upload",
    response_model=SuccessResponse[DocumentUploadResponse],
    status_code=status.HTTP_201_CREATED,
    summary="上传文档到 MinIO",
    description="上传文档文件到 MinIO 存储,返回文件 URL 供 AI 智能体使用",
)
async def upload_document(
    file: UploadFile = File(..., description="要上传的文档文件"),
) -> SuccessResponse[DocumentUploadResponse]:
    """
    上传文档到 MinIO
    
    支持的文件类型:
    - 图片: JPG, PNG, GIF, WebP
    - 文档: PDF, Word, TXT
    
    最大文件大小: 15MB
    """
    # 验证文件类型
    valid_types = [
        "image/jpeg",
        "image/png",
        "image/gif",
        "image/webp",
        "application/pdf",
        "application/msword",
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        "text/plain",
    ]
    
    if file.content_type not in valid_types:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"不支持的文件类型: {file.content_type}",
        )
    
    # 验证文件大小 (15MB)
    content = await file.read()
    if len(content) > 15 * 1024 * 1024:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="文件大小不能超过 15MB",
        )
    
    # 生成对象名
    import uuid
    from datetime import datetime
    
    file_ext = ""
    if file.filename:
        parts = file.filename.rsplit(".", 1)
        if len(parts) > 1:
            file_ext = f".{parts[1]}"
# pragma: no cover  Mi80OmFIVnBZMlhscm9ua3VMazZjV1YzU0E9PToyODRjNTNmYg==
    
    timestamp = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
    unique_id = str(uuid.uuid4())[:8]
    object_name = f"documents/{timestamp}_{unique_id}{file_ext}"
    
    # 上传到 MinIO
    try:
        MinIOClient.upload_bytes(
            object_name=object_name,
            data=content,
            content_type=file.content_type or "application/octet-stream",
        )
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"文件上传失败: {str(e)}",
        )
# fmt: off  My80OmFIVnBZMlhscm9ua3VMazZjV1YzU0E9PToyODRjNTNmYg==
    
    # 生成预签名 URL (7天有效)
    try:
        url = MinIOClient.get_presigned_url(
            object_name=object_name,
        )
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"生成下载链接失败: {str(e)}",
        )
    
    return SuccessResponse(
        success=True,
        data=DocumentUploadResponse(
            object_name=object_name,
            file_name=file.filename or "unnamed",
            file_size=len(content),
            content_type=file.content_type or "application/octet-stream",
            url=url,
        ),
    )

