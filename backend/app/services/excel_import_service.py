"""Excel API 文档导入服务。

编排：保存原始附件 -> 解析所有 Sheet -> 归一化为统一接口模型 -> 持久化解析任务。
以及"确认导入"：把（可能被前端编辑过的）统一模型写入 APIEndpoint。
"""

from __future__ import annotations

import uuid
from typing import Any
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.api_doc_parse_task import APIDocParseTask
from app.models.api_endpoint import APIEndpoint
from app.models.attachment import Attachment, AttachmentEntityType
from app.models.folder import Folder
from app.models.folder_type import FolderType
from app.config.minio_client import MinIOClient
from app.services.api_spec_normalizer import APISpecNormalizer
from app.services.excel_api_parser import ExcelAPIParser


class ExcelImportService:
    """Excel API 文档导入服务。"""

    def __init__(self, session: AsyncSession):
        self.session = session
        self.normalizer = APISpecNormalizer()

    async def save_source_attachment(
        self,
        project_id: UUID,
        filename: str,
        content: bytes,
        content_type: str,
        created_by: str | None = None,
    ) -> Attachment:
        """保存原始 Excel 附件到 MinIO + attachments 表。"""
        object_name = f"{project_id}/api_doc/{uuid.uuid4().hex[:8]}_{filename}"
        MinIOClient.upload_bytes(
            object_name=object_name,
            data=content,
            content_type=content_type or "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )
        att = Attachment(
            entity_type=AttachmentEntityType.API_ENDPOINT,  # 复用现有枚举，避免 PG ENUM migration
            entity_id=project_id,
            project_id=project_id,
            file_name=filename,
            file_size=len(content),
            content_type=content_type or "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            object_name=object_name,
            created_by=created_by,
            description="API 文档解析原始附件",
        )
        self.session.add(att)
        await self.session.flush()
        return att

    async def parse_and_create_task(
        self,
        project_id: UUID,
        attachment: Attachment,
        content: bytes,
        created_by: str | None = None,
    ) -> APIDocParseTask:
        """解析 Excel 并创建解析任务（同步执行，状态记录到任务）。"""
        task = APIDocParseTask(
            project_id=project_id,
            attachment_id=attachment.id,
            status="pending",
            doc_type="excel_mx",
            created_by=created_by,
        )
        self.session.add(task)
        await self.session.flush()

        try:
            parser = ExcelAPIParser().load(content)
            guide: dict[str, Any] = {}
            interfaces: list[dict[str, Any]] = []
            for name in parser.sheet_names():
                ws = parser._wb[name]
                if parser.is_guide_sheet(ws):
                    guide = parser.parse_guide(ws)
                else:
                    iface = parser.parse_interface_sheet(ws)
                    spec = self.normalizer.to_unified_spec(
                        iface, guide, attachment_id=str(attachment.id)
                    )
                    interfaces.append(spec)

            if not interfaces:
                raise ValueError("未从 Excel 中识别到任何接口 Sheet")

            # 统一补一次公共规则（防止导读在接口之后）
            self.normalizer.merge_common_rules(interfaces, guide)

            task.result = {"guide": guide, "interfaces": interfaces}
            task.status = "completed"
        except Exception as exc:  # noqa: BLE001
            task.status = "failed"
            task.error = str(exc)[:2000]

        await self.session.flush()
        return task

    async def confirm_import(
        self,
        project_id: UUID,
        task: APIDocParseTask,
        interface_index: int,
        edited_spec: dict[str, Any] | None,
        parent_folder_id: UUID | None,
    ) -> APIEndpoint:
        """确认导入单个接口为 APIEndpoint。

        edited_spec: 前端编辑后的统一模型（可选）；为 None 时用 task.result 中的原始模型。
        """
        result = task.result or {}
        interfaces = result.get("interfaces", [])
        if interface_index < 0 or interface_index >= len(interfaces):
            raise IndexError(f"接口索引越界: {interface_index}")
        spec = edited_spec or interfaces[interface_index]

        # 创建文件夹（按接口名分组）
        api_name = spec.get("name") or f"api_{interface_index}"
        folder = Folder(
            project_id=project_id,
            parent_id=parent_folder_id,
            name=api_name,
            description=spec.get("summary") or "",
            folder_type=FolderType.API_TEST,
        )
        self.session.add(folder)
        await self.session.flush()

        method = spec.get("http_method") or "POST"
        path = spec.get("path") or f"/api/json/{api_name}"
        display_name = f"{method} {path}"

        endpoint = APIEndpoint(
            project_id=project_id,
            folder_id=folder.id,
            display_name=display_name,
            path=path,
            method=method,
            summary=spec.get("summary"),
            description=spec.get("scenario"),
            schema_file_id=task.attachment_id,
            request_body={
                "content_type": "application/json",
                "fields": spec.get("request", {}).get("fields", []),
                "source": spec.get("source"),
            },
            responses={
                "fields": spec.get("response", {}).get("fields", []),
                "error_codes": spec.get("response", {}).get("error_codes", []),
            },
            security=[spec.get("auth")] if spec.get("auth") else [],
            tags=["excel_mx"],
            tag_group=spec.get("method_name") or api_name,
            custom_config={
                "source_type": "excel_mx",
                "source_location": spec.get("source"),
                "business_rules": spec.get("business_rules", []),
                "pending_confirmations": spec.get("pending_confirmations", []),
                "auth": spec.get("auth"),
                "error_codes": spec.get("response", {}).get("error_codes", []),
                "method_name": spec.get("method_name"),
                "api_name": api_name,
                "call_url": spec.get("call_url"),
            },
            sort_order=0,
        )
        self.session.add(endpoint)
        await self.session.flush()
        return endpoint
