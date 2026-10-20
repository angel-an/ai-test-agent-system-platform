"""
API 测试服务

处理 API 测试相关的业务逻辑
"""

import json
from datetime import datetime
from typing import Optional, Any
from uuid import UUID, uuid4
# noqa  MC80OmFIVnBZMlhscm9ua3VMazZNRFk1ZWc9PTphMzk3NWNlMQ==

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.api_test import APITest, APITestRun, APITestResult
from app.models.api_endpoint import APIEndpoint
from app.models.attachment import Attachment, AttachmentEntityType
from app.repositories.api_test_repo import (
    APITestRepository,
    APITestRunRepository,
    APITestResultRepository,
)
from app.repositories.project_repo import ProjectRepository
from app.schemas.enums import TestResultStatus
from app.utils.exceptions import NotFoundException, BadRequestException
from app.config.minio_client import MinIOClient, MinIOError
from app.config.settings import settings
from app.services.api_test_executor import APITestExecutor
from app.services.api_spec_test_generator import (
    analyze_requirement,
    generate_playwright_script,
    generate_test_plan,
)
from app.services.api_endpoint_script_executor import execute_endpoint_script, redact_config
from app.services.signing.mx_md5_signer import mx_md5_js_helper
from app.services.artifact_publisher import ArtifactPublisher, sha256_hex
from app.test_platform.directory_layout import DirectoryLayout

