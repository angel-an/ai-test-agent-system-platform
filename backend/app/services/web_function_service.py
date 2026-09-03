"""
Web 功能服务

处理 Web 功能和子功能相关的业务逻辑
"""

from typing import Optional
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.web_function import WebFunction, WebSubFunction
from app.repositories.web_function_repo import (
    WebFunctionRepository,
    WebSubFunctionRepository,
)
from app.repositories.project_repo import ProjectRepository
from app.utils.exceptions import NotFoundException

class WebFunctionService:
    """Web 功能服务类"""

    def __init__(self, session: AsyncSession):
        self.session = session
        self.web_function_repo = WebFunctionRepository(session)
        self.web_sub_function_repo = WebSubFunctionRepository(session)
        self.project_repo = ProjectRepository(session)
# fmt: off  MC80OmFIVnBZMlhscm9ua3VMazZjVTlhWnc9PTpkMmNiNWU3Yw==

    async def _get_project_by_identifier(self, identifier: str):
        """获取项目，不存在则抛出异常"""
        project = await self.project_repo.get_by_identifier(identifier)
        if not project:
            raise NotFoundException(resource_type="项目", resource_id=identifier)
        return project

    # ==================== Web 功能管理 ====================

    async def create_web_function(
        self,
        project_identifier: str,
        display_name: str,
        name: str,
        folder_id: Optional[str] = None,
        description: Optional[str] = None,
        base_url: Optional[str] = None,
        business_module: Optional[str] = None,
        navigation: Optional[dict] = None,
        pages: Optional[list] = None,
        tags: Optional[list] = None,
        custom_config: Optional[dict] = None,
        sort_order: int = 0,
    ) -> dict:
        """
        创建 Web 功能

        Args:
            project_identifier: 项目标识符
            display_name: 功能显示名称
            name: 功能名称（英文）
            folder_id: 所属文件夹 ID
            description: 功能描述
            base_url: 功能基础 URL
            business_module: 业务模块标识
            navigation: 导航配置
            pages: 包含的页面列表
            tags: 功能标签
            custom_config: 自定义配置
            sort_order: 排序顺序

        Returns:
            dict: 创建的 Web 功能信息
        """
        project = await self._get_project_by_identifier(project_identifier)
        identifier = await self.web_function_repo.get_next_identifier(project.id)

        web_function = await self.web_function_repo.create(
            project_id=project.id,
            identifier=identifier,
            display_name=display_name,
            name=name,
            folder_id=UUID(folder_id) if folder_id else None,
            description=description,
            base_url=base_url,
            business_module=business_module,
            navigation=navigation,
            pages=pages,
            tags=tags,
            custom_config=custom_config,
            sort_order=sort_order,
        )

        return {
            "id": str(web_function.id),
            "identifier": web_function.identifier,
            "display_name": web_function.display_name,
            "name": web_function.name,
            "description": web_function.description,
            "base_url": web_function.base_url,
            "business_module": web_function.business_module,
            "folder_id": str(web_function.folder_id) if web_function.folder_id else None,
            "total_sub_functions": web_function.total_sub_functions,
            "total_test_cases": web_function.total_test_cases,
            "total_test_runs": web_function.total_test_runs,
            "sort_order": web_function.sort_order,
            "created_at": web_function.created_at.isoformat(),
        }

    async def get_web_function(
        self,
        function_id: str,
    ) -> dict:
        """
        获取 Web 功能详情

        Args:
            function_id: Web 功能 ID

        Returns:
            dict: Web 功能详情
        """
        web_function = await self.web_function_repo.get_by_id_with_relations(
            UUID(function_id)
        )

        if not web_function:
            raise NotFoundException(resource_type="Web 功能", resource_id=function_id)

        return {
            "id": str(web_function.id),
            "identifier": web_function.identifier,
            "display_name": web_function.display_name,
            "name": web_function.name,
            "description": web_function.description,
            "base_url": web_function.base_url,
            "business_module": web_function.business_module,
            "navigation": web_function.navigation,
            "pages": web_function.pages,
            "tags": web_function.tags,
            "custom_config": web_function.custom_config,
            "folder_id": str(web_function.folder_id) if web_function.folder_id else None,
            "total_sub_functions": web_function.total_sub_functions,
            "total_test_cases": web_function.total_test_cases,
            "total_test_runs": web_function.total_test_runs,
            "last_run_status": web_function.last_run_status,
            "sort_order": web_function.sort_order,
            "created_at": web_function.created_at.isoformat(),
            "updated_at": web_function.updated_at.isoformat() if web_function.updated_at else None,
        }

    async def list_web_functions(
        self,
        project_identifier: str,
        folder_id: Optional[str] = None,
        offset: int = 0,
        limit: int = 20,
        search: Optional[str] = None,
    ) -> dict:
        """
        获取 Web 功能列表

        Args:
            project_identifier: 项目标识符
            folder_id: 文件夹 ID（可选）
            offset: 偏移量
            limit: 限制数量
            search: 搜索关键词

        Returns:
            dict: Web 功能列表和总数
        """
        from sqlalchemy import func, select

        project = await self._get_project_by_identifier(project_identifier)

        if folder_id:
            items, total = await self.web_function_repo.get_by_folder(
                UUID(folder_id),
                offset=offset,
                limit=limit,
                search=search,
            )
        else:
            items, total = await self.web_function_repo.get_by_project(
                project.id,
                offset=offset,
                limit=limit,
                search=search,
            )

        # 批量查询所有功能的子功能统计（1次查询替代 2*N 次）
        function_ids = [item.id for item in items]
        if function_ids:
            stats_stmt = select(
                WebSubFunction.function_id,
                func.count(WebSubFunction.id).label('sub_count'),
                func.sum(WebSubFunction.total_test_cases).label('test_case_sum')
            ).where(
                WebSubFunction.function_id.in_(function_ids),
                WebSubFunction.identifier.not_in(["WSF-1035", "WSF-1030"])
            ).group_by(WebSubFunction.function_id)

            stats_result = await self.session.execute(stats_stmt)
            stats_map = {
                row[0]: {'sub_count': row[1], 'test_case_sum': row[2] or 0}
                for row in stats_result.all()
            }
        else:
            stats_map = {}

        # 应用统计到每个功能
        for item in items:
            stats = stats_map.get(item.id, {'sub_count': 0, 'test_case_sum': 0})
            item.total_sub_functions = stats['sub_count']
            item.total_test_cases = stats['test_case_sum']

        return {
            "items": [
                {
                    "id": str(item.id),
                    "identifier": item.identifier,
                    "display_name": item.display_name,
                    "name": item.name,
                    "description": item.description,
                    "base_url": item.base_url,
                    "business_module": item.business_module,
                    "folder_id": str(item.folder_id) if item.folder_id else None,
                    "total_sub_functions": item.total_sub_functions,
                    "total_test_cases": item.total_test_cases,
                    "total_test_runs": item.total_test_runs,
                    "last_run_status": item.last_run_status,
                    "sort_order": item.sort_order,
                    "created_at": item.created_at.isoformat(),
                }
                for item in items
            ],
            "total": total,
            "offset": offset,
            "limit": limit,
        }

    async def update_web_function(
        self,
        function_id: str,
        display_name: Optional[str] = None,
        name: Optional[str] = None,
        description: Optional[str] = None,
        base_url: Optional[str] = None,
        business_module: Optional[str] = None,
        navigation: Optional[dict] = None,
        pages: Optional[list] = None,
        tags: Optional[list] = None,
        custom_config: Optional[dict] = None,
        sort_order: Optional[int] = None,
    ) -> dict:
        """
        更新 Web 功能

        Args:
            function_id: Web 功能 ID
            display_name: 功能显示名称
            name: 功能名称
            description: 功能描述
            base_url: 功能基础 URL
            business_module: 业务模块标识
            navigation: 导航配置
            pages: 包含的页面列表
            tags: 功能标签
            custom_config: 自定义配置
            sort_order: 排序顺序

        Returns:
            dict: 更新后的 Web 功能信息
        """
        web_function = await self.web_function_repo.get_by_id(UUID(function_id))

        if not web_function:
            raise NotFoundException(resource_type="Web 功能", resource_id=function_id)

        update_data = {}
        if display_name is not None:
            update_data["display_name"] = display_name
        if name is not None:
            update_data["name"] = name
        if description is not None:
            update_data["description"] = description
        if base_url is not None:
            update_data["base_url"] = base_url
        if business_module is not None:
            update_data["business_module"] = business_module
        if navigation is not None:
            update_data["navigation"] = navigation
        if pages is not None:
            update_data["pages"] = pages
        if tags is not None:
            update_data["tags"] = tags
        if custom_config is not None:
            update_data["custom_config"] = custom_config
        if sort_order is not None:
            update_data["sort_order"] = sort_order

        updated_function = await self.web_function_repo.update(
            web_function, **update_data
        )
