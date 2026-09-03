"""
iOS App 功能服务

处理 iOS App 功能和子功能相关的业务逻辑
"""

from typing import Optional
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.ios_app import IOSApp, IOSSubFunction
from app.repositories.ios_app_repo import (
    IOSAppRepository,
    IOSSubFunctionRepository,
)
from app.repositories.project_repo import ProjectRepository
from app.utils.exceptions import NotFoundException


class IOSAppService:
    """iOS App 功能服务类"""

    def __init__(self, session: AsyncSession):
        self.session = session
        self.ios_app_repo = IOSAppRepository(session)
        self.ios_sub_function_repo = IOSSubFunctionRepository(session)
        self.project_repo = ProjectRepository(session)

    async def _get_project_by_identifier(self, identifier: str):
        """获取项目，不存在则抛出异常"""
        project = await self.project_repo.get_by_identifier(identifier)
        if not project:
            raise NotFoundException(resource_type="项目", resource_id=identifier)
        return project

    # ==================== iOS App 功能管理 ====================

    async def create_app(
        self,
        project_identifier: str,
        display_name: str,
        name: str,
        folder_id: Optional[str] = None,
        description: Optional[str] = None,
        bundle_id: Optional[str] = None,
        main_activity: Optional[str] = None,
        version: Optional[str] = None,
        device_udid: Optional[str] = None,
        device_type: Optional[str] = None,
        wda_url: Optional[str] = None,
        business_module: Optional[str] = None,
        navigation: Optional[dict] = None,
        screens: Optional[list] = None,
        tags: Optional[list] = None,
        custom_config: Optional[dict] = None,
        sort_order: int = 0,
    ) -> dict:
        """
        创建 iOS App 功能

        Args:
            project_identifier: 项目标识符
            display_name: 功能显示名称
            name: 功能名称（英文）
            folder_id: 所属文件夹 ID
            description: 功能描述
            bundle_id: iOS App Bundle ID
            main_activity: 主 ViewController 名称或启动页面标识
            version: App 版本号
            device_udid: 设备 UDID
            device_type: 设备类型，如 iPhone, iPad, Simulator
            wda_url: WebDriverAgent 服务 URL
            business_module: 业务模块标识
            navigation: 导航配置
            screens: 包含的页面/屏幕列表
            tags: 功能标签
            custom_config: 自定义配置
            sort_order: 排序顺序

        Returns:
            dict: 创建的 iOS App 功能信息
        """
        project = await self._get_project_by_identifier(project_identifier)
        identifier = await self.ios_app_repo.get_next_identifier(project.id)

        ios_app = await self.ios_app_repo.create(
            project_id=project.id,
            identifier=identifier,
            display_name=display_name,
            name=name,
            folder_id=UUID(folder_id) if folder_id else None,
            description=description,
            bundle_id=bundle_id,
            main_activity=main_activity,
            version=version,
            device_udid=device_udid,
            device_type=device_type,
            wda_url=wda_url,
            business_module=business_module,
            navigation=navigation,
            screens=screens,
            tags=tags,
            custom_config=custom_config,
            sort_order=sort_order,
        )

        return {
            "id": str(ios_app.id),
            "identifier": ios_app.identifier,
            "display_name": ios_app.display_name,
            "name": ios_app.name,
            "description": ios_app.description,
            "bundle_id": ios_app.bundle_id,
            "main_activity": ios_app.main_activity,
            "version": ios_app.version,
            "device_udid": ios_app.device_udid,
            "device_type": ios_app.device_type,
            "wda_url": ios_app.wda_url,
            "business_module": ios_app.business_module,
            "folder_id": str(ios_app.folder_id) if ios_app.folder_id else None,
            "total_sub_functions": ios_app.total_sub_functions,
            "total_test_cases": ios_app.total_test_cases,
            "total_test_runs": ios_app.total_test_runs,
            "sort_order": ios_app.sort_order,
            "created_at": ios_app.created_at.isoformat(),
        }

    async def get_app(
        self,
        app_id: str,
    ) -> dict:
        """
        获取 iOS App 功能详情

        Args:
            app_id: iOS App 功能 ID

        Returns:
            dict: iOS App 功能详情
        """
        ios_app = await self.ios_app_repo.get_by_id_with_relations(
            UUID(app_id)
        )

        if not ios_app:
            raise NotFoundException(resource_type="iOS App 功能", resource_id=app_id)

        return {
            "id": str(ios_app.id),
            "identifier": ios_app.identifier,
            "display_name": ios_app.display_name,
            "name": ios_app.name,
            "description": ios_app.description,
            "bundle_id": ios_app.bundle_id,
            "main_activity": ios_app.main_activity,
            "version": ios_app.version,
            "device_udid": ios_app.device_udid,
            "device_type": ios_app.device_type,
            "wda_url": ios_app.wda_url,
            "business_module": ios_app.business_module,
            "navigation": ios_app.navigation,
            "screens": ios_app.screens,
            "tags": ios_app.tags,
            "custom_config": ios_app.custom_config,
            "folder_id": str(ios_app.folder_id) if ios_app.folder_id else None,
            "total_sub_functions": ios_app.total_sub_functions,
            "total_test_cases": ios_app.total_test_cases,
            "total_test_runs": ios_app.total_test_runs,
            "last_run_status": ios_app.last_run_status,
            "sort_order": ios_app.sort_order,
            "created_at": ios_app.created_at.isoformat(),
            "updated_at": ios_app.updated_at.isoformat() if ios_app.updated_at else None,
        }

    async def list_apps(
        self,
        project_identifier: str,
        folder_id: Optional[str] = None,
        offset: int = 0,
        limit: int = 20,
        search: Optional[str] = None,
    ) -> dict:
        """
        获取 iOS App 功能列表

        Args:
            project_identifier: 项目标识符
            folder_id: 文件夹 ID（可选）
            offset: 偏移量
            limit: 限制数量
            search: 搜索关键词

        Returns:
            dict: iOS App 功能列表和总数
        """
        from sqlalchemy import func, select

        project = await self._get_project_by_identifier(project_identifier)

        if folder_id:
            items, total = await self.ios_app_repo.get_by_folder(
                UUID(folder_id),
                offset=offset,
                limit=limit,
                search=search,
            )
        else:
            items, total = await self.ios_app_repo.get_by_project(
                project.id,
                offset=offset,
                limit=limit,
                search=search,
            )

        # 实时计算每个功能的子功能数和测试用例数
        for item in items:
            # 计算子功能数
            sub_func_count_stmt = select(func.count(IOSSubFunction.id)).where(
                IOSSubFunction.function_id == item.id
            )
            sub_func_result = await self.session.execute(sub_func_count_stmt)
            item.total_sub_functions = sub_func_result.scalar_one() or 0

            # 计算测试用例总数（所有子功能的测试用例之和）
            test_case_sum_stmt = select(func.sum(IOSSubFunction.total_test_cases)).where(
                IOSSubFunction.function_id == item.id
            )
            test_case_result = await self.session.execute(test_case_sum_stmt)
            item.total_test_cases = test_case_result.scalar_one() or 0

        return {
            "items": [
                {
                    "id": str(item.id),
                    "identifier": item.identifier,
                    "display_name": item.display_name,
                    "name": item.name,
                    "description": item.description,
                    "bundle_id": item.bundle_id,
                    "main_activity": item.main_activity,
                    "version": item.version,
                    "device_udid": item.device_udid,
                    "device_type": item.device_type,
                    "wda_url": item.wda_url,
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

    async def update_app(
        self,
        app_id: str,
        display_name: Optional[str] = None,
        name: Optional[str] = None,
        description: Optional[str] = None,
        bundle_id: Optional[str] = None,
        main_activity: Optional[str] = None,
        version: Optional[str] = None,
        device_udid: Optional[str] = None,
        device_type: Optional[str] = None,
        wda_url: Optional[str] = None,
        business_module: Optional[str] = None,
        navigation: Optional[dict] = None,
        screens: Optional[list] = None,
        tags: Optional[list] = None,
        custom_config: Optional[dict] = None,
        sort_order: Optional[int] = None,
    ) -> dict:
        """
        更新 iOS App 功能

        Args:
            app_id: iOS App 功能 ID
            display_name: 功能显示名称
            name: 功能名称
            description: 功能描述
            bundle_id: iOS App Bundle ID
            main_activity: 主 ViewController 名称或启动页面标识
            version: App 版本号
            device_udid: 设备 UDID
            device_type: 设备类型
            wda_url: WebDriverAgent 服务 URL
            business_module: 业务模块标识
            navigation: 导航配置
            screens: 包含的页面/屏幕列表
            tags: 功能标签
            custom_config: 自定义配置
            sort_order: 排序顺序

        Returns:
            dict: 更新后的 iOS App 功能信息
        """
        ios_app = await self.ios_app_repo.get_by_id(UUID(app_id))

        if not ios_app:
            raise NotFoundException(resource_type="iOS App 功能", resource_id=app_id)

        update_data = {}
        if display_name is not None:
            update_data["display_name"] = display_name
        if name is not None:
            update_data["name"] = name
        if description is not None:
            update_data["description"] = description
        if bundle_id is not None:
            update_data["bundle_id"] = bundle_id
        if main_activity is not None:
            update_data["main_activity"] = main_activity
        if version is not None:
            update_data["version"] = version
        if device_udid is not None:
            update_data["device_udid"] = device_udid
        if device_type is not None:
            update_data["device_type"] = device_type
        if wda_url is not None:
            update_data["wda_url"] = wda_url
        if business_module is not None:
            update_data["business_module"] = business_module
        if navigation is not None:
            update_data["navigation"] = navigation
        if screens is not None:
            update_data["screens"] = screens
        if tags is not None:
            update_data["tags"] = tags
        if custom_config is not None:
            update_data["custom_config"] = custom_config
        if sort_order is not None:
            update_data["sort_order"] = sort_order

        updated_app = await self.ios_app_repo.update(
            ios_app, **update_data
        )

        return {
            "id": str(updated_app.id),
            "identifier": updated_app.identifier,
            "display_name": updated_app.display_name,
            "name": updated_app.name,
            "description": updated_app.description,
            "bundle_id": updated_app.bundle_id,
            "main_activity": updated_app.main_activity,
            "version": updated_app.version,
            "device_udid": updated_app.device_udid,
            "device_type": updated_app.device_type,
            "wda_url": updated_app.wda_url,
            "business_module": updated_app.business_module,
            "total_sub_functions": updated_app.total_sub_functions,
            "total_test_cases": updated_app.total_test_cases,
            "total_test_runs": updated_app.total_test_runs,
            "sort_order": updated_app.sort_order,
            "updated_at": updated_app.updated_at.isoformat() if updated_app.updated_at else None,
        }

    async def delete_app(
        self,
        app_id: str,
    ) -> dict:
        """
        删除 iOS App 功能

        Args:
            app_id: iOS App 功能 ID

        Returns:
            dict: 删除结果
        """
        ios_app = await self.ios_app_repo.get_by_id(UUID(app_id))

        if not ios_app:
            raise NotFoundException(resource_type="iOS App 功能", resource_id=app_id)

        await self.ios_app_repo.delete(ios_app)

        # 显式提交事务，确保删除操作生效
        await self.session.commit()

        return {"success": True, "message": "iOS App 功能已删除"}

    # ==================== iOS App 子功能管理 ====================

    async def create_sub_function(
        self,
        project_identifier: str,
        function_id: str,
        display_name: str,
        name: str,
        folder_id: Optional[str] = None,
        description: Optional[str] = None,
        test_type: str = "functional",
        target_screens: Optional[list] = None,
        test_scenario: Optional[str] = None,
        test_data: Optional[dict] = None,
        expected_results: Optional[list] = None,
        priority: str = "medium",
        tags: Optional[list] = None,
        custom_config: Optional[dict] = None,
        sort_order: int = 0,
    ) -> dict:
        """
        创建 iOS App 子功能

        Args:
            project_identifier: 项目标识符
            function_id: 所属功能 ID
            display_name: 子功能显示名称
            name: 子功能名称（英文）
            folder_id: 所属文件夹 ID
            description: 子功能描述
            test_type: 测试类型
            target_screens: 目标屏幕列表
            test_scenario: 测试场景描述
            test_data: 测试数据模板
            expected_results: 预期结果列表
            priority: 优先级
            tags: 标签
            custom_config: 自定义配置
            sort_order: 排序顺序

        Returns:
            dict: 创建的 iOS App 子功能信息
        """
        project = await self._get_project_by_identifier(project_identifier)
        identifier = await self.ios_sub_function_repo.get_next_identifier(project.id)

        # 验证功能存在
        ios_app = await self.ios_app_repo.get_by_id(UUID(function_id))
        if not ios_app:
            raise NotFoundException(resource_type="iOS App 功能", resource_id=function_id)

        ios_sub_function = await self.ios_sub_function_repo.create(
            project_id=project.id,
            function_id=UUID(function_id),
            identifier=identifier,
            display_name=display_name,
            name=name,
            folder_id=UUID(folder_id) if folder_id else None,
            description=description,
            test_type=test_type,
            target_screens=target_screens,
            test_scenario=test_scenario,
            test_data=test_data,
            expected_results=expected_results,
            priority=priority,
            tags=tags,
            custom_config=custom_config,
            sort_order=sort_order,
        )

        # 更新父功能的子功能计数
        ios_app.total_sub_functions += 1
        await self.session.commit()

        return {
            "id": str(ios_sub_function.id),
            "identifier": ios_sub_function.identifier,
            "function_id": str(ios_sub_function.function_id),
            "display_name": ios_sub_function.display_name,
            "name": ios_sub_function.name,
            "description": ios_sub_function.description,
            "test_type": ios_sub_function.test_type,
            "folder_id": str(ios_sub_function.folder_id) if ios_sub_function.folder_id else None,
            "total_test_cases": ios_sub_function.total_test_cases,
            "total_test_runs": ios_sub_function.total_test_runs,
            "last_run_status": ios_sub_function.last_run_status,
            "priority": ios_sub_function.priority,
            "sort_order": ios_sub_function.sort_order,
            "created_at": ios_sub_function.created_at.isoformat(),
        }

    async def get_sub_function(
        self,
        sub_function_id: str,
    ) -> dict:
        """
        获取 iOS App 子功能详情

        Args:
            sub_function_id: iOS App 子功能 ID

        Returns:
            dict: iOS App 子功能详情
        """
        ios_sub_function = await self.ios_sub_function_repo.get_by_id_with_relations(
            UUID(sub_function_id)
        )

        if not ios_sub_function:
            raise NotFoundException(resource_type="iOS App 子功能", resource_id=sub_function_id)

        return {
            "id": str(ios_sub_function.id),
            "identifier": ios_sub_function.identifier,
            "function_id": str(ios_sub_function.function_id),
            "display_name": ios_sub_function.display_name,
            "name": ios_sub_function.name,
            "description": ios_sub_function.description,
            "test_type": ios_sub_function.test_type,
            "target_screens": ios_sub_function.target_screens,
            "test_scenario": ios_sub_function.test_scenario,
            "test_data": ios_sub_function.test_data,
            "expected_results": ios_sub_function.expected_results,
            "priority": ios_sub_function.priority,
            "tags": ios_sub_function.tags,
            "custom_config": ios_sub_function.custom_config,
            "folder_id": str(ios_sub_function.folder_id) if ios_sub_function.folder_id else None,
            "total_test_cases": ios_sub_function.total_test_cases,
            "total_test_runs": ios_sub_function.total_test_runs,
            "last_run_status": ios_sub_function.last_run_status,
            "sort_order": ios_sub_function.sort_order,
            "created_at": ios_sub_function.created_at.isoformat(),
            "updated_at": ios_sub_function.updated_at.isoformat() if ios_sub_function.updated_at else None,
        }

    async def list_sub_functions(
        self,
        project_identifier: str,
        function_id: Optional[str] = None,
        folder_id: Optional[str] = None,
        offset: int = 0,
        limit: int = 20,
        search: Optional[str] = None,
    ) -> dict:
        """
        获取 iOS App 子功能列表

        Args:
            project_identifier: 项目标识符
            function_id: 功能 ID（可选）
            folder_id: 文件夹 ID（可选）
            offset: 偏移量
            limit: 限制数量
            search: 搜索关键词

        Returns:
            dict: iOS App 子功能列表和总数
        """
        project = await self._get_project_by_identifier(project_identifier)

        if function_id:
            items, total = await self.ios_sub_function_repo.get_by_function(
                UUID(function_id),
                offset=offset,
                limit=limit,
                search=search,
            )
        elif folder_id:
            # 子功能没有直接的 folder_id，通过该文件夹下的功能间接查询
            from sqlalchemy import select
            from app.models.ios_app import IOSApp
            func_ids_result = await self.ios_app_repo.session.execute(
                select(IOSApp.id).where(IOSApp.folder_id == UUID(folder_id))
            )
            func_ids = [row[0] for row in func_ids_result.fetchall()]
            if not func_ids:
                return {"items": [], "total": 0}
            items, total = await self.ios_sub_function_repo.get_by_function_ids(
                func_ids,
                offset=offset,
                limit=limit,
                search=search,
            )
        else:
            items, total = await self.ios_sub_function_repo.get_by_project(
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

    async def update_sub_function(
        self,
        sub_function_id: str,
        display_name: Optional[str] = None,
        name: Optional[str] = None,
        description: Optional[str] = None,
        test_type: Optional[str] = None,
        target_screens: Optional[list] = None,
        test_scenario: Optional[str] = None,
        test_data: Optional[dict] = None,
        expected_results: Optional[list] = None,
        priority: Optional[str] = None,
        tags: Optional[list] = None,
        custom_config: Optional[dict] = None,
        sort_order: Optional[int] = None,
    ) -> dict:
        """
        更新 iOS App 子功能

        Args:
            sub_function_id: iOS App 子功能 ID
            display_name: 子功能显示名称
            name: 子功能名称
            description: 子功能描述
            test_type: 测试类型
            target_screens: 目标屏幕列表
            test_scenario: 测试场景描述
            test_data: 测试数据模板
            expected_results: 预期结果列表
            priority: 优先级
            tags: 标签
            custom_config: 自定义配置
            sort_order: 排序顺序

        Returns:
            dict: 更新后的 iOS App 子功能信息
        """
        ios_sub_function = await self.ios_sub_function_repo.get_by_id(UUID(sub_function_id))

        if not ios_sub_function:
            raise NotFoundException(resource_type="iOS App 子功能", resource_id=sub_function_id)

        update_data = {}
        if display_name is not None:
            update_data["display_name"] = display_name
        if name is not None:
            update_data["name"] = name
        if description is not None:
            update_data["description"] = description
        if test_type is not None:
            update_data["test_type"] = test_type
        if target_screens is not None:
            update_data["target_screens"] = target_screens
        if test_scenario is not None:
            update_data["test_scenario"] = test_scenario
        if test_data is not None:
            update_data["test_data"] = test_data
        if expected_results is not None:
            update_data["expected_results"] = expected_results
        if priority is not None:
            update_data["priority"] = priority
        if tags is not None:
            update_data["tags"] = tags
        if custom_config is not None:
            update_data["custom_config"] = custom_config
        if sort_order is not None:
            update_data["sort_order"] = sort_order

        updated_sub_function = await self.ios_sub_function_repo.update(
            ios_sub_function, **update_data
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

    async def delete_sub_function(
        self,
        sub_function_id: str,
    ) -> dict:
        """
        删除 iOS App 子功能

        Args:
            sub_function_id: iOS App 子功能 ID

        Returns:
            dict: 删除结果
        """
        ios_sub_function = await self.ios_sub_function_repo.get_by_id(UUID(sub_function_id))

        if not ios_sub_function:
            raise NotFoundException(resource_type="iOS App 子功能", resource_id=sub_function_id)

        # 获取父功能，用于更新计数
        function_id = ios_sub_function.function_id

        await self.ios_sub_function_repo.delete(ios_sub_function)

        # 更新父功能的子功能计数
        if function_id:
            ios_app = await self.ios_app_repo.get_by_id(function_id)
            if ios_app and ios_app.total_sub_functions > 0:
                ios_app.total_sub_functions -= 1
                await self.session.commit()

        return {"success": True, "message": "iOS App 子功能已删除"}

    async def get_sub_function_artifacts(
        self,
        sub_function_id: str,
    ) -> dict:
        """
        获取 iOS App 子功能的测试成果物

        Args:
            sub_function_id: iOS App 子功能 ID

        Returns:
            dict: 测试成果物列表
        """
        from sqlalchemy import select
        from app.models.attachment import Attachment

        # 验证子功能存在
        ios_sub_function = await self.ios_sub_function_repo.get_by_id(UUID(sub_function_id))
        if not ios_sub_function:
            raise NotFoundException(resource_type="iOS App 子功能", resource_id=sub_function_id)

        # 查询所有相关的附件
        stmt = select(Attachment).where(
            Attachment.entity_id == UUID(sub_function_id)
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
