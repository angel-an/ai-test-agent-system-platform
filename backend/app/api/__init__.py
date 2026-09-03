"""
API 路由模块

包含所有 API 端点的路由定义
"""

from fastapi import APIRouter

from .v2 import ai, auth, projects, folders, test_cases, test_runs, test_results, attachments, configurations, test_plans, documents, api_tests, api_tests_extended, api_endpoints, api_docs, api_case_links, web_case_links, web_path_templates, scenarios, web_tests, web_functions, scheduled_runs, llm_settings, mcp_proxy, security_tests, ios_apps, android_apps, idp_defects, web_script_reviews, governance_metrics, knowledge_base
# noqa  MC8yOmFIVnBZMlhscm9ua3VMazZNemd4Y0E9PTo4MDdiOTdhYQ==

# 创建 API v2 路由
api_router = APIRouter(prefix="/api/v2")

# 注册子路由
api_router.include_router(auth.router, tags=["认证"])
api_router.include_router(ai.router, tags=["AI 生成"])
api_router.include_router(projects.router, tags=["项目管理"])
api_router.include_router(folders.router, tags=["文件夹管理"])
api_router.include_router(test_cases.router, tags=["测试用例管理"])
api_router.include_router(test_cases.exports_router, tags=["导出管理"])
api_router.include_router(test_plans.router, tags=["测试计划管理"])
api_router.include_router(test_runs.router, tags=["测试运行管理"])
api_router.include_router(test_results.router, tags=["测试结果管理"])
api_router.include_router(attachments.test_case_attachments_router, tags=["附件管理"])
api_router.include_router(attachments.test_result_attachments_router, tags=["附件管理"])
api_router.include_router(attachments.attachments_router, tags=["附件管理"])
api_router.include_router(configurations.router, tags=["配置管理"])
api_router.include_router(documents.router, tags=["文档管理"])
api_router.include_router(documents.files_router, tags=["文件管理"])
api_router.include_router(api_tests.router, tags=["API 测试管理"])
api_router.include_router(api_tests_extended.router, tags=["API 测试扩展"])
api_router.include_router(api_endpoints.router, tags=["API 端点管理"])
api_router.include_router(api_docs.router, tags=["API 文档解析"])
api_router.include_router(api_case_links.router, tags=["API 用例闭环"])
api_router.include_router(web_case_links.router, tags=["Web 用例闭环"])
api_router.include_router(web_path_templates.router, tags=["Web 路径模板"])
api_router.include_router(scenarios.router, prefix="/scenarios", tags=["场景测试管理"])
api_router.include_router(web_tests.router, tags=["Web 测试管理"])
api_router.include_router(web_functions.router, tags=["Web 功能管理"])
api_router.include_router(scheduled_runs.router, tags=["定时运行管理"])
api_router.include_router(llm_settings.router, tags=["LLM 配置"])
api_router.include_router(mcp_proxy.router, tags=["MCP 代理"])
api_router.include_router(security_tests.router, tags=["安全测试管理"])
api_router.include_router(ios_apps.router, tags=["iOS App 管理"])
api_router.include_router(android_apps.router, tags=["Android App 管理"])
api_router.include_router(idp_defects.router, tags=["IDP 缺陷管理"])
api_router.include_router(web_script_reviews.router, tags=["Web 脚本评审"])
api_router.include_router(governance_metrics.router, tags=["执行治理指标"])
api_router.include_router(knowledge_base.router, tags=["知识库管理"])
# pragma: no cover  MS8yOmFIVnBZMlhscm9ua3VMazZNemd4Y0E9PTo4MDdiOTdhYQ==

__all__ = ["api_router"]