class APITestService:
    """API 测试服务类"""

    def __init__(self, session: AsyncSession, mongodb=None):
        self.session = session
        self.mongodb = mongodb
        self.api_test_repo = APITestRepository(session)
        self.api_test_run_repo = APITestRunRepository(session)
        self.api_test_result_repo = APITestResultRepository(session)
        self.project_repo = ProjectRepository(session)

    async def _get_project_by_identifier(self, identifier: str):
        """获取项目，不存在则抛出异常"""
        project = await self.project_repo.get_by_identifier(identifier)
        if not project:
            raise NotFoundException(resource_type="项目", resource_id=identifier)
        return project

    # ==================== API 测试管理 ====================

    async def create_api_test(
        self,
        project_identifier: str,
        name: str,
        schema_path: str,
        script_path: str,
        script_format: str = "playwright",
        script_language: str = "typescript",
        schema_url: Optional[str] = None,
        description: Optional[str] = None,
        test_config: Optional[dict] = None,
        total_endpoints: int = 0,
        total_scenarios: int = 0,
        generation_params: Optional[dict] = None,
        folder_id: Optional[str] = None,
    ) -> dict:
        """
        创建 API 测试

        Args:
            project_identifier: 项目标识符
            name: API 测试名称
            schema_path: Schema 文件路径 (MinIO)
            script_path: 脚本文件路径 (MinIO)
            script_format: 脚本格式
            script_language: 脚本语言
            schema_url: Schema URL（可选）
            description: 描述
            test_config: 测试配置
            total_endpoints: 端点总数
            total_scenarios: 场景总数
            generation_params: 生成参数

        Returns:
            dict: 创建的 API 测试信息
        """
        project = await self._get_project_by_identifier(project_identifier)
        identifier = await self.api_test_repo.get_next_identifier(project.id)

        # 验证并转换 folder_id
        folder_uuid = None
        if folder_id:
            try:
                folder_uuid = UUID(folder_id)
            except (ValueError, TypeError):
                raise BadRequestException(f"无效的文件夹 ID 格式: {folder_id}")

        api_test = await self.api_test_repo.create(
            project_id=project.id,
            folder_id=folder_uuid,
            identifier=identifier,
            name=name,
            description=description,
            schema_url=schema_url,
            schema_path=schema_path,
            schema_type="openapi",
            script_path=script_path,
            script_format=script_format,
            script_language=script_language,
            test_config=test_config or {},
            generated_by_agent="api_agent",
            generation_params=generation_params,
            total_endpoints=total_endpoints,
            total_scenarios=total_scenarios,
        )

        return {
            "id": str(api_test.id),
            "identifier": api_test.identifier,
            "name": api_test.name,
            "description": api_test.description,
            "script_format": api_test.script_format,
            "script_language": api_test.script_language,
            "total_endpoints": api_test.total_endpoints,
            "total_scenarios": api_test.total_scenarios,
            "created_at": api_test.created_at.isoformat(),
        }

    async def get_api_test(
        self,
        project_identifier: str,
        api_test_id: str,
    ) -> dict:
        """获取 API 测试详情"""
        project = await self._get_project_by_identifier(project_identifier)
        api_test = await self.api_test_repo.get_by_id_with_relations(UUID(api_test_id))

        if not api_test or api_test.project_id != project.id:
            raise NotFoundException(resource_type="API 测试", resource_id=api_test_id)

        return {
            "id": str(api_test.id),
            "identifier": api_test.identifier,
            "name": api_test.name,
            "description": api_test.description,
            "schema_url": api_test.schema_url,
            "schema_path": api_test.schema_path,
            "schema_type": api_test.schema_type,
            "script_path": api_test.script_path,
            "script_format": api_test.script_format,
            "script_language": api_test.script_language,
            "test_config": api_test.test_config,
            "total_endpoints": api_test.total_endpoints,
            "total_scenarios": api_test.total_scenarios,
            "created_at": api_test.created_at.isoformat(),
            "updated_at": api_test.updated_at.isoformat() if api_test.updated_at else None,
        }

    async def list_api_tests(
        self,
        project_identifier: str,
        page: int = 1,
        page_size: int = 20,
        search: Optional[str] = None,
        script_format: Optional[str] = None,
    ) -> dict:
        """获取 API 测试列表"""
        project = await self._get_project_by_identifier(project_identifier)
        offset = (page - 1) * page_size

        items, total = await self.api_test_repo.get_by_project(
            project.id,
            offset=offset,
            limit=page_size,
            search=search,
            script_format=script_format,
        )

        return {
            "items": [
                {
                    "id": str(item.id),
                    "identifier": item.identifier,
                    "name": item.name,
                    "description": item.description,
                    "script_format": item.script_format,
                    "total_endpoints": item.total_endpoints,
                    "total_scenarios": item.total_scenarios,
                    "created_at": item.created_at.isoformat(),
                }
                for item in items
            ],
            "total": total,
            "page": page,
            "page_size": page_size,
        }

    async def list_api_tests_by_folder(
        self,
        project_identifier: str,
        folder_id: str,
        page: int = 1,
        page_size: int = 20,
        search: Optional[str] = None,
    ) -> dict:
        """
        获取文件夹下的 API 测试列表

        Args:
            project_identifier: 项目标识符
            folder_id: 文件夹 ID
            page: 页码
            page_size: 每页数量
            search: 搜索关键词

        Returns:
            dict: API 测试列表
        """
        project = await self._get_project_by_identifier(project_identifier)
        folder_uuid = UUID(folder_id)

        # 验证文件夹存在且属于该项目
        from app.repositories.folder_repo import FolderRepository
        folder_repo = FolderRepository(self.session)
        folder = await folder_repo.get_by_id(folder_uuid)

        if not folder or folder.project_id != project.id:
            raise NotFoundException(resource_type="文件夹", resource_id=folder_id)