# pragma: no cover  MS80OmFIVnBZMlhscm9ua3VMazZjVTlhWnc9PTpkMmNiNWU3Yw==

        return {
            "id": str(updated_function.id),
            "identifier": updated_function.identifier,
            "display_name": updated_function.display_name,
            "name": updated_function.name,
            "description": updated_function.description,
            "base_url": updated_function.base_url,
            "business_module": updated_function.business_module,
            "total_sub_functions": updated_function.total_sub_functions,
            "total_test_cases": updated_function.total_test_cases,
            "total_test_runs": updated_function.total_test_runs,
            "sort_order": updated_function.sort_order,
            "updated_at": updated_function.updated_at.isoformat() if updated_function.updated_at else None,
        }

    async def delete_web_function(
        self,
        function_id: str,
    ) -> dict:
        """
        删除 Web 功能

        Args:
            function_id: Web 功能 ID

        Returns:
            dict: 删除结果
        """
        web_function = await self.web_function_repo.get_by_id(UUID(function_id))

        if not web_function:
            raise NotFoundException(resource_type="Web 功能", resource_id=function_id)

        await self.web_function_repo.delete(web_function)

        # 显式提交事务，确保删除操作生效
        await self.session.commit()

        return {"success": True, "message": "Web 功能已删除"}

    # ==================== Web 子功能管理 ====================

    async def create_web_sub_function(
        self,
        project_identifier: str,
        function_id: str,
        display_name: str,
        name: str,
        folder_id: Optional[str] = None,
        description: Optional[str] = None,
        test_type: str = "functional",
        target_pages: Optional[list] = None,
        test_scenario: Optional[str] = None,
        test_data: Optional[dict] = None,
        expected_results: Optional[list] = None,
        priority: str = "medium",
        tags: Optional[list] = None,
        custom_config: Optional[dict] = None,
        sort_order: int = 0,
    ) -> dict:
        """
        创建 Web 子功能

        Args:
            project_identifier: 项目标识符
            function_id: 所属功能 ID
            display_name: 子功能显示名称
            name: 子功能名称（英文）
            folder_id: 所属文件夹 ID
            description: 子功能描述
            test_type: 测试类型
            target_pages: 目标页面列表
            test_scenario: 测试场景描述
            test_data: 测试数据模板
            expected_results: 预期结果列表
            priority: 优先级
            tags: 标签
            custom_config: 自定义配置
            sort_order: 排序顺序

        Returns:
            dict: 创建的 Web 子功能信息
        """
        project = await self._get_project_by_identifier(project_identifier)
        identifier = await self.web_sub_function_repo.get_next_identifier(project.id)

        # 验证功能存在
        web_function = await self.web_function_repo.get_by_id(UUID(function_id))
        if not web_function:
            raise NotFoundException(resource_type="Web 功能", resource_id=function_id)

        # rev56（断言编译）：expected_results 保存时编译为结构化可机检断言；
        # 编译失败/模糊表述 → human_oracle（执行时只校验 compiled，不现场发明）
        compiled = None
        assertion_mode = None
        if expected_results is not None:
            from app.agents.tools.web.assertion_compiler import compile_expected_results

            _c = compile_expected_results(expected_results)
            compiled = _c["assertions"] or []
            assertion_mode = _c["mode"]
            if _c["human_oracle"]:
                print(f"[web_function_service] rev56：{len(_c['human_oracle'])} 条 "
                      f"expected_result 无法编译 → human_oracle（人工判定）")

        web_sub_function = await self.web_sub_function_repo.create(
            project_id=project.id,
            function_id=UUID(function_id),
            identifier=identifier,
            display_name=display_name,
            name=name,
            folder_id=UUID(folder_id) if folder_id else None,
            description=description,
            test_type=test_type,
            target_pages=target_pages,
            test_scenario=test_scenario,
            test_data=test_data,
            expected_results=expected_results,
            compiled_assertions=compiled,
            assertion_mode=assertion_mode,
            priority=priority,
            tags=tags,
            custom_config=custom_config,
            sort_order=sort_order,
        )

        # 更新父功能的子功能计数
        web_function.total_sub_functions += 1
        await self.session.commit()

        return {
            "id": str(web_sub_function.id),
            "identifier": web_sub_function.identifier,
            "function_id": str(web_sub_function.function_id),
            "display_name": web_sub_function.display_name,
            "name": web_sub_function.name,
            "description": web_sub_function.description,
            "test_type": web_sub_function.test_type,
            "folder_id": str(web_sub_function.folder_id) if web_sub_function.folder_id else None,
            "total_test_cases": web_sub_function.total_test_cases,
            "total_test_runs": web_sub_function.total_test_runs,
            "last_run_status": web_sub_function.last_run_status,
            "priority": web_sub_function.priority,
            "sort_order": web_sub_function.sort_order,
            "created_at": web_sub_function.created_at.isoformat(),
        }