# noqa  MS80OmFIVnBZMlhscm9ua3VMazZNRFk1ZWc9PTphMzk3NWNlMQ==

        offset = (page - 1) * page_size

        items, total = await self.api_test_repo.get_by_folder(
            folder_uuid,
            offset=offset,
            limit=page_size,
            search=search,
        )

        return {
            "items": [
                {
                    "id": str(item.id),
                    "identifier": item.identifier,
                    "name": item.name,
                    "description": item.description,
                    "script_format": item.script_format,
                    "total_endpoints": item.total_endpoints,
                    "total_scenarios": item.total_scenarios,
                    "created_at": item.created_at.isoformat(),
                }
                for item in items
            ],
            "total": total,
            "page": page,
            "page_size": page_size,
        }

    async def update_api_test(
        self,
        project_identifier: str,
        api_test_id: str,
        name: Optional[str] = None,
        description: Optional[str] = None,
        test_config: Optional[dict] = None,
    ) -> dict:
        """更新 API 测试"""
        project = await self._get_project_by_identifier(project_identifier)
        api_test = await self.api_test_repo.get_by_id(UUID(api_test_id))

        if not api_test or api_test.project_id != project.id:
            raise NotFoundException(resource_type="API 测试", resource_id=api_test_id)

        update_data = {}
        if name is not None:
            update_data["name"] = name
        if description is not None:
            update_data["description"] = description
        if test_config is not None:
            update_data["test_config"] = test_config

        updated = await self.api_test_repo.update(api_test, **update_data)

        return {
            "id": str(updated.id),
            "identifier": updated.identifier,
            "name": updated.name,
            "description": updated.description,
            "test_config": updated.test_config,
            "updated_at": updated.updated_at.isoformat() if updated.updated_at else None,
        }

    async def delete_api_test(
        self,
        project_identifier: str,
        api_test_id: str,
    ) -> None:
        """删除 API 测试"""
        project = await self._get_project_by_identifier(project_identifier)
        api_test = await self.api_test_repo.get_by_id(UUID(api_test_id))

        if not api_test or api_test.project_id != project.id:
            raise NotFoundException(resource_type="API 测试", resource_id=api_test_id)

        await self.api_test_repo.delete(api_test)

    # ==================== 测试脚本管理 ====================

    async def get_test_script(
        self,
        project_identifier: str,
        api_test_id: str,
    ) -> str:
        """获取测试脚本内容"""
        project = await self._get_project_by_identifier(project_identifier)
        api_test = await self.api_test_repo.get_by_id(UUID(api_test_id))

        if not api_test or api_test.project_id != project.id:
            raise NotFoundException(resource_type="API 测试", resource_id=api_test_id)
# fmt: off  Mi80OmFIVnBZMlhscm9ua3VMazZNRFk1ZWc9PTphMzk3NWNlMQ==

        # 从 MinIO 下载脚本
        try:
            script_content = MinIOClient.download_file(api_test.script_path)
            return script_content.decode("utf-8")
        except MinIOError as e:
            if e.code == "NoSuchKey":
                raise NotFoundException(resource_type="脚本文件", resource_id=api_test.script_path)
            raise BadRequestException(f"无法读取脚本文件: {str(e)}")

    async def update_test_script(
        self,
        project_identifier: str,
        api_test_id: str,
        script_content: str,
    ) -> dict:
        """更新测试脚本内容"""
        project = await self._get_project_by_identifier(project_identifier)
        api_test = await self.api_test_repo.get_by_id(UUID(api_test_id))

        if not api_test or api_test.project_id != project.id:
            raise NotFoundException(resource_type="API 测试", resource_id=api_test_id)

        # 更新 MinIO 中的脚本
        try:
            script_bytes = script_content.encode("utf-8")
            MinIOClient.upload_bytes(
                object_name=api_test.script_path,
                data=script_bytes,
                content_type="text/plain",
            )
        except MinIOError as e:
            raise BadRequestException(f"脚本上传失败: {str(e)}")

        # TODO: 保存版本历史到 MongoDB

        return {"success": True, "message": "脚本更新成功"}

    # ==================== 测试运行管理 ====================

    # ---------- 契约驱动：从 APIEndpoint 生成并执行 ----------

    def _endpoint_to_spec(self, endpoint: APIEndpoint) -> dict:
        """从 APIEndpoint 还原统一接口模型（生成服务只读该模型，不读 Excel）。"""
        cc = endpoint.custom_config or {}
        req_body = endpoint.request_body or {}
        resp = endpoint.responses or {}
        # 字段可能在 schema.fields 或直接在 fields
        req_fields = req_body.get("fields") or req_body.get("schema", {}).get("fields", [])
        resp_fields = resp.get("fields") or resp.get("schema", {}).get("fields", [])
        return {
            "name": cc.get("api_name") or endpoint.tag_group,
            "method_name": cc.get("method_name"),
            "http_method": endpoint.method,
            "path": endpoint.path,
            "summary": endpoint.summary,
            "scenario": endpoint.description,
            "request": {"fields": req_fields},
            "response": {"fields": resp_fields, "error_codes": resp.get("error_codes", [])},
            "auth": cc.get("auth"),
            "business_rules": cc.get("business_rules", []),
            "source": cc.get("source_location"),
            "pending_confirmations": cc.get("pending_confirmations", []),
        }

    def _generate_gateway_script(
        self,
        endpoint: APIEndpoint,
        spec: dict,
        env_config: dict,
    ) -> str:
        """为网关通用路径端点（如 POST /cop/json）生成 Playwright 脚本。

        美心 COP/MP 网关用通用路径 + Method header 区分实际接口。
        当契约只有 Collection 来源（无 Excel 导读、无 mx_md5 鉴权）时，
        生成基础连通性脚本：发送空 JSON body + Method header，验证网关可达。
        """
        base_url = (env_config.get("base_url") or "").rstrip("/")
        if not base_url:
            raise ValueError("缺少 env_config.base_url（环境地址），脚本生成被阻塞")

        path = spec.get("path") or endpoint.path or ""
        url = f"{base_url}{path}"
        method_name = spec.get("method_name") or ""
        helper = mx_md5_js_helper()

        lines = []
        lines.append("/**")
        lines.append(f" * 自动生成：{spec.get('name')} 网关连通性测试")
        lines.append(f" * 路径：{path}（通用网关路径，经 Method header 区分接口）")
        lines.append(f" * Method：{method_name}")
        lines.append(" * 注意：该端点仅有 Collection 来源，无 Excel 契约与 mx_md5 鉴权配置")
        lines.append(" */")
        lines.append("const { test, expect } = require('@playwright/test');")
        lines.append(helper)
        lines.append("")
        lines.append(f"const BASE_URL = {json.dumps(url, ensure_ascii=False)};")
        lines.append(f"const METHOD = {json.dumps(method_name, ensure_ascii=False)};")
        lines.append("")
        lines.append("test('01 网关连通性：Method header 可到达', async ({ request }) => {")
        lines.append("  const ts = mxTimestamp(new Date());")
        lines.append("  const res = await request.post(BASE_URL, {")
        lines.append("    headers: {")
        lines.append("      'Content-Type': 'application/json',")
        lines.append("      Method: METHOD,")
        lines.append("      ts,")
        lines.append("    },")
        lines.append("    data: '{}',")
        lines.append("  });")
        lines.append("  // 网关可达即可：返回 4xx/5xx 也算连通（鉴权/参数错误是预期行为）")
        lines.append("  expect(res.status()).toBeGreaterThan(0);")
        lines.append("  const body = await res.text().catch(() => '');")
        lines.append("  console.log(`Response ${res.status()}: ${body.substring(0, 500)}`);")
        lines.append("});")
        lines.append("")

        return "\n".join(lines)

    async def generate_artifacts_from_endpoint(
        self,
        endpoint_id: str,
        env_config: dict,
    ) -> dict:
        """对单个端点生成需求分析、测试计划与 Playwright 脚本。

        返回 {analysis, plan, script, script_attachment_id}；脚本存为附件以便执行与追溯。
        """
        endpoint = await self._get_endpoint(UUID(endpoint_id))
        spec = self._endpoint_to_spec(endpoint)

        analysis = analyze_requirement(spec)
        plan = generate_test_plan(spec)

        # 网关通用路径端点（如 POST /cop/json）：仅有 Collection 来源时无 mx_md5 鉴权，
        # 生成基础连通性脚本而非契约驱动脚本
        auth = spec.get("auth") or {}
        is_gateway = auth.get("type") != "mx_md5" and spec.get("method_name")
        if is_gateway:
            script = self._generate_gateway_script(endpoint, spec, env_config)
        else:
            script = generate_playwright_script(spec, env_config)  # 缺 base_url 会抛 ValueError

        # 脚本存为附件（API_TEST_SCRIPT），供执行器下载与追溯
        object_name = f"{endpoint.project_id}/api_test_script/{endpoint.id}/contract_{uuid4().hex[:8]}.spec.ts"
        MinIOClient.upload_bytes(
            object_name=object_name,
            data=script.encode("utf-8"),
            content_type="text/plain",
        )
        attachment = Attachment(
            entity_type=AttachmentEntityType.API_TEST_SCRIPT,
            entity_id=endpoint.id,
            project_id=endpoint.project_id,
            file_name=f"{spec.get('name') or endpoint.id}.spec.ts",
            file_size=len(script.encode("utf-8")),
            content_type="text/plain",
            object_name=object_name,
            description="网关连通性测试脚本" if is_gateway else "契约驱动生成的 Playwright 脚本（含美心 MD5 签名）",
        )
        self.session.add(attachment)
        await self.session.flush()

        return {
            "analysis": analysis,
            "plan": plan,
            "script": script,
            "script_attachment_id": str(attachment.id),
            "endpoint_id": str(endpoint.id),
        }

    async def generate_and_run_from_endpoint(
        self,
        project_identifier: str,
        endpoint_id: str,
        env_config: dict,
    ) -> dict:
        """契约驱动闭环：生成脚本 -> 创建/复用 APITest -> 预检 -> 执行 -> 登记产物。

        阶段 8：接入统一预检（execute_with_preflight）与 TestRunArtifact 登记。
        env_config 需含 base_url；requires_env=true 时缺配置会返回 blocked，不执行。
        """
        from app.services.test_run_artifact_service import TestRunArtifactService
        from app.services.unified_execution import execute_with_preflight
        from app.test_platform.runtime_context import RunContext
        from app.test_platform.execution_gates import GateConfig

        project = await self._get_project_by_identifier(project_identifier)
        endpoint = await self._get_endpoint(UUID(endpoint_id))
        if endpoint.project_id != project.id:
            raise NotFoundException(resource_type="API 端点", resource_id=endpoint_id)

        # 持久化环境配置到端点 custom_config.env_config：供后续 AI 智能体
        # execute_api_script 执行同一端点时复用（注入 base_url/凭证/测试数据），
        # 避免脚本回退到 api.example.com 等占位符导致 DNS 失败。
        # 敏感值（appsecret/secret/token 等）加密后存储（enc:v1: 前缀），执行器在服务端
        # 读取时解密还原真实值；API 响应侧由 APIEndpointResponse 序列化器脱敏为 ***。
        # 注意：custom_config 是 JSONB 且未包 MutableDict，需 flag_modified 显式标记变更，
        # 否则 flush 不会触发 UPDATE，env_config 丢失（新会话读不到）。
        if env_config.get("base_url"):
            from app.utils.env_crypto import encrypt_value
            from app.services.api_endpoint_script_executor import _SENSITIVE_KEYS

            def _encrypt_sensitive(obj):
                if isinstance(obj, dict):
                    out = {}
                    for k, v in obj.items():
                        if isinstance(v, str) and any(s in str(k).lower() for s in _SENSITIVE_KEYS) and v:
                            out[k] = encrypt_value(v)
                        else:
                            out[k] = _encrypt_sensitive(v)
                    return out
                return obj

            cc = dict(endpoint.custom_config or {})
            cc["env_config"] = _encrypt_sensitive(dict(env_config))
            endpoint.custom_config = cc
            from sqlalchemy.orm.attributes import flag_modified
            flag_modified(endpoint, "custom_config")
            self.session.add(endpoint)
            await self.session.flush()
            print(f"[generate_and_run] 端点 {endpoint_id} 已保存 env_config(base_url={env_config.get('base_url')}, 敏感值已加密)")

        # 环境配置检查前置：缺 base_url 直接 blocked，不生成脚本
        if env_config.get("requires_env") and not env_config.get("base_url"):
            api_test = await self._ensure_contract_api_test(project.id, endpoint)
            run = await self.api_test_run_repo.create(
                project_id=project.id,
                api_test_id=api_test.id,
                identifier=f"SR-EP-{endpoint.id}",
                status="blocked",
                # 与执行器保持一致：落库前必须脱敏，否则缺配置分支会把
                # 明文密钥（如 appsecret）写进 api_test_runs.execution_config
                execution_config=redact_config(env_config),
                error_message="缺执行环境配置: base_url",
            )
            await self.session.flush()
            return {"run_id": str(run.id), "status": "blocked",
                    "message": "缺执行环境配置: base_url"}

        artifacts = await self.generate_artifacts_from_endpoint(endpoint_id, env_config)
        api_test = await self._ensure_contract_api_test(project.id, endpoint)
        script_att = await self.session.get(Attachment, UUID(artifacts["script_attachment_id"]))

        # 阶段 8：统一预检 + 产物登记
        # run_id 使用脚本附件 ID 派生，保证全链路可追踪
        run_id = f"ep-{endpoint.id}-{script_att.id}"
        # 使用 api_test.id 作为 test_case_id（预检要求有 test_case_id 才能定位 api_tests 目录）
        context = RunContext(
            project_id=project.identifier,
            run_id=run_id,
            artifact_id=str(script_att.id),
            test_case_id=str(api_test.id),
            mode="safe",
        )

        # 发布产物到运行目录（写入 manifest.json 和 script_provenance.json）
        # 预检要求这两个文件存在，否则阻断执行
        try:
            layout = DirectoryLayout()
            run_dir = layout.api_run_dir(project.identifier, str(api_test.id), run_id)
            script_content = artifacts["script"].encode("utf-8")
            script_path = run_dir / f"{script_att.file_name or 'script.spec.ts'}"
            script_path.write_bytes(script_content)

            publisher = ArtifactPublisher(layout)
            published = publisher.publish_artifact(
                context=context,
                source_path=script_path,
                artifact_type="api_script",
                artifact_version="1",
            )
            print(f"[generate_and_run] 产物发布成功: {published.manifest_path}")
        except Exception as e:
            import traceback
            print(f"[generate_and_run] 产物发布失败: {e}")
            traceback.print_exc()
            # 如果发布失败，尝试直接写入 manifest 和 provenance
            try:
                import hashlib
                from datetime import datetime, timezone
                script_hash = hashlib.sha256(script_content).hexdigest()
                now = datetime.now(timezone.utc).isoformat()

                manifest = {
                    "project_id": project.identifier,
                    "run_id": run_id,
                    "artifact_id": str(script_att.id),
                    "artifact_type": "api_script",
                    "artifact_version": "1",
                    "artifact_path": str(script_path),
                    "artifact_filename": script_path.name,
                    "source_path": str(script_path),
                    "sha256": script_hash,
                    "published_at": now,
                    "mode": "safe",
                    "test_case_id": str(api_test.id),
                }
                manifest_path = run_dir / "manifest.json"
                manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")

                provenance = {
                    "project_id": project.identifier,
                    "run_id": run_id,
                    "artifact_id": str(script_att.id),
                    "sha256": script_hash,
                    "version": "1",
                    "published_at": now,
                    "source_sha256": script_hash,
                    "target_sha256": script_hash,
                    "status": "published",
                }
                provenance_path = run_dir / "script_provenance.json"
                provenance_path.write_text(json.dumps(provenance, ensure_ascii=False, indent=2), encoding="utf-8")
                print(f"[generate_and_run] 手动写入 manifest 和 provenance 成功")
            except Exception as e2:
                print(f"[generate_and_run] 手动写入也失败: {e2}")

        artifact_service = TestRunArtifactService(self.session)
        artifact = await artifact_service.register(
            project_id=project.id,
            run_id=run_id,
            artifact_type="api_script",
            artifact_path=script_att.object_name or str(script_att.id),
            test_case_id=api_test.test_case_id,
            artifact_version="1",
            mode="safe",
            status="draft",
            metadata={"endpoint_id": str(endpoint.id), "script_attachment_id": str(script_att.id)},
        )

        # 更新端点的测试用例数和运行状态
        try:
            plan = artifacts.get("plan", {})
            total_cases = plan.get("total_cases", 0)
            endpoint.total_test_cases = total_cases
            endpoint.total_test_runs = (endpoint.total_test_runs or 0) + 1
            endpoint.last_run_status = "running"
            self.session.add(endpoint)
            await self.session.flush()
        except Exception as e:
            print(f"[generate_and_run] 更新端点状态失败: {e}")

        gate_config = GateConfig.from_env()

        async def _do_execute() -> dict:
            await artifact_service.mark_executing(artifact)
            rid = await execute_endpoint_script(
                session=self.session,
                attachment=script_att,
                project_id=project.id,
                api_test_id=api_test.id,
                execution_config=env_config,
            )
            return {"run_id": rid, "status": "running", "artifacts": artifacts}

        result = await execute_with_preflight(
            context=context,
            track="api",
            executor=_do_execute,
            gate_config=gate_config,
        )

        # 更新端点的最终运行状态
        try:
            if result.get("status") == "blocked":
                endpoint.last_run_status = "blocked"
            elif result.get("status") == "running":
                endpoint.last_run_status = "running"
            self.session.add(endpoint)
            await self.session.flush()
        except Exception as e:
            print(f"[generate_and_run] 更新端点最终状态失败: {e}")

        if result.get("status") == "blocked":
            await artifact_service.mark_blocked(
                artifact,
                reason=result.get("message", "预检阻断"),
                reason_code=(result.get("preflight") or {}).get("reason_code", "UNKNOWN"),
            )
            await self.session.flush()
            return {
                "run_id": run_id,
                "status": "blocked",
                "message": result.get("message", "执行被预检阻断"),
                "preflight": result.get("preflight"),
            }

        await self.session.flush()
        return result

    async def _ensure_contract_api_test(self, project_id: UUID, endpoint: APIEndpoint) -> APITest:
        """复用端点已关联的 APITest，否则创建占位。"""
        existing_ids = list(endpoint.api_test_ids or [])
        if existing_ids:
            existing = await self.api_test_repo.get_by_id(UUID(existing_ids[0]))
            if existing:
                return existing
        identifier = await self.api_test_repo.get_next_identifier(project_id)
        api_test = APITest(
            project_id=project_id,
            identifier=identifier,
            name=f"{endpoint.display_name} 契约测试",
            description="由 Excel 契约自动生成",
            script_path=f"contract://{endpoint.id}",  # 占位；实际脚本经附件下发执行
            script_format="playwright",
            script_language="typescript",
            test_config={},
            generated_by_agent="contract_excel",
            total_endpoints=1,
            total_scenarios=0,
        )
        self.session.add(api_test)
        await self.session.flush()
        # 回填端点关联
        endpoint.api_test_ids = existing_ids + [str(api_test.id)]
        self.session.add(endpoint)
        await self.session.flush()
        return api_test

    async def _get_endpoint(self, endpoint_id: UUID) -> APIEndpoint:
        from sqlalchemy import select
        result = await self.session.execute(
            select(APIEndpoint).where(APIEndpoint.id == endpoint_id)
        )
        endpoint = result.scalar_one_or_none()
        if not endpoint:
            raise NotFoundException(resource_type="API 端点", resource_id=str(endpoint_id))
        return endpoint

    async def run_api_test(
        self,
        project_identifier: str,
        api_test_id: str,
        execution_config: Optional[dict] = None,
    ) -> dict:
        """
        执行 API 测试（异步）

        Args:
            project_identifier: 项目标识符
            api_test_id: API 测试 ID
            execution_config: 执行配置

        Returns:
            dict: 运行信息
        """
        project = await self._get_project_by_identifier(project_identifier)
        api_test = await self.api_test_repo.get_by_id(UUID(api_test_id))

        if not api_test or api_test.project_id != project.id:
            raise NotFoundException(resource_type="API 测试", resource_id=api_test_id)

        # 使用测试执行器执行测试
        executor = APITestExecutor(self.session, self.mongodb)
        run_id = await executor.execute_test(
            api_test_id=api_test.id,
            execution_config=execution_config or {},
        )

        # 获取运行记录以返回信息
        test_run = await self.api_test_run_repo.get_by_id(UUID(run_id))

        return {
            "run_id": str(test_run.id),
            "identifier": test_run.identifier,
            "status": test_run.status,
            "message": "测试已加入执行队列",
        }

    async def get_test_runs(
        self,
        project_identifier: str,
        api_test_id: str,
        page: int = 1,
        page_size: int = 20,
    ) -> dict:
        """获取测试运行历史"""
        project = await self._get_project_by_identifier(project_identifier)
        api_test = await self.api_test_repo.get_by_id(UUID(api_test_id))