# pylint: disable  Mi80OmFIVnBZMlhscm9ua3VMazZjVTlhWnc9PTpkMmNiNWU3Yw==

    async def get_web_sub_function(
        self,
        sub_function_id: str,
    ) -> dict:
        """
        获取 Web 子功能详情

        Args:
            sub_function_id: Web 子功能 ID

        Returns:
            dict: Web 子功能详情
        """
        web_sub_function = await self.web_sub_function_repo.get_by_id_with_relations(
            UUID(sub_function_id)
        )

        if not web_sub_function:
            raise NotFoundException(resource_type="Web 子功能", resource_id=sub_function_id)

        return {
            "id": str(web_sub_function.id),
            "identifier": web_sub_function.identifier,
            "function_id": str(web_sub_function.function_id),
            "display_name": web_sub_function.display_name,
            "name": web_sub_function.name,
            "description": web_sub_function.description,
            "test_type": web_sub_function.test_type,
            "target_pages": web_sub_function.target_pages,
            "test_scenario": web_sub_function.test_scenario,
            "test_data": web_sub_function.test_data,
            "expected_results": web_sub_function.expected_results,
            "priority": web_sub_function.priority,
            "tags": web_sub_function.tags,
            "custom_config": web_sub_function.custom_config,
            "folder_id": str(web_sub_function.folder_id) if web_sub_function.folder_id else None,
            "total_test_cases": web_sub_function.total_test_cases,
            "total_test_runs": web_sub_function.total_test_runs,
            "last_run_status": web_sub_function.last_run_status,
            "sort_order": web_sub_function.sort_order,
            "created_at": web_sub_function.created_at.isoformat(),
            "updated_at": web_sub_function.updated_at.isoformat() if web_sub_function.updated_at else None,
        }

    async def list_web_sub_functions(
        self,
        project_identifier: str,
        function_id: Optional[str] = None,
        folder_id: Optional[str] = None,
        offset: int = 0,
        limit: int = 20,
        search: Optional[str] = None,
    ) -> dict:
        """
        获取 Web 子功能列表

        Args:
            project_identifier: 项目标识符
            function_id: 功能 ID（可选）
            folder_id: 文件夹 ID（可选）
            offset: 偏移量
            limit: 限制数量
            search: 搜索关键词

        Returns:
            dict: Web 子功能列表和总数
        """
        project = await self._get_project_by_identifier(project_identifier)

        if function_id:
            items, total = await self.web_sub_function_repo.get_by_function(
                UUID(function_id),
                offset=offset,
                limit=limit,
                search=search,
            )
        elif folder_id:
            # 子功能没有直接的 folder_id，通过该文件夹下的功能间接查询
            from sqlalchemy import select
            from app.models.web_function import WebFunction
            func_ids_result = await self.web_function_repo.session.execute(
                select(WebFunction.id).where(WebFunction.folder_id == UUID(folder_id))
            )
            func_ids = [row[0] for row in func_ids_result.fetchall()]
            if not func_ids:
                return {"items": [], "total": 0}
            items, total = await self.web_sub_function_repo.get_by_function_ids(
                func_ids,
                offset=offset,
                limit=limit,
                search=search,
            )
        else:
            items, total = await self.web_sub_function_repo.get_by_project(
                project.id,
                offset=offset,
                limit=limit,
                search=search,
            )

        return {
            "items": [
                {
                    "id": str(item.id),
                    "identifier": item.identifier,
                    "function_id": str(item.function_id),
                    "display_name": item.display_name,
                    "name": item.name,
                    "description": item.description,
                    "test_type": item.test_type,
                    "folder_id": str(item.folder_id) if item.folder_id else None,
                    "total_test_cases": item.total_test_cases,
                    "total_test_runs": item.total_test_runs,
                    "last_run_status": item.last_run_status,
                    "priority": item.priority,
                    "sort_order": item.sort_order,
                    "created_at": item.created_at.isoformat(),
                }
                for item in items
            ],
            "total": total,
            "offset": offset,
            "limit": limit,
        }

    async def update_web_sub_function(
        self,
        sub_function_id: str,
        display_name: Optional[str] = None,
        name: Optional[str] = None,
        description: Optional[str] = None,
        test_type: Optional[str] = None,
        target_pages: Optional[list] = None,
        test_scenario: Optional[str] = None,
        test_data: Optional[dict] = None,
        expected_results: Optional[list] = None,
        priority: Optional[str] = None,
        tags: Optional[list] = None,
        custom_config: Optional[dict] = None,
        sort_order: Optional[int] = None,
    ) -> dict:
        """
        更新 Web 子功能

        Args:
            sub_function_id: Web 子功能 ID
            display_name: 子功能显示名称
            name: 子功能名称
            description: 子功能描述
            test_type: 测试类型
            target_pages: 目标页面列表
            test_scenario: 测试场景描述
            test_data: 测试数据模板
            expected_results: 预期结果列表
            priority: 优先级
            tags: 标签
            custom_config: 自定义配置
            sort_order: 排序顺序

        Returns:
            dict: 更新后的 Web 子功能信息
        """
        web_sub_function = await self.web_sub_function_repo.get_by_id(UUID(sub_function_id))

        if not web_sub_function:
            raise NotFoundException(resource_type="Web 子功能", resource_id=sub_function_id)

        update_data = {}
        if display_name is not None:
            update_data["display_name"] = display_name
        if name is not None:
            update_data["name"] = name
        if description is not None:
            update_data["description"] = description
        if test_type is not None:
            update_data["test_type"] = test_type
        if target_pages is not None:
            update_data["target_pages"] = target_pages
        if test_scenario is not None:
            update_data["test_scenario"] = test_scenario
        if test_data is not None:
            update_data["test_data"] = test_data
        if expected_results is not None:
            update_data["expected_results"] = expected_results
            # rev56（断言编译）：更新时同步编译
            from app.agents.tools.web.assertion_compiler import compile_expected_results

            _c = compile_expected_results(expected_results)
            update_data["compiled_assertions"] = _c["assertions"] or []
            update_data["assertion_mode"] = _c["mode"]
            if _c["human_oracle"]:
                print(f"[web_function_service] rev56：{len(_c['human_oracle'])} 条 "
                      f"expected_result 无法编译 → human_oracle（人工判定）")
        if priority is not None:
            update_data["priority"] = priority
        if tags is not None:
            update_data["tags"] = tags
        if custom_config is not None:
            update_data["custom_config"] = custom_config
        if sort_order is not None:
            update_data["sort_order"] = sort_order

        updated_sub_function = await self.web_sub_function_repo.update(
            web_sub_function, **update_data
        )

        return {
            "id": str(updated_sub_function.id),
            "identifier": updated_sub_function.identifier,
            "function_id": str(updated_sub_function.function_id),
            "display_name": updated_sub_function.display_name,
            "name": updated_sub_function.name,
            "description": updated_sub_function.description,
            "test_type": updated_sub_function.test_type,
            "priority": updated_sub_function.priority,
            "total_test_cases": updated_sub_function.total_test_cases,
            "total_test_runs": updated_sub_function.total_test_runs,
            "updated_at": updated_sub_function.updated_at.isoformat() if updated_sub_function.updated_at else None,
        }

    async def delete_web_sub_function(
        self,
        sub_function_id: str,
    ) -> dict:
        """
        删除 Web 子功能

        Args:
            sub_function_id: Web 子功能 ID

        Returns:
            dict: 删除结果
        """
        web_sub_function = await self.web_sub_function_repo.get_by_id(UUID(sub_function_id))