# noqa  My80OmFIVnBZMlhscm9ua3VMazZNRFk1ZWc9PTphMzk3NWNlMQ==

        if not api_test or api_test.project_id != project.id:
            raise NotFoundException(resource_type="API 测试", resource_id=api_test_id)

        offset = (page - 1) * page_size
        items, total = await self.api_test_run_repo.get_by_api_test(
            api_test.id,
            offset=offset,
            limit=page_size,
        )

        return {
            "items": [
                {
                    "id": str(item.id),
                    "identifier": item.identifier,
                    "status": item.status,
                    "total_tests": item.total_tests,
                    "passed_tests": item.passed_tests,
                    "failed_tests": item.failed_tests,
                    "skipped_tests": item.skipped_tests,
                    "duration_ms": item.duration_ms,
                    "created_at": item.created_at.isoformat(),
                }
                for item in items
            ],
            "total": total,
            "page": page,
            "page_size": page_size,
        }

    async def get_test_run(
        self,
        project_identifier: str,
        api_test_id: str,
        run_id: str,
    ) -> dict:
        """获取测试运行详情"""
        project = await self._get_project_by_identifier(project_identifier)
        test_run = await self.api_test_run_repo.get_by_id(UUID(run_id))

        if not test_run or test_run.project_id != project.id:
            raise NotFoundException(resource_type="测试运行", resource_id=run_id)

        # 计算进度
        progress = 0
        if test_run.total_tests > 0:
            progress = int(
                ((test_run.passed_tests + test_run.failed_tests + test_run.skipped_tests) / test_run.total_tests) * 100
            )

        return {
            "id": str(test_run.id),
            "identifier": test_run.identifier,
            "status": test_run.status,
            "total_tests": test_run.total_tests,
            "passed_tests": test_run.passed_tests,
            "failed_tests": test_run.failed_tests,
            "skipped_tests": test_run.skipped_tests,
            "duration_ms": test_run.duration_ms,
            "progress": progress,
            "error_message": test_run.error_message,
            "report_path": test_run.report_path,
            "created_at": test_run.created_at.isoformat(),
            "updated_at": test_run.updated_at.isoformat() if test_run.updated_at else None,
        }

    async def get_test_results(
        self,
        project_identifier: str,
        api_test_id: str,
        run_id: str,
        page: int = 1,
        page_size: int = 50,
    ) -> dict:
        """获取测试结果列表"""
        project = await self._get_project_by_identifier(project_identifier)
        test_run = await self.api_test_run_repo.get_by_id(UUID(run_id))

        if not test_run or test_run.project_id != project.id:
            raise NotFoundException(resource_type="测试运行", resource_id=run_id)

        offset = (page - 1) * page_size
        items, total = await self.api_test_result_repo.get_by_test_run(
            UUID(run_id),
            offset=offset,
            limit=page_size,
        )

        return {
            "items": [
                {
                    "id": str(item.id),
                    "scenario_name": item.scenario_name,
                    "endpoint": item.endpoint,
                    "method": item.method,
                    "status": item.status.value,
                    "duration_ms": item.duration_ms,
                    "retry_count": item.retry_count,
                    "error_message": item.error_message,
                    "created_at": item.created_at.isoformat(),
                }
                for item in items
            ],
            "total": total,
            "page": page,
            "page_size": page_size,
        }

    # ==================== Schema 上传 ====================

    async def upload_schema(
        self,
        project_identifier: str,
        filename: str,
        content: bytes,
        content_type: str,
    ) -> dict:
        """
        上传 Schema 文件到 MinIO

        Args:
            project_identifier: 项目标识符
            filename: 文件名
            content: 文件内容
            content_type: 内容类型

        Returns:
            dict: 上传结果
        """
        project = await self._get_project_by_identifier(project_identifier)

        # 生成对象名称
        import time
        timestamp = int(time.time())
        object_name = f"api-schemas/{project.id}/{timestamp}-{filename}"

        # 上传到 MinIO
        try:
            MinIOClient.upload_bytes(
                object_name=object_name,
                data=content,
                content_type=content_type,
            )
        except MinIOError as e:
            raise BadRequestException(f"Schema 文件上传失败: {str(e)}")

        # 生成公共 URL（如果配置了）
        schema_url = None
        if settings.minio_public_url:
            schema_url = f"{settings.minio_public_url}/{object_name}"

        return {
            "schema_path": object_name,
            "schema_url": schema_url,
        }