# pylint: disable  My80OmFIVnBZMlhscm9ua3VMazZjVTlhWnc9PTpkMmNiNWU3Yw==

        if not web_sub_function:
            raise NotFoundException(resource_type="Web 子功能", resource_id=sub_function_id)

        # 获取父功能，用于更新计数
        function_id = web_sub_function.function_id

        await self.web_sub_function_repo.delete(web_sub_function)

        # 更新父功能的子功能计数
        if function_id:
            web_function = await self.web_function_repo.get_by_id(function_id)
            if web_function and web_function.total_sub_functions > 0:
                web_function.total_sub_functions -= 1
                await self.session.commit()

        return {"success": True, "message": "Web 子功能已删除"}

    async def get_sub_function_artifacts(
        self,
        sub_function_id: str,
    ) -> dict:
        """
        获取 Web 子功能的测试成果物

        Args:
            sub_function_id: Web 子功能 ID

        Returns:
            dict: 测试成果物列表
        """
        from sqlalchemy import or_, select, and_
        from app.models.attachment import Attachment
        from app.models.attachment import AttachmentEntityType
        from app.models.web_test import WebTest, WebTestRun

        # 验证子功能存在
        web_sub_function = await self.web_sub_function_repo.get_by_id(UUID(sub_function_id))
        if not web_sub_function:
            raise NotFoundException(resource_type="Web 子功能", resource_id=sub_function_id)

        # 成果物通常直接挂在子功能上；HTTP 执行链生成的报告挂在 WebTestRun
        # 上。两种关联都要返回，否则页面会一直显示旧的直接附件报告。
        run_ids = select(WebTestRun.id).join(
            WebTest, WebTest.id == WebTestRun.web_test_id
        ).where(WebTest.sub_function_id == UUID(sub_function_id))
        stmt = select(Attachment).where(
            or_(
                Attachment.entity_id == UUID(sub_function_id),
                and_(
                    Attachment.entity_type == AttachmentEntityType.WEB_TEST_REPORT,
                    Attachment.entity_id.in_(run_ids),
                ),
            )
        ).order_by(Attachment.created_at.desc())

        result = await self.session.execute(stmt)
        attachments = result.scalars().all()

        # 转换为前端期望的格式
        artifacts = []
        for attachment in attachments:
            artifacts.append({
                "id": str(attachment.id),
                "type": attachment.entity_type.value,
                "file_name": attachment.file_name,
                "description": attachment.description or "",
                "file_size": attachment.file_size,
                "content_type": attachment.content_type,
                "object_name": attachment.object_name,
                "created_at": attachment.created_at.isoformat() if attachment.created_at else None,
            })

        return {
            "artifacts": artifacts,
            "total": len(artifacts),
        }
